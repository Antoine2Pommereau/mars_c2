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
    ap.add_argument("--api", default="http://localhost:8000/api")   # API en direct : pas de délai du relais nginx
    args = ap.parse_args()
    r = requests.post(f"{args.api}/rules/rendezvous/run", json={"day": args.day}, timeout=1800)
    if r.status_code >= 400:
        raise SystemExit(f"Refusé : {r.status_code} {r.text}")
    print("Rendez vous suspects :")
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
