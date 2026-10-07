"""Charge les listes de surveillance en base (table watchlist) depuis des fichiers locaux et rafraîchit le
rapprochement avec les navires (vue vessel_watch). Sur le serveur, la mise à jour est automatique (conteneur
taches : OpenSanctions chaque jour, GUR chaque semaine) ; ce script sert au premier chargement et au Mac.

Usage : python scripts/import_watchlist.py [--gur chemin] [--opensanctions chemin]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mars.config import load_env
from mars.db import connect
from mars.watchlist import GUR, OS_CSV, gur_rows, load_gur, load_os, os_rows, replace_source

LABEL = {"fort": "fort", "sanctionne": "sanctionné", "flotte_fantome": "flotte fantôme",
         "suspect_gur": "suspect GUR", "autre_risque": "autre risque"}


def main():
    ap = argparse.ArgumentParser(description="Import des listes de surveillance")
    ap.add_argument("--gur", default=str(GUR))
    ap.add_argument("--opensanctions", default=str(OS_CSV))
    args = ap.parse_args()
    load_env()

    gur, os_ = load_gur(Path(args.gur)), load_os(Path(args.opensanctions))
    if gur.empty and os_.empty:
        raise SystemExit(f"Aucune liste trouvée ({args.gur}, {args.opensanctions})")
    with connect() as conn, conn.cursor() as cur:
        # Remplacement source par source : une liste absente ne vide pas l'autre
        if not gur.empty:
            replace_source(cur, "gur", gur_rows(gur))
        if not os_.empty:
            replace_source(cur, "opensanctions", os_rows(os_))
        cur.execute("REFRESH MATERIALIZED VIEW vessel_watch")
        stats = cur.execute(
            """SELECT source, count(*), count(imo), count(mmsi), count(*) FILTER (WHERE sanctioned),
                      count(*) FILTER (WHERE shadow)
               FROM watchlist GROUP BY source ORDER BY source""").fetchall()
        seen = cur.execute("SELECT level, count(*) FROM vessel_watch GROUP BY level, rank ORDER BY rank").fetchall()
        conn.commit()

    for source, n, n_imo, n_mmsi, n_sanc, n_shadow in stats:
        print(f"{source:14s} {n:6d} navires, {n_imo} avec OMI valide, {n_mmsi} avec MMSI, "
              f"{n_sanc} sanctionnés, {n_shadow} flotte fantôme")
    total = sum(n for _, n in seen)
    print(f"\nNavires de la base rapprochés d'une liste : {total}")
    for level, n in seen:
        print(f"  {LABEL[level]:15s} {n}")


if __name__ == "__main__":
    main()
