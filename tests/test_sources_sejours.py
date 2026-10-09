"""Origine des positions (champ « source ») et séjour prolongé d'un navire des listes. Le test d'ingestion demande une
base de test (MARS_TEST_DATABASE_URL) ; les autres sont purs."""
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from mars.ais.live import SOURCE, clean_positions
from mars.archive import compact
from mars.rules import stay, stay_text

T0 = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
URL = os.environ.get("MARS_TEST_DATABASE_URL")


def pos(minutes, sog, lon=-4.5, lat=48.3):
    return {"ts": T0 + timedelta(minutes=minutes), "lon": lon, "lat": lat, "sog": sog}


def test_stay_share_is_weighted_by_time_not_by_positions():
    # 2 h en route (un point par minute), puis 30 h à l'arrêt (un point toutes les dix minutes) : 94 % à l'arrêt
    pts = [pos(m, 12.0, lon=-5 + m / 1000) for m in range(120)]
    pts += [pos(120 + 10 * k, 0.2) for k in range(181)]
    s = stay(pts, 1.0)
    assert s["duree_h"] == pytest.approx(32, abs=0.1)
    assert s["arret_part"] == pytest.approx(30 / 32, abs=0.01)
    assert s["lieu"] == [-4.5, 48.3]              # médiane des positions à l'arrêt, pas du trajet


def test_stay_without_halt_and_text():
    s = stay([pos(m, 10.0) for m in range(0, 1800, 30)], 1.0)
    assert s["arret_part"] == 0 and s["lieu"] is None
    assert stay_text({**s, "duree_h": 29.5}) == "Séjour prolongé : 30 h dans nos eaux, 0 % du temps à l'arrêt"
    t = stay_text({"duree_h": 31, "arret_part": 0.82, "lieu": [-4.5, 48.3], "mouillage_connu": False,
                   "distance_cote_km": 7.5})
    assert t == ("Séjour prolongé : 31 h dans nos eaux, 82 % du temps à l'arrêt, hors de tout mouillage connu, "
                 "à 7,5 km des côtes")
    assert stay([pos(0, 0.0)], 1.0)["arret_part"] is None


def test_clean_positions_keeps_or_fills_source():
    base = {"mmsi": [227932820], "ts": ["2026-10-06 12:00:00 +0000 UTC"], "lat": [48.0], "lon": [-5.0], "sog": [3.0],
            "cog": [10.0], "heading": [10], "nav_status": [0.0]}
    out, _ = clean_positions(pd.DataFrame(base), pd.Timestamp(T0))          # fichier antérieur au champ
    assert out.source.tolist() == [SOURCE]
    out, _ = clean_positions(pd.DataFrame({**base, "source": ["spire"]}), pd.Timestamp(T0))
    assert out.source.tolist() == ["spire"]


def test_compact_adds_source_to_old_files(tmp_path: Path):
    old = pd.DataFrame({"mmsi": [1], "ts": ["2026-10-06 12:00:00 +0000 UTC"]})
    new = pd.DataFrame({"mmsi": [2], "ts": ["2026-10-06 12:01:00 +0000 UTC"], "source": ["aisstream"]})
    old.to_parquet(tmp_path / "a.parquet", index=False)
    data, _ = compact([tmp_path / "a.parquet"])
    assert pq.read_table(io.BytesIO(data)).column("source").to_pylist() == [SOURCE]
    new.to_parquet(tmp_path / "b.parquet", index=False)
    data, _ = compact([tmp_path / "a.parquet", tmp_path / "b.parquet"])
    assert pq.read_table(io.BytesIO(data)).column("source").to_pylist() == [SOURCE, "aisstream"]


@pytest.mark.skipif(not URL, reason="MARS_TEST_DATABASE_URL absente : base de test requise")
def test_ingestion_writes_the_source(tmp_path: Path):
    import psycopg

    from mars.ais.ingest import Ingestor
    from mars.config import load_rules

    mmsi = 227999001
    now = pd.Timestamp.now(tz="UTC").floor("s")
    raw = pd.DataFrame({"mmsi": [mmsi, mmsi], "ts": [f"{now - pd.Timedelta(minutes=5):%Y-%m-%d %H:%M:%S} +0000 UTC",
                                                     f"{now - pd.Timedelta(minutes=3):%Y-%m-%d %H:%M:%S} +0000 UTC"],
                        "lat": [48.0, 48.01], "lon": [-5.0, -5.0], "sog": [12.0, 12.0], "cog": [0.0, 0.0],
                        "heading": [0, 0], "nav_status": [0.0, 0.0], "class_b": [False, False],
                        "source": ["essai", None]})
    with psycopg.connect(URL) as conn:
        conn.execute("DELETE FROM positions WHERE vessel_id IN (SELECT id FROM vessels WHERE mmsi = %s)", (mmsi,))
        conn.commit()
        Ingestor(conn, tmp_path, load_rules()["ingestion"]).load_positions(raw)
        rows = conn.execute("SELECT p.source FROM positions p JOIN vessels v ON v.id = p.vessel_id "
                            "WHERE v.mmsi = %s ORDER BY p.ts", (mmsi,)).fetchall()
        assert [r[0] for r in rows] == ["essai", SOURCE]
        conn.execute("DELETE FROM positions WHERE vessel_id IN (SELECT id FROM vessels WHERE mmsi = %s)", (mmsi,))
        conn.commit()


