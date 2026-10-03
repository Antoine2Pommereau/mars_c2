"""API de MARS C2.

Horloge simulée et trafic AIS rejoué (phase 2), analyses radar à la demande (phase 3) : recherche des passages,
orchestration du service d'inférence, moteur de fusion, alertes et suivi de progression diffusé en SSE.
"""
import asyncio
import json
import logging
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

import asyncpg
import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field, field_validator

from mars.config import load_env, load_rules
from mars.fusion.pipeline import fuse
from mars.geo import bbox_size_km
from mars.sar.catalog import get_token, search_passes
from rules import (build_reception_cells, build_stationary_zones, find_gaps, run_ais_gap,
                   run_infrastructure, run_infrastructure_selftest, run_rendezvous, run_rendezvous_selftest)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mars.api")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://mars:mars@localhost:5432/mars")
INFERENCE_URL = os.environ.get("INFERENCE_URL", "http://host.docker.internal:8001")
TRAFFIC_WINDOW_MIN = 30   # un navire reste affiché 30 minutes simulées après son dernier message
TRAIL_MIN = 10            # longueur de la traînée, en minutes simulées
MAX_ZONE_KM = 50          # taille maximale d'une zone analysée
MAX_ANALYSES = 2          # nombre maximal d'analyses menées de front
MAX_STREAMS = 32          # nombre maximal de flux SSE simultanés
CHIP_MIN_M = 50           # taille minimale d'une vignette radar
CHIP_MAX_M = 5000         # taille maximale d'une vignette radar
TASKS: set = set()
_ANALYSIS_SEM = asyncio.Semaphore(MAX_ANALYSES)
_STREAM_SEM = asyncio.Semaphore(MAX_STREAMS)

# Jeton OAuth Sentinel Hub mis en cache jusqu'à son expiration (marge de sécurité de 60 secondes).
# Le jeton CDSE vit environ 600 secondes ; on borne par prudence pour ne pas le réutiliser trop longtemps.
_TOKEN_TTL_S = 540
_token_cache: dict = {"value": None, "expires": 0.0}


def is_finite(v) -> bool:
    """Vrai si v est un flottant fini ; les NaN et les infinis ne sont pas du JSON valide ni acceptés en base."""
    return not (isinstance(v, float) and not math.isfinite(v))


def fetch_token() -> str:
    """Rend un jeton OAuth Sentinel Hub, depuis le cache tant qu'il est valide. Lève ValueError si les
    identifiants manquent et remonte l'erreur réseau en cas d'échec de l'échange."""
    now = asyncio.get_event_loop().time()
    if _token_cache["value"] is not None and now < _token_cache["expires"]:
        return _token_cache["value"]
    token = get_token(os.environ.get("SH_CLIENT_ID"), os.environ.get("SH_CLIENT_SECRET"))
    _token_cache["value"] = token
    _token_cache["expires"] = now + _TOKEN_TTL_S
    return token


async def _init(conn):
    for t in ("json", "jsonb"):
        await conn.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@asynccontextmanager
async def lifespan(app):
    load_env()
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, init=_init, min_size=1, max_size=10)
    # Reprise au démarrage : les analyses restées en cours ou en attente lors d'un arrêt sont orphelines
    # (aucune tâche ne les mène plus), on les repasse en échec pour que l'opérateur puisse les relancer.
    async with app.state.pool.acquire() as c:
        status = await c.execute(
            "UPDATE analyses SET status = 'failed', completed_at = now(), "
            "error = coalesce(error, 'Analyse interrompue par un redémarrage du service') "
            "WHERE status IN ('pending', 'running')")
        orphans = int(status.split()[-1]) if status.startswith("UPDATE") else 0
        if orphans:
            log.info("Reprise au démarrage : %s analyse(s) orpheline(s) repassée(s) en échec", orphans)
    yield
    await app.state.pool.close()


app = FastAPI(title="MARS C2", lifespan=lifespan)


def feature(geometry, properties):
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def collection(features):
    return {"type": "FeatureCollection", "features": features}


def finite(v):
    """Les valeurs NaN et infinies ne sont pas du JSON valide ni acceptées en base : elles deviennent null."""
    return None if isinstance(v, float) and not math.isfinite(v) else v


def sanitize(obj):
    """Assainit récursivement une structure avant sérialisation JSON : les flottants non finis deviennent null,
    y compris au sein des JSON imbriqués du flux SSE (summary, details, progress)."""
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    return obj


def clean(record, drop=("geometry",)):
    out = {}
    for k, v in dict(record).items():
        if k in drop:
            continue
        out[k] = v.isoformat() if hasattr(v, "isoformat") else sanitize(v)
    return out


def valid_lonlat(lon: float, lat: float) -> bool:
    return is_finite(lon) and is_finite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90


