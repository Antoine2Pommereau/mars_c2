"""Sentinel 1 (étape 3, lot C) et Sentinel 2 (lot S2) : détections radar et optiques analysées par les travailleurs,
fiche d'une détection, vérification satellite d'un navire, état pour la barre d'état et mesures. Le traitement est fait
par le conteneur taches (mars/sentinel.py, mars/sentinel2.py) ; l'API ne fait que lire."""
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, HTTPException, Request

from preuves import ais_near, proof_of
from regions import dans_region, region_ewkt

router = APIRouter()

STATUT = ("CASE WHEN d.mask_reason = 'non_evaluable' THEN 'non_evaluable' WHEN d.mask_reason IS NOT NULL THEN 'ecartee' "
          "WHEN d.matched_vessel_id IS NOT NULL THEN 'avec_ais' ELSE 'sans_ais' END")
VIGNETTE_RAYON_M = 2000      # AIS superposés à la vignette radar (environ 3 km de côté)
CONTEXTE_RAYON_M = 6000      # AIS superposés à la vignette de contexte optique (10 km de côté)
SOURCES = "('sentinel1', 'sentinel2')"


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(r) -> dict:
    return {k: _iso(v) for k, v in dict(r).items()}


@router.get("/api/sentinel/detections")
async def detections(request: Request, start: datetime | None = None, end: datetime | None = None,
                     region: str | None = None, limit: int = 5000):
    """Détections Sentinel 1 et 2 de la plage (couche « Détections radar et optiques ») : source, statut, longueur, navire
    apparié."""
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=24)
    async with request.app.state.pool.acquire() as c:
        rows = await c.fetch(
            f"""SELECT d.id, d.ts, {STATUT} AS statut, d.mask_reason, round(d.length_m)::int AS longueur_m,
                       round(d.heading_deg)::int AS cap_deg, p.satellite, v.name AS navire, a.source,
                       ST_AsGeoJSON(d.geom, 5)::json AS geometry
                FROM detections d JOIN analyses a ON a.id = d.analysis_id AND a.source IN {SOURCES}
                JOIN sar_passes p ON p.id = a.pass_id LEFT JOIN vessels v ON v.id = d.matched_vessel_id
                WHERE d.ts BETWEEN $1 AND $2 AND {dans_region('d.geom::geometry', 3)}
                ORDER BY d.ts LIMIT $4::int""", start, end, await region_ewkt(c, region), limit)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": r["geometry"], "properties": {k: _iso(v) for k, v in dict(r).items() if k != "geometry"}}
        for r in rows]}


