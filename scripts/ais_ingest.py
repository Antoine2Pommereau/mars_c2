"""Service d'ingestion : charge en continu la collecte AISStream (Parquet) dans la base.

Au démarrage, tous les fichiers pas encore chargés le sont (rattrapage) ; ensuite, toutes les 15 secondes, les
dossiers du jour et de la veille sont relus. Les paramètres d'allègement sont dans config/rules.yaml (ingestion).

Usage :
    python scripts/ais_ingest.py            # en continu (service Docker « ingest » sur le serveur)
    python scripts/ais_ingest.py --once     # un seul passage, puis arrêt
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from mars.ais.ingest import Ingestor
from mars.config import ROOT, database_url, load_env, load_rules


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
    ap.add_argument("--interval", type=float, default=float(os.environ.get("AIS_INGEST_INTERVAL_S", 15)))
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    load_env()

    ing = Ingestor(connect(), Path(args.root), load_rules()["ingestion"])
    print(f"Ingestion depuis {args.root} ; allègement repris pour {ing.seed()} navire(s) vus depuis deux heures",
          flush=True)
    recent_only = False                      # premier passage : tous les dossiers (rattrapage)
    while True:
        t = time.time()
        totals = ing.run_once(recent_only=recent_only)
        if totals["fichiers"]:
            print(show(totals, time.time() - t), flush=True)
        if args.once:
            ing.refresh_watch(force=True)
            break
        recent_only = True
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