def check_bbox(b: list[float]) -> list[float]:
    """Vérifie une emprise [lon_min, lat_min, lon_max, lat_max] : bornes finies, dans les plages lon/lat,
    et ordonnées. Lève HTTPException(422) sinon. Rempart contre des valeurs nan ou inf jusqu'au WKT PostGIS."""
    if len(b) != 4 or not all(is_finite(x) for x in b):
        raise HTTPException(422, "Emprise attendue : quatre nombres finis lon_min,lat_min,lon_max,lat_max")
    if not (-180 <= b[0] <= 180 and -180 <= b[2] <= 180 and -90 <= b[1] <= 90 and -90 <= b[3] <= 90):
        raise HTTPException(422, "Emprise hors des plages valides (longitude 180, latitude 90)")
    if not (b[0] < b[2] and b[1] < b[3]):
        raise HTTPException(422, "Emprise invalide : lon_min < lon_max et lat_min < lat_max attendus")
    return b


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


async def read_live_alerts(c, now: datetime) -> dict:
    """Alertes comportementales déjà franchies à l'instant simulé (12 dernières heures simulées)."""
    rows = await c.fetch(
        """SELECT al.id, al.type, al.severity, al.status, al.event_time, al.details, al.rule_version,
                  ST_AsGeoJSON(al.geom)::json AS geometry
           FROM alerts al
           WHERE al.type IN ('RENDEZVOUS', 'AIS_GAP')
             AND al.event_time <= $1::timestamptz AND al.event_time > $1::timestamptz - interval '12 hours'
           ORDER BY al.event_time DESC""", now)
    return collection([feature(r["geometry"], clean(r)) for r in rows])


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
    if _STREAM_SEM.locked():
        raise HTTPException(503, "Trop de flux ouverts simultanément")

    async def events():
        # Connexion dédiée hors pool : un flux garde sa connexion une seconde par seconde, en acquérir une
        # nouvelle à chaque tour affamait les analyses. On la libère proprement à la fin du flux.
        await _STREAM_SEM.acquire()
        conn = None
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            await _init(conn)
            while not await request.is_disconnected():
                try:
                    clock = await read_clock(conn)
                    payload = {"clock": clock_json(clock), "traffic": await read_traffic(conn, clock["now"]),
                               "analyses": await read_active_analyses(conn),
                               "live_alerts": await read_live_alerts(conn, clock["now"])}
                    yield f"event: traffic\ndata: {json.dumps(sanitize(payload))}\n\n"
                except Exception:
                    # Une erreur ponctuelle (base momentanément indisponible) ne doit pas couper le flux :
                    # on la journalise et on réessaie au tour suivant.
                    log.exception("Erreur dans le flux SSE, poursuite au tour suivant")
                await asyncio.sleep(1)
        finally:
            if conn is not None:
                await conn.close()
            _STREAM_SEM.release()

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
    except ValueError:
        raise HTTPException(422, "Emprise attendue : lon_min,lat_min,lon_max,lat_max")
    return check_bbox(b)


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

    try:
        token = await asyncio.to_thread(fetch_token)
    except ValueError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        log.exception("Échange du jeton Sentinel Hub en échec")
        raise HTTPException(503, f"Jeton Sentinel Hub indisponible : {e}")
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

    @field_validator("bbox")
    @classmethod
    def _bbox_valide(cls, b: list[float]) -> list[float]:
        # Rempart précoce : des bornes nan ou inf traverseraient sinon jusqu'au WKT PostGIS.
        if not all(isinstance(x, float) and math.isfinite(x) for x in b):
            raise ValueError("Emprise : quatre nombres finis attendus")
        if not (-180 <= b[0] <= 180 and -180 <= b[2] <= 180 and -90 <= b[1] <= 90 and -90 <= b[3] <= 90):
            raise ValueError("Emprise hors des plages valides (longitude 180, latitude 90)")
        if not (b[0] < b[2] and b[1] < b[3]):
            raise ValueError("Emprise invalide : lon_min < lon_max et lat_min < lat_max attendus")
        return b


async def add_progress(c, analysis_id: int, item: dict):
    await c.execute("UPDATE analyses SET progress = progress || $2::jsonb WHERE id = $1",
                    analysis_id, [sanitize(item)])


