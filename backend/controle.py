"""Panneau de contrôle (lot P) : connexion des opérateurs et routes du panneau.

Les routes d'écriture exigent une session d'opérateur : mot de passe haché dans le .env (MARS_OPERATEURS, voir
mars/controle.py), cookie HttpOnly et SameSite strict, 12 h, Secure dès que l'interface est servie en https ; l'origine
d'une requête d'écriture doit être celle de l'interface. Chaque action est journalisée dans journal_controle avec le nom
de l'opérateur. Les routes de lecture ne changent pas (aucune connexion requise), comme le reste de l'API. L'état
écrit ici est lu par le conteneur taches (mars/controle.appliquer) à chaque cycle."""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from mars import controle, traces
from mars.config import load_rules

router = APIRouter()
COOKIE = "mars_operateur"
ECHECS_MAX, ECHECS_FENETRE_S = 5, 600          # 5 échecs en 10 minutes par adresse : connexion bloquée 10 minutes
_echecs: dict[str, list[float]] = {}


def _json(v):
    return None if v is None else json.dumps(v, default=str, ensure_ascii=False)


async def journaliser(c, auteur: str, action: str, cible: str | None, avant=None, apres=None, motif: str | None = None):
    await c.execute("INSERT INTO journal_controle (auteur, action, cible, avant, apres, motif, trace_id) "
                    "VALUES ($1, $2, $3, $4::text::jsonb, $5::text::jsonb, $6, $7)",
                    auteur, action, cible, _json(avant), _json(apres), motif, traces.trace_id())
    traces.attributs(**{"controle.action": action, "controle.cible": cible or "", "controle.auteur": auteur})


async def notifier(c, categorie: str, gravite: str, titre: str, message: str) -> str:
    """Notification selon les préférences du panneau (envoyée, retenue pour le résumé du réveil, ou tue)."""
    prefs = controle.preferences(load_rules(), await c.fetchval(
        "SELECT valeur FROM reglages_controle WHERE cle = 'notif:preferences'"))
    d = controle.decision_notification(prefs, categorie, gravite, datetime.now(timezone.utc))
    if d == "retenir":
        await c.execute("INSERT INTO notifications_retenues (categorie, gravite, titre, message) VALUES ($1, $2, $3, $4)",
                        categorie, gravite, titre, message)
    elif d == "envoyer":
        await asyncio.to_thread(controle.envoyer, gravite, titre, message, "satellite")
    traces.attributs(**{"notification.categorie": categorie, "notification.decision": d})
    return d


# Session

def _empreinte(request: Request) -> str | None:
    j = request.cookies.get(COOKIE)
    return controle.empreinte_jeton(j) if j else None


async def operateur_connecte(request: Request) -> str | None:
    e = _empreinte(request)
    if e is None:
        return None
    async with request.app.state.pool.acquire() as c:
        return await c.fetchval("SELECT operateur FROM sessions_operateur WHERE empreinte = $1 AND expire_le > now() "
                                "AND revoquee_le IS NULL", e)


def _meme_origine(request: Request):
    """Écriture depuis l'interface seulement : l'en tête Origin, s'il est présent, désigne le même hôte."""
    origine = request.headers.get("origin")
    if origine and origine.split("://", 1)[-1].rstrip("/") != (request.headers.get("host") or ""):
        raise HTTPException(403, "Origine refusée")


async def exiger_operateur(request: Request) -> str:
    _meme_origine(request)
    op = await operateur_connecte(request)
    if op is None:
        raise HTTPException(401, "Connexion opérateur requise")
    traces.attributs(**{"controle.operateur": op})
    return op


def _https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


class Connexion(BaseModel):
    operateur: str = Field(min_length=1, max_length=40)
    mot_de_passe: str = Field(min_length=1, max_length=200)


@router.get("/api/controle/session")
async def session(request: Request):
    return {"operateur": await operateur_connecte(request), "configure": bool(controle.operateurs())}


@router.post("/api/controle/connexion")
async def connexion(request: Request, response: Response, x: Connexion):
    _meme_origine(request)
    ip = request.client.host if request.client else "?"
    t = time.time()
    _echecs[ip] = [e for e in _echecs.get(ip, []) if t - e < ECHECS_FENETRE_S]
    if len(_echecs[ip]) >= ECHECS_MAX:
        raise HTTPException(429, "Trop d'essais : réessayer dans 10 minutes")
    ops = controle.operateurs()
    if not ops:
        raise HTTPException(503, "Aucun opérateur configuré (MARS_OPERATEURS dans le .env)")
    nom = x.operateur.strip()
    empreinte = ops.get(nom)
    ok = empreinte is not None and await asyncio.to_thread(controle.verifier_mot_de_passe, x.mot_de_passe, empreinte)
    async with request.app.state.pool.acquire() as c:
        if not ok:
            _echecs[ip].append(t)
            await journaliser(c, nom[:40], "connexion_refusee", "operateur")
            raise HTTPException(401, "Opérateur ou mot de passe incorrect")
        jeton, emp = controle.jeton_session()
        heures = float((load_rules().get("controle") or {}).get("session_h", 12))
        await c.execute("INSERT INTO sessions_operateur (empreinte, operateur, expire_le) VALUES ($1, $2, $3)",
                        emp, nom, datetime.now(timezone.utc) + timedelta(hours=heures))
        await c.execute("DELETE FROM sessions_operateur WHERE expire_le < now() - interval '7 days'")
        await journaliser(c, nom, "connexion", "operateur")
    _echecs.pop(ip, None)
    response.set_cookie(COOKIE, jeton, max_age=int(heures * 3600), httponly=True, samesite="strict",
                        secure=_https(request), path="/api")
    return {"operateur": nom}


