"""Calendrier des passages Sentinel 1 et 2 (étape 3, lot A) : emprises sur la plage et la région, fiche d'un passage
(infrastructures et navires des listes couverts, état de l'analyse), prochain passage pour la barre d'état.

Les passages sont tenus chaque jour par le conteneur taches (mars/satellites.py) ; ces routes ne font que lire.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

COLS = """s.id, s.mission, s.satellite, s.mode, s.orbit_direction, s.relative_orbit, s.absolute_orbit,
          s.acquired_at, s.ended_at, s.statut, s.source, s.produits, s.regions, s.nuages,
          cardinality(coalesce(s.infra_ids, '{}')) AS n_infra, cardinality(coalesce(s.watch_ids, '{}')) AS n_listes,
          (SELECT a.status FROM analyses a WHERE a.pass_id = s.id ORDER BY a.id DESC LIMIT 1) AS analyse"""


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _props(r) -> dict:
    d = {k: _iso(v) for k, v in dict(r).items() if k != "geometry"}
    d["analyse"] = d.get("analyse") or "non_analyse"
    return d


@router.get("/api/satellites/passes")
async def passes(request: Request, start: datetime | None = None, end: datetime | None = None,
                 region: str | None = None, limit: int = 500):
    """Passages acquis ou prévus qui chevauchent [start, end] (par défaut : les dernières 24 heures et les 24
    suivantes), dans la région si elle est donnée. Emprises simplifiées à environ 1 km, coordonnées à 4 décimales."""
    now = datetime.now(timezone.utc)
    start, end = start or now - timedelta(hours=24), end or now + timedelta(hours=24)
    async with request.app.state.pool.acquire() as c:
        rows = await c.fetch(
            f"""SELECT {COLS}, ST_AsGeoJSON(s.footprint, 4)::json AS geometry FROM sar_passes s
                WHERE s.mission IS NOT NULL AND s.acquired_at <= $2::timestamptz
                  AND coalesce(s.ended_at, s.acquired_at) >= $1::timestamptz
                  AND ($3::text IS NULL OR $3::text = ANY(s.regions))
                ORDER BY s.acquired_at LIMIT $4::int""", start, end, region.lower() if region else None, limit)
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": r["geometry"], "properties": _props(r)} for r in rows]}


@router.get("/api/satellites/passes/{pass_id}")
async def pass_card(request: Request, pass_id: int):
    """Fiche d'un passage : heure, emprise, infrastructures et navires des listes couverts, analyses."""
    async with request.app.state.pool.acquire() as c:
        r = await c.fetchrow(
            f"""SELECT {COLS}, s.infra_ids, s.watch_ids, s.couverture_le, ST_AsGeoJSON(s.footprint, 4)::json AS geometry,
                       ST_XMin(g) AS x0, ST_YMin(g) AS y0, ST_XMax(g) AS x1, ST_YMax(g) AS y1,
                       round((ST_Area(s.footprint) / 1e6)::numeric)::int AS surface_km2
                FROM sar_passes s, LATERAL (SELECT s.footprint::geometry AS g) b
                WHERE s.id = $1 AND s.mission IS NOT NULL""", pass_id)
        if r is None:
            raise HTTPException(404, "Passage inconnu")
        infra = await c.fetch(
            "SELECT i.id, i.name, i.kind, i.attrs->>'type' AS type, r.name AS region FROM infrastructure i "
            "JOIN regions r ON r.id = i.region_id WHERE i.id = ANY($1::int[]) ORDER BY i.attrs->>'type', i.name",
            r["infra_ids"] or [])
        vessels = await c.fetch(
            "SELECT v.id AS vessel_id, v.mmsi, v.name, v.flag, w.level AS watch FROM vessels v "
            "LEFT JOIN vessel_watch w ON w.vessel_id = v.id WHERE v.id = ANY($1::bigint[]) ORDER BY w.rank, v.name",
            r["watch_ids"] or [])
        analyses = await c.fetch("SELECT id, status, completed_at FROM analyses WHERE pass_id = $1 ORDER BY id DESC", pass_id)
    props = {k: v for k, v in _props(r).items() if k not in ("infra_ids", "watch_ids", "x0", "y0", "x1", "y1")}
    return {**props, "geometry": r["geometry"], "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]],
            "infrastructures": [dict(x) for x in infra], "navires": [dict(x) for x in vessels],
            "analyses": [{k: _iso(v) for k, v in dict(a).items()} for a in analyses]}


async def next_passes(c, region: str | None) -> dict:
    """Barre d'état : prochain passage prévu de chaque mission sur la région affichée, dernier passage acquis, et
    date de la dernière mise à jour du calendrier."""
    reg = region.lower() if region else None
    rows = await c.fetch(
        """SELECT DISTINCT ON (s.mission) s.id, s.mission, s.satellite, s.mode, s.acquired_at, s.statut,
                  cardinality(coalesce(s.infra_ids, '{}')) AS n_infra
           FROM sar_passes s
           WHERE s.mission IS NOT NULL AND s.acquired_at > now() AND ($1::text IS NULL OR $1::text = ANY(s.regions))
           ORDER BY s.mission, s.acquired_at""", reg)
    last = await c.fetchrow(
        """SELECT s.id, s.mission, s.satellite, s.acquired_at FROM sar_passes s
           WHERE s.mission IS NOT NULL AND s.statut = 'acquis' AND ($1::text IS NULL OR $1::text = ANY(s.regions))
           ORDER BY s.acquired_at DESC LIMIT 1""", reg)
    run = await c.fetchrow("SELECT status, started_at FROM task_runs WHERE task = 'passages' ORDER BY started_at DESC LIMIT 1")
    return {"prochains": [{k: _iso(v) for k, v in dict(x).items()} for x in rows],
            "dernier": {k: _iso(v) for k, v in dict(last).items()} if last else None,
            "mise_a_jour": {"statut": run["status"], "le": _iso(run["started_at"])} if run else None}