@router.get("/api/sentinel/detections/{did}")
async def detection(request: Request, did: int):
    """Fiche « détection satellite » Sentinel 1 ou 2 : attributs mesurés (longueur, largeur, cap, score, contraste ou
    vitesse et type), navire apparié et longueur déclarée, évaluation, alertes liées, vignette radar ou en couleurs
    réelles (et de contexte pour l'optique), nuages de la fenêtre, AIS proches à l'heure de la prise de vue."""
    async with request.app.state.pool.acquire() as c:
        r = await c.fetchrow(
            f"""SELECT d.id, d.ts, {STATUT} AS statut, d.mask_reason, d.non_evaluable, d.score, d.length_m, d.width_m,
                       d.heading_deg, d.heading_prob, d.speed_kn, d.fishing_score, d.contrast_vv_db, d.distance_cote_m,
                       d.ais_navires_rayon, d.scene, d.match_cost, a.id AS analyse, p.id AS pass_id, p.satellite,
                       p.orbit_direction, ST_X(d.geom::geometry) AS lon, ST_Y(d.geom::geometry) AS lat,
                       v.id AS vessel_id, v.name, v.mmsi, v.flag, v.length_m AS longueur_declaree_m, w.level AS watch,
                       a.source, d.type_navire, d.nuages_pct, d.masque_nuages, a.timings
                FROM detections d JOIN analyses a ON a.id = d.analysis_id AND a.source IN {SOURCES}
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
        contexte = await proof_of(c, "detection", did, "contexte") if r["source"] == "sentinel2" else None
        ais = await ais_near(c, r["lon"], r["lat"], r["ts"], r["vessel_id"],
                             CONTEXTE_RAYON_M if contexte else VIGNETTE_RAYON_M)
    out = {k: v for k, v in _row(r).items() if k != "timings"}
    if r["source"] == "sentinel2":
        out["fenetre"] = fenetre_de(r["timings"], r["scene"], r["lon"], r["lat"])
    return {**out, "alertes": [_row(a) for a in alerts], "preuve": preuve, "contexte": contexte, "ais_proches": ais}


def fenetre_de(timings, scene: str | None, lon: float, lat: float) -> dict | None:
    """Fenêtre optique qui contient la détection : nuages au masque de scène, masque présent ou absent, octets lus."""
    import json
    t = json.loads(timings) if isinstance(timings, str) else (timings or {})
    m = t.get(scene or "") or {}
    for f in m.get("fenetres") or []:
        b = f.get("bbox") or []
        if len(b) == 4 and b[0] <= lon <= b[2] and b[1] <= lat <= b[3]:
            return {"nuages_pct": f.get("nuages_pct"), "masque": m.get("masque"), "octets": f.get("octets")}
    return None


@router.get("/api/vessels/{vessel_id}/satellite")
async def vessel_check(request: Request, vessel_id: int, limit: int = 20):
    """Vérification satellite d'un navire : passages radar (Sentinel 1) et optiques (Sentinel 2) confondus, avec leur
    source, et lumières VIIRS appariés au navire ; longueur mesurée contre longueur déclarée, vignettes."""
    async with request.app.state.pool.acquire() as c:
        radar = await c.fetch(
            f"""SELECT d.id, d.ts, round(d.length_m)::int AS longueur_m, round(d.width_m)::int AS largeur_m,
                      round(d.heading_deg)::int AS cap_deg, round(d.match_cost::numeric, 2)::float8 AS cout,
                      p.satellite, v.length_m AS longueur_declaree_m, a.source,
                      (SELECT pi.id FROM preuves_images pi WHERE pi.objet = 'detection' AND pi.objet_id = d.id
                         AND pi.role = 'vignette' AND pi.supprime_le IS NULL ORDER BY pi.id DESC LIMIT 1) AS preuve
               FROM detections d JOIN analyses a ON a.id = d.analysis_id AND a.source IN {SOURCES}
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
            "attente": attente, "optique": await sentinel2_status(c)}


async def sentinel2_status(c) -> dict | None:
    """Sentinel 2 (lot S2) : dernière analyse et son résultat (analysée, nuages, sans détection), dernier travailleur,
    coût du mois de chaque source (budget partagé)."""
    try:
        last = await c.fetchrow(
            """SELECT a.id AS analyse, a.status AS statut, a.completed_at AS fini_le, p.id AS pass_id, p.satellite,
                      p.acquired_at AS acquis_le, a.summary->>'resultat' AS resultat,
                      (SELECT count(*) FROM detections d WHERE d.analysis_id = a.id) AS detections
               FROM analyses a JOIN sar_passes p ON p.id = a.pass_id WHERE a.source = 'sentinel2'
               ORDER BY a.requested_at DESC LIMIT 1""")
        w = await c.fetchrow("SELECT id, etat, commercial_type, cree_le, detruit_le, erreur FROM travailleurs "
                             "WHERE tache = 'sentinel2' ORDER BY id DESC LIMIT 1")
        parts = {r["tache"]: round(float(r["eur"]), 2) for r in await c.fetch(
            """SELECT tache, coalesce(sum(coalesce(cout_reel_eur, (mesures->>'cout_estime_eur')::float8)), 0) AS eur
               FROM travailleurs WHERE tache IN ('sentinel', 'sentinel2') AND cree_le >= date_trunc('month', now())
               GROUP BY 1""")}
    except asyncpg.PostgresError:
        return None                       # base sans la migration 32
    return {"dernier": _row(last) if last else None, "travailleur": _row(w) if w else None,
            "euros_mois": {"radar": parts.get("sentinel", 0.0), "optique": parts.get("sentinel2", 0.0)}}


