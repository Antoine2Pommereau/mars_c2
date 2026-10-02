"""Règles comportementales évaluées en SQL sur l'archive AIS.

Les alertes sont calculées sur une journée entière, mais horodatées à l'instant où la règle aurait été
franchie en direct : l'interface ne les montre qu'une fois cet instant atteint par l'horloge simulée.
"""
from datetime import timedelta

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
    -- Navires lents, hors tranches où le navire se déclare amarré
    SELECT * FROM slots
    WHERE sog_kn IS NOT NULL AND sog_kn < $4::float8 AND NOT (coalesce(nav_status, -1) = ANY($10::int[]))
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
    """Évalue la règle sur [day_start, day_end[ et remplace les alertes RENDEZVOUS de cette période."""
    r = rules["rendezvous"]
    rows = await c.fetch(RENDEZVOUS_SQL, day_start, day_end, r["slot_min"], r["max_speed_kn"], r["max_distance_m"],
                         r["max_gap_min"], r["stationary_zone_buffer_m"], r["excluded_ship_types"],
                         r["min_duration_min"], r["excluded_nav_status"], r["min_coast_km"] * 1000.0)
    names = {}
    if rows:
        ids = list({x["v1"] for x in rows} | {x["v2"] for x in rows})
        for v in await c.fetch("SELECT id, mmsi, name, ship_type, length_m FROM vessels WHERE id = ANY($1)", ids):
            names[v["id"]] = {"vessel_id": v["id"], "mmsi": v["mmsi"], "name": v["name"],
                              "ship_type": v["ship_type"],
                              "length_m": None if v["length_m"] is None or v["length_m"] != v["length_m"] else v["length_m"]}

    kept, excluded, anchorage = 0, {"cote": 0}, 0
    async with c.transaction():
        await c.execute("DELETE FROM alerts WHERE type = 'RENDEZVOUS' AND (details->>'debut')::timestamptz >= $1 "
                        "AND (details->>'debut')::timestamptz < $2", day_start, day_end)
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
            alert_id = await c.fetchval(
                "INSERT INTO alerts (type, severity, event_time, geom, details, rule_version) "
                "VALUES ('RENDEZVOUS', $1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography, $5, $6) RETURNING id",
                severity, event_time, x["lon"], x["lat"], details, rules["version"])
            await c.executemany("INSERT INTO alert_evidence VALUES ($1, 'vessel', $2) ON CONFLICT DO NOTHING",
                                [(alert_id, x["v1"]), (alert_id, x["v2"])])
            kept += 1
    return {"episodes_detectes": len(rows), "alertes": kept, "dont_zone_de_mouillage": anchorage, "exclus": excluded}


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
