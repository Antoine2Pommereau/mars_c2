"""Détections nocturnes VIIRS (étape 3, lot B), côté serveur : le serveur choisit les granules, un travailleur
éphémère les analyse (travailleurs/viirs.py, modèle allenai/vessel-detection-viirs), et le serveur reçoit les
détections pour les apparier à l'AIS, écarter les lumières fixes et les côtes, et lever les alertes DARK_SHIP.

Sources : bande Day/Night de Suomi NPP (VNP02DNB), NOAA 20 (VJ102DNB) et NOAA 21 (VJ202DNB), avec leur
géolocalisation (VNP03DNB, VJ103DNB, VJ203DNB), cherchées dans le catalogue CMR de la NASA (public) : produits en
temps quasi réel (« _NRT », environ 2 à 3 heures après le passage) d'abord, produits standard ensuite pour une nuit
rattrapée. Le serveur ne lit que les métadonnées ; le travailleur télécharge avec le jeton Earthdata. Masque des
nuages CLDMSK_L2 de la même minute (lot viirs-masque) : temps quasi réel de SIPS, puis produit standard de LAADS ; sous
une lune forte, l'absence de détection d'un granule ne prouve rien : « non évaluable » (`granule_non_evaluable`).

Les fonctions de calcul (nuit d'un instant, paires de granules, instant d'une détection, appariement, gravité) sont
pures et couvertes par tests/test_viirs.py.
"""
import json
import math
import re
import statistics
from datetime import date, datetime, timedelta, timezone

from mars import traces
from mars.ais.live import ZONES
from mars.evaluation import GARDE_VERIFICATION, critere, motif_non_evaluable
from mars.fusion.doute import motif_doute
from mars.geo import distance_m
from mars.satellites import extent

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
HEADERS = {"User-Agent": "mars-c2 (surveillance maritime, projet personnel)"}
KNOT = 0.514444


def night_of(t: datetime, rules: dict) -> date:
    """Une nuit porte la date de son début : 01 h 30 le 07/10 appartient à la nuit du 06/10."""
    return (t - timedelta(hours=rules["viirs"]["nuit_fin_h"])).date() if t.hour < rules["viirs"]["nuit_fin_h"] else t.date()


def night_window(d: date, rules: dict) -> tuple[datetime, datetime]:
    v = rules["viirs"]
    start = datetime(d.year, d.month, d.day, v["nuit_debut_h"], tzinfo=timezone.utc)
    return start, datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1, hours=v["nuit_fin_h"])


def last_night(now: datetime, rules: dict) -> date:
    """Dernière nuit terminée à l'instant `now`."""
    n = night_of(now, rules)
    while night_window(n, rules)[1] > now:
        n -= timedelta(days=1)
    return n


def granule_key(name: str) -> str | None:
    """« VJ102DNB_NRT.A2026280.0036.021.2026280030537.nc » donne « A2026280.0036 » (jour julien et heure)."""
    m = re.search(r"\.(A\d{7}\.\d{4})\.", name)
    return m.group(1) if m else None


def pair_granules(sat: str, dnb: list[dict], geo: list[dict]) -> list[dict]:
    """Associe chaque fichier DNB à sa géolocalisation (même jour et même heure)."""
    geos = {granule_key(g["name"]): g for g in geo}
    out = []
    for d in dnb:
        k = granule_key(d["name"])
        if k and k in geos:
            out.append({"nom": f"{sat}.{k}", "satellite": sat, "dnb": d["name"], "dnb_url": d["url"],
                        "geo_url": geos[k]["url"], "debut": d["start"]})
    return out


def cmr_search(session, short_name: str, start: datetime, end: datetime) -> list[dict]:
    x0, y0, x1, y1 = extent()
    r = session.get(CMR, headers=HEADERS, timeout=60, params={
        "short_name": short_name, "bounding_box": f"{x0},{y0},{x1},{y1}", "day_night_flag": "night",
        "temporal": f"{start:%Y-%m-%dT%H:%M:%SZ},{end:%Y-%m-%dT%H:%M:%SZ}", "page_size": 200})
    r.raise_for_status()
    out = []
    for e in r.json().get("feed", {}).get("entry", []):
        url = next((ln["href"] for ln in e.get("links", []) if ln.get("rel", "").endswith("/data#")), None)
        if url:
            out.append({"name": url.rsplit("/", 1)[-1], "url": url, "start": e["time_start"]})
    return out


# Appels simultanés au catalogue CMR de la NASA (19/10/2026 : une vingtaine d'appels l'un après l'autre, 13,1 s pour
# la tâche viirs) : par nuit et par satellite, au plus quatre à la fois, pour rester poli avec le catalogue
CMR_SIMULTANES = 4


def mask_urls(session, sat: str, start: datetime, end: datetime, rules: dict) -> tuple[dict[str, list[str]], list[str]]:
    """Adresses des masques des nuages d'un satellite sur la nuit, par clé de granule (jour julien et heure), dans
    l'ordre des produits des règles (temps quasi réel d'abord). Un catalogue injoignable n'empêche rien : le granule
    part sans masque, le motif est rendu. Retourne (adresses par clé, erreurs)."""
    out: dict[str, list[str]] = {}
    erreurs = []
    for prod in rules["viirs"].get("masque_nuages", {}).get("produits", {}).get(sat, []):
        try:
            found = cmr_search(session, prod, start, end)
        except Exception as e:
            erreurs.append(f"{prod} : {type(e).__name__}: {e}"[:200])
            continue
        for g in found:
            k = granule_key(g["name"])
            if k:
                out.setdefault(k, []).append(g["url"])
    return out, erreurs


def sat_granules(session, d: date, sat: str, products: tuple, rules: dict) -> list[dict]:
    """Granules d'un satellite pour une nuit : temps quasi réel, sinon produits standard (rattrapage) ; avec, pour
    chacune, les adresses de son masque des nuages (`masque_urls`, vide s'il n'est pas publié)."""
    start, end = night_window(d, rules)
    dnb, geo = products
    for suffix in ("_NRT", ""):
        pairs = pair_granules(sat, cmr_search(session, dnb + suffix, start, end), cmr_search(session, geo + suffix, start, end))
        if pairs:
            masks, erreurs = mask_urls(session, sat, start, end, rules)
            for g in pairs:
                g["masque_urls"] = masks.get(g["nom"].split(".", 1)[1], [])
                if erreurs and not g["masque_urls"]:
                    g["masque_recherche"] = "; ".join(erreurs)
            return pairs
    return []


