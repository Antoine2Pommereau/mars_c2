"""Panneau de contrôle (lot P) : connexion des opérateurs et routes du panneau.

Les routes d'écriture exigent une session d'opérateur : mot de passe haché dans le .env (MARS_OPERATEURS, voir
mars/controle.py), cookie HttpOnly et SameSite strict, 12 h, Secure dès que l'interface est servie en https ; l'origine
d'une requête d'écriture doit être celle de l'interface. Chaque action est journalisée dans journal_controle avec le nom
de l'opérateur. Les routes de lecture ne changent pas (aucune connexion requise), comme le reste de l'API. L'état
écrit ici est lu par le conteneur taches (mars/controle.appliquer) à chaque cycle."""
import asyncio
import json
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


async def notifier(categorie: str, gravite: str, titre: str, message: str):
    await asyncio.to_thread(controle.notifier, categorie, gravite, titre, message)


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
    r = controle.appliquer_reglages(rules, regl, {})
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
        await notifier("satellites", "info", f"MARS C2 : {controle.NOMS[source]} "
                       f"{'en pause' if x.en_pause else 'repris'}",
                       f"{'Pause' if x.en_pause else 'Reprise'}{fin} par {op}" + (f" : {motif}" if motif else ""))
    return {**apres, "nom": controle.NOMS[source]}


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
