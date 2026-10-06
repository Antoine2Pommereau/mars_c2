"""Routes du contenu de l'interface (lot 2 de docs/vision_interface.md) : recherche, fiches navire (comportement,
trajectoire en GPX, notes, photo), alerte, infrastructure et zone, navires suivis.

Sobriété : aucune écriture sur le disque du serveur (la photo est relayée, gardée en mémoire pour quelques navires,
puis par le navigateur) ; requêtes bornées par la plage de temps et par des limites de résultats.
"""
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from mars.ais.live import ZONES
from mars.config import load_rules

router = APIRouter()
DEUX_MILLES_M = 3704
ARRET_KN = 1.0            # arrêt : vitesse sous 1 nœud
ARRET_MIN = 30            # pendant au moins 30 minutes
PASSAGE_GAP_MIN = 30      # deux points proches d'une infrastructure séparés de plus de 30 min : deux passages


def pool(request: Request):
    return request.app.state.pool


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(r) -> dict:
    return {k: _iso(v) for k, v in dict(r).items()}


def _unique(rows: list[dict], key) -> list[dict]:
    """Les régions Bretagne et Manche se chevauchent : un tracé EMODnet commun y est stocké deux fois. On ne garde
    qu'une ligne par clé (type, nom, emprise ou instants)."""
    seen, out = set(), []
    for r in rows:
        k = key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    end = end or datetime.now(timezone.utc)
    return start or end - timedelta(hours=24), end


# Recherche

