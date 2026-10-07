"""Travailleurs éphémères et détections nocturnes VIIRS (étape 3, lot B) ; mesures de la plateforme.

* Retour des travailleurs (réseau privé seulement, port 8090 de nginx) : état et résultats, protégés par le jeton de
  l'exécution (seule son empreinte est en base). Le traitement est fait par le conteneur taches (mars/viirs.py).
* Lecture : détections VIIRS d'une plage et d'une région, fiche d'une détection, nuits traitées (frise).
* /api/metrics : compteurs, durées et états au format texte de Prometheus, prêt à être lu par un agent Grafana.
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from regions import dans_region, region_ewkt

router = APIRouter()


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(r) -> dict:
    return {k: _iso(v) for k, v in dict(r).items()}


def _range(start, end):
    end = end or datetime.now(timezone.utc)
    return start or end - timedelta(hours=24), end


async def _check(c, wid: int, authorization: str | None):
    w = await c.fetchrow("SELECT id, etat, jeton_hash, detruit_le FROM travailleurs WHERE id = $1", wid)
    token = (authorization or "").removeprefix("Bearer ").strip()
    if w is None or not token or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), w["jeton_hash"]):
        raise HTTPException(403, "Jeton refusé")
    if w["detruit_le"] is not None or w["etat"] not in ("cree", "demarre"):
        raise HTTPException(409, f"Travailleur dans l'état {w['etat']}")
    return w


@router.post("/api/travailleurs/{wid}/etat")
async def worker_state(request: Request, wid: int, authorization: str | None = Header(None)):
    async with request.app.state.pool.acquire() as c:
        await _check(c, wid, authorization)
        await c.execute("UPDATE travailleurs SET etat = 'demarre', demarre_le = now() WHERE id = $1", wid)
    return {"ok": True}


@router.post("/api/travailleurs/{wid}/resultats")
async def worker_results(request: Request, wid: int, authorization: str | None = Header(None)):
    body = await request.json()
    async with request.app.state.pool.acquire() as c:
        await _check(c, wid, authorization)
        await c.execute(
            "UPDATE travailleurs SET etat = 'resultats', resultats_le = now(), resultat = $2, "
            "mesures = mesures || $3 WHERE id = $1", wid, body, body.get("mesures") or {})
    return {"ok": True}


# Détections VIIRS

STATUT = ("CASE WHEN d.mask_reason IS NOT NULL THEN 'ecartee' WHEN d.matched_vessel_id IS NOT NULL THEN 'avec_ais' "
          "ELSE 'sans_ais' END")


@router.get("/api/viirs/detections")
async def viirs_detections(request: Request, start: datetime | None = None, end: datetime | None = None,
                           region: str | None = None, limit: int = 5000):
    start, end = _range(start, end)
    async with request.app.state.pool.acquire() as c:
        rows = await c.fetch(
            f"""SELECT d.id, d.ts, round(d.nanowatts::numeric, 1)::float8 AS nanowatts, d.mask_reason, {STATUT} AS statut,
                       g.satellite, v.name AS navire, ST_AsGeoJSON(d.geom, 5)::json AS geometry
                FROM viirs_detections d JOIN viirs_granules g ON g.id = d.granule_id
                LEFT JOIN vessels v ON v.id = d.matched_vessel_id
                WHERE d.ts BETWEEN $1 AND $2 AND {dans_region('d.geom::geometry', 3)}
                ORDER BY d.ts LIMIT $4::int""", start, end, await region_ewkt(c, region), limit)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": r["geometry"], "properties": {k: _iso(v) for k, v in dict(r).items() if k != "geometry"}}
        for r in rows]}


@router.get("/api/viirs/detections/{did}")
async def viirs_detection(request: Request, did: int):
    """Fiche d'une détection : heure, intensité, navire AIS apparié ou absence d'appariement, alertes liées."""
    async with request.app.state.pool.acquire() as c:
        r = await c.fetchrow(
            f"""SELECT d.id, d.ts, d.nanowatts, d.orientation, d.lune, d.ciel_clair, d.mask_reason, {STATUT} AS statut,
                       d.match_distance_m, d.distance_cote_m, g.satellite, g.nom AS granule, g.nuit,
                       ST_X(d.geom::geometry) AS lon, ST_Y(d.geom::geometry) AS lat,
                       v.id AS vessel_id, v.name, v.mmsi, v.flag, w.level AS watch
                FROM viirs_detections d JOIN viirs_granules g ON g.id = d.granule_id
                LEFT JOIN vessels v ON v.id = d.matched_vessel_id LEFT JOIN vessel_watch w ON w.vessel_id = v.id
                WHERE d.id = $1""", did)
        if r is None:
            raise HTTPException(404, "Détection inconnue")
        alerts = await c.fetch(
            """SELECT a.id, a.type, a.severity, a.status, a.event_time FROM alerts a
               JOIN alert_evidence e ON e.alert_id = a.id AND e.evidence_type = 'viirs' AND e.evidence_id = $1
               ORDER BY a.event_time DESC""", did)
    return {**_row(r), "alertes": [_row(a) for a in alerts]}


@router.get("/api/viirs/nuits")
async def viirs_nights(request: Request, start: datetime | None = None, end: datetime | None = None):
    """Nuits traitées qui chevauchent la plage (piste de la frise) : heures des granules, détections par statut."""
    start, end = _range(start, end)
    async with request.app.state.pool.acquire() as c:
        rows = await c.fetch(
            """SELECT g.nuit, min(g.debut) AS debut, max(g.fin) AS fin, count(DISTINCT g.id) AS granules,
                      count(DISTINCT g.id) FILTER (WHERE g.erreur IS NOT NULL) AS en_echec,
                      array_agg(DISTINCT g.satellite) AS satellites, count(d.id) AS detections,
                      count(d.id) FILTER (WHERE d.mask_reason IS NULL AND d.matched_vessel_id IS NULL) AS sans_ais,
                      round(avg(g.lune)::numeric)::int AS lune
               FROM viirs_granules g LEFT JOIN viirs_detections d ON d.granule_id = g.id
               GROUP BY g.nuit HAVING max(g.fin) >= $1 AND min(g.debut) <= $2 ORDER BY g.nuit""", start, end)
    return [_row(r) for r in rows]


async def viirs_status(c) -> dict:
    """Barre d'état : dernière nuit traitée et dernier travailleur."""
    night = await c.fetchrow(
        """SELECT g.nuit, max(g.traite_le) AS traite_le, count(*) AS granules,
                  count(*) FILTER (WHERE g.erreur IS NOT NULL) AS en_echec, sum(g.detections) AS detections
           FROM viirs_granules g GROUP BY g.nuit ORDER BY g.nuit DESC LIMIT 1""")
    w = await c.fetchrow(
        """SELECT id, tache, etat, commercial_type, cree_le, fini_le, detruit_le, erreur,
                  (mesures->>'cout_estime_eur')::float8 AS cout_eur, (mesures->>'duree_s')::float8 AS duree_s
           FROM travailleurs ORDER BY id DESC LIMIT 1""")
    return {"derniere_nuit": _row(night) if night else None, "travailleur": _row(w) if w else None}


