"""Tip and cue (lot T1) : demandes d'observation satellite. Lecture (fiche d'alerte, fiche navire, barre d'état) et
demande manuelle d'un opérateur (navire, alerte ou point de la carte). La planification, l'analyse et le retour sur
l'alerte sont faits par le conteneur taches (mars/tip_and_cue.py) : l'API n'écrit qu'une demande « en_attente »,
planifiée au cycle suivant (5 minutes au plus)."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from mars import traces
from mars.config import load_rules
from regions import dans_region, region_ewkt

router = APIRouter()

COLS = """d.id, d.cree_le, d.tip, d.priorite, d.vessel_id, v.name AS navire, v.mmsi, d.position_le, d.etat,
          d.expire_le, d.pass_id, p.satellite, d.heure_passage, d.passage_retro, d.rayon_m, d.prediction,
          jsonb_array_length(coalesce(d.fenetres, '[]'::jsonb)) AS fenetres, d.analysis_id, d.resultat,
          d.resultat_details, d.detection_id, d.distance_m, d.motif, d.auteur, d.trace_id, d.fini_le,
          ST_X(d.position::geometry) AS lon, ST_Y(d.position::geometry) AS lat,
          ST_X(d.position_predite::geometry) AS lon_predite, ST_Y(d.position_predite::geometry) AS lat_predite,
          (SELECT coalesce(array_agg(o.alerte_id) FILTER (WHERE o.alerte_id IS NOT NULL), '{}')
           FROM demandes_origines o WHERE o.demande_id = d.id) AS alertes"""
FROM = "FROM demandes_observation d LEFT JOIN vessels v ON v.id = d.vessel_id LEFT JOIN sar_passes p ON p.id = d.pass_id"


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(r) -> dict:
    return {k: _iso(v) for k, v in dict(r).items()}


@router.get("/api/demandes")
async def demandes(request: Request, alerte_id: int | None = None, vessel_id: int | None = None,
                   start: datetime | None = None, end: datetime | None = None, limit: int = 200):
    """Demandes d'observation d'une alerte, d'un navire, ou de la plage (les plus récentes d'abord)."""
    end = end or datetime.now(timezone.utc) + timedelta(days=2)
    start = start or end - timedelta(days=9)
    async with request.app.state.pool.acquire() as c:
        rows = await c.fetch(
            f"""SELECT {COLS} {FROM}
                WHERE ($1::bigint IS NULL OR EXISTS (SELECT 1 FROM demandes_origines o WHERE o.demande_id = d.id
                                                     AND o.alerte_id = $1::bigint))
                  AND ($2::bigint IS NULL OR d.vessel_id = $2::bigint)
                  AND ($1::bigint IS NOT NULL OR $2::bigint IS NOT NULL OR d.cree_le BETWEEN $3 AND $4)
                ORDER BY d.id DESC LIMIT $5::int""", alerte_id, vessel_id, start, end, limit)
    return [_row(r) for r in rows]


# Panneau « Observations » : rang d'affichage (planifiées par passage le plus proche, en attente, analysées les plus
# récentes, puis closes sans résultat repliées en bas)
RANG = """CASE d.etat WHEN 'planifiee' THEN 0 WHEN 'en_attente' THEN 1 WHEN 'analysee' THEN 2 ELSE 3 END"""
ACTIVES = "d.etat IN ('en_attente', 'planifiee')"


@router.get("/api/observations")
async def observations(request: Request, start: datetime | None = None, end: datetime | None = None,
                       region: str | None = None, etat: str | None = None, limit: int = 300):
    """Demandes d'observation pour le panneau du rail : navire (pavillon, nom), alerte d'origine et son type, passage,
    résultat, position prédite et cercle d'incertitude. Les demandes actives sont toujours listées ; les closes,
    seulement si elles ont vécu sur la plage. Région : position prédite, sinon dernière position connue. Ordre : voir
    RANG. `actives` : demandes en attente ou planifiées de la région (compteur de l'icône)."""
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=24)
    geo = "coalesce(d.position_predite, d.position)::geometry"
    async with request.app.state.pool.acquire() as c:
        ewkt = await region_ewkt(c, region)
        rows = await c.fetch(
            f"""SELECT d.id, d.tip, d.etat, d.vessel_id, v.name AS navire, v.flag, v.mmsi, d.heure_passage, d.resultat,
                       d.rayon_m, d.cree_le, d.fini_le, d.motif, p.satellite, {RANG} AS rang,
                       ST_X({geo}) AS lon, ST_Y({geo}) AS lat, o.alerte_id, a.type AS alerte_type
                FROM demandes_observation d
                LEFT JOIN vessels v ON v.id = d.vessel_id LEFT JOIN sar_passes p ON p.id = d.pass_id
                LEFT JOIN LATERAL (SELECT alerte_id FROM demandes_origines WHERE demande_id = d.id
                                   AND alerte_id IS NOT NULL ORDER BY priorite DESC, cree_le LIMIT 1) o ON true
                LEFT JOIN alerts a ON a.id = o.alerte_id
                WHERE ({ACTIVES} OR (d.cree_le <= $2 AND coalesce(d.fini_le, d.mis_a_jour_le) >= $1))
                  AND {dans_region(geo, 3)} AND ($4::text IS NULL OR d.etat = $4::text)
                ORDER BY rang, CASE WHEN d.etat = 'planifiee' THEN d.heure_passage END ASC NULLS LAST,
                         coalesce(d.fini_le, d.cree_le) DESC, d.id DESC
                LIMIT $5::int""", start, end, ewkt, etat, limit)
        actives = await c.fetchval(f"SELECT count(*) FROM demandes_observation d WHERE {ACTIVES} AND {dans_region(geo, 1)}",
                                   ewkt)
    traces.attributs(**{"observations.lignes": len(rows), "observations.actives": actives, "observations.region": region})
    return {"actives": actives, "demandes": [_row(r) for r in rows]}


