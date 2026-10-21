"""Sentinel 1 (étape 3, lot C) : choix des passages à analyser, travailleur GPU, fusion avec l'AIS et alertes.

Le serveur ne télécharge jamais d'image : il lit des métadonnées (catalogue STAC de Copernicus Data Space) et reçoit
des détections et des vignettes (quelques kilooctets chacune). Le travailleur (travailleurs/sentinel.py), sur une
instance GPU éphémère, lit lui même les images VV et VH de chaque scène dans le stockage S3 de Copernicus, signature
par en tête, avec une paire de clés qui lui est dédiée (CDSE_TRAVAILLEUR_*), remise par le réseau privé au moment où
il récupère sa tâche, jamais dans le cloud-init, un journal ou la base ; la paire du serveur (CDSE_ACCESS_KEY) ne
quitte jamais le serveur. Copernicus refuse les adresses présignées (14/10/2026).

Chaîne :
1. Sélection sobre (plan) : un passage Sentinel 1 en mode IW, acquis depuis moins de `passages_recents_h`, est analysé
   s'il couvre un corridor d'infrastructure où se trouvait un navire des listes à l'heure du passage, ou une alerte
   DARK_SHIP de source VIIRS ouverte de gravité moyenne au moins ; seules les scènes (tranches de 25 s) qui couvrent
   ces points d'intérêt sont retenues, toutes dans une seule session GPU. Scène pas encore publiée : nouvel essai au
   cycle suivant.
2. Garde fous : plafond quotidien de scènes, budget mensuel en euros (coût estimé avant lancement, coût mesuré
   journalisé après) ; au delà, refus clair.
3. Résultats (ingest) : détections en base (une seule fois), vignettes radar sur R2 (registre preuves_images), puis
   fusion avec l'AIS (mars/fusion : positions à l'instant de la scène, tolérance orientée le long de la trace,
   appariement, échos fixes), éoliennes, côtes, évaluation (mars/evaluation.py) et alertes : DARK_SHIP de source radar,
   AIS_UNCONFIRMED, IDENTITY_MISMATCH.

Les fonctions de décision (cap, plafonds, coût, choix des scènes, incohérences) sont pures, couvertes par
tests/test_sentinel.py.
"""
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

from mars import traces
from mars.evaluation import GARDE_VERIFICATION, MOTIF_COUPURE, critere, motif_non_evaluable

STAC = "https://stac.dataspace.copernicus.eu/v1/search"
S3_HOST = "eodata.dataspace.copernicus.eu"
HEADERS = {"User-Agent": "mars-c2 (surveillance maritime, projet personnel)"}
INCLINAISON_DEG = 98.18          # orbite héliosynchrone de Sentinel 1
SEVERITES = ["faible", "moyenne", "elevee", "critique"]


# Décisions pures

def track_heading(lat: float, ascending: bool) -> float:
    """Direction de vol du satellite au dessus de la latitude `lat` (degrés depuis le nord) : sin(az) = cos(i) / cos(lat).
    Vers 47° N : 348° en orbite ascendante, 192° en descendante (valeurs de config/rules.yaml, fusion)."""
    az = math.degrees(math.asin(max(-1.0, min(1.0, math.cos(math.radians(INCLINAISON_DEG)) / math.cos(math.radians(lat))))))
    return (360 + az) % 360 if ascending else (180 - az) % 360


def heading_from_buckets(probs: list[float]) -> tuple[float | None, float | None]:
    """Cap de l'écho et sa probabilité à partir des 16 classes du modèle d'AI2 (22,5° chacune, depuis le nord, sens des
    aiguilles d'une montre) : centre de la classe la plus probable (incertitude d'une demi classe)."""
    vals = [p for p in probs if p is not None and p == p]
    if len(vals) != 16:
        return None, None
    j = max(range(16), key=lambda k: vals[k])
    return round((j + 0.5) * 22.5, 1), round(vals[j], 3)


def angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def mismatches(det: dict, ais: dict, s: dict) -> list[str]:
    """Incohérences entre un écho apparié et la déclaration AIS du navire. `det` : longueur_m, cap_deg, cap_prob ;
    `ais` : length_m, cog, sog. Longueur : écart au delà de `ecart_longueur_m` ET de `ecart_longueur_relatif` de la
    longueur déclarée. Cap : écart entre `cap_ecart_min_deg` et `cap_ecart_max_deg` (en travers ; l'inversion avant
    arrière, fréquente au radar, n'est pas retenue), classe assez probable, navire faisant route."""
    out = []
    L, la = det.get("longueur_m"), ais.get("length_m")
    if L is not None and la is not None and la >= s["longueur_declaree_min_m"]:
        gap = L - la
        if abs(gap) > s["ecart_longueur_m"] and abs(gap) > s["ecart_longueur_relatif"] * la:
            out.append(f"Longueur mesurée {L:.0f} m contre {la:.0f} m déclarés ({'+' if gap > 0 else ''}{gap:.0f} m)"
                       .replace("+", "plus "))
    h, hp, cog, sog = det.get("cap_deg"), det.get("cap_prob"), ais.get("cog"), ais.get("sog")
    if (h is not None and hp is not None and cog is not None and sog is not None and hp >= s["cap_probabilite_min"]
            and sog >= s["cap_vitesse_min_kn"]):
        d = angle_diff(h, cog)
        if s["cap_ecart_min_deg"] <= d <= s["cap_ecart_max_deg"]:
            out.append(f"Écho orienté à {h:.0f}° contre une route déclarée de {cog:.0f}° ({d:.0f}° d'écart)")
    return out


def cost_estimate(n_scenes: int, s: dict, t: dict) -> float:
    """Coût estimé hors taxes d'une session GPU : partie fixe (démarrage, image) et part par scène, à la minute."""
    minutes = s["minutes_fixes"] + n_scenes * s["minutes_par_scene"]
    return round(minutes / 60 * t["prix_heure"], 3)


def guard(n_scenes: int, scenes_today: int, spent_month: float, estimate: float, s: dict) -> str | None:
    """Motif de refus si le plafond quotidien de scènes ou le budget mensuel serait dépassé, sinon None."""
    if scenes_today + n_scenes > s["scenes_max_par_jour"]:
        return (f"plafond de {s['scenes_max_par_jour']} scènes par jour : {scenes_today} déjà analysée(s), "
                f"{n_scenes} demandée(s)")
    if spent_month + estimate > s["budget_mensuel_eur"]:
        return (f"budget mensuel de {s['budget_mensuel_eur']:.2f} € : {spent_month:.2f} € engagés, "
                f"{estimate:.2f} € estimés pour cette session").replace(".", ",")
    return None