def granule_non_evaluable(g: dict, rules: dict) -> str | None:
    """Motif quand l'absence de détection sur un granule ne prouve rien (ses détections restent valables) :
    * sans masque des nuages, au dessus de `lune_max_sans_masque` % de lune moyenne (80, seuil d'allenai, la même
      moyenne que la sienne), le modèle tient tout le ciel pour couvert et éteint la scène entière (essai du
      25/10/2026 : 4 bateaux sur 4 jusqu'à 80 %, 0 au dessus) ;
    * au dessus de `lune_max_absence` % (55), masque ou pas, le classifieur final d'allenai écarte les navires faibles
      (20 à 40 nW : 4 sur 4 jusqu'à 58 %, 3 à 60 %, 1 à 70 %, 0 à 75 %)."""
    v = rules["viirs"]
    lune = g.get("lune_moyenne")
    if lune is None:
        return None
    seuil = v.get("masque_nuages", {}).get("lune_max_sans_masque", 80)
    if (g.get("masque") or {}).get("etat") != "fourni" and lune > seuil:
        return (f"masque des nuages absent, lune à {lune:.0f} % : le modèle éteint toute la scène au dessus de "
                f"{seuil} %, aucune conclusion sur ce granule")
    absence = v.get("lune_max_absence", seuil)
    if lune > absence:
        return (f"lune à {lune:.0f} % : au dessus de {absence} %, le classifieur d'allenai écarte les navires faibles "
                "(mesuré : 3 sur 4 à 60 %, 1 à 70 %, aucun à 75 %), l'absence de détection ne prouve rien")
    return None



def plan(conn, session, rules: dict, now: datetime, nuits: list[date] | None = None) -> dict:
    """Granules à traiter : celles des dernières nuits terminées, ni traitées ni en cours (une granule en échec est
    retentée une fois). Une nuit manquée est ainsi rattrapée au passage suivant. Avec `nuits` (retraitement demandé,
    par exemple après le lot du masque des nuages) : toutes les granules de ces nuits, même déjà traitées."""
    if nuits:
        nights = sorted(set(nuits), reverse=True)
        done = set()
    else:
        nights = [n - timedelta(days=k) for n in [last_night(now, rules)] for k in range(rules["viirs"]["rattrapage_nuits"])]
        done = {r[0] for r in conn.execute(
            "SELECT nom FROM viirs_granules WHERE nuit = ANY(%s) AND (erreur IS NULL OR tentatives >= 2)",
            (nights,)).fetchall()}
    busy = set()
    for (p,) in conn.execute("SELECT parametres FROM travailleurs WHERE tache = 'viirs' AND detruit_le IS NULL").fetchall():
        busy |= {g["nom"] for g in (p or {}).get("granules", [])}
    todo, per_night = [], {}
    sats = list(rules["viirs"]["produits"].items())
    units = [(n, sat, pr) for n in nights for sat, pr in sats]
    # Une recherche par nuit et par satellite, en parallèle ; chaque appel CMR reste un span de la tâche
    with traces.span("viirs.recherche_granules", **{"viirs.nuits": len(nights), "viirs.recherches": len(units),
                                                    "viirs.appels_simultanes": CMR_SIMULTANES}) as sp:
        found = traces.en_parallele([lambda u=u: sat_granules(session, u[0], u[1], u[2], rules) for u in units],
                                    CMR_SIMULTANES)
        sp.set_attribute("viirs.granules_trouvees", sum(len(f) for f in found))
    for n in nights:
        gs = [g for (un, _s, _p), f in zip(units, found) if un == n for g in f
              if g["nom"] not in done and g["nom"] not in busy]
        per_night[n.isoformat()] = len(gs)
        todo += [{**g, "nuit": n.isoformat()} for g in gs]
    return {"nuits": per_night, "granules": todo}


# Traitement des résultats (handler du superviseur des travailleurs)

def detection_time(lon: float, lat: float, start: datetime, end: datetime, frame: list) -> datetime:
    """Instant d'une détection dans la granule : une granule balaie 6 minutes de trace ; la position du point entre
    la première ligne de balayage (deux premiers coins de l'emprise) et la dernière (deux suivants) donne l'instant."""
    try:
        (a, b), (c, d) = ((frame[0][0] + frame[1][0]) / 2, (frame[0][1] + frame[1][1]) / 2), \
                         ((frame[2][0] + frame[3][0]) / 2, (frame[2][1] + frame[3][1]) / 2)
        ux, uy = c - a, d - b
        f = ((lon - a) * ux + (lat - b) * uy) / (ux * ux + uy * uy)
    except (TypeError, IndexError, ZeroDivisionError):
        f = 0.5
    return start + (end - start) * min(1.0, max(0.0, f))


haversine_m = distance_m             # lot Z4 : distance commune (mars/geo.py)


def position_at(track: list[dict], t: datetime, max_min: float):
    """Position d'un navire à l'instant t : interpolation entre deux positions qui l'encadrent, sinon estime depuis
    la plus proche (route et vitesse déclarées) à `max_min` minutes au plus. Retourne (lon, lat, durée d'estime en s)."""
    before = [p for p in track if p["ts"] <= t]
    after = [p for p in track if p["ts"] > t]
    a, b = (before[-1] if before else None), (after[0] if after else None)
    lim = timedelta(minutes=max_min)
    if a and b and b["ts"] - a["ts"] <= 2 * lim:
        f = (t - a["ts"]) / (b["ts"] - a["ts"])
        return a["lon"] + f * (b["lon"] - a["lon"]), a["lat"] + f * (b["lat"] - a["lat"]), 0.0
    near = min((p for p in (a, b) if p), key=lambda p: abs(p["ts"] - t), default=None)
    if near is None or abs(near["ts"] - t) > lim:
        return None
    dt = (t - near["ts"]).total_seconds()
    sog, cog = near.get("sog") or 0.0, near.get("cog")
    if cog is None or sog < 0.5:
        return near["lon"], near["lat"], abs(dt)
    d = sog * KNOT * dt
    dlat = d * math.cos(math.radians(cog)) / 111320
    dlon = d * math.sin(math.radians(cog)) / (111320 * max(0.1, math.cos(math.radians(near["lat"]))))
    return near["lon"] + dlon, near["lat"] + dlat, abs(dt)