@router.get("/api/demandes/{did}")
async def demande(request: Request, did: int):
    async with request.app.state.pool.acquire() as c:
        r = await c.fetchrow(f"SELECT {COLS} {FROM} WHERE d.id = $1", did)
    if r is None:
        raise HTTPException(404, "Demande inconnue")
    return _row(r)


class NouvelleDemande(BaseModel):
    vessel_id: int | None = None
    alerte_id: int | None = None
    lon: float | None = None
    lat: float | None = None
    author: str = "Opérateur"


@router.post("/api/demandes")
async def demander(request: Request, n: NouvelleDemande):
    """Demande manuelle d'un opérateur : navire (dernière position), alerte (son navire, sinon son point) ou point de
    la carte. Rattachée à la demande active du même navire s'il y en a une. Refusée tant que le tip and cue est coupé
    (config/rules.yaml, tip_and_cue.actif)."""
    rules = load_rules()
    tc = rules.get("tip_and_cue") or {}
    if not tc.get("actif") or not (tc.get("tips", {}).get("operateur") or {}).get("actif"):
        raise HTTPException(409, "Tip and cue coupé (config/rules.yaml, tip_and_cue.actif)")
    auteur = (n.author or "").strip() or "Opérateur"
    async with request.app.state.pool.acquire() as c, c.transaction():
        if await c.fetchval("SELECT source_en_pause('tip_and_cue')"):
            raise HTTPException(409, "Tip and cue en pause (panneau de contrôle)")
        vid, lon, lat, ts, sog, cog, alerte = n.vessel_id, n.lon, n.lat, None, None, None, n.alerte_id
        if alerte is not None:
            a = await c.fetchrow("SELECT ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat, event_time, "
                                 "(SELECT min(e.evidence_id) FROM alert_evidence e WHERE e.alert_id = al.id AND "
                                 "e.evidence_type = 'vessel') AS vessel_id FROM alerts al WHERE al.id = $1", alerte)
            if a is None:
                raise HTTPException(404, "Alerte inconnue")
            vid, lon, lat, ts = vid or a["vessel_id"], a["lon"], a["lat"], a["event_time"]
        if vid is not None:
            p = await c.fetchrow("SELECT ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat, ts, sog_kn, cog_deg "
                                 "FROM positions WHERE vessel_id = $1 ORDER BY ts DESC LIMIT 1", vid)
            if p is None and lon is None:
                raise HTTPException(404, "Aucune position connue pour ce navire")
            if p is not None:
                lon, lat, ts, sog, cog = p["lon"], p["lat"], p["ts"], p["sog_kn"], p["cog_deg"]
        if lon is None or lat is None:
            raise HTTPException(422, "Préciser un navire, une alerte ou un point (lon, lat)")
        now = datetime.now(timezone.utc)
        prio = int(tc["tips"]["operateur"]["priorite"])
        existante = await c.fetchval("SELECT id FROM demandes_observation WHERE vessel_id = $1 AND etat IN "
                                     "('en_attente', 'planifiee') ORDER BY id DESC LIMIT 1", vid) if vid else None
        if existante:
            did = existante
            await c.execute("UPDATE demandes_observation SET priorite = greatest(priorite, $2), mis_a_jour_le = now() "
                            "WHERE id = $1", did, prio)
        else:
            did = await c.fetchval(
                """INSERT INTO demandes_observation (tip, priorite, vessel_id, position, position_le, sog_kn, cog_deg,
                                                     expire_le, motif, auteur, trace_id, traceparent)
                   VALUES ('operateur', $1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography, $5, $6, $7, $8,
                           'demande manuelle', $9, $10, $11) RETURNING id""",
                prio, vid, lon, lat, ts or now, sog, cog, now + timedelta(hours=tc["expiration_h"]), auteur,
                traces.trace_id(), traces.traceparent())
        await c.execute("INSERT INTO demandes_origines (cle, demande_id, tip, priorite, alerte_id, motif, trace_id) "
                        "VALUES ($1, $2, 'operateur', $3, $4, $5, $6)", f"operateur:{uuid.uuid4().hex}", did, prio,
                        alerte, f"demande de {auteur}", traces.trace_id())
        traces.attributs(**{"tip.demande": did, "tip.regroupee": bool(existante), "tip.navire": vid,
                            "tip.alerte": alerte})
    return {"id": did, "regroupee": bool(existante), "etat": "en_attente"}