@router.post("/api/controle/deconnexion")
async def deconnexion(request: Request, response: Response):
    _meme_origine(request)
    e = _empreinte(request)
    if e:
        async with request.app.state.pool.acquire() as c:
            op = await c.fetchval("UPDATE sessions_operateur SET revoquee_le = now() WHERE empreinte = $1 "
                                  "AND revoquee_le IS NULL RETURNING operateur", e)
            if op:
                await journaliser(c, op, "deconnexion", "operateur")
    response.delete_cookie(COOKIE, path="/api")
    return {"operateur": None}


# Lecture : état du panneau

async def interrupteurs(c) -> list[dict]:
    rows = await c.fetch(f"SELECT {controle.COLS_INTERRUPTEUR} FROM interrupteurs_satellites ORDER BY source")
    return [{**dict(r), "nom": controle.NOMS[r["source"]]} for r in rows]


@router.get("/api/controle/etat")
async def etat(request: Request):
    """Interrupteurs, plafonds (valeur, défaut du fichier, bornes), budget du mois, coût réel des derniers
    travailleurs, version des règles."""
    rules = load_rules()
    now = datetime.now(timezone.utc)
    async with request.app.state.pool.acquire() as c:
        regl = {r["cle"]: r["valeur"] for r in await c.fetch("SELECT cle, valeur FROM reglages_controle")}
        mois = controle.debut_mois(now)
        bud = controle.budget_calcul([tuple(r) for r in await c.fetch(controle.BUDGET_SQL.format(p="$1"), mois)],
                                     rules, mois)
        derniers = await c.fetch(
            """SELECT id, tache, etat, commercial_type, cree_le, detruit_le, cout_reel_eur,
                      (mesures->>'cout_estime_eur')::float8 AS cout_estime_eur, trace_id
               FROM travailleurs ORDER BY id DESC LIMIT 10""")
        inter = await interrupteurs(c)
    plafonds = {}
    for source, section in controle.PLAFONDS.items():
        lo, hi = controle.bornes_plafond(rules, source)
        defaut = int(rules[section]["scenes_max_par_jour"])
        v = regl.get(f"plafond:{source}")
        plafonds[source] = {"valeur": max(lo, min(hi, int(v))) if v is not None else defaut, "defaut": defaut,
                            "min": lo, "max": hi, "regle_par_le_panneau": v is not None}
    return {"version_regles": rules["version"], "interrupteurs": inter, "plafonds": plafonds, "budget": bud,
            "travailleurs": [dict(r) for r in derniers],
            "rayon_demande_m": (rules.get("controle") or {}).get("rayon_demande_m")}


@router.get("/api/controle/journal")
async def journal(request: Request, limit: int = 50):
    async with request.app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT id, le, auteur, action, cible, avant, apres, motif, trace_id FROM journal_controle "
                             "ORDER BY le DESC, id DESC LIMIT $1", min(max(limit, 1), 500))
    return [dict(r) for r in rows]


# Lecture : état du système (phase B), sans socket Docker

