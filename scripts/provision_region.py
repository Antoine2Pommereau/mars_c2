"""Provisionne la donnée statique d'une région (infrastructures EMODnet).

La logique est dans mars/regions/provision.py, partagée avec le backend. Ce script en est la
ligne de commande.

Exemples :
    python scripts/provision_region.py --region Bretagne
    python scripts/provision_region.py --region Mediterranee
    python scripts/provision_region.py --region Bretagne --only infrastructure
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mars.config import load_env
from mars.db import connect
from mars.regions.provision import provision


def find_region(cur, name):
    if name:
        cur.execute("SELECT id, name FROM regions WHERE name = %s OR id::text = %s", (name, name))
    else:
        cur.execute("SELECT id, name FROM regions WHERE active ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if not row:
        sys.exit("Aucune région trouvée")
    return row


def region_bbox(cur, region_id):
    cur.execute("SELECT ST_XMin(b), ST_YMin(b), ST_XMax(b), ST_YMax(b) FROM "
                "(SELECT ST_Envelope(geom::geometry) AS b FROM regions WHERE id = %s) q", (region_id,))
    return cur.fetchone()


def main():
    ap = argparse.ArgumentParser(description="Provisionnement de la donnée statique d'une région")
    ap.add_argument("--region", help="Nom ou identifiant de région ; par défaut la région active")
    ap.add_argument("--only", choices=["infrastructure"], default="infrastructure", help="Un seul fournisseur")
    args = ap.parse_args()
    load_env()

    with connect() as conn, conn.cursor() as cur:
        region_id, name = find_region(cur, args.region)
        bbox = region_bbox(cur, region_id)
        print(f"Région {region_id} : {name}")
        print(f"  emprise {bbox[0]:.2f} {bbox[1]:.2f} {bbox[2]:.2f} {bbox[3]:.2f}")
        t = time.time()
        provision(cur, region_id, bbox, which=args.only)
        conn.commit()
        print(f"Provisionnement terminé en {time.time() - t:.0f} s.")


if __name__ == "__main__":
    main()
