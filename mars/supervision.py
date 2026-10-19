"""Supervision (étape D) : signes de vie, mesures du disque, alertes sur le téléphone par ntfy.

Après l'incident du 18/10/2026 (disque plein vers 01:00 UTC, collecte figée à 01:27 sans s'arrêter, ingestion en échec
pendant 21 h, environ 21 h de données AIS perdues, et personne de prévenu), trois couches indépendantes :
  1. la collecte et l'ingestion ne meurent plus en silence : une erreur est journalisée, et un processus figé est
     arrêté par son contrôle de santé (scripts/sante.py), puis relancé par la politique de redémarrage de Docker ;
  2. Grafana (grafana/provisioning) suit les tableaux et envoie les alertes sur le téléphone, par ntfy ;
  3. en secours, si Grafana est arrêté, taches envoie lui même les trois alertes les plus graves (disque, collecte,
     ingestion), sans rien écrire en base : il doit fonctionner disque plein.

Les seuils sont dans config/supervision.env, lu par taches, la collecte, l'ingestion et Grafana (un seul fichier).
Aucun message envoyé ne contient de donnée sensible : ni nom de navire, ni position, ni clé ; le sujet ntfy
(NTFY_SUJET) est le seul secret, il ne quitte jamais le .env et n'est jamais journalisé.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Fichier touché par la collecte après chaque écriture réussie d'un fichier Parquet (mise à jour de la date seule : sans
# besoin de place, même disque plein). Lu par son contrôle de santé et par taches.
SIGNE_COLLECTE = ".signe_de_vie_collecte"
# Même principe pour l'ingestion, dans son propre conteneur (le dossier de la collecte y est en lecture seule)
SIGNE_INGESTION = "/tmp/signe_de_vie_ingestion"


def seuil(nom: str, defaut: float) -> float:
    """Seuil de config/supervision.env (variables d'environnement des services), valeur par défaut sinon."""
    try:
        return float(os.environ.get(nom, defaut))
    except ValueError:
        return defaut


def seuils() -> dict:
    return {"disque_pct": seuil("SEUIL_DISQUE_LIBRE_PCT", 15), "collecte_min": seuil("SEUIL_COLLECTE_MIN", 5),
            "ingestion_min": seuil("SEUIL_INGESTION_MIN", 10), "heure_nuit_viirs": seuil("HEURE_NUIT_VIIRS", 8),
            "echecs_successifs": seuil("ECHECS_SUCCESSIFS", 2)}


def toucher(path: Path | str):
    """Signe de vie : date du fichier mise à jour (créé s'il manque). Ne demande aucune place une fois le fichier créé."""
    p = Path(path)
    try:
        os.utime(p, None)
    except FileNotFoundError:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()


def age_s(path: Path | str, now: float | None = None) -> float | None:
    """Âge en secondes de la dernière mise à jour d'un fichier, None s'il n'existe pas."""
    try:
        return (time.time() if now is None else now) - os.stat(path).st_mtime
    except FileNotFoundError:
        return None


# Mesures

def dernier_parquet(live: Path, now: datetime | None = None) -> datetime | None:
    """Dernier fichier Parquet de positions écrit, lu dans les dossiers du jour et de la veille (les journées terminées
    partent sur R2 chaque nuit : quelques milliers de fichiers au plus). Indépendant du signe de vie de la collecte."""
    now = now or datetime.now(timezone.utc)
    best = None
    for day in (now.date(), now.date() - timedelta(days=1)):
        for folder in (live / "positions").glob(f"zone=*/date={day:%Y-%m-%d}"):
            try:
                for e in os.scandir(folder):
                    if e.name.endswith(".parquet") and not e.name.startswith("."):
                        m = e.stat().st_mtime
                        best = m if best is None or m > best else best
            except FileNotFoundError:
                continue
    return datetime.fromtimestamp(best, timezone.utc) if best else None


def taille_dossier(path: Path) -> tuple[int, int]:
    """Octets et nombre de fichiers d'un dossier (parcours sans suivre les liens)."""
    total = n = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                        n += 1
        except (FileNotFoundError, PermissionError):
            continue
    return total, n


def mesurer(conn, live: Path, disque: str = "/") -> dict:
    """Mesure de toutes les 10 minutes, historisée (mesures_disque) : disque, base, positions, collecte. Un an gardé."""
    u = shutil.disk_usage(disque)
    base, positions = conn.execute("SELECT pg_database_size(current_database()), "
                                   "pg_total_relation_size('positions')").fetchone()
    octets, fichiers = taille_dossier(live)
    m = {"total_octets": u.total, "libre_octets": u.free, "libre_pct": round(100.0 * u.free / u.total, 2),
         "base_octets": base, "positions_octets": positions, "ais_live_octets": octets, "ais_live_fichiers": fichiers,
         "dernier_parquet": dernier_parquet(live)}
    conn.execute("INSERT INTO mesures_disque (total_octets, libre_octets, libre_pct, base_octets, positions_octets, "
                 "ais_live_octets, ais_live_fichiers, dernier_parquet) VALUES (%(total_octets)s, %(libre_octets)s, "
                 "%(libre_pct)s, %(base_octets)s, %(positions_octets)s, %(ais_live_octets)s, %(ais_live_fichiers)s, "
                 "%(dernier_parquet)s)", m)
    conn.execute("DELETE FROM mesures_disque WHERE mesure_le < now() - interval '365 days'")
    return m


def signe(conn, service: str, dernier_le: datetime, detail: dict | None = None):
    conn.execute("INSERT INTO signes_de_vie (service, dernier_le, detail, mesure_le) VALUES (%s, %s, %s, now()) "
                 "ON CONFLICT (service) DO UPDATE SET dernier_le = EXCLUDED.dernier_le, detail = EXCLUDED.detail, "
                 "mesure_le = now()", (service, dernier_le, json.dumps(detail or {}, default=str)))


def signe_collecte(live: Path) -> datetime | None:
    """Dernier fichier écrit par la collecte : signe de vie de la collecte, sinon dernier fichier trouvé."""
    a = age_s(live / SIGNE_COLLECTE)
    t = dernier_parquet(live)
    if a is not None:
        s = datetime.now(timezone.utc) - timedelta(seconds=a)
        t = s if t is None or s > t else t
    return t


def parametres(conn, rules: dict):
    """Paramètres des tableaux et seuils des règles d'alerte de Grafana, recopiés depuis config/rules.yaml et
    config/supervision.env (toutes les 10 minutes et au démarrage de taches). Grafana 12 ne remplace pas les variables
    d'environnement dans les règles d'alerte provisionnées : elles lisent leurs seuils dans cette table."""
    s = rules.get("sentinel", {})
    values = {"budget_sentinel_eur": s.get("budget_mensuel_eur"), "scenes_max_par_jour": s.get("scenes_max_par_jour"),
              "ratio_coupure": rules.get("continu", {}).get("flux_min_ratio"),
              **{f"seuil_{k}": v for k, v in seuils().items()}}
    for k, v in values.items():
        if v is not None:
            conn.execute("INSERT INTO parametres_supervision (cle, valeur, mis_a_jour_le) VALUES (%s, %s, now()) "
                         "ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur, mis_a_jour_le = now()", (k, float(v)))


def role_grafana(conn) -> bool:
    """Donne au rôle grafana_lecture (migration 26) le mot de passe du .env et le droit de connexion. Sans mot de passe,
    le rôle reste sans connexion."""
    mdp = os.environ.get("GRAFANA_PG_MOT_DE_PASSE")
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'grafana_lecture'").fetchone()
    if not exists or not mdp:
        return False
    from psycopg import sql
    conn.execute(sql.SQL("ALTER ROLE grafana_lecture LOGIN PASSWORD {}").format(sql.Literal(mdp)))
    return True


# Alertes sur le téléphone

class Ntfy:
    """Envoi par HTTP vers ntfy (https://ntfy.sh par défaut, NTFY_URL sinon), sur le sujet secret NTFY_SUJET. Message
    court, sans donnée sensible. Le sujet n'apparaît dans aucun journal ni message d'erreur."""

    def __init__(self, sujet: str | None = None, url: str | None = None, opener=None):
        self.sujet = sujet if sujet is not None else os.environ.get("NTFY_SUJET", "")
        self.url = (url or os.environ.get("NTFY_URL") or "https://ntfy.sh").rstrip("/")
        self.opener = opener or urllib.request.urlopen

    @classmethod
    def from_env(cls):
        n = cls()
        return n if n.sujet else None

    def envoyer(self, titre: str, message: str, priorite: str = "high", tags: str = "warning") -> int:
        # Les en têtes HTTP sont en latin 1 : le titre passe en UTF 8 par l'encodage RFC 2047 que ntfy décode
        req = urllib.request.Request(f"{self.url}/{self.sujet}", data=message.encode("utf-8"), method="POST",
                                     headers={"Title": _rfc2047(titre), "Priority": priorite, "Tags": tags})
        try:
            with self.opener(req, timeout=15) as r:
                return r.status
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"ntfy a refusé l'envoi (HTTP {e.code})") from None
        except Exception as e:
            raise RuntimeError(f"ntfy injoignable ({type(e).__name__})") from None