async def run_analysis(analysis_id: int, bbox: list[float], t0: datetime, product_name: str, mode: str, rules: dict):
    # Les règles sont chargées une seule fois par l'appelant et propagées : l'analyse et ses alertes
    # portent toutes la même version, pas de risque d'incohérence si le fichier change en cours de route.
    pool = app.state.pool
    started = asyncio.get_running_loop().time()
    # Le sémaphore borne le nombre d'analyses menées de front : au delà, la tâche attend ici,
    # l'analyse reste « pending » et ne mobilise ni le service d'inférence ni le pool de connexions.
    await _ANALYSIS_SEM.acquire()
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
                "WHERE ST_DWithin(p.geom, ST_MakeEnvelope($1, $2, $3, $4, 4326)::geography, 5000) "
                "AND p.ts BETWEEN $5 AND $6",
                bbox[0], bbox[1], bbox[2], bbox[3],
                t0 - pd.Timedelta(minutes=f["ais_window_min"]),
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
        det["on_land"] = False
        if len(det):
            async with pool.acquire() as c:
                land_idx = await c.fetch(
                    "SELECT i FROM unnest($1::float8[], $2::float8[]) WITH ORDINALITY AS t(lon, lat, i) "
                    "WHERE EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, "
                    "ST_SetSRID(ST_MakePoint(t.lon, t.lat), 4326)::geography, $3::float8))",
                    det.lon.astype(float).tolist(), det.lat.astype(float).tolist(), float(rules["masks"]["land_buffer_m"]))
            det.loc[[r["i"] - 1 for r in land_idx], "on_land"] = True
        async with pool.acquire() as c:
            orbit = await c.fetchval("SELECT lower(orbit_direction) FROM sar_passes WHERE product_name = $1", product_name)
        heading = rules["fusion"]["flight_heading_deg"].get(orbit) if orbit else None

        # Persistance : échos fixes connus, et échos sans AIS observés à une autre date sur la zone
        async with pool.acquire() as c:
            fixed_rows = await c.fetch(
                """WITH env AS (SELECT ST_MakeEnvelope($3, $4, $5, $6, 4326)::geography AS g)
                   SELECT 'registre' AS source, f.id AS ref, ST_X(f.geom::geometry) AS lon, ST_Y(f.geom::geometry) AS lat
                   FROM fixed_echoes f, env WHERE ST_DWithin(f.geom, env.g, 1000)
                   UNION ALL
                   SELECT 'detection', d.id, ST_X(d.geom::geometry), ST_Y(d.geom::geometry)
                   FROM detections d JOIN analyses a ON a.id = d.analysis_id JOIN sar_passes p ON p.id = a.pass_id, env
                   WHERE d.matched_vessel_id IS NULL AND coalesce(d.mask_reason, '') <> 'terre'
                     AND abs(extract(epoch FROM p.acquired_at - $1::timestamptz)) >= $2::float8 * 86400
                     AND ST_DWithin(d.geom, env.g, 1000)""",
                t0, float(rules["persistence"]["min_days_apart"]),
                bbox[0], bbox[1], bbox[2], bbox[3])
        fixed_points = pd.DataFrame([dict(r) for r in fixed_rows], columns=["source", "ref", "lon", "lat"])
        det, alerts, n_ais, extras = await asyncio.to_thread(fuse, det, pos, pd.Timestamp(t0), bbox, rules, heading,
                                                             fixed_points)

        # Positions AIS non confirmées : on écarte les navires près de la terre (ports, quais) et hors de l'emprise
        # du passage satellite, où l'absence d'écho ne prouve rien
        unconfirmed = []
        if extras["unconfirmed"]:
            u = extras["unconfirmed"]
            async with pool.acquire() as c:
                ok = await c.fetch(
                    "SELECT i FROM unnest($1::float8[], $2::float8[]) WITH ORDINALITY AS t(lon, lat, i) "
                    "WHERE NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, "
                    "      ST_SetSRID(ST_MakePoint(t.lon, t.lat), 4326)::geography, $3::float8)) "
                    "  AND EXISTS (SELECT 1 FROM sar_passes s WHERE s.product_name = $4 AND (s.footprint IS NULL OR "
                    "      ST_Covers(s.footprint, ST_SetSRID(ST_MakePoint(t.lon, t.lat), 4326)::geography)))",
                    [x["lon"] for x in u], [x["lat"] for x in u], float(rules["masks"]["land_buffer_m"]) * 2,
                    product_name)
            unconfirmed = [u[r["i"] - 1] for r in ok]

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
                await c.executemany("INSERT INTO alert_evidence (alert_id, evidence_type, evidence_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                                    [(alert_id, et, eid) for et, eid in evidence])

            # Registre des échos fixes, et reclassement des alertes « navire sombre » levées sur ces échos à d'autres dates
            n_fixed = 0
            for hit in extras["fixed_hits"]:
                d = det.loc[hit["det_index"]]
                prior = [r["ref"] for r in hit["refs"] if r["source"] == "detection"]
                registry = [r["ref"] for r in hit["refs"] if r["source"] == "registre"]
                if registry:
                    await c.execute("UPDATE fixed_echoes SET observations = observations + 1, "
                                    "last_seen = greatest(last_seen, $2::timestamptz), first_seen = least(first_seen, $2::timestamptz), "
                                    "detection_ids = detection_ids || $3::bigint[] WHERE id = $1",
                                    registry[0], t0, [det_ids[hit["det_index"]]])
                else:
                    first = await c.fetchval(
                        "SELECT min(p.acquired_at) FROM detections d JOIN analyses a ON a.id = d.analysis_id "
                        "JOIN sar_passes p ON p.id = a.pass_id WHERE d.id = ANY($1::bigint[])", prior)
                    await c.execute(
                        "INSERT INTO fixed_echoes (geom, first_seen, last_seen, observations, detection_ids) "
                        "VALUES (ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography, "
                        "least($3::timestamptz, $4::timestamptz), greatest($3::timestamptz, $4::timestamptz), $5::int, $6::bigint[])",
                        float(d.lon), float(d.lat), first or t0, t0, 1 + len(prior), prior + [det_ids[hit["det_index"]]])
                if prior:
                    await c.execute("UPDATE detections SET mask_reason = 'echo fixe' "
                                    "WHERE id = ANY($1::bigint[]) AND mask_reason IS NULL", prior)
                    await c.execute(
                        "UPDATE alerts SET status = 'classee', details = details || jsonb_build_object("
                        "'reclassement', 'Écho revu au même endroit le ' || to_char($2::timestamptz, 'DD/MM/YYYY') || "
                        "' sans AIS : écho fixe (éolienne, plateforme, bouée), pas un navire') "
                        "WHERE type = 'DARK_SHIP' AND id IN (SELECT alert_id FROM alert_evidence "
                        "WHERE evidence_type = 'detection' AND evidence_id = ANY($1::bigint[]))", prior, t0)
                n_fixed += 1

            for x in unconfirmed:
                big = x["length_m"] >= rules["unconfirmed"]["high_length_m"]
                details = {"analysis_id": analysis_id, "pass": product_name,
                           "navire": {k: x[k] for k in ("vessel_id", "mmsi", "name", "length_m", "sog_kn")},
                           "methode_position": x["methode_position"],
                           "echo_le_plus_proche_m": x["echo_le_plus_proche_m"],
                           "tolerance_le_long_m": x["tolerance_le_long_m"],
                           "tolerance_en_travers_m": x["tolerance_en_travers_m"],
                           "motif": "Grand navire déclaré par l'AIS dans la zone analysée, sans aucun écho radar "
                                    "compatible à l'instant du passage : position possiblement falsifiée",
                           "contexte": ["Le détecteur manque environ un navire sur dix : indice à recouper, pas une preuve"],
                           "parametres": dict(rules["unconfirmed"])}
                alert_id = await c.fetchval(
                    "INSERT INTO alerts (type, severity, event_time, geom, details, rule_version) "
                    "VALUES ('AIS_UNCONFIRMED', $1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography, $5, $6) "
                    "RETURNING id",
                    "moyenne" if big else "faible", t0, x["lon"], x["lat"], details, rules["version"])
                await c.executemany("INSERT INTO alert_evidence (alert_id, evidence_type, evidence_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                                    [(alert_id, "analysis", analysis_id), (alert_id, "vessel", x["vessel_id"])])

            fusion_s = round(asyncio.get_running_loop().time() - t_fusion, 2)
            timings = {**result["timings"], "fusion_s": fusion_s,
                       "total_s": round(asyncio.get_running_loop().time() - started, 2)}
            summary = {"detections": len(det), "retenues": int(det.mask_reason.isna().sum()) if len(det) else 0,
                       "appariees": int(det.matched_vessel_id.notna().sum()) if len(det) else 0,
                       "alertes": len(alerts), "positions_non_confirmees": len(unconfirmed), "echos_fixes": n_fixed,
                       "decalages": extras["offsets"], "direction_de_vol_deg": heading,
                       "navires_ais": n_ais, "tuiles": result["n_tiles"],
                       "accelerateur": result["device"], "precision_mixte": result["amp"]}
            await add_progress(c, analysis_id, {"type": "progress", "step": "fusion", "state": "done",
                                                "seconds": fusion_s,
                                                "detail": f"{summary['appariees']} appariées, {len(alerts)} alerte(s)"})
            await c.execute("UPDATE analyses SET status = 'done', completed_at = now(), timings = $2, summary = $3 "
                            "WHERE id = $1", analysis_id, sanitize(timings), sanitize(summary))
    except Exception as e:
        # On journalise la pile complète avant d'écrire l'échec : sans cela la cause restait invisible,
        # seule une chaîne tronquée atteignait la base.
        log.exception("Analyse %s en échec", analysis_id)
        async with pool.acquire() as c:
            await c.execute("UPDATE analyses SET status = 'failed', completed_at = now(), error = $2 WHERE id = $1",
                            analysis_id, str(e)[:1000])
    finally:
        _ANALYSIS_SEM.release()


@app.post("/api/analyses", status_code=202)
async def create_analysis(req: AnalysisRequest):
    b = check_bbox(req.bbox)
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
        # Déduplication : si une analyse sur le même passage et la même emprise est déjà en cours
        # (à quelques mètres près), on renvoie celle ci au lieu d'en lancer une seconde en double.
        existing = await c.fetchval(
            "SELECT id FROM analyses WHERE pass_id = $1 AND status IN ('pending', 'running') "
            "AND ST_Equals(ST_SnapToGrid(aoi::geometry, 0.00001), "
            "ST_SnapToGrid(ST_MakeEnvelope($2, $3, $4, $5, 4326), 0.00001)) "
            "ORDER BY id DESC LIMIT 1",
            p["id"], b[0], b[1], b[2], b[3])
        if existing is not None:
            return {"id": existing, "status": "pending", "deja_en_cours": True}
        analysis_id = await c.fetchval(
            "INSERT INTO analyses (pass_id, aoi, mode, status, model_version) "
            "VALUES ($1, ST_MakeEnvelope($2, $3, $4, $5, 4326)::geography, $6, 'pending', $7) RETURNING id",
            p["id"], b[0], b[1], b[2], b[3], req.mode, rules["model"]["version"])
    task = asyncio.create_task(run_analysis(analysis_id, b, p["acquired_at"], req.product_name, req.mode, rules))
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


# Masques et règles comportementales

class RuleRun(BaseModel):
    day: str | None = None   # AAAA-MM-JJ ; toutes les journées chargées si absent


def parse_day(day: str):
    """Lit une date AAAA-MM-JJ. Lève HTTPException(422) sur un format invalide plutôt que de laisser
    remonter une exception non maîtrisée."""
    try:
        return datetime.fromisoformat(day).date()
    except (ValueError, TypeError):
        raise HTTPException(422, "Date attendue au format AAAA-MM-JJ")


@app.post("/api/masks/stationary")
async def rebuild_stationary_zones():
    async with app.state.pool.acquire() as c:
        span = await c.fetchrow("SELECT min(day) AS d0, max(day) AS d1 FROM ais_days")
        if span["d0"] is None:
            raise HTTPException(422, "Aucune journée AIS chargée")
        n = await build_stationary_zones(c, span["d0"], span["d1"], load_rules())
    return {"zones": n, "du": span["d0"].isoformat(), "au": span["d1"].isoformat()}


@app.get("/api/masks/stationary")
async def stationary_zones():
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT id, vessels, slow_positions, ST_AsGeoJSON(geom)::json AS geometry FROM stationary_zones")
    return collection([feature(r["geometry"], clean(r)) for r in rows])


@app.post("/api/masks/reception")
async def rebuild_reception_cells():
    async with app.state.pool.acquire() as c:
        return await build_reception_cells(c, load_rules())


@app.get("/api/masks/reception")
async def reception_cells():
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT messages, vessels, hours, coverage, ST_AsGeoJSON(geom)::json AS geometry FROM reception_cells")
    return collection([feature(r["geometry"], clean(r)) for r in rows])


@app.get("/api/regions")
async def regions():
    """Régions de couverture et état de provisionnement de leurs couches (voir docs/spec_regions.md)."""
    async with app.state.pool.acquire() as c:
        regs = await c.fetch(
            "SELECT id, name, origin, active, "
            "ST_XMin(b) AS lon_min, ST_YMin(b) AS lat_min, ST_XMax(b) AS lon_max, ST_YMax(b) AS lat_max "
            "FROM (SELECT id, name, origin, active, ST_Envelope(geom::geometry) AS b FROM regions) q ORDER BY id")
        layers = await c.fetch(
            "SELECT region_id, layer, usage, status, source, license, feature_count, size_bytes, fetched_at "
            "FROM region_layers ORDER BY region_id, layer")
    by_region: dict[int, list] = {}
    for l in layers:
        by_region.setdefault(l["region_id"], []).append({
            "layer": l["layer"], "usage": l["usage"], "status": l["status"], "source": l["source"],
            "license": l["license"], "feature_count": l["feature_count"], "size_bytes": l["size_bytes"],
            "fetched_at": l["fetched_at"].isoformat() if l["fetched_at"] else None})
    return [{"id": r["id"], "name": r["name"], "origin": r["origin"], "active": r["active"],
             "bbox": [r["lon_min"], r["lat_min"], r["lon_max"], r["lat_max"]],
             "layers": by_region.get(r["id"], [])} for r in regs]


@app.get("/api/regions/{region_id}/infrastructure")
async def region_infrastructure(region_id: int):
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(
            "SELECT kind, name, operator, source, attrs, ST_AsGeoJSON(geom)::json AS geometry "
            "FROM infrastructure WHERE region_id = $1", region_id)
    return collection([feature(r["geometry"],
                       {"kind": r["kind"], "name": r["name"], "operator": r["operator"], "source": r["source"],
                        "attrs": json.loads(r["attrs"]) if isinstance(r["attrs"], str) else (r["attrs"] or {})})
                       for r in rows])


@app.get("/api/regions/{region_id}/bathymetry/contours")
async def region_bathymetry_contours(region_id: int):
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(
            "SELECT depth_m, ST_AsGeoJSON(geom)::json AS geometry "
            "FROM bathymetry_contours WHERE region_id = $1 ORDER BY depth_m", region_id)
    return collection([feature(r["geometry"], {"depth_m": r["depth_m"]}) for r in rows])


@app.get("/api/regions/{region_id}/bathymetry/image")
async def region_bathymetry_image(region_id: int):
    """Image colorée du fond marin, provisionnée en local, pour l'overlay carte."""
    from fastapi.responses import FileResponse
    from mars.config import ROOT
    path = ROOT / "data" / "zones" / str(region_id) / "bathymetry.png"
    if not path.exists():
        raise HTTPException(404, "Image bathymétrique non provisionnée")
    return FileResponse(path, media_type="image/png")


# Cache mémoire du raster bathymétrique par région : évite de rouvrir le GeoTIFF à chaque relevé
_BATHY_CACHE: dict = {}


def _load_bathy(region_id: int):
    if region_id not in _BATHY_CACHE:
        import numpy as np
        import rasterio
        from mars.config import ROOT
        path = ROOT / "data" / "zones" / str(region_id) / "bathymetry.tif"
        if not path.exists():
            _BATHY_CACHE[region_id] = None
        else:
            with rasterio.open(path) as ds:
                arr = ds.read(1, masked=True).filled(np.nan).astype("float32")
                _BATHY_CACHE[region_id] = (arr, ds.transform, tuple(ds.bounds))
    return _BATHY_CACHE[region_id]


@app.get("/api/regions/{region_id}/depth")
async def region_depth(region_id: int, lon: float, lat: float):
    """Profondeur de la mer au point demandé, échantillonnée dans le raster provisionné."""
    import math
    from fastapi.concurrency import run_in_threadpool
    data = await run_in_threadpool(_load_bathy, region_id)
    if not data:
        raise HTTPException(404, "Bathymétrie non provisionnée pour cette région")
    arr, transform, bounds = data
    left, bottom, right, top = bounds
    if not (left <= lon <= right and bottom <= lat <= top):
        return {"depth_m": None, "note": "hors zone"}
    col = int((lon - transform.c) / transform.a)
    row = int((lat - transform.f) / transform.e)
    if row < 0 or col < 0 or row >= arr.shape[0] or col >= arr.shape[1]:
        return {"depth_m": None, "note": "hors zone"}
    v = float(arr[row, col])
    if not math.isfinite(v):
        return {"depth_m": None, "note": "indisponible"}
    if v >= 0:
        return {"depth_m": None, "note": "terre"}
    return {"depth_m": round(-v, 1)}


class NewRegion(BaseModel):
    name: str
    bbox: list[float]


@app.post("/api/regions", status_code=201)
async def create_region(req: NewRegion):
    """Crée une région tracée à la main (emprise rectangulaire) et inscrit ses couches en attente."""
    bbox = check_bbox(req.bbox)
    name = req.name.strip()
    if not name:
        raise HTTPException(422, "Nom de région requis")
    async with app.state.pool.acquire() as c:
        row = await c.fetchrow(
            "INSERT INTO regions (name, origin, geom, active) "
            "VALUES ($1, 'manuelle', ST_MakeEnvelope($2, $3, $4, $5, 4326)::geography, false) "
            "ON CONFLICT (name) DO NOTHING RETURNING id", name, bbox[0], bbox[1], bbox[2], bbox[3])
        if not row:
            raise HTTPException(409, "Une région porte déjà ce nom")
        rid = row["id"]
        await c.execute(
            "INSERT INTO region_layers (region_id, layer, usage) "
            "SELECT $1, v.layer, v.usage FROM (VALUES "
            "('coastline', 'operationnelle'), ('bathymetry', 'affichage'), ('infrastructure', 'operationnelle')"
            ") AS v(layer, usage) ON CONFLICT (region_id, layer) DO NOTHING", rid)
    return {"id": rid}


@app.post("/api/regions/{region_id}/activate")
async def activate_region(region_id: int):
    async with app.state.pool.acquire() as c:
        if not await c.fetchval("SELECT true FROM regions WHERE id = $1", region_id):
            raise HTTPException(404, "Région inconnue")
        await c.execute("UPDATE regions SET active = (id = $1)", region_id)
    return {"active": region_id}


def _provision_sync(region_id: int, bbox: list[float]):
    """Exécuté dans un fil : télécharge et stocke la donnée statique, met à jour le manifeste."""
    import logging
    from mars.db import connect
    from mars.regions.provision import provision
    try:
        with connect() as conn, conn.cursor() as cur:
            provision(cur, region_id, bbox, which="all")
            conn.commit()
        _BATHY_CACHE.pop(region_id, None)
    except Exception:
        logging.getLogger("mars.regions").exception("Provisionnement région %s en échec", region_id)
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("UPDATE region_layers SET status = 'echec' WHERE region_id = %s AND status = 'en_cours'", (region_id,))
                conn.commit()
        except Exception:
            pass


@app.post("/api/regions/{region_id}/provision", status_code=202)
async def provision_region_endpoint(region_id: int):
    """Lance le provisionnement de la région en tâche de fond ; le manifeste passe en 'en_cours'."""
    from fastapi.concurrency import run_in_threadpool
    async with app.state.pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT ST_XMin(b) AS x0, ST_YMin(b) AS y0, ST_XMax(b) AS x1, ST_YMax(b) AS y1 "
            "FROM (SELECT ST_Envelope(geom::geometry) AS b FROM regions WHERE id = $1) q", region_id)
        if not row:
            raise HTTPException(404, "Région inconnue")
        await c.execute("UPDATE region_layers SET status = 'en_cours' "
                        "WHERE region_id = $1 AND layer IN ('infrastructure', 'bathymetry')", region_id)
    bbox = [row["x0"], row["y0"], row["x1"], row["y1"]]
    asyncio.create_task(run_in_threadpool(_provision_sync, region_id, bbox))
    return {"status": "provisioning"}


async def _rule_days(c, day: str | None):
    if day:
        return [parse_day(day)]
    days = [r["day"] for r in await c.fetch("SELECT day FROM ais_days ORDER BY day")]
    if not days:
        raise HTTPException(422, "Aucune journée AIS chargée")
    return days


@app.post("/api/rules/ais_gap/run")
async def ais_gap_run(req: RuleRun):
    rules = load_rules()
    async with app.state.pool.acquire() as c:
        if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM reception_cells)"):
            raise HTTPException(422, "Zone de réception absente : lancer scripts/build_masks.py")
        results = {}
        for d in await _rule_days(c, req.day):
            start = pd.Timestamp(d, tz="UTC").to_pydatetime()
            results[d.isoformat()] = await run_ais_gap(c, start, start + pd.Timedelta(days=1), rules)
    return results


class _Rollback(Exception):
    pass


@app.post("/api/rules/ais_gap/selftest")
async def ais_gap_selftest(req: RuleRun):
    """Test par injection : on efface trois heures de messages de navires réels, dans une transaction annulée
    à la fin, et on vérifie que la règle détecte bien chaque coupure ainsi créée. La base n'est pas modifiée."""
    rules = load_rules()
    async with app.state.pool.acquire() as c:
        day = (await _rule_days(c, req.day))[0]
        start = pd.Timestamp(day, tz="UTC").to_pydatetime()
        t_cut = start + pd.Timedelta(hours=11)
        t_back = t_cut + pd.Timedelta(hours=3)
        candidates = await c.fetch(
            """SELECT p.vessel_id, v.mmsi, v.name FROM positions p JOIN vessels v ON v.id = p.vessel_id
               WHERE p.ts BETWEEN $1::timestamptz - interval '1 hour' AND $2::timestamptz + interval '1 hour'
                 AND v.ais_class = 'A' AND NOT (coalesce(v.ship_type, '') = ANY($3::text[]))
               GROUP BY p.vessel_id, v.mmsi, v.name
               HAVING count(*) >= 100 AND avg(p.sog_kn) >= 8
               ORDER BY count(*) DESC LIMIT 40""",
            t_cut, t_back, rules["ais_gap"]["excluded_ship_types"])
        # On teste la détection elle même : navires au large (plus de 10 km des côtes) au moment de la coupure
        offshore = []
        for cand in candidates:
            far = await c.fetchval(
                """SELECT NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, p.geom, 10000))
                   FROM positions p WHERE p.vessel_id = $1 AND p.ts < $2 ORDER BY p.ts DESC LIMIT 1""",
                cand["vessel_id"], t_cut)
            if far:
                offshore.append(cand)
            if len(offshore) == 5:
                break
        candidates = offshore
        trials = []
        for cand in candidates:
            result = {"mmsi": cand["mmsi"], "name": cand["name"]}
            try:
                async with c.transaction():
                    result["messages_effaces"] = int((await c.execute(
                        "DELETE FROM positions WHERE vessel_id = $1 AND ts >= $2 AND ts < $3",
                        cand["vessel_id"], t_cut, t_back)).split()[-1])
                    rows = await find_gaps(c, t_cut - pd.Timedelta(hours=2), t_back + pd.Timedelta(hours=1), rules)
                    found = [r for r in rows if r["vessel_id"] == cand["vessel_id"]]
                    hit = [r for r in found if r["projection_couverte"]]
                    result["detectee"] = bool(hit)
                    if found and not hit:
                        result["raison_probable"] = "route menant hors de la zone fiable : traitée comme une sortie de couverture"
                    elif hit:
                        result["duree_detectee_min"] = round(hit[0]["duration_min"])
                    if not found:
                        last = await c.fetchrow(
                            """SELECT p.sog_kn,
                                      EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, p.geom, $3::float8)) AS pres_cote,
                                      EXISTS (SELECT 1 FROM reception_cells rc
                                              WHERE rc.cx = floor(ST_X(p.geom::geometry) / $4::float8)::int
                                                AND rc.cy = floor(ST_Y(p.geom::geometry) / $5::float8)::int) AS reception_fiable
                               FROM positions p WHERE p.vessel_id = $1 AND p.ts < $2 ORDER BY p.ts DESC LIMIT 1""",
                            cand["vessel_id"], t_cut, rules["ais_gap"]["min_coast_km"] * 1000.0,
                            rules["reception"]["cell_deg_lon"], rules["reception"]["cell_deg_lat"])
                        result["raison_probable"] = (
                            "dernière position près de la côte" if last["pres_cote"] else
                            "dernière position hors zone de réception fiable" if not last["reception_fiable"] else
                            "vitesse trop faible avant la coupure" if (last["sog_kn"] or 0) < rules["ais_gap"]["min_speed_kn"]
                            else "autre filtre (bord des données, classe, historique)")
                    raise _Rollback()
            except _Rollback:
                pass
            trials.append(result)
    detected = [t for t in trials if t.get("detectee")]
    return {"coupure_simulee": f"{t_cut:%H:%M} à {t_back:%H:%M} UTC", "detectees": len(detected),
            "essais": len(trials), "details": trials}


@app.post("/api/rules/rendezvous/run")
async def rendezvous_run(req: RuleRun):
    rules = load_rules()
    async with app.state.pool.acquire() as c:
        days = await _rule_days(c, req.day)
        if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM land)"):
            raise HTTPException(422, "Masque de terre absent : lancer scripts/build_masks.py")
        results = {}
        for d in days:
            start = pd.Timestamp(d, tz="UTC").to_pydatetime()
            results[d.isoformat()] = await run_rendezvous(c, start, start + pd.Timedelta(days=1), rules)
    return results


@app.post("/api/rules/rendezvous/selftest")
async def rendezvous_selftest(req: RuleRun):
    """Test par injection : on fabrique un rendez vous synthétique (deux navires lents, bord à bord, au large,
    au delà de la durée seuil) dans une transaction annulée à la fin, et on vérifie que la règle lève bien
    l'alerte attendue. La base n'est pas modifiée. Prouve que la règle n'est pas silencieuse."""
    rules = load_rules()
    async with app.state.pool.acquire() as c:
        if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM land)"):
            raise HTTPException(422, "Masque de terre absent : lancer scripts/build_masks.py")
        day = (await _rule_days(c, req.day))[0]
        start = pd.Timestamp(day, tz="UTC").to_pydatetime()
        result = await run_rendezvous_selftest(c, start, start + pd.Timedelta(days=1), rules)
    return {"jour": day.isoformat(), **result}


@app.post("/api/rules/infrastructure/run")
async def infrastructure_run(req: RuleRun):
    rules = load_rules()
    async with app.state.pool.acquire() as c:
        if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM infrastructure)"):
            raise HTTPException(422, "Aucune infrastructure provisionnée : provisionner une région d'abord")
        days = await _rule_days(c, req.day)
        results = {}
        for d in days:
            start = pd.Timestamp(d, tz="UTC").to_pydatetime()
            results[d.isoformat()] = await run_infrastructure(c, start, start + pd.Timedelta(days=1), rules)
    return results


@app.post("/api/rules/infrastructure/selftest")
async def infrastructure_selftest(req: RuleRun):
    """Test par injection : un navire synthétique stationne sur un corridor au delà du seuil, dans une
    transaction annulée, et on vérifie que la règle lève l'alerte INFRA_THREAT. La base n'est pas modifiée."""
    rules = load_rules()
    async with app.state.pool.acquire() as c:
        day = (await _rule_days(c, req.day))[0]
        start = pd.Timestamp(day, tz="UTC").to_pydatetime()
        result = await run_infrastructure_selftest(c, start, start + pd.Timedelta(days=1), rules)
    return {"jour": day.isoformat(), **result}


@app.get("/api/chip")
async def chip(lon: float, lat: float, time: str, size_m: float = 800):
    """Vignette radar autour d'un point, produite par le service d'inférence qui détient les extraits."""
    if not valid_lonlat(lon, lat):
        raise HTTPException(422, "Position hors des plages valides (longitude 180, latitude 90)")
    if not (is_finite(size_m) and CHIP_MIN_M <= size_m <= CHIP_MAX_M):
        raise HTTPException(422, f"Taille de vignette attendue entre {CHIP_MIN_M} et {CHIP_MAX_M} m")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{INFERENCE_URL}/v1/chip",
                                 params={"lon": lon, "lat": lat, "acquired_at": time, "size_m": size_m})
    except httpx.ConnectError:
        raise HTTPException(503, "Service d'inférence injoignable")
    if r.status_code != 200:
        raise HTTPException(404, "Vignette indisponible pour ce passage")
    return Response(r.content, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


class AlertAction(BaseModel):
    action: str
    note: str | None = None
    author: str = "Opérateur"


STATUS_OF = {"acquitter": "acquittee", "confirmer": "confirmee", "classer": "classee", "rouvrir": "nouvelle"}


@app.post("/api/alerts/{alert_id}/actions")
async def alert_action(alert_id: int, a: AlertAction):
    """Décision d'un opérateur sur une alerte : nouveau statut, et trace dans le journal."""
    if a.action not in STATUS_OF:
        raise HTTPException(422, "Action inconnue")
    async with app.state.pool.acquire() as c, c.transaction():
        status = await c.fetchval("UPDATE alerts SET status = $2 WHERE id = $1 RETURNING status",
                                  alert_id, STATUS_OF[a.action])
        if status is None:
            raise HTTPException(404, "Alerte inconnue")
        await c.execute("INSERT INTO alert_actions (alert_id, action, note, author) VALUES ($1, $2, $3, $4)",
                        alert_id, a.action, (a.note or "").strip() or None, a.author)
    return {"id": alert_id, "status": status}


@app.get("/api/alerts/{alert_id}/actions")
async def alert_actions(alert_id: int):
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT action, note, author, at FROM alert_actions WHERE alert_id = $1 ORDER BY at DESC",
                             alert_id)
    return [clean(r) for r in rows]


@app.get("/api/alerts/day")
async def alerts_of_day(day: str):
    """Alertes comportementales d'une journée, pour la frise chronologique de l'interface."""
    start = pd.Timestamp(parse_day(day), tz="UTC").to_pydatetime()
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(
            """SELECT al.id, al.type, al.severity, al.status, al.event_time, al.details, al.rule_version,
                      ST_AsGeoJSON(al.geom)::json AS geometry
               FROM alerts al
               WHERE al.type IN ('RENDEZVOUS', 'AIS_GAP')
                 AND al.event_time >= $1::timestamptz AND al.event_time < $1::timestamptz + interval '1 day'
               ORDER BY al.event_time""", start)
    return collection([feature(r["geometry"], clean(r)) for r in rows])


@app.get("/api/alerts/live")
async def live_alerts():
    async with app.state.pool.acquire() as c:
        clock = await read_clock(c)
        return await read_live_alerts(c, clock["now"])


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
