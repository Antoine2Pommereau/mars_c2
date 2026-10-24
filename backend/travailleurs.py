"""Travailleurs éphémères et détections nocturnes VIIRS (étape 3, lot B) ; mesures de la plateforme.

* Retour des travailleurs (réseau privé seulement, port 8090 de nginx) : état et résultats, protégés par le jeton de
  l'exécution (seule son empreinte est en base). Le traitement est fait par le conteneur taches (mars/viirs.py).
* Lecture : détections VIIRS d'une plage et d'une région, fiche d'une détection, nuits traitées (frise).
"""
import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from preuves import ais_near, proof_of
from regions import dans_region, region_ewkt
from mars import traces
from mars.config import load_rules
from mars.r2_signature import MODELES, modeles_manquantes, r2_modeles_get_url

router = APIRouter()


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row(r) -> dict:
    return {k: _iso(v) for k, v in dict(r).items()}


def _range(start, end):
    end = end or datetime.now(timezone.utc)
    return start or end - timedelta(hours=24), end


async def _check(c, wid: int, authorization: str | None, states=("cree", "demarre")):
    w = await c.fetchrow("SELECT id, etat, jeton_hash, detruit_le FROM travailleurs WHERE id = $1", wid)
    token = (authorization or "").removeprefix("Bearer ").strip()
    if w is None or not token or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), w["jeton_hash"]):
        raise HTTPException(403, "Jeton refusé")
    if w["detruit_le"] is not None or w["etat"] not in states:
        raise HTTPException(409, f"Travailleur dans l'état {w['etat']}")
    return w


log = logging.getLogger("mars.travailleurs")

# Clés remises par le réseau privé avec la tâche, par type de travailleur (lues dans le .env du serveur, jamais en base) :
# Copernicus refuse les adresses présignées (14/10/2026), le travailleur Sentinel lit donc les images S3 lui même, avec
# une paire qui lui est dédiée ; la paire du serveur (CDSE_ACCESS_KEY) ne quitte jamais le serveur
IDENTIFIANTS = {"sentinel": ("CDSE_TRAVAILLEUR_ACCESS_KEY", "CDSE_TRAVAILLEUR_SECRET_KEY"),
                "sentinel2": ("CDSE_TRAVAILLEUR_ACCESS_KEY", "CDSE_TRAVAILLEUR_SECRET_KEY")}
# Jeton NASA Earthdata des travailleurs VIIRS (lot Z1) : remis de la même façon, une seule fois, jamais dans le
# cloud-init (lisible par l'API Scaleway et par tout processus de l'instance)
JETONS = {"viirs": "EARTHDATA_TOKEN"}
# Poids du modèle Sentinel 2 (lot S2), dans le seau des modèles : adresses présignées de 3 heures par son jeton dédié
# (jamais le jeton principal, filtré sur l'IP du serveur), ajoutées à la remise seulement (jamais en base ni dans le
# cloud-init) ; adresses de 5 minutes pour le contrôle d'accès du démarrage (état « acces »)
POIDS_EXPIRE_S = 3 * 3600
ACCES_EXPIRE_S = 300
S3_COPERNICUS = "eodata.dataspace.copernicus.eu"