def _rfc2047(s: str) -> str:
    import base64
    return "=?UTF-8?B?" + base64.b64encode(s.encode("utf-8")).decode() + "?="


def conditions(conn, live: Path, disque: str = "/", now: datetime | None = None) -> dict[str, str | None]:
    """Les trois alertes les plus graves, évaluées sans rien écrire (le disque peut être plein) : motif si active,
    None sinon. Une lecture impossible de la base compte comme ingestion en échec."""
    now = now or datetime.now(timezone.utc)
    s = seuils()
    out: dict[str, str | None] = {}
    u = shutil.disk_usage(disque)
    pct = 100.0 * u.free / u.total
    out["disque"] = (f"{pct:.0f} % libres ({u.free / 1e9:.1f} Go), seuil {s['disque_pct']:.0f} %"
                     if pct < s["disque_pct"] else None)
    t = signe_collecte(live)
    age = None if t is None else (now - t).total_seconds() / 60
    out["collecte"] = (("aucun fichier écrit" if age is None else f"aucun fichier écrit depuis {age:.0f} min")
                       if age is None or age > s["collecte_min"] else None)
    try:
        row = conn.execute("SELECT dernier_le, detail FROM signes_de_vie WHERE service = 'ingestion'").fetchone()
    except Exception as e:
        out["ingestion"] = f"base illisible ({type(e).__name__})"
        return out
    if row is None:
        out["ingestion"] = "aucun passage réussi enregistré"
    else:
        silence = (now - row[0]).total_seconds() / 60
        retard = float((row[1] or {}).get("retard_s") or 0) / 60
        if silence > s["ingestion_min"]:
            out["ingestion"] = f"aucun passage réussi depuis {silence:.0f} min"
        elif retard > s["ingestion_min"]:
            out["ingestion"] = f"en retard de {retard:.0f} min"
        else:
            out["ingestion"] = None
    return out