def arret_viirs(rules: dict) -> dict:
    """Maintien à l'arrêt (mars/fusion/positions.py) avec la tolérance de base du capteur VIIRS (pixel de 750 m)."""
    from mars.fusion.arret import arret_params
    return {**arret_params(rules), "base_m": float(rules["viirs"]["tolerance_m"])}


def position_ou_arret(track: list[dict], t: datetime, max_min: float, arret: dict | None):
    """Position à l'instant t (interpolation ou estime, `position_at`), sinon dernière position d'un navire à l'arrêt
    encore valable : (lon, lat, durée d'estime ou âge en s, tolérance ajoutée en m), ou None."""
    pos = position_at(track, t, max_min)
    if pos is not None:
        return (*pos, 0.0)
    if not arret:
        return None
    from mars.fusion.arret import maintien_arret
    m = maintien_arret(track, t, arret)
    if m is None:
        return None
    b, age = m
    return b["lon"], b["lat"], age * 60, arret["derive_m_par_min"] * age


def match(dets: list[dict], tracks: dict[int, list[dict]], rules: dict) -> dict[int, tuple[int, float]]:
    """Appariement glouton un pour un, par distance croissante : détection (indice) vers (navire, distance). La
    tolérance est celle du capteur (pixel de 750 m), augmentée de la moitié du chemin parcouru pendant l'estime, ou,
    pour la position ancienne d'un navire à l'arrêt (120 min au plus), de la dérive admise selon son âge."""
    v = rules["viirs"]
    arret = arret_viirs(rules) if "fusion" in rules else None
    cands = []
    for i, d in enumerate(dets):
        for vid, tr in tracks.items():
            pos = position_ou_arret(tr, d["ts"], v["estime_max_min"], arret)
            if pos is None:
                continue
            dist = haversine_m(d["lon"], d["lat"], pos[0], pos[1])
            sog = max((p.get("sog") or 0.0) for p in tr)
            tol = v["tolerance_m"] + (pos[3] if pos[3] else v["estime_marge_ratio"] * sog * KNOT * pos[2])
            if dist <= tol:
                cands.append((dist, i, vid))
    out, used = {}, set()
    for dist, i, vid in sorted(cands):
        if i not in out and vid not in used:
            out[i] = (vid, dist)
            used.add(vid)
    return out


def nearest_ais(lon: float, lat: float, t: datetime, tracks: dict[int, list[dict]], max_min: float):
    """Navire AIS le plus proche à l'instant t (position interpolée, ou estimée sur `max_min` minutes au plus) :
    (distance en m, navire, durée d'estime en s, 0 si interpolée), ou None si aucun navire n'est connu à cet instant."""
    best = None
    for vid, tr in tracks.items():
        pos = position_at(tr, t, max_min)
        if pos is None:
            continue
        d = haversine_m(lon, lat, pos[0], pos[1])
        if best is None or d < best[0]:
            best = (d, vid, pos[2])
    return best


def vessels_around(lon: float, lat: float, t: datetime, tracks: dict[int, list[dict]], radius_m: float,
                   window_min: float) -> int:
    """Navires AIS reçus à moins de `radius_m` dans les `window_min` minutes autour de t : mesure de la réception."""
    w = timedelta(minutes=window_min)
    return sum(1 for tr in tracks.values()
               if any(abs(p["ts"] - t) <= w and haversine_m(lon, lat, p["lon"], p["lat"]) <= radius_m for p in tr))


def feed_cut(counts: dict[datetime, int], t: datetime, ratio: float, margin_min: int, min_minutes: int = 3) -> bool:
    """Flux AIS coupé à l'instant t : au moins `min_minutes` minutes, à `margin_min` minutes près, sous `ratio` fois
    la médiane des positions par minute de la fenêtre (`counts`, minutes absentes comptées à zéro). Sans aucun
    compte, rien n'est jugé (statistiques pas encore calculées)."""
    if not counts:
        return False
    threshold = ratio * statistics.median(counts.values())
    minute = t.replace(second=0, microsecond=0)
    weak = sum(1 for k in range(-margin_min, margin_min + 1) if counts.get(minute + timedelta(minutes=k), 0) < threshold)
    return weak >= min_minutes


def severity(corridor: bool, listed: bool, coast_km: float | None, rules: dict) -> tuple[str, bool]:
    """Gravité d'une détection sans AIS, et « à confirmer » : critique dans un corridor et près d'un navire des
    listes, élevée pour l'un des deux, moyenne très au large, faible et à confirmer sinon (AIS incomplet, petite
    pêche sans obligation d'AIS)."""
    if corridor and listed:
        return "critique", False
    if corridor or listed:
        return "elevee", False
    if coast_km is not None and coast_km >= rules["viirs"]["tres_au_large_km"]:
        return "moyenne", False
    return "faible", True


def _tracks(conn, t0: datetime, t1: datetime, box: tuple, margin_min: int) -> dict[int, list[dict]]:
    rows = conn.execute(
        """SELECT vessel_id, ts, ST_X(geom::geometry), ST_Y(geom::geometry), sog_kn, cog_deg FROM positions
           WHERE ts BETWEEN %s AND %s AND ST_Intersects(geom::geometry, ST_MakeEnvelope(%s, %s, %s, %s, 4326))
           ORDER BY vessel_id, ts""",
        (t0 - timedelta(minutes=margin_min), t1 + timedelta(minutes=margin_min), *box)).fetchall()
    out: dict[int, list[dict]] = {}
    for vid, ts, lon, lat, sog, cog in rows:
        out.setdefault(vid, []).append({"ts": ts, "lon": lon, "lat": lat, "sog": sog, "cog": cog})
    return out


