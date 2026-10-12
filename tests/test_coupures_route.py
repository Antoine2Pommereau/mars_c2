"""Coupures AIS (AIS_GAP, mode observation) : route déclarée qui quitte la zone fiable dans l'horizon, et points de
disparition collectifs (constat du 11/10/2026 au sud de l'île de Wight). Tests en base avec MARS_TEST_DATABASE_URL."""
import asyncio
import json
import math
import os
from datetime import datetime, timedelta, timezone

import pytest

from mars.config import load_rules
from mars.rules import collective_points

RULES = load_rules()
G = RULES["ais_gap"]
URL = os.environ.get("MARS_TEST_DATABASE_URL")
T0 = datetime(2026, 10, 11, 0, 39, tzinfo=timezone.utc)


def _row(vid, lon, lat, minutes):
    return {"vessel_id": vid, "lon": lon, "lat": lat, "t_last": T0 + timedelta(minutes=minutes)}


def test_collective_point_needs_three_unrelated_vessels_the_same_day():
    rows = [_row(1, -0.85, 50.35, 0), _row(2, -0.83, 50.36, 90), _row(3, -0.88, 50.33, 400)]
    pts = collective_points(rows, G)
    assert list(pts.values()) == [3]
    # Deux navires qui se taisent ensemble (300 m, même minute) comptent pour un : plus de point collectif
    assert collective_points([rows[0], _row(2, -0.846, 50.35, 1), rows[2]], G) == {}
    # Le même endroit, mais des jours différents : pas de limite de réception établie
    assert collective_points([rows[0], rows[1], _row(3, -0.88, 50.33, 60 * 24)], G) == {}


# Sur la base : zone fiable en deux morceaux le long du parallèle 50,3 N, trou entre les deux

LAT = 50.35                                             # milieu d'une maille de 0,1 degré
DLON = 1 / (111_320 * math.cos(math.radians(LAT)))      # degrés de longitude par mètre


def _cells(lon0, lon1):
    z = RULES["reception"]
    return [(cx, cy) for cx in range(math.floor(lon0 / z["cell_deg_lon"]), math.floor(lon1 / z["cell_deg_lon"]) + 1)
            for cy in range(math.floor((LAT - 0.2) / z["cell_deg_lat"]), math.floor((LAT + 0.2) / z["cell_deg_lat"]) + 1)]


async def _scenario(c):
    z = RULES["reception"]
    for t in ("positions", "reception_cells", "ais_days", "stats_minute"):
        await c.execute(f"DELETE FROM {t}")
    await c.execute("DELETE FROM vessels WHERE mmsi BETWEEN 636999000 AND 636999999")
    # Zone fiable : à l'est de -0,65 (où les navires se taisent) et un îlot de -1,4 à -1,1 ; trou entre les deux
    for cx, cy in _cells(-0.65, 0.5) + _cells(-1.4, -1.1):
        x0, y0 = cx * z["cell_deg_lon"], cy * z["cell_deg_lat"]
        await c.execute("INSERT INTO reception_cells (cx, cy, messages, vessels, hours, coverage, geom) VALUES ($1, $2, 100, "
                        "5, 10, 0.95, ST_MakeEnvelope($3, $4, $5, $6, 4326)::geography) ON CONFLICT DO NOTHING",
                        cx, cy, x0, y0, x0 + z["cell_deg_lon"], y0 + z["cell_deg_lat"])
    await c.execute("INSERT INTO ais_days (day, messages, vessels, lon_min, lat_min, lon_max, lat_max) VALUES "
                    "('2026-10-10', 1, 1, -5, 48.4, 2.6, 51.2), ('2026-10-11', 1, 1, -5, 48.4, 2.6, 51.2) ON CONFLICT DO NOTHING")
    ids = {}
    # NORDIC C : 14 nœuds, cap 270, se tait en -0,5 ; sa route sort de la zone en 25 minutes, mais son point projeté à
    # 120 minutes (-1,23) retombe dans l'îlot : l'ancien contrôle le retenait comme coupure
    # Trois navires sans lien, cap 90 (route couverte), se taisent dans la même maille à des heures différentes
    ships = [("NORDIC C", 636999001, -0.50, LAT, 270, 0), ("EST 1", 636999002, -0.30, LAT + 0.02, 90, 0),
             ("EST 2", 636999003, -0.31, LAT - 0.02, 90, 120), ("EST 3", 636999004, -0.28, LAT + 0.01, 90, 300)]
    for name, mmsi, lon_end, lat, cog, offset in ships:
        vid = await c.fetchval("INSERT INTO vessels (mmsi, name, ship_type, length_m, ais_class) VALUES ($1, $2, "
                               "'Cargo', 180, 'A') RETURNING id", mmsi, name)
        ids[name] = vid
        t_last = T0 + timedelta(minutes=offset)
        step = 14 * 0.514444 * 60 * DLON * (1 if cog == 90 else -1)        # une position par minute, 14 nœuds
        for k in range(60, -1, -1):
            await c.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn, cog_deg) VALUES ($1, $2, "
                            "ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography, 14, $5)",
                            vid, t_last - timedelta(minutes=k), lon_end - k * step, lat, float(cog))
    return ids


def test_route_leaving_the_zone_is_an_exit_and_collective_points_are_downgraded():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import asyncpg
    from mars.rules import find_gaps, run_ais_gap

    async def go():
        c = await asyncpg.connect(URL)
        for t in ("json", "jsonb"):
            await c.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        tr = c.transaction()
        await tr.start()
        try:
            ids = await _scenario(c)
            start, end = T0 - timedelta(hours=2), T0 + timedelta(hours=12)
            rows = {r["vessel_id"]: r for r in await find_gaps(c, start, end, RULES)}
            nordic = rows[ids["NORDIC C"]]
            assert nordic["projection_couverte"] and not nordic["route_couverte"]      # retenu avant, sortie après
            assert all(rows[ids[n]]["route_couverte"] for n in ("EST 1", "EST 2", "EST 3"))
            out = await run_ais_gap(c, start, end, RULES)
            assert out["mode"] == "observation" and out["exclus_route_hors_zone"] == 1
            assert out["coupures_retenues"] == 3 and out["points_de_disparition_collectifs"] == 1
            assert out["coupures_declassees_disparition_collective"] == 3
            assert out["gravites_hypothetiques"] == {"faible": 3}
            # Horizon nul : ancien comportement (NORDIC C retenu)
            old = await run_ais_gap(c, start, end, {**RULES, "ais_gap": {**G, "route_horizon_min": 0}})
            assert old["exclus_route_hors_zone"] == 0 and old["coupures_retenues"] == 4
        finally:
            await tr.rollback()
            await c.close()
    asyncio.run(go())