def _hav_m(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _inside(lon: float, lat: float, ring: list) -> bool:
    """Point dans un polygone (anneau extérieur, lon et lat), par la règle du rayon."""
    inside, n = False, len(ring)
    for k in range(n):
        (x1, y1), (x2, y2) = ring[k][:2], ring[(k + 1) % n][:2]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


def _segment_m(lon: float, lat: float, a: tuple, b: tuple) -> float:
    """Distance en mètres du point au segment ab (plan tangent local : emprises de quelques centaines de km)."""
    kx, ky = 111_320.0 * math.cos(math.radians(lat)), 110_574.0
    ax, ay, bx, by = (a[0] - lon) * kx, (a[1] - lat) * ky, (b[0] - lon) * kx, (b[1] - lat) * ky
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def covers(geometry: dict, lon: float, lat: float, margin_m: float) -> bool:
    """La scène (géométrie GeoJSON) couvre le point, ou en passe à moins de `margin_m` (bords de l'emprise)."""
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    for poly in polys:
        ring = [tuple(p[:2]) for p in poly[0]]
        if _inside(lon, lat, ring):
            return True
        if margin_m > 0 and any(_segment_m(lon, lat, ring[k], ring[(k + 1) % len(ring)]) <= margin_m
                                for k in range(len(ring))):
            return True
    return False


def distance_emprise_m(geometry: dict, lon: float, lat: float) -> float:
    """Distance du point à l'emprise (GeoJSON), 0 dedans ; plan tangent local, approché au delà de quelques centaines
    de km (motif d'une demande d'observation)."""
    if covers(geometry, lon, lat, 0):
        return 0.0
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return min(_segment_m(lon, lat, ring[k], ring[(k + 1) % len(ring)])
               for ring in ([tuple(p[:2]) for p in poly[0]] for poly in polys) for k in range(len(ring)))


def pick_scenes(scenes: list[dict], points: list[dict], margin_m: float) -> list[dict]:
    """Scènes qui couvrent au moins un point d'intérêt, chacune avec ses points, les plus riches d'abord."""
    out = []
    for sc in scenes:
        mine = [p for p in points if covers(sc["emprise"], p["lon"], p["lat"], margin_m)]
        if mine:
            out.append({**sc, "points": mine})
    return sorted(out, key=lambda sc: (-len(sc["points"]), sc["debut"]))


# Catalogue (métadonnées seulement) et clés de lecture

def s3_path(href: str) -> str:
    """« s3://eodata/Sentinel-1/… » devient « /eodata/Sentinel-1/… » (chemin de l'adresse S3)."""
    if not href.startswith("s3://"):
        raise ValueError(f"adresse S3 attendue : {href}")
    return "/" + href[5:]


def scene_from_item(f: dict) -> dict | None:
    """Scène utilisable : produit GRD en mode IW avec ses deux polarisations VV et VH."""
    a, p = f.get("assets", {}), f.get("properties", {})
    if p.get("sar:instrument_mode") != "IW" or "vv" not in a or "vh" not in a:
        return None
    return {"id": f["id"], "debut": p.get("start_datetime") or p["datetime"], "fin": p.get("end_datetime") or p["datetime"],
            "orbite": p.get("sat:orbit_state"), "satellite": (p.get("platform") or "").upper().replace("SENTINEL-", "S"),
            "emprise": f["geometry"], "vv": a["vv"]["href"], "vh": a["vh"]["href"],
            "octets": int(a["vv"].get("file:size") or 0) + int(a["vh"].get("file:size") or 0)}


def stac_scenes(session, satellite: str, t0: datetime, t1: datetime, bbox: tuple) -> list[dict]:
    """Scènes Sentinel 1 GRD d'un passage (`satellite` : S1C, S1D…) sur l'intervalle de l'acquisition et l'emprise de
    nos régions ; le satellite est filtré ici (quelques produits seulement sur deux à trois minutes)."""
    body = {"collections": ["sentinel-1-grd"], "bbox": list(bbox), "limit": 100,
            "datetime": f"{t0:%Y-%m-%dT%H:%M:%SZ}/{t1:%Y-%m-%dT%H:%M:%SZ}"}
    r = session.post(STAC, json=body, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return [s for s in (scene_from_item(f) for f in r.json().get("features", [])) if s and s["satellite"] == satellite]


# Deux paires de clés S3 de Copernicus. Copernicus Data Space refuse l'authentification par la requête (adresse
# présignée : 403 InvalidAccessKeyId même avec des clés valides, test du 14/10/2026) : le travailleur lit donc les images
# lui même, signature par en tête, avec une paire qui lui est dédiée, remise par le réseau privé au moment où il
# récupère sa tâche (backend/travailleurs.py) ; la paire du serveur ne quitte jamais le serveur.
SERVEUR = {"nom": "du serveur", "cle": "CDSE_ACCESS_KEY", "secret": "CDSE_SECRET_KEY", "expire": "CDSE_CLES_EXPIRENT_LE"}
TRAVAILLEUR = {"nom": "dédiées aux travailleurs", "cle": "CDSE_TRAVAILLEUR_ACCESS_KEY",
               "secret": "CDSE_TRAVAILLEUR_SECRET_KEY", "expire": "CDSE_TRAVAILLEUR_EXPIRENT_LE"}


def pair_keys(pair: dict) -> tuple[str, str] | None:
    key, secret = os.environ.get(pair["cle"]), os.environ.get(pair["secret"])
    return (key.strip(), secret.strip()) if key and secret else None


def pair_expired(pair: dict, now: datetime) -> str | None:
    """Motif si la date d'expiration reportée dans le .env est passée (rien si elle n'est pas renseignée)."""
    value = (os.environ.get(pair["expire"]) or "").strip()
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None
    if day and day < now.date():
        return f"clés Copernicus {pair['nom']} expirées le {day:%d/%m/%Y} ({pair['expire']}) : les régénérer"
    return None


def check_cdse(session, href: str, pair: dict = TRAVAILLEUR) -> str | None:
    """Clés S3 de Copernicus valides ? Lecture réelle de 16 octets d'une image (`href` en s3://eodata/…), signature par
    en tête (seule acceptée par Copernicus). Aucun coût, aucune écriture. None si la lecture réussit, sinon le motif."""
    from mars.r2_signature import signed_headers
    keys = pair_keys(pair)
    if not keys:
        return f"clés Copernicus {pair['nom']} absentes du .env ({pair['cle']}, {pair['secret']})"
    path = s3_path(href)
    try:
        r = session.get(f"https://{S3_HOST}{path}", stream=True, timeout=30,
                        headers={"Range": "bytes=0-15", **HEADERS, **signed_headers("GET", S3_HOST, path, *keys)})
    except Exception as e:
        return f"Copernicus injoignable ({type(e).__name__})"
    code = r.status_code
    if code in (200, 206):
        r.close()
        return None
    # Réponse d'erreur S3 (XML, quelques centaines d'octets) : son code dit la cause (InvalidAccessKeyId : clé inconnue
    # ou expirée ; SignatureDoesNotMatch : clé secrète fausse ; AccessDenied…)
    detail = ""
    try:
        text = (getattr(r, "text", "") or "")[:600]
        found = re.search(r"<Code>([^<]+)</Code>", text)
        detail = f", code {found.group(1)}" if found else ""
    except Exception:
        pass
    r.close()
    if code in (401, 403):
        return (f"clés Copernicus {pair['nom']} refusées (HTTP {code}{detail}) : {pair['cle']} ou {pair['secret']} "
                "invalides ou périmées (https://eodata-s3keysmanager.dataspace.copernicus.eu)")
    return f"lecture d'essai refusée par Copernicus (HTTP {code}{detail})"


def launch_failures_today(conn, now: datetime) -> list[int]:
    """Passages dont l'analyse a échoué aujourd'hui au lancement, sans instance créée (refus de Scaleway, image
    introuvable, clés) : la sélection automatique les laisse jusqu'au cycle quotidien suivant, sans les bloquer pour
    autant (l'index unique ignore les analyses en échec)."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return [r[0] for r in conn.execute(
        """SELECT DISTINCT a.pass_id FROM analyses a LEFT JOIN travailleurs t ON t.id = a.travailleur_id
           WHERE a.source = 'sentinel1' AND a.status = 'failed' AND a.requested_at >= %s AND t.scw_server_id IS NULL""",
        (day,)).fetchall()]


# Sélection des passages (base)

MOTIF_A_QUAI = "à quai, invisible au radar"


def en_mer(conn, points: list[dict], rules: dict) -> tuple[list[dict], list[dict]]:
    """Points d'un navire des listes retenus seulement en mer (lot C5) : à quai ou dans un bassin portuaire, un navire
    se confond au radar avec les quais et les autres coques (essai du 19/10/2026, VICTOR ANDRYUKHIN dans les bassins
    nord de Marseille : l'analyse ne pouvait rien en dire). Retenu à plus de `point_cote_min_km` de la côte, ou au
    mouillage hors port (zone de mouillage connue à `point_mouillage_m` près et au moins `point_mouillage_cote_min_m`
    de la côte) ; sinon écarté avec son motif. Les autres points (alertes VIIRS) sont gardés tels quels."""
    s = rules["sentinel"]
    kept, dropped = [], []
    for pt in points:
        if pt.get("motif") not in ("corridor_liste", "navire_liste"):
            kept.append(pt)
            continue
        cote, mouillage = conn.execute(
            """SELECT (SELECT min(ST_Distance(l.geom, x.g)) FROM land l WHERE ST_DWithin(l.geom, x.g, %(r)s)),
                      EXISTS (SELECT 1 FROM stationary_zones z WHERE ST_DWithin(z.geom, x.g, %(m)s))
               FROM (SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography AS g) x""",
            {"r": s["point_cote_min_km"] * 1000.0, "m": float(s["point_mouillage_m"]), "lon": pt["lon"],
             "lat": pt["lat"]}).fetchone()
        pt = {**pt, "distance_cote_m": None if cote is None else round(cote)}
        if cote is None:
            kept.append({**pt, "en_mer": f"à plus de {s['point_cote_min_km']} km de la côte"})
        elif mouillage and cote >= s["point_mouillage_cote_min_m"]:
            kept.append({**pt, "en_mer": "au mouillage hors port"})
        else:
            dropped.append({**pt, "ecarte": MOTIF_A_QUAI})
    return kept, dropped


def interest_points(conn, pass_id: int, rules: dict, ecartes: list | None = None) -> list[dict]:
    """Points d'intérêt dans l'emprise d'un passage : navires des listes à moins de `corridor_m` d'une infrastructure,
    à `liste_fenetre_min` près de l'acquisition ; alertes DARK_SHIP VIIRS ouvertes, assez graves, récentes."""
    from mars import tip_and_cue
    s = rules["sentinel"]
    # Lot T1 : quand tip and cue est actif, le navire des listes près d'une infrastructure est un tip (watchlist) ; ses
    # points arrivent par les demandes d'observation planifiées sur ce passage, la recherche directe ci dessous est
    # alors coupée (un seul mécanisme)
    par_tips = tip_and_cue.actif(rules) and (rules["tip_and_cue"]["tips"].get("watchlist") or {}).get("actif")
    rows = [] if par_tips else conn.execute(
        """SELECT DISTINCT ON (p.vessel_id) 'liste', p.vessel_id, ST_X(p.geom::geometry), ST_Y(p.geom::geometry),
                  v.name, i.id, coalesce(i.name, i.attrs->>'type')
           FROM sar_passes sp
           JOIN positions p ON p.ts BETWEEN sp.acquired_at - make_interval(mins => %(f)s)
                                       AND coalesce(sp.ended_at, sp.acquired_at) + make_interval(mins => %(f)s)
                           AND ST_Intersects(p.geom::geometry, sp.footprint::geometry)
           JOIN vessel_watch w ON w.vessel_id = p.vessel_id AND w.level <> 'autre_risque'
           JOIN vessels v ON v.id = p.vessel_id
           JOIN infrastructure i ON ST_DWithin(i.geom, p.geom, %(c)s)
           WHERE sp.id = %(id)s
           ORDER BY p.vessel_id, abs(extract(epoch FROM p.ts - sp.acquired_at))""",
        {"id": pass_id, "f": s["liste_fenetre_min"], "c": s["corridor_m"]}).fetchall()
    rank = SEVERITES.index(s["viirs_gravite_min"])
    rows += conn.execute(
        """SELECT 'viirs', a.id, ST_X(a.geom::geometry), ST_Y(a.geom::geometry), a.severity, NULL, NULL
           FROM sar_passes sp JOIN alerts a ON a.type = 'DARK_SHIP' AND a.details->>'source' = 'viirs'
             AND a.status <> 'classee' AND a.severity = ANY(%(sev)s)
             AND a.event_time BETWEEN sp.acquired_at - make_interval(hours => %(h)s) AND sp.acquired_at
             AND ST_Intersects(a.geom::geometry, sp.footprint::geometry)
           WHERE sp.id = %(id)s""",
        {"id": pass_id, "sev": SEVERITES[rank:], "h": s["viirs_fenetre_h"]}).fetchall()
    out = []
    for kind, ref, lon, lat, label, infra, infra_name in rows:
        if kind == "liste":
            out.append({"motif": "corridor_liste", "vessel_id": ref, "navire": label, "infrastructure": infra,
                        "infrastructure_nom": infra_name, "lon": lon, "lat": lat})
        else:
            out.append({"motif": "alerte_viirs", "alerte": ref, "gravite": label, "lon": lon, "lat": lat})
    out, dropped = en_mer(conn, out, rules)
    if ecartes is not None:
        ecartes += dropped
    # Zones de vigilance (lot P) : un point d'intérêt dans une zone est servi comme une demande de la priorité du profil
    from mars import controle
    for p in out:
        zs = controle.zones_de(rules, p["lon"], p["lat"])
        if zs:
            p["zone_vigilance"] = zs[0]["id"]
            p["priorite"] = controle.priorite_zone(rules, p["lon"], p["lat"], "priorite_sentinel")
    return tip_and_cue.points_passage(conn, rules, pass_id) + out     # demandes d'observation : déjà en mer


def points_liste(conn, pass_id: int, rules: dict, ecartes: list | None = None) -> list[dict]:
    """Navires des listes dans l'emprise d'un passage, à `liste_fenetre_min` près de l'acquisition, corridor ou non :
    points d'un passage choisi à la main (essai réel), pour n'analyser que leurs fenêtres."""
    s = rules["sentinel"]
    rows = conn.execute(
        """SELECT DISTINCT ON (p.vessel_id) p.vessel_id, ST_X(p.geom::geometry), ST_Y(p.geom::geometry), v.name
           FROM sar_passes sp
           JOIN positions p ON p.ts BETWEEN sp.acquired_at - make_interval(mins => %(f)s)
                                       AND coalesce(sp.ended_at, sp.acquired_at) + make_interval(mins => %(f)s)
                           AND ST_Intersects(p.geom::geometry, sp.footprint::geometry)
           JOIN vessel_watch w ON w.vessel_id = p.vessel_id AND w.level <> 'autre_risque'
           JOIN vessels v ON v.id = p.vessel_id
           WHERE sp.id = %(id)s
           ORDER BY p.vessel_id, abs(extract(epoch FROM p.ts - sp.acquired_at))""",
        {"id": pass_id, "f": s["liste_fenetre_min"]}).fetchall()
    out, dropped = en_mer(conn, [{"motif": "navire_liste", "vessel_id": v, "navire": nom, "lon": lon, "lat": lat}
                                 for v, lon, lat, nom in rows], rules)
    if ecartes is not None:
        ecartes += dropped
    return out


def candidats_essai(conn, session, rules: dict, now: datetime, heures: int = 36) -> list[dict]:
    """Passages Sentinel 1 récents qui couvrent un navire des listes (essai réel) : points, scènes, fenêtres, part des
    images lue et coût estimés, sans rien lancer ni écrire."""
    from mars.viirs import regions_boxes
    s = rules["sentinel"]
    boxes = regions_boxes()
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    out = []
    with traces.span("sentinel.candidats_essai", **{"sentinel.heures": heures}) as sp:
        rows = conn.execute(
            """SELECT id, satellite, acquired_at, ended_at, cardinality(watch_ids) FROM sar_passes
               WHERE mission = 'S1' AND coalesce(mode, 'IW') = 'IW' AND statut = 'acquis'
                 AND cardinality(watch_ids) > 0 AND acquired_at BETWEEN %s AND %s
                 AND NOT EXISTS (SELECT 1 FROM analyses a WHERE a.pass_id = sar_passes.id AND a.source = 'sentinel1'
                                 AND a.status <> 'failed')
               ORDER BY acquired_at DESC LIMIT 10""", (now - timedelta(hours=heures), now)).fetchall()
        for pid, sat, t0, t1, n_listes in rows:
            ecartes: list[dict] = []
            points = interest_points(conn, pid, rules, ecartes) or points_liste(conn, pid, rules, ecartes)
            vus, uniques = set(), []
            for e in ecartes:                       # un navire écarté par les deux recherches n'est montré qu'une fois
                if e.get("vessel_id") not in vus:
                    vus.add(e.get("vessel_id"))
                    uniques.append(e)
            scenes = stac_scenes(session, sat or "", t0 - timedelta(minutes=2), (t1 or t0) + timedelta(minutes=2),
                                 box) if points else []
            chosen = pick_scenes(scenes, points, s["interet_rayon_m"]) if points else []
            fen = [(sc, fenetres(sc["emprise"], sc["points"], s)) for sc in chosen]
            n_fen = sum(len(f) for _sc, f in fen)
            octets = sum(part_de_scene(f, sc["emprise"]) * sc["octets"] for sc, f in fen if f)
            calculs, motif = strategie(n_fen, 0, s, now, now) if n_fen else ((), "rien à analyser")
            out.append({"passage": pid, "satellite": sat, "acquis_le": t0, "navires_des_listes": n_listes,
                        "points": len(points), "scenes": len(chosen), "fenetres": n_fen,
                        "km2": sum(x["km2"] for _sc, f in fen for x in f), "octets_estimes": int(octets),
                        "octets_scenes": sum(sc["octets"] for sc in chosen), "points_ecartes": uniques,
                        "calcul_prevu": motif,
                        "cout_estime_eur": estimation(rules, s, n_fen, 0, calculs[0]) if n_fen else None})
        sp.set_attribute("sentinel.candidats", len(out))
    return out


# Budget mensuel partagé (lot S2) : Sentinel 1 et Sentinel 2 puisent dans sentinel.budget_mensuel_eur ; chacun garde
# son plafond quotidien de scènes
TACHES_BUDGET = ("sentinel", "sentinel2")
SOURCES_BUDGET = ("sentinel1", "sentinel2")


def usage(conn, now: datetime, source: str = "sentinel1") -> tuple[int, float]:
    """Scènes de `source` déjà engagées aujourd'hui (analyses non échouées) et euros engagés ce mois ci par Sentinel 1 et
    Sentinel 2 ensemble (coût mesuré des sessions finies, coût estimé de celles en cours)."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    scenes = conn.execute(
        "SELECT coalesce(sum(jsonb_array_length(scenes)), 0) FROM analyses "
        "WHERE source = %s AND status <> 'failed' AND requested_at >= %s", (source, day)).fetchone()[0]
    # Coût réel de chaque travailleur Sentinel du mois (journal, une fois détruit : un GPU inutilisable compte, une
    # rupture de stock vaut 0), plus le coût estimé des analyses dont le travailleur tourne encore (lot C4)
    spent = conn.execute(
        """SELECT coalesce((SELECT sum(coalesce(cout_reel_eur, (mesures->>'cout_estime_eur')::float8)) FROM travailleurs
                            WHERE tache = ANY(%(t)s) AND cree_le >= %(m)s AND detruit_le IS NOT NULL), 0)
                + coalesce((SELECT sum(a.cout_estime_eur) FROM analyses a JOIN travailleurs t ON t.id = a.travailleur_id
                            WHERE a.source = ANY(%(s)s) AND t.detruit_le IS NULL AND t.cree_le >= %(m)s), 0)""",
        {"m": month, "t": list(TACHES_BUDGET), "s": list(SOURCES_BUDGET)}).fetchone()[0]
    return int(scenes), float(spent)


def plan(conn, session, rules: dict, now: datetime, pass_id: int | None = None) -> dict:
    """Passages à analyser maintenant, avec leurs scènes et leurs motifs. `pass_id` : passage choisi à la main (sans
    points d'intérêt requis : toutes ses scènes sur nos régions, dans la limite du plafond)."""
    from mars.viirs import regions_boxes
    s = rules["sentinel"]
    if pass_id is not None:
        rows = conn.execute("SELECT id, satellite, acquired_at, ended_at, statut, mode FROM sar_passes WHERE id = %s",
                            (pass_id,)).fetchall()
        if not rows:
            return {"refus": f"passage {pass_id} inconnu"}
        if (rows[0][5] or "IW") != "IW" or not (rows[0][1] or "").startswith("S1"):
            return {"refus": f"passage {pass_id} : Sentinel 1 en mode IW seulement ({rows[0][1]}, {rows[0][5]})"}
    else:
        rows = conn.execute(
            """SELECT sp.id, sp.satellite, sp.acquired_at, sp.ended_at, sp.statut, sp.mode FROM sar_passes sp
               WHERE sp.mission = 'S1' AND coalesce(sp.mode, 'IW') = 'IW'
                 AND sp.acquired_at BETWEEN %s AND %s
                 AND NOT EXISTS (SELECT 1 FROM analyses a WHERE a.pass_id = sp.id AND a.source = 'sentinel1'
                                 AND a.status <> 'failed')
               ORDER BY sp.acquired_at""",
            (now - timedelta(hours=s["passages_recents_h"]), now)).fetchall()
        failed = set(launch_failures_today(conn, now))
        rows = [r for r in rows if r[0] not in failed]
    boxes = regions_boxes()
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    out, waiting, skipped, ecartes_par = [], [], 0, {}
    for pid, sat, t0, t1, _statut, _mode in rows:
        ecartes: list[dict] = []
        points = interest_points(conn, pid, rules, ecartes)
        if ecartes:
            ecartes_par[pid] = ecartes
        if pass_id is None and not points:
            skipped += 1                          # aucun point, ou seulement des navires à quai : pas d'analyse
            continue
        scenes = stac_scenes(session, sat or "", t0 - timedelta(minutes=2),
                             (t1 or t0) + timedelta(minutes=2), box)
        if not scenes:
            if now - t0 < timedelta(hours=s["attente_publication_h"]):
                waiting.append(pid)
            continue
        if pass_id is not None and not points:
            # Passage choisi à la main : navires des listes dans l'emprise (fenêtres), sinon scènes entières
            points = points_liste(conn, pid, rules, ecartes)
            if ecartes:
                ecartes_par[pid] = ecartes
        if pass_id is not None and not points and ecartes:
            noms = ", ".join(str(e.get("navire") or e.get("vessel_id")) for e in ecartes)
            return {"refus": f"passage {pid} : aucun point en mer, navire(s) des listes {MOTIF_A_QUAI} ({noms})",
                    "points_ecartes": ecartes}
        if pass_id is not None and not points:
            chosen = [{**sc, "points": []} for sc in sorted(scenes, key=lambda sc: sc["debut"])]
        else:
            chosen = pick_scenes(scenes, points, s["interet_rayon_m"])
        if chosen:
            # Scènes des demandes d'observation d'abord (par priorité du tip), puis les plus riches en points
            from mars.tip_and_cue import priorite
            chosen.sort(key=lambda sc: -priorite(sc["points"]))
            out.append({"pass_id": pid, "satellite": sat, "acquired_at": t0, "scenes": chosen, "points": points})
    # Plafond quotidien : passages portant une demande d'observation d'abord, par priorité du tip (lot T1)
    from mars.tip_and_cue import priorite
    out.sort(key=lambda ps: (-priorite(ps["points"]), ps["acquired_at"]))
    return {"passages": out, "en_attente_de_publication": waiting, "sans_interet": skipped,
            "points_ecartes": ecartes_par}


# Fenêtres utiles (lot C3) : seules ces zones des images sont lues et analysées

def _carre(lon: float, lat: float, demi_m: float) -> list[float]:
    dlat, dlon = demi_m / 110_574.0, demi_m / (111_320.0 * math.cos(math.radians(lat)))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]


def _chevauche(a: list, b: list) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def fenetres(emprise: dict, points: list[dict], s: dict) -> list[dict]:
    """Fenêtres à analyser dans une scène : autour de chaque point d'intérêt qu'elle couvre, un carré de
    2 × (interet_rayon_m + fenetre_marge_m) de côté (au moins fenetre_cote_min_m) ; les carrés qui se chevauchent
    sont réunis en un rectangle, jusqu'à ce qu'aucun ne se chevauche plus. Chaque fenêtre : emprise (lon_min, lat_min,
    lon_max, lat_max), marge (les détections à moins de la marge du bord sont écartées par le travailleur), points."""
    demi = max(s["interet_rayon_m"] + s["fenetre_marge_m"], s["fenetre_cote_min_m"] / 2)
    boites = [{"bbox": _carre(p["lon"], p["lat"], demi), "points": 1} for p in points
              if covers(emprise, p["lon"], p["lat"], s["interet_rayon_m"])]
    fusion = True
    while fusion:
        fusion = False
        for a in range(len(boites)):
            for b in range(a + 1, len(boites)):
                if _chevauche(boites[a]["bbox"], boites[b]["bbox"]):
                    x, y = boites[a]["bbox"], boites.pop(b)["bbox"]
                    boites[a]["bbox"] = [min(x[0], y[0]), min(x[1], y[1]), max(x[2], y[2]), max(x[3], y[3])]
                    boites[a]["points"] += 1
                    fusion = True
                    break
            if fusion:
                break
    out = []
    for f in sorted(boites, key=lambda f: (f["bbox"][1], f["bbox"][0])):
        x0, y0, x1, y1 = f["bbox"]
        km2 = (_hav_m(x0, y0, x1, y0) / 1000) * (_hav_m(x0, y0, x0, y1) / 1000)
        out.append({"bbox": [round(v, 5) for v in f["bbox"]], "marge_m": s["fenetre_marge_m"], "points": f["points"],
                    "km2": round(km2)})
    return out


def part_de_scene(fen: list[dict], emprise: dict) -> float:
    """Part de la surface de la scène couverte par ses fenêtres (estimation des octets lus)."""
    polys = emprise["coordinates"] if emprise["type"] == "MultiPolygon" else [emprise["coordinates"]]
    xs = [q[0] for poly in polys for q in poly[0]]
    ys = [q[1] for poly in polys for q in poly[0]]
    lat = sum(ys) / len(ys)
    scene_km2 = (max(xs) - min(xs)) * 111.32 * math.cos(math.radians(lat)) * (max(ys) - min(ys)) * 110.57 * 0.75
    return min(1.0, sum(f["km2"] for f in fen) / max(scene_km2, 1.0))


# Coût et choix du calcul (lot C3)

def cout_calcul(n_fenetres: int, n_scenes_entieres: int, s: dict, t: dict, calcul: str) -> float:
    """Coût estimé d'une session selon le type réellement lancé (GPU ou processeur) et le nombre de fenêtres : partie
    fixe, puis par fenêtre (ou par scène entière, analyse manuelle sans point d'intérêt, GPU seulement)."""
    from mars.travailleurs import cost, granularite_min
    if calcul == "cpu":
        minutes = s["minutes_fixes_cpu"] + n_fenetres * s["minutes_par_fenetre_cpu"]
    else:
        minutes = (s["minutes_fixes"] + n_fenetres * s["minutes_par_fenetre_gpu"]
                   + n_scenes_entieres * s["minutes_par_scene"])
    return cost(minutes, t["prix_heure"], granularite_min(t))


def duree_cpu(n_fenetres: int, s: dict) -> int:
    """Durée de vie d'un travailleur sur processeur, d'après l'essai réel du 19/10/2026 : démarrage, Docker et image,
    3 min par fenêtre, plus marge (au lieu de 150 min fixes)."""
    return int(s["minutes_fixes_cpu"] + n_fenetres * s["minutes_par_fenetre_cpu"] + s["cpu_marge_min"])


MOTIF_PETIT, MOTIF_GRAND, MOTIF_LOTS = ("petit passage : processeur", "grand passage : GPU",
                                        "GPU en rupture : processeur par lots")


def strategie(n_fen: int, n_full: int, s: dict, attente_depuis: datetime, now: datetime,
              force: str | None = None) -> tuple[tuple[str, ...], str]:
    """Choix du calcul (lot C5, processeur en premier) : (calculs à essayer dans l'ordre, motif). Petit passage
    (fenetres_max_cpu fenêtres au plus, aucune scène entière) : processeur d'emblée, sans essayer de GPU. Au delà :
    GPU ; en rupture de GPU depuis repli_cpu_apres_h, processeur par lots de fenetres_max_cpu fenêtres. Une scène
    entière ne va jamais sur processeur. `force` (SENTINEL_CALCUL_ESSAI, commande manuelle) : cpu ou gpu seulement."""
    if force == "cpu":
        return ("cpu",), "essai forcé sur processeur (SENTINEL_CALCUL_ESSAI=cpu, commande manuelle) : fenêtres seules"
    if force == "gpu":
        return ("gpu",), "essai forcé sur GPU (SENTINEL_CALCUL_ESSAI=gpu, commande manuelle)"
    if n_full == 0 and 0 < n_fen <= s["fenetres_max_cpu"]:
        return ("cpu",), MOTIF_PETIT
    if n_full == 0 and now - attente_depuis >= timedelta(hours=s["repli_cpu_apres_h"]):
        return ("gpu", "lots"), MOTIF_LOTS
    return ("gpu",), MOTIF_GRAND


def candidats(scw, rules: dict, calculs: tuple[str, ...] = ("gpu",), incompatibles: dict | None = None,
              eprouves: set | None = None, essai_explicite: bool = False) -> tuple[list, dict]:
    """Types à essayer : ceux des `calculs` demandés (gpu, puis cpu pour « lots » ou « cpu »), dans l'ordre de
    config/rules.yaml, dont le stock n'est pas épuisé (disponibilité lue avant toute création, API Instances).
    Retourne (variantes, disponibilités par « type zone »). Sans client Scaleway (essais), disponibilité inconnue : on
    essaie. Garde de dépense (lot C5) : un type facturé à l'heure entamée et plus cher que prix_max_essai_eur n'est
    jamais lancé tant qu'aucun travailleur n'a abouti sur lui (`eprouves`), sauf essai explicite."""
    from mars import travailleurs
    base = rules["travailleurs"]
    dispo, par_zone = {}, {}
    voulus = ["cpu" if c == "lots" else c for c in calculs]
    out = []
    toutes = travailleurs.variantes(rules, "sentinel")
    for calc in voulus:
        for v in (x for x in toutes if x["calcul"] == calc):
            t = travailleurs.type_params(rules, "sentinel", v)
            zone = t.get("zone", base["zone"])
            cle = f"{t['commercial_type']} {zone}"
            fin = (incompatibles or {}).get((t["commercial_type"], zone))
            if fin is not None:                 # lot C4 : GPU inutilisable constaté, type sauté jusqu'à la fin du marquage
                dispo[cle] = f"incompatible jusqu'au {fin:%d/%m/%Y %H:%M}"
                continue
            motif = travailleurs.garde_essai(rules, t, eprouves, essai_explicite)
            if motif:
                dispo[cle] = motif
                continue
            if scw is not None and zone not in par_zone:
                with traces.span("sentinel.disponibilite", **{"scaleway.zone": zone}) as sp:
                    try:
                        par_zone[zone] = scw.availability(zone)
                    except Exception as e:
                        sp.record_exception(e)
                        par_zone[zone] = {}
            etat = (par_zone.get(zone) or {}).get(t["commercial_type"], "inconnue")
            dispo[cle] = etat
            if etat != "shortage":
                out.append(v)
    return out, dispo


def _abandonner(conn, rules: dict, now: datetime) -> list[int]:
    """Analyses en attente d'un GPU dont la scène n'est plus utile (fenetre_utile_h après l'acquisition) : en échec,
    avec leur motif ; la règle d'alerte de Grafana prévient sur le téléphone."""
    h = rules["sentinel"]["fenetre_utile_h"]
    return [r[0] for r in conn.execute(
        """UPDATE analyses a SET status = 'failed', completed_at = now(),
                  error = 'GPU indisponible pendant toute la fenêtre utile de ' || %(h)s || ' h (' || a.essais_calcul
                          || ' essais) : analyse abandonnée'
           FROM sar_passes sp
           WHERE sp.id = a.pass_id AND a.source = 'sentinel1' AND a.status = 'attente_gpu'
             AND sp.acquired_at + make_interval(hours => %(h)s) < %(now)s
           RETURNING a.id""", {"h": h, "now": now}).fetchall()]


def _en_attente(conn, now: datetime) -> list[tuple]:
    """Analyses en attente d'un GPU dont le nouvel essai est dû : (id, scènes, attente_depuis, déclenchement)."""
    return conn.execute(
        "SELECT id, scenes, attente_depuis, declenchement FROM analyses WHERE source = 'sentinel1' "
        "AND status = 'attente_gpu' AND (prochain_essai IS NULL OR prochain_essai <= %s) ORDER BY id", (now,)).fetchall()


def launch(conn, scw, rules: dict, now: datetime, session, pass_id: int | None = None, log=print) -> dict:
    """Une session de calcul pour les passages à analyser : analyses en attente d'un GPU reprises d'abord (tant que
    leur scène reste utile), sinon passages nouveaux. Plafond de scènes et budget vérifiés, une analyse par passage
    (index unique), clés des travailleurs vérifiées, puis le premier type disponible de la liste (GPU, puis processeur
    après repli_cpu_apres_h). Rupture de stock : analyses en « attente_gpu », nouvel essai au cycle suivant. Retourne le
    résumé ; {"refus": motif} si un garde fou l'interdit."""
    s = dict(rules["sentinel"])
    # Essai forcé (lot C4 pour cpu, lot C5 pour gpu) : commande manuelle seulement ; la boucle n'est pas touchée
    force = (os.environ.get("SENTINEL_CALCUL_ESSAI") or "").strip().lower() if pass_id is not None else ""
    s["_force"] = force if force in ("cpu", "gpu") else None
    if s["_force"]:
        traces.attributs(**{"sentinel.calcul_force": s["_force"]})
    essai = os.environ.get("SENTINEL_PLAFOND_ESSAI")
    if pass_id is not None and essai:
        # Essai réel à la main : plafond relevé pour cette commande seulement (rien n'est écrit dans config/rules.yaml)
        s["scenes_max_par_jour"] = int(essai)
        traces.attributs(**{"sentinel.plafond_essai": int(essai)})
    with traces.span("sentinel.attente") as sp:
        abandon = _abandonner(conn, rules, now)
        attente = _en_attente(conn, now) if pass_id is None else []
        sp.set_attributes({"sentinel.abandonnees": len(abandon), "sentinel.en_attente": len(attente)})
    if abandon:
        log(f"Sentinel 1 : analyse(s) {abandon} abandonnée(s), GPU indisponible pendant toute la fenêtre utile")
    if attente:
        return {**_reprendre(conn, scw, rules, s, now, attente, log), "abandonnees": abandon}
    with traces.span("sentinel.selection", **{"sentinel.passage_demande": pass_id}) as sp:
        p = plan(conn, session, rules, now, pass_id)
        sp.set_attribute("sentinel.passages", len(p.get("passages") or []))
    if "refus" in p:
        return p
    if not p["passages"]:
        return {"passages": 0, "abandonnees": abandon, **{k: v for k, v in p.items() if k != "passages"}}
    scenes_today, spent = usage(conn, now)
    room = max(0, s["scenes_max_par_jour"] - scenes_today)
    # Plafond : les scènes les plus riches en points d'intérêt d'abord, passage par passage
    selected, dropped = [], 0
    for ps in p["passages"]:
        keep = ps["scenes"][:max(0, room - sum(len(x["scenes"]) for x in selected))]
        dropped += len(ps["scenes"]) - len(keep)
        if keep:
            selected.append({**ps, "scenes": keep})
    n, asked = sum(len(x["scenes"]) for x in selected), sum(len(x["scenes"]) for x in p["passages"])
    # Demandes d'observation (lot T1) : celles dont aucune fenêtre n'a trouvé place sous le plafond sont refusées avec
    # leur motif ; les autres seront portées par l'analyse de leur passage
    from mars import tip_and_cue

    def demandes(scenes):
        return sorted({pt["demande"] for sc in scenes for pt in sc.get("points") or [] if pt.get("demande")})
    gardees = {ps["pass_id"]: demandes(ps["scenes"]) for ps in selected}
    toutes = sorted({d for ps in p["passages"] for d in demandes(ps["scenes"])})
    tip_and_cue.refuser(conn, [d for d in toutes if d not in {x for v in gardees.values() for x in v}],
                        f"plafond de {s['scenes_max_par_jour']} scènes par jour atteint")
    # Fenêtres utiles de chaque scène (passage choisi à la main sans point d'intérêt : scène entière)
    with traces.span("sentinel.fenetres") as sp:
        for ps in selected:
            for sc in ps["scenes"]:
                sc["fenetres"] = fenetres(sc["emprise"], sc.get("points") or [], s) if sc.get("points") else []
                for k, f in enumerate(sc["fenetres"]):
                    f["id"] = k                 # lots sur processeur : fenêtres faites suivies par identifiant
        n_fen = sum(len(sc["fenetres"]) for ps in selected for sc in ps["scenes"])
        n_full = sum(1 for ps in selected for sc in ps["scenes"] if not sc["fenetres"])
        sp.set_attributes({"sentinel.fenetres": n_fen, "sentinel.scenes_entieres": n_full,
                           "sentinel.fenetres_km2": sum(f["km2"] for ps in selected for sc in ps["scenes"]
                                                        for f in sc["fenetres"])})
    if s["_force"] == "cpu" and n_full:
        return {"refus": "essai forcé sur processeur : aucune fenêtre pour ce passage (aucun navire des listes ni point "
                         "d'intérêt), une scène entière n'est jamais analysée sur processeur"}
    calculs, _m = strategie(n_fen, n_full, s, now, now, s["_force"])
    estimate = estimation(rules, s, n_fen, n_full, calculs[0])
    motif = guard(n or asked, scenes_today, spent, estimate, s)       # aucune place : le plafond est le motif
    if motif:
        tip_and_cue.refuser(conn, toutes, motif)                       # plafond ou budget : refus avec son motif
        return {"refus": motif, "scenes_demandees": asked}
    import psycopg
    # Paire dédiée aux travailleurs : présente, non expirée, et acceptée par Copernicus (lecture réelle de 16 octets),
    # avant tout appel à Scaleway
    with traces.span("sentinel.verification_cles"):
        motif = pair_expired(TRAVAILLEUR, now) or check_cdse(session, selected[0]["scenes"][0]["vv"], TRAVAILLEUR)
    if motif:
        return {"refus": motif}
    analyses = []
    try:
        with conn.transaction():
            for ps in selected:
                env = _envelope(ps["scenes"])
                motifs = sorted({pt["motif"] for pt in ps["points"]}) or ["demande"]
                aid = conn.execute(
                    """INSERT INTO analyses (pass_id, aoi, mode, status, model_version, source, scenes, declenchement,
                                             cout_estime_eur, requested_at, trace_id, fenetres, attente_depuis)
                       VALUES (%s, ST_MakeEnvelope(%s, %s, %s, %s, 4326)::geography, 'full', 'pending', %s, 'sentinel1',
                               %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (ps["pass_id"], *env, "allenai/vessel-detection-sentinels fafc6c3",
                     json.dumps([{k: sc[k] for k in ("id", "debut", "fin", "orbite", "satellite", "emprise", "octets",
                                                     "vv", "vh", "fenetres")} for sc in ps["scenes"]]),
                     json.dumps({"motifs": motifs, "points": ps["points"]}, default=str),
                     round(estimate * len(ps["scenes"]) / n, 3), now, traces.trace_id(),
                     json.dumps({sc["id"]: sc["fenetres"] for sc in ps["scenes"]}), now)).fetchone()[0]
                analyses.append((aid, ps["scenes"]))
                tip_and_cue.rattacher(conn, ps["pass_id"], aid, gardees.get(ps["pass_id"], []))
    except psycopg.errors.UniqueViolation:
        return {"en_cours": "passage déjà en analyse (lancement concurrent)"}
    r = _calculer(conn, scw, rules, s, now, analyses, now, spent, log)
    return {**r, "analyses": [a for a, _ in analyses], "scenes": n, "scenes_ecartees_par_le_plafond": dropped,
            "fenetres": n_fen, "abandonnees": abandon, "en_attente_de_publication": p["en_attente_de_publication"]}


def estimation(rules: dict, s: dict, n_fen: int, n_full: int, calcul: str) -> float:
    """Coût estimé avec le premier type du calcul choisi (processeur : un lot de fenetres_max_cpu fenêtres au plus)."""
    from mars import travailleurs
    v = next(x for x in travailleurs.variantes(rules, "sentinel") if x["calcul"] == calcul)
    t = travailleurs.type_params(rules, "sentinel", v)
    if calcul == "cpu":
        return cout_calcul(min(n_fen, s["fenetres_max_cpu"]), 0, s, t, "cpu")
    return cout_calcul(n_fen, n_full, s, t, "gpu")


def restantes(fenetres_scene: list[dict], faites: list) -> list[dict]:
    """Fenêtres d'une scène pas encore analysées (lots sur processeur)."""
    return [f for k, f in enumerate(fenetres_scene) if f.get("id", k) not in set(faites or [])]


def _reprendre(conn, scw, rules: dict, s: dict, now: datetime, attente: list, log) -> dict:
    """Nouvel essai pour les analyses en attente d'un GPU : même tâche, nouveau choix du type."""
    analyses = [(aid, scenes or []) for aid, scenes, _d, _m in attente]
    depuis = min((d for _a, _s, d, _m in attente if d), default=now)
    _scenes, spent = usage(conn, now)
    r = _calculer(conn, scw, rules, s, now, analyses, depuis, spent, log)
    return {**r, "analyses": [a for a, _ in analyses], "reprise": True}


def _calculer(conn, scw, rules: dict, s: dict, now: datetime, analyses: list, attente_depuis: datetime,
              spent: float, log) -> dict:
    """Choisit le calcul (strategie : processeur en premier pour un petit passage, GPU pour un grand, processeur par
    lots en rupture de GPU) et lance le travailleur. Chaque décision est un span de la trace de l'analyse :
    disponibilités, type retenu et motif, rupture de stock, garde de dépense, mise en attente."""
    from mars import travailleurs
    from mars.viirs import regions_boxes
    ids = [a for a, _ in analyses]
    faites = dict(conn.execute("SELECT id, fenetres_faites FROM analyses WHERE id = ANY(%s)", (ids,)).fetchall())
    tasks = [{"analyse": aid, **{k: sc.get(k) for k in ("id", "debut", "fin", "orbite", "satellite", "vv", "vh")},
              "fenetres": restantes(sc.get("fenetres") or [], (faites.get(aid) or {}).get(sc.get("id"))),
              "entiere": not sc.get("fenetres")} for aid, scs in analyses for sc in scs]
    tasks = [t for t in tasks if t["entiere"] or t["fenetres"]]
    n_fen = sum(len(t["fenetres"]) for t in tasks)
    n_full = sum(1 for t in tasks if t["entiere"])
    force = s.get("_force")
    calculs, motif_strategie = strategie(n_fen, n_full, s, attente_depuis, now, force)
    with traces.span("sentinel.choix_calcul", **{"sentinel.analyses": len(ids), "sentinel.fenetres": n_fen,
                                                 "sentinel.attente_min": round((now - attente_depuis).total_seconds() / 60),
                                                 "sentinel.calcul_force": force,
                                                 "sentinel.strategie": motif_strategie}) as sp:
        eprouves = travailleurs.eprouves(conn, "sentinel")
        cands, dispo = candidats(scw, rules, calculs, travailleurs.incompatibles(conn), eprouves, bool(force))
        sp.set_attributes({(f"incompatible.{k}" if str(v).startswith("incompatible") else f"dispo.{k}"): v
                           for k, v in dispo.items()})
        essais = []
        for v in cands:
            t = travailleurs.type_params(rules, "sentinel", v)
            zone = t.get("zone", rules["travailleurs"]["zone"])
            lot = tasks
            if v["calcul"] == "cpu":
                # Processeur : fenêtres seules, au plus fenetres_max_cpu par session (lots en rupture de GPU)
                lot, reste = [], s["fenetres_max_cpu"]
                for tk in tasks:
                    if reste > 0 and tk["fenetres"]:
                        lot.append({**tk, "fenetres": tk["fenetres"][:reste]})
                        reste -= len(lot[-1]["fenetres"])
            n_lot = sum(len(tk["fenetres"]) for tk in lot)
            est = cout_calcul(n_lot, 0 if v["calcul"] == "cpu" else n_full, s, t, v["calcul"])
            if spent + est > s["budget_mensuel_eur"]:
                essais.append(f"{t['commercial_type']} : budget mensuel dépassé ({est:.2f} € estimés)".replace(".", ","))
                continue
            variante = {**v, "cout_prevu_eur": est}
            if v["calcul"] == "cpu":
                variante["duree_max_min"] = duree_cpu(n_lot, s)
            params = {"scenes": [{k: x for k, x in tk.items() if k != "entiere"} for tk in lot],
                      "regions": regions_boxes(), "confiance": s["confiance"], "nms_m": s["nms_m"],
                      "vignette_km": s["vignette_km"], "force_cpu": v["calcul"] == "cpu"}
            with traces.span("sentinel.essai_type", **{"sentinel.type": t["commercial_type"], "scaleway.zone": zone,
                                                       "sentinel.calcul": v["calcul"], "sentinel.cout_estime": est,
                                                       "sentinel.fenetres_lot": n_lot}) as se:
                try:
                    r = travailleurs.launch(conn, scw, rules, "sentinel", params, {}, now=now, log=log,
                                            variante=variante)
                except Exception as e:
                    w = conn.execute("SELECT id FROM travailleurs WHERE tache = 'sentinel' AND cree_le >= %s "
                                     "ORDER BY id DESC LIMIT 1", (now - timedelta(minutes=5),)).fetchone()
                    conn.execute("UPDATE analyses SET status = 'failed', error = %s, completed_at = now(), "
                                 "travailleur_id = %s WHERE id = ANY(%s)",
                                 (f"lancement du travailleur : {e}"[:1000], w and w[0], ids))
                    raise
                if "rupture" in r:
                    se.set_attribute("sentinel.rupture_stock", True)
                    essais.append(f"{t['commercial_type']} ({zone}) : rupture de stock à la création ou au démarrage")
                    continue
            if "travailleur" not in r:
                conn.execute("UPDATE analyses SET status = 'failed', error = %s, completed_at = now() WHERE id = ANY(%s)",
                             (r.get("refus") or f"travailleur occupé ({r.get('occupe')})", ids))
                return r
            if force:
                motif = motif_strategie
            elif v["calcul"] == "cpu" and "lots" in calculs:
                motif = (f"{MOTIF_LOTS} : aucun GPU de la liste depuis "
                         f"{(now - attente_depuis).total_seconds() / 3600:.1f} h, lot de {n_lot} fenêtre(s) sur {n_fen}")
            elif v["calcul"] == "cpu":
                motif = f"{MOTIF_PETIT} ({n_fen} fenêtre(s))"
            else:
                motif = MOTIF_GRAND
            if essais:
                motif += f" ({' ; '.join(essais)})"
            sp.set_attributes({"sentinel.calcul": v["calcul"], "sentinel.type": t["commercial_type"],
                               "sentinel.motif": motif[:300]})
            conn.execute(
                "UPDATE analyses SET status = 'running', travailleur_id = %s, calcul = %s, calcul_type = %s, "
                "calcul_motif = %s, essais_calcul = essais_calcul + 1, prochain_essai = NULL, "
                "cout_estime_eur = %s / greatest(%s, 1) WHERE id = ANY(%s)",
                (r["travailleur"], v["calcul"], f"{t['commercial_type']} {zone}", motif[:1000], est, len(ids), ids))
            return {**r, "calcul": v["calcul"], "type": t["commercial_type"], "zone": zone, "motif": motif,
                    "cout_estime_eur": est, "fenetres_lot": n_lot}
        # Aucun type n'a pu être lancé : attente, nouvel essai au cycle suivant (pas un échec)
        attente = [f"{k} : {'épuisé' if d == 'shortage' else d}" for k, d in dispo.items()] + essais
        quoi = "processeur" if calculs == ("cpu",) else "GPU"
        motif = f"{motif_strategie}, {quoi} indisponible chez Scaleway (" + " ; ".join(attente) + ")"
        prochain = now + timedelta(minutes=s["intervalle_min"])
        sp.set_attributes({"sentinel.calcul": "attente", "sentinel.motif": motif[:300]})
        conn.execute("UPDATE analyses SET status = 'attente_gpu', attente_depuis = coalesce(attente_depuis, %s), "
                     "essais_calcul = essais_calcul + 1, prochain_essai = %s, calcul_motif = %s WHERE id = ANY(%s)",
                     (attente_depuis, prochain, motif[:1000], ids))
        log(f"Sentinel 1 : {motif} ; nouvel essai dans {s['intervalle_min']} min")
        return {"attente_gpu": ids, "motif": motif, "prochain_essai_min": s["intervalle_min"]}


def _envelope(scenes: list[dict]) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for sc in scenes:
        g = sc["emprise"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            for x, y in (q[:2] for q in poly[0]):
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


# Résultats du travailleur

def ingest(conn, w: dict, rules: dict) -> dict:
    """Résultat d'un travailleur Sentinel : détections en base, vignettes sur R2, fusion avec l'AIS, alertes. Appelé
    par la surveillance (mars/travailleurs.supervise) dans une transaction : tout ou rien."""
    res = w["resultat"] or {}
    out = {"scenes": 0, "detections": 0, "vignettes": 0, "appariees": 0, "alertes_dark_ship": 0,
           "alertes_non_confirme": 0, "alertes_incoherence": 0, "non_evaluables": 0, "echos_fixes": 0}
    by_analysis: dict[int, list[dict]] = {}
    termines: list[int] = []                       # analyses finies : résultat des demandes d'observation (lot T1)
    for sc in res.get("scenes", []):
        by_analysis.setdefault(int(sc["analyse"]), []).append(sc)
    images = []
    for aid, scenes in by_analysis.items():
        errors = [f"{sc['id']} : {sc['erreur']}" for sc in scenes if sc.get("erreur")]
        for sc in scenes:
            if sc.get("erreur"):
                continue
            out["scenes"] += 1
            t_scene = _mid(sc["debut"], sc["fin"])
            for d in sc.get("detections", []):
                cle = f"{sc['id']}:{d['lon']:.5f}:{d['lat']:.5f}"
                did = conn.execute(
                    """INSERT INTO detections (analysis_id, geom, objectness, vessel_score, fishing_score, length_m,
                                               contrast_vv_db, scene, ts, score, width_m, heading_deg, heading_prob,
                                               speed_kn, cle)
                       VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s, %s)
                       ON CONFLICT (analysis_id, cle) WHERE cle IS NOT NULL DO NOTHING RETURNING id""",
                    (aid, d["lon"], d["lat"], d.get("score"), d.get("score"), d.get("peche"), d.get("longueur_m"),
                     d.get("contraste_db"), sc["id"], t_scene, d.get("score"), d.get("largeur_m"), d.get("cap_deg"),
                     d.get("cap_prob"), d.get("vitesse_kn"), cle)).fetchone()
                if did is None:
                    continue                              # déjà écrite (résultat reçu deux fois)
                out["detections"] += 1
                if d.get("vignette"):
                    images.append((did[0], d["vignette"], d.get("vignette_geo"), t_scene, sc["id"]))
        # Lots sur processeur (lot C5) : fenêtres faites ajoutées ; s'il en reste, l'analyse repart en attente pour le
        # lot suivant, et la fusion n'a lieu qu'une fois toutes les fenêtres analysées (sinon les échos fixes
        # seraient comptés à chaque lot)
        prev = conn.execute("SELECT fenetres, fenetres_faites, timings, octets_lus FROM analyses WHERE id = %s",
                            (aid,)).fetchone()
        prevues, faites = (prev[0] or {}) if prev else {}, dict((prev[1] or {}) if prev else {})
        for sc in scenes:
            if sc.get("fenetres"):
                recues = {f.get("index") for f in sc["fenetres"] if f.get("index") is not None}
            else:                                     # scène analysée sans détail par fenêtre : toutes faites
                recues = {f.get("id", k) for k, f in enumerate(prevues.get(sc["id"]) or [])}
            faites[sc["id"]] = sorted(set(faites.get(sc["id"], [])) | recues)
        reste = sum(len(restantes(fs, faites.get(sid))) for sid, fs in prevues.items())
        mes = dict((prev[2] or {}) if prev and isinstance(prev[2], dict) else {})
        for sc in scenes:                             # fenêtres : taille, octets, durées, détections, motif d'échec
            m = {k: sc.get(k) for k in ("telecharge_s", "telecharge_mo", "analyse_s", "gpu_max_mo", "erreur",
                                         "octets_lus", "ecartees_marge", "image_px")}
            if sc.get("fenetres"):
                m["fenetres"] = (mes.get(sc["id"]) or {}).get("fenetres", []) + [
                    {k: f.get(k) for k in ("index", "srcwin", "octets", "lecture_s", "analyse_s", "detections",
                                           "ecartees_marge", "erreur")} for f in sc["fenetres"]]
            mes[sc["id"]] = m
        octets = (sum(int(sc.get("octets_lus") or 0) for sc in scenes) + int((prev[3] or 0) if prev else 0)) or None
        if reste and not errors:
            fait = sum(len(v) for v in faites.values())
            conn.execute("UPDATE analyses SET status = 'attente_gpu', fenetres_faites = %s, timings = %s, octets_lus = %s, "
                         "prochain_essai = NULL, calcul_motif = %s WHERE id = %s",
                         (json.dumps(faites), json.dumps(mes), octets,
                          f"{MOTIF_LOTS} : {fait} fenêtre(s) faite(s), {reste} restante(s), lot suivant au prochain "
                          "cycle", aid))
            traces.attributs(**{"sentinel.lot_fenetres_faites": fait, "sentinel.lot_fenetres_restantes": reste})
            continue
        with traces.span("sentinel.fusion_et_alertes", **{"sentinel.analyse": aid, "sentinel.scenes": len(scenes)}) as sp:
            summary = fuse_analysis(conn, aid, rules) if any(not sc.get("erreur") for sc in scenes) else {}
            sp.set_attributes({f"sentinel.{k}": v for k, v in summary.items() if isinstance(v, (int, float))})
        for k in ("appariees", "alertes_dark_ship", "alertes_non_confirme", "alertes_incoherence", "non_evaluables",
                  "echos_fixes"):
            out[k] += summary.get(k, 0)
        conn.execute("UPDATE analyses SET fenetres_faites = %s WHERE id = %s", (json.dumps(faites), aid))
        status = "done" if len(errors) < len(scenes) else "failed"
        termines.append(aid)
        conn.execute("UPDATE analyses SET status = %s, completed_at = now(), summary = %s, timings = %s, error = %s, "
                     "octets_lus = %s WHERE id = %s", (status, json.dumps(summary, default=str), json.dumps(mes),
                                                       "; ".join(errors)[:1000] or None, octets, aid))
        traces.attributs(**{"sentinel.octets_lus": octets})
    with traces.span("sentinel.vignettes", **{"sentinel.vignettes": len(images)}):
        out.update(_store_images(conn, images))
    # Analyses de ce travailleur sans aucun résultat (travailleur arrêté avant) : en échec, avec le motif
    termines += [r[0] for r in conn.execute(
        "UPDATE analyses SET status = 'failed', completed_at = now(), error = coalesce(error, %s) "
        "WHERE travailleur_id = %s AND status IN ('pending', 'running') RETURNING id",
        (res.get("erreur") or "aucun résultat du travailleur pour cette analyse", w["id"])).fetchall()]
    from mars import tip_and_cue
    for aid in termines:
        out.setdefault("demandes_evaluees", []).extend(tip_and_cue.evaluer(conn, aid, rules))
    out["mesures_travailleur"] = res.get("mesures", {})
    return out


def _mid(a: str, b: str) -> datetime:
    t0 = datetime.fromisoformat(a.replace("Z", "+00:00"))
    t1 = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return t0 + (t1 - t0) / 2


def _store_images(conn, images: list[tuple]) -> dict:
    """Vignettes radar sur R2 et au registre des preuves (source sentinel1) ; sans R2, détections gardées sans image."""
    import base64
    from mars import preuves
    if not images:
        return {"vignettes": 0}
    try:
        from mars.r2 import R2
        r2 = R2()
    except Exception as e:
        return {"vignettes": 0, "vignettes_non_rangees": len(images), "vignettes_motif": str(e)[:200]}
    ok = failed = octets = 0
    for did, b64, geo, ts, scene in images:
        png = base64.b64decode(b64)
        try:
            preuves.store(conn, r2, "sentinel1", did, png, geo, ts, preuves.key("sentinel1", f"{ts:%Y-%m-%d}", scene, did))
            ok += 1
            octets += len(png)
        except Exception:
            failed += 1
    return {"vignettes": ok, "vignettes_octets": octets, **({"vignettes_non_rangees": failed} if failed else {})}


def fuse_analysis(conn, aid: int, rules: dict) -> dict:
    """Fusion d'une analyse avec l'AIS, scène par scène (instant du milieu de la scène, 25 s), puis alertes."""
    import pandas as pd
    from mars.fusion.pipeline import fuse
    f, s = rules["fusion"], rules["sentinel"]
    # Contraste facultatif : le modèle d'AI2 filtre déjà ses détections ; une mesure manquante ne les écarte pas
    rules_f = {**rules, "contrast": {**rules["contrast"], "obligatoire": s["contraste_obligatoire"]}}
    out = {"appariees": 0, "alertes_dark_ship": 0, "alertes_non_confirme": 0, "alertes_incoherence": 0,
           "non_evaluables": 0, "echos_fixes": 0, "scenes": {}}
    scenes = conn.execute("SELECT DISTINCT scene, ts FROM detections WHERE analysis_id = %s AND scene IS NOT NULL",
                          (aid,)).fetchall()
    meta_row = conn.execute("SELECT scenes, timings FROM analyses WHERE id = %s", (aid,)).fetchone()
    meta = {sc["id"]: sc for sc in (meta_row[0] or [])}
    timings = meta_row[1] if isinstance(meta_row[1], dict) else {}
    # Scènes sans détection : leurs grands navires AIS doivent quand même être confrontés au radar
    for sid, sc in meta.items():
        if sid not in {x[0] for x in scenes}:
            scenes.append((sid, _mid(sc["debut"], sc["fin"])))
    cells = conn.execute("SELECT EXISTS (SELECT 1 FROM reception_cells)").fetchone()[0]
    for scene, t0 in scenes:
        sc = meta.get(scene) or {}
        foot = json.dumps(sc["emprise"]) if sc.get("emprise") else None
        det_rows = conn.execute(
            """SELECT d.id, ST_X(d.geom::geometry), ST_Y(d.geom::geometry), d.score, d.fishing_score, d.length_m,
                      d.contrast_vv_db, d.width_m, d.heading_deg, d.heading_prob,
                      EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, d.geom, %s)) AS terre
               FROM detections d WHERE d.analysis_id = %s AND d.scene = %s ORDER BY d.id""",
            (float(rules["masks"]["land_buffer_m"]), aid, scene)).fetchall()
        det = pd.DataFrame([{"id": r[0], "lon": r[1], "lat": r[2], "objectness": r[3] or 0.0, "vessel_score": r[3] or 0.0,
                             "fishing_score": r[4], "length_m": r[5], "contrast_vv_db": r[6], "width_m": r[7],
                             "heading_deg": r[8], "heading_prob": r[9], "on_land": bool(r[10])} for r in det_rows],
                           columns=["id", "lon", "lat", "objectness", "vessel_score", "fishing_score", "length_m",
                                    "contrast_vv_db", "width_m", "heading_deg", "heading_prob", "on_land"])
        for col in ("length_m", "contrast_vv_db", "fishing_score"):
            det[col] = pd.to_numeric(det[col], errors="coerce")
        bbox = _envelope([sc]) if sc.get("emprise") else (det.lon.min(), det.lat.min(), det.lon.max(), det.lat.max())
        pos_rows = conn.execute(
            """SELECT p.vessel_id, v.mmsi, v.name, v.length_m, p.ts, ST_Y(p.geom::geometry), ST_X(p.geom::geometry),
                      p.sog_kn, p.cog_deg
               FROM positions p JOIN vessels v ON v.id = p.vessel_id
               WHERE p.ts BETWEEN %s AND %s
                 AND ST_Intersects(p.geom::geometry, ST_Expand(ST_MakeEnvelope(%s, %s, %s, %s, 4326), 0.05))""",
            (t0 - timedelta(minutes=f["ais_window_min"]), t0 + timedelta(minutes=f["ais_window_min"]), *bbox)).fetchall()
        pos = pd.DataFrame(pos_rows, columns=["vessel_id", "mmsi", "name", "length_m", "ts", "lat", "lon", "sog", "cog"])
        if len(pos):
            pos["ts"] = pd.to_datetime(pos.ts, utc=True)
            for col in ("lat", "lon", "sog", "cog", "length_m"):
                pos[col] = pd.to_numeric(pos[col], errors="coerce")
        ascending = (sc.get("orbite") or "").lower().startswith("asc")
        heading = track_heading((bbox[1] + bbox[3]) / 2, ascending) if sc.get("orbite") else None
        fixed = _fixed_points(conn, bbox, t0, rules)
        det_f, _alerts, n_ais, extras = fuse(det, pos, pd.Timestamp(t0), bbox, rules_f, heading, fixed)
        n_fixed = _record_fixed(conn, det_f, extras["fixed_hits"], t0)
        cut = flux_coupe(conn, t0, rules)
        # Analyse par fenêtres (lot C3) : seules les fenêtres lues sont vues ; un grand navire AIS ailleurs dans la scène
        # n'a pas été regardé, son absence d'écho ne prouve rien (défaut corrigé le 21/10/2026)
        lues = fenetres_lues(sc, (timings.get(scene) or {}).get("fenetres"))
        capteur = {"visible": lambda lon, lat, f=lues: dans_coeur(f, lon, lat)} if sc.get("fenetres") else None
        r = _screen_and_alert(conn, aid, scene, t0, sc, det_f, extras, cut, cells, heading, rules, capteur)
        out["appariees"] += int(det_f.matched_vessel_id.notna().sum()) if len(det_f) else 0
        out["echos_fixes"] += n_fixed
        for k in ("alertes_dark_ship", "alertes_non_confirme", "alertes_incoherence", "non_evaluables"):
            out[k] += r[k]
        out["scenes"][scene] = {"detections": len(det_f), "navires_ais": n_ais, "direction_de_vol_deg": heading,
                                "decalages": extras["offsets"], "flux_coupe": cut, "emprise": foot is not None}
    out["critere_evaluation"] = critere(cells)
    return out


def fenetres_lues(sc: dict, mesures: list | None) -> list[dict]:
    """Fenêtres d'une scène réellement analysées : celles prévues (emprise, marge), moins celles en erreur au retour du
    travailleur (hors de l'image, trop petites). Sans mesure par fenêtre, toutes les fenêtres prévues."""
    erreurs = {f.get("index") for f in mesures or [] if f.get("erreur")}
    return [f for k, f in enumerate(sc.get("fenetres") or []) if f.get("id", k) not in erreurs]


def dans_coeur(fenetres_scene: list[dict], lon: float, lat: float) -> bool:
    """Le point est il dans le cœur d'une fenêtre lue (emprise moins sa marge, où les détections sont gardées) ?"""
    for f in fenetres_scene:
        b, m = f["bbox"], f.get("marge_m", 2000)
        dlat, dlon = m / 110_574.0, m / (111_320.0 * math.cos(math.radians((b[1] + b[3]) / 2)))
        if b[0] + dlon <= lon <= b[2] - dlon and b[1] + dlat <= lat <= b[3] - dlat:
            return True
    return False


MOTIF_HORS_FENETRE = ("Navire hors des fenêtres lues de la scène radar : son absence d'écho ne prouve rien (alerte émise "
                      "à tort avant la correction du 21/10/2026)")


def non_confirmees_hors_fenetres(conn, appliquer: bool = False) -> dict:
    """Alertes AIS_UNCONFIRMED de Sentinel 1 émises hors des fenêtres lues (défaut corrigé le 21/10/2026). Lecture
    seule par défaut ; `appliquer` : une alerte encore « nouvelle » est classée avec son motif (action « classer »
    signée MARS C2, comme une décision d'opérateur) ; une alerte déjà traitée garde son statut et ses décisions, seul le
    motif est ajouté à ses détails. Rien n'est supprimé."""
    out = {"examinees": 0, "hors_fenetres": [], "classees": 0, "annotees": 0}
    rows = conn.execute(
        """SELECT a.id, a.status, ST_X(a.geom::geometry), ST_Y(a.geom::geometry), a.details->>'scene',
                  (a.details->>'analysis_id')::bigint,
                  EXISTS (SELECT 1 FROM alert_actions x WHERE x.alert_id = a.id) AS decisions
           FROM alerts a WHERE a.type = 'AIS_UNCONFIRMED' AND coalesce(a.details->>'source', 'sentinel1') = 'sentinel1'
             AND NOT (a.details ? 'reclassement') ORDER BY a.id""").fetchall()
    for aid, statut, lon, lat, scene, analyse, decisions in rows:
        out["examinees"] += 1
        an = conn.execute("SELECT scenes, timings FROM analyses WHERE id = %s", (analyse,)).fetchone()
        if not an:
            continue
        sc = next((x for x in an[0] or [] if x.get("id") == scene), None)
        if not sc or not sc.get("fenetres"):
            continue                                   # scène entière : l'emprise était bien lue
        mesures = ((an[1] if isinstance(an[1], dict) else {}).get(scene) or {}).get("fenetres")
        if dans_coeur(fenetres_lues(sc, mesures), lon, lat):
            continue
        out["hors_fenetres"].append({"alerte": aid, "statut": statut, "decisions": decisions, "analyse": analyse})
        if not appliquer:
            continue
        conn.execute("UPDATE alerts SET details = details || jsonb_build_object('reclassement', %s::text) WHERE id = %s",
                     (MOTIF_HORS_FENETRE, aid))
        if statut == "nouvelle" and not decisions:
            conn.execute("UPDATE alerts SET status = 'classee' WHERE id = %s", (aid,))
            conn.execute("INSERT INTO alert_actions (alert_id, action, note, motif, author) VALUES (%s, 'classer', %s, "
                         "'faux_positif', 'MARS C2 (correction du 21/10/2026)')", (aid, MOTIF_HORS_FENETRE))
            out["classees"] += 1
        else:
            out["annotees"] += 1                        # décision d'opérateur conservée telle quelle
    traces.attributs(**{"sentinel.non_confirmees_examinees": out["examinees"],
                        "sentinel.non_confirmees_hors_fenetres": len(out["hors_fenetres"]),
                        "sentinel.non_confirmees_classees": out["classees"]})
    return out


def flux_coupe(conn, t0: datetime, rules: dict) -> bool:
    """Flux AIS coupé à l'instant du passage, lu dans stats_minute (3 h avant, 1 h après) : même définition que VIIRS
    (mars/viirs.py, feed_cut). Une minute absente de stats_minute compte pour zéro : un trou des statistiques (18/10/2026)
    se lit comme une coupure, d'où la garde et la reconstruction du lot C5."""
    from mars.viirs import feed_cut
    counts = dict(conn.execute("SELECT minute, positions FROM stats_minute WHERE minute BETWEEN %s AND %s",
                               (t0 - timedelta(hours=3), t0 + timedelta(hours=1))).fetchall())
    return feed_cut(counts, t0, rules["continu"]["flux_min_ratio"], rules["viirs"]["coupure_marge_min"])


def recalculer_flux(conn, aid: int, rules: dict) -> dict:
    """Après une reconstruction des statistiques : drapeau flux_coupe de chaque scène recalculé, et détections jugées
    non évaluables pour la seule coupure du flux réévaluées. Une détection redevenue évaluable n'a pas d'alerte : elle
    est comptée et signalée (relancer la fusion de l'analyse pour la créer)."""
    row = conn.execute("SELECT summary FROM analyses WHERE id = %s AND source = 'sentinel1'", (aid,)).fetchone()
    if row is None:
        raise ValueError(f"analyse Sentinel 1 {aid} introuvable")
    summary = row[0] or {}
    v, out = rules["viirs"], {"analyse": aid, "scenes": {}, "detections_reevaluees": 0, "redevenues_evaluables": []}
    cells = conn.execute("SELECT EXISTS (SELECT 1 FROM reception_cells)").fetchone()[0]
    for scene, t0 in conn.execute("SELECT scene, min(ts) FROM detections WHERE analysis_id = %s AND scene IS NOT NULL "
                                  "GROUP BY 1", (aid,)).fetchall():
        cut = flux_coupe(conn, t0, rules)
        avant = (summary.get("scenes") or {}).get(scene, {}).get("flux_coupe")
        out["scenes"][scene] = {"avant": avant, "apres": cut}
        summary.setdefault("scenes", {}).setdefault(scene, {})["flux_coupe"] = cut
        for did, inside, around in conn.execute(
                """SELECT d.id, EXISTS (SELECT 1 FROM reception_cells c WHERE ST_Intersects(c.geom, d.geom)),
                          d.ais_navires_rayon FROM detections d
                   WHERE d.analysis_id = %s AND d.scene = %s AND d.non_evaluable = %s""",
                (aid, scene, MOTIF_COUPURE)).fetchall():
            motif = motif_non_evaluable(cut, cells, inside, around, v)
            out["detections_reevaluees"] += 1
            conn.execute("UPDATE detections SET non_evaluable = %s, mask_reason = %s WHERE id = %s",
                         (motif, "non_evaluable" if motif else None, did))
            if motif is None:
                out["redevenues_evaluables"].append(did)
    summary["flux_recalcule_le"] = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE analyses SET summary = %s WHERE id = %s", (json.dumps(summary, default=str), aid))
    return out


def _fixed_points(conn, bbox, t0, rules):
    import pandas as pd
    rows = conn.execute(
        """SELECT 'registre', f.id, ST_X(f.geom::geometry), ST_Y(f.geom::geometry) FROM fixed_echoes f
           WHERE ST_Intersects(f.geom::geometry, ST_MakeEnvelope(%s, %s, %s, %s, 4326))
           UNION ALL
           SELECT 'detection', d.id, ST_X(d.geom::geometry), ST_Y(d.geom::geometry)
           FROM detections d JOIN analyses a ON a.id = d.analysis_id JOIN sar_passes p ON p.id = a.pass_id
           WHERE d.matched_vessel_id IS NULL AND coalesce(d.mask_reason, '') <> 'terre'
             AND abs(extract(epoch FROM coalesce(d.ts, p.acquired_at) - %s::timestamptz)) >= %s * 86400
             AND ST_Intersects(d.geom::geometry, ST_MakeEnvelope(%s, %s, %s, %s, 4326))""",
        (*bbox, t0, float(rules["persistence"]["min_days_apart"]), *bbox)).fetchall()
    return pd.DataFrame(rows, columns=["source", "ref", "lon", "lat"])


def _record_fixed(conn, det, hits, t0) -> int:
    """Registre des échos fixes (même logique que l'analyse du Mac) : un écho sans AIS revu au même endroit à une autre
    date ; les détections antérieures sont écartées et leurs alertes encore ouvertes classées."""
    for hit in hits:
        d = det.loc[hit["det_index"]]
        did = int(d.id)
        prior = [r["ref"] for r in hit["refs"] if r["source"] == "detection"]
        registry = [r["ref"] for r in hit["refs"] if r["source"] == "registre"]
        if registry:
            conn.execute("UPDATE fixed_echoes SET observations = observations + 1, last_seen = greatest(last_seen, %s), "
                         "first_seen = least(first_seen, %s), detection_ids = detection_ids || %s::bigint[] WHERE id = %s",
                         (t0, t0, [did], registry[0]))
        else:
            first = conn.execute("SELECT min(coalesce(ts, now())) FROM detections WHERE id = ANY(%s)", (prior,)).fetchone()[0]
            conn.execute("INSERT INTO fixed_echoes (geom, first_seen, last_seen, observations, detection_ids) VALUES "
                         "(ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, least(%s::timestamptz, %s::timestamptz), "
                         "greatest(%s::timestamptz, %s::timestamptz), %s, %s)",
                         (float(d.lon), float(d.lat), first or t0, t0, first or t0, t0, 1 + len(prior), prior + [did]))
        if prior:
            conn.execute("UPDATE detections SET mask_reason = 'echo fixe' WHERE id = ANY(%s) AND mask_reason IS NULL",
                         (prior,))
            conn.execute(
                "UPDATE alerts SET status = 'classee', details = details || jsonb_build_object('reclassement', "
                "'Écho revu au même endroit le ' || to_char(%s::timestamptz, 'DD/MM/YYYY') || ' sans AIS : écho fixe "
                "(éolienne, plateforme, bouée), pas un navire') WHERE type = 'DARK_SHIP' AND status = 'nouvelle' AND id IN "
                "(SELECT alert_id FROM alert_evidence WHERE evidence_type = 'detection' AND evidence_id = ANY(%s))",
                (t0, prior))
    return len(hits)


# Capteur d'une fusion (lot S2) : source, textes, clés des alertes et règles propres. Sentinel 1 garde ses valeurs.
RADAR = {
    "source": "sentinel1", "cles": ("s1", "s1m", "s1u"),
    "dark": "Écho radar de navire sans navire AIS compatible, alors que l'AIS est reçu dans la zone",
    "incoherence": "Écho radar apparié au navire, mais sa longueur ou son orientation contredit la déclaration AIS : "
                   "identité possiblement usurpée",
    "non_confirme": "Grand navire déclaré par l'AIS dans la scène radar, sans aucun écho compatible à l'instant du "
                    "passage : position possiblement falsifiée",
    "masque": None,            # détection → motif de masque propre au capteur (Sentinel 2 : nuages)
    "visible": None,           # navire AIS (lon, lat) → visible par le capteur ? (Sentinel 2 : fenêtre dégagée)
    "contexte": [],            # phrases ajoutées au contexte de chaque alerte
    "a_confirmer": False,      # toute alerte DARK_SHIP à confirmer (Sentinel 2 sans masque des nuages)
}


def _screen_and_alert(conn, aid, scene, t0, sc, det, extras, cut, cells, heading, rules, capteur=None) -> dict:
    """Écriture de la fusion sur les détections, éoliennes, côtes, évaluation, puis alertes de la scène."""
    from mars.viirs import severity
    cap = {**RADAR, **(capteur or {})}
    s, v = rules["sentinel"], rules["viirs"]
    out = {"alertes_dark_ship": 0, "alertes_non_confirme": 0, "alertes_incoherence": 0, "non_evaluables": 0}
    ais = extras["ais"]
    for _, d in det.iterrows():
        did = int(d.id)
        mask = None if d.mask_reason is None or d.mask_reason != d.mask_reason else d.mask_reason
        vid = None if d.matched_vessel_id is None or d.matched_vessel_id != d.matched_vessel_id else int(d.matched_vessel_id)
        geo = conn.execute(
            """SELECT EXISTS (SELECT 1 FROM infrastructure i WHERE i.kind = 'windfarm'
                              AND ST_DWithin(i.geom, d.geom, %s)) AS eolien,
                      (SELECT min(ST_Distance(l.geom, d.geom)) FROM land l WHERE ST_DWithin(l.geom, d.geom, 100000)) AS cote,
                      EXISTS (SELECT 1 FROM reception_cells c WHERE ST_Intersects(c.geom, d.geom)) AS dans_zone,
                      (SELECT count(DISTINCT p.vessel_id) FROM positions p
                       WHERE p.ts BETWEEN %s - make_interval(mins => %s) AND %s + make_interval(mins => %s)
                         AND ST_DWithin(p.geom, d.geom, %s)) AS autour
               FROM detections d WHERE d.id = %s""",
            (s["eolien_m"], t0, v["couverture_fenetre_min"] // 2, t0, v["couverture_fenetre_min"] // 2,
             v["couverture_rayon_km"] * 1000, did)).fetchone()
        eolien, cote_m, inside, around = geo
        if mask is None and vid is None and eolien:
            mask = "eolienne probable"
        if mask is None and vid is None and cap["masque"]:
            mask = cap["masque"](d)                   # Sentinel 2 : navire sous un nuage, « nuages »
        motif = None
        if mask is None and vid is None:
            motif = motif_non_evaluable(cut, cells, inside, around, v)
            if motif:
                mask = "non_evaluable"
                out["non_evaluables"] += 1
        conn.execute("UPDATE detections SET mask_reason = %s, matched_vessel_id = %s, match_cost = %s, "
                     "distance_cote_m = %s, non_evaluable = %s, ais_navires_rayon = %s WHERE id = %s",
                     (mask, vid, _r(d.match_cost, 3), cote_m, motif, around, did))
        common = {"source": cap["source"], "analysis_id": aid, "scene": scene, "satellite": sc.get("satellite"),
                  "heure": t0.isoformat(), "detection_id": did, "direction_de_vol_deg": heading}
        attrs = {"longueur_m": _r(d.length_m), "largeur_m": _r(d.width_m), "cap_deg": _r(d.heading_deg),
                 "cap_prob": _r(d.heading_prob, 2), "score": _r(d.objectness, 3), "contraste_db": _r(d.contrast_vv_db, 1)}
        if cap["source"] == "sentinel2":
            attrs.update({"vitesse_kn": _r(getattr(d, "speed_kn", None), 1), "type_navire": getattr(d, "type_navire", None),
                          "nuages_pct": _r(getattr(d, "nuages_pct", None), 1), "masque_nuages": sc.get("masque")})
            attrs.pop("contraste_db")
        if mask is None and vid is None:
            # Écho sans AIS compatible, évaluable : DARK_SHIP de source radar, gravité comme pour VIIRS
            infra, liste = _context(conn, did, t0, v)
            coast_km = None if cote_m is None else cote_m / 1000
            sev, to_confirm = severity(bool(infra), bool(liste), coast_km, rules)
            to_confirm = to_confirm or cap["a_confirmer"]
            ctx = list(cap["contexte"])
            if to_confirm:
                ctx.append("À confirmer : l'AIS peut être incomplet (un point par minute, réception partielle) et la "
                           "petite pêche n'a pas l'obligation d'AIS")
            if infra:
                ctx.append(f"À {infra['distance_m']:.0f} m d'une infrastructure ({infra.get('name') or infra.get('type')})")
            if liste:
                ctx.append(f"Navire des listes à {str(liste['distance_km']).replace('.', ',')} km : "
                           f"{liste.get('name') or liste.get('mmsi')}")
            cands = d.candidates if isinstance(d.candidates, list) else []
            details = {**common, **attrs, "distance_cote_km": None if coast_km is None else round(coast_km, 1),
                       "infrastructure": infra, "navire_liste": liste, "candidats_ais": cands, "contexte": ctx,
                       "a_confirmer": to_confirm, "critere_evaluation": critere(cells), "ais_navires_rayon": around,
                       "motif": cap["dark"],
                       "parametres": {"rayon_base_m": rules["fusion"]["base_radius_m"],
                                      "doppler_s": rules["fusion"]["doppler_s"], "confiance": cap.get("confiance",
                                                                                                     s["confiance"])}}
            if _alert(conn, "DARK_SHIP", sev, t0, float(d.lon), float(d.lat), details, rules,
                      f"{cap['cles'][0]}:{scene}:{float(d.lon):.4f}:{float(d.lat):.4f}",
                      [("detection", did), ("analysis", aid)] + ([("vessel", liste["vessel_id"])] if liste else [])):
                out["alertes_dark_ship"] += 1
        elif vid is not None and len(ais):
            # Écho apparié : longueur et cap mesurés contre la déclaration AIS
            a = ais[ais.vessel_id == vid].iloc[0]
            # Optique (lot S2) : cap mesuré sur la forme et le sillage, sans classe ni probabilité ; écart à 3 à 8° de
            # l'AIS sur cinq navires (17/10/2026), compté comme certain
            prob = _r(d.heading_prob, 2) if cap["source"] == "sentinel1" else 1.0
            found = mismatches({"longueur_m": _r(d.length_m), "cap_deg": _r(d.heading_deg), "cap_prob": prob},
                               {"length_m": _r(a.length_m), "cog": _r(a.cog), "sog": _r(a.sog)}, s)
            if found:
                listed = conn.execute("SELECT level FROM vessel_watch WHERE vessel_id = %s AND level <> 'autre_risque'",
                                      (vid,)).fetchone()
                details = {**common, **attrs,
                           "navire": {"vessel_id": vid, "mmsi": int(a.mmsi), "name": None if a["name"] != a["name"] else a["name"],
                                      "length_m": _r(a.length_m), "cog": _r(a.cog), "sog_kn": _r(a.sog, 1),
                                      "methode_position": a.method},
                           "ecarts": found, "contexte": found + ([f"Navire des listes ({listed[0]})"] if listed else []),
                           "motif": cap["incoherence"],
                           "parametres": {k: s[k] for k in ("ecart_longueur_m", "ecart_longueur_relatif",
                                                            "cap_ecart_min_deg", "cap_ecart_max_deg")}}
                if _alert(conn, "IDENTITY_MISMATCH", "moyenne" if listed else "faible", t0, float(d.lon),
                          float(d.lat), details, rules, f"{cap['cles'][1]}:{scene}:{vid}",
                          [("detection", did), ("analysis", aid), ("vessel", vid)]):
                    out["alertes_incoherence"] += 1
    # Grands navires AIS sans écho : seulement dans l'emprise réelle de la scène, au large, là où l'AIS est fiable
    for x in extras["unconfirmed"]:
        if sc.get("emprise") and not covers(sc["emprise"], x["lon"], x["lat"], 0):
            continue
        if cap["visible"] and not cap["visible"](x["lon"], x["lat"]):
            continue                                  # Sentinel 2 : hors des fenêtres lues, sous un nuage, sans masque
        ok = conn.execute(
            """SELECT NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, g, %s)),
                      EXISTS (SELECT 1 FROM reception_cells c WHERE ST_Intersects(c.geom, g))
               FROM (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography AS g) p""",
            (float(rules["masks"]["land_buffer_m"]) * 2, x["lon"], x["lat"])).fetchone()
        if not ok[0] or cut or (cells and not ok[1]):
            continue
        big = x["length_m"] >= rules["unconfirmed"]["high_length_m"]
        details = {"source": cap["source"], "analysis_id": aid, "scene": scene, "satellite": sc.get("satellite"),
                   "heure": t0.isoformat(), "navire": {k: x[k] for k in ("vessel_id", "mmsi", "name", "length_m", "sog_kn")},
                   "methode_position": x["methode_position"], "echo_le_plus_proche_m": x["echo_le_plus_proche_m"],
                   "tolerance_le_long_m": x["tolerance_le_long_m"], "tolerance_en_travers_m": x["tolerance_en_travers_m"],
                   "critere_evaluation": critere(cells),
                   "motif": cap["non_confirme"],
                   "contexte": ["Le détecteur manque une partie des navires : indice à recouper, pas une preuve"],
                   "parametres": dict(rules["unconfirmed"])}
        if _alert(conn, "AIS_UNCONFIRMED", "moyenne" if big else "faible", t0, x["lon"], x["lat"], details, rules,
                  f"{cap['cles'][2]}:{scene}:{x['vessel_id']}", [("analysis", aid), ("vessel", x["vessel_id"])]):
            out["alertes_non_confirme"] += 1
    return out


def _r(v, nd=0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v else (round(v) if nd == 0 else round(v, nd))


def _context(conn, did: int, t0: datetime, v: dict):
    r = conn.execute(
        """SELECT (SELECT json_build_object('id', i.id, 'name', i.name, 'type', i.attrs->>'type',
                                            'distance_m', round(ST_Distance(i.geom, d.geom)))
                   FROM infrastructure i WHERE ST_DWithin(i.geom, d.geom, %s)
                   ORDER BY ST_Distance(i.geom, d.geom) LIMIT 1),
                  (SELECT json_build_object('vessel_id', vv.id, 'name', vv.name, 'mmsi', vv.mmsi, 'flag', vv.flag,
                                            'level', w.level, 'distance_km', round((ST_Distance(p.geom, d.geom) / 1000)::numeric, 1))
                   FROM positions p JOIN vessel_watch w ON w.vessel_id = p.vessel_id AND w.level <> 'autre_risque'
                   JOIN vessels vv ON vv.id = p.vessel_id
                   WHERE p.ts BETWEEN %s - interval '30 minutes' AND %s + interval '30 minutes'
                     AND ST_DWithin(p.geom, d.geom, %s)
                   ORDER BY ST_Distance(p.geom, d.geom) LIMIT 1)
           FROM detections d WHERE d.id = %s""",
        (v["corridor_m"], t0, t0, v["liste_rayon_km"] * 1000, did)).fetchone()
    return r[0], r[1]


def _alert(conn, kind, sev, t0, lon, lat, details, rules, key, evidence):
    """Alerte d'une analyse Sentinel 1 ou 2 ; None si sa règle est en observation (épisode compté) ou coupée au
    panneau de contrôle (lot P)."""
    from mars import controle
    sev = controle.filtrer_sync(conn, rules, kind, details, key, sev, lon, lat)     # zone de vigilance appliquée
    if sev is None:
        return None
    aid = conn.execute(
        """INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key, controle_empreinte)
           VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s)
           ON CONFLICT (type, rule_key) WHERE rule_key IS NOT NULL DO UPDATE
           SET rule_version = EXCLUDED.rule_version, controle_empreinte = EXCLUDED.controle_empreinte, """
        + GARDE_VERIFICATION + """
           RETURNING id""",
        (kind, sev, t0, lon, lat, json.dumps(details, default=str), rules["version"], key,
         controle.stamp(conn, rules))).fetchone()[0]
    for et, eid in evidence:
        conn.execute("INSERT INTO alert_evidence VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (aid, et, eid))
    return aid


def passes_status(conn, now: datetime | None = None) -> dict:
    """État pour la barre d'état et /api/metrics : dernier passage analysé, analyses du mois, coût engagé."""
    now = now or datetime.now(timezone.utc)
    last = conn.execute(
        """SELECT a.id, a.status, a.completed_at, p.satellite, p.acquired_at, a.summary
           FROM analyses a JOIN sar_passes p ON p.id = a.pass_id WHERE a.source = 'sentinel1'
           ORDER BY a.requested_at DESC LIMIT 1""").fetchone()
    scenes, spent = usage(conn, now)
    return {"dernier": None if not last else {"analyse": last[0], "statut": last[1], "fini_le": last[2],
                                               "satellite": last[3], "acquis_le": last[4]},
            "scenes_aujourd_hui": scenes, "euros_mois": round(spent, 2)}
