"""Sentinel 1 (étape 3, lot C) : détections radar analysées par le travailleur GPU, fiche d'une détection,
vérification satellite d'un navire, état pour la barre d'état et mesures. Le traitement est fait par le conteneur
taches (mars/sentinel.py) ; l'API ne fait que lire."""
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, HTTPException, Request

from preuves import ais_near, proof_of
from regions import dans_region, region_ewkt

router = APIRouter()

STATUT = ("CASE WHEN d.mask_reason = 'non_evaluable' THEN 'non_evaluable' WHEN d.mask_reason IS NOT NULL THEN 'ecartee' "
          "WHEN d.matched_vessel_id IS NOT NULL THEN 'avec_ais' ELSE 'sans_ais' END")
VIGNETTE_RAYON_M = 2000      # AIS superposés à la vignette radar (environ 3 km de côté)


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(r) -> dict:
    return {k: _iso(v) for k, v in dict(r).items()}


@router.get("/api/sentinel/detections")
async def detections(request: Request, start: datetime | None = None, end: datetime | None = None,
                     region: str | None = None, limit: int = 5000):
    """Détections Sentinel 1 de la plage (couche « Détections radar et optiques ») : statut, longueur, navire apparié."""
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=24)
    async with request.app.state.pool.acquire() as c:
        rows = await c.fetch(
            f"""SELECT d.id, d.ts, {STATUT} AS statut, d.mask_reason, round(d.length_m)::int AS longueur_m,
                       round(d.heading_deg)::int AS cap_deg, p.satellite, v.name AS navire,
                       ST_AsGeoJSON(d.geom, 5)::json AS geometry
                FROM detections d JOIN analyses a ON a.id = d.analysis_id AND a.source = 'sentinel1'
                JOIN sar_passes p ON p.id = a.pass_id LEFT JOIN vessels v ON v.id = d.matched_vessel_id
                WHERE d.ts BETWEEN $1 AND $2 AND {dans_region('d.geom::geometry', 3)}
                ORDER BY d.ts LIMIT $4::int""", start, end, await region_ewkt(c, region), limit)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": r["geometry"], "properties": {k: _iso(v) for k, v in dict(r).items() if k != "geometry"}}
        for r in rows]}


@router.get("/api/sentinel/detections/{did}")
async def detection(request: Request, did: int):
    """Fiche « détection satellite » Sentinel 1 : attributs mesurés (longueur, largeur, cap, score, contraste), navire
    apparié et longueur déclarée, évaluation, alertes liées, vignette radar et AIS proches à l'heure de la scène."""
    async with request.app.state.pool.acquire() as c:
        r = await c.fetchrow(
            f"""SELECT d.id, d.ts, {STATUT} AS statut, d.mask_reason, d.non_evaluable, d.score, d.length_m, d.width_m,
                       d.heading_deg, d.heading_prob, d.speed_kn, d.fishing_score, d.contrast_vv_db, d.distance_cote_m,
                       d.ais_navires_rayon, d.scene, d.match_cost, a.id AS analyse, p.id AS pass_id, p.satellite,
                       p.orbit_direction, ST_X(d.geom::geometry) AS lon, ST_Y(d.geom::geometry) AS lat,
                       v.id AS vessel_id, v.name, v.mmsi, v.flag, v.length_m AS longueur_declaree_m, w.level AS watch
                FROM detections d JOIN analyses a ON a.id = d.analysis_id AND a.source = 'sentinel1'
                JOIN sar_passes p ON p.id = a.pass_id
                LEFT JOIN vessels v ON v.id = d.matched_vessel_id LEFT JOIN vessel_watch w ON w.vessel_id = v.id
                WHERE d.id = $1""", did)
        if r is None:
            raise HTTPException(404, "Détection inconnue")
        alerts = await c.fetch(
            """SELECT a.id, a.type, a.severity, a.status, a.event_time FROM alerts a
               JOIN alert_evidence e ON e.alert_id = a.id AND e.evidence_type = 'detection' AND e.evidence_id = $1
               ORDER BY a.event_time DESC""", did)
        preuve = await proof_of(c, "detection", did)
        ais = await ais_near(c, r["lon"], r["lat"], r["ts"], r["vessel_id"], VIGNETTE_RAYON_M)
    return {**_row(r), "alertes": [_row(a) for a in alerts], "preuve": preuve, "ais_proches": ais}


@router.get("/api/vessels/{vessel_id}/satellite")
async def vessel_check(request: Request, vessel_id: int, limit: int = 20):
    """Vérification satellite d'un navire : échos radar Sentinel 1 et lumières VIIRS appariés au navire, longueur
    mesurée contre longueur déclarée, vignettes."""
    async with request.app.state.pool.acquire() as c:
        radar = await c.fetch(
            """SELECT d.id, d.ts, round(d.length_m)::int AS longueur_m, round(d.width_m)::int AS largeur_m,
                      round(d.heading_deg)::int AS cap_deg, round(d.match_cost::numeric, 2)::float8 AS cout,
                      p.satellite, v.length_m AS longueur_declaree_m,
                      (SELECT pi.id FROM preuves_images pi WHERE pi.objet = 'detection' AND pi.objet_id = d.id
                         AND pi.supprime_le IS NULL ORDER BY pi.id DESC LIMIT 1) AS preuve
               FROM detections d JOIN analyses a ON a.id = d.analysis_id AND a.source = 'sentinel1'
               JOIN sar_passes p ON p.id = a.pass_id JOIN vessels v ON v.id = d.matched_vessel_id
               WHERE d.matched_vessel_id = $1 ORDER BY d.ts DESC LIMIT $2::int""", vessel_id, limit)
        viirs = await c.fetch(
            """SELECT d.id, d.ts, round(d.nanowatts::numeric, 1)::float8 AS nanowatts, g.satellite,
                      (SELECT pi.id FROM preuves_images pi WHERE pi.objet = 'viirs_detection' AND pi.objet_id = d.id
                         AND pi.supprime_le IS NULL ORDER BY pi.id DESC LIMIT 1) AS preuve
               FROM viirs_detections d JOIN viirs_granules g ON g.id = d.granule_id
               WHERE d.matched_vessel_id = $1 ORDER BY d.ts DESC LIMIT $2::int""", vessel_id, limit)
    return {"radar": [{**_row(x), "preuve": x["preuve"] and f"/api/preuves/{x['preuve']}.png"} for x in radar],
            "viirs": [{**_row(x), "preuve": x["preuve"] and f"/api/preuves/{x['preuve']}.png"} for x in viirs]}


