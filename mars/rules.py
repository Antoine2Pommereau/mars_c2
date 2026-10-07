"""Moteur de règles comportementales, partagé par l'API (lancement manuel par journée) et le conteneur taches
(évaluation continue toutes les quelques minutes, scripts/taches.py). Requêtes asyncpg.

Les alertes sont enregistrées par clé stable (colonne rule_key) : une nouvelle évaluation met à jour une alerte
existante sans toucher à son statut ni au journal des décisions des opérateurs. Seules les alertes encore vierges
(statut nouvelle, aucune décision) qui ne sont plus détectées sont retirées.

Règles : rendez vous (RENDEZVOUS), coupure AIS (AIS_GAP), navire d'une liste de surveillance dans nos eaux
(WATCHLIST), changement d'identité (IDENTITY_CHANGE). Les fonctions de décision des deux dernières, et la mesure des
coupures du flux, sont pures et couvertes par tests/test_rules.py.
"""
import re
import statistics
from datetime import datetime, timedelta, timezone

from mars.ais.live import ZONES
from mars.ais.mid import flag_of

WATCH_SEVERITY = {"fort": "critique", "sanctionne": "elevee", "flotte_fantome": "elevee", "suspect_gur": "moyenne"}
SEVERITIES = ["faible", "moyenne", "elevee", "critique"]


def stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")


# Enregistrement des alertes

async def save_alerts(c, kind: str, items: list[dict], prune_from=None, prune_to=None, time_field: str | None = None,
                      rule_version: str = "") -> dict:
    """Crée ou met à jour les alertes `items` (clé `key`) ; si une fenêtre et un champ de temps sont donnés, retire
    les alertes vierges de ce type, dans cette fenêtre, qui n'ont pas été retrouvées."""
    created = updated = 0
    async with c.transaction():
        for it in items:
            r = await c.fetchrow(
                """INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key)
                   VALUES ($1, $2, $3, ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography, $6, $7, $8)
                   ON CONFLICT (type, rule_key) WHERE rule_key IS NOT NULL DO UPDATE
                   SET severity = EXCLUDED.severity, event_time = EXCLUDED.event_time, geom = EXCLUDED.geom,
                       details = EXCLUDED.details, rule_version = EXCLUDED.rule_version
                   RETURNING id, (xmax = 0) AS created""",
                kind, it["severity"], it["event_time"], float(it["lon"]), float(it["lat"]), it["details"],
                rule_version, it["key"])
            created += r["created"]
            updated += not r["created"]
            await c.executemany("INSERT INTO alert_evidence VALUES ($1, 'vessel', $2) ON CONFLICT DO NOTHING",
                                [(r["id"], v) for v in dict.fromkeys(it["vessels"]) if v is not None])
        removed = 0
        if time_field and prune_from is not None:
            removed = int((await c.execute(
                f"""DELETE FROM alerts a
                    WHERE a.type = $1 AND a.status = 'nouvelle'
                      AND (a.details->>'{time_field}')::timestamptz >= $2 AND (a.details->>'{time_field}')::timestamptz < $3
                      AND NOT (coalesce(a.rule_key, '') = ANY($4::text[]))
                      AND NOT EXISTS (SELECT 1 FROM alert_actions x WHERE x.alert_id = a.id)""",
                kind, prune_from, prune_to, [it["key"] for it in items])).split()[-1])
    return {"nouvelles": created, "mises_a_jour": updated, "retirees": removed}


RENDEZVOUS_SQL = """
WITH slots AS (
    -- Dernière position de chaque navire dans chaque tranche de temps
    SELECT DISTINCT ON (p.vessel_id, slot)
           p.vessel_id, slot, p.geom, p.sog_kn, p.nav_status
    FROM (
        SELECT vessel_id, geom, sog_kn, nav_status, ts,
               to_timestamp(floor(extract(epoch FROM ts) / ($3::int * 60)) * ($3::int * 60)) AS slot
        FROM positions
        WHERE ts >= $1::timestamptz AND ts < $2::timestamptz
    ) p
    JOIN vessels v ON v.id = p.vessel_id
    WHERE NOT (coalesce(v.ship_type, '') = ANY($8::text[]))
    ORDER BY p.vessel_id, slot, p.ts DESC
),
slow AS (
    -- Navires lents, hors statuts exclus et hors abords immédiats de la côte : un navire à quai ou dans un bassin
    -- ne peut appartenir qu'à un épisode écarté plus bas comme côtier ; l'écarter ici évite l'appariement deux à
    -- deux des ports, qui faisait l'essentiel du coût (plusieurs milliers d'épisodes calculés puis rejetés).
    SELECT * FROM slots s
    WHERE sog_kn IS NOT NULL AND sog_kn < $4::float8 AND NOT (coalesce(nav_status, -1) = ANY($10::int[]))
      AND NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, s.geom, $12::float8))
),
pairs AS (
    SELECT a.slot, a.vessel_id AS v1, b.vessel_id AS v2,
           ST_Distance(a.geom, b.geom) AS d,
           (a.nav_status = 1 OR b.nav_status = 1) AS anchored,
           (a.nav_status = 5 OR b.nav_status = 5) AS moored,
           ST_Centroid(ST_Collect(a.geom::geometry, b.geom::geometry)) AS mid
    FROM slow a JOIN slow b
      ON a.slot = b.slot AND a.vessel_id < b.vessel_id AND ST_DWithin(a.geom, b.geom, $5::float8)
),
marked AS (
    SELECT *, CASE WHEN lag(slot) OVER w IS NULL OR slot - lag(slot) OVER w > make_interval(mins => $6::int) THEN 1 ELSE 0 END AS new_ep
    FROM pairs WINDOW w AS (PARTITION BY v1, v2 ORDER BY slot)
),
episodes AS (
    SELECT v1, v2, sum(new_ep) OVER (PARTITION BY v1, v2 ORDER BY slot) AS ep, slot, d, mid, anchored, moored FROM marked
),
grouped AS (
    SELECT v1, v2, ep, min(slot) AS t_start, max(slot) + make_interval(mins => $3::int) AS t_end,
           count(*) AS n_slots, min(d) AS d_min, avg(d) AS d_avg,
           avg(CASE WHEN anchored THEN 1.0 ELSE 0.0 END) AS anchored_share,
           avg(CASE WHEN moored THEN 1.0 ELSE 0.0 END) AS moored_share,
           ST_Centroid(ST_Collect(mid)) AS mid
    FROM episodes GROUP BY v1, v2, ep
),
filtered AS (
    -- Test rapide de proximité de la côte (index spatial, rayon borné) avant tout calcul de distance exact
    SELECT g.*, EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, g.mid::geography, $11::float8)) AS near_coast
    FROM grouped g
    WHERE g.t_end - g.t_start >= make_interval(mins => $9::int)
)
SELECT f.v1, f.v2, f.t_start, f.t_end, f.n_slots, f.d_min, f.d_avg, f.anchored_share::float8 AS anchored_share,
       f.moored_share::float8 AS moored_share,
       ST_X(f.mid) AS lon, ST_Y(f.mid) AS lat, f.near_coast, c.coast_m,
       EXISTS (SELECT 1 FROM stationary_zones z WHERE ST_DWithin(z.geom, f.mid::geography, $7::float8)) AS in_stationary_zone
FROM filtered f
LEFT JOIN LATERAL (
    -- Distance exacte à la côte, calculée seulement pour les épisodes au large
    SELECT min(ST_Distance(l.geom, f.mid::geography)) AS coast_m FROM land l
    WHERE NOT f.near_coast AND ST_DWithin(l.geom, f.mid::geography, 100000)
) c ON true
"""


