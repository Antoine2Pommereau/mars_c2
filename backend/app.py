"""API de MARS C2 (phase 1) : analyses, positions AIS à l'instant du passage, détections et alertes, en GeoJSON."""
import json
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://mars:mars@localhost:5432/mars")


async def _init(conn):
    for t in ("json", "jsonb"):
        await conn.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@asynccontextmanager
async def lifespan(app):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, init=_init, min_size=1, max_size=5)
    yield
    await app.state.pool.close()


app = FastAPI(title="MARS C2", lifespan=lifespan)


def feature(geometry, properties):
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def collection(features):
    return {"type": "FeatureCollection", "features": features}


def clean(record, drop=("geometry",)):
    out = {}
    for k, v in dict(record).items():
        if k in drop:
            continue
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


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


@app.get("/api/analyses/{analysis_id}/ais")
async def ais_at_pass(analysis_id: int):
    """Position AIS la plus proche dans le temps de l'instant du passage, pour chaque navire de la zone."""
    q = """
    WITH a AS (
        SELECT an.aoi, p.acquired_at AS t0 FROM analyses an JOIN sar_passes p ON p.id = an.pass_id WHERE an.id = $1
    )
    SELECT DISTINCT ON (pos.vessel_id)
           pos.vessel_id, v.mmsi, v.name, v.ship_type, v.length_m, pos.ts, pos.sog_kn, pos.cog_deg,
           ST_AsGeoJSON(pos.geom)::json AS geometry
    FROM positions pos JOIN vessels v ON v.id = pos.vessel_id, a
    WHERE ST_DWithin(pos.geom, a.aoi, 5000)
      AND pos.ts BETWEEN a.t0 - interval '15 minutes' AND a.t0 + interval '15 minutes'
    ORDER BY pos.vessel_id, abs(extract(epoch FROM pos.ts - a.t0))
    """
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(q, analysis_id)
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
    if rows is None:
        raise HTTPException(404)
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
