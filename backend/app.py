"""API de MARS C2.

Horloge simulée et trafic AIS rejoué (phase 2), analyses radar à la demande (phase 3) : recherche des passages,
orchestration du service d'inférence, moteur de fusion, alertes et suivi de progression diffusé en SSE.
"""
import asyncio
import json
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

import asyncpg
import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mars.config import load_rules
from mars.fusion.pipeline import fuse
from mars.geo import bbox_size_km
from mars.sar.catalog import get_token, search_passes

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://mars:mars@localhost:5432/mars")
INFERENCE_URL = os.environ.get("INFERENCE_URL", "http://host.docker.internal:8001")
TRAFFIC_WINDOW_MIN = 30   # un navire reste affiché 30 minutes simulées après son dernier message
TRAIL_MIN = 10            # longueur de la traînée, en minutes simulées
MAX_ZONE_KM = 50          # taille maximale d'une zone analysée
TASKS: set = set()


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


def aoi_wkt(b):
    return f"SRID=4326;POLYGON(({b[0]} {b[1]},{b[2]} {b[1]},{b[2]} {b[3]},{b[0]} {b[3]},{b[0]} {b[1]}))"


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


async def read_active_analyses(c) -> list:
    rows = await c.fetch(
        """SELECT id, status, progress, summary, error, completed_at FROM analyses
           WHERE status IN ('pending', 'running') OR completed_at > now() - interval '20 seconds'
           ORDER BY id""")
    return [clean(r) for r in rows]


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
    """Flux SSE, une fois par seconde réelle : horloge, trafic, et progression des analyses en cours."""
    async def events():
        while not await request.is_disconnected():
            async with app.state.pool.acquire() as c:
                clock = await read_clock(c)
                payload = {"clock": clock_json(clock), "traffic": await read_traffic(c, clock["now"]),
                           "analyses": await read_active_analyses(c)}
            yield f"event: traffic\ndata: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/ais/days")
async def ais_days():
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT day, messages, vessels FROM ais_days ORDER BY day")
    return [clean(r) for r in rows]


# Passages Sentinel 1

def parse_bbox(bbox: str) -> list[float]:
    try:
        b = [float(v) for v in bbox.split(",")]
        assert len(b) == 4 and b[0] < b[2] and b[1] < b[3]
        return b
    except Exception:
        raise HTTPException(422, "Emprise attendue : lon_min,lat_min,lon_max,lat_max")


@app.get("/api/passes")
async def passes(bbox: str, start: datetime | None = None, end: datetime | None = None):
    """Passages Sentinel 1 couvrant la zone, avec le taux de recouvrement et la disponibilité de l'AIS."""
    b = parse_bbox(bbox)
    async with app.state.pool.acquire() as c:
        if start is None or end is None:
            span = await c.fetchrow("SELECT min(day) AS d0, max(day) AS d1 FROM ais_days")
            if span["d0"] is None:
                raise HTTPException(422, "Aucune journée AIS chargée")
            start = start or pd.Timestamp(span["d0"], tz="UTC").to_pydatetime()
            end = end or (pd.Timestamp(span["d1"], tz="UTC") + pd.Timedelta(days=1)).to_pydatetime()

    token = await asyncio.to_thread(get_token, os.environ.get("SH_CLIENT_ID"), os.environ.get("SH_CLIENT_SECRET"))
    found = await asyncio.to_thread(search_passes, token, b, pd.Timestamp(start), pd.Timestamp(end))

    async with app.state.pool.acquire() as c:
        for p in found:
            await c.execute(
                "INSERT INTO sar_passes (product_name, platform, acquired_at, orbit_direction, footprint) "
                "VALUES ($1, $2, $3, $4, ST_GeomFromGeoJSON($5)::geography) "
                "ON CONFLICT (product_name) DO UPDATE SET footprint = EXCLUDED.footprint",
                p["product_name"], p["platform"], p["acquired_at"].to_pydatetime(), p["orbit_direction"],
                json.dumps(p["footprint"]) if p["footprint"] else None)
        rows = await c.fetch(
            """
            WITH env AS (SELECT ST_MakeEnvelope($2, $3, $4, $5, 4326) AS g)
            SELECT s.product_name, s.acquired_at, s.platform, s.orbit_direction,
                   CASE WHEN s.footprint IS NULL THEN NULL
                        ELSE round((ST_Area(ST_Intersection(s.footprint::geometry, env.g)::geography)
                                    / ST_Area(env.g::geography))::numeric, 3)::float8 END AS coverage,
                   EXISTS (SELECT 1 FROM ais_days d WHERE d.day = (s.acquired_at AT TIME ZONE 'UTC')::date) AS ais_available
            FROM sar_passes s, env
            WHERE s.product_name = ANY($1::text[])
            ORDER BY s.acquired_at
            """, [p["product_name"] for p in found], *b)
    return [clean(r) for r in rows]