@router.get("/api/controle/systeme")
async def systeme(request: Request):
    """Services et version déployée (signes_de_vie), dernier signe de vie, retard de l'ingestion, disque, mémoire des
    conteneurs qui la publient (taches, ingestion, API ; omise sinon), dernières tâches en échec (refus attendus
    exclus) ; liens vers Grafana et vers la trace de chaque élément."""
    from mars import supervision
    async with request.app.state.pool.acquire() as c:
        services = await c.fetch(
            """SELECT service, dernier_le, extract(epoch FROM now() - dernier_le)::float8 AS age_s,
                      detail->>'version' AS version, (detail->>'memoire_mo')::float8 AS memoire_mo,
                      (detail->>'memoire_max_mo')::float8 AS memoire_max_mo, (detail->>'retard_s')::float8 AS retard_s
               FROM signes_de_vie ORDER BY service""")
        retard = await c.fetchval("SELECT extract(epoch FROM now() - max(ts))::float8 FROM positions "
                                  "WHERE ts > now() - interval '1 day'")
        disque = await c.fetchrow("SELECT mesure_le, libre_pct, libre_octets, total_octets, base_octets FROM mesures_disque "
                                  "ORDER BY mesure_le DESC LIMIT 1")
        echecs = await c.fetch(
            """SELECT id, task AS tache, started_at AS le, left(details->>'erreur', 300) AS erreur, trace_id
               FROM task_runs WHERE status = 'echec' AND NOT (details ? 'refus') ORDER BY started_at DESC LIMIT 10""")
        derniere_trace = await c.fetchval("SELECT trace_id FROM task_runs WHERE task = 'regles' AND trace_id IS NOT NULL "
                                          "ORDER BY started_at DESC LIMIT 1")
    lignes = [dict(r) for r in services]
    api_mem = supervision.memoire_conteneur() or {}
    if not any(x["service"] == "api" for x in lignes):
        lignes.append({"service": "api", "dernier_le": None, "age_s": None, "retard_s": None})
    for x in lignes:
        if x["service"] == "api":            # l'API se mesure en direct : elle répond, donc elle vit
            x.update({"version": traces.version(), "age_s": 0.0, **api_mem})
    return {"grafana": controle.grafana_url(), "services": lignes, "retard_positions_s": retard,
            "disque": dict(disque) if disque else None,
            "echecs": [{**dict(r), "lien_trace": controle.lien_trace(r["trace_id"])} for r in echecs],
            "lien_regles": controle.lien_trace(derniere_trace)}


# Règles d'alerte (phase C) : modes, seuils en lecture seule, épisodes et alertes des 7 derniers jours

async def _modes(c) -> dict:
    return {r["cle"]: r["valeur"] for r in await c.fetch("SELECT cle, valeur FROM reglages_controle "
                                                         "WHERE cle LIKE 'mode:%'")}


@router.get("/api/controle/regles")
async def regles(request: Request):
    rules = load_rules()
    async with request.app.state.pool.acquire() as c:
        regl = await _modes(c)
        alertes = await c.fetch(
            """SELECT type, details->>'source' AS source, details->>'changement' AS changement, count(*) AS n
               FROM alerts WHERE detected_at >= now() - interval '7 days' GROUP BY 1, 2, 3""")
        observes = await c.fetch(
            """SELECT regle, sous_type, count(*) AS n FROM episodes_observes
               WHERE derniere_le >= now() - interval '7 days' GROUP BY 1, 2""")
        regl.update({r["cle"]: r["valeur"] for r in await c.fetch(
            "SELECT cle, valeur FROM reglages_controle WHERE cle = 'suivis:prioritaires'")})
        zones = [dict(zip(controle.COLS_ZONE, tuple(z))) for z in await c.fetch(controle.ZONES_ACTIVES_SQL)]
    r = controle.appliquer_reglages(rules, regl, {}, zones)
    n_alertes: dict = {}
    for a in alertes:
        regle, sous = controle.regle_de(a["type"], {"source": a["source"], "changement": a["changement"]})
        if regle:
            for k in (regle, f"{regle}.{sous}" if sous else None):
                if k:
                    n_alertes[k] = n_alertes.get(k, 0) + a["n"]
    n_obs: dict = {}
    for o in observes:
        for k in (o["regle"], f"{o['regle']}.{o['sous_type']}" if o["sous_type"] else None):
            if k:
                n_obs[k] = n_obs.get(k, 0) + o["n"]

    def bloc(regle, sous=None):
        k = f"{regle}.{sous}" if sous else regle
        return {"mode": controle.mode_effectif(r, regle, sous), "mode_fichier": controle.mode_fichier(rules, regle),
                "mode_panneau": regl.get(f"mode:{k}"), "alertes_7j": n_alertes.get(k, 0),
                "episodes_7j": n_alertes.get(k, 0) + n_obs.get(k, 0), "observes_7j": n_obs.get(k, 0)}
    out = []
    for regle, d in controle.catalogue(rules).items():
        out.append({"regle": regle, "nom": d["nom"], **bloc(regle),
                    "sous_types": [{"sous_type": s, "nom": nom, **bloc(regle, s)}
                                   for s, nom in (d.get("sous_types") or {}).items()],
                    "seuils": controle.seuils(rules.get(d.get("section")) or {})})
    return {"version_regles": rules["version"], "empreinte": r["_controle"]["empreinte"], "regles": out}


class Mode(BaseModel):
    mode: str
    sous_type: str | None = None
    motif: str = Field(min_length=3, max_length=300)