TITRES = {"disque": "MARS C2 : disque presque plein", "collecte": "MARS C2 : collecte AIS figée",
          "ingestion": "MARS C2 : ingestion AIS en échec"}


class Secours:
    """Alertes de secours de taches, quand Grafana ne répond pas : une alerte active est envoyée, puis répétée au plus
    une fois par heure ; son retour à la normale est annoncé une fois. État gardé en mémoire (rien en base : taches doit
    alerter disque plein)."""

    def __init__(self, ntfy: Ntfy | None, repetition_s: float = 3600):
        self.ntfy, self.repetition_s = ntfy, repetition_s
        self.actives: dict[str, float] = {}         # clé de l'alerte : heure du dernier envoi

    def evaluer(self, etat: dict[str, str | None], now: float | None = None, log=print) -> list[str]:
        now = time.time() if now is None else now
        envoyes = []
        for cle, motif in etat.items():
            if motif and (cle not in self.actives or now - self.actives[cle] >= self.repetition_s):
                if self._envoyer(TITRES[cle], f"{motif}. Alerte de secours (Grafana ne répond pas).", "urgent",
                                 "rotating_light", log):
                    self.actives[cle] = now
                    envoyes.append(cle)
            elif not motif and cle in self.actives:
                if self._envoyer(TITRES[cle].split(" : ")[0] + " : retour à la normale",
                                 f"{TITRES[cle].split(' : ')[1].capitalize()} : rétabli.", "default", "white_check_mark", log):
                    del self.actives[cle]
                    envoyes.append(f"{cle}_retablie")
        return envoyes

    def _envoyer(self, titre: str, message: str, priorite: str, tags: str, log) -> bool:
        if self.ntfy is None:
            log(f"ALERTE (NTFY_SUJET absent, rien n'est envoyé) : {titre}, {message}")
            return True
        try:
            self.ntfy.envoyer(titre, message, priorite, tags)
            log(f"alerte envoyée sur le téléphone : {titre}")
            return True
        except RuntimeError as e:
            log(f"alerte NON envoyée ({e}) : {titre} ; nouvel essai à la minute suivante")
            return False