async def run_rendezvous(c, day_start, day_end, rules: dict) -> dict:
    """Évalue la règle sur [day_start, day_end[ et met à jour les alertes RENDEZVOUS de cette période."""
    r = rules["rendezvous"]
    rows = await c.fetch(RENDEZVOUS_SQL, day_start, day_end, r["slot_min"], r["max_speed_kn"], r["max_distance_m"],
                         r["max_gap_min"], r["stationary_zone_buffer_m"], r["excluded_ship_types"],
                         r["min_duration_min"], r["excluded_nav_status"], r["min_coast_km"] * 1000.0,
                         float(r.get("coast_prefilter_m", 0)))
    names = {}
    if rows:
        ids = list({x["v1"] for x in rows} | {x["v2"] for x in rows})
        for v in await c.fetch("SELECT id, mmsi, name, ship_type, length_m FROM vessels WHERE id = ANY($1)", ids):
            names[v["id"]] = {"vessel_id": v["id"], "mmsi": v["mmsi"], "name": v["name"],
                              "ship_type": v["ship_type"],
                              "length_m": None if v["length_m"] is None or v["length_m"] != v["length_m"] else v["length_m"]}

    items, excluded, anchorage = [], {"cote": 0}, 0
    for x in rows:
        if x["near_coast"]:
            excluded["cote"] += 1
            continue
        coast_km = None if x["coast_m"] is None else x["coast_m"] / 1000
        duration_min = (x["t_end"] - x["t_start"]).total_seconds() / 60
        alongside = x["d_min"] <= r["alongside_m"]
        in_zone = bool(x["in_stationary_zone"])
        context = []
        if in_zone:
            context.append("Zone de mouillage connue : activité souvent légitime (avitaillement, transbordement autorisé)")
            anchorage += 1
        if alongside:
            context.append(f"Bord à bord ({round(x['d_min'])} m au plus près) : transfert physiquement possible")
        moored_offshore = bool(x["moored_share"] and x["moored_share"] > 0)
        if moored_offshore:
            context.append(f"Un navire se déclare « amarré » en pleine mer sur {round(100 * x['moored_share'])} % "
                           "de la rencontre : amarrage probable à l'autre navire")
        if x["anchored_share"] and x["anchored_share"] > 0:
            context.append(f"Statut déclaré « au mouillage » sur {round(100 * x['anchored_share'])} % de la rencontre")
        if in_zone:
            severity = "faible"
        elif (duration_min >= r["high_duration_min"] or alongside or moored_offshore
              or (coast_km is not None and coast_km >= r["high_coast_km"])):
            severity = "elevee"
        else:
            severity = "moyenne"
        details = {
            "navires": [names.get(x["v1"]), names.get(x["v2"])],
            "debut": x["t_start"].isoformat(), "fin": x["t_end"].isoformat(),
            "duree_min": round(duration_min), "distance_min_m": round(x["d_min"]),
            "distance_moyenne_m": round(x["d_avg"]),
            "distance_cote_km": None if coast_km is None else round(coast_km, 1),
            "bord_a_bord": alongside, "zone_de_mouillage": in_zone, "amarre_en_mer": moored_offshore,
            "contexte": context,
            "motif": "Deux navires quasi immobiles, à moins de 500 m l'un de l'autre, pendant plus de deux heures, "
                     "loin des côtes",
            "parametres": {k: r[k] for k in ("max_distance_m", "max_speed_kn", "min_duration_min", "min_coast_km",
                                             "alongside_m")},
        }
        # Instant où la règle aurait été franchie en direct : deux heures après le début de l'épisode
        event_time = x["t_start"] + timedelta(minutes=r["min_duration_min"])
        items.append({"v1": x["v1"], "v2": x["v2"], "severity": severity, "event_time": event_time,
                      "lon": x["lon"], "lat": x["lat"], "details": details, "vessels": [x["v1"], x["v2"]],
                      "t_start": x["t_start"], "t_end": x["t_end"]})

    # Un même épisode, vu tronqué par le début de la fenêtre ou prolongé depuis, garde sa clé et son début
    gap = timedelta(minutes=r["max_gap_min"])
    # Alertes existantes des mêmes paires, en une requête (une par épisode coûterait une lecture de table chacune)
    prior_by_pair: dict[tuple, list] = {}
    for p in await c.fetch(
            "SELECT rule_key, (details->>'debut')::timestamptz AS debut, (details->>'fin')::timestamptz AS fin "
            "FROM alerts WHERE type = 'RENDEZVOUS' AND rule_key IS NOT NULL "
            "AND (details->>'fin')::timestamptz >= $1::timestamptz - make_interval(mins => $2::int)",
            day_start, r["max_gap_min"]):
        prior_by_pair.setdefault(tuple(p["rule_key"].split(":")[:2]), []).append(p)
    for it in items:
        prior = prior_by_pair.get((str(it["v1"]), str(it["v2"])), [])
        same = [p for p in prior if p["debut"] <= it["t_end"] + gap and p["fin"] >= it["t_start"] - gap]
        if same:
            first = min(same, key=lambda p: p["debut"])
            it["key"] = first["rule_key"]
            if first["debut"] < it["t_start"]:
                it["details"]["debut"] = first["debut"].isoformat()
                it["details"]["duree_min"] = round((it["t_end"] - first["debut"]).total_seconds() / 60)
                it["event_time"] = first["debut"] + timedelta(minutes=r["min_duration_min"])
        else:
            it["key"] = f"{it['v1']}:{it['v2']}:{stamp(it['t_start'])}"
    saved = await save_alerts(c, "RENDEZVOUS", items, day_start, day_end, "debut", rules["version"])
    return {"episodes_detectes": len(rows), "alertes": len(items), "dont_zone_de_mouillage": anchorage,
            "exclus": excluded, **saved}


