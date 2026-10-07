"""Détections nocturnes VIIRS (étape 3, lot B), côté serveur : le serveur choisit les granules, un travailleur
éphémère les analyse (travailleurs/viirs.py, modèle allenai/vessel-detection-viirs), et le serveur reçoit les
détections pour les apparier à l'AIS, écarter les lumières fixes et les côtes, et lever les alertes DARK_SHIP.

Sources : bande Day/Night de Suomi NPP (VNP02DNB), NOAA 20 (VJ102DNB) et NOAA 21 (VJ202DNB), avec leur
géolocalisation (VNP03DNB, VJ103DNB, VJ203DNB), cherchées dans le catalogue CMR de la NASA (public) : produits en
temps quasi réel (« _NRT », environ 2 à 3 heures après le passage) d'abord, produits standard ensuite pour une nuit
rattrapée. Le serveur ne lit que les métadonnées ; le travailleur télécharge avec le jeton Earthdata.

Les fonctions de calcul (nuit d'un instant, paires de granules, instant d'une détection, appariement, gravité) sont
pures et couvertes par tests/test_viirs.py.
"""
import json
import math
import re
from datetime import date, datetime, timedelta, timezone

from mars.ais.live import ZONES
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


def night_granules(session, d: date, rules: dict) -> list[dict]:
    """Granules de nuit d'une nuit sur nos régions, par satellite : temps quasi réel, sinon produits standard."""
    start, end = night_window(d, rules)
    out = []
    for sat, (dnb, geo) in rules["viirs"]["produits"].items():
        for suffix in ("_NRT", ""):
            pairs = pair_granules(sat, cmr_search(session, dnb + suffix, start, end), cmr_search(session, geo + suffix, start, end))
            if pairs:
                out += pairs
                break
    return out


def plan(conn, session, rules: dict, now: datetime) -> dict:
    """Granules à traiter : celles des dernières nuits terminées, ni traitées ni en cours (une granule en échec est
    retentée une fois). Une nuit manquée est ainsi rattrapée au passage suivant."""
    nights = [n - timedelta(days=k) for n in [last_night(now, rules)] for k in range(rules["viirs"]["rattrapage_nuits"])]
    done = {r[0] for r in conn.execute(
        "SELECT nom FROM viirs_granules WHERE nuit = ANY(%s) AND (erreur IS NULL OR tentatives >= 2)", (nights,)).fetchall()}
    busy = set()
    for (p,) in conn.execute("SELECT parametres FROM travailleurs WHERE tache = 'viirs' AND detruit_le IS NULL").fetchall():
        busy |= {g["nom"] for g in (p or {}).get("granules", [])}
    todo, per_night = [], {}
    for n in nights:
        gs = [g for g in night_granules(session, n, rules) if g["nom"] not in done and g["nom"] not in busy]
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


