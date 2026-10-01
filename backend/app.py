"""API de MARS C2 : horloge simulée, trafic AIS rejoué, analyses radar, détections et alertes.

Le rejeu repose sur une horloge stockée en base (table sim_clock, fonction sim_now()) : l'API, l'interface et
les règles raisonnent toutes sur le même « maintenant » simulé. Le trafic est diffusé en continu par SSE.
"""
import asyncio
import json
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://mars:mars@localhost:5432/mars")
TRAFFIC_WINDOW_MIN = 30   # un navire reste affiché 30 minutes simulées après son dernier message
TRAIL_MIN = 10            # longueur de la traînée, en minutes simulées


async def _init(conn):
    for t in ("json", "jsonb"):
        await conn.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@asynccontextmanager
async def lifespan(app):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, init=_init, min_size=1, max_size=10)
    yield
    await app.state.pool.close()


app = FastAPI(title="MARS C2", lifespan=lifespan)


def feature(geometry, properties):
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def collection(features):
    return {"type": "FeatureCollection", "features": features}


def finite(v):
    """Les valeurs NaN ne sont pas du JSON valide : elles deviennent null."""
    return None if isinstance(v, float) and math.isnan(v) else v


def clean(record, drop=("geometry",)):
    out = {}
    for k, v in dict(record).items():
        if k in drop:
            continue
        out[k] = v.isoformat() if hasattr(v, "isoformat") else finite(v)
    return out


# Horloge simulée

async def read_clock(c) -> dict:
    r = await c.fetchrow("SELECT sim_now() AS now, speed, paused FROM sim_clock WHERE id = 1")
    return {"now": r["now"], "speed": r["speed"], "paused": r["paused"]}


def clock_json(clock: dict) -> dict:
    return {"now": clock["now"].isoformat(), "speed": clock["speed"], "paused": clock["paused"]}


class ClockCommand(BaseModel):
    action: Literal["play", "pause", "speed", "seek"]
    speed: float | None = None
    time: datetime | None = None


@app.get("/api/clock")
async def get_clock():
    async with app.state.pool.acquire() as c:
        return clock_json(await read_clock(c))


@app.post("/api/clock")
async def set_clock(cmd: ClockCommand):
    # On fige d'abord l'instant simulé courant, puis on applique la commande à partir de cet ancrage
    rebase = "sim_anchor = sim_now(), real_anchor = clock_timestamp()"
    async with app.state.pool.acquire() as c:
        if cmd.action == "play":
            await c.execute(f"UPDATE sim_clock SET {rebase}, paused = false WHERE id = 1")
        elif cmd.action == "pause":
            await c.execute(f"UPDATE sim_clock SET {rebase}, paused = true WHERE id = 1")
        elif cmd.action == "speed":
            if cmd.speed is None or not 0 < cmd.speed <= 3600:
                raise HTTPException(422, "Vitesse attendue entre 0 et 3600")
            await c.execute(f"UPDATE sim_clock SET {rebase}, speed = $1 WHERE id = 1", cmd.speed)
        elif cmd.action == "seek":
            if cmd.time is None:
                raise HTTPException(422, "Instant attendu")
            await c.execute("UPDATE sim_clock SET sim_anchor = $1, real_anchor = clock_timestamp() WHERE id = 1",
                            cmd.time)
        return clock_json(await read_clock(c))


# Trafic AIS rejoué

