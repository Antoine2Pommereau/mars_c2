"""Service d'ingestion : charge en continu la collecte AISStream (Parquet) dans la base.

Au démarrage, tous les fichiers pas encore chargés le sont (rattrapage) ; ensuite, toutes les 15 secondes, les
dossiers du jour et de la veille sont relus. Les paramètres d'allègement sont dans config/rules.yaml (ingestion).

Usage :
    python scripts/ais_ingest.py            # en continu (service Docker « ingest » sur le serveur)
    python scripts/ais_ingest.py --once     # un seul passage, puis arrêt
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from mars import traces
from mars.ais.ingest import Ingestor
from mars.config import ROOT, database_url, load_env, load_rules
from mars.supervision import SIGNE_INGESTION, signe, toucher

BOUCLE = "/tmp/boucle_ingestion"          # chaque tour de boucle (contrôle de santé, scripts/sante.py)
JOURNAL_ERREUR_S = 3600                   # une même erreur consignée dans task_runs au plus une fois par heure


def connect(retries: int = 30) -> psycopg.Connection:
    for k in range(retries):
        try:
            return psycopg.connect(database_url(), autocommit=True)
        except psycopg.OperationalError as e:
            if k == retries - 1:
                raise
            print(f"Base injoignable ({e.__class__.__name__}), nouvel essai dans 2 s", flush=True)
            time.sleep(2)


def show(totals: dict, elapsed: float) -> str:
    keys = [("conserves", "positions conservées"), ("allege", "allégées"), ("en_retard", "en retard"),
            ("doublons", "doublons"), ("statiques", "messages statiques"),
            ("changements_identite", "changements d'identité")]
    parts = [f"{totals[k]} {label}" for k, label in keys if totals.get(k)]
    return (f"{datetime.now():%H:%M:%S}  {totals['fichiers']} fichier(s) en {elapsed:.1f} s : "
            f"{totals.get('lus', 0)} positions lues, " + ", ".join(parts))


def main():
    ap = argparse.ArgumentParser(description="Ingestion continue de l'AIS en direct")
    ap.add_argument("--root", default=str(ROOT / "data" / "ais_live"), help="Dossier de la collecte")
    ap.add_argument("--interval", type=float, default=float(os.environ.get("AIS_INGEST_INTERVAL_S", "15")))
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    load_env()
    traces.init("mars-ingestion")

    ing, recent_only = None, False           # premier passage : tous les dossiers (rattrapage)
    failures, last_logged = 0, {}
    while True:
        toucher(BOUCLE)
        t, pause = time.time(), args.interval
        # Un span par cycle (racine de sa trace), un span par fichier dans Ingestor.run_once
        with traces.span("ingestion.cycle", nouvelle_trace=True) as sp:
            try:
                if ing is None:
                    ing = Ingestor(connect(), Path(args.root), load_rules()["ingestion"])
                    print(f"Ingestion depuis {args.root} ; allègement repris pour {ing.seed()} navire(s) vus depuis deux "
                          "heures", flush=True)
                totals = ing.run_once(recent_only=recent_only)
                sp.set_attributes({f"ingestion.{k}": v for k, v in totals.items() if isinstance(v, (int, float))})
                sp.set_attribute("ingestion.retard_s", round(ing.retard_s))
                if totals["fichiers"]:
                    print(show(totals, time.time() - t), flush=True)
                if args.once:
                    ing.refresh_watch(force=True)
                    break
                recent_only = True
                toucher(SIGNE_INGESTION)
                signe(ing.conn, "ingestion", datetime.now(timezone.utc),
                      {"fichiers": totals["fichiers"], "retard_s": round(ing.retard_s), "duree_s": round(time.time() - t, 1)},
                      memoire=True)
                if failures:
                    print(f"Ingestion rétablie après {failures} passage(s) en échec", flush=True)
                failures = 0
            except Exception as e:
                # Incident du 18/10/2026 : DiskFull au rafraîchissement de vessel_watch, l'ingestion plantait et
                # redémarrait en boucle pendant 21 h. Désormais elle reste en vie, dit clairement pourquoi elle échoue,
                # attend de plus en plus longtemps (jusqu'à 5 min) et reprend seule ; l'alerte « ingestion en échec »
                # (Grafana, ou taches en secours) prévient
                failures += 1
                motif = describe(e)
                sp.record_exception(e)
                traces.erreur(motif)
                print(f"{datetime.now(timezone.utc):%d/%m/%Y %H:%M:%S}  ÉCHEC DE L'INGESTION ({failures}) : {motif}",
                      flush=True)
                if failures == 1 and not is_disk_full(e):
                    traceback.print_exc()
                if time.time() - last_logged.get(motif[:60], 0) >= JOURNAL_ERREUR_S:
                    last_logged[motif[:60]] = time.time()
                    record_failure(motif, failures)
                if ing is not None and ing.conn.closed:
                    ing = None                    # connexion perdue (base redémarrée) : reprise depuis le début
                if args.once:
                    raise
                pause = min(args.interval * 2 ** min(failures, 5), 300)   # attente hors du span du cycle
        time.sleep(pause)


def is_disk_full(e: Exception) -> bool:
    return isinstance(e, psycopg.errors.DiskFull) or "No space left on device" in str(e)


def describe(e: Exception) -> str:
    if is_disk_full(e):
        return f"DISQUE PLEIN, la base refuse d'écrire ({str(e).splitlines()[0][:200]})"
    return f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"


def record_failure(motif: str, failures: int):
    """Échec consigné dans task_runs (tâche ingestion), sur une connexion à part ; disque plein, la base refuse aussi
    cette écriture : le journal du conteneur et les alertes restent."""
    try:
        with psycopg.connect(database_url(), autocommit=True, connect_timeout=5) as c:
            c.execute("INSERT INTO task_runs (task, run_day, status, finished_at, details) VALUES ('ingestion', %s, "
                      "'echec', now(), %s)", (date.today(), json.dumps({"erreur": motif, "passages_en_echec": failures})))
    except Exception:
        pass


if __name__ == "__main__":
    main()
