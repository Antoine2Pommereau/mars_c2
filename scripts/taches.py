"""Tâches planifiées du serveur, dans le conteneur « taches » (docker-compose.serveur.yml) : pas de cron sur
l'hôte, tout redémarre avec la plateforme.

Chaque nuit à TACHES_HEURE (UTC, 02:30 par défaut), dans cet ordre :
  1. archivage : fichiers Parquet des journées terminées compactés (un par zone, type et passage), envoyés sur R2,
     vérifiés, puis supprimés du serveur s'ils sont ingérés et confirmés ;
  2. purge : positions de plus de CONSERVATION_JOURS jours (30), seulement pour les journées archivées ;
  3. sauvegarde : pg_dump compressé vers R2, sans les données de positions, SAUVEGARDES_GARDEES (7) conservées.
Listes de surveillance, après les tâches de la nuit : OpenSanctions chaque jour, GUR chaque semaine (seulement si
Vessels1.db a changé dans son dépôt) ; téléchargement en mémoire, journal des navires ajoutés, retirés ou modifiés
dans task_runs, puis nouveau passage des règles des listes. En cas d'échec, la liste précédente reste en place et la
barre d'état de l'interface le signale.
Calendrier des passages Sentinel 1 et 2, chaque jour : catalogue public de Copernicus Data Space et plans
d'acquisition de l'ESA (métadonnées seulement), puis infrastructures et navires des listes couverts (mars/satellites.py).
Détections nocturnes VIIRS, chaque jour à VIIRS_HEURE (UTC, 06:30 par défaut, après la publication des données de
la nuit) : granules des dernières nuits pas encore traitées (rattrapage automatique), analysées par un travailleur
éphémère Scaleway (mars/travailleurs.py, mars/viirs.py). Chaque minute : surveillance des travailleurs (résultats
traités, instances finies ou trop vieilles détruites, orphelins détruits, exécutions journalisées dans task_runs).
Preuves images (vignettes des détections sur R2), chaque nuit : sans limite si liées à une alerte, 30 jours sinon.
Toutes les 10 minutes : espace disque, alerte sous ALERTE_DISQUE_PCT (15 %), et mesures historisées pour Grafana
(taille de la base, des positions, du dossier de la collecte, dernier fichier Parquet : mesures_disque).
Chaque minute : signes de vie (collecte, taches) et, si Grafana ne répond pas, alertes de secours sur le téléphone
par ntfy pour les trois plus graves (disque, collecte figée, ingestion en échec ou en retard), sans rien écrire en
base (mars/supervision.py). Chaque étape est isolée : une erreur (disque plein) n'arrête ni la boucle ni les alertes.
Toutes les 5 minutes (section continu de config/rules.yaml) : règles comportementales sur le flux en direct
(rendez vous, coupures AIS, navires des listes, changements d'identité), consignées dans task_runs (tâche regles),
puis statistiques du trafic pour la frise de l'interface (mars/frise.py).
Une tâche réussie ne rejoue pas le même jour ; une tâche en échec est retentée une heure plus tard.

Usage :
    python scripts/taches.py                          # boucle (service Docker « taches »)
    python scripts/taches.py tester-alerte            # message d'essai sur le téléphone (ntfy, NTFY_SUJET)
    python scripts/taches.py mesurer-tableaux         # durée de chaque requête des tableaux Grafana (lecture seule)
    python scripts/taches.py archiver | purger | sauvegarder | disque | regles | listes | passages | viirs
    python scripts/taches.py travailleurs             # état des travailleurs, une surveillance tout de suite
    python scripts/taches.py journal-travailleur [ID] # journal envoyé par un travailleur, état Scaleway s'il est muet
    python scripts/taches.py viirs-reevaluer          # détections VIIRS en base réévaluées (gravité, non évaluables)
    python scripts/taches.py sentinel                 # passages Sentinel 1 à analyser maintenant (sélection automatique)
    python scripts/taches.py sentinel-analyser ID      # analyse d'un passage choisi à la main (identifiant sar_passes)
    python scripts/taches.py sentinel-diagnostic       # prêt pour Sentinel ? lecture seule, aucune création, aucun coût
    python scripts/taches.py coupures-injection [--jour 2026-10-09] [--essais 20]
                                                      # test par injection de la règle des coupures (rien n'est écrit)
    python scripts/taches.py detruire-travailleurs    # arrêt d'urgence : détruit toute instance étiquetée
    python scripts/taches.py mesures [--jours 14]     # mesures de calibration (scripts/mesures_calibration.py)
    python scripts/taches.py sauvegardes              # liste des sauvegardes sur R2
    python scripts/taches.py telecharger CLE CHEMIN   # récupère un objet de R2 (restauration)
    python scripts/taches.py restaurer-positions --du 2026-10-01 --au 2026-10-30
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from mars import archive, supervision
from mars.config import ROOT, database_url, load_env, load_rules

LIVE = Path(os.environ.get("AIS_LIVE_DIR", ROOT / "data" / "ais_live"))
HOUR = os.environ.get("TACHES_HEURE", "02:30")
VIIRS_HOUR = os.environ.get("VIIRS_HEURE", "06:30")
KEEP_DAYS = int(os.environ.get("CONSERVATION_JOURS", 30))
KEEP_BACKUPS = int(os.environ.get("SAUVEGARDES_GARDEES", 7))
DISK_PCT = float(os.environ.get("ALERTE_DISQUE_PCT", 15))
DISK_PATHS = ["/", str(LIVE)]
DISK_EVERY_S = 600
RETRY_S = 3600


def log(msg: str):
    print(f"{datetime.now(timezone.utc):%d/%m/%Y %H:%M:%S}  {msg}", flush=True)


def connect() -> psycopg.Connection:
    for k in range(30):
        try:
            return psycopg.connect(database_url(), autocommit=True)
        except psycopg.OperationalError:
            if k == 29:
                raise
            time.sleep(2)


def r2():
    from mars.r2 import R2
    return R2()


def run(conn, task: str, fn, quiet: bool = False) -> dict | None:
    """Exécute une tâche et la consigne dans task_runs (traçabilité, et pas de double exécution le même jour)."""
    day = datetime.now(timezone.utc).date()
    rid = conn.execute("INSERT INTO task_runs (task, run_day, status) VALUES (%s, %s, 'en_cours') RETURNING id",
                       (task, day)).fetchone()[0]
    t = time.time()
    try:
        details = fn()
        details["duree_s"] = round(time.time() - t, 1)
        conn.execute("UPDATE task_runs SET status = 'ok', finished_at = now(), details = %s WHERE id = %s",
                     (json.dumps(details, default=str), rid))
        if not quiet or _changed(details):
            log(f"{task} : {json.dumps(details, default=str, ensure_ascii=False)}")
        return details
    except Exception as e:
        # refus : attendu (plafond, budget, clés absentes), ignoré par l'alerte « deux échecs de suite »
        conn.execute("UPDATE task_runs SET status = 'echec', finished_at = now(), details = %s WHERE id = %s",
                     (json.dumps({"erreur": str(e)[:1000], **({"refus": True} if getattr(e, "motif_seul", False) else {})}),
                      rid))
        log(f"{task} : ÉCHEC, {e}")
        if not getattr(e, "motif_seul", False):   # refus attendu (plafond, clés absentes) : le motif suffit
            traceback.print_exc()
        return None


def _changed(details) -> bool:
    """Un cycle des règles n'est journalisé que s'il crée ou retire des alertes (journaux sobres)."""
    if isinstance(details, dict):
        return bool(details.get("nouvelles") or details.get("retirees")) or any(_changed(v) for v in details.values())
    return False


def due(conn, task: str, now: datetime, every_days: int = 1, hour: str = HOUR) -> bool:
    """Tâche nocturne à lancer : heure passée, pas de réussite depuis `every_days` jours (aujourd'hui compris), pas
    d'échec depuis moins d'une heure."""
    h, m = (int(x) for x in hour.split(":"))
    if now < now.replace(hour=h, minute=m, second=0, microsecond=0):
        return False
    r = conn.execute("SELECT bool_or(status = 'ok') FILTER (WHERE run_day > %s), "
                     "max(started_at) FILTER (WHERE status = 'echec') FROM task_runs WHERE task = %s",
                     (now.date() - timedelta(days=every_days), task)).fetchone()
    return not r[0] and (r[1] is None or (now - r[1]).total_seconds() >= RETRY_S)


def tasks(conn):
    """Tâches quotidiennes, dans l'ordre : fonction, période en jours, heure de déclenchement (UTC)."""
    return {
        "archivage": (lambda: archive.run_archive(conn, r2(), LIVE, log=log), 1, HOUR),
        "purge": (lambda: archive.run_purge(conn, LIVE, KEEP_DAYS), 1, HOUR),
        "sauvegarde": (lambda: archive.run_backup(r2(), database_url(), KEEP_BACKUPS), 1, HOUR),
        "listes_opensanctions": (lambda: update_list(conn, "opensanctions"), 1, HOUR),
        "listes_gur": (lambda: update_list(conn, "gur"), 7, HOUR),
        "passages": (lambda: update_passes(conn), 1, HOUR),
        "viirs": (lambda: launch_viirs(conn), 1, VIIRS_HOUR),
        "preuves": (lambda: purge_proofs(conn), 1, HOUR),
    }


def purge_proofs(conn) -> dict:
    """Preuves images : sans limite si liées à une alerte, CONSERVATION_JOURS (30) sinon (mars/preuves.py)."""
    from mars import preuves
    return preuves.purge(conn, r2(), KEEP_DAYS)


def launch_viirs(conn) -> dict:
    """Granules des dernières nuits pas encore traitées, confiées à un travailleur éphémère (un seul à la fois)."""
    import requests
    from mars import travailleurs, viirs
    rules = load_rules()
    scw, token = travailleurs.Scaleway.from_env(), os.environ.get("EARTHDATA_TOKEN")
    if scw is None or not token:
        raise travailleurs.RefusLancement("clés absentes du .env : SCW_SECRET_KEY, SCW_PROJECT_ID, EARTHDATA_TOKEN")
    busy = conn.execute("SELECT id FROM travailleurs WHERE tache = 'viirs' AND detruit_le IS NULL").fetchone()
    if busy:                     # travailleur actif : ses nuits sont couvertes, rien à lancer (le verrou en base le garantit)
        return {"en_cours": busy[0]}
    p = viirs.plan(conn, requests.Session(), rules, datetime.now(timezone.utc))
    if not p["granules"]:
        return {"nuits": p["nuits"], "granules": 0}
    r = travailleurs.launch(conn, scw, rules, "viirs", {"granules": p["granules"], "regions": viirs.regions_boxes()},
                            {"EARTHDATA_TOKEN": token}, log=log)
    if "occupe" in r:            # un autre processus vient de lancer (verrou) : pas un échec
        return {"en_cours": r["occupe"]}
    if "refus" in r:
        raise travailleurs.RefusLancement(r["refus"])
    return {**r, "nuits": p["nuits"], "granules": len(p["granules"])}


def sentinel_suspended(conn) -> str | None:
    """Motif du dernier échec de lancement Sentinel du jour (UTC), s'il y en a un : la sélection automatique attend
    alors le lendemain au lieu de recommencer toutes les 30 minutes (même motif, mêmes passages). Le motif reste dans
    task_runs, donc dans l'indicateur « Satellites »."""
    r = conn.execute("SELECT details->>'erreur' FROM task_runs WHERE task = 'sentinel' AND status = 'echec' "
                     "AND run_day = %s ORDER BY started_at DESC LIMIT 1", (datetime.now(timezone.utc).date(),)).fetchone()
    return r and (r[0] or "échec sans motif")


def launch_sentinel(conn, pass_id: int | None = None, auto: bool = False) -> dict:
    """Passages Sentinel 1 à analyser (corridor avec un navire des listes, alerte VIIRS), ou un passage choisi à la
    main : une session GPU, dans les limites du plafond de scènes et du budget (mars/sentinel.py). `auto` : appel de
    la boucle, suspendu jusqu'au lendemain après un échec du jour."""
    import requests
    from mars import sentinel, travailleurs
    if auto:
        motif = sentinel_suspended(conn)
        if motif:
            return {"passages": 0, "suspendu_jusqu_au_lendemain": motif}
    rules = load_rules()
    scw = travailleurs.Scaleway.from_env()
    # Paire de clés Copernicus dédiée aux travailleurs (remise par le réseau privé) ; celle du serveur n'y sert pas
    missing = [k for k in ("CDSE_TRAVAILLEUR_ACCESS_KEY", "CDSE_TRAVAILLEUR_SECRET_KEY") if not os.environ.get(k)]
    if scw is None or missing:
        raise travailleurs.RefusLancement("clés absentes du .env : " + ", ".join(
            (["SCW_SECRET_KEY, SCW_PROJECT_ID"] if scw is None else []) + missing))
    busy = conn.execute("SELECT id FROM travailleurs WHERE tache = 'sentinel' AND detruit_le IS NULL").fetchone()
    if busy:
        return {"en_cours": busy[0]}
    r = sentinel.launch(conn, scw, rules, datetime.now(timezone.utc), requests.Session(), pass_id, log=log)
    if "refus" in r:
        raise travailleurs.RefusLancement(r["refus"])
    return r


def supervise_workers(conn, quiet: bool = True) -> dict | None:
    """Une minute de surveillance des travailleurs ; une erreur (API Scaleway injoignable) n'arrête pas la boucle."""
    from mars import sentinel, travailleurs, viirs
    rules = load_rules()
    try:
        out = travailleurs.supervise(conn, travailleurs.Scaleway.from_env(), rules,
                                     {"viirs": lambda c, w: viirs.ingest(c, w, rules),
                                      "sentinel": lambda c, w: sentinel.ingest(c, w, rules)}, log=log)
    except Exception as e:
        log(f"surveillance des travailleurs : ÉCHEC, {e}")
        return None
    if not quiet or any(out.values()):
        log(f"travailleurs : {json.dumps(out, ensure_ascii=False)}")
    return out


def update_passes(conn) -> dict:
    from mars import satellites
    return satellites.update(conn)


def update_list(conn, source: str) -> dict:
    """Mise à jour d'une liste de surveillance (mars/watchlist.py), puis, si elle a changé, passage immédiat des
    règles des listes pour que les alertes WATCHLIST suivent sans attendre le prochain cycle."""
    from mars import watchlist
    if source == "gur":
        known = conn.execute("SELECT details->>'version' FROM task_runs WHERE task = 'listes_gur' AND status = 'ok' "
                             "AND details ? 'version' ORDER BY started_at DESC LIMIT 1").fetchone()
        out = watchlist.update_gur(conn, known[0] if known else None)
    else:
        out = watchlist.update_opensanctions(conn)
    if out.get("ajoutes") or out.get("retires") or out.get("modifies"):
        log(f"liste {source} : {out['ajoutes']} ajoutés, {out['retires']} retirés, {out['modifies']} modifiés")
        out["regles"] = rules_cycle(lists_only=True)
    return out


def injection(day: date, essais: int) -> dict:
    """Test par injection de la règle des coupures AIS (mars/rules.py, ais_gap_injection) : coupures de 3 h à 2, 6,
    10, 14 et 18 h UTC, sur une connexion asyncpg dédiée ; chaque essai est annulé, la base n'est pas modifiée."""
    import asyncio
    import asyncpg
    from mars.rules import ais_gap_injection

    async def go():
        c = await asyncpg.connect(database_url())
        try:
            if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM reception_cells)"):
                return {"refus": "zone de réception fiable absente : lancer scripts/build_masks.py"}
            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            return await ais_gap_injection(c, start, load_rules(), essais=essais, heures=[2, 6, 10, 14, 18])
        finally:
            await c.close()
    return asyncio.run(go())