STATIONARY_ZONES_SQL = """
INSERT INTO stationary_zones (geom, vessels, slow_positions, day_from, day_to)
SELECT ST_MakeEnvelope(cx * $1::float8, cy * $2::float8, (cx + 1) * $1::float8, (cy + 1) * $2::float8, 4326)::geography,
       count(DISTINCT vessel_id), count(*), $5::date, $6::date
FROM (
    SELECT vessel_id, floor(ST_X(geom::geometry) / $1::float8)::int AS cx, floor(ST_Y(geom::geometry) / $2::float8)::int AS cy
    FROM positions
    WHERE sog_kn IS NOT NULL AND sog_kn < $3::float8 AND ts >= $5::date AND ts < ($6::date + 1)
) s
GROUP BY cx, cy
HAVING count(DISTINCT vessel_id) >= $4::int AND count(*) >= $7::int
"""


async def build_stationary_zones(c, day_from, day_to, rules: dict) -> int:
    z = rules["stationary_zones"]
    async with c.transaction():
        await c.execute("DELETE FROM stationary_zones")
        await c.execute(STATIONARY_ZONES_SQL, z["cell_deg_lon"], z["cell_deg_lat"], z["max_speed_kn"],
                        z["min_vessels"], day_from, day_to, z["min_slow_positions"])
        return await c.fetchval("SELECT count(*) FROM stationary_zones")


RECEPTION_SQL = """
INSERT INTO reception_cells (cx, cy, messages, vessels, hours, coverage, geom)
SELECT cx, cy, count(*), count(DISTINCT vessel_id), count(DISTINCT date_trunc('hour', ts)),
       avg(CASE WHEN dt <= $3::float8 THEN 1.0 ELSE 0.0 END),
       ST_MakeEnvelope(cx * $1::float8, cy * $2::float8, (cx + 1) * $1::float8, (cy + 1) * $2::float8, 4326)::geography
FROM (
    SELECT p.vessel_id, p.ts,
           floor(ST_X(p.geom::geometry) / $1::float8)::int AS cx, floor(ST_Y(p.geom::geometry) / $2::float8)::int AS cy,
           extract(epoch FROM lead(p.ts) OVER (PARTITION BY p.vessel_id ORDER BY p.ts) - p.ts) AS dt,
           p.sog_kn
    FROM positions p JOIN vessels v ON v.id = p.vessel_id
    WHERE v.ais_class = 'A' AND ($7::timestamptz IS NULL OR p.ts >= $7::timestamptz)
) s
WHERE sog_kn >= 1 AND dt IS NOT NULL AND dt < 7200
GROUP BY cx, cy
HAVING count(*) >= $5::int AND count(DISTINCT vessel_id) >= $6::int
   AND avg(CASE WHEN dt <= $3::float8 THEN 1.0 ELSE 0.0 END) >= $4::float8
"""


async def build_reception_cells(c, rules: dict, since=None) -> dict:
    """Zone de réception fiable : cellules où les trajectoires des navires en route sont continues. `since` borne
    les positions examinées (calcul plus léger en mémoire et en fichiers temporaires sur un petit serveur)."""
    z = rules["reception"]
    async with c.transaction():
        await c.execute("DELETE FROM reception_cells")
        await c.execute(RECEPTION_SQL, z["cell_deg_lon"], z["cell_deg_lat"], z["max_interval_s"],
                        z["min_coverage"], z["min_pairs"], z["min_vessels"], since)
        r = await c.fetchrow("SELECT count(*) AS n, round((sum(ST_Area(geom)) / 1e6)::numeric) AS km2, "
                             "round(avg(coverage)::numeric, 3) AS couverture FROM reception_cells")
    return {"cellules_fiables": r["n"], "surface_km2": float(r["km2"] or 0), "continuite_moyenne": float(r["couverture"] or 0)}


