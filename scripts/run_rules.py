"""Évalue les règles comportementales sur les journées AIS chargées.

Exemple :
    python scripts/run_rules.py --day 2024-06-05
"""
import argparse
import json

import requests


def main():
    ap = argparse.ArgumentParser(description="Règles comportementales de MARS C2")
    ap.add_argument("--day", help="AAAA-MM-JJ ; toutes les journées chargées si absent")
    ap.add_argument("--api", default="http://localhost:8000/api")
    ap.add_argument("--selftest", action="store_true", help="Test par injection de la règle des coupures AIS")   # API en direct : pas de délai du relais nginx
    args = ap.parse_args()
    if args.selftest:
        r = requests.post(f"{args.api}/rules/ais_gap/selftest", json={"day": args.day}, timeout=1800)
        if r.status_code >= 400:
            raise SystemExit(f"Test refusé : {r.status_code} {r.text}")
        print("Test par injection des coupures AIS :")
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))
        return
    for rule, label in [("rendezvous", "Rendez vous suspects"), ("ais_gap", "Coupures AIS")]:
        r = requests.post(f"{args.api}/rules/{rule}/run", json={"day": args.day}, timeout=1800)
        if r.status_code >= 400:
            raise SystemExit(f"{label} : refusé, {r.status_code} {r.text}")
        print(f"{label} :")
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