async def sentinel_status(c) -> dict:
    """Barre d'état : dernier passage analysé, état du dernier travailleur GPU, coût engagé ce mois ci."""
    last = await c.fetchrow(
        """SELECT a.id AS analyse, a.status AS statut, a.completed_at AS fini_le, p.id AS pass_id, p.satellite,
                  p.acquired_at AS acquis_le, (a.summary->>'alertes_dark_ship')::int AS alertes,
                  (SELECT count(*) FROM detections d WHERE d.analysis_id = a.id) AS detections
           FROM analyses a JOIN sar_passes p ON p.id = a.pass_id WHERE a.source = 'sentinel1'
           ORDER BY a.requested_at DESC LIMIT 1""")
    w = await c.fetchrow(
        """SELECT id, etat, commercial_type, cree_le, fini_le, detruit_le, erreur,
                  (mesures->>'cout_estime_eur')::float8 AS cout_eur
           FROM travailleurs WHERE tache = 'sentinel' ORDER BY id DESC LIMIT 1""")
    launch = await c.fetchrow(
        """SELECT status AS statut, started_at AS le, details->>'erreur' AS erreur FROM task_runs
           WHERE task = 'sentinel' AND (status <> 'ok' OR details->>'passages' IS DISTINCT FROM '0')
           ORDER BY started_at DESC LIMIT 1""")
    month = await c.fetchval(
        """SELECT coalesce(sum((mesures->>'cout_estime_eur')::float8), 0) FROM travailleurs
           WHERE tache = 'sentinel' AND cree_le >= date_trunc('month', now())""")
    attente = None
    try:                                  # lot C3 : analyses en attente d'un GPU (rupture de stock chez Scaleway)
        a = await c.fetchrow(
            """SELECT count(*) AS analyses, min(attente_depuis) AS depuis, min(prochain_essai) AS prochain_essai,
                      (array_agg(calcul_motif ORDER BY id DESC))[1] AS motif
               FROM analyses WHERE source = 'sentinel1' AND status = 'attente_gpu'""")
        attente = _row(a) if a and a["analyses"] else None
        if w is not None:
            w = {**dict(w), "rupture_stock": await c.fetchval("SELECT rupture_stock FROM travailleurs WHERE id = $1",
                                                              w["id"])}
    except asyncpg.PostgresError:
        pass                              # base sans la migration 28
    return {"dernier": _row(last) if last else None, "travailleur": _row(w) if w else None,
            "lancement": _row(launch) if launch else None, "euros_mois": round(float(month or 0), 2),
            "attente": attente}


async def sentinel_metrics(c, line) -> list[str]:
    """Mesures pour /api/metrics : analyses par état, scènes, durées et mémoire GPU de la dernière analyse, coût,
    détections par statut."""
    out = []
    for r in await c.fetch("SELECT status, count(*) AS n, coalesce(sum(jsonb_array_length(scenes)), 0) AS scenes "
                           "FROM analyses WHERE source = 'sentinel1' GROUP BY 1"):
        out += [line("mars_sentinel_analyses_total", r["n"], {"etat": r["status"]}),
                line("mars_sentinel_scenes_total", r["scenes"], {"etat": r["status"]})]
    last = await c.fetchrow(
        """SELECT (SELECT max((v->>'telecharge_s')::float8) FROM jsonb_each(a.timings) AS t(k, v)) AS telecharge,
                  (SELECT max((v->>'analyse_s')::float8) FROM jsonb_each(a.timings) AS t(k, v)) AS analyse,
                  (SELECT max((v->>'gpu_max_mo')::float8) FROM jsonb_each(a.timings) AS t(k, v)) AS gpu
           FROM analyses a WHERE a.source = 'sentinel1' AND a.status = 'done' AND a.timings IS NOT NULL
           ORDER BY a.completed_at DESC LIMIT 1""")
    if last:
        out += [line("mars_sentinel_derniere_scene_telechargement_secondes", last["telecharge"]),
                line("mars_sentinel_derniere_scene_analyse_secondes", last["analyse"]),
                line("mars_sentinel_derniere_scene_memoire_gpu_mo", last["gpu"])]
    out.append(line("mars_sentinel_cout_mois_euros", await c.fetchval(
        "SELECT coalesce(sum((mesures->>'cout_estime_eur')::float8), 0) FROM travailleurs "
        "WHERE tache = 'sentinel' AND cree_le >= date_trunc('month', now())")))
    for r in await c.fetch(f"SELECT {STATUT} AS statut, count(*) AS n FROM detections d "
                           "JOIN analyses a ON a.id = d.analysis_id AND a.source = 'sentinel1' GROUP BY 1"):
        out.append(line("mars_sentinel_detections_total", r["n"], {"statut": r["statut"]}))
    return out
