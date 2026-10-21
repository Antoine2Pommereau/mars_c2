"""Panneau de contrôle (lot P) : état en base qui prime sur config/rules.yaml, journal de chaque action.

Partagé par le conteneur taches (psycopg) et l'API (asyncpg, backend/controle.py) : les calculs sont purs, les accès
à la base passent par des requêtes simples écrites ici une fois.

* Interrupteurs des analyses satellites (sentinel1, sentinel2, viirs, tip_and_cue) : une source en pause ne crée aucun
  travailleur ; reprise automatique à la date prévue, notification la veille.
* Réglages bornés par le fichier : plafond quotidien de scènes de Sentinel 1 et 2 (section controle.plafonds).
* `appliquer(conn, rules)` : copie des règles où l'état du panneau est appliqué (plafonds), avec `rules["_controle"]`
  (pauses) ; les modules (sentinel, sentinel2, tip_and_cue) ne lisent que ce dictionnaire, jamais la base pour cela.
* Journal (`journaliser`) : qui, quand, quoi, avant, après, motif, trace.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from mars import traces

SOURCES = ("sentinel1", "sentinel2", "viirs", "tip_and_cue")
TOUT = ("sentinel1", "sentinel2", "tip_and_cue")          # « tout » : VIIRS seulement s'il est nommé
NOMS = {"sentinel1": "Sentinel 1", "sentinel2": "Sentinel 2", "viirs": "VIIRS", "tip_and_cue": "Tip and cue"}
TACHE = {"sentinel1": "sentinel", "sentinel2": "sentinel2", "viirs": "viirs"}   # travailleurs.tache
PLAFONDS = {"sentinel1": "sentinel", "sentinel2": "sentinel2"}                   # section de config/rules.yaml


# Journal

def journaliser(conn, auteur: str, action: str, cible: str | None, avant=None, apres=None, motif: str | None = None):
    """Une action du panneau ou d'une commande équivalente (psycopg). Jamais de secret dans avant ou après."""
    conn.execute("INSERT INTO journal_controle (auteur, action, cible, avant, apres, motif, trace_id) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                 (auteur, action, cible, _json(avant), _json(apres), motif, traces.trace_id()))
    traces.attributs(**{"controle.action": action, "controle.cible": cible or "", "controle.auteur": auteur})


def _json(v):
    return None if v is None else json.dumps(v, default=str, ensure_ascii=False)


# Interrupteurs

COLS_INTERRUPTEUR = "source, en_pause, depuis, jusqu_a, motif, auteur, veille_notifiee_le"


def interrupteur(row) -> dict:
    return dict(zip(COLS_INTERRUPTEUR.split(", "), row)) if not hasattr(row, "keys") else dict(row)


def pauses(conn) -> dict[str, dict]:
    """Sources en pause : {source: {motif, depuis, jusqu_a, auteur}}. Une pause échue compte encore jusqu'à sa reprise
    par la boucle (une minute au plus)."""
    rows = conn.execute(f"SELECT {COLS_INTERRUPTEUR} FROM interrupteurs_satellites WHERE en_pause").fetchall()
    return {r[0]: {k: v for k, v in interrupteur(r).items() if k != "source"} for r in rows}


def en_pause(rules: dict, source: str) -> str | None:
    """Motif de la pause de `source` d'après les règles appliquées (None si en service ou règles du fichier seules)."""
    p = ((rules.get("_controle") or {}).get("pauses") or {}).get(source)
    if p is None:
        return None
    fin = p.get("jusqu_a")
    reprise = f", reprise le {_paris(fin):%d/%m/%Y à %H:%M}" if fin else ""
    return f"{NOMS[source]} en pause ({p.get('motif') or 'sans motif'}{reprise})"


def _paris(t: datetime) -> datetime:
    from zoneinfo import ZoneInfo
    return t.astimezone(ZoneInfo("Europe/Paris"))


def basculer(conn, source: str, en_pause_: bool, auteur: str, motif: str | None = None,
             jusqu_a: datetime | None = None, envoi=None) -> dict:
    """Met `source` en pause ou en service (psycopg), journalise et notifie (ntfy). Retourne l'état après."""
    if source not in SOURCES:
        raise ValueError(f"source inconnue : {source} ({', '.join(SOURCES)})")
    if en_pause_ and not (motif or "").strip():
        raise ValueError("motif obligatoire pour une pause")
    if jusqu_a is not None and jusqu_a <= datetime.now(timezone.utc):
        raise ValueError("date de reprise déjà passée")
    avant = interrupteur(conn.execute(f"SELECT {COLS_INTERRUPTEUR} FROM interrupteurs_satellites WHERE source = %s "
                                      "FOR UPDATE", (source,)).fetchone())
    if en_pause_:
        conn.execute("UPDATE interrupteurs_satellites SET en_pause = true, depuis = CASE WHEN en_pause THEN depuis "
                     "ELSE now() END, jusqu_a = %s, motif = %s, auteur = %s, veille_notifiee_le = NULL "
                     "WHERE source = %s", (jusqu_a, motif.strip(), auteur, source))
    else:
        conn.execute("UPDATE interrupteurs_satellites SET en_pause = false, depuis = now(), jusqu_a = NULL, motif = %s, "
                     "auteur = %s, veille_notifiee_le = NULL WHERE source = %s", ((motif or "").strip() or None,
                                                                                  auteur, source))
    apres = interrupteur(conn.execute(f"SELECT {COLS_INTERRUPTEUR} FROM interrupteurs_satellites WHERE source = %s",
                                      (source,)).fetchone())
    action = "pause" if en_pause_ else "reprise"
    journaliser(conn, auteur, action, source, _public(avant), _public(apres), motif)
    if bool(avant["en_pause"]) != en_pause_ or en_pause_:
        fin = f" jusqu'au {_paris(jusqu_a):%d/%m/%Y %H:%M}" if jusqu_a else ""
        (envoi if envoi is not None else notifier)("satellites", "info",
                 f"MARS C2 : {NOMS[source]} {'en pause' if en_pause_ else 'repris'}",
                 f"{'Pause' if en_pause_ else 'Reprise'}{fin} par {auteur}" + (f" : {motif}" if motif else ""))
    return apres


def _public(etat: dict) -> dict:
    return {k: v for k, v in etat.items() if k in ("en_pause", "jusqu_a", "motif")}


def echeances(conn, now: datetime, envoi=None) -> dict:
    """Chaque minute (taches) : reprise automatique des pauses échues, notification la veille d'une reprise prévue."""
    envoi = envoi if envoi is not None else notifier
    out = {"reprises": [], "veilles": []}
    for (source,) in conn.execute("SELECT source FROM interrupteurs_satellites WHERE en_pause AND jusqu_a <= %s",
                                  (now,)).fetchall():
        basculer(conn, source, False, "automatique", "reprise prévue atteinte", envoi=envoi)
        out["reprises"].append(source)
    for source, fin in conn.execute(
            "SELECT source, jusqu_a FROM interrupteurs_satellites WHERE en_pause AND jusqu_a > %s AND jusqu_a <= %s "
            "AND veille_notifiee_le IS NULL", (now, now + timedelta(hours=24))).fetchall():
        conn.execute("UPDATE interrupteurs_satellites SET veille_notifiee_le = now() WHERE source = %s", (source,))
        envoi("satellites", "info", f"MARS C2 : reprise de {NOMS[source]} demain",
                 f"Reprise automatique le {_paris(fin):%d/%m/%Y à %H:%M} (heure de Paris)")
        out["veilles"].append(source)
    return out


# Plafonds quotidiens de scènes, bornés par config/rules.yaml (controle.plafonds)

def bornes_plafond(rules: dict, source: str) -> tuple[int, int]:
    b = ((rules.get("controle") or {}).get("plafonds") or {}).get(source) or {}
    return int(b.get("min", 0)), int(b.get("max", 4))


def verifier_plafond(rules: dict, source: str, valeur: int) -> int:
    if source not in PLAFONDS:
        raise ValueError(f"aucun plafond réglable pour {source}")
    lo, hi = bornes_plafond(rules, source)
    if not lo <= int(valeur) <= hi:
        raise ValueError(f"plafond de {NOMS[source]} hors de la plage {lo} à {hi} (config/rules.yaml, controle.plafonds)")
    return int(valeur)


def reglages(conn) -> dict:
    return {k: v for k, v in conn.execute("SELECT cle, valeur FROM reglages_controle").fetchall()}


def regler(conn, cle: str, valeur, auteur: str, motif: str | None, action: str, cible: str | None) -> dict:
    """Écrit un réglage (psycopg) et le journalise avec sa valeur précédente."""
    row = conn.execute("SELECT valeur FROM reglages_controle WHERE cle = %s FOR UPDATE", (cle,)).fetchone()
    conn.execute("INSERT INTO reglages_controle (cle, valeur, auteur) VALUES (%s, %s, %s) ON CONFLICT (cle) DO UPDATE "
                 "SET valeur = EXCLUDED.valeur, auteur = EXCLUDED.auteur, mis_a_jour_le = now()",
                 (cle, json.dumps(valeur), auteur))
    journaliser(conn, auteur, action, cible, {"valeur": row[0] if row else None}, {"valeur": valeur}, motif)
    return {"cle": cle, "valeur": valeur}


def appliquer_reglages(rules: dict, regl: dict, pauses_: dict) -> dict:
    """Règles appliquées (calcul pur) : plafonds du panneau dans leurs bornes, pauses dans rules["_controle"]."""
    r = copy.deepcopy(rules)
    for source, section in PLAFONDS.items():
        v = regl.get(f"plafond:{source}")
        if v is not None and section in r:
            lo, hi = bornes_plafond(rules, source)
            r[section]["scenes_max_par_jour"] = max(lo, min(hi, int(v)))
    r["_controle"] = {"pauses": pauses_}
    return r


def appliquer(conn, rules: dict) -> dict:
    """Règles du fichier, avec l'état du panneau qui prime (psycopg)."""
    return appliquer_reglages(rules, reglages(conn), pauses(conn))


# Budget du mois et coût des travailleurs

def budget(conn, rules: dict, now: datetime) -> dict:
    """Dépensé (coût réel des travailleurs détruits, estimé à défaut), réservé (travailleurs en vie, coût estimé de
    leurs analyses ou de leur durée de vie) et restant, par source et au total. Le budget mensuel (sentinel.
    budget_mensuel_eur) est partagé par Sentinel 1 et 2 ; VIIRS est compté hors de ce budget."""
    mois = debut_mois(now)
    return budget_calcul(conn.execute(BUDGET_SQL.format(p="%s"), (mois,)).fetchall(), rules, mois)


def debut_mois(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# {p} : marque du paramètre, %s pour psycopg, $1 pour asyncpg
BUDGET_SQL = """SELECT t.tache,
       sum(coalesce(t.cout_reel_eur, (t.mesures->>'cout_estime_eur')::float8)) FILTER (WHERE t.detruit_le IS NOT NULL),
       sum(coalesce((SELECT sum(a.cout_estime_eur) FROM analyses a WHERE a.travailleur_id = t.id),
                    (t.mesures->>'cout_estime_eur')::float8, 0)) FILTER (WHERE t.detruit_le IS NULL),
       count(*)
FROM travailleurs t WHERE t.cree_le >= {p} GROUP BY t.tache"""


def budget_calcul(rows, rules: dict, mois: datetime) -> dict:
    lignes = {r[0]: {"depense": float(r[1] or 0), "reserve": float(r[2] or 0), "travailleurs": int(r[3])} for r in rows}
    plafond = float(rules["sentinel"]["budget_mensuel_eur"])
    out = {}
    for source, tache in TACHE.items():
        x = lignes.get(tache, {"depense": 0.0, "reserve": 0.0, "travailleurs": 0})
        out[source] = {k: round(v, 2) if isinstance(v, float) else v for k, v in x.items()}
    partage = sum(out[s]["depense"] + out[s]["reserve"] for s in ("sentinel1", "sentinel2"))
    total = {"depense": round(sum(o["depense"] for o in out.values()), 2),
             "reserve": round(sum(o["reserve"] for o in out.values()), 2)}
    return {"mois": mois.date().isoformat(), "sources": out, "total": total,
            "budget_sentinel": {"plafond": plafond, "engage": round(partage, 2),
                                "restant": round(max(plafond - partage, 0.0), 2)}}


# Notifications : envoi par défaut (ntfy) ; la phase D y branchera les préférences et les heures calmes

def notifier(categorie: str, gravite: str, titre: str, message: str):
    from mars import supervision
    n = supervision.Ntfy.from_env()
    if n is None:
        return None
    try:
        return n.envoyer(titre, message, priorite="high" if gravite in ("grave", "elevee", "critique") else "default",
                         tags="satellite" if categorie == "satellites" else "warning")
    except Exception as e:                       # une notification ne bloque jamais l'action
        traces.erreur(f"ntfy : {e}")
        return None


# Système (phase B) : liens vers Grafana et vers une trace

def grafana_url() -> str:
    """Adresse de Grafana vue du navigateur de l'opérateur (par le tunnel SSH par défaut)."""
    return (os.environ.get("MARS_GRAFANA_URL") or "http://localhost:3000").rstrip("/")


def lien_trace(trace_id: str | None) -> str | None:
    """Exploration Tempo de Grafana sur une trace (vue Gantt), comme les liens des tableaux (grafana/generer.py)."""
    if not trace_id:
        return None
    from urllib.parse import quote
    pane = {"a": {"datasource": "tempo", "queries": [{"refId": "A", "datasource": {"type": "tempo", "uid": "tempo"},
                                                         "queryType": "traceql", "query": trace_id}],
                  "range": {"from": "now-3d", "to": "now"}}}
    return f"{grafana_url()}/explore?schemaVersion=1&orgId=1&panes=" + quote(json.dumps(pane, separators=(",", ":")),
                                                                              safe="")


# Opérateurs : mot de passe haché dans le .env (MARS_OPERATEURS), sessions par cookie

ITERATIONS = 600_000


def hacher(mot_de_passe: str, sel: bytes | None = None, iterations: int = ITERATIONS) -> str:
    """Empreinte PBKDF2 SHA 256 au format pbkdf2_sha256:<itérations>:<sel hex>:<empreinte hex> (ni $ ni espace : le
    .env la garde telle quelle)."""
    sel = sel or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(), sel, iterations)
    return f"pbkdf2_sha256:{iterations}:{sel.hex()}:{h.hex()}"


def verifier_mot_de_passe(mot_de_passe: str, empreinte: str) -> bool:
    try:
        algo, it, sel, h = empreinte.split(":")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(), bytes.fromhex(sel), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        return False


def operateurs(env: str | None = None) -> dict[str, str]:
    """MARS_OPERATEURS=nom:pbkdf2_sha256:…,autre:pbkdf2_sha256:… ; noms sans deux points ni virgule."""
    out = {}
    for e in (env if env is not None else os.environ.get("MARS_OPERATEURS", "")).split(","):
        nom, _, h = e.strip().partition(":")
        if nom and h:
            out[nom.strip()] = h.strip()
    return out


def jeton_session() -> tuple[str, str]:
    """Jeton du cookie et son empreinte (seule l'empreinte va en base)."""
    j = secrets.token_urlsafe(32)
    return j, hashlib.sha256(j.encode()).hexdigest()


def empreinte_jeton(j: str) -> str:
    return hashlib.sha256(j.encode()).hexdigest()