@pytest.mark.skipif(not URL, reason="MARS_TEST_DATABASE_URL absente : base de test requise")
def test_watchlist_stay_spans_beyond_the_rules_window():
    """Un navire des listes reste 30 h au mouillage : la fenêtre glissante de 24 h ne doit pas tronquer le séjour, qui
    se mesure depuis le début du passage repris de l'alerte précédente."""
    import asyncio
    import json

    import asyncpg

    from mars.config import load_rules
    from mars.rules import run_watchlist

    mmsi, imo, t0 = 227999003, 9999003, T0
    rules = load_rules()

    async def go():
        c = await asyncpg.connect(URL)
        for t in ("json", "jsonb"):
            await c.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        try:
            async with c.transaction():
                vid = await c.fetchval("INSERT INTO vessels (mmsi, imo, name) VALUES ($1, $2, 'ESSAI SEJOUR') RETURNING id",
                                       mmsi, imo)
                await c.execute("INSERT INTO watchlist (source, imo, name, risks, datasets, sanctioned) "
                                "VALUES ('opensanctions', $1, 'ESSAI SEJOUR', '{sanction}', '{eu_essai}', true)", imo)
                await c.execute("REFRESH MATERIALIZED VIEW vessel_watch")
                # 2 h de route, puis 28 h à l'arrêt (un point toutes les dix minutes)
                pts = [(t0 + timedelta(minutes=m), -5 + m / 1000, 48.3, 10.0) for m in range(0, 120, 2)]
                pts += [(t0 + timedelta(minutes=120 + 10 * k), -4.5, 48.3, 0.1) for k in range(169)]
                await c.executemany("INSERT INTO positions (vessel_id, ts, geom, sog_kn) VALUES "
                                    "($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography, $5)",
                                    [(vid, ts, lon, lat, sog) for ts, lon, lat, sog in pts])
                end = pts[-1][0] + timedelta(minutes=1)
                # Premier cycle 10 h après le début : pas encore de séjour prolongé
                await run_watchlist(c, t0 - timedelta(hours=14), t0 + timedelta(hours=10), rules)
                d = await c.fetchval("SELECT details FROM alerts WHERE type = 'WATCHLIST' AND rule_key LIKE $1", f"{vid}:%")
                assert d["sejour"] is None
                # Cycle final, fenêtre de 24 h qui ne voit plus le début du passage
                await run_watchlist(c, end - timedelta(hours=24), end, rules)
                rows = await c.fetch("SELECT details FROM alerts WHERE type = 'WATCHLIST' AND rule_key LIKE $1", f"{vid}:%")
                assert len(rows) == 1
                s = rows[0]["details"]["sejour"]
                assert s["duree_h"] == pytest.approx(30, abs=0.1)
                assert s["arret_part"] == pytest.approx(28 / 30, abs=0.01)
                assert s["lieu"] == [-4.5, 48.3] and s["mouillage_connu"] is False
                assert any(t.startswith("Séjour prolongé : 30 h") for t in rows[0]["details"]["contexte"])
                raise _Rollback
        except _Rollback:
            pass
        finally:
            await c.close()

    asyncio.run(go())


class _Rollback(Exception):
    """Annule la transaction du test : la base de test reste propre."""


def test_reception_range_section():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from mesures_calibration import RANGE_MAX_M, range_section

    lignes = [("bretagne", "aisstream", d) for d in (1000, 5000, 10_000, 30_000, None)]
    txt = "\n".join(range_section({"portee": {"sans_terres": False, "lignes": lignes}}))
    assert "| Bretagne | 5 | 10,0 km | > 300 km | > 300 km | 40 % | aisstream 5 |" in txt
    assert RANGE_MAX_M == 300_000
    assert "table `land` vide" in "\n".join(range_section({"portee": {"sans_terres": True, "lignes": []}}))