AIS_GAP_SQL = """
WITH p AS (
    SELECT vessel_id, ts, geom, sog_kn, cog_deg, nav_status,
           lead(ts) OVER w AS next_ts, lead(geom) OVER w AS next_geom,
           count(*) OVER (PARTITION BY vessel_id ORDER BY ts
                          RANGE BETWEEN make_interval(mins => $11::int) PRECEDING AND CURRENT ROW) AS prior_n
    FROM positions
    WHERE ts >= $1::timestamptz AND ts < $2::timestamptz
    WINDOW w AS (PARTITION BY vessel_id ORDER BY ts)
),
gaps AS (
    SELECT p.*, coalesce(p.next_ts, $2::timestamptz) - p.ts AS duration,
           -- Position estimée à l'estime : $13 minutes après le dernier message, ou $14 sans réapparition
           CASE WHEN p.cog_deg IS NULL THEN NULL
                ELSE ST_Project(p.geom, p.sog_kn * 0.514444 * 60 *
                                (CASE WHEN p.next_ts IS NULL THEN $14::float8 ELSE $13::float8 END),
                                radians(p.cog_deg))::geometry END AS proj
    FROM p
    WHERE coalesce(p.next_ts, $2::timestamptz) - p.ts >= make_interval(mins => $3::int)
      AND coalesce(p.sog_kn, 0) >= $4::float8
      AND p.prior_n >= $12::int
),
-- Couverture des données de chaque journée : union des zones collectées (à défaut, rectangle de l'import). Son bord
-- n'est une sortie possible qu'en mer : les portions de bord qui passent sur la terre sont retirées.
area AS (
    SELECT d.day, coalesce(d.coverage, ST_MakeEnvelope(d.lon_min, d.lat_min, d.lon_max, d.lat_max, 4326)) AS g
    FROM ais_days d
    WHERE d.day BETWEEN ($1::timestamptz AT TIME ZONE 'UTC')::date AND ($2::timestamptz AT TIME ZONE 'UTC')::date
),
edge AS (
    SELECT a.day, a.g AS area,
           coalesce(ST_Difference(ST_Boundary(a.g),
                                  (SELECT ST_Union(l.geom::geometry) FROM land l
                                   WHERE ST_Intersects(l.geom::geometry, ST_Boundary(a.g)))),
                    ST_Boundary(a.g)) AS sea_edge
    FROM area a
)
SELECT g.vessel_id, v.mmsi, v.name, v.ship_type, v.length_m, v.ais_class, g.prior_n,
       g.ts AS t_last, g.next_ts AS t_next, extract(epoch FROM g.duration)::float8 / 60 AS duration_min,
       g.sog_kn, g.nav_status,
       ST_X(g.geom::geometry) AS lon, ST_Y(g.geom::geometry) AS lat,
       ST_X(g.next_geom::geometry) AS next_lon, ST_Y(g.next_geom::geometry) AS next_lat,
       CASE WHEN g.next_geom IS NULL THEN NULL ELSE ST_Distance(g.geom, g.next_geom) END AS displacement_m,
       rc.messages AS cell_messages, rc.vessels AS cell_vessels, rc.hours AS cell_hours, rc.coverage AS cell_coverage,
       (g.proj IS NULL OR (
            EXISTS (SELECT 1 FROM reception_cells r2
                    WHERE r2.cx = floor(ST_X(g.proj) / $5::float8)::int AND r2.cy = floor(ST_Y(g.proj) / $6::float8)::int)
            AND ST_Within(g.proj, e.area) AND NOT ST_DWithin(g.proj, e.sea_edge, $9::float8))) AS projection_couverte
FROM gaps g
JOIN vessels v ON v.id = g.vessel_id
JOIN reception_cells rc
  ON rc.cx = floor(ST_X(g.geom::geometry) / $5::float8)::int AND rc.cy = floor(ST_Y(g.geom::geometry) / $6::float8)::int
JOIN edge e ON e.day = (g.ts AT TIME ZONE 'UTC')::date
WHERE coalesce(v.ais_class, '') = ANY($7::text[])
  AND NOT (coalesce(v.ship_type, '') = ANY($8::text[]))
  AND ST_Within(g.geom::geometry, e.area) AND NOT ST_DWithin(g.geom::geometry, e.sea_edge, $9::float8)
  AND NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, g.geom, $10::float8))
"""

PARTNERS_SQL = """
SELECT p.vessel_id, v.mmsi, v.name, v.ship_type, count(*) AS positions,
       round(min(ST_Distance(p.geom, $4::geography)))::int AS distance_min_m,
       min(p.ts) AS debut, max(p.ts) AS fin,
       bool_or(EXISTS (SELECT 1 FROM stationary_zones z WHERE ST_DWithin(z.geom, p.geom, 1000))) AS au_mouillage
FROM positions p JOIN vessels v ON v.id = p.vessel_id
WHERE p.ts BETWEEN $1::timestamptz AND $2::timestamptz
  AND p.vessel_id <> $3::bigint
  AND p.sog_kn IS NOT NULL AND p.sog_kn < $5::float8
  AND ST_DWithin(p.geom, $4::geography, $6::float8)
GROUP BY p.vessel_id, v.mmsi, v.name, v.ship_type
HAVING count(*) >= $7::int AND max(p.ts) - min(p.ts) >= make_interval(mins => $8::int)
ORDER BY count(*) DESC
LIMIT 3
"""


def _finite(v):
    return None if v is None or v != v else v