@router.get("/api/search")
async def search(request: Request, q: str, limit: int = 8):
    """Recherche dans les navires (nom actuel ou ancien, MMSI, OMI : un OMI retrouve tous les MMSI successifs),
    les infrastructures (nom) et les alertes (numéro). Les lieux (ports, zones) sont cherchés par l'interface."""
    q = q.strip()
    if len(q) < 2 and not q.isdigit():
        return {"navires": [], "infrastructures": [], "alertes": []}
    digits = q.isdigit()
    like = f"%{q}%"
    async with pool(request).acquire() as c:
        # Navires : par MMSI (exact, ou début si au moins 4 chiffres), OMI exact (vue imo_history), nom actuel, nom ancien
        vessels = await c.fetch(
            """WITH hits AS (
                 SELECT v.id, 'mmsi' AS par, NULL::text AS ancien FROM vessels v
                 WHERE $1::bool AND (v.mmsi::text = $2 OR (length($2) >= 4 AND v.mmsi::text LIKE $2 || '%'))
                 UNION ALL
                 SELECT DISTINCT h.vessel_id, 'omi', NULL FROM imo_history h WHERE $1::bool AND h.imo::text = $2
                 UNION ALL
                 SELECT v.id, 'omi', NULL FROM vessels v WHERE $1::bool AND v.imo::text = $2
                 UNION ALL
                 SELECT v.id, 'nom', NULL FROM vessels v WHERE v.name ILIKE $3
                 UNION ALL
                 SELECT i.vessel_id, 'ancien_nom', i.name FROM vessel_identities i JOIN vessels v ON v.id = i.vessel_id
                 WHERE i.name ILIKE $3 AND i.name IS DISTINCT FROM v.name)
               SELECT DISTINCT ON (v.id) v.id AS vessel_id, v.mmsi, v.imo, v.name, v.flag, v.ship_type, h.par, h.ancien,
                      w.level AS watch, v.last_seen, ST_X(p.geom::geometry) AS lon, ST_Y(p.geom::geometry) AS lat
               FROM hits h JOIN vessels v ON v.id = h.id
               LEFT JOIN vessel_watch w ON w.vessel_id = v.id
               LEFT JOIN LATERAL (SELECT geom FROM positions WHERE vessel_id = v.id ORDER BY ts DESC LIMIT 1) p ON true
               ORDER BY v.id, CASE h.par WHEN 'mmsi' THEN 0 WHEN 'omi' THEN 1 WHEN 'nom' THEN 2 ELSE 3 END
               LIMIT $4::int""", digits, q, like, limit * 3)
        vessels = sorted(vessels, key=lambda r: (r["lon"] is None, -(r["last_seen"].timestamp() if r["last_seen"] else 0)))[:limit]
        infra = await c.fetch(
            """SELECT i.id, i.name, i.kind, i.attrs->>'type' AS type, i.operator, r.name AS region,
                      ST_XMin(e) AS x0, ST_YMin(e) AS y0, ST_XMax(e) AS x1, ST_YMax(e) AS y1
               FROM infrastructure i JOIN regions r ON r.id = i.region_id,
                    LATERAL (SELECT ST_Envelope(i.geom::geometry) AS e) b
               WHERE i.name ILIKE $1 ORDER BY i.name LIMIT $2::int""", like, limit)
        alerts = await c.fetch(
            """SELECT id, type, severity, status, event_time, ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat
               FROM alerts WHERE $1::bool AND id::text = $2""", digits, q)
    infra = _unique([{**_row(r), "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]]} for r in infra],
                    lambda r: (r["name"], r["type"], tuple(round(v, 2) for v in r["bbox"])))
    return {"navires": [_row(r) for r in vessels], "infrastructures": infra,
            "alertes": [_row(r) for r in alerts]}


# Fiche navire : comportement, trajectoire GPX, notes, photo

@router.get("/api/vessels/{vessel_id}/comportement")
async def behaviour(request: Request, vessel_id: int, start: datetime | None = None, end: datetime | None = None):
    """Comportement d'un navire sur la plage : silences de plus de deux heures, arrêts au large (sous 1 nœud pendant
    30 minutes, à plus de 3 km des côtes), passages à moins de 2 milles d'une infrastructure (nom, durée, vitesse
    minimale). Les rendez vous et les coupures retenues par les règles sont dans les alertes du navire."""
    start, end = _range(start, end)
    rules = load_rules()
    gap_min, coast_m = rules["ais_gap"]["min_gap_min"], rules["ais_gap"]["min_coast_km"] * 1000.0
    async with pool(request).acquire() as c:
        silences = await c.fetch(
            """SELECT ts AS debut, nxt AS fin, extract(epoch FROM nxt - ts)::int / 60 AS duree_min
               FROM (SELECT ts, lead(ts) OVER (ORDER BY ts) AS nxt FROM positions
                     WHERE vessel_id = $1 AND ts BETWEEN $2 AND $3) s
               WHERE nxt - ts >= make_interval(mins => $4::int) ORDER BY ts""", vessel_id, start, end, gap_min)
        stops = await c.fetch(
            """WITH p AS (SELECT ts, geom, coalesce(sog_kn, 0) < $4::float8 AS lent,
                                 lag(coalesce(sog_kn, 0) < $4::float8) OVER (ORDER BY ts) AS avant
                          FROM positions WHERE vessel_id = $1 AND ts BETWEEN $2 AND $3),
                    g AS (SELECT *, sum(CASE WHEN lent AND avant IS NOT TRUE THEN 1 ELSE 0 END) OVER (ORDER BY ts) AS ep FROM p),
                    e AS (SELECT ep, min(ts) AS debut, max(ts) AS fin, ST_Centroid(ST_Collect(geom::geometry)) AS c
                          FROM g WHERE lent GROUP BY ep)
               SELECT debut, fin, extract(epoch FROM fin - debut)::int / 60 AS duree_min, ST_X(c) AS lon, ST_Y(c) AS lat
               FROM e WHERE fin - debut >= make_interval(mins => $5::int)
                 AND NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, c::geography, $6::float8))
               ORDER BY debut""", vessel_id, start, end, ARRET_KN, ARRET_MIN, coast_m)
        passages = await c.fetch(
            """WITH near AS (
                 SELECT i.id AS infra_id, i.name, i.attrs->>'type' AS type, p.ts, p.sog_kn, ST_Distance(p.geom, i.geom) AS d
                 FROM positions p JOIN infrastructure i ON ST_DWithin(i.geom, p.geom, $4::float8)
                 WHERE p.vessel_id = $1 AND p.ts BETWEEN $2 AND $3),
               l AS (SELECT *, lag(ts) OVER (PARTITION BY infra_id ORDER BY ts) AS prev FROM near),
               g AS (SELECT *, sum(CASE WHEN prev IS NULL OR ts - prev > make_interval(mins => $5::int) THEN 1 ELSE 0 END)
                               OVER (PARTITION BY infra_id ORDER BY ts) AS ep FROM l)
               SELECT infra_id, name, type, min(ts) AS debut, max(ts) AS fin,
                      extract(epoch FROM max(ts) - min(ts))::int / 60 AS duree_min,
                      round(min(sog_kn)::numeric, 1)::float8 AS vitesse_min, round(min(d))::int AS distance_min_m
               FROM g GROUP BY infra_id, name, type, ep ORDER BY min(ts) LIMIT 50""",
            vessel_id, start, end, float(DEUX_MILLES_M), PASSAGE_GAP_MIN)
    return {"silences": [_row(r) for r in silences], "arrets": [_row(r) for r in stops],
            "passages_infra": _unique([_row(r) for r in passages],
                                      lambda r: (r["type"], r["name"], r["debut"], r["fin"], r["distance_min_m"]))}


@router.get("/api/vessels/{vessel_id}/track.gpx")
async def track_gpx(request: Request, vessel_id: int, start: datetime, end: datetime):
    """Trajectoire au format GPX (20 000 points au plus), pour un logiciel de cartographie ou un service partenaire."""
    async with pool(request).acquire() as c:
        v = await c.fetchrow("SELECT mmsi, name FROM vessels WHERE id = $1", vessel_id)
        if v is None:
            raise HTTPException(404, "Navire inconnu")
        rows = await c.fetch("SELECT ts, ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat, sog_kn, cog_deg "
                             "FROM positions WHERE vessel_id = $1 AND ts BETWEEN $2 AND $3 ORDER BY ts LIMIT 20000",
                             vessel_id, start, end)
    name = escape(v["name"] or str(v["mmsi"]))
    pts = "".join(f'<trkpt lat="{r["lat"]:.6f}" lon="{r["lon"]:.6f}"><time>{r["ts"].astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}</time></trkpt>'
                  for r in rows)
    gpx = (f'<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="MARS C2" xmlns="http://www.topografix.com/GPX/1/1">'
           f"<trk><name>{name} (MMSI {v['mmsi']})</name><trkseg>{pts}</trkseg></trk></gpx>")
    fichier = f"{v['mmsi']}_{start:%Y%m%d%H%M}_{end:%Y%m%d%H%M}.gpx"
    return Response(gpx, media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="{fichier}"'})


class Note(BaseModel):
    note: str
    author: str = "Opérateur"


@router.get("/api/vessels/{vessel_id}/notes")
async def notes(request: Request, vessel_id: int):
    async with pool(request).acquire() as c:
        rows = await c.fetch("SELECT id, note, author, at FROM vessel_notes WHERE vessel_id = $1 ORDER BY at DESC", vessel_id)
    return [_row(r) for r in rows]


@router.post("/api/vessels/{vessel_id}/notes")
async def add_note(request: Request, vessel_id: int, n: Note):
    note = n.note.strip()
    if not note:
        raise HTTPException(422, "Note vide")
    async with pool(request).acquire() as c:
        try:
            r = await c.fetchrow("INSERT INTO vessel_notes (vessel_id, note, author) VALUES ($1, $2, $3) "
                                 "RETURNING id, note, author, at", vessel_id, note, (n.author or "").strip() or "Opérateur")
        except Exception:
            raise HTTPException(404, "Navire inconnu")
    return _row(r)


# Photo : relayée depuis la fiche publique VesselFinder (source indiquée sous l'image dans l'interface). Aucune
# écriture sur le disque du serveur : quelques dizaines de photos en mémoire, puis le cache du navigateur.
# PHOTOS=aucune la désactive (conditions d'utilisation de la source à vérifier avant une démonstration publique).
PHOTOS = os.environ.get("PHOTOS", "vesselfinder")
_PAGE = "https://www.vesselfinder.com/vessels/details/{mmsi}"
_IMAGE = re.compile(r"https://static\.vesselfinder\.net/ship-photo/[^\"'\s]+")
_CACHE: "OrderedDict[int, tuple[float, bytes | None]]" = OrderedDict()
_CACHE_MAX, _ABSENT_TTL = 40, 24 * 3600


@router.get("/api/vessels/{vessel_id}/photo")
async def photo(request: Request, vessel_id: int):
    if PHOTOS != "vesselfinder":
        raise HTTPException(404, "Photos désactivées")
    async with pool(request).acquire() as c:
        mmsi = await c.fetchval("SELECT mmsi FROM vessels WHERE id = $1", vessel_id)
    if mmsi is None or not 100_000_000 <= mmsi <= 999_999_999:
        raise HTTPException(404, "Navire inconnu")
    hit = _CACHE.get(mmsi)
    if hit and (hit[1] is not None or time.time() - hit[0] < _ABSENT_TTL):
        _CACHE.move_to_end(mmsi)
        if hit[1] is None:
            raise HTTPException(404, "Pas de photo pour ce navire")
        return Response(hit[1], media_type="image/jpeg", headers={"Cache-Control": "max-age=86400", "X-Source": "VesselFinder"})
    data = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (MARS C2)"}) as client:
            page = await client.get(_PAGE.format(mmsi=mmsi))
            m = _IMAGE.search(page.text) if page.status_code == 200 else None
            if m:
                r = await client.get(m.group(0))
                # Une vraie image : un corps minuscule trahit un pixel ou une page d'erreur
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") and len(r.content) > 2048:
                    data = r.content
    except httpx.HTTPError:
        raise HTTPException(503, "Source de photo injoignable")
    _CACHE[mmsi] = (time.time(), data)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    if data is None:
        raise HTTPException(404, "Pas de photo pour ce navire")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400", "X-Source": "VesselFinder"})


# Navires suivis

class Suivi(BaseModel):
    vessel_id: int
    author: str = "Opérateur"


@router.get("/api/suivis")
async def followed(request: Request):
    """Navires suivis, avec leur dernière position et leur dernière alerte."""
    async with pool(request).acquire() as c:
        rows = await c.fetch(
            """SELECT f.vessel_id, f.since, f.author, v.mmsi, v.name, v.flag, v.ship_type, w.level AS watch,
                      p.ts AS derniere_position, ST_X(p.geom::geometry) AS lon, ST_Y(p.geom::geometry) AS lat,
                      a.id AS alerte_id, a.type AS alerte_type, a.event_time AS alerte_le, a.status AS alerte_statut
               FROM followed_vessels f JOIN vessels v ON v.id = f.vessel_id
               LEFT JOIN vessel_watch w ON w.vessel_id = f.vessel_id
               LEFT JOIN LATERAL (SELECT ts, geom FROM positions WHERE vessel_id = f.vessel_id ORDER BY ts DESC LIMIT 1) p ON true
               LEFT JOIN LATERAL (SELECT al.id, al.type, al.event_time, al.status FROM alerts al
                                  JOIN alert_evidence e ON e.alert_id = al.id
                                  WHERE e.evidence_type = 'vessel' AND e.evidence_id = f.vessel_id
                                  ORDER BY al.event_time DESC LIMIT 1) a ON true
               ORDER BY f.since DESC""")
    return [_row(r) for r in rows]


@router.post("/api/suivis")
async def follow(request: Request, s: Suivi):
    async with pool(request).acquire() as c:
        if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM vessels WHERE id = $1)", s.vessel_id):
            raise HTTPException(404, "Navire inconnu")
        await c.execute("INSERT INTO followed_vessels (vessel_id, author) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        s.vessel_id, (s.author or "").strip() or "Opérateur")
    return {"vessel_id": s.vessel_id, "suivi": True}


@router.delete("/api/suivis/{vessel_id}")
async def unfollow(request: Request, vessel_id: int):
    async with pool(request).acquire() as c:
        await c.execute("DELETE FROM followed_vessels WHERE vessel_id = $1", vessel_id)
    return {"vessel_id": vessel_id, "suivi": False}


# Fiches alerte, infrastructure et zone

@router.get("/api/alerts/{alert_id}")
async def alert(request: Request, alert_id: int):
    """Une alerte par son numéro (recherche, lien partagé hors de la plage affichée)."""
    async with pool(request).acquire() as c:
        r = await c.fetchrow(
            """SELECT al.id, al.type, al.severity, al.status, al.event_time, al.detected_at, al.details, al.rule_version,
                      ST_AsGeoJSON(al.geom, 5)::json AS geometry,
                      coalesce((SELECT array_agg(e.evidence_id) FROM alert_evidence e
                                WHERE e.alert_id = al.id AND e.evidence_type = 'vessel'), '{}') AS vessel_ids
               FROM alerts al WHERE al.id = $1""", alert_id)
    if r is None:
        raise HTTPException(404, "Alerte inconnue")
    props = {k: _iso(v) for k, v in dict(r).items() if k != "geometry"}
    return {"type": "Feature", "geometry": r["geometry"], "properties": props}


@router.get("/api/infrastructure/{infra_id}")
async def infrastructure_card(request: Request, infra_id: int, start: datetime | None = None, end: datetime | None = None):
    """Fiche d'une infrastructure : type, opérateur, longueur, zone ; navires passés à moins de 2 milles sur la plage
    (50 au plus, du plus proche au plus lointain) ; alertes liées. Le tracé est découpé en morceaux courts pour que
    l'index spatial des positions reste efficace sur un câble de plusieurs centaines de kilomètres."""
    start, end = _range(start, end)
    async with pool(request).acquire() as c:
        i = await c.fetchrow(
            """SELECT i.id, i.kind, i.name, i.operator, i.status, i.source, i.attrs->>'type' AS type, r.name AS region,
                      round((CASE WHEN i.kind = 'windfarm' THEN ST_Perimeter(i.geom) ELSE ST_Length(i.geom) END
                             / 1000)::numeric, 1)::float8 AS longueur_km,
                      ST_XMin(e) AS x0, ST_YMin(e) AS y0, ST_XMax(e) AS x1, ST_YMax(e) AS y1
               FROM infrastructure i JOIN regions r ON r.id = i.region_id,
                    LATERAL (SELECT ST_Envelope(i.geom::geometry) AS e) b WHERE i.id = $1""", infra_id)
        if i is None:
            raise HTTPException(404, "Infrastructure inconnue")
        async with c.transaction():
            await c.execute("SET LOCAL statement_timeout = '15s'")
            vessels = await c.fetch(
                """WITH seg AS (SELECT ST_Subdivide(geom::geometry, 32)::geography AS g FROM infrastructure WHERE id = $1),
                        near AS (SELECT p.vessel_id, p.ts, p.sog_kn, min(ST_Distance(p.geom, s.g)) AS d
                                 FROM seg s JOIN positions p ON ST_DWithin(p.geom, s.g, $4::float8)
                                 WHERE p.ts BETWEEN $2 AND $3 GROUP BY p.vessel_id, p.ts, p.sog_kn)
                   SELECT n.vessel_id, v.mmsi, v.name, v.flag, v.ship_type, w.level AS watch, min(n.ts) AS debut,
                          max(n.ts) AS fin, round(min(n.d))::int AS distance_min_m,
                          round(min(n.sog_kn)::numeric, 1)::float8 AS vitesse_min, count(*) AS positions
                   FROM near n JOIN vessels v ON v.id = n.vessel_id LEFT JOIN vessel_watch w ON w.vessel_id = n.vessel_id
                   GROUP BY n.vessel_id, v.mmsi, v.name, v.flag, v.ship_type, w.level
                   ORDER BY min(n.d) LIMIT 50""", infra_id, start, end, float(DEUX_MILLES_M))
        alerts = await c.fetch(
            "SELECT id, type, severity, status, event_time FROM alerts "
            "WHERE (details->'infrastructure'->>'id')::int = $1 ORDER BY event_time DESC LIMIT 20", infra_id)
    return {**{k: _iso(v) for k, v in dict(i).items() if k not in ("x0", "y0", "x1", "y1")},
            "bbox": [i["x0"], i["y0"], i["x1"], i["y1"]], "navires": [_row(r) for r in vessels],
            "alertes": [_row(r) for r in alerts]}


@router.get("/api/zones/{key}")
async def zone_card(request: Request, key: str):
    """Fiche d'une zone collectée : emprise, réception fiable (cellules, surface, continuité), mouillages connus. Le
    trafic et les alertes de la zone sont comptés par l'interface sur ce qu'elle affiche."""
    if key not in ZONES:
        raise HTTPException(404, "Zone inconnue")
    a, b, c_, d = ZONES[key]
    env = f"SRID=4326;POLYGON(({b} {a},{d} {a},{d} {c_},{b} {c_},{b} {a}))"
    async with pool(request).acquire() as c:
        rec = await c.fetchrow(
            "SELECT count(*) AS cellules, round((coalesce(sum(ST_Area(geom)), 0) / 1e6)::numeric)::int AS surface_km2, "
            "round(avg(coverage)::numeric, 3)::float8 AS continuite FROM reception_cells WHERE ST_Intersects(geom, $1::geography)", env)
        moor = await c.fetchval("SELECT count(*) FROM stationary_zones WHERE ST_Intersects(geom, $1::geography)", env)
        area = await c.fetchval("SELECT round((ST_Area($1::geography) / 1e6)::numeric)::int", env)
    return {"zone": key, "bbox": [b, a, d, c_], "surface_km2": area, "reception": _row(rec), "mouillages": moor}