@router.post("/api/demandes/{did}/annuler")
async def annuler(request: Request, did: int):
    async with request.app.state.pool.acquire() as c:
        etat = await c.fetchval(
            "UPDATE demandes_observation SET etat = 'annulee', fini_le = now(), mis_a_jour_le = now() "
            "WHERE id = $1 AND etat IN ('en_attente', 'planifiee') AND analysis_id IS NULL RETURNING etat", did)
    if etat is None:
        raise HTTPException(409, "Demande déjà analysée, close ou portée par une analyse en cours")
    return {"id": did, "etat": etat}


async def demandes_status(c) -> dict:
    """Barre d'état (indicateur Satellites) : demandes en attente ou planifiées, prochaine observation prévue."""
    tc = load_rules().get("tip_and_cue") or {}
    n = await c.fetchrow("SELECT count(*) FILTER (WHERE etat = 'en_attente') AS en_attente, "
                         "count(*) FILTER (WHERE etat = 'planifiee') AS planifiees FROM demandes_observation "
                         "WHERE etat IN ('en_attente', 'planifiee')")
    nxt = await c.fetchrow(
        "SELECT d.id, d.heure_passage, p.satellite, v.name AS navire, d.tip FROM demandes_observation d "
        "LEFT JOIN sar_passes p ON p.id = d.pass_id LEFT JOIN vessels v ON v.id = d.vessel_id "
        "WHERE d.etat = 'planifiee' AND d.heure_passage > now() ORDER BY d.heure_passage LIMIT 1")
    return {"actif": bool(tc.get("actif")), "en_attente": n["en_attente"], "planifiees": n["planifiees"],
            "prochaine": _row(nxt) if nxt else None}