def haversine_m(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


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


def match(dets: list[dict], tracks: dict[int, list[dict]], rules: dict) -> dict[int, tuple[int, float]]:
    """Appariement glouton un pour un, par distance croissante : détection (indice) vers (navire, distance). La
    tolérance est celle du capteur (pixel de 750 m), augmentée de la moitié du chemin parcouru pendant l'estime."""
    v = rules["viirs"]
    cands = []
    for i, d in enumerate(dets):
        for vid, tr in tracks.items():
            pos = position_at(tr, d["ts"], v["estime_max_min"])
            if pos is None:
                continue
            dist = haversine_m(d["lon"], d["lat"], pos[0], pos[1])
            sog = max((p.get("sog") or 0.0) for p in tr)
            tol = v["tolerance_m"] + v["estime_marge_ratio"] * sog * KNOT * pos[2]
            if dist <= tol:
                cands.append((dist, i, vid))
    out, used = {}, set()
    for dist, i, vid in sorted(cands):
        if i not in out and vid not in used:
            out[i] = (vid, dist)
            used.add(vid)
    return out


def severity(corridor: bool, listed: bool) -> str:
    return "critique" if corridor and listed else "elevee" if corridor or listed else "moyenne"


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


def ingest(conn, w: dict, rules: dict) -> dict:
    """Résultat d'un travailleur VIIRS : granules et détections en base, appariement AIS, côtes, lumières fixes,
    alertes. Retourne le résumé (journal du travailleur et de task_runs)."""
    v = rules["viirs"]
    res = w["resultat"] or {}
    by_dnb = {g["dnb"]: g for g in (w["parametres"] or {}).get("granules", [])}
    out = {"granules": 0, "granules_en_echec": 0, "detections": 0, "appariees": 0, "cote": 0, "lumiere_fixe": 0,
           "alertes": 0, "reclassees": 0}
    if res.get("erreur"):
        raise RuntimeError(f"travailleur : {res['erreur']}")
    new_ids: list[int] = []
    for g in res.get("granules", []):
        p = by_dnb.get(g.get("dnb"))
        if p is None:
            continue
        err = g.get("erreur")
        start = datetime.fromisoformat(g["debut"].replace("Z", "+00:00")) if g.get("debut") else datetime.fromisoformat(p["debut"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(g["fin"].replace("Z", "+00:00")) if g.get("fin") else start + timedelta(minutes=6)
        frame = [c for c in (g.get("emprise") or []) if None not in c]
        ewkt = ("SRID=4326;POLYGON((" + ",".join(f"{x} {y}" for x, y in frame) + "))") if len(frame) >= 4 else None
        gid = conn.execute(
            """INSERT INTO viirs_granules (nom, satellite, nuit, debut, fin, emprise, lune, statut, erreur, travailleur_id)
               VALUES (%s, %s, %s, %s, %s, ST_GeogFromText(%s), %s, %s, %s, %s)
               ON CONFLICT (nom) DO UPDATE SET debut = EXCLUDED.debut, fin = EXCLUDED.fin, emprise = EXCLUDED.emprise,
                 lune = EXCLUDED.lune, statut = EXCLUDED.statut, erreur = EXCLUDED.erreur,
                 travailleur_id = EXCLUDED.travailleur_id, traite_le = now(),
                 tentatives = viirs_granules.tentatives + 1
               RETURNING id""",
            (p["nom"], p["satellite"], p["nuit"], start, end, ewkt, g.get("lune_moyenne"),
             ",".join(g.get("statut") or []) or None, err, w["id"])).fetchone()[0]
        out["granules"] += 1
        if err:
            out["granules_en_echec"] += 1
            continue
        conn.execute("DELETE FROM alert_evidence WHERE evidence_type = 'viirs' AND evidence_id IN "
                     "(SELECT id FROM viirs_detections WHERE granule_id = %s)", (gid,))
        conn.execute("DELETE FROM viirs_detections WHERE granule_id = %s", (gid,))
        dets = [{**d, "ts": detection_time(d["lon"], d["lat"], start, end, frame)} for d in g.get("detections", [])]
        if dets:
            box = (min(d["lon"] for d in dets) - 0.2, min(d["lat"] for d in dets) - 0.2,
                   max(d["lon"] for d in dets) + 0.2, max(d["lat"] for d in dets) + 0.2)
            matched = match(dets, _tracks(conn, start, end, box, v["estime_max_min"]), rules)
        else:
            matched = {}
        for i, d in enumerate(dets):
            vid, dist = matched.get(i, (None, None))
            did = conn.execute(
                """INSERT INTO viirs_detections (granule_id, ts, geom, nanowatts, orientation, lune, ciel_clair,
                       matched_vessel_id, match_distance_m)
                   VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (gid, d["ts"], d["lon"], d["lat"], d.get("nanowatts"), d.get("orientation"), d.get("lune"),
                 d.get("ciel_clair"), vid, dist)).fetchone()[0]
            new_ids.append(did)
        conn.execute("UPDATE viirs_granules SET detections = %s WHERE id = %s", (len(dets), gid))
        out["detections"] += len(dets)
        out["appariees"] += len(matched)
    if new_ids:
        out.update(_screen(conn, new_ids, rules, out))
    return out


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
    out["alertes"] = _alerts(conn, ids, rules)
    return out


def _alerts(conn, ids: list[int], rules: dict) -> int:
    v = rules["viirs"]
    rows = conn.execute(
        """SELECT d.id, d.ts, ST_X(d.geom::geometry), ST_Y(d.geom::geometry), d.nanowatts, d.lune, d.ciel_clair,
                  d.distance_cote_m, g.satellite, g.nom, g.nuit,
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
    for (did, ts, lon, lat, nw, lune, ciel, cote_m, sat, nom, nuit, infra, liste, cands) in rows:
        context = []
        if infra:
            context.append(f"À {infra['distance_m']:.0f} m d'une infrastructure ({infra.get('name') or infra.get('type')})")
        if liste:
            km = str(liste["distance_km"]).replace(".", ",")
            context.append(f"Navire des listes à {km} km : {liste.get('name') or liste.get('mmsi')}")
        if cote_m is None:
            context.append("Distance à la côte inconnue (trait de côte absent)")
        cands = sorted(cands or [], key=lambda c: c["distance_m"])[:3]
        details = {
            "source": "viirs", "detection_id": did, "satellite": sat, "granule": nom, "nuit": str(nuit),
            "heure": ts.isoformat(), "nanowatts": nw, "lune": lune, "ciel_clair": ciel,
            "distance_cote_km": None if cote_m is None else round(cote_m / 1000, 1),
            "infrastructure": infra, "navire_liste": liste, "candidats_ais": cands, "contexte": context,
            "motif": "Lumière en mer la nuit, sans navire AIS compatible, loin des côtes et absente les autres nuits",
            "parametres": {k: v[k] for k in ("tolerance_m", "cote_km", "persistance_m", "persistance_nuits", "corridor_m",
                                             "liste_rayon_km")},
        }
        aid = conn.execute(
            """INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key)
               VALUES ('DARK_SHIP', %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s)
               ON CONFLICT (type, rule_key) WHERE rule_key IS NOT NULL DO UPDATE
               SET severity = EXCLUDED.severity, details = EXCLUDED.details, rule_version = EXCLUDED.rule_version
               RETURNING id""",
            (severity(bool(infra), bool(liste)), ts, lon, lat, json.dumps(details, default=str), rules["version"],
             f"viirs:{nom}:{lon:.4f}:{lat:.4f}")).fetchone()[0]
        conn.execute("INSERT INTO alert_evidence VALUES (%s, 'viirs', %s) ON CONFLICT DO NOTHING", (aid, did))
        if liste:
            conn.execute("INSERT INTO alert_evidence VALUES (%s, 'vessel', %s) ON CONFLICT DO NOTHING", (aid, liste["vessel_id"]))
        n += 1
    return n


def regions_boxes() -> list[list[float]]:
    """Emprises des régions pour le travailleur : [lon_min, lat_min, lon_max, lat_max]."""
    return [[b, a, d, c] for a, b, c, d in ZONES.values()]
