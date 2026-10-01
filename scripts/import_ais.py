"""Importe une journée AIS de la Danish Maritime Authority dans la base, pour une zone donnée.

Exemple :
    python scripts/import_ais.py --csv data/ais/aisdk-2024-06-05.csv --bbox 10.2 57.7 10.9 58.15
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from mars.ais.dma import read_dma_csv
from mars.config import load_env
from mars.db import connect


def main():
    ap = argparse.ArgumentParser(description="Import d'une journée AIS (Danish Maritime Authority)")
    ap.add_argument("--csv", required=True, help="Fichier CSV journalier décompressé")
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    ap.add_argument("--margin", type=float, default=0.2, help="Marge autour de la zone, en degrés")
    args = ap.parse_args()
    load_env()

    t = time.time()
    df = read_dma_csv(args.csv, args.bbox, margin=args.margin)
    print(f"{len(df)} messages retenus, {df.mmsi.nunique()} navires, lecture en {time.time() - t:.0f} s")
    if df.empty:
        return

    statics = df.groupby("mmsi").agg(
        name=("name", lambda s: s.dropna().iloc[0] if s.notna().any() else None),
        ship_type=("ship_type", lambda s: s.dropna().iloc[0] if s.notna().any() else None),
        length=("length", lambda s: float(s.dropna().iloc[0]) if s.notna().any() else None),
        first_seen=("ts", "min"),
        last_seen=("ts", "max"),
    ).reset_index()

    with connect() as conn, conn.cursor() as cur:
        # Référentiel : un navire par MMSI observé, réutilisé s'il existe déjà
        cur.execute("SELECT mmsi, id FROM vessels WHERE mmsi = ANY(%s)", (statics.mmsi.astype(int).tolist(),))
        ids = dict(cur.fetchall())
        for s in statics.itertuples():
            if int(s.mmsi) in ids:
                cur.execute("UPDATE vessels SET first_seen = LEAST(first_seen, %s), last_seen = GREATEST(last_seen, %s) "
                            "WHERE id = %s", (s.first_seen, s.last_seen, ids[int(s.mmsi)]))
            else:
                cur.execute("INSERT INTO vessels (mmsi, name, ship_type, length_m, first_seen, last_seen) "
                            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                            (int(s.mmsi), s.name, s.ship_type, s.length, s.first_seen, s.last_seen))
                ids[int(s.mmsi)] = cur.fetchone()[0]

        # Positions déjà présentes pour ces navires sur la même période : remplacées
        cur.execute("DELETE FROM positions WHERE vessel_id = ANY(%s) AND ts BETWEEN %s AND %s",
                    (list(ids.values()), df.ts.min(), df.ts.max()))

        t = time.time()
        with cur.copy("COPY positions (vessel_id, ts, geom, sog_kn, cog_deg, heading_deg) FROM STDIN") as cp:
            for r in df.itertuples():
                cp.write_row((
                    ids[int(r.mmsi)], r.ts, f"SRID=4326;POINT({r.lon} {r.lat})",
                    None if pd.isna(r.sog) else float(r.sog),
                    None if pd.isna(r.cog) else float(r.cog),
                    None if pd.isna(r.heading) else int(r.heading),
                ))
        conn.commit()
    print(f"{len(df)} positions insérées en {time.time() - t:.0f} s")


if __name__ == "__main__":
    main()
