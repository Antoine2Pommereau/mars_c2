"""Mesures du lot Z7 (scripts/mesures_z7.py) : fonctions pures ; sur la base de test, rapport écrit en lecture seule
stricte (toute écriture serait refusée par PostgreSQL). En base : MARS_TEST_DATABASE_URL."""
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mesures_z7 as z  # noqa: E402

URL = os.environ.get("MARS_TEST_DATABASE_URL")


def test_regions_categories_classes_and_excluded_days():
    assert z.region(5.3, 43.2) == "mediterranee" and z.region(-4.5, 48.4) == "bretagne"   # Bretagne avant Manche
    assert z.region(-1.0, 50.5) == "manche" and z.region(20.0, 60.0) == "hors zone"
    assert (z.categorie(70), z.categorie(84), z.categorie(30), z.categorie(None)) == ("Cargo", "Pétrolier", "Pêche",
                                                                                   "Inconnu")
    assert z.classe(1.5, [2, 5], "km") == "moins de 2 km" and z.classe(None, [2, 5], "km") == "au delà de 5 km"
    assert z.jours_periode(date(2026, 10, 17), date(2026, 10, 19), {date(2026, 10, 18)}) == [date(2026, 10, 17),
                                                                                              date(2026, 10, 19)]


def test_report_is_written_on_a_read_only_connection(tmp_path, monkeypatch):
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    monkeypatch.setenv("DATABASE_URL", URL)
    chemin = asyncio.run(z.mesurer(14, {date(2026, 10, 18)}, tmp_path))
    texte = chemin.read_text()
    for titre in ("## 1. Trous du flux AIS", "## 2. Coupures AIS observées", "## 3. Zone de réception fiable recalculée",
                  "## 4. Rendez vous au large", "## 5. Zones d'arrêt", "## 6. VIIRS sous la lune actuelle"):
        assert titre in texte
    res = json.loads(chemin.with_suffix(".json").read_text())
    assert [v["variante"] for v in res["reception"]["variantes"]][0] == "en service"
    assert set(res["rendezvous"]["variantes"]) == {"danois", "1 000 m, 120 min", "500 m, 60 min"}
    assert chemin.name.startswith("z7_mesures_2")