@router.post("/api/controle/regles/{regle}")
async def changer_mode(request: Request, regle: str, x: Mode, op: str = Depends(exiger_operateur)):
    """Mode d'une règle (ou d'un sous type) : actif, observation ou coupé ; journalisé. Aucune alerte supprimée ; au
    passage en coupé, les alertes ouvertes de la règle restent telles quelles et le motif entre dans leur historique."""
    rules = load_rules()
    try:
        cle = controle.verifier_mode(rules, regle, x.sous_type, x.mode)
    except ValueError as e:
        raise HTTPException(422, str(e))
    nom = controle.catalogue(rules)[regle]["nom"]
    if x.sous_type:
        nom += ", " + controle.catalogue(rules)[regle]["sous_types"][x.sous_type]
    annotees = 0
    async with request.app.state.pool.acquire() as c, c.transaction():
        avant = await c.fetchval("SELECT valeur FROM reglages_controle WHERE cle = $1 FOR UPDATE", cle)
        await c.execute("INSERT INTO reglages_controle (cle, valeur, auteur) VALUES ($1, $2::text::jsonb, $3) "
                        "ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur, auteur = EXCLUDED.auteur, "
                        "mis_a_jour_le = now()", cle, json.dumps(x.mode), op)
        if x.mode == "coupe" and avant != "coupe":
            ouvertes = await c.fetch("SELECT id, type, details->>'source' AS source, details->>'changement' AS changement "
                                     "FROM alerts WHERE status IN ('nouvelle', 'acquittee', 'confirmee')")
            ids = [a["id"] for a in ouvertes if controle.concerne((regle, x.sous_type), a["type"],
                                                                   {"source": a["source"], "changement": a["changement"]})]
            if ids:
                note = f"Règle « {nom} » coupée au panneau de contrôle par {op} : {x.motif.strip()}"
                await c.executemany("INSERT INTO alert_actions (alert_id, action, note, author) "
                                    "VALUES ($1, 'commenter', $2, $3)", [(i, note, op) for i in ids])
            annotees = len(ids)
        await journaliser(c, op, "mode_regle", f"{regle}.{x.sous_type}" if x.sous_type else regle,
                          {"mode": avant}, {"mode": x.mode, "alertes_ouvertes_annotees": annotees}, x.motif.strip())
    return {"regle": regle, "sous_type": x.sous_type, "mode": x.mode, "alertes_ouvertes_annotees": annotees}


# Écriture : interrupteurs, plafonds, demande d'observation

class Bascule(BaseModel):
    en_pause: bool
    motif: str | None = Field(default=None, max_length=300)
    jusqu_a: datetime | None = None


@router.post("/api/controle/satellites/{source}")
async def basculer(request: Request, source: str, x: Bascule, op: str = Depends(exiger_operateur)):
    if source not in controle.SOURCES:
        raise HTTPException(404, "Source inconnue")
    motif = (x.motif or "").strip() or None
    if x.en_pause and not motif:
        raise HTTPException(422, "Motif obligatoire pour une pause")
    if x.jusqu_a is not None and (x.jusqu_a.tzinfo is None or x.jusqu_a <= datetime.now(timezone.utc)):
        raise HTTPException(422, "Date de reprise absente de fuseau ou déjà passée")
    async with request.app.state.pool.acquire() as c, c.transaction():
        cols = controle.COLS_INTERRUPTEUR
        avant = dict(await c.fetchrow(f"SELECT {cols} FROM interrupteurs_satellites WHERE source = $1 FOR UPDATE",
                                      source))
        if x.en_pause:
            await c.execute("UPDATE interrupteurs_satellites SET en_pause = true, depuis = CASE WHEN en_pause THEN depuis "
                            "ELSE now() END, jusqu_a = $2, motif = $3, auteur = $4, veille_notifiee_le = NULL "
                            "WHERE source = $1", source, x.jusqu_a if x.en_pause else None, motif, op)
        else:
            await c.execute("UPDATE interrupteurs_satellites SET en_pause = false, depuis = now(), jusqu_a = NULL, "
                            "motif = $2, auteur = $3, veille_notifiee_le = NULL WHERE source = $1", source, motif, op)
        apres = dict(await c.fetchrow(f"SELECT {cols} FROM interrupteurs_satellites WHERE source = $1", source))
        await journaliser(c, op, "pause" if x.en_pause else "reprise", source, controle._public(avant),
                          controle._public(apres), motif)
    if bool(avant["en_pause"]) != x.en_pause or x.en_pause:
        fin = f" jusqu'au {controle._paris(x.jusqu_a):%d/%m/%Y %H:%M}" if x.en_pause and x.jusqu_a else ""
        async with request.app.state.pool.acquire() as c:
            await notifier(c, "travailleurs", "info", f"MARS C2 : {controle.NOMS[source]} "
                           f"{'en pause' if x.en_pause else 'repris'}",
                           f"{'Pause' if x.en_pause else 'Reprise'}{fin} par {op}" + (f" : {motif}" if motif else ""))
    return {**apres, "nom": controle.NOMS[source]}


# Notifications (phase D)