def rules_cycle(lists_only: bool = False) -> dict:
    """Un cycle des règles sur le flux en direct (mars/rules.py), sur une connexion asyncpg dédiée. `lists_only` :
    seulement les règles qui dépendent des listes (navires des listes, gravité des changements d'identité)."""
    import asyncio
    import asyncpg
    from mars.frise import refresh_stats
    from mars.rules import run_continuous, run_identity, run_watchlist

    async def go():
        c = await asyncpg.connect(database_url())
        for t in ("json", "jsonb"):
            await c.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        try:
            now, rules = datetime.now(timezone.utc), load_rules()
            if lists_only:
                p = rules["continu"]
                return {"watchlist": await run_watchlist(c, now - timedelta(hours=p["fenetre_h"]), now, rules),
                        "identite": await run_identity(c, now - timedelta(hours=p["identite_fenetre_h"]), now, rules)}
            out = await run_continuous(c, now, rules)
            t = time.perf_counter()
            out["frise"] = await refresh_stats(c, now)      # statistiques de la frise (densité, coupures du flux)
            out.setdefault("durees_s", {})["frise"] = round(time.perf_counter() - t, 2)
            return out
        finally:
            await c.close()

    return asyncio.run(go())


def loop(conn):
    # Une tâche restée « en cours » a été interrompue (redémarrage du conteneur, ou base restaurée depuis une
    # sauvegarde prise pendant sa propre exécution) : elle est marquée en échec et sera retentée.
    n = conn.execute("UPDATE task_runs SET status = 'echec', finished_at = now(), "
                     "details = details || '{\"erreur\": \"interrompue\"}'::jsonb WHERE status = 'en_cours'").rowcount
    if n:
        log(f"{n} tâche(s) interrompue(s) marquée(s) en échec")
    log(f"Tâches planifiées : chaque nuit à {HOUR} UTC (archivage, purge au delà de {KEEP_DAYS} jours, sauvegarde, "
        f"{KEEP_BACKUPS} gardées) ; disque toutes les {DISK_EVERY_S // 60} min, alerte sous {DISK_PCT:.0f} %")
    step(lambda: supervision.role_grafana(conn), "rôle grafana_lecture")
    step(lambda: supervision.parametres(conn, load_rules()), "paramètres de supervision")
    secours = supervision.Secours(supervision.Ntfy.from_env())
    if secours.ntfy is None:
        log("NTFY_SUJET absent du .env : aucune alerte de secours ne partira sur le téléphone")
    last_disk = last_rules = last_sentinel = 0.0
    while True:
        if conn.closed:                             # base redémarrée : nouvelle connexion, sans arrêter les alertes
            conn = step(connect, "connexion à la base") or conn
        # Alertes de secours d'abord, sans écrire en base : elles doivent partir disque plein (incident du 18/10/2026)
        step(lambda: alert_if_grafana_down(conn, secours), "alertes de secours")
        step(lambda: signs_of_life(conn), "signes de vie")
        step(lambda: supervise_workers(conn), "surveillance des travailleurs")   # chaque minute : aucun orphelin
        if time.time() - last_disk >= DISK_EVERY_S:
            last_disk = time.time()
            step(lambda: archive.check_disk(conn, DISK_PATHS, DISK_PCT, log=log), "disque")
            step(lambda: supervision.mesurer(conn, LIVE), "mesures du disque")
            step(lambda: supervision.parametres(conn, load_rules()), "paramètres de supervision")
        if time.time() - last_rules >= 60 * load_rules()["continu"]["intervalle_min"]:
            last_rules = time.time()
            step(lambda: run(conn, "regles", rules_cycle, quiet=True), "règles")
        # Sentinel 1 : passages examinés toutes les 30 minutes (rien n'est lancé sans point d'intérêt ; un refus du
        # plafond ou du budget est consigné dans task_runs, sans trace d'erreur)
        if time.time() - last_sentinel >= 60 * load_rules()["sentinel"]["intervalle_min"]:
            last_sentinel = time.time()
            if os.environ.get("CDSE_TRAVAILLEUR_ACCESS_KEY") and os.environ.get("SCW_SECRET_KEY"):
                step(lambda: run(conn, "sentinel", lambda: launch_sentinel(conn, auto=True), quiet=True), "sentinel")
        for task, (fn, every, hour) in tasks(conn).items():
            if step(lambda: due(conn, task, datetime.now(timezone.utc), every, hour), f"{task} (échéance)"):
                step(lambda: run(conn, task, fn), task)
        time.sleep(60)


