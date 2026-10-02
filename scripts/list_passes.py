"""Liste les passages Sentinel 1 couvrant une zone sur une période, avec le recouvrement et la disponibilité de l'AIS.

Exemple :
    python scripts/list_passes.py --bbox 11.05 56.52 11.35 56.70 --start 2024-06-01 --end 2024-06-30
"""
import argparse

import requests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--api", default="http://localhost:8000/api")
    args = ap.parse_args()
    r = requests.get(f"{args.api}/passes", params={"bbox": ",".join(map(str, args.bbox)),
                     "start": f"{args.start}T00:00:00Z", "end": f"{args.end}T23:59:59Z"}, timeout=120)
    r.raise_for_status()
    for p in r.json():
        print(f"{p['acquired_at'][:19]}  {str(p['orbit_direction']):10s}  recouvrement {p['coverage']}  "
              f"AIS {'chargé' if p['ais_available'] else 'absent'}  {p['product_name']}")


if __name__ == "__main__":
    main()