@router.get("/api/controle/notifications")
async def notifications(request: Request):
    rules = load_rules()
    async with request.app.state.pool.acquire() as c:
        en_base = await c.fetchval("SELECT valeur FROM reglages_controle WHERE cle = 'notif:preferences'")
        retenues = await c.fetchval("SELECT count(*) FROM notifications_retenues WHERE envoyee_le IS NULL")
    prefs = controle.preferences(rules, en_base)
    return {"preferences": prefs, "par_le_panneau": en_base is not None, "retenues": retenues,
            "heures_calmes_maintenant": controle.en_heures_calmes(prefs, datetime.now(timezone.utc)),
            "gravites": list(controle.GRAVITES), "categories": list(controle.CATEGORIES),
            "ntfy": bool(os.environ.get("NTFY_SUJET"))}


class Preferences(BaseModel):
    preferences: dict
    motif: str | None = Field(default=None, max_length=300)


@router.post("/api/controle/notifications")
async def regler_notifications(request: Request, x: Preferences, op: str = Depends(exiger_operateur)):
    rules = load_rules()
    try:
        p = controle.verifier_preferences(rules, x.preferences)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(422, str(e))
    async with request.app.state.pool.acquire() as c, c.transaction():
        avant = await c.fetchval("SELECT valeur FROM reglages_controle WHERE cle = 'notif:preferences' FOR UPDATE")
        await c.execute("INSERT INTO reglages_controle (cle, valeur, auteur) VALUES ('notif:preferences', "
                        "$1::text::jsonb, $2) ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur, "
                        "auteur = EXCLUDED.auteur, mis_a_jour_le = now()", json.dumps(p), op)
        await journaliser(c, op, "notifications", "preferences", avant, p, (x.motif or "").strip() or None)
    return {"preferences": controle.preferences(rules, p)}


@router.post("/api/controle/notifications/test")
async def notification_test(request: Request, op: str = Depends(exiger_operateur)):
    """Notification d'essai, envoyée tout de suite (préférences et heures calmes ignorées : on vérifie le canal)."""
    if not os.environ.get("NTFY_SUJET"):
        raise HTTPException(503, "NTFY_SUJET absent du .env : aucune notification ne peut partir")
    code = await asyncio.to_thread(controle.envoyer, "info", "MARS C2 : notification de test",
                                   f"Envoyée depuis le panneau de contrôle par {op}", "white_check_mark")
    async with request.app.state.pool.acquire() as c:
        await journaliser(c, op, "notification_test", "ntfy", None, {"http": code})
    if code is None:
        raise HTTPException(502, "ntfy injoignable ou refus (voir les journaux de l'API)")
    return {"envoyee": True, "http": code}


# Zones de vigilance (phase E) et priorité aux navires suivis

ZONE_COLS = """z.id, z.nom, z.motif, z.profil, z.infrastructure_id, z.tampon_m, z.fin_le, z.cree_le, z.cree_par,
               z.retiree_le, z.retiree_par,
               (z.retiree_le IS NULL AND (z.fin_le IS NULL OR z.fin_le > now())) AS active"""


@router.get("/api/controle/zones")
async def zones(request: Request, toutes: bool = False):
    """Zones de vigilance (actives, ou toutes) en GeoJSON, profils du fichier, réglage des navires suivis."""
    rules = load_rules()
    async with request.app.state.pool.acquire() as c:
        rows = await c.fetch(f"""SELECT {ZONE_COLS}, ST_AsGeoJSON(z.geom, 5)::json AS geometry FROM zones_vigilance z
                                 WHERE $1 OR (z.retiree_le IS NULL AND (z.fin_le IS NULL OR z.fin_le > now()))
                                 ORDER BY z.id""", toutes)
        suivis = await c.fetchval("SELECT valeur FROM reglages_controle WHERE cle = 'suivis:prioritaires'")
    profils = controle.profils_zones(rules)
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": r["geometry"],
                          "properties": {**{k: v for k, v in dict(r).items() if k != "geometry"},
                                         "profil_nom": (profils.get(r["profil"]) or {}).get("nom", r["profil"])}}
                         for r in rows],
            "profils": {k: {"nom": v.get("nom", k), "gravite_min": v.get("gravite_min"),
                            "priorite_plus": v.get("priorite_plus"), "priorite_sentinel": v.get("priorite_sentinel"),
                            "seuils": v.get("seuils") or {}} for k, v in profils.items()},
            "tampon_m": (rules.get("zones_vigilance") or {}).get("tampon_m"),
            "suivis_prioritaires": bool(suivis)}


