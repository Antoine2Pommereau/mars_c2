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


def test_gap_duration_uses_the_last_message_despite_fractional_seconds():
    """La clé d'un épisode tronque l'heure du dernier message à la seconde ; la position garde ses fractions. Le rapport
    du 25/10 prenait la position d'avant : toutes les durées valaient 0 min."""
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import asyncpg
    from datetime import datetime, timedelta, timezone

    async def run():
        w = await asyncpg.connect(URL)
        cle, ids = None, []
        try:
            vid = await w.fetchval("SELECT id FROM vessels ORDER BY id LIMIT 1")
            if vid is None:
                pytest.skip("aucun navire dans la base de test")
            # Dernier message à 12:00:05,734 en route à 10 nœuds, réapparition 3 h plus tard à 3 km : arrêt
            dernier = datetime(2031, 3, 4, 12, 0, 5, 734000, tzinfo=timezone.utc)
            retour = dernier + timedelta(hours=3)
            for ts, lon, sog in ((dernier - timedelta(minutes=1), -1.002, 10.0), (dernier, -1.0, 10.0),
                                 (retour, -0.958, 10.0)):
                ids.append(await w.fetchval(
                    "INSERT INTO positions (vessel_id, ts, geom, sog_kn) VALUES ($1, $2, "
                    "ST_SetSRID(ST_MakePoint($3, 50.0), 4326)::geography, $4) RETURNING id", vid, ts, lon, sog))
            tl = dernier.replace(microsecond=0)
            cle = f"{vid}:{tl:%Y%m%dT%H%M%S}"
            await w.execute("INSERT INTO episodes_observes (regle, cle, gravite, premiere_le) VALUES ('ais_gap', $1, "
                            "'moyenne', $2) ON CONFLICT DO NOTHING", cle, tl)
            c = await asyncpg.connect(URL, server_settings={"default_transaction_read_only": "on"})
            try:
                x = await z.coupures(c, tl - timedelta(days=1), tl + timedelta(days=1), set(), z.load_rules())
            finally:
                await c.close()
            e = next(e for e in x["episodes"] if e["cle"] == cle)
            assert e["duree_min"] == 180 and e["reapparu"]
            assert e["deplacement_km"] == 3.0 and e["conduite"] == "arret"
        finally:
            if cle:
                await w.execute("DELETE FROM episodes_observes WHERE regle = 'ais_gap' AND cle = $1", cle)
            if ids:
                await w.execute("DELETE FROM positions WHERE id = ANY($1::bigint[])", ids)
            await w.close()
    asyncio.run(run())


def test_sections_option_limits_the_report(tmp_path, monkeypatch):
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    monkeypatch.setenv("DATABASE_URL", URL)
    chemin = asyncio.run(z.mesurer(14, {date(2026, 10, 18)}, tmp_path, ("coupures",)))
    texte = chemin.read_text()
    assert "## 2. Coupures AIS observées" in texte and "## 4. Rendez vous au large" not in texte