# Analyses radar à la demande

class AnalysisRequest(BaseModel):
    bbox: list[float] = Field(min_length=4, max_length=4)
    product_name: str
    mode: Literal["fast", "full"] = "fast"


async def add_progress(c, analysis_id: int, item: dict):
    await c.execute("UPDATE analyses SET progress = progress || $2::jsonb WHERE id = $1", analysis_id, [item])


async def run_analysis(analysis_id: int, bbox: list[float], t0: datetime, product_name: str, mode: str):
    pool = app.state.pool
    rules = load_rules()
    started = asyncio.get_running_loop().time()
    try:
        async with pool.acquire() as c:
            await c.execute("UPDATE analyses SET status = 'running' WHERE id = $1", analysis_id)

        # 1. Service d'inférence : extrait radar et détections, avec progression ligne par ligne
        result = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=5)) as client:
                async with client.stream("POST", f"{INFERENCE_URL}/v1/analyze",
                                         json={"bbox": bbox, "acquired_at": t0.isoformat(), "mode": mode}) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        msg = json.loads(line)
                        if msg["type"] == "progress":
                            async with pool.acquire() as c:
                                await add_progress(c, analysis_id, msg)
                        elif msg["type"] == "error":
                            raise RuntimeError(f"Service d'inférence : {msg['message']}")
                        elif msg["type"] == "result":
                            result = msg
        except httpx.ConnectError:
            raise RuntimeError(f"Service d'inférence injoignable ({INFERENCE_URL}). "
                               "Lancer : uvicorn inference.app:app --port 8001")
        if result is None:
            raise RuntimeError("Le service d'inférence n'a pas renvoyé de résultat")

        # 2. Fusion avec l'AIS
        async with pool.acquire() as c:
            await add_progress(c, analysis_id, {"type": "progress", "step": "fusion", "state": "start"})
        t_fusion = asyncio.get_running_loop().time()
        f = rules["fusion"]
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT p.vessel_id, v.mmsi, v.name, v.length_m, p.ts, ST_Y(p.geom::geometry) AS lat, "
                "ST_X(p.geom::geometry) AS lon, p.sog_kn AS sog, p.cog_deg AS cog "
                "FROM positions p JOIN vessels v ON v.id = p.vessel_id "
                "WHERE ST_DWithin(p.geom, $1::geography, 5000) AND p.ts BETWEEN $2 AND $3",
                aoi_wkt(bbox), t0 - pd.Timedelta(minutes=f["ais_window_min"]),
                t0 + pd.Timedelta(minutes=f["ais_window_min"]))
        pos = pd.DataFrame([dict(r) for r in rows],
                           columns=["vessel_id", "mmsi", "name", "length_m", "ts", "lat", "lon", "sog", "cog"])
        if len(pos):
            pos["ts"] = pd.to_datetime(pos.ts, utc=True)
            for col in ["lat", "lon", "sog", "cog", "length_m"]:
                pos[col] = pd.to_numeric(pos[col], errors="coerce")
        det = pd.DataFrame(result["detections"],
                           columns=["lon", "lat", "objectness", "vessel_score", "fishing_score", "length_m", "contrast_vv_db"])
        det["contrast_vv_db"] = pd.to_numeric(det.contrast_vv_db, errors="coerce")
        det, alerts, n_ais = await asyncio.to_thread(fuse, det, pos, pd.Timestamp(t0), bbox, rules)

        # 3. Enregistrement des détections, des alertes et de leurs preuves
        async with pool.acquire() as c, c.transaction():
            det_ids = {}
            for i, d in det.iterrows():
                det_ids[i] = await c.fetchval(
                    "INSERT INTO detections (analysis_id, geom, objectness, vessel_score, fishing_score, length_m, "
                    "contrast_vv_db, mask_reason, matched_vessel_id, match_cost) "
                    "VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography, $4, $5, $6, $7, $8, $9, $10, $11) "
                    "RETURNING id",
                    analysis_id, float(d.lon), float(d.lat), float(d.objectness), float(d.vessel_score),
                    float(d.fishing_score), float(d.length_m), finite(float(d.contrast_vv_db)),
                    None if pd.isna(d.mask_reason) else d.mask_reason,
                    None if pd.isna(d.matched_vessel_id) else int(d.matched_vessel_id),
                    finite(float(d.match_cost)) if pd.notna(d.match_cost) else None)
            for a in alerts:
                d = det.loc[a["det_index"]]
                details = {"analysis_id": analysis_id, "pass": product_name, **a["details"]}
                alert_id = await c.fetchval(
                    "INSERT INTO alerts (type, severity, event_time, geom, details, rule_version) "
                    "VALUES ('DARK_SHIP', $1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography, $5, $6) RETURNING id",
                    a["severity"], t0, float(d.lon), float(d.lat), details, rules["version"])
                evidence = [("analysis", analysis_id), ("detection", det_ids[a["det_index"]])]
                evidence += [("vessel", v) for v in a["vessel_ids"]]
                await c.executemany("INSERT INTO alert_evidence VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                                    [(alert_id, et, eid) for et, eid in evidence])

            fusion_s = round(asyncio.get_running_loop().time() - t_fusion, 2)
            timings = {**result["timings"], "fusion_s": fusion_s,
                       "total_s": round(asyncio.get_running_loop().time() - started, 2)}
            summary = {"detections": len(det), "retenues": int(det.mask_reason.isna().sum()) if len(det) else 0,
                       "appariees": int(det.matched_vessel_id.notna().sum()) if len(det) else 0,
                       "alertes": len(alerts), "navires_ais": n_ais, "tuiles": result["n_tiles"],
                       "accelerateur": result["device"], "precision_mixte": result["amp"]}
            await add_progress(c, analysis_id, {"type": "progress", "step": "fusion", "state": "done",
                                                "seconds": fusion_s,
                                                "detail": f"{summary['appariees']} appariées, {len(alerts)} alerte(s)"})
            await c.execute("UPDATE analyses SET status = 'done', completed_at = now(), timings = $2, summary = $3 "
                            "WHERE id = $1", analysis_id, timings, summary)
    except Exception as e:
        async with pool.acquire() as c:
            await c.execute("UPDATE analyses SET status = 'failed', completed_at = now(), error = $2 WHERE id = $1",
                            analysis_id, str(e)[:1000])


@app.post("/api/analyses", status_code=202)
async def create_analysis(req: AnalysisRequest):
    b = req.bbox
    if not (b[0] < b[2] and b[1] < b[3]):
        raise HTTPException(422, "Emprise invalide")
    w, h = bbox_size_km(b)
    if w > MAX_ZONE_KM or h > MAX_ZONE_KM:
        raise HTTPException(422, f"Zone trop grande ({w:.0f} x {h:.0f} km), maximum {MAX_ZONE_KM} km de côté")
    rules = load_rules()
    async with app.state.pool.acquire() as c:
        p = await c.fetchrow("SELECT id, acquired_at FROM sar_passes WHERE product_name = $1", req.product_name)
        if p is None:
            raise HTTPException(404, "Passage inconnu : le rechercher d'abord via /api/passes")
        ais_ok = await c.fetchval("SELECT EXISTS (SELECT 1 FROM ais_days WHERE day = ($1::timestamptz AT TIME ZONE 'UTC')::date)",
                                  p["acquired_at"])
        if not ais_ok:
            raise HTTPException(422, "Pas de données AIS chargées pour la date de ce passage")
        analysis_id = await c.fetchval(
            "INSERT INTO analyses (pass_id, aoi, mode, status, model_version) "
            "VALUES ($1, $2::geography, $3, 'pending', $4) RETURNING id",
            p["id"], aoi_wkt(b), req.mode, rules["model"]["version"])
    task = asyncio.create_task(run_analysis(analysis_id, b, p["acquired_at"], req.product_name, req.mode))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return {"id": analysis_id, "status": "pending"}


@app.get("/api/analyses/{analysis_id}")
async def get_analysis(analysis_id: int):
    async with app.state.pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT a.id, a.mode, a.status, a.progress, a.timings, a.summary, a.error, a.model_version, "
            "a.requested_at, a.completed_at, p.product_name, p.acquired_at "
            "FROM analyses a JOIN sar_passes p ON p.id = a.pass_id WHERE a.id = $1", analysis_id)
    if r is None:
        raise HTTPException(404)
    return clean(r)


@app.get("/api/inference/health")
async def inference_health():
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            return (await client.get(f"{INFERENCE_URL}/v1/health")).json()
    except Exception:
        return {"status": "injoignable", "url": INFERENCE_URL}


# Consultation

@app.get("/api/health")
async def health():
    async with app.state.pool.acquire() as c:
        await c.fetchval("SELECT 1")
    return {"status": "ok"}


@app.get("/api/analyses")
async def analyses():
    q = """
    SELECT a.id, a.mode, a.status, a.model_version, a.requested_at, a.completed_at, a.summary, a.timings,
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