@router.get("/api/controle/zones/{zid}")
async def zone(request: Request, zid: int):
    """Fiche d'une zone : alertes qui la portent, demandes d'observation et passages qui la couvrent (7 jours avant,
    3 jours après)."""
    async with request.app.state.pool.acquire() as c:
        z = await c.fetchrow(f"SELECT {ZONE_COLS}, ST_AsGeoJSON(z.geom, 5)::json AS geometry, "
                             "round((ST_Area(z.geom) / 1e6)::numeric, 1)::float8 AS km2 FROM zones_vigilance z "
                             "WHERE z.id = $1", zid)
        if z is None:
            raise HTTPException(404, "Zone inconnue")
        alertes = await c.fetch(
            """SELECT id, type, severity, status, event_time, details->'zone_vigilance'->>'gravite_avant' AS gravite_avant
               FROM alerts WHERE details ? 'zone_vigilance' AND (details->'zone_vigilance'->>'id')::bigint = $1
               ORDER BY event_time DESC LIMIT 100""", zid)
        demandes = await c.fetch(
            """SELECT d.id, d.tip, d.etat, d.priorite, d.cree_le, d.heure_passage, d.resultat
               FROM demandes_observation d, zones_vigilance z
               WHERE z.id = $1 AND ST_Intersects(coalesce(d.position_predite, d.position), z.geom)
                 AND d.cree_le >= z.cree_le ORDER BY d.cree_le DESC LIMIT 50""", zid)
        passages = await c.fetch(
            """SELECT p.id, p.mission, p.satellite, p.acquired_at, p.statut FROM sar_passes p, zones_vigilance z
               WHERE z.id = $1 AND p.footprint IS NOT NULL AND ST_Intersects(p.footprint, z.geom)
                 AND p.acquired_at BETWEEN now() - interval '7 days' AND now() + interval '3 days'
               ORDER BY p.acquired_at DESC LIMIT 50""", zid)
    return {**dict(z), "alertes": [dict(r) for r in alertes], "demandes": [dict(r) for r in demandes],
            "passages": [dict(r) for r in passages]}


class NouvelleZone(BaseModel):
    nom: str = Field(min_length=2, max_length=80)
    motif: str = Field(min_length=3, max_length=300)
    profil: str
    fin_le: datetime | None = None
    geometrie: dict | None = None                 # polygone GeoJSON dessiné sur la carte
    infrastructure_id: int | None = None          # ou tampon autour d'une infrastructure
    tampon_m: float | None = None


@router.post("/api/controle/zones")
async def creer_zone(request: Request, x: NouvelleZone, op: str = Depends(exiger_operateur)):
    rules = load_rules()
    if x.profil not in controle.profils_zones(rules):
        raise HTTPException(422, f"Profil inconnu : {x.profil} (zones_vigilance.profils de config/rules.yaml)")
    if x.fin_le is not None and (x.fin_le.tzinfo is None or x.fin_le <= datetime.now(timezone.utc)):
        raise HTTPException(422, "Date de fin absente de fuseau ou déjà passée")
    if (x.geometrie is None) == (x.infrastructure_id is None):
        raise HTTPException(422, "Préciser un polygone dessiné ou une infrastructure, l'un des deux")
    async with request.app.state.pool.acquire() as c, c.transaction():
        if x.geometrie is not None:
            if x.geometrie.get("type") not in ("Polygon", "MultiPolygon") or len(json.dumps(x.geometrie)) > 200_000:
                raise HTTPException(422, "Polygone GeoJSON attendu (200 Ko au plus)")
            try:
                g = await c.fetchrow(
                    "SELECT ST_IsValid(g) AS valide, ST_Area(g::geography) / 1e6 AS km2 "
                    "FROM (SELECT ST_SetSRID(ST_GeomFromGeoJSON($1), 4326) AS g) x", json.dumps(x.geometrie))
            except Exception:
                raise HTTPException(422, "Polygone illisible") from None
            if not g["valide"] or not 0.01 <= g["km2"] <= 50_000:
                raise HTTPException(422, "Polygone invalide (côtés croisés) ou surface hors de 0,01 à 50 000 km²")
            zid = await c.fetchval(
                "INSERT INTO zones_vigilance (nom, motif, profil, geom, fin_le, cree_par) VALUES ($1, $2, $3, "
                "ST_SetSRID(ST_GeomFromGeoJSON($4), 4326)::geography, $5, $6) RETURNING id",
                x.nom.strip(), x.motif.strip(), x.profil, json.dumps(x.geometrie), x.fin_le, op)
        else:
            b = (rules.get("zones_vigilance") or {}).get("tampon_m") or {"min": 500, "max": 20000, "defaut": 3704}
            tampon = x.tampon_m if x.tampon_m is not None else b["defaut"]
            if not b["min"] <= tampon <= b["max"]:
                raise HTTPException(422, f"Tampon hors de la plage {b['min']} à {b['max']} m")
            zid = await c.fetchval(
                "INSERT INTO zones_vigilance (nom, motif, profil, geom, infrastructure_id, tampon_m, fin_le, cree_par) "
                "SELECT $1, $2, $3, ST_Buffer(geom, $5), id, $5, $6, $7 FROM infrastructure WHERE id = $4 RETURNING id",
                x.nom.strip(), x.motif.strip(), x.profil, x.infrastructure_id, tampon, x.fin_le, op)
            if zid is None:
                raise HTTPException(404, "Infrastructure inconnue")
        await journaliser(c, op, "zone_creee", f"zone:{zid}", None,
                          {"id": zid, "nom": x.nom.strip(), "profil": x.profil, "fin_le": x.fin_le,
                           "infrastructure_id": x.infrastructure_id}, x.motif.strip())
    return {"id": zid}