def _measure_granule(conn, gid: int, rules: dict):
    """Navire AIS le plus proche et réception AIS autour de chaque détection d'une granule (mesures stockées)."""
    v = rules["viirs"]
    dets = conn.execute("SELECT id, ts, ST_X(geom::geometry), ST_Y(geom::geometry) FROM viirs_detections WHERE granule_id = %s",
                        (gid,)).fetchall()
    if not dets:
        return
    r = v["couverture_rayon_km"] * 1000
    dlat = r / 111_000
    dlon = r / (111_000 * math.cos(math.radians(max(abs(d[3]) for d in dets))))
    box = (min(d[2] for d in dets) - dlon, min(d[3] for d in dets) - dlat, max(d[2] for d in dets) + dlon,
           max(d[3] for d in dets) + dlat)
    margin = max(v["estime_max_min"], v["couverture_fenetre_min"])
    tracks = _tracks(conn, min(d[1] for d in dets), max(d[1] for d in dets), box, margin)
    for did, ts, lon, lat in dets:
        n = nearest_ais(lon, lat, ts, tracks, v["estime_max_min"])
        conn.execute("UPDATE viirs_detections SET ais_proche_m = %s, ais_proche_vessel_id = %s, ais_proche_ecart_s = %s, "
                     "ais_navires_rayon = %s WHERE id = %s",
                     (n and n[0], n and n[1], n and n[2],
                      vessels_around(lon, lat, ts, tracks, r, v["couverture_fenetre_min"]), did))


def _evaluability(conn, ids: list[int], rules: dict) -> int:
    """Détections sans AIS non évaluables (aucune alerte), selon mars/evaluation.py : coupure du flux AIS à l'heure
    du passage, ou hors de la zone de réception fiable dès qu'elle est construite (sinon, critère provisoire : aucun
    navire reçu dans le rayon). Retourne leur nombre."""
    v, ratio = rules["viirs"], rules["continu"]["flux_min_ratio"]
    cells = conn.execute("SELECT EXISTS (SELECT 1 FROM reception_cells)").fetchone()[0]
    rows = conn.execute(
        """SELECT d.id, d.ts, d.ais_navires_rayon,
                  EXISTS (SELECT 1 FROM reception_cells c WHERE ST_Intersects(c.geom, d.geom)) AS couverte
           FROM viirs_detections d WHERE d.id = ANY(%s) AND d.mask_reason IS NULL AND d.matched_vessel_id IS NULL""",
        (ids,)).fetchall()
    n = 0
    for did, ts, around, covered in rows:
        counts = dict(conn.execute("SELECT minute, positions FROM stats_minute WHERE minute BETWEEN %s AND %s",
                                   (ts - timedelta(hours=3), ts + timedelta(hours=1))).fetchall())
        motif = motif_non_evaluable(feed_cut(counts, ts, ratio, v["coupure_marge_min"]), cells, covered, around, v)
        if motif is None:
            continue
        conn.execute("UPDATE viirs_detections SET mask_reason = 'non_evaluable', non_evaluable = %s WHERE id = %s",
                     (motif, did))
        n += 1
    return n


def reevaluate(conn, rules: dict, since: datetime | None = None) -> dict:
    """Réévalue les détections déjà en base avec les règles en vigueur : mesures AIS, détections non évaluables,
    gravité des alertes. Les alertes encore vierges d'une détection devenue non évaluable sont retirées ; celles qui
    portent une décision de l'opérateur sont gardées, en gravité faible, avec le motif."""
    since = since or datetime(2000, 1, 1, tzinfo=timezone.utc)
    sev_sql = ("SELECT severity, count(*) FROM alerts WHERE type = 'DARK_SHIP' AND details->>'source' = 'viirs' "
               "GROUP BY 1 ORDER BY 1")
    before = dict(conn.execute(sev_sql).fetchall())
    with conn.transaction():
        conn.execute("UPDATE viirs_detections SET mask_reason = NULL, non_evaluable = NULL "
                     "WHERE mask_reason = 'non_evaluable' AND ts >= %s", (since,))
        for (gid,) in conn.execute("SELECT DISTINCT granule_id FROM viirs_detections WHERE ts >= %s", (since,)).fetchall():
            _measure_granule(conn, gid, rules)
        ids = [r[0] for r in conn.execute("SELECT id FROM viirs_detections WHERE ts >= %s", (since,)).fetchall()]
        non_eval = _evaluability(conn, ids, rules)
        removed = kept = 0
        for aid, virgin, motif in conn.execute(
                """SELECT a.id, a.status = 'nouvelle' AND NOT EXISTS (SELECT 1 FROM alert_actions x WHERE x.alert_id = a.id),
                          coalesce(d.non_evaluable, d.mask_reason, 'appariée à l''AIS')
                   FROM alerts a JOIN alert_evidence e ON e.alert_id = a.id AND e.evidence_type = 'viirs'
                   JOIN viirs_detections d ON d.id = e.evidence_id
                   WHERE a.type = 'DARK_SHIP' AND d.ts >= %s
                     AND (d.mask_reason IS NOT NULL OR d.matched_vessel_id IS NOT NULL)""", (since,)).fetchall():
            if virgin:
                conn.execute("DELETE FROM alerts WHERE id = %s", (aid,))
                removed += 1
            else:
                conn.execute("UPDATE alerts SET severity = 'faible', details = details || %s WHERE id = %s",
                             (json.dumps({"non_evaluable": motif}), aid))
                kept += 1
        alerts = _alerts(conn, ids, rules)
    return {"detections": len(ids), "non_evaluables": non_eval, "alertes": alerts, "alertes_retirees": removed,
            "alertes_gardees_avec_decision": kept, "gravites_avant": before,
            "gravites_apres": dict(conn.execute(sev_sql).fetchall())}


