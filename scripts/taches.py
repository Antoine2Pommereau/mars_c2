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
Toutes les 10 minutes : espace disque, alerte sous ALERTE_DISQUE_PCT (15 %).
Toutes les 5 minutes (section continu de config/rules.yaml) : règles comportementales sur le flux en direct
(rendez vous, coupures AIS, navires des listes, changements d'identité), consignées dans task_runs (tâche regles),
puis statistiques du trafic pour la frise de l'interface (mars/frise.py).
Une tâche réussie ne rejoue pas le même jour ; une tâche en échec est retentée une heure plus tard.

Usage :
    python scripts/taches.py                          # boucle (service Docker « taches »)
    python scripts/taches.py archiver | purger | sauvegarder | disque | regles | listes
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

from mars import archive
from mars.config import ROOT, database_url, load_env, load_rules

LIVE = Path(os.environ.get("AIS_LIVE_DIR", ROOT / "data" / "ais_live"))
HOUR = os.environ.get("TACHES_HEURE", "02:30")
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
        conn.execute("UPDATE task_runs SET status = 'echec', finished_at = now(), details = %s WHERE id = %s",
                     (json.dumps({"erreur": str(e)[:1000]}), rid))
        log(f"{task} : ÉCHEC, {e}")
        traceback.print_exc()
        return None


def _changed(details) -> bool:
    """Un cycle des règles n'est journalisé que s'il crée ou retire des alertes (journaux sobres)."""
    if isinstance(details, dict):
        return bool(details.get("nouvelles") or details.get("retirees")) or any(_changed(v) for v in details.values())
    return False


def due(conn, task: str, now: datetime, every_days: int = 1) -> bool:
    """Tâche nocturne à lancer : heure passée, pas de réussite depuis `every_days` jours (aujourd'hui compris), pas
    d'échec depuis moins d'une heure."""
    h, m = (int(x) for x in HOUR.split(":"))
    if now < now.replace(hour=h, minute=m, second=0, microsecond=0):
        return False
    r = conn.execute("SELECT bool_or(status = 'ok') FILTER (WHERE run_day > %s), "
                     "max(started_at) FILTER (WHERE status = 'echec') FROM task_runs WHERE task = %s",
                     (now.date() - timedelta(days=every_days), task)).fetchone()
    return not r[0] and (r[1] is None or (now - r[1]).total_seconds() >= RETRY_S)


def tasks(conn):
    """Tâches de la nuit, dans l'ordre, avec leur période en jours."""
    return {
        "archivage": (lambda: archive.run_archive(conn, r2(), LIVE, log=log), 1),
        "purge": (lambda: archive.run_purge(conn, LIVE, KEEP_DAYS), 1),
        "sauvegarde": (lambda: archive.run_backup(r2(), database_url(), KEEP_BACKUPS), 1),
        "listes_opensanctions": (lambda: update_list(conn, "opensanctions"), 1),
        "listes_gur": (lambda: update_list(conn, "gur"), 7),
    }


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
            out["frise"] = await refresh_stats(c, now)      # statistiques de la frise (densité, coupures du flux)
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
    last_disk = last_rules = 0.0
    while True:
        if time.time() - last_disk >= DISK_EVERY_S:
            archive.check_disk(conn, DISK_PATHS, DISK_PCT, log=log)
            last_disk = time.time()
        if time.time() - last_rules >= 60 * load_rules()["continu"]["intervalle_min"]:
            last_rules = time.time()
            run(conn, "regles", rules_cycle, quiet=True)
        for task, (fn, every) in tasks(conn).items():
            if due(conn, task, datetime.now(timezone.utc), every):
                run(conn, task, fn)
        time.sleep(60)


def main():
    ap = argparse.ArgumentParser(description="Tâches planifiées de MARS C2")
    ap.add_argument("commande", nargs="?", default="boucle",
                    choices=["boucle", "archiver", "purger", "sauvegarder", "disque", "regles", "listes", "mesures",
                             "sauvegardes", "telecharger", "restaurer-positions"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--du", type=date.fromisoformat)
    ap.add_argument("--au", type=date.fromisoformat)
    ap.add_argument("--jours", type=int, default=14, help="mesures : nombre de jours examinés")
    a = ap.parse_args()
    load_env()
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
    elif a.commande == "mesures":
        from mesures_calibration import main as mesures
        mesures(a.jours)
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