_step_errors: dict[str, str] = {}


def step(fn, name: str):
    """Une étape de la boucle : son erreur est journalisée (une fois tant qu'elle se répète) sans arrêter la boucle."""
    try:
        out = fn()
        if _step_errors.pop(name, None):
            log(f"{name} : rétabli")
        return out
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"
        if _step_errors.get(name) != msg:
            _step_errors[name] = msg
            log(f"{name} : ÉCHEC, {msg}")
        return None


def signs_of_life(conn):
    now = datetime.now(timezone.utc)
    t = supervision.signe_collecte(LIVE)
    if t is not None:
        supervision.signe(conn, "collecte", t, {"age_s": round((now - t).total_seconds())})
    supervision.signe(conn, "taches", now)


def alert_if_grafana_down(conn, secours) -> list[str]:
    """Si Grafana ne répond pas, taches envoie lui même les trois alertes les plus graves ; sinon, il n'annonce que le
    retour à la normale de celles qu'il avait envoyées (Grafana prend le relais pour les nouvelles)."""
    etat = supervision.conditions(conn, LIVE)
    if supervision.grafana_repond():
        etat = {k: v for k, v in etat.items() if k in secours.actives}
    return secours.evaluer(etat, log=log)


def main():
    ap = argparse.ArgumentParser(description="Tâches planifiées de MARS C2")
    ap.add_argument("commande", nargs="?", default="boucle",
                    choices=["boucle", "archiver", "purger", "sauvegarder", "disque", "regles", "listes", "passages", "viirs",
                             "travailleurs", "journal-travailleur", "detruire-travailleurs", "viirs-reevaluer", "mesures",
                             "coupures-injection", "sentinel", "sentinel-analyser",
                             "sentinel-diagnostic", "tester-alerte", "mesurer-tableaux",
                             "sauvegardes", "telecharger", "restaurer-positions"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--du", type=date.fromisoformat)
    ap.add_argument("--au", type=date.fromisoformat)
    ap.add_argument("--jours", type=int, default=14, help="mesures : nombre de jours examinés")
    ap.add_argument("--jour", type=date.fromisoformat, help="coupures-injection : journée (par défaut, la veille)")
    ap.add_argument("--essais", type=int, default=20, help="coupures-injection : navires par heure de coupure")
    a = ap.parse_args()
    load_env()
    if a.commande == "tester-alerte":                   # avant la base : l'envoi doit marcher même base arrêtée
        n = supervision.Ntfy.from_env()
        if n is None:
            print("NTFY_SUJET absent du .env : rien n'est envoyé")
            sys.exit(1)
        try:
            code = n.envoyer("MARS C2 : essai d'alerte", f"Message d'essai envoyé le "
                             f"{datetime.now(timezone.utc):%d/%m/%Y à %H:%M} UTC. La réception fonctionne.", "default",
                             "white_check_mark")
        except RuntimeError as e:
            print(f"Envoi en échec : {e}")
            sys.exit(1)
        print(f"Message d'essai envoyé (HTTP {code}) : il doit apparaître sur le téléphone dans les secondes qui suivent")
        return
    conn = connect()
    named = {"archiver": "archivage", "purger": "purge", "sauvegarder": "sauvegarde"}
    if a.commande == "boucle":
        loop(conn)
    elif a.commande in named:
        if run(conn, named[a.commande], tasks(conn)[named[a.commande]][0]) is None:
            sys.exit(1)
    elif a.commande == "regles":
        if run(conn, "regles", rules_cycle) is None:
            sys.exit(1)
    elif a.commande == "listes":                  # les deux listes, sans attendre la nuit
        ok = [run(conn, f"listes_{s}", lambda s=s: update_list(conn, s)) is not None for s in ("opensanctions", "gur")]
        if not all(ok):
            sys.exit(1)
    elif a.commande == "viirs":
        if run(conn, "viirs", lambda: launch_viirs(conn)) is None:
            sys.exit(1)
    elif a.commande == "travailleurs":
        supervise_workers(conn, quiet=False)
        for r in conn.execute("SELECT id, tache, etat, commercial_type, cree_le, detruit_le, erreur FROM travailleurs "
                              "ORDER BY id DESC LIMIT 10").fetchall():
            print(*r)
    elif a.commande == "journal-travailleur":           # journal envoyé par un travailleur (le dernier par défaut)
        row = conn.execute("SELECT id, etat, erreur, journal_le, journal, diagnostic FROM travailleurs "
                           "WHERE (%s::bigint IS NULL OR id = %s) ORDER BY id DESC LIMIT 1",
                           (a.args[0] if a.args else None,) * 2).fetchone()
        if row is None:
            raise SystemExit("aucun travailleur")
        print(f"travailleur {row[0]}, état {row[1]}, journal reçu le {row[3] or 'jamais'}\n{row[2] or ''}")
        if row[5]:
            print("état Scaleway relevé avant la destruction :\n" + json.dumps(row[5], ensure_ascii=False, indent=2))
        print(row[4] or "(aucun journal : le travailleur n'a jamais joint le serveur ; lire la console série Scaleway)")
    elif a.commande == "detruire-travailleurs":
        from mars import travailleurs
        scw = travailleurs.Scaleway.from_env()
        if scw is None:
            raise SystemExit("clés Scaleway absentes")
        p = load_rules()["travailleurs"]
        zones = sorted({p["zone"], *(t.get("zone", p["zone"]) for t in p["types"].values())})   # GPU : fr-par-2
        block = {r[0]: r[1] or [] for r in conn.execute(
            "SELECT scw_server_id, volumes_bloc FROM travailleurs WHERE scw_server_id IS NOT NULL").fetchall()}
        for zone in zones:
            for s in scw.tagged(zone):                                     # travailleurs seulement (garde complète)
                try:
                    gone = scw.destroy(zone, s["id"], block.get(s["id"], []))
                    print(s["id"], s["name"], zone, "détruite" if gone else "destruction en cours (relancer dans une minute)")
                except travailleurs.RefusDestruction as e:
                    print(s["id"], s.get("name"), f"REFUSÉE : {e}")
        conn.execute("UPDATE travailleurs SET etat = 'echec', fini_le = coalesce(fini_le, now()), "
                     "erreur = coalesce(erreur, 'arrêt d''urgence') WHERE detruit_le IS NULL AND etat NOT IN ('termine', 'echec')")
    elif a.commande == "viirs-reevaluer":                # règles VIIRS en vigueur appliquées aux détections en base
        from mars import viirs
        print(json.dumps(viirs.reevaluate(conn, load_rules()), ensure_ascii=False, indent=2, default=str))
    elif a.commande == "sentinel-diagnostic":            # lecture seule : Copernicus, puis Scaleway
        import requests
        from mars import diagnostic, travailleurs
        # Volumes bloc enregistrés des travailleurs Sentinel récents : chacun doit avoir été supprimé
        recorded = conn.execute("SELECT id, volumes_bloc FROM travailleurs WHERE tache = 'sentinel' AND "
                                "cardinality(volumes_bloc) > 0 AND cree_le > now() - interval '30 days' "
                                "ORDER BY id").fetchall()
        lines = diagnostic.run(load_rules(), requests.Session(), travailleurs.Scaleway.from_env(), recorded=recorded)
        print(diagnostic.report(lines))
        sys.exit(1 if any(s == diagnostic.MANQUE for s, _r, _d in lines) else 0)
    elif a.commande in ("sentinel", "sentinel-analyser"):
        pid = int(a.args[0]) if a.commande == "sentinel-analyser" and a.args else None
        if a.commande == "sentinel-analyser" and pid is None:
            raise SystemExit("Préciser l'identifiant du passage : taches.py sentinel-analyser ID (table sar_passes)")
        if run(conn, "sentinel", lambda: launch_sentinel(conn, pid)) is None:
            sys.exit(1)
    elif a.commande == "coupures-injection":             # coupures simulées dans des transactions annulées
        print(json.dumps(injection(a.jour or (datetime.now(timezone.utc) - timedelta(days=1)).date(), a.essais),
                         ensure_ascii=False, indent=2, default=str))
    elif a.commande == "passages":
        if run(conn, "passages", lambda: update_passes(conn)) is None:
            sys.exit(1)
    elif a.commande == "mesures":
        from mesures_calibration import main as mesures
        mesures(a.jours)
    elif a.commande == "mesurer-tableaux":               # lecture seule, sous le rôle de Grafana, transactions annulées
        import importlib.util
        spec = importlib.util.spec_from_file_location("generer", ROOT / "grafana" / "generer.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        rows = supervision.mesurer_requetes(conn, gen.requetes())
        for r in sorted(rows, key=lambda r: -r["duree_s"]):
            print(f"{r['duree_s']:7.3f} s  {r['jours']:>2} j  {r['tableau']:<10} {r['panneau']:<48} "
                  f"{r.get('erreur') or str(r['lignes']) + ' lignes'}")
        lentes = [r for r in rows if r["duree_s"] > 1 or r.get("erreur")]
        print(f"\n{len(rows)} mesures, la plus longue {max(r['duree_s'] for r in rows):.3f} s ; "
              f"{len(lentes)} au dessus de 1 s ou en erreur")
        sys.exit(1 if lentes else 0)
    elif a.commande == "disque":
        print(archive.check_disk(conn, DISK_PATHS, DISK_PCT, log=log))
    elif a.commande == "sauvegardes":
        for o in sorted(r2().list(archive.BACKUP_PREFIX), key=lambda o: o["key"]):
            print(f"{o['key']}  {o['bytes'] / 1e6:.1f} Mo")
    elif a.commande == "telecharger":
        key, dest = a.args
        r2().download(key, Path(dest))
        print(f"{key} enregistré dans {dest}")
    elif a.commande == "restaurer-positions":
        from mars.ais.ingest import Ingestor
        if not a.du or not a.au:
            raise SystemExit("Préciser --du et --au (AAAA-MM-JJ)")
        client, ing = r2(), Ingestor(conn, LIVE, load_rules()["ingestion"])   # allègement neuf, sans reprise
        workdir = LIVE / ".restauration"
        workdir.mkdir(parents=True, exist_ok=True)
        d = a.du
        while d <= a.au:                          # dans l'ordre chronologique : continuité de l'allègement
            log(json.dumps(archive.restore_positions(conn, client, ing, d, workdir), ensure_ascii=False))
            d += timedelta(days=1)
        ing.refresh_watch(force=True)


if __name__ == "__main__":
    main()
