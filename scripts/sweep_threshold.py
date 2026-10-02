"""Balayage du seuil de présence du détecteur sur une ou plusieurs zones.

Pour chaque seuil, relance les analyses via l'API et compare : détections, retenues après filtres, appariées à l'AIS,
navires sombres, positions AIS non confirmées. Le fichier de règles est restauré à la fin, et les analyses créées
pour le balayage sont supprimées (option --keep pour les conserver).

Exemple :
    python scripts/sweep_threshold.py --thresholds 0.30 0.25 0.20 0.15 0.10
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import requests

from mars.config import ROOT, load_env
from mars.db import connect

ZONES = {
    "large_skagen": [10.30, 57.80, 10.80, 58.07],
    "mouillage": [10.45, 57.58, 10.85, 57.80],
}
RULES = ROOT / "config" / "rules.yaml"
PATTERN = re.compile(r"(thresholds:\s*\n\s*objectness:\s*)([0-9.]+)")


def run(api, bbox, product, timeout=600):
    r = requests.post(f"{api}/analyses", json={"bbox": bbox, "product_name": product, "mode": "fast"}, timeout=30)
    r.raise_for_status()
    aid = r.json()["id"]
    t = time.time()
    while time.time() - t < timeout:
        a = requests.get(f"{api}/analyses/{aid}", timeout=30).json()
        if a["status"] in ("done", "failed"):
            return aid, a
        time.sleep(0.5)
    raise TimeoutError(f"analyse {aid}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresholds", nargs="+", type=float, default=[0.30, 0.25, 0.20, 0.15, 0.10])
    ap.add_argument("--time", default="2024-06-05T17:02:26Z")
    ap.add_argument("--api", default="http://localhost:8000/api")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    load_env()

    t = pd.Timestamp(args.time)
    products = {}
    for name, bbox in ZONES.items():
        r = requests.get(f"{args.api}/passes", params={"bbox": ",".join(map(str, bbox)),
                         "start": (t - pd.Timedelta(hours=1)).isoformat(), "end": (t + pd.Timedelta(hours=1)).isoformat()},
                         timeout=120)
        r.raise_for_status()
        products[name] = min(r.json(), key=lambda p: abs(pd.Timestamp(p["acquired_at"]) - t))["product_name"]

    original = RULES.read_text()
    created, rows = [], []
    try:
        for thr in args.thresholds:
            RULES.write_text(PATTERN.sub(lambda m: f"{m.group(1)}{thr:.2f}", original, count=1))
            for name, bbox in ZONES.items():
                aid, a = run(args.api, bbox, products[name])
                created.append(aid)
                if a["status"] == "failed":
                    print(f"Échec {name} au seuil {thr} : {a['error']}")
                    continue
                s = a["summary"]
                rows.append({"seuil": thr, "zone": name, "detections": s["detections"], "retenues": s["retenues"],
                             "appariees": s["appariees"], "navires_sombres": s["alertes"],
                             "non_confirmees": s.get("positions_non_confirmees"),
                             "inference_s": a["timings"]["inference_s"]})
                print(rows[-1])
    finally:
        RULES.write_text(original)
        print("Règles restaurées")
        if not args.keep and created:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM alerts WHERE id IN (SELECT alert_id FROM alert_evidence "
                            "WHERE evidence_type = 'analysis' AND evidence_id = ANY(%s))", (created,))
                cur.execute("DELETE FROM analyses WHERE id = ANY(%s)", (created,))
                conn.commit()
            print(f"{len(created)} analyses de balayage supprimées")

    print()
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