@router.post("/api/travailleurs/{wid}/etat")
async def worker_state(request: Request, wid: int, authorization: str | None = Header(None)):
    """Signe de vie : « reseau » (le script de démarrage a joint le serveur sur le réseau privé), puis « demarre »
    (le script d'analyse commence). Le premier fixe demarre_le : le délai de démarrage est tenu.

    « acces » : adresses R2 que la tâche remettra (poids du modèle), présignées pour 5 minutes, sans marquer la tâche
    remise : le script de démarrage en lit un octet dès le réseau privé joint, avant Docker et l'image (7,2 Go pour
    Sentinel 2), et signale aussitôt un refus de R2 avec son code et son message.

    « tache » : remise de la tâche par le réseau privé (types `tache_par_le_reseau`, config/rules.yaml), même route
    pour ne pas changer le relais nginx (web/nginx.conf, port 8090 : etat, resultats, journal). Une seule fois, au
    travailleur qui présente le jeton de l'exécution ; avec la paire de clés dédiée de son type."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    async with request.app.state.pool.acquire() as c:
        await _check(c, wid, authorization)
        if isinstance(body, dict) and body.get("etat") == "tache":
            return await _deliver_task(c, wid)
        if isinstance(body, dict) and body.get("etat") == "acces":
            params = await c.fetchval("SELECT parametres FROM travailleurs WHERE id = $1", wid) or {}
            return {"adresses": _adresses_r2(params, ACCES_EXPIRE_S)}
        if isinstance(body, dict) and body.get("etat") in ("echec", "gpu_inutilisable"):
            return await _fin_signalee(c, wid, body)
        await c.execute("UPDATE travailleurs SET etat = 'demarre', demarre_le = coalesce(demarre_le, now()) WHERE id = $1", wid)
    return {"ok": True}


async def _fin_signalee(c, wid: int, body: dict) -> dict:
    """Fin signalée par le script de démarrage (lot C4) : « echec » (étape et motif) ou « gpu_inutilisable » (message
    de nvidia-smi ou de Docker). Le travailleur quitte aussitôt l'état actif ; la surveillance (taches) détruit
    l'instance à la minute suivante, remet l'analyse en attente ou la passe en échec, et marque le type si le GPU est
    inutilisable. Le motif est gardé tel quel (aucune clé n'y figure : lignes de Docker ou du système)."""
    etat = body["etat"]
    etape = str(body.get("etape") or "")[:60]
    motif = str(body.get("message") or "")[:1500]
    erreur = (f"GPU inutilisable : {motif}" if etat == "gpu_inutilisable" else f"échec à l'étape {etape or '?'} : {motif}")
    await c.execute(
        "UPDATE travailleurs SET etat = $2, erreur = $3, fini_le = now(), "
        "mesures = coalesce(mesures, '{}'::jsonb) || jsonb_build_object('fin_signalee', $2::text, 'etape_echec', $4::text) "
        "WHERE id = $1", wid, etat, erreur[:1000], etape)
    log.warning("travailleur %s : %s", wid, erreur[:300])
    return {"ok": True}


def _adresses_r2(params: dict, expires: int) -> list[dict]:
    """Toute adresse R2 remise à un travailleur, présignée par le jeton du seau des modèles (sans filtre IP) : poids
    du modèle. Refus 503 (noms des variables manquantes, jamais leurs valeurs) si ce seau n'est pas configuré dans ce
    conteneur."""
    poids = (params or {}).get("poids") or []
    if not poids:
        return []
    manque = modeles_manquantes()
    if manque:
        raise HTTPException(503, f"seau des modèles non configuré dans le conteneur backend : {', '.join(manque)} "
                                 "absente(s) ; poids du modèle inaccessibles aux travailleurs")
    return [{"nom": f"poids {p['chemin']}", "url": r2_modeles_get_url(p["r2"], expires=expires)} for p in poids]


@router.get("/api/travailleurs/variables")
async def worker_variables():
    """Présence (oui ou non, jamais la valeur) des variables dont l'API a besoin pour équiper un travailleur, lue
    dans le conteneur backend lui même : taches.py sentinel2-diagnostic dit MANQUE si l'une n'y arrive pas. Pas de
    relais par le port 8090 (nginx n'y laisse passer que etat, resultats, journal et traces)."""
    noms = (*MODELES, *sorted({n for pair in IDENTIFIANTS.values() for n in pair}), *sorted(set(JETONS.values())))
    return {n: bool((os.environ.get(n) or "").strip()) for n in noms}


async def _deliver_task(c, wid: int) -> dict:
    """Tâche du travailleur (paramètres en base, sans secret) et, pour son type, la paire de clés Copernicus ou le jeton
    Earthdata lus dans l'environnement de l'API.
    La remise est notée dans `mesures` (tache_remise_le) dans la même requête que sa vérification : une seconde demande
    reçoit 409, même concurrente. Les clés ne sont ni écrites en base ni journalisées."""
    tache = await c.fetchval("SELECT tache FROM travailleurs WHERE id = $1", wid)
    names, creds = IDENTIFIANTS.get(tache), None
    if names:
        key, secret = ((os.environ.get(n) or "").strip() for n in names)
        if not key or not secret:                       # rien n'est marqué remis : la demande peut être refaite
            raise HTTPException(503, f"clés absentes du .env du serveur ({names[0]}, {names[1]})")
        creds = {"endpoint": S3_COPERNICUS, "region": "default", "access_key": key, "secret_key": secret}
    jeton = None
    if tache in JETONS:
        jeton = (os.environ.get(JETONS[tache]) or "").strip()
        if not jeton:
            raise HTTPException(503, f"jeton absent de l'environnement de l'API ({JETONS[tache]})")
    params = await c.fetchval("SELECT parametres FROM travailleurs WHERE id = $1", wid) or {}
    _adresses_r2(params, ACCES_EXPIRE_S)                          # vérifié avant de marquer la tâche remise
    row = await c.fetchrow(
        "UPDATE travailleurs SET mesures = coalesce(mesures, '{}'::jsonb) || jsonb_build_object('tache_remise_le', now()) "
        "WHERE id = $1 AND NOT (coalesce(mesures, '{}'::jsonb) ? 'tache_remise_le') RETURNING parametres", wid)
    if row is None:
        raise HTTPException(409, "Tâche déjà remise")
    log.warning("travailleur %s : tâche remise par le réseau privé%s", wid,
                " avec sa paire de clés" if creds else " avec son jeton" if jeton else "")
    traces.attributs(**{"travailleur.remise": "tache", "travailleur.secret_remis": bool(creds or jeton)})
    params = dict(row["parametres"] or {})
    if params.get("poids"):
        urls = _adresses_r2(params, POIDS_EXPIRE_S)
        params["poids"] = [{**p, "url": u["url"]} for p, u in zip(params["poids"], urls)]
    ident = {**({"s3": creds} if creds else {}), **({"earthdata": jeton} if jeton else {})}
    return {**params, **({"identifiants": ident} if ident else {})}


# États d'un travailleur dont les spans sont encore acceptés : ses derniers spans partent juste après ses résultats
TRACES_ETATS = ("cree", "demarre", "resultats", "termine", "echec")


@router.get("/api/travailleurs/{wid}/otel", status_code=204)
async def worker_traces_auth(request: Request, wid: int, authorization: str | None = Header(None)):
    """Vérification par nginx (auth_request, port 8090) du jeton d'un travailleur avant de relayer ses spans au
    collecteur OpenTelemetry : même jeton que pour ses résultats, refusé dès que l'instance est détruite. Le collecteur
    n'écoute jamais sur l'adresse privée lui même. Route non tracée (une vérification par lot de spans)."""
    async with request.app.state.pool.acquire() as c:
        await _check(c, wid, authorization, TRACES_ETATS)
    return Response(status_code=204)


JOURNAL_MAX = 200_000


@router.post("/api/travailleurs/{wid}/journal")
async def worker_log(request: Request, wid: int, authorization: str | None = Header(None)):
    """Journal de démarrage du travailleur (texte complet à chaque envoi ; les 200 000 derniers caractères gardés)."""
    text = (await request.body()).decode("utf-8", "replace")[-JOURNAL_MAX:]
    async with request.app.state.pool.acquire() as c:
        await _check(c, wid, authorization, ("cree", "demarre", "resultats", "termine", "echec"))
        await c.execute("UPDATE travailleurs SET journal = $2, journal_le = now() WHERE id = $1", wid, text)
    return {"ok": True}


def _regles() -> dict:
    """Règles du fichier ; sans lui (conteneur sans config monté), les seuils par défaut de la validation."""
    try:
        return load_rules()
    except OSError:
        return {}


@router.post("/api/travailleurs/{wid}/resultats")
async def worker_results(request: Request, wid: int, authorization: str | None = Header(None)):
    """Résultats d'un travailleur, validés contre sa tâche avant d'être gardés (mars/validation.py) : un refus est
    journalisé (travailleur en échec avec son motif, journaux de l'API, trace) et répond 422 ; la surveillance de taches
    détruit l'instance à la minute suivante, comme après un échec signalé."""
    from mars import validation
    try:
        body = await request.json()
    except Exception:
        body = None
    async with request.app.state.pool.acquire() as c:
        await _check(c, wid, authorization)
        row = await c.fetchrow("SELECT tache, parametres FROM travailleurs WHERE id = $1", wid)
        try:
            with traces.span("travailleurs.validation", **{"travailleur.id": wid, "travailleur.tache": row["tache"]}):
                validation.valider(row["tache"], row["parametres"], body, _regles())
        except validation.Refus as e:
            motif = f"résultats refusés : {e}"[:1000]
            traces.erreur(motif)
            log.warning("travailleur %s : %s", wid, motif)
            await c.execute(
                "UPDATE travailleurs SET etat = 'echec', erreur = $2, fini_le = now(), mesures = coalesce(mesures, "
                "'{}'::jsonb) || jsonb_build_object('resultats_refuses', $2::text) WHERE id = $1", wid, motif)
            raise HTTPException(422, motif)
        await c.execute(
            "UPDATE travailleurs SET etat = 'resultats', resultats_le = now(), resultat = $2, "
            "mesures = mesures || $3 WHERE id = $1", wid, body, body.get("mesures") or {})
    return {"ok": True}


# Détections VIIRS

STATUT = ("CASE WHEN d.mask_reason = 'non_evaluable' THEN 'non_evaluable' WHEN d.mask_reason IS NOT NULL THEN 'ecartee' "
          "WHEN d.matched_vessel_id IS NOT NULL THEN 'avec_ais' ELSE 'sans_ais' END")


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
    """Fiche d'une détection : heure, intensité, navire AIS apparié ou absence d'appariement, alertes liées, preuve
    image (vignette sur R2) et positions AIS à moins de 5 km à l'heure du passage pour la surimpression."""
    async with request.app.state.pool.acquire() as c:
        r = await c.fetchrow(
            f"""SELECT d.id, d.ts, d.nanowatts, d.orientation, d.lune, d.ciel_clair, d.mask_reason, {STATUT} AS statut,
                       d.match_distance_m, d.distance_cote_m, g.satellite, g.nom AS granule, g.nuit, d.non_evaluable,
                       round(d.ais_proche_m)::int AS ais_proche_m, d.ais_proche_ecart_s, d.ais_navires_rayon,
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
        preuve = await proof_of(c, "viirs_detection", did)
        ais = await ais_near(c, r["lon"], r["lat"], r["ts"], r["vessel_id"])
    return {**_row(r), "alertes": [_row(a) for a in alerts], "preuve": preuve, "ais_proches": ais}


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
                      count(d.id) FILTER (WHERE d.mask_reason = 'non_evaluable') AS non_evaluables,
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
    launch = await c.fetchrow(
        """SELECT status AS statut, started_at AS le, details->>'erreur' AS erreur FROM task_runs WHERE task = 'viirs'
           ORDER BY started_at DESC LIMIT 1""")
    w = await c.fetchrow(
        """SELECT id, tache, etat, commercial_type, cree_le, demarre_le, fini_le, detruit_le, erreur, journal_le,
                  (mesures->>'cout_estime_eur')::float8 AS cout_eur, (mesures->>'duree_s')::float8 AS duree_s
           FROM travailleurs ORDER BY id DESC LIMIT 1""")
    return {"derniere_nuit": _row(night) if night else None, "travailleur": _row(w) if w else None,
            "lancement": _row(launch) if launch else None}



