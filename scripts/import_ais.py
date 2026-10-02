"""Importe une journée AIS de la Danish Maritime Authority dans la base, pour une zone donnée.

Exemple (Skagerrak et Kattegat) :
    python scripts/import_ais.py --csv data/ais/aisdk-2024-06-05.csv --bbox 8.5 56.0 13.0 58.6
"""
import argparse
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from mars.ais.dma import read_dma_csv
from mars.config import load_env
from mars.db import connect

BATCH = 500_000


def main():
    ap = argparse.ArgumentParser(description="Import d'une journée AIS (Danish Maritime Authority)")
    ap.add_argument("--csv", required=True, help="Fichier CSV journalier décompressé")
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    ap.add_argument("--margin", type=float, default=0.2, help="Marge autour de la zone, en degrés")
    args = ap.parse_args()
    load_env()

    t = time.time()
    df, stats = read_dma_csv(args.csv, args.bbox, margin=args.margin)
    print(f"Lecture et nettoyage en {time.time() - t:.0f} s")
    for k, v in stats.items():
        print(f"  {k:34s} {v:>12,}".replace(",", " "))
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
                            (int(s.mmsi), s.name, s.ship_type, None if pd.isna(s.length) else float(s.length),
                             s.first_seen, s.last_seen))
                ids[int(s.mmsi)] = cur.fetchone()[0]

        # Positions déjà présentes pour ces navires sur la même période : remplacées
        cur.execute("DELETE FROM positions WHERE vessel_id = ANY(%s) AND ts BETWEEN %s AND %s",
                    (list(ids.values()), df.ts.min(), df.ts.max()))

        # Insertion en masse par COPY, en lots, au format texte de PostgreSQL
        t = time.time()
        df = df.sort_values("ts")  # ordre chronologique : meilleure localité pour les lectures par fenêtre de temps
        out = pd.DataFrame({
            "vessel_id": df.mmsi.astype(int).map(ids),
            "ts": df.ts.dt.strftime("%Y-%m-%d %H:%M:%S+00"),
            "geom": "SRID=4326;POINT(" + df.lon.astype(str) + " " + df.lat.astype(str) + ")",
            "sog": df.sog, "cog": df.cog, "heading": df.heading.astype("Int64"),
        })
        with cur.copy("COPY positions (vessel_id, ts, geom, sog_kn, cog_deg, heading_deg) FROM STDIN") as cp:
            for start in range(0, len(out), BATCH):
                buf = io.StringIO()
                out.iloc[start:start + BATCH].to_csv(buf, sep="\t", header=False, index=False, na_rep="\\N")
                cp.write(buf.getvalue())
                print(f"\r  {min(start + BATCH, len(out)):,} positions insérées".replace(",", " "), end="")
        elapsed = time.time() - t
        print(f"\n{len(out):,} positions en {elapsed:.0f} s, soit {len(out) / max(elapsed, 1e-6):,.0f} par seconde"
              .replace(",", " "))

        # Journées disponibles
        for day, g in df.groupby(df.ts.dt.date):
            cur.execute("INSERT INTO ais_days (day, messages, vessels) VALUES (%s, %s, %s) "
                        "ON CONFLICT (day) DO UPDATE SET messages = EXCLUDED.messages, vessels = EXCLUDED.vessels, "
                        "imported_at = now()", (day, len(g), int(g.mmsi.nunique())))

        # Si l'horloge simulée pointe hors des journées chargées, on la place sur la première journée importée
        cur.execute("SELECT EXISTS (SELECT 1 FROM ais_days WHERE day = (sim_now() AT TIME ZONE 'UTC')::date)")
        if not cur.fetchone()[0]:
            start = pd.Timestamp(df.ts.min().date(), tz="UTC") + pd.Timedelta(hours=12)
            cur.execute("UPDATE sim_clock SET sim_anchor = %s, real_anchor = clock_timestamp(), paused = true", (start,))
            print(f"Horloge simulée placée au {start}")
        conn.commit()


if __name__ == "__main__":
    main()