class Retrait(BaseModel):
    motif: str = Field(min_length=3, max_length=300)


@router.post("/api/controle/zones/{zid}/retirer")
async def retirer_zone(request: Request, zid: int, x: Retrait, op: str = Depends(exiger_operateur)):
    """Zone retirée (jamais supprimée) : les alertes qui la portent gardent la zone et le profil appliqués."""
    async with request.app.state.pool.acquire() as c, c.transaction():
        nom = await c.fetchval("UPDATE zones_vigilance SET retiree_le = now(), retiree_par = $2 WHERE id = $1 "
                               "AND retiree_le IS NULL RETURNING nom", zid, op)
        if nom is None:
            raise HTTPException(409, "Zone inconnue ou déjà retirée")
        await journaliser(c, op, "zone_retiree", f"zone:{zid}", {"id": zid, "nom": nom}, None, x.motif.strip())
    return {"id": zid, "retiree": True}


class Suivis(BaseModel):
    actif: bool
    motif: str = Field(min_length=3, max_length=300)


@router.post("/api/controle/suivis")
async def suivis_prioritaires(request: Request, x: Suivis, op: str = Depends(exiger_operateur)):
    async with request.app.state.pool.acquire() as c, c.transaction():
        avant = await c.fetchval("SELECT valeur FROM reglages_controle WHERE cle = 'suivis:prioritaires' FOR UPDATE")
        await c.execute("INSERT INTO reglages_controle (cle, valeur, auteur) VALUES ('suivis:prioritaires', "
                        "$1::text::jsonb, $2) ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur, "
                        "auteur = EXCLUDED.auteur, mis_a_jour_le = now()", json.dumps(x.actif), op)
        await journaliser(c, op, "suivis_prioritaires", "suivis", {"valeur": avant}, {"valeur": x.actif}, x.motif.strip())
    return {"suivis_prioritaires": x.actif}


# Démonstration (phase F) : masquage des navires (cookie de session) et scénarios enregistrés

@router.get("/api/controle/demo")
async def demo_etat(request: Request):
    import demo
    return {"masquage": demo.graine_valide(request.cookies.get(demo.COOKIE)) is not None}


class Demo(BaseModel):
    masquage: bool


@router.post("/api/controle/demo")
async def demo_basculer(request: Request, response: Response, x: Demo):
    """Masquage pour une démonstration publique, propre à ce navigateur : graine aléatoire dans un cookie de session
    (effacé à la fermeture). Sans connexion requise : il ne fait que cacher ; journalisé avec l'opérateur s'il y en a un."""
    import demo
    _meme_origine(request)
    if x.masquage:
        response.set_cookie(demo.COOKIE, demo.nouvelle_graine(), httponly=True, samesite="strict",
                            secure=_https(request), path="/api")
    else:
        response.delete_cookie(demo.COOKIE, path="/api")
    async with request.app.state.pool.acquire() as c:
        await journaliser(c, await operateur_connecte(request) or "visiteur", "demonstration", "masquage", None,
                          {"masquage": x.masquage})
    return {"masquage": x.masquage}


@router.get("/api/controle/scenarios")
async def scenarios(request: Request):
    async with request.app.state.pool.acquire() as c:
        rows = await c.fetch("SELECT id, nom, adresse, debut, fin, region, selection, cree_le, cree_par "
                             "FROM scenarios_demo WHERE retire_le IS NULL ORDER BY cree_le DESC")
    return [dict(r) for r in rows]


class Scenario(BaseModel):
    nom: str = Field(min_length=2, max_length=80)
    adresse: str = Field(min_length=1, max_length=2000)


