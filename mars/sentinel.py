"""Sentinel 1 (étape 3, lot C) : choix des passages à analyser, travailleur GPU, fusion avec l'AIS et alertes.

Le serveur ne télécharge jamais d'image : il lit des métadonnées (catalogue STAC de Copernicus Data Space), signe des
adresses de lecture temporaires (clés S3 de Copernicus dans le .env du serveur, jamais transmises au travailleur), et
reçoit des détections et des vignettes (quelques kilooctets chacune). Le travailleur (travailleurs/sentinel.py), sur
une instance GPU éphémère, télécharge les images VV et VH de chaque scène et lance le modèle d'AI2.

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
from datetime import datetime, timedelta, timezone

from mars.evaluation import critere, motif_non_evaluable

STAC = "https://stac.dataspace.copernicus.eu/v1/search"
S3_HOST = "eodata.dataspace.copernicus.eu"
HEADERS = {"User-Agent": "mars-c2 (surveillance maritime, projet personnel)"}
INCLINAISON_DEG = 98.18          # orbite héliosynchrone de Sentinel 1
URL_VALIDITE_S = 3 * 3600        # adresses de lecture valables 3 heures (démarrage, image d'analyse, scènes)
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


def pick_scenes(scenes: list[dict], points: list[dict], margin_m: float) -> list[dict]:
    """Scènes qui couvrent au moins un point d'intérêt, chacune avec ses points, les plus riches d'abord."""
    out = []
    for sc in scenes:
        mine = [p for p in points if covers(sc["emprise"], p["lon"], p["lat"], margin_m)]
        if mine:
            out.append({**sc, "points": mine})
    return sorted(out, key=lambda sc: (-len(sc["points"]), sc["debut"]))


# Catalogue (métadonnées seulement) et adresses de lecture

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


def signed_urls(scene: dict, now: datetime | None = None) -> dict:
    """Adresses de lecture des images VV et VH, signées avec les clés S3 de Copernicus du serveur, valables
    URL_VALIDITE_S secondes ; le travailleur ne reçoit jamais les clés."""
    from mars.r2_signature import presign
    key, secret = os.environ.get("CDSE_ACCESS_KEY"), os.environ.get("CDSE_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("clés CDSE_ACCESS_KEY et CDSE_SECRET_KEY absentes du .env")
    return {pol: presign("GET", S3_HOST, s3_path(scene[pol]), key, secret, region="default", expires=URL_VALIDITE_S,
                         now=now) for pol in ("vv", "vh")}


# Sélection des passages (base)

def interest_points(conn, pass_id: int, rules: dict) -> list[dict]:
    """Points d'intérêt dans l'emprise d'un passage : navires des listes à moins de `corridor_m` d'une infrastructure,
    à `liste_fenetre_min` près de l'acquisition ; alertes DARK_SHIP VIIRS ouvertes, assez graves, récentes."""
    s = rules["sentinel"]
    rows = conn.execute(
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
    return out


def usage(conn, now: datetime) -> tuple[int, float]:
    """Scènes déjà engagées aujourd'hui (analyses Sentinel non échouées) et euros engagés ce mois ci (coût mesuré des
    sessions finies, coût estimé de celles en cours)."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    scenes = conn.execute(
        "SELECT coalesce(sum(jsonb_array_length(scenes)), 0) FROM analyses "
        "WHERE source = 'sentinel1' AND status <> 'failed' AND requested_at >= %s", (day,)).fetchone()[0]
    spent = conn.execute(
        """SELECT coalesce(sum(coalesce((t.mesures->>'cout_estime_eur')::float8, a.cout)), 0)
           FROM (SELECT travailleur_id, sum(cout_estime_eur) AS cout FROM analyses
                 WHERE source = 'sentinel1' AND requested_at >= %s AND travailleur_id IS NOT NULL
                 GROUP BY travailleur_id) a
           LEFT JOIN travailleurs t ON t.id = a.travailleur_id""", (month,)).fetchone()[0]
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
    boxes = regions_boxes()
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    out, waiting, skipped = [], [], 0
    for pid, sat, t0, t1, _statut, _mode in rows:
        points = interest_points(conn, pid, rules)
        if pass_id is None and not points:
            skipped += 1
            continue
        scenes = stac_scenes(session, sat or "", t0 - timedelta(minutes=2),
                             (t1 or t0) + timedelta(minutes=2), box)
        if not scenes:
            if now - t0 < timedelta(hours=s["attente_publication_h"]):
                waiting.append(pid)
            continue
        if pass_id is not None and not points:
            chosen = [{**sc, "points": []} for sc in sorted(scenes, key=lambda sc: sc["debut"])]
        else:
            chosen = pick_scenes(scenes, points, s["interet_rayon_m"])
        if chosen:
            out.append({"pass_id": pid, "satellite": sat, "acquired_at": t0, "scenes": chosen, "points": points})
    return {"passages": out, "en_attente_de_publication": waiting, "sans_interet": skipped}


def launch(conn, scw, rules: dict, now: datetime, session, pass_id: int | None = None, log=print) -> dict:
    """Une session GPU pour les passages à analyser : plafond de scènes et budget vérifiés, une analyse par passage
    (index unique : jamais deux fois le même passage), adresses de lecture signées, travailleur lancé. Retourne le
    résumé ; {"refus": motif} si un garde fou l'interdit."""
    from mars import travailleurs
    from mars.viirs import regions_boxes
    s, t = rules["sentinel"], travailleurs.type_params(rules, "sentinel")
    p = plan(conn, session, rules, now, pass_id)
    if "refus" in p:
        return p
    if not p["passages"]:
        return {"passages": 0, **{k: v for k, v in p.items() if k != "passages"}}
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
    estimate = cost_estimate(max(n, 1), s, t)
    motif = guard(n or asked, scenes_today, spent, estimate, s)       # aucune place : le plafond est le motif
    if motif:
        return {"refus": motif, "scenes_demandees": asked}
    import psycopg
    analyses, tasks = [], []
    try:
        with conn.transaction():
            for ps in selected:
                env = _envelope(ps["scenes"])
                motifs = sorted({pt["motif"] for pt in ps["points"]}) or ["demande"]
                aid = conn.execute(
                    """INSERT INTO analyses (pass_id, aoi, mode, status, model_version, source, scenes, declenchement,
                                             cout_estime_eur)
                       VALUES (%s, ST_MakeEnvelope(%s, %s, %s, %s, 4326)::geography, 'full', 'pending', %s, 'sentinel1',
                               %s, %s, %s) RETURNING id""",
                    (ps["pass_id"], *env, "allenai/vessel-detection-sentinels fafc6c3",
                     json.dumps([{k: sc[k] for k in ("id", "debut", "fin", "orbite", "satellite", "emprise", "octets")}
                                 for sc in ps["scenes"]]),
                     json.dumps({"motifs": motifs, "points": ps["points"]}, default=str),
                     round(estimate * len(ps["scenes"]) / n, 3))).fetchone()[0]
                analyses.append(aid)
                for sc in ps["scenes"]:
                    tasks.append({"analyse": aid, "id": sc["id"], "debut": sc["debut"], "fin": sc["fin"],
                                  "orbite": sc["orbite"], "satellite": sc["satellite"], **signed_urls(sc, now)})
    except psycopg.errors.UniqueViolation:
        return {"en_cours": "passage déjà en analyse (lancement concurrent)"}
    params = {"scenes": tasks, "regions": regions_boxes(), "confiance": s["confiance"], "nms_m": s["nms_m"],
              "vignette_km": s["vignette_km"]}
    try:
        r = travailleurs.launch(conn, scw, rules, "sentinel", params, {}, now=now, log=log)
    except Exception as e:
        conn.execute("UPDATE analyses SET status = 'failed', error = %s, completed_at = now() WHERE id = ANY(%s)",
                     (f"lancement du travailleur : {e}"[:1000], analyses))
        raise
    if "travailleur" not in r:
        conn.execute("UPDATE analyses SET status = 'failed', error = %s, completed_at = now() WHERE id = ANY(%s)",
                     (r.get("refus") or f"travailleur occupé ({r.get('occupe')})", analyses))
        return r
    conn.execute("UPDATE analyses SET status = 'running', travailleur_id = %s WHERE id = ANY(%s)",
                 (r["travailleur"], analyses))
    return {**r, "analyses": analyses, "scenes": n, "scenes_ecartees_par_le_plafond": dropped,
            "cout_estime_eur": estimate, "en_attente_de_publication": p["en_attente_de_publication"]}


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
        summary = fuse_analysis(conn, aid, rules) if any(not sc.get("erreur") for sc in scenes) else {}
        for k in ("appariees", "alertes_dark_ship", "alertes_non_confirme", "alertes_incoherence", "non_evaluables",
                  "echos_fixes"):
            out[k] += summary.get(k, 0)
        mes = {sc["id"]: {k: sc.get(k) for k in ("telecharge_s", "telecharge_mo", "analyse_s", "gpu_max_mo", "erreur")}
               for sc in scenes}
        status = "done" if len(errors) < len(scenes) else "failed"
        conn.execute("UPDATE analyses SET status = %s, completed_at = now(), summary = %s, timings = %s, error = %s "
                     "WHERE id = %s", (status, json.dumps(summary, default=str), json.dumps(mes),
                                       "; ".join(errors)[:1000] or None, aid))
    out.update(_store_images(conn, images))
    # Analyses de ce travailleur sans aucun résultat (travailleur arrêté avant) : en échec, avec le motif
    conn.execute("UPDATE analyses SET status = 'failed', completed_at = now(), error = coalesce(error, %s) "
                 "WHERE travailleur_id = %s AND status IN ('pending', 'running')",
                 (res.get("erreur") or "aucun résultat du travailleur pour cette analyse", w["id"]))
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
    meta = {sc["id"]: sc for sc in (conn.execute("SELECT scenes FROM analyses WHERE id = %s", (aid,)).fetchone()[0] or [])}
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
        counts = dict(conn.execute("SELECT minute, positions FROM stats_minute WHERE minute BETWEEN %s AND %s",
                                   (t0 - timedelta(hours=3), t0 + timedelta(hours=1))).fetchall())
        from mars.viirs import feed_cut
        cut = feed_cut(counts, t0, rules["continu"]["flux_min_ratio"], rules["viirs"]["coupure_marge_min"])
        r = _screen_and_alert(conn, aid, scene, t0, sc, det_f, extras, cut, cells, heading, rules)
        out["appariees"] += int(det_f.matched_vessel_id.notna().sum()) if len(det_f) else 0
        out["echos_fixes"] += n_fixed
        for k in ("alertes_dark_ship", "alertes_non_confirme", "alertes_incoherence", "non_evaluables"):
            out[k] += r[k]
        out["scenes"][scene] = {"detections": len(det_f), "navires_ais": n_ais, "direction_de_vol_deg": heading,
                                "decalages": extras["offsets"], "flux_coupe": cut, "emprise": foot is not None}
    out["critere_evaluation"] = critere(cells)
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


def _screen_and_alert(conn, aid, scene, t0, sc, det, extras, cut, cells, heading, rules) -> dict:
    """Écriture de la fusion sur les détections, éoliennes, côtes, évaluation, puis alertes de la scène."""
    from mars.viirs import severity
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
        motif = None
        if mask is None and vid is None:
            motif = motif_non_evaluable(cut, cells, inside, around, v)
            if motif:
                mask = "non_evaluable"
                out["non_evaluables"] += 1
        conn.execute("UPDATE detections SET mask_reason = %s, matched_vessel_id = %s, match_cost = %s, "
                     "distance_cote_m = %s, non_evaluable = %s, ais_navires_rayon = %s WHERE id = %s",
                     (mask, vid, _r(d.match_cost, 3), cote_m, motif, around, did))
        common = {"source": "sentinel1", "analysis_id": aid, "scene": scene, "satellite": sc.get("satellite"),
                  "heure": t0.isoformat(), "detection_id": did, "direction_de_vol_deg": heading}
        attrs = {"longueur_m": _r(d.length_m), "largeur_m": _r(d.width_m), "cap_deg": _r(d.heading_deg),
                 "cap_prob": _r(d.heading_prob, 2), "score": _r(d.objectness, 3), "contraste_db": _r(d.contrast_vv_db, 1)}
        if mask is None and vid is None:
            # Écho sans AIS compatible, évaluable : DARK_SHIP de source radar, gravité comme pour VIIRS
            infra, liste = _context(conn, did, t0, v)
            coast_km = None if cote_m is None else cote_m / 1000
            sev, to_confirm = severity(bool(infra), bool(liste), coast_km, rules)
            ctx = []
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
                       "motif": "Écho radar de navire sans navire AIS compatible, alors que l'AIS est reçu dans la zone",
                       "parametres": {"rayon_base_m": rules["fusion"]["base_radius_m"],
                                      "doppler_s": rules["fusion"]["doppler_s"], "confiance": s["confiance"]}}
            _alert(conn, "DARK_SHIP", sev, t0, float(d.lon), float(d.lat), details, rules,
                   f"s1:{scene}:{float(d.lon):.4f}:{float(d.lat):.4f}",
                   [("detection", did), ("analysis", aid)] + ([("vessel", liste["vessel_id"])] if liste else []))
            out["alertes_dark_ship"] += 1
        elif vid is not None and len(ais):
            # Écho apparié : longueur et cap mesurés contre la déclaration AIS
            a = ais[ais.vessel_id == vid].iloc[0]
            found = mismatches({"longueur_m": _r(d.length_m), "cap_deg": _r(d.heading_deg), "cap_prob": _r(d.heading_prob, 2)},
                               {"length_m": _r(a.length_m), "cog": _r(a.cog), "sog": _r(a.sog)}, s)
            if found:
                listed = conn.execute("SELECT level FROM vessel_watch WHERE vessel_id = %s AND level <> 'autre_risque'",
                                      (vid,)).fetchone()
                details = {**common, **attrs,
                           "navire": {"vessel_id": vid, "mmsi": int(a.mmsi), "name": None if a["name"] != a["name"] else a["name"],
                                      "length_m": _r(a.length_m), "cog": _r(a.cog), "sog_kn": _r(a.sog, 1),
                                      "methode_position": a.method},
                           "ecarts": found, "contexte": found + ([f"Navire des listes ({listed[0]})"] if listed else []),
                           "motif": "Écho radar apparié au navire, mais sa longueur ou son orientation contredit la "
                                    "déclaration AIS : identité possiblement usurpée",
                           "parametres": {k: s[k] for k in ("ecart_longueur_m", "ecart_longueur_relatif",
                                                            "cap_ecart_min_deg", "cap_ecart_max_deg")}}
                _alert(conn, "IDENTITY_MISMATCH", "moyenne" if listed else "faible", t0, float(d.lon), float(d.lat),
                       details, rules, f"s1m:{scene}:{vid}", [("detection", did), ("analysis", aid), ("vessel", vid)])
                out["alertes_incoherence"] += 1
    # Grands navires AIS sans écho : seulement dans l'emprise réelle de la scène, au large, là où l'AIS est fiable
    for x in extras["unconfirmed"]:
        if sc.get("emprise") and not covers(sc["emprise"], x["lon"], x["lat"], 0):
            continue
        ok = conn.execute(
            """SELECT NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, g, %s)),
                      EXISTS (SELECT 1 FROM reception_cells c WHERE ST_Intersects(c.geom, g))
               FROM (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography AS g) p""",
            (float(rules["masks"]["land_buffer_m"]) * 2, x["lon"], x["lat"])).fetchone()
        if not ok[0] or cut or (cells and not ok[1]):
            continue
        big = x["length_m"] >= rules["unconfirmed"]["high_length_m"]
        details = {"source": "sentinel1", "analysis_id": aid, "scene": scene, "satellite": sc.get("satellite"),
                   "heure": t0.isoformat(), "navire": {k: x[k] for k in ("vessel_id", "mmsi", "name", "length_m", "sog_kn")},
                   "methode_position": x["methode_position"], "echo_le_plus_proche_m": x["echo_le_plus_proche_m"],
                   "tolerance_le_long_m": x["tolerance_le_long_m"], "tolerance_en_travers_m": x["tolerance_en_travers_m"],
                   "critere_evaluation": critere(cells),
                   "motif": "Grand navire déclaré par l'AIS dans la scène radar, sans aucun écho compatible à l'instant "
                            "du passage : position possiblement falsifiée",
                   "contexte": ["Le détecteur manque une partie des navires : indice à recouper, pas une preuve"],
                   "parametres": dict(rules["unconfirmed"])}
        _alert(conn, "AIS_UNCONFIRMED", "moyenne" if big else "faible", t0, x["lon"], x["lat"], details, rules,
               f"s1u:{scene}:{x['vessel_id']}", [("analysis", aid), ("vessel", x["vessel_id"])])
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
    aid = conn.execute(
        """INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key)
           VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s)
           ON CONFLICT (type, rule_key) WHERE rule_key IS NOT NULL DO UPDATE
           SET severity = EXCLUDED.severity, details = EXCLUDED.details, rule_version = EXCLUDED.rule_version
           RETURNING id""",
        (kind, sev, t0, lon, lat, json.dumps(details, default=str), rules["version"], key)).fetchone()[0]
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
