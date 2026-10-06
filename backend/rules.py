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
    WHERE v.ais_class = 'A'
) s
WHERE sog_kn >= 1 AND dt IS NOT NULL AND dt < 7200
GROUP BY cx, cy
HAVING count(*) >= $5::int AND count(DISTINCT vessel_id) >= $6::int
   AND avg(CASE WHEN dt <= $3::float8 THEN 1.0 ELSE 0.0 END) >= $4::float8
"""


async def build_reception_cells(c, rules: dict) -> dict:
    """Zone de réception fiable : cellules où les trajectoires des navires en route sont continues."""
    z = rules["reception"]
    async with c.transaction():
        await c.execute("DELETE FROM reception_cells")
        await c.execute(RECEPTION_SQL, z["cell_deg_lon"], z["cell_deg_lat"], z["max_interval_s"],
                        z["min_coverage"], z["min_pairs"], z["min_vessels"])
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


async def find_gaps(c, day_start, day_end, rules: dict):
    g, rc = rules["ais_gap"], rules["reception"]
    return await c.fetch(AIS_GAP_SQL, day_start, day_end, g["min_gap_min"], g["min_speed_kn"],
                         rc["cell_deg_lon"], rc["cell_deg_lat"], g["ais_classes"], g["excluded_ship_types"],
                         g["edge_margin_deg"], g["min_coast_km"] * 1000.0, g["prior_window_min"],
                         g["min_prior_messages"], g["projection_min"], g["projection_open_min"])


async def run_ais_gap(c, day_start, day_end, rules: dict) -> dict:
    """Évalue la règle des coupures AIS sur [day_start, day_end[ et remplace les alertes AIS_GAP de cette période."""
    g = rules["ais_gap"]
    rows = await find_gaps(c, day_start, day_end, rules)
    kept, with_partner, exits = 0, 0, 0
    async with c.transaction():
        await c.execute("DELETE FROM alerts WHERE type = 'AIS_GAP' AND (details->>'dernier_message')::timestamptz >= $1 "
                        "AND (details->>'dernier_message')::timestamptz < $2", day_start, day_end)
        for x in rows:
            if not x["projection_couverte"]:
                exits += 1   # cap et vitesse menaient hors de la zone fiable : sortie de couverture probable
                continue
            open_gap = x["t_next"] is None
            t_end = day_end if open_gap else x["t_next"]
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
            event_time = x["t_last"] + timedelta(minutes=g["min_gap_min"])
            alert_id = await c.fetchval(
                "INSERT INTO alerts (type, severity, event_time, geom, details, rule_version) "
                "VALUES ('AIS_GAP', $1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography, $5, $6) RETURNING id",
                severity, event_time, x["lon"], x["lat"], details, rules["version"])
            evidence = [(alert_id, "vessel", x["vessel_id"])] + [(alert_id, "vessel", p["vessel_id"]) for p in partners]
            await c.executemany("INSERT INTO alert_evidence VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", evidence)
            kept += 1
    return {"silences_detectes": len(rows), "exclus_sortie_de_couverture": exits, "coupures_retenues": kept,
            "avec_partenaire_possible": with_partner}