# Mesures (Prometheus, format texte)

def _line(name: str, value, labels: dict | None = None) -> str:
    lab = ",".join(f'{k}="{str(v).replace(chr(34), "")}"' for k, v in (labels or {}).items())
    return f"{name}{{{lab}}} {float(value or 0)}" if lab else f"{name} {float(value or 0)}"


@router.get("/api/metrics", response_class=PlainTextResponse)
async def metrics(request: Request):
    """Mesures de la plateforme, sans outil installé : un agent Grafana (Alloy) ou Prometheus pourra les lire telles
    quelles plus tard. Par tâche : exécutions par état, durée de la dernière, âge de la dernière réussite ; par
    travailleur : nombre, minutes, coût estimé ; détections VIIRS ; passages satellites."""
    out = []
    async with request.app.state.pool.acquire() as c:
        for r in await c.fetch("SELECT task, status, count(*) AS n FROM task_runs GROUP BY 1, 2"):
            out.append(_line("mars_tache_executions_total", r["n"], {"tache": r["task"], "etat": r["status"]}))
        for r in await c.fetch(
                """SELECT DISTINCT ON (task) task, status, (details->>'duree_s')::float8 AS duree,
                          extract(epoch FROM now() - started_at) AS age
                   FROM task_runs ORDER BY task, started_at DESC"""):
            out.append(_line("mars_tache_derniere_duree_secondes", r["duree"], {"tache": r["task"]}))
            out.append(_line("mars_tache_derniere_reussie", r["status"] == "ok", {"tache": r["task"]}))
            out.append(_line("mars_tache_age_secondes", r["age"], {"tache": r["task"]}))
        for r in await c.fetch(
                """SELECT tache, etat, count(*) AS n,
                          sum(extract(epoch FROM coalesce(detruit_le, now()) - cree_le)) / 60 AS minutes,
                          sum((mesures->>'cout_estime_eur')::float8) AS euros
                   FROM travailleurs GROUP BY 1, 2"""):
            lab = {"tache": r["tache"], "etat": r["etat"]}
            out += [_line("mars_travailleurs_total", r["n"], lab), _line("mars_travailleurs_minutes_total", r["minutes"], lab),
                    _line("mars_travailleurs_cout_euros_total", r["euros"], lab)]
        out.append(_line("mars_travailleurs_actifs", await c.fetchval("SELECT count(*) FROM travailleurs WHERE detruit_le IS NULL")))
        for r in await c.fetch(
                f"SELECT {STATUT} AS statut, count(*) AS n FROM viirs_detections d GROUP BY 1"):
            out.append(_line("mars_viirs_detections_total", r["n"], {"statut": r["statut"]}))
        out.append(_line("mars_viirs_granules_total", await c.fetchval("SELECT count(*) FROM viirs_granules")))
        for r in await c.fetch("SELECT mission, statut, count(*) AS n FROM sar_passes WHERE mission IS NOT NULL GROUP BY 1, 2"):
            out.append(_line("mars_passages_total", r["n"], {"mission": r["mission"], "etat": r["statut"]}))
        for r in await c.fetch("SELECT type, status, count(*) AS n FROM alerts GROUP BY 1, 2"):
            out.append(_line("mars_alertes_total", r["n"], {"type": r["type"], "etat": r["status"]}))
    return "\n".join(out) + "\n"