@router.post("/api/controle/scenarios")
async def enregistrer_scenario(request: Request, x: Scenario, op: str = Depends(exiger_operateur)):
    """Vue courante enregistrée (adresse de la page) : période, instant, région, objet sélectionné, couches."""
    from urllib.parse import parse_qs
    q = {k: v[0] for k, v in parse_qs(x.adresse.lstrip("?")).items()}
    if not set(q) <= {"mode", "duree", "debut", "fin", "instant", "vitesse", "sel", "couches", "region", "zone"}:
        raise HTTPException(422, "Adresse inattendue : paramètres de la page seulement")

    def instant(k):
        try:
            return datetime.fromisoformat(q[k].replace("Z", "+00:00")) if k in q else None
        except ValueError:
            raise HTTPException(422, f"Date illisible : {k}") from None
    async with request.app.state.pool.acquire() as c, c.transaction():
        sid = await c.fetchval("INSERT INTO scenarios_demo (nom, adresse, debut, fin, region, selection, cree_par) "
                               "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id", x.nom.strip(), x.adresse.lstrip("?"),
                               instant("debut"), instant("fin"), q.get("region"), q.get("sel"), op)
        await journaliser(c, op, "scenario", f"scenario:{sid}", None, {"nom": x.nom.strip(), "adresse": x.adresse})
    return {"id": sid}


@router.post("/api/controle/scenarios/{sid}/retirer")
async def retirer_scenario(request: Request, sid: int, op: str = Depends(exiger_operateur)):
    async with request.app.state.pool.acquire() as c, c.transaction():
        nom = await c.fetchval("UPDATE scenarios_demo SET retire_le = now() WHERE id = $1 AND retire_le IS NULL "
                               "RETURNING nom", sid)
        if nom is None:
            raise HTTPException(409, "Scénario inconnu ou déjà retiré")
        await journaliser(c, op, "scenario_retire", f"scenario:{sid}", {"nom": nom}, None)
    return {"id": sid, "retire": True}


class Plafond(BaseModel):
    valeur: int
    motif: str = Field(min_length=3, max_length=300)


@router.post("/api/controle/plafonds/{source}")
async def plafond(request: Request, source: str, x: Plafond, op: str = Depends(exiger_operateur)):
    try:
        v = controle.verifier_plafond(load_rules(), source, x.valeur)
    except ValueError as e:
        raise HTTPException(422, str(e))
    cle = f"plafond:{source}"
    async with request.app.state.pool.acquire() as c, c.transaction():
        avant = await c.fetchval("SELECT valeur FROM reglages_controle WHERE cle = $1 FOR UPDATE", cle)
        await c.execute("INSERT INTO reglages_controle (cle, valeur, auteur) VALUES ($1, $2::text::jsonb, $3) "
                        "ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur, auteur = EXCLUDED.auteur, "
                        "mis_a_jour_le = now()", cle, json.dumps(v), op)
        await journaliser(c, op, "plafond", source, {"valeur": avant}, {"valeur": v}, x.motif.strip())
    return {"source": source, "valeur": v}


class DemandePoint(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    rayon_m: float
    motif: str = Field(min_length=3, max_length=300)


@router.post("/api/controle/demandes")
async def demande(request: Request, x: DemandePoint, op: str = Depends(exiger_operateur)):
    """Demande d'observation manuelle d'un point (panneau) : rayon borné par controle.rayon_demande_m ; refusée si tip
    and cue est coupé (fichier) ou en pause (panneau)."""
    rules = load_rules()
    tc = rules.get("tip_and_cue") or {}
    b = (rules.get("controle") or {}).get("rayon_demande_m") or {"min": 1000, "max": 30000}
    if not b["min"] <= x.rayon_m <= b["max"]:
        raise HTTPException(422, f"Rayon hors de la plage {b['min']} à {b['max']} m")
    if not tc.get("actif") or not (tc.get("tips", {}).get("operateur") or {}).get("actif"):
        raise HTTPException(409, "Tip and cue coupé (config/rules.yaml, tip_and_cue.actif)")
    now = datetime.now(timezone.utc)
    async with request.app.state.pool.acquire() as c, c.transaction():
        if await c.fetchval("SELECT source_en_pause('tip_and_cue')"):
            raise HTTPException(409, "Tip and cue en pause (panneau de contrôle)")
        prio = int(tc["tips"]["operateur"]["priorite"])
        did = await c.fetchval(
            """INSERT INTO demandes_observation (tip, priorite, position, position_le, expire_le, motif, auteur,
                                                 rayon_demande_m, trace_id, traceparent)
               VALUES ('operateur', $1, ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography, $4, $5, $6, $7, $8, $9, $10)
               RETURNING id""",
            prio, x.lon, x.lat, now, now + timedelta(hours=tc["expiration_h"]), x.motif.strip(), op, x.rayon_m,
            traces.trace_id(), traces.traceparent())
        await c.execute("INSERT INTO demandes_origines (cle, demande_id, tip, priorite, motif, trace_id) "
                        "VALUES ($1, $2, 'operateur', $3, $4, $5)", f"panneau:{did}", did, prio,
                        f"panneau, demande de {op}", traces.trace_id())
        await journaliser(c, op, "demande", "tip_and_cue", None,
                          {"demande": did, "lon": round(x.lon, 5), "lat": round(x.lat, 5), "rayon_m": x.rayon_m},
                          x.motif.strip())
    return {"id": did, "etat": "en_attente"}