def ingest(conn, w: dict, rules: dict) -> dict:
    """Résultat d'un travailleur VIIRS : granules et détections en base, appariement AIS, côtes, lumières fixes,
    alertes. Retourne le résumé (journal du travailleur et de task_runs)."""
    v = rules["viirs"]
    res = w["resultat"] or {}
    by_dnb = {g["dnb"]: g for g in (w["parametres"] or {}).get("granules", [])}
    out = {"granules": 0, "granules_en_echec": 0, "detections": 0, "appariees": 0, "cote": 0, "lumiere_fixe": 0,
           "alertes": 0, "reclassees": 0, "non_evaluables": 0}
    # res["erreur"] : arrêt du travailleur (données inaccessibles…) ; les granules reçues sont tout de même écrites,
    # le motif est porté par l'exécution (mars/travailleurs.py)
    new_ids: list[int] = []
    images: list[tuple] = []
    for g in res.get("granules", []):
        p = by_dnb.get(g.get("dnb"))
        if p is None:
            continue
        err = g.get("erreur")
        start = datetime.fromisoformat(g["debut"].replace("Z", "+00:00")) if g.get("debut") else datetime.fromisoformat(p["debut"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(g["fin"].replace("Z", "+00:00")) if g.get("fin") else start + timedelta(minutes=6)
        frame = [c for c in (g.get("emprise") or []) if None not in c]
        ewkt = ("SRID=4326;POLYGON((" + ",".join(f"{x} {y}" for x, y in frame) + "))") if len(frame) >= 4 else None
        m = g.get("masque") or {}
        etat = None if err else (m.get("etat") or "absent")
        motif_masque = m.get("motif") or (f"{m.get('source')} : {m.get('fichier')}" if m.get("etat") == "fourni" else None)
        if etat == "absent" and not motif_masque:
            motif_masque = "masque des nuages absent : " + (p.get("masque_recherche") or "non transmis par le travailleur")
        nev = None if err else granule_non_evaluable(g, rules)
        gid = conn.execute(
            """INSERT INTO viirs_granules (nom, satellite, nuit, debut, fin, emprise, lune, statut, erreur, travailleur_id,
                                           masque, masque_motif, ciel_clair_part, non_evaluable)
               VALUES (%s, %s, %s, %s, %s, ST_GeogFromText(%s), %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (nom) DO UPDATE SET debut = EXCLUDED.debut, fin = EXCLUDED.fin, emprise = EXCLUDED.emprise,
                 lune = EXCLUDED.lune, statut = EXCLUDED.statut, erreur = EXCLUDED.erreur,
                 travailleur_id = EXCLUDED.travailleur_id, traite_le = now(),
                 tentatives = viirs_granules.tentatives + 1, masque = EXCLUDED.masque,
                 masque_motif = EXCLUDED.masque_motif, ciel_clair_part = EXCLUDED.ciel_clair_part,
                 non_evaluable = EXCLUDED.non_evaluable
               RETURNING id""",
            (p["nom"], p["satellite"], p["nuit"], start, end, ewkt, g.get("lune_moyenne"),
             ",".join(g.get("statut") or []) or None, err, w["id"], etat, motif_masque, m.get("ciel_clair_part"),
             nev)).fetchone()[0]
        out["granules"] += 1
        if err:
            out["granules_en_echec"] += 1
            continue
        out["masques_fournis"] = out.get("masques_fournis", 0) + (etat == "fourni")
        if nev:
            out["granules_non_evaluables"] = out.get("granules_non_evaluables", 0) + 1
        with traces.span("viirs.granule", **{"viirs.granule": gid, "viirs.satellite": p["satellite"],
                                             "viirs.lune": g.get("lune_moyenne"), "viirs.masque": etat,
                                             "viirs.non_evaluable": nev is not None}) as sp:
            n = _ingest_granule(conn, gid, g, start, end, frame, p, rules, v, new_ids, images, out)
            sp.set_attribute("viirs.detections", n)
    if new_ids:
        with traces.span("viirs.evaluation_et_alertes", **{"viirs.detections": len(new_ids)}):
            out.update(_screen(conn, new_ids, rules, out))
    with traces.span("viirs.vignettes", **{"viirs.vignettes": len(images)}):
        out.update(_store_images(conn, images))
    return out


def _ingest_granule(conn, gid, g, start, end, frame, p, rules, v, new_ids, images, out) -> int:
    """Détections d'une granule : appariement AIS, écriture, mesures ; retourne le nombre de détections."""
    conn.execute("DELETE FROM alert_evidence WHERE evidence_type = 'viirs' AND evidence_id IN "
                 "(SELECT id FROM viirs_detections WHERE granule_id = %s)", (gid,))
    conn.execute("DELETE FROM viirs_detections WHERE granule_id = %s", (gid,))   # leurs preuves seront purgées
    dets = [{**d, "ts": detection_time(d["lon"], d["lat"], start, end, frame)} for d in g.get("detections", [])]
    if dets:
        box = (min(d["lon"] for d in dets) - 0.2, min(d["lat"] for d in dets) - 0.2,
               max(d["lon"] for d in dets) + 0.2, max(d["lat"] for d in dets) + 0.2)
        # Positions lues jusqu'à la durée bornée d'un navire à l'arrêt (fusion.arret_max_min) avant le granule
        marge = max(v["estime_max_min"], int(rules.get("fusion", {}).get("arret_max_min", 0)))
        matched = match(dets, _tracks(conn, start, end, box, marge), rules)
    else:
        matched = {}
    for i, d in enumerate(dets):
        vid, dist = matched.get(i, (None, None))
        did = conn.execute(
            """INSERT INTO viirs_detections (granule_id, ts, geom, nanowatts, orientation, lune, ciel_clair,
                   matched_vessel_id, match_distance_m)
               VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING RETURNING id""",
            (gid, d["ts"], d["lon"], d["lat"], d.get("nanowatts"), d.get("orientation"), d.get("lune"),
             d.get("ciel_clair"), vid, dist)).fetchone()
        if did:                                   # index unique : une même détection n'est jamais écrite deux fois
            new_ids.append(did[0])
            if d.get("vignette"):
                images.append((did[0], d["vignette"], d.get("vignette_geo"), d["ts"], p["nuit"], p["nom"]))
    conn.execute("UPDATE viirs_granules SET detections = (SELECT count(*) FROM viirs_detections WHERE granule_id = %s) "
                 "WHERE id = %s", (gid, gid))
    _measure_granule(conn, gid, rules)
    out["detections"] += len(dets)
    out["appariees"] += len(matched)
    return len(dets)


def _store_images(conn, images: list[tuple]) -> dict:
    """Vignettes des détections rangées sur R2 et inscrites au registre des preuves (mars/preuves.py). Sans R2
    configuré, ou en cas d'échec d'un envoi, la détection reste sans image (compté, sans bloquer le traitement)."""
    import base64
    from mars import preuves
    if not images:
        return {"vignettes": 0}
    try:
        from mars.r2 import R2
        r2 = R2()
    except Exception as e:
        return {"vignettes": 0, "vignettes_non_rangees": len(images), "vignettes_motif": str(e)[:200]}
    ok, failed, octets = 0, 0, 0
    for did, b64, geo, ts, nuit, nom in images:
        png = base64.b64decode(b64)
        try:
            preuves.store(conn, r2, "viirs", did, png, geo, ts, preuves.key("viirs", str(nuit), nom, did))
            ok += 1
            octets += len(png)
        except Exception:
            failed += 1
    return {"vignettes": ok, "vignettes_octets": octets, **({"vignettes_non_rangees": failed} if failed else {})}


def _screen(conn, ids: list[int], rules: dict, out: dict) -> dict:
    """Côtes, lumières fixes (persistance), puis alertes DARK_SHIP pour les détections sans AIS restantes."""
    v = rules["viirs"]
    # Côtes : distance à la terre (trait de côte GSHHG, table land), mesurée jusqu'à 100 km
    conn.execute(
        """UPDATE viirs_detections d SET distance_cote_m = (
               SELECT min(ST_Distance(l.geom, d.geom)) FROM land l WHERE ST_DWithin(l.geom, d.geom, 100000))
           WHERE d.id = ANY(%s)""", (ids,))
    out["cote"] = conn.execute(
        "UPDATE viirs_detections SET mask_reason = 'cote' WHERE id = ANY(%s) AND distance_cote_m < %s",
        (ids, v["cote_km"] * 1000)).rowcount
    # Lumières fixes : registre, puis persistance sur plusieurs nuits distinctes
    fixed = conn.execute(
        """UPDATE viirs_detections d SET mask_reason = 'lumiere_fixe' WHERE d.id = ANY(%s) AND d.mask_reason IS NULL
           AND EXISTS (SELECT 1 FROM viirs_lumieres_fixes f WHERE ST_DWithin(f.geom, d.geom, %s)) RETURNING d.id""",
        (ids, v["persistance_m"])).fetchall()
    persistent = conn.execute(
        """SELECT d.id, count(DISTINCT g2.nuit), min(d2.ts), max(d2.ts) FROM viirs_detections d
           JOIN viirs_detections d2 ON ST_DWithin(d.geom, d2.geom, %s) AND d2.ts > d.ts - make_interval(days => %s)
           JOIN viirs_granules g2 ON g2.id = d2.granule_id
           WHERE d.id = ANY(%s) AND d.mask_reason IS NULL GROUP BY d.id HAVING count(DISTINCT g2.nuit) >= %s""",
        (v["persistance_m"], v["persistance_jours"], ids, v["persistance_nuits"])).fetchall()
    reclassees = 0
    for did, nuits, first, last in persistent:
        # La détection, et ses précédentes sans AIS au même endroit, deviennent des lumières fixes
        conn.execute(
            """UPDATE viirs_detections d2 SET mask_reason = 'lumiere_fixe' FROM viirs_detections d
               WHERE d.id = %s AND (d2.id = d.id OR (d2.mask_reason IS NULL AND d2.matched_vessel_id IS NULL
                     AND ST_DWithin(d2.geom, d.geom, %s)))""", (did, v["persistance_m"]))
        conn.execute(
            """INSERT INTO viirs_lumieres_fixes (geom, nuits, premiere, derniere)
               SELECT geom, %s, %s, %s FROM viirs_detections WHERE id = %s
               AND NOT EXISTS (SELECT 1 FROM viirs_lumieres_fixes f, viirs_detections d
                               WHERE d.id = %s AND ST_DWithin(f.geom, d.geom, %s))""",
            (nuits, first, last, did, did, v["persistance_m"]))
        # Les alertes encore vierges levées sur cette lumière les nuits précédentes sont classées, avec une note
        for (aid,) in conn.execute(
                """SELECT a.id FROM alerts a, viirs_detections d WHERE d.id = %s AND a.type = 'DARK_SHIP'
                   AND a.details->>'source' = 'viirs' AND a.status = 'nouvelle' AND ST_DWithin(a.geom, d.geom, %s)""",
                (did, v["persistance_m"])).fetchall():
            conn.execute("UPDATE alerts SET status = 'classee' WHERE id = %s", (aid,))
            conn.execute("INSERT INTO alert_actions (alert_id, action, motif, note, author) VALUES "
                         "(%s, 'classer', 'faux_positif', %s, 'MARS C2')",
                         (aid, f"Lumière fixe : revue {nuits} nuits distinctes à moins de {v['persistance_m']} m"))
            reclassees += 1
    out["lumiere_fixe"] = len(fixed) + len(persistent)
    out["reclassees"] = reclassees
    out["non_evaluables"] = _evaluability(conn, ids, rules)
    out["alertes"] = _alerts(conn, ids, rules)
    return out


def _alerts(conn, ids: list[int], rules: dict) -> int:
    v = rules["viirs"]
    cells = conn.execute("SELECT EXISTS (SELECT 1 FROM reception_cells)").fetchone()[0]
    rows = conn.execute(
        """SELECT d.id, d.ts, ST_X(d.geom::geometry), ST_Y(d.geom::geometry), d.nanowatts, d.lune, d.ciel_clair,
                  d.distance_cote_m, g.satellite, g.nom, g.nuit, round(d.ais_proche_m) AS ais_m, d.ais_navires_rayon,
                  (SELECT json_build_object('id', i.id, 'name', i.name, 'type', i.attrs->>'type',
                                            'distance_m', round(ST_Distance(i.geom, d.geom)))
                   FROM infrastructure i WHERE ST_DWithin(i.geom, d.geom, %s)
                   ORDER BY ST_Distance(i.geom, d.geom) LIMIT 1) AS infra,
                  (SELECT json_build_object('vessel_id', v.id, 'name', v.name, 'mmsi', v.mmsi, 'flag', v.flag,
                                            'level', w.level, 'distance_km', round((ST_Distance(p.geom, d.geom) / 1000)::numeric, 1))
                   FROM positions p JOIN vessel_watch w ON w.vessel_id = p.vessel_id AND w.level <> 'autre_risque'
                   JOIN vessels v ON v.id = p.vessel_id
                   WHERE p.ts BETWEEN d.ts - interval '30 minutes' AND d.ts + interval '30 minutes'
                     AND ST_DWithin(p.geom, d.geom, %s)
                   ORDER BY ST_Distance(p.geom, d.geom) LIMIT 1) AS liste,
                  (SELECT coalesce(json_agg(c), '[]') FROM (
                     SELECT DISTINCT ON (p.vessel_id) p.vessel_id, v.name, v.mmsi, v.flag,
                            round(ST_Distance(p.geom, d.geom)) AS distance_m, %s AS rayon_tolere_m
                     FROM positions p JOIN vessels v ON v.id = p.vessel_id
                     WHERE p.ts BETWEEN d.ts - interval '15 minutes' AND d.ts + interval '15 minutes'
                       AND ST_DWithin(p.geom, d.geom, 10000)
                     ORDER BY p.vessel_id, abs(extract(epoch FROM p.ts - d.ts))) c) AS candidats
           FROM viirs_detections d JOIN viirs_granules g ON g.id = d.granule_id
           WHERE d.id = ANY(%s) AND d.mask_reason IS NULL AND d.matched_vessel_id IS NULL""",
        (v["corridor_m"], v["liste_rayon_km"] * 1000, v["tolerance_m"], ids)).fetchall()
    n = 0
    for (did, ts, lon, lat, nw, lune, ciel, cote_m, sat, nom, nuit, ais_m, around, infra, liste, cands) in rows:
        context = []
        if infra:
            context.append(f"À {infra['distance_m']:.0f} m d'une infrastructure ({infra.get('name') or infra.get('type')})")
        if liste:
            km = str(liste["distance_km"]).replace(".", ",")
            context.append(f"Navire des listes à {km} km : {liste.get('name') or liste.get('mmsi')}")
        if cote_m is None:
            context.append("Distance à la côte inconnue (trait de côte absent)")
        cands = sorted(cands or [], key=lambda c: c["distance_m"])[:3]
        coast_km = None if cote_m is None else cote_m / 1000
        sev, to_confirm = severity(bool(infra), bool(liste), coast_km, rules)
        if to_confirm:
            context.insert(0, "À confirmer : l'AIS peut être incomplet (un point par minute, réception partielle) et la "
                              "petite pêche n'a pas l'obligation d'AIS")
        doute = _doute(conn, did, ts, lon, lat, rules)
        if doute:
            # Prudence : un navire AIS connu dans la durée bornée peut expliquer la lumière, jamais une alerte élevée
            sev, to_confirm = "faible", True
            context.insert(0, motif_doute(doute, doute.get("name")))
        details = {
            "source": "viirs", "detection_id": did, "satellite": sat, "granule": nom, "nuit": str(nuit),
            "heure": ts.isoformat(), "nanowatts": nw, "lune": lune, "ciel_clair": ciel,
            "distance_cote_km": None if cote_m is None else round(cote_m / 1000, 1),
            "infrastructure": infra, "navire_liste": liste, "candidats_ais": cands, "contexte": context,
            "a_confirmer": to_confirm, "ais_proche_m": ais_m, "ais_navires_rayon": around, "navire_compatible": doute,
            "critere_evaluation": critere(cells),
            "motif": "Lumière en mer la nuit, sans navire AIS compatible alors que l'AIS est reçu alentour, loin des côtes "
                     "et absente les autres nuits",
            "parametres": {k: v[k] for k in ("tolerance_m", "cote_km", "persistance_m", "persistance_nuits", "corridor_m",
                                             "liste_rayon_km", "tres_au_large_km", "couverture_rayon_km")},
        }
        cle = f"viirs:{nom}:{lon:.4f}:{lat:.4f}"
        from mars import controle
        sev = controle.filtrer_sync(conn, rules, "DARK_SHIP", details, cle, sev, lon, lat)
        if sev is None:
            continue                     # règle en observation (épisode compté) ou coupée au panneau (lot P)
        aid = conn.execute(
            """INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key, controle_empreinte)
               VALUES ('DARK_SHIP', %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s)
               ON CONFLICT (type, rule_key) WHERE rule_key IS NOT NULL DO UPDATE
               SET rule_version = EXCLUDED.rule_version, controle_empreinte = EXCLUDED.controle_empreinte,
               """ + GARDE_VERIFICATION + """
               RETURNING id""",
            (sev, ts, lon, lat, json.dumps(details, default=str), rules["version"], cle,
             controle.stamp(conn, rules))).fetchone()[0]
        conn.execute("INSERT INTO alert_evidence VALUES (%s, 'viirs', %s) ON CONFLICT DO NOTHING", (aid, did))
        if liste:
            conn.execute("INSERT INTO alert_evidence VALUES (%s, 'vessel', %s) ON CONFLICT DO NOTHING", (aid, liste["vessel_id"]))
        if doute:
            conn.execute("INSERT INTO alert_evidence VALUES (%s, 'vessel', %s) ON CONFLICT DO NOTHING", (aid, doute["vessel_id"]))
        n += 1
    return n


def _doute(conn, did: int, ts: datetime, lon: float, lat: float, rules: dict) -> dict | None:
    """Navire AIS connu dans la durée bornée qui peut expliquer la détection (mars/fusion/doute.py), hors navires déjà
    appariés à une autre détection du même granule."""
    from mars.fusion.doute import compatible
    arret = arret_viirs(rules)
    box = (lon - 0.3, lat - 0.2, lon + 0.3, lat + 0.2)
    tracks = _tracks(conn, ts, ts, box, int(arret["max_min"]))
    exclus = {r[0] for r in conn.execute(
        "SELECT matched_vessel_id FROM viirs_detections WHERE granule_id = (SELECT granule_id FROM viirs_detections "
        "WHERE id = %s) AND matched_vessel_id IS NOT NULL", (did,)).fetchall()}
    c = compatible(lon, lat, ts, tracks, arret, exclus)
    if c:
        r = conn.execute("SELECT name FROM vessels WHERE id = %s", (c["vessel_id"],)).fetchone()
        c["name"] = r and r[0]
    return c


# Bilan des nuits (lot Z6) : lecture seule, une ligne par nuit depuis une date, nuits absentes comprises

BILAN_SQL = """
WITH nuits AS (SELECT d::date AS nuit FROM generate_series(%(du)s::date, (now() AT TIME ZONE 'UTC')::date - 1,
                                                          interval '1 day') d),
g AS (SELECT nuit, count(*) AS granules, count(*) FILTER (WHERE erreur IS NOT NULL) AS en_erreur,
             round(avg(lune)::numeric) AS lune, count(*) FILTER (WHERE masque = 'fourni') AS masques,
             count(*) FILTER (WHERE erreur IS NULL AND non_evaluable IS NOT NULL) AS granules_non_evaluables,
             array_agg(DISTINCT travailleur_id) FILTER (WHERE travailleur_id IS NOT NULL) AS travailleurs,
             max(traite_le) AS traitee_le
      FROM viirs_granules WHERE nuit >= %(du)s GROUP BY 1),
d AS (SELECT g.nuit, count(*) AS detections,
             count(*) FILTER (WHERE d.mask_reason IS NOT NULL) AS ecartees,
             count(*) FILTER (WHERE d.mask_reason IS NULL AND d.matched_vessel_id IS NOT NULL) AS avec_ais,
             count(*) FILTER (WHERE d.mask_reason IS NULL AND d.matched_vessel_id IS NULL
                              AND d.non_evaluable IS NOT NULL) AS non_evaluables,
             count(*) FILTER (WHERE d.mask_reason IS NULL AND d.matched_vessel_id IS NULL
                              AND d.non_evaluable IS NULL) AS sans_ais
      FROM viirs_detections d JOIN viirs_granules g ON g.id = d.granule_id WHERE g.nuit >= %(du)s GROUP BY 1),
a AS (SELECT (details->>'nuit')::date AS nuit, count(*) AS alertes,
             count(*) FILTER (WHERE severity IN ('elevee', 'critique')) AS alertes_hautes
      FROM alerts WHERE type = 'DARK_SHIP' AND details->>'source' = 'viirs' AND details ? 'nuit'
        AND (details->>'nuit')::date >= %(du)s GROUP BY 1),
w AS (SELECT g.nuit, string_agg(t.id || ' ' || t.etat || ' ' || coalesce(round(extract(epoch FROM t.detruit_le - t.cree_le)
                                 / 60)::text || ' min', 'en vie') || ' ' || coalesce(
                                 round(coalesce(t.mesures->>'rss_max_mo', t.mesures->'mesures_travailleur'->>'rss_max_mo')::numeric)::text || ' Mo', '')
                                 || ' ' || coalesce(to_char(coalesce(t.cout_reel_eur,
                                 (t.mesures->>'cout_estime_eur')::float8), 'FM0D00') || ' €', ''), ' ; ' ORDER BY t.id)
                                 AS travailleurs,
             sum(coalesce(t.cout_reel_eur, (t.mesures->>'cout_estime_eur')::float8, 0)) AS cout
      FROM g JOIN travailleurs t ON t.id = ANY(g.travailleurs) GROUP BY 1)
SELECT n.nuit, coalesce(g.granules, 0), coalesce(g.en_erreur, 0), coalesce(d.detections, 0), coalesce(d.avec_ais, 0),
       coalesce(d.sans_ais, 0), coalesce(d.non_evaluables, 0), coalesce(d.ecartees, 0), coalesce(a.alertes, 0),
       coalesce(a.alertes_hautes, 0), w.travailleurs, coalesce(w.cout, 0), g.traitee_le, g.lune,
       coalesce(g.masques, 0), coalesce(g.granules_non_evaluables, 0)
FROM nuits n LEFT JOIN g USING (nuit) LEFT JOIN d USING (nuit) LEFT JOIN a USING (nuit) LEFT JOIN w USING (nuit)
ORDER BY n.nuit"""

BILAN_COLS = ("nuit", "granules", "en_erreur", "detections", "avec_ais", "sans_ais", "non_evaluables", "ecartees",
              "alertes", "alertes_hautes", "travailleurs", "cout_eur", "traitee_le", "lune", "masques",
              "granules_non_evaluables")


def etat_nuit(n: dict) -> str:
    """« non évaluable » quand l'absence de détection ne prouve rien sur aucun granule traité sans erreur (lune
    forte) : jamais « 0 détection » ; « partielle » quand une partie seulement l'est ; « évaluable » sinon."""
    traites = n["granules"] - n["en_erreur"]
    if traites <= 0:
        return "aucune granule exploitable"
    if n["granules_non_evaluables"] >= traites:
        return "non évaluable"
    return "partielle" if n["granules_non_evaluables"] else "évaluable"


def bilan(conn, du) -> dict:
    """Nuits VIIRS depuis `du` (jusqu'à la veille) : granules et erreurs, détections par statut, alertes DARK_SHIP de
    source VIIRS, travailleurs (état, durée, mémoire, coût), plus les tâches viirs du journal (pause, refus, échec).
    Une nuit sans granule est une nuit manquée ou pas encore traitée (la nuit se traite vers 06:30 UTC)."""
    nuits = [dict(zip(BILAN_COLS, r)) for r in conn.execute(BILAN_SQL, {"du": du}).fetchall()]
    for n in nuits:
        n["etat"] = etat_nuit(n) if n["granules"] else None
    taches = conn.execute(
        "SELECT run_day, status, coalesce(details->>'en_pause', details->>'erreur', '') FROM task_runs "
        "WHERE task = 'viirs' AND run_day >= %s AND (status <> 'ok' OR details ? 'en_pause') ORDER BY started_at",
        (du,)).fetchall()
    tot = {k: sum(n[k] for n in nuits) for k in BILAN_COLS[1:10]}
    tot["cout_eur"] = round(sum(float(n["cout_eur"]) for n in nuits), 2)
    tot["nuits"], tot["nuits_traitees"] = len(nuits), sum(1 for n in nuits if n["granules"])
    tot["nuits_non_evaluables"] = sum(1 for n in nuits if n["etat"] == "non évaluable")
    return {"du": str(du), "nuits": nuits, "total": tot,
            "journal": [{"jour": str(j), "statut": s, "motif": m[:200]} for j, s, m in taches]}


def regions_boxes() -> list[list[float]]:
    """Emprises des régions pour le travailleur : [lon_min, lat_min, lon_max, lat_max]."""
    return [[b, a, d, c] for a, b, c, d in ZONES.values()]