def outage_minutes(counts: dict, ratio: float) -> set:
    """Minutes où le flux AIS était coupé : moins de `ratio` fois la médiane des messages par minute de la fenêtre
    (minutes sans aucun message comprises, `counts` ne les contient pas)."""
    if not counts:
        return set()
    start, end = min(counts), max(counts)
    full = [counts.get(start + timedelta(minutes=k), 0) for k in range(int((end - start).total_seconds() // 60) + 1)]
    med = statistics.median(full)
    return {start + timedelta(minutes=k) for k, n in enumerate(full) if n < ratio * med}


def outage_overlap_min(outage: set, t0: datetime, t1: datetime) -> float:
    """Minutes de coupure du flux comprises dans [t0, t1[."""
    m = t0.replace(second=0, microsecond=0)
    n = 0
    while m < t1:
        n += m in outage
        m += timedelta(minutes=1)
    return float(n)


async def feed_outage(c, start, end, ratio: float) -> set:
    rows = await c.fetch("SELECT date_trunc('minute', ts) AS m, count(*) AS n FROM positions "
                         "WHERE ts >= $1::timestamptz AND ts < $2::timestamptz GROUP BY 1", start, end)
    return outage_minutes({r["m"]: r["n"] for r in rows}, ratio)


async def find_gaps(c, day_start, day_end, rules: dict):
    g, rc = rules["ais_gap"], rules["reception"]
    return await c.fetch(AIS_GAP_SQL, day_start, day_end, g["min_gap_min"], g["min_speed_kn"],
                         rc["cell_deg_lon"], rc["cell_deg_lat"], g["ais_classes"], g["excluded_ship_types"],
                         g["edge_margin_deg"], g["min_coast_km"] * 1000.0, g["prior_window_min"],
                         g["min_prior_messages"], g["projection_min"], g["projection_open_min"])


async def run_ais_gap(c, day_start, day_end, rules: dict) -> dict:
    """Évalue la règle des coupures AIS sur [day_start, day_end[ et met à jour les alertes AIS_GAP de cette période.
    Un silence n'est compté que pendant les minutes où le flux AIS fonctionnait (section continu de rules.yaml) : une
    coupure du flux AISStream ou de la collecte ne fait pas apparaître une coupure sur chaque navire."""
    g = rules["ais_gap"]
    rows = await find_gaps(c, day_start, day_end, rules)
    outage = await feed_outage(c, day_start, day_end, rules["continu"]["flux_min_ratio"])
    items, with_partner, exits, outages = [], 0, 0, 0
    for x in rows:
        if not x["projection_couverte"]:
            exits += 1   # cap et vitesse menaient hors de la zone fiable : sortie de couverture probable
            continue
        open_gap = x["t_next"] is None
        t_end = day_end if open_gap else x["t_next"]
        # Silence effectif : les minutes où le flux AIS lui même était coupé ne comptent pas
        lost = outage_overlap_min(outage, x["t_last"], t_end)
        if (t_end - x["t_last"]).total_seconds() / 60 - lost < g["min_gap_min"]:
            outages += 1
            continue
        # Lieux où une rencontre dissimulée laisserait une trace : la dernière position et la réapparition
        if open_gap:
            ref = f"SRID=4326;POINT({x['lon']} {x['lat']})"
        else:
            ref = f"SRID=4326;MULTIPOINT(({x['lon']} {x['lat']}),({x['next_lon']} {x['next_lat']}))"
        t_partner_end = min(t_end, x["t_last"] + timedelta(minutes=g["partner_window_max_min"]))
        partners = await c.fetch(PARTNERS_SQL, x["t_last"], t_partner_end, x["vessel_id"], ref,
                                 g["partner_max_speed_kn"], g["partner_radius_m"], g["partner_min_positions"],
                                 g["partner_min_slow_min"])
        partners = [{"vessel_id": p["vessel_id"], "mmsi": p["mmsi"], "name": p["name"], "ship_type": p["ship_type"],
                     "positions_lentes": p["positions"], "distance_min_m": p["distance_min_m"],
                     "debut": p["debut"].isoformat(), "fin": p["fin"].isoformat(),
                     "au_mouillage": p["au_mouillage"]} for p in partners]
        offshore_partners = [p for p in partners if not p["au_mouillage"]]
        linked = await c.fetch(
            "SELECT a.id FROM alerts a JOIN alert_evidence e ON e.alert_id = a.id "
            "WHERE a.type = 'RENDEZVOUS' AND e.evidence_type = 'vessel' AND e.evidence_id = $1 "
            "AND (a.details->>'debut')::timestamptz < $3 AND (a.details->>'fin')::timestamptz > $2",
            x["vessel_id"], x["t_last"], t_end)

        displacement_km = None if x["displacement_m"] is None else x["displacement_m"] / 1000
        implied_kn = (None if displacement_km is None or x["duration_min"] <= 0
                      else displacement_km * 1000 / (x["duration_min"] * 60) / 0.514444)
        context = []
        if offshore_partners:
            context.append(f"{len(offshore_partners)} navire(s) resté(s) lent(s) hors mouillage à moins de "
                           f"{g['partner_radius_m']} m de la dernière position ou de la réapparition : rencontre possible")
            with_partner += 1
        if len(partners) > len(offshore_partners):
            context.append(f"{len(partners) - len(offshore_partners)} navire(s) lent(s) au mouillage à proximité : "
                           "présence habituelle, indice faible")
        if linked:
            context.append("Rendez vous suspect enregistré pendant le silence")
        behaviour = None
        if open_gap:
            context.append("Pas de réapparition avant la fin des données de la journée, alors que la route "
                           "déclarée restait dans la zone de réception fiable")
        elif implied_kn is not None and x["sog_kn"] > 0:
            ratio = implied_kn / x["sog_kn"]
            if ratio < g["stop_ratio"] and x["sog_kn"] >= g["stop_min_speed_kn"]:
                behaviour = "arret"
                context.append(f"Arrêt pendant le silence : réapparition à {displacement_km:.1f} km seulement, "
                               f"soit {implied_kn:.1f} nœuds de moyenne contre {x['sog_kn']:.1f} déclarés avant")
            elif ratio > g["continue_ratio"]:
                behaviour = "route_poursuivie"
                context.append(f"Route poursuivie pendant le silence : {displacement_km:.1f} km à {implied_kn:.1f} "
                               f"nœuds de moyenne, cohérent avec les {x['sog_kn']:.1f} déclarés : silence moins suspect")
            else:
                context.append(f"Déplacement de {displacement_km:.1f} km à {implied_kn:.1f} nœuds de moyenne, "
                               f"contre {x['sog_kn']:.1f} déclarés avant la coupure")
        if offshore_partners or linked or behaviour == "arret":
            severity = "elevee"
        elif behaviour == "route_poursuivie":
            severity = "faible"
        elif open_gap and x["duration_min"] >= g["high_duration_min"]:
            severity = "elevee"
        else:
            severity = "moyenne"
        details = {
            "navire": {"vessel_id": x["vessel_id"], "mmsi": x["mmsi"], "name": x["name"], "ship_type": x["ship_type"],
                       "length_m": _finite(x["length_m"]), "classe_ais": x["ais_class"]},
            "dernier_message": x["t_last"].isoformat(),
            "reapparition": None if open_gap else x["t_next"].isoformat(),
            "duree_min": round(x["duration_min"]),
            "derniere_position": [x["lon"], x["lat"]],
            "position_reapparition": None if open_gap else [x["next_lon"], x["next_lat"]],
            "vitesse_avant_kn": round(x["sog_kn"], 1),
            "messages_heure_precedente": x["prior_n"],
            "deplacement_km": None if displacement_km is None else round(displacement_km, 1),
            "vitesse_implicite_kn": None if implied_kn is None else round(implied_kn, 1),
            "reception_cellule": {"messages": x["cell_messages"], "navires": x["cell_vessels"],
                                  "heures": x["cell_hours"], "continuite": round(x["cell_coverage"], 3)},
            "comportement": behaviour,
            "partenaires_possibles": partners,
            "rendez_vous_lies": [r["id"] for r in linked],
            "contexte": context,
            "motif": "Navire de classe A faisant route qui cesse d'émettre pendant plus de deux heures, au large, "
                     "dans une zone où la réception AIS est continue",
            "parametres": {k: g[k] for k in ("min_gap_min", "min_speed_kn", "min_coast_km", "partner_radius_m")},
        }
        if lost:
            context.append(f"Flux AIS interrompu {round(lost)} min pendant le silence : durée effective "
                           f"{round(x['duration_min'] - lost)} min")
        details["flux_interrompu_min"] = round(lost)
        event_time = x["t_last"] + timedelta(minutes=g["min_gap_min"])
        items.append({"key": f"{x['vessel_id']}:{stamp(x['t_last'])}", "severity": severity,
                      "event_time": event_time, "lon": x["lon"], "lat": x["lat"], "details": details,
                      "vessels": [x["vessel_id"]] + [p["vessel_id"] for p in partners]})

    # Les alertes dont le dernier message précède le début de la fenêtre plus la fenêtre d'historique de la règle ne
    # sont pas retirées : la fenêtre glissante ne voit plus leur historique, elles restent valables
    prune_from = day_start + timedelta(minutes=g["prior_window_min"])
    saved = await save_alerts(c, "AIS_GAP", items, prune_from, day_end, "dernier_message", rules["version"])
    return {"silences_detectes": len(rows), "exclus_sortie_de_couverture": exits,
            "exclus_coupure_du_flux": outages, "coupures_retenues": len(items),
            "avec_partenaire_possible": with_partner, "minutes_de_flux_coupe": len(outage), **saved}


# Navire d'une liste de surveillance dans nos eaux (WATCHLIST)

def passages(times: list[datetime], gap: timedelta) -> list[tuple[int, int]]:
    """Découpe une suite d'instants triés en passages : un silence de plus de `gap` ouvre un nouveau passage.
    Renvoie les indices (premier, dernier) de chaque passage."""
    out, first = [], 0
    for k in range(1, len(times)):
        if times[k] - times[k - 1] > gap:
            out.append((first, k - 1))
            first = k
    if times:
        out.append((first, len(times) - 1))
    return out


def zone_names(points) -> list[str]:
    seen = []
    for lon, lat in points:
        for name, (a, b, c, d) in ZONES.items():
            if a <= lat <= c and b <= lon <= d and name not in seen:
                seen.append(name)
    return seen


def watch_severity(level: str, matched_by: str) -> str:
    """Gravité selon le niveau de signal ; un cran de moins si le navire n'est reconnu que par son MMSI."""
    s = WATCH_SEVERITY[level]
    return s if matched_by == "omi" else SEVERITIES[max(0, SEVERITIES.index(s) - 1)]


async def run_watchlist(c, start, end, rules: dict) -> dict:
    """Une alerte par passage dans nos eaux d'un navire des listes (niveaux fort, sanctionné, flotte fantôme,
    suspect GUR). Un passage prolongé met à jour son alerte : pas une alerte par position."""
    w = rules["watchlist"]
    gap = timedelta(hours=w["passage_gap_h"])
    vessels = await c.fetch(
        """SELECT w.vessel_id, w.level, w.matched_by, w.entries, v.mmsi, v.imo, v.name, v.flag, v.ship_type, v.length_m
           FROM vessel_watch w JOIN vessels v ON v.id = w.vessel_id WHERE w.level = ANY($1::text[])""",
        w["niveaux"])
    items = []
    for v in vessels:
        pos = await c.fetch("SELECT ts, ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat FROM positions "
                            "WHERE vessel_id = $1 AND ts >= $2::timestamptz AND ts < $3::timestamptz ORDER BY ts",
                            v["vessel_id"], start, end)
        if not pos:
            continue
        prior = await c.fetch("SELECT rule_key, (details->>'debut')::timestamptz AS debut FROM alerts "
                              "WHERE type = 'WATCHLIST' AND rule_key LIKE $1 AND (details->>'fin')::timestamptz >= $2",
                              f"{v['vessel_id']}:%", pos[0]["ts"] - gap)
        former = [{"mmsi": e["mmsi"], "pavillon": flag_of(e["mmsi"]), "source": e["source"]}
                  for e in v["entries"] if e.get("mmsi") and e["mmsi"] != v["mmsi"]]
        for i, j in passages([p["ts"] for p in pos], gap):
            debut, fin = pos[i]["ts"], pos[j]["ts"]
            same = [p for p in prior if p["debut"] <= fin + gap] if i == 0 else []
            key = same[0]["rule_key"] if same else f"{v['vessel_id']}:{stamp(debut)}"
            if same and same[0]["debut"] < debut:
                debut = same[0]["debut"]
            context = []
            if v["matched_by"] != "omi":
                context.append("Reconnu par le MMSI seul : correspondance moins sûre qu'avec l'OMI")
            if former:
                context.append("MMSI différent sur la liste : " + ", ".join(
                    f"{f['mmsi']} ({f['pavillon'] or 'pavillon inconnu'})" for f in former))
            details = {
                "navire": {"vessel_id": v["vessel_id"], "mmsi": v["mmsi"], "imo": v["imo"], "name": v["name"],
                           "pavillon": v["flag"], "ship_type": v["ship_type"], "length_m": _finite(v["length_m"])},
                "niveau": v["level"], "reconnu_par": v["matched_by"], "sources": v["entries"],
                "autres_mmsi_sur_liste": former,
                "debut": debut.isoformat(), "fin": fin.isoformat(), "positions": j - i + 1,
                "zones": zone_names((p["lon"], p["lat"]) for p in pos[i:j + 1]),
                "entree": [pos[i]["lon"], pos[i]["lat"]], "derniere_position": [pos[j]["lon"], pos[j]["lat"]],
                "contexte": context,
                "motif": "Navire d'une liste de surveillance présent dans une zone couverte",
            }
            items.append({"key": key, "severity": watch_severity(v["level"], v["matched_by"]), "event_time": debut,
                          "lon": pos[j]["lon"], "lat": pos[j]["lat"], "details": details, "vessels": [v["vessel_id"]]})
    saved = await save_alerts(c, "WATCHLIST", items, rule_version=rules["version"])
    return {"navires_des_listes": len(vessels), "passages": len(items), **saved}


# Changement d'identité (IDENTITY_CHANGE)

def normalize_name(name: str | None) -> str | None:
    if not name:
        return None
    s = re.sub(r"[^A-Z0-9]+", " ", name.upper()).strip()
    return s or None


def placeholder_mmsi(mmsi: int) -> bool:
    """MMSI générique : code pays suivi de six zéros (227000000, partagé par des bâtiments de la Marine nationale)."""
    return mmsi % 1_000_000 == 0


def military(names, ship_type: str | None, ship_type_code: int | None, mmsi: int, p: dict) -> str | None:
    """Raison de tenir le navire pour un bâtiment militaire, ou None. Indices : type AIS déclaré (35, Military),
    MMSI de la liste `mmsi`, nom commençant par un préfixe de marine (HMS, FS, USS…), ou nom réduit à un numéro de
    coque (« 101 », émis avant le nom de baptême, comme 101 puis HMS CATTISTOCK)."""
    if ship_type_code == 35 or ship_type == "Military":
        return "type militaire déclaré"
    if mmsi in set(p.get("mmsi", [])):
        return "MMSI de la Marine"
    prefixes = set(p.get("prefixes_nom", []))
    for n in names:
        words = (normalize_name(n) or "").split()
        if words and words[0] in prefixes and len(words) > 1:
            return f"préfixe {words[0]}"
    for n in names:
        if re.fullmatch(r"\d{1,4}", normalize_name(n) or ""):
            return "numéro de coque"
    return None


def name_changes(rows: list[dict], now: datetime, confirmation: timedelta) -> list[dict]:
    """Nouveaux noms d'un navire (lignes de vessel_identities d'un même MMSI, triées par première vue).

    Un nom nouveau n'est retenu qu'une fois confirmé : apparu depuis au moins `confirmation`, et sans qu'aucun nom
    antérieur ait été revu depuis. Un navire qui alterne entre deux noms (MUTIN et FS MUTIN, deux émetteurs) n'est
    donc jamais signalé, ni au premier basculement ni aux suivants."""
    out = []
    for k, b in enumerate(rows):
        new = normalize_name(b["name"])
        before = [a for a in rows[:k] if normalize_name(a["name"])]
        if not new or not before or new in {normalize_name(a["name"]) for a in before}:
            continue
        if b["first_seen"] > now - confirmation:
            continue
        if any(a["last_seen"] > b["first_seen"] and normalize_name(a["name"]) != new for a in rows if a is not b
               and a["first_seen"] < b["first_seen"]):
            continue                      # un nom antérieur est revu après le nouveau : alternance
        out.append({"identity_id": b["id"], "ancien": before[-1], "nouveau": b})
    return out


def imo_changes(rows: list[dict], now: datetime, confirmation: timedelta) -> list[dict]:
    """Même OMI sous un autre MMSI. `rows` : identités portant un OMI (vessel_id, mmsi, imo, first_seen, last_seen).
    Pour chaque OMI, le premier MMSI vu est la référence ; chaque autre MMSI confirmé est un changement."""
    by_imo: dict[int, dict[int, dict]] = {}
    for r in rows:
        if placeholder_mmsi(r["mmsi"]):
            continue
        cur = by_imo.setdefault(r["imo"], {}).get(r["vessel_id"])
        if cur is None:
            by_imo[r["imo"]][r["vessel_id"]] = dict(r)
        else:
            cur["first_seen"] = min(cur["first_seen"], r["first_seen"])
            cur["last_seen"] = max(cur["last_seen"], r["last_seen"])
    out = []
    for imo, vs in by_imo.items():
        if len(vs) < 2:
            continue
        ordered = sorted(vs.values(), key=lambda r: r["first_seen"])
        for later in ordered[1:]:
            if later["first_seen"] > now - confirmation:
                continue
            earlier = [r for r in ordered if r["first_seen"] < later["first_seen"]]
            out.append({"imo": imo, "nouveau": later, "anciens": earlier,
                        "simultane": any(r["last_seen"] > later["first_seen"] for r in earlier),
                        "pavillon_change": any(flag_of(r["mmsi"]) != flag_of(later["mmsi"]) for r in earlier)})
    return out


def imo_severity(ch: dict, listed: bool, mil: str | None) -> str:
    """Gravité d'un même OMI sous un autre MMSI. Un navire des listes en cause garde la gravité élevée ; un bâtiment
    militaire reçoit la gravité minimale ; même pays des deux côtés : faible (moyenne en cas d'usage simultané) ;
    changement de pavillon : élevée."""
    if listed:
        return "elevee"
    if mil:
        return "faible"
    if not ch["pavillon_change"]:
        return "moyenne" if ch["simultane"] else "faible"
    return "elevee"


async def run_identity(c, start, end, rules: dict) -> dict:
    """Nouveau nom, ou même OMI sous un autre MMSI (avec ou sans changement de pavillon), d'après vessel_identities.
    Seules les identités apparues depuis `start` sont examinées ; les MMSI génériques et ceux de la liste
    `mmsi_ignores` sont écartés."""
    p = rules["identite"]
    conf = timedelta(minutes=p["confirmation_min"])
    ignored = set(p["mmsi_ignores"])
    rows = await c.fetch(
        """SELECT i.id, i.vessel_id, v.mmsi, v.ais_class, v.ship_type, v.ship_type_code, i.name, i.imo, i.callsign, i.flag, i.first_seen, i.last_seen,
                  i.messages
           FROM vessel_identities i JOIN vessels v ON v.id = i.vessel_id
           WHERE i.vessel_id IN (SELECT vessel_id FROM vessel_identities WHERE first_seen >= $1 AND first_seen < $2)
           ORDER BY i.vessel_id, i.first_seen""", start, end)
    by_vessel: dict[int, list[dict]] = {}
    for r in rows:
        if not placeholder_mmsi(r["mmsi"]) and r["mmsi"] not in ignored:
            by_vessel.setdefault(r["vessel_id"], []).append(dict(r))
    imo_rows = await c.fetch(
        """SELECT i.vessel_id, v.mmsi, v.ship_type, v.ship_type_code, i.name, i.imo, i.first_seen, i.last_seen
           FROM vessel_identities i
           JOIN vessels v ON v.id = i.vessel_id
           WHERE i.imo IN (SELECT imo FROM vessel_identities WHERE imo IS NOT NULL AND first_seen >= $1 AND first_seen < $2)""",
        start, end)
    watched = {r["vessel_id"] for r in await c.fetch("SELECT vessel_id FROM vessel_watch WHERE level <> 'autre_risque'")}

    async def last_position(vid):
        return await c.fetchrow("SELECT ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat FROM positions "
                                "WHERE vessel_id = $1 AND ts <= $2::timestamptz ORDER BY ts DESC LIMIT 1", vid, end)

    def ident(r):
        return {"mmsi": r["mmsi"], "name": r.get("name"), "imo": r.get("imo"), "pavillon": flag_of(r["mmsi"]),
                "premiere_vue": r["first_seen"].isoformat(), "derniere_vue": r["last_seen"].isoformat()}

    items, skipped = [], 0
    for vid, ids in by_vessel.items():
        for ch in name_changes(ids, end, conf):
            if ch["nouveau"]["first_seen"] < start:
                continue
            pos = await last_position(vid)
            if pos is None:
                skipped += 1
                continue
            b, a = ch["nouveau"], ch["ancien"]
            mil = military([x["name"] for x in ids], b["ship_type"], b["ship_type_code"], b["mmsi"], p["militaire"])
            severity = ("elevee" if vid in watched else "faible" if mil or b["ais_class"] == "B" else "moyenne")
            context = ["Navire d'une liste de surveillance"] if vid in watched else []
            if mil and vid not in watched:
                context.append(f"Bâtiment militaire probable ({mil}) : gravité minimale")
            details = {"changement": "nom", "navire": {"vessel_id": vid, "mmsi": b["mmsi"], "name": b["name"]},
                       "ancien_nom": a["name"], "nouveau_nom": b["name"], "debut": b["first_seen"].isoformat(),
                       "identites": [ident(x) for x in ids],
                       "militaire": mil, "contexte": context,
                       "motif": "Le navire émet sous un nouveau nom, confirmé dans la durée"}
            items.append({"key": f"nom:{vid}:{b['id']}", "severity": severity, "event_time": b["first_seen"],
                          "lon": pos["lon"], "lat": pos["lat"], "details": details, "vessels": [vid]})
    for ch in imo_changes([dict(r) for r in imo_rows if r["mmsi"] not in ignored], end, conf):
        new = ch["nouveau"]
        if new["first_seen"] < start:
            continue
        pos = await last_position(new["vessel_id"])
        if pos is None:
            skipped += 1
            continue
        context = []
        if ch["pavillon_change"]:
            context.append("Changement de pavillon : " + ", ".join(
                f"{flag_of(r['mmsi']) or '?'} (MMSI {r['mmsi']})" for r in ch["anciens"])
                + f" puis {flag_of(new['mmsi']) or '?'} (MMSI {new['mmsi']})")
        if ch["simultane"]:
            context.append("L'ancien MMSI émet encore après l'apparition du nouveau : usage simultané d'un même OMI")
        involved = [r["vessel_id"] for r in ch["anciens"]] + [new["vessel_id"]]
        listed = any(v in watched for v in involved)
        names = [r["name"] for r in imo_rows if r["imo"] == ch["imo"]]
        mil = military(names, new["ship_type"], new["ship_type_code"], new["mmsi"], p["militaire"])
        severity = imo_severity(ch, listed, mil)
        if listed:
            context.append("Navire d'une liste de surveillance")
        elif mil:
            context.append(f"Bâtiment militaire probable ({mil}) : gravité minimale")
        elif not ch["pavillon_change"]:
            context.append("Ancien et nouveau MMSI du même pays : réimmatriculation administrative probable")
        details = {"changement": "omi_autre_mmsi", "omi": ch["imo"],
                   "navire": {"vessel_id": new["vessel_id"], "mmsi": new["mmsi"]},
                   "debut": new["first_seen"].isoformat(), "pavillon_change": ch["pavillon_change"],
                   "usage_simultane": ch["simultane"], "militaire": mil,
                   "identites": [ident(r) for r in ch["anciens"] + [new]], "contexte": context,
                   "motif": "Le même numéro OMI apparaît sous un autre MMSI"}
        items.append({"key": f"omi:{ch['imo']}:{new['vessel_id']}", "severity": severity,
                      "event_time": new["first_seen"], "lon": pos["lon"], "lat": pos["lat"], "details": details,
                      "vessels": involved})
    saved = await save_alerts(c, "IDENTITY_CHANGE", items, rule_version=rules["version"])
    return {"changements": len(items), "sans_position": skipped, **saved}


# Évaluation continue

async def run_continuous(c, now: datetime, rules: dict) -> dict:
    """Un cycle des règles sur le flux en direct : fenêtre glissante, bornée par la dernière position reçue (une
    collecte en retard ne fait pas croire à des silences)."""
    p = rules["continu"]
    start = now - timedelta(hours=p["fenetre_h"])
    watermark = await c.fetchval("SELECT max(ts) FROM positions WHERE ts <= $1::timestamptz", now)
    out = {"fenetre": [start.isoformat(), watermark.isoformat() if watermark else None]}
    if watermark is None or watermark < start:
        return {**out, "ignore": "aucune position dans la fenêtre"}
    if await c.fetchval("SELECT EXISTS (SELECT 1 FROM land)"):
        out["rendezvous"] = await run_rendezvous(c, start, watermark, rules)
    else:
        out["rendezvous"] = "trait de côte absent : lancer scripts/build_masks.py"
    if await c.fetchval("SELECT EXISTS (SELECT 1 FROM reception_cells)"):
        out["ais_gap"] = await run_ais_gap(c, start, watermark, rules)
    else:
        out["ais_gap"] = "zone de réception absente : lancer scripts/build_masks.py"
    out["watchlist"] = await run_watchlist(c, start, now, rules)
    out["identite"] = await run_identity(c, now - timedelta(hours=p["identite_fenetre_h"]), now, rules)
    return out