async def read_traffic(c, now: datetime) -> dict:
    rows = await c.fetch(
        """
        SELECT DISTINCT ON (p.vessel_id)
               p.vessel_id, v.mmsi, v.name, v.ship_type, v.length_m, p.sog_kn, p.cog_deg,
               extract(epoch FROM $1::timestamptz - p.ts)::float8 AS age_s,
               ST_X(p.geom::geometry) AS lon, ST_Y(p.geom::geometry) AS lat
        FROM positions p JOIN vessels v ON v.id = p.vessel_id
        WHERE p.ts > $1::timestamptz - make_interval(mins => $2::int) AND p.ts <= $1::timestamptz
        ORDER BY p.vessel_id, p.ts DESC
        """, now, TRAFFIC_WINDOW_MIN)
    return collection([
        feature({"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                {k: finite(r[k]) for k in ("vessel_id", "mmsi", "name", "ship_type", "length_m", "sog_kn", "cog_deg", "age_s")})
        for r in rows
    ])


@app.get("/api/traffic")
async def traffic():
    async with app.state.pool.acquire() as c:
        clock = await read_clock(c)
        return {"clock": clock_json(clock), "traffic": await read_traffic(c, clock["now"])}


@app.get("/api/traffic/trails")
async def trails(minutes: int = TRAIL_MIN):
    async with app.state.pool.acquire() as c:
        clock = await read_clock(c)
        rows = await c.fetch(
            """
            SELECT p.vessel_id, ST_AsGeoJSON(ST_MakeLine(p.geom::geometry ORDER BY p.ts))::json AS geometry
            FROM positions p
            WHERE p.ts > $1::timestamptz - make_interval(mins => $2::int) AND p.ts <= $1::timestamptz
            GROUP BY p.vessel_id
            HAVING count(*) >= 2
            """, clock["now"], minutes)
    return collection([feature(r["geometry"], {"vessel_id": r["vessel_id"]}) for r in rows])


@app.get("/api/vessels/{vessel_id}/track")
async def track(vessel_id: int, start: datetime, end: datetime):
    """Trajectoire d'un navire sur une fenêtre de temps."""
    async with app.state.pool.acquire() as c:
        geometry = await c.fetchval(
            "SELECT ST_AsGeoJSON(ST_MakeLine(geom::geometry ORDER BY ts))::json FROM positions "
            "WHERE vessel_id = $1 AND ts BETWEEN $2 AND $3", vessel_id, start, end)
    if geometry is None:
        raise HTTPException(404, "Aucune position sur cette fenêtre")
    return feature(geometry, {"vessel_id": vessel_id, "start": start.isoformat(), "end": end.isoformat()})


@app.get("/api/stream")
async def stream(request: Request):
    """Flux SSE : horloge et trafic, une fois par seconde réelle."""
    async def events():
        while not await request.is_disconnected():
            async with app.state.pool.acquire() as c:
                clock = await read_clock(c)
                payload = {"clock": clock_json(clock), "traffic": await read_traffic(c, clock["now"])}
            yield f"event: traffic\ndata: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/ais/days")
async def ais_days():
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT day, messages, vessels FROM ais_days ORDER BY day")
    return [clean(r) for r in rows]


# Analyses radar

@app.get("/api/health")
async def health():
    async with app.state.pool.acquire() as c:
        await c.fetchval("SELECT 1")
    return {"status": "ok"}


@app.get("/api/analyses")
async def analyses():
    q = """
    SELECT a.id, a.mode, a.status, a.model_version, a.requested_at, a.completed_at,
           p.product_name, p.acquired_at, ST_AsGeoJSON(a.aoi)::json AS geometry,
           (SELECT count(*) FROM detections d WHERE d.analysis_id = a.id) AS detections
    FROM analyses a JOIN sar_passes p ON p.id = a.pass_id
    ORDER BY a.id DESC
    """
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(q)
    return collection([feature(r["geometry"], clean(r)) for r in rows])


@app.get("/api/analyses/{analysis_id}/detections")
async def detections(analysis_id: int):
    q = """
    SELECT d.id, d.objectness, d.vessel_score, d.fishing_score, d.length_m, d.contrast_vv_db, d.mask_reason,
           d.match_cost, v.mmsi AS matched_mmsi, v.name AS matched_name,
           ST_AsGeoJSON(d.geom)::json AS geometry
    FROM detections d LEFT JOIN vessels v ON v.id = d.matched_vessel_id
    WHERE d.analysis_id = $1
    """
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(q, analysis_id)
    return collection([feature(r["geometry"], clean(r)) for r in rows])


@app.get("/api/alerts")
async def alerts(analysis_id: int | None = None):
    q = """
    SELECT al.id, al.type, al.severity, al.status, al.event_time, al.detected_at, al.details, al.rule_version,
           ST_AsGeoJSON(al.geom)::json AS geometry
    FROM alerts al
    WHERE $1::bigint IS NULL OR EXISTS (
        SELECT 1 FROM alert_evidence e
        WHERE e.alert_id = al.id AND e.evidence_type = 'analysis' AND e.evidence_id = $1::bigint
    )
    ORDER BY al.detected_at DESC
    """
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(q, analysis_id)
    return collection([feature(r["geometry"], clean(r)) for r in rows])