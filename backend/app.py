"""API de MARS C2.

Trafic AIS à un instant et en direct (flux SSE), navires, état du direct, masques, infrastructures, alertes et
décisions, frise ; lecture des analyses radar passées. Les autres routes sont dans les routeurs importés ci dessous.
"""
import asyncio
import json
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime

import asyncpg
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

from contenu import router as contenu_router
from regions import dans_region, region_ewkt, regions_list
from satellites import next_passes, router as satellites_router
from preuves import router as preuves_router
from travailleurs import router as travailleurs_router, viirs_status
from sentinel import router as sentinel_router, sentinel_status
from demandes import demandes_status, router as demandes_router
from controle import exiger_operateur, router as controle_router
from dossiers import router as dossiers_router
import demo
from mars import traces
from mars.config import load_rules
from mars.frise import timeline
from mars.rules import build_reception_cells, build_stationary_zones

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://mars:mars@localhost:5432/mars")
TRAFFIC_WINDOW_MIN = 30   # un navire reste affiché 30 minutes simulées après son dernier message
TRAIL_MIN = 10            # longueur de la traînée, en minutes simulées

# Traces (étape D2) : FastAPI, asyncpg, httpx et requests instrumentés ; spans métier sur les routes lourdes. Ni le flux
# SSE (une réponse par seconde), ni /api/health, ni la vérification du jeton des travailleurs par nginx ne sont tracés.
# Paramètres SQL jamais capturés (positions), chaînes de requête retirées (recherche par nom), mars/traces.py.
traces.init("mars-api")
try:
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    AsyncPGInstrumentor(capture_parameters=False).instrument()
    HTTPXClientInstrumentor().instrument(request_hook=traces.requete_httpx, async_request_hook=traces.requete_httpx_async)
except ImportError:                                         # pragma: no cover
    FastAPIInstrumentor = None


async def _init(conn):
    for t in ("json", "jsonb"):
        await conn.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


@asynccontextmanager
async def lifespan(app):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, init=_init, min_size=1, max_size=10)
    try:                                       # version de l'API, lue par la barre d'état et Grafana
        async with app.state.pool.acquire() as c:
            await c.execute("INSERT INTO signes_de_vie (service, dernier_le, detail) VALUES ('api', now(), $1) "
                            "ON CONFLICT (service) DO UPDATE SET dernier_le = now(), detail = EXCLUDED.detail, "
                            "mesure_le = now()", {"version": traces.version()})
    except Exception:
        pass                                   # base sans la migration 26 : la version reste lisible par /api/ingestion
    yield
    await app.state.pool.close()


app = FastAPI(title="MARS C2", lifespan=lifespan)


@app.middleware("http")
async def demonstration(request: Request, call_next):
    """Mode démonstration (lot P, backend/demo.py) : réponses JSON de l'API masquées (noms, MMSI, OMI, photos) quand le
    cookie de session mars_demo est présent ; photos et traces GPX refusées."""
    graine = demo.graine_valide(request.cookies.get(demo.COOKIE))
    if graine is None or not request.url.path.startswith("/api/"):
        return await call_next(request)
    if request.url.path.endswith(("/photo", "/track.gpx")):
        return Response(status_code=404, content="Masqué en démonstration")
    response = await call_next(request)
    if not (response.headers.get("content-type") or "").startswith("application/json"):
        return response
    corps = b"".join([part async for part in response.body_iterator])
    try:
        corps = demo.masquer_json(graine, corps)
    except ValueError:
        pass                                         # corps non JSON malgré l'en tête : rendu tel quel
    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    return Response(content=corps, status_code=response.status_code, headers=headers, media_type="application/json")
if FastAPIInstrumentor is not None:
    FastAPIInstrumentor.instrument_app(app, excluded_urls=traces.API_NON_TRACEES)
app.include_router(contenu_router)      # recherche, fiches, notes, navires suivis, photo (backend/contenu.py)
app.include_router(satellites_router)   # calendrier des passages Sentinel 1 et 2 (backend/satellites.py)
app.include_router(travailleurs_router) # travailleurs, détections VIIRS, mesures (backend/travailleurs.py)
app.include_router(preuves_router)      # preuves images des détections satellites, relayées depuis R2
app.include_router(sentinel_router)     # détections Sentinel 1, vérification satellite (backend/sentinel.py)
app.include_router(demandes_router)     # tip and cue : demandes d'observation satellite (backend/demandes.py)
app.include_router(controle_router)     # panneau de contrôle, connexion des opérateurs (backend/controle.py, lot P)
app.include_router(dossiers_router)     # dossiers par navire ou par point (backend/dossiers.py, lot U)


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


# Trafic AIS rejoué

TRAFFIC_COLS = ["vessel_id", "lon", "lat", "sog_kn", "cog_deg", "t", "mmsi", "name", "ship_type", "length_m", "flag",
                "watch"]


async def read_traffic(c, now: datetime, region: str | None = None) -> dict:
    """Dernière position de chaque navire vu dans les 30 minutes précédant `now`, en colonnes : sans répéter les
    noms de champs à chaque navire, avec des coordonnées arrondies au mètre, le trafic de plusieurs milliers de
    navires tient en 5 fois moins d'octets que du GeoJSON. `t` : instant de la position, en secondes. `region` :
    seulement les navires dont la dernière position est dans la région (géométrie EWKT, voir backend/regions.py)."""
    with traces.span("trafic.lecture") as sp:
        out = await _read_traffic(c, now, region)
        sp.set_attribute("trafic.navires", len(out["rows"]))
        return out


async def _read_traffic(c, now: datetime, region: str | None) -> dict:
    rows = await c.fetch(
        """
        SELECT t.vessel_id, round(t.lon::numeric, 5)::float8 AS lon, round(t.lat::numeric, 5)::float8 AS lat,
               round(t.sog_kn::numeric, 1)::float8 AS sog_kn, round(t.cog_deg)::int AS cog_deg,
               extract(epoch FROM t.ts)::bigint AS t,
               v.mmsi, v.name, v.ship_type, v.length_m, v.flag, w.level AS watch
        FROM (SELECT DISTINCT ON (p.vessel_id)
                     p.vessel_id, p.sog_kn, p.cog_deg, p.ts,
                     ST_X(p.geom::geometry) AS lon, ST_Y(p.geom::geometry) AS lat
              FROM positions p
              WHERE p.ts > $1::timestamptz - make_interval(mins => $2::int) AND p.ts <= $1::timestamptz
              ORDER BY p.vessel_id, p.ts DESC) t
        JOIN vessels v ON v.id = t.vessel_id
        LEFT JOIN vessel_watch w ON w.vessel_id = t.vessel_id
        WHERE """ + dans_region("ST_SetSRID(ST_MakePoint(t.lon, t.lat), 4326)", 3), now, TRAFFIC_WINDOW_MIN, region)
    return {"at": now.isoformat(), "cols": TRAFFIC_COLS,
            "rows": [[finite(r[k]) for k in TRAFFIC_COLS] for r in rows]}


@app.get("/api/regions")
async def regions():
    """Régions du sélecteur de la barre d'état : clé, nom, emprise (lon_min, lat_min, lon_max, lat_max)."""
    async with app.state.pool.acquire() as c:
        return await regions_list(c)


@app.get("/api/traffic")
async def traffic(at: datetime | None = None, region: str | None = None):
    """Trafic à un instant donné (plage ou rejeu de l'interface, instant porté par l'adresse de la page), sans
    dépendre de l'horloge partagée ; sans `at`, l'heure réelle."""
    async with app.state.pool.acquire() as c:
        return await read_traffic(c, at or await c.fetchval("SELECT clock_timestamp()"), await region_ewkt(c, region))


@app.get("/api/traffic/trails")
async def trails(minutes: int = TRAIL_MIN, at: datetime | None = None, region: str | None = None):
    """Traînées des `minutes` précédant `at` (sans `at` : l'heure réelle ; l'instant est tenu par l'interface)."""
    async with app.state.pool.acquire() as c:
        now = at or await c.fetchval("SELECT clock_timestamp()")
        with traces.span("trafic.trainees", **{"trafic.minutes": minutes}) as sp:
            rows = await _trails(c, now, minutes, await region_ewkt(c, region))
            sp.set_attribute("trafic.navires", len(rows))
    return collection([feature(r["geometry"], {"vessel_id": r["vessel_id"]}) for r in rows])


async def _trails(c, now: datetime, minutes: int, region):
    return await c.fetch(
        """
        SELECT p.vessel_id, ST_AsGeoJSON(ST_MakeLine(p.geom::geometry ORDER BY p.ts))::json AS geometry
        FROM positions p
        WHERE p.ts > $1::timestamptz - make_interval(mins => $2::int) AND p.ts <= $1::timestamptz
          AND """ + dans_region("p.geom::geometry", 3) + """
        GROUP BY p.vessel_id
        HAVING count(*) >= 2
        """, now, minutes, region)


@app.get("/api/vessels/{vessel_id}")
async def vessel(vessel_id: int):
    """Fiche d'un navire : identité courante, historique des identités déclarées (y compris sous d'autres MMSI
    portant le même OMI) et correspondances avec les listes de surveillance."""
    async with app.state.pool.acquire() as c:
        v = await c.fetchrow(
            "SELECT id, mmsi, imo, name, callsign, ship_type, ship_type_code, flag, length_m, ais_class, destination, "
            "first_seen, last_seen FROM vessels WHERE id = $1", vessel_id)
        if v is None:
            raise HTTPException(404, "Navire inconnu")
        history = await c.fetch(
            """SELECT v.mmsi, i.name, i.imo, i.callsign, i.flag, i.first_seen, i.last_seen, i.messages
               FROM vessel_identities i JOIN vessels v ON v.id = i.vessel_id
               WHERE i.vessel_id = $1 OR ($2::int IS NOT NULL AND i.imo = $2::int)
               ORDER BY i.first_seen""", vessel_id, v["imo"])
        watch = await c.fetchrow("SELECT level, matched_by, entries FROM vessel_watch WHERE vessel_id = $1", vessel_id)
        # Alertes dont ce navire est une preuve (listes, identité, rendez vous, coupures), les plus récentes d'abord
        alerts = await c.fetch(
            """SELECT al.id, al.type, al.severity, al.status, al.event_time, al.details, al.rule_version,
                      ST_AsGeoJSON(al.geom)::json AS geometry
               FROM alerts al JOIN alert_evidence e ON e.alert_id = al.id
               WHERE e.evidence_type = 'vessel' AND e.evidence_id = $1
               ORDER BY al.event_time DESC LIMIT 20""", vessel_id)
    return {**clean(v), "identities": [clean(r) for r in history], "watch": clean(watch) if watch else None,
            "alerts": [feature(r["geometry"], clean(r)) for r in alerts]}


@app.get("/api/ingestion")
async def ingestion_status(region: str | None = None):
    """État du direct : dernier fichier chargé, fraîcheur et volume de la dernière heure, listes chargées, prochain
    passage satellite prévu sur la région affichée."""
    async with app.state.pool.acquire() as c:
        satellites = await next_passes(c, region)
        satellites["viirs"] = await viirs_status(c)
        satellites["sentinel"] = await sentinel_status(c)
        satellites["tip_and_cue"] = await demandes_status(c)
        last = await c.fetchrow("SELECT folder, name, ingested_at FROM ingested_files ORDER BY ingested_at DESC LIMIT 1")
        hour = await c.fetchrow(
            "SELECT count(*) AS positions, count(DISTINCT vessel_id) AS navires, max(ts) AS derniere_position, "
            "extract(epoch FROM now() - max(ts))::float8 AS retard_s "
            "FROM positions WHERE ts > now() - interval '1 hour'")
        files = await c.fetchrow(
            "SELECT count(*) AS fichiers, coalesce(sum(rows_read), 0) AS lus, coalesce(sum(rows_kept), 0) AS conserves "
            "FROM ingested_files WHERE folder LIKE 'positions/%' AND ingested_at > now() - interval '1 hour'")
        lists = await c.fetch("SELECT source, count(*) AS navires, max(imported_at) AS importe_le FROM watchlist GROUP BY source")
        # Mesures écrites toutes les 10 minutes par le conteneur « taches » (serveur seulement)
        disk = await c.fetch(
            "SELECT path AS chemin, round(free_pct::numeric, 1)::float8 AS libre_pct, "
            "round(free_bytes / 1e9, 1)::float8 AS libre_go, round(total_bytes / 1e9, 1)::float8 AS total_go, "
            "alert AS alerte, checked_at AS mesure_le, checked_at < now() - interval '30 minutes' AS perimee "
            "FROM disk_status ORDER BY path")
        runs = await c.fetch(
            "SELECT DISTINCT ON (task) task AS tache, status AS statut, started_at AS debut, details "
            "FROM task_runs ORDER BY task, started_at DESC")
        try:                                  # seuils du disque, ceux de Grafana (config/supervision.env, recopiés par taches)
            seuils = {r["cle"].removeprefix("seuil_"): r["valeur"] for r in await c.fetch(
                "SELECT cle, valeur FROM parametres_supervision WHERE cle IN ('seuil_disque_pct', "
                "'seuil_disque_orange_pts')")}
        except asyncpg.PostgresError:
            seuils = {}
        try:                                  # empreinte du commit de chaque image (signes de vie, migration 26)
            versions = {r["service"]: r["version"] for r in await c.fetch(
                "SELECT service, detail->>'version' AS version FROM signes_de_vie WHERE detail ? 'version'")}
        except asyncpg.PostgresError:
            versions = {}
    versions["api"] = traces.version()
    return {"dernier_fichier": clean(last) if last else None, "derniere_heure": {**clean(hour), **clean(files)},
            "versions": versions,
            "seuils": {"disque_pct": seuils.get("disque_pct", 15.0), "disque_orange_pts": seuils.get("disque_orange_pts", 10.0)},
            "listes": [clean(r) for r in lists],
            "alerte_disque": any(r["alerte"] for r in disk), "disque": [clean(r) for r in disk],
            "taches": [clean(r) for r in runs], "satellites": satellites}


@app.get("/api/vessels/{vessel_id}/track")
async def track(vessel_id: int, start: datetime, end: datetime, max_points: int = 2000):
    """Trajectoire d'un navire sur une fenêtre de temps, allégée à `max_points` points au plus (une position sur k,
    dans l'ordre du temps, plus la dernière) : 30 jours d'un navire en route font plus de 40 000 positions."""
    async with app.state.pool.acquire() as c:
        geometry = await c.fetchval(
            """WITH p AS (SELECT geom, ts, row_number() OVER (ORDER BY ts) AS k, count(*) OVER () AS n
                          FROM positions WHERE vessel_id = $1 AND ts BETWEEN $2 AND $3)
               SELECT ST_AsGeoJSON(ST_MakeLine(geom::geometry ORDER BY ts), 5)::json FROM p
               WHERE n <= $4::int OR (k - 1) % ceil(n::float8 / $4::int)::int = 0 OR k = n""",
            vessel_id, start, end, max(2, max_points))
    if geometry is None:
        raise HTTPException(404, "Aucune position sur cette fenêtre")
    return feature(geometry, {"vessel_id": vessel_id, "start": start.isoformat(), "end": end.isoformat()})


STREAM_TRAFFIC_MAX_S = 30   # trafic renvoyé au plus tard toutes les 30 s, et dès que l'ingestion a chargé du nouveau


@app.get("/api/stream")
async def stream(request: Request, direct: bool = False, region: str | None = None):
    """Flux SSE du mode direct, une fois par seconde (message vide qui tient la connexion) ; trafic à l'heure réelle
    seulement quand il a changé.

    Les positions arrivent par fichiers (une écriture par minute, une ingestion toutes les 15 s) : renvoyer chaque
    seconde 2 000 navires coûtait 680 Ko par client et par seconde pour rien. `direct` : gardé pour les anciens
    clients ; l'horloge partagée a disparu (lot Z4), l'instant des autres modes est tenu par l'interface."""
    async with app.state.pool.acquire() as c:
        reg = await region_ewkt(c, region)
    graine = demo.graine_valide(request.cookies.get(demo.COOKIE))     # mode démonstration : flux masqué aussi

    async def events():
        last_ingest, last_sent = None, 0.0
        loop = asyncio.get_running_loop()
        while not await request.is_disconnected():
            async with app.state.pool.acquire() as c:
                now = await c.fetchval("SELECT clock_timestamp()")
                payload = {}
                ingest = await c.fetchval("SELECT max(ingested_at) FROM ingested_files")
                if ingest != last_ingest or loop.time() - last_sent >= STREAM_TRAFFIC_MAX_S:
                    payload["navires"] = await read_traffic(c, now, reg)
                    last_ingest, last_sent = ingest, loop.time()
            if graine:
                payload = demo.Masque(graine).masquer(json.loads(json.dumps(payload, default=str)))
            yield f"event: traffic\ndata: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Masques (zones de mouillage, réception)


@app.post("/api/masks/stationary")
async def rebuild_stationary_zones(jours: int | None = None, _op: str = Depends(exiger_operateur)):
    """Zones de mouillage sur toutes les journées chargées, ou sur les `jours` dernières."""
    async with app.state.pool.acquire() as c:
        span = await c.fetchrow("SELECT min(day) AS d0, max(day) AS d1 FROM ais_days")
        if span["d0"] is None:
            raise HTTPException(422, "Aucune journée AIS chargée")
        if jours:
            span = {"d0": max(span["d0"], span["d1"] - pd.Timedelta(days=jours - 1)), "d1": span["d1"]}
        n = await build_stationary_zones(c, span["d0"], span["d1"], load_rules())
    return {"zones": n, "du": span["d0"].isoformat(), "au": span["d1"].isoformat()}


@app.get("/api/masks/stationary")
async def stationary_zones(region: str | None = None):
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT id, vessels, slow_positions, ST_AsGeoJSON(geom)::json AS geometry FROM stationary_zones "
                             "WHERE " + dans_region("geom::geometry", 1), await region_ewkt(c, region))
    return collection([feature(r["geometry"], clean(r)) for r in rows])


# Périmètre France de la section 0 : par défaut on ne sert que ces zones (la base peut contenir
# d'autres régions issues du worktree de calibration danois). ?region=<id> cible une zone précise.
FRANCE_REGIONS = ("Bretagne", "Manche", "Gascogne", "Mediterranee")


@app.get("/api/infrastructure")
async def infrastructure(region: str | None = None, tolerance: float = 0.0):
    """Câbles, pipelines et parcs éoliens provisionnés depuis EMODnet (table infrastructure), en GeoJSON.
    `tolerance` (degrés) simplifie les tracés : les 816 tracés de France pèsent 2,6 Mo en pleine résolution.
    `region` : clé d'une région (bretagne…), ou son numéro ; sans région, toute la France."""
    geom = ("ST_SimplifyPreserveTopology(i.geom::geometry, $2::float8)" if tolerance > 0 else "i.geom::geometry")
    sql = ("SELECT i.id, i.kind, i.name, i.operator, i.source, i.attrs->>'type' AS type, "
           f"r.name AS region, ST_AsGeoJSON({geom}, 5)::json AS geometry "
           "FROM infrastructure i JOIN regions r ON r.id = i.region_id")
    if region is not None and region.isdigit():
        sql += " WHERE i.region_id = $1::int"
        args: list = [int(region)]
    elif region:
        sql += " WHERE lower(r.name) = $1::text"
        args = [region.lower()]
    else:
        sql += " WHERE lower(r.name) = ANY($1::text[])"
        args = [[n.lower() for n in FRANCE_REGIONS]]   # régions saisies à la main : casse indifférente
    if tolerance > 0:
        args.append(tolerance)
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(sql, *args)
    return collection([feature(r["geometry"], {
        "id": r["id"], "kind": r["kind"], "name": r["name"], "operator": r["operator"],
        "type": r["type"], "source": r["source"], "region": r["region"],
    }) for r in rows])


@app.post("/api/masks/reception")
async def rebuild_reception_cells(jours: int | None = None, _op: str = Depends(exiger_operateur)):
    """Zone de réception fiable sur toutes les positions, ou sur celles des `jours` derniers jours."""
    since = None if not jours else (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=jours)).to_pydatetime()
    async with app.state.pool.acquire() as c:
        return await build_reception_cells(c, load_rules(), since)


@app.get("/api/masks/reception")
async def reception_cells(region: str | None = None):
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT messages, vessels, hours, coverage, ST_AsGeoJSON(geom)::json AS geometry "
                             "FROM reception_cells WHERE " + dans_region("geom::geometry", 1), await region_ewkt(c, region))
    return collection([feature(r["geometry"], clean(r)) for r in rows])


class AlertAction(BaseModel):
    action: str
    note: str | None = None
    motif: str | None = None     # obligatoire pour classer : faux_positif, activite_legitime, doublon
                                 # auteur : l'opérateur connecté (un champ author envoyé est ignoré)


STATUS_OF = {"acquitter": "acquittee", "confirmer": "confirmee", "classer": "classee", "rouvrir": "nouvelle",
             "commenter": None}
MOTIFS = ("faux_positif", "activite_legitime", "doublon")


@app.post("/api/alerts/{alert_id}/actions")
async def alert_action(alert_id: int, a: AlertAction, author: str = Depends(exiger_operateur)):
    """Décision d'un opérateur sur une alerte : nouveau statut, et trace dans le journal."""
    if a.action not in STATUS_OF:
        raise HTTPException(422, "Action inconnue")
    note = (a.note or "").strip() or None
    if a.action == "classer" and a.motif not in MOTIFS:
        raise HTTPException(422, f"Motif de classement obligatoire : {', '.join(MOTIFS)}")
    if a.action == "commenter" and not note:
        raise HTTPException(422, "Commentaire vide")
    async with app.state.pool.acquire() as c, c.transaction():
        if STATUS_OF[a.action] is None:          # commentaire : le statut ne change pas
            status = await c.fetchval("SELECT status FROM alerts WHERE id = $1", alert_id)
        else:
            status = await c.fetchval("UPDATE alerts SET status = $2 WHERE id = $1 RETURNING status",
                                      alert_id, STATUS_OF[a.action])
        if status is None:
            raise HTTPException(404, "Alerte inconnue")
        await c.execute("INSERT INTO alert_actions (alert_id, action, note, motif, author) VALUES ($1, $2, $3, $4, $5)",
                        alert_id, a.action, note, a.motif if a.action == "classer" else None, author)
    return {"id": alert_id, "status": status}


@app.get("/api/alerts/{alert_id}/actions")
async def alert_actions(alert_id: int):
    async with app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT action, note, motif, author, at FROM alert_actions WHERE alert_id = $1 "
                             "ORDER BY at DESC", alert_id)
    return [clean(r) for r in rows]


# Frise : densité du trafic et coupures du flux AIS sur une plage

@app.get("/api/timeline")
async def timeline_route(start: datetime, end: datetime, bins: int = 240, region: str | None = None):
    """Histogramme du nombre de navires (moyenne par tranche de 10 minutes) et coupures du flux AIS, lus dans les
    statistiques tenues par le conteneur taches : une plage de 30 jours ne lit que quelques dizaines de milliers de
    lignes, au lieu de millions de positions."""
    if end <= start:
        raise HTTPException(422, "Plage vide")
    async with app.state.pool.acquire() as c:
        await region_ewkt(c, region)                      # 404 pour une région inconnue
        with traces.span("frise.lecture", **{"frise.jours": round((end - start).total_seconds() / 86400, 2),
                                             "frise.tranches": bins}):
            return await timeline(c, start, end, max(10, min(bins, 1000)), load_rules()["continu"]["flux_min_ratio"],
                                  region.lower() if region else None)


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
async def alerts(analysis_id: int | None = None, start: datetime | None = None, end: datetime | None = None,
                 limit: int = 2000, region: str | None = None, a_l_instant: datetime | None = None):
    """Alertes d'une analyse radar, ou d'une plage de temps [start, end] : instant de l'alerte dans la plage, ou, pour
    un navire des listes, passage qui chevauche la plage. `vessel_ids` : navires preuves de l'alerte (regroupement
    du fil d'alertes, navires en alerte sur la carte). `a_l_instant` (rejeu de démonstration, lot P) : seulement les
    alertes déjà levées à cet instant, avec leur statut d'alors (décisions des opérateurs antérieures) ; lecture seule."""
    q = """
    SELECT al.id, al.type, al.severity,
           CASE WHEN $6::timestamptz IS NULL THEN al.status ELSE coalesce((
               SELECT CASE x.action WHEN 'acquitter' THEN 'acquittee' WHEN 'confirmer' THEN 'confirmee'
                                    WHEN 'classer' THEN 'classee' ELSE 'nouvelle' END
               FROM alert_actions x WHERE x.alert_id = al.id AND x.at <= $6::timestamptz AND x.action <> 'commenter'
               ORDER BY x.at DESC LIMIT 1), 'nouvelle') END AS status,
           al.event_time, al.detected_at, al.details, al.rule_version,
           al.controle_empreinte, ST_AsGeoJSON(al.geom, 5)::json AS geometry,
           coalesce((SELECT array_agg(e.evidence_id) FROM alert_evidence e
                     WHERE e.alert_id = al.id AND e.evidence_type = 'vessel'), '{}') AS vessel_ids
    FROM alerts al
    WHERE ($1::bigint IS NULL OR EXISTS (
            SELECT 1 FROM alert_evidence e
            WHERE e.alert_id = al.id AND e.evidence_type = 'analysis' AND e.evidence_id = $1::bigint))
      AND ($2::timestamptz IS NULL OR al.event_time <= $3::timestamptz AND (
            al.event_time >= $2::timestamptz
            OR (al.type = 'WATCHLIST' AND (al.details->>'fin')::timestamptz >= $2::timestamptz)))
      AND """ + dans_region("al.geom::geometry", 5) + """
      AND ($6::timestamptz IS NULL OR al.detected_at <= $6::timestamptz)
    ORDER BY al.event_time DESC
    LIMIT $4::int
    """
    async with app.state.pool.acquire() as c:
        rows = await c.fetch(q, analysis_id, start, end or (start and datetime.now(start.tzinfo)), limit,
                             await region_ewkt(c, region), a_l_instant)
    return collection([feature(r["geometry"], clean(r)) for r in rows])
