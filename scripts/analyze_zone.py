"""Demande une analyse radar à l'API, pour le passage Sentinel 1 le plus proche d'un instant donné,
puis suit sa progression jusqu'au résultat.

Exemple :
    python scripts/analyze_zone.py --bbox 10.30 57.80 10.80 58.07 --time 2024-06-05T17:02:26Z
"""
import argparse
import sys
import time

import pandas as pd
import requests


def main():
    ap = argparse.ArgumentParser(description="Analyse radar d'une zone, via l'API de MARS C2")
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    ap.add_argument("--time", required=True, help="Instant proche du passage voulu, en UTC (ISO 8601)")
    ap.add_argument("--search-hours", type=float, default=3.0)
    ap.add_argument("--mode", default="fast", choices=["fast", "full"])
    ap.add_argument("--api", default="http://localhost:8080/api")
    args = ap.parse_args()

    t = pd.Timestamp(args.time)
    t = t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")
    w = pd.Timedelta(hours=args.search_hours)
    bbox = ",".join(str(v) for v in args.bbox)
    r = requests.get(f"{args.api}/passes", params={"bbox": bbox, "start": (t - w).isoformat(), "end": (t + w).isoformat()},
                     timeout=120)
    r.raise_for_status()
    passes = [p for p in r.json() if p["ais_available"]]
    if not passes:
        sys.exit("Aucun passage exploitable (avec AIS chargé) autour de cet instant.")
    p = min(passes, key=lambda x: abs(pd.Timestamp(x["acquired_at"]) - t))
    print(f"Passage : {p['product_name']} ({p['acquired_at']}, recouvrement {p['coverage']})")

    r = requests.post(f"{args.api}/analyses", json={"bbox": args.bbox, "product_name": p["product_name"],
                                                    "mode": args.mode}, timeout=30)
    if r.status_code >= 400:
        sys.exit(f"Refusé : {r.status_code} {r.text}")
    analysis_id = r.json()["id"]
    print(f"Analyse n° {analysis_id} lancée")

    seen = 0
    while True:
        a = requests.get(f"{args.api}/analyses/{analysis_id}", timeout=30).json()
        for step in a["progress"][seen:]:
            if step["state"] == "done":
                print(f"  {step['step']:12s} {step['seconds']:6.1f} s  {step.get('detail', '')}")
        seen = len(a["progress"])
        if a["status"] in ("done", "failed"):
            break
        time.sleep(0.5)

    if a["status"] == "failed":
        sys.exit(f"Échec : {a['error']}")
    print("Résultat :", a["summary"])
    print("Durées   :", a["timings"])


if __name__ == "__main__":
    main()