def grafana_repond(url: str | None = None, opener=None) -> bool:
    """Grafana en service (et sa base joignable) : /api/health répond « ok »."""
    url = url or os.environ.get("GRAFANA_URL", "http://grafana:3000")
    try:
        with (opener or urllib.request.urlopen)(f"{url}/api/health", timeout=5) as r:
            return r.status == 200 and json.loads(r.read() or b"{}").get("database") == "ok"
    except Exception:
        return False


# Durée des requêtes des tableaux (taches.py mesurer-tableaux) : lecture seule, sous le rôle de Grafana

def developper(sql: str, debut: datetime, fin: datetime) -> str:
    """Macros de Grafana remplacées comme le fait sa source PostgreSQL ($__timeFilter, $__timeFrom, $__timeTo,
    $__timeGroupAlias avec remplissage)."""
    import re
    a, b = f"'{debut.isoformat()}'::timestamptz", f"'{fin.isoformat()}'::timestamptz"

    def groupe(m):
        n, u = int(m.group(2)), m.group(3)
        s = n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u]
        return f'to_timestamp(floor(extract(epoch FROM {m.group(1)}) / {s}) * {s}) AS "time"'
    sql = re.sub(r"\$__timeGroupAlias\(\s*([^,]+?)\s*,\s*'(\d+)([smhd])'\s*(?:,\s*[^)]*)?\)", groupe, sql)
    sql = re.sub(r"\$__timeFilter\(\s*([^)]+?)\s*\)", lambda m: f"{m.group(1)} BETWEEN {a} AND {b}", sql)
    return sql.replace("$__timeFrom()", a).replace("$__timeTo()", b)


def mesurer_requetes(conn, requetes: list[tuple[str, str, str]], jours=(2, 30), role: str = "grafana_lecture",
                     now: datetime | None = None) -> list[dict]:
    """Chaque requête exécutée sous le rôle de Grafana, dans une transaction en lecture seule annulée ensuite, sur deux
    périodes (48 h et 30 jours) : durée et nombre de lignes, ou l'erreur (droit manquant, syntaxe)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for tableau, panneau, sql in requetes:
        for j in jours:
            q = developper(sql, now - timedelta(days=j), now)
            r = {"tableau": tableau, "panneau": panneau, "jours": j}
            t = time.perf_counter()
            try:
                with conn.transaction(force_rollback=True):
                    conn.execute("SET TRANSACTION READ ONLY")
                    conn.execute(f"SET LOCAL ROLE {role}")
                    conn.execute("SET LOCAL statement_timeout = '10s'")
                    r["lignes"] = len(conn.execute(q).fetchall())
            except Exception as e:
                r["erreur"] = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
            r["duree_s"] = round(time.perf_counter() - t, 3)
            out.append(r)
    return out
