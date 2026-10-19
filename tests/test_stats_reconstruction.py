"""Statistiques de la frise sans trou après un incident (lot C5). Le 18/10/2026, stats_minute est restée vide du 17/10
22:46 au 19/10 06:00 UTC alors que les positions étaient en base : les statistiques n'étaient tenues qu'à la fin d'un
cycle des règles (en échec, disque plein), sur les 3 dernières heures, et les positions chargées en retard n'ont jamais
été comptées. Les tests en base (MARS_TEST_DATABASE_URL) tournent dans une transaction annulée ; la garde de taches est
éprouvée avec des fonctions simulées."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

from mars import frise

URL = os.environ.get("MARS_TEST_DATABASE_URL")
T = datetime(2031, 1, 1, tzinfo=timezone.utc)                 # bien après les données de la base de test


def test_ranges_and_file_time():
    m = [T + timedelta(minutes=k) for k in (0, 1, 2, 30, 31)]
    assert frise.plages(m) == [(T, T + timedelta(minutes=3)), (T + timedelta(minutes=30), T + timedelta(minutes=32))]
    assert frise.heure_fichier("positions/zone=manche/date=2026-10-18", "013012.parquet") == \
        datetime(2026, 10, 18, 1, 30, 12, tzinfo=timezone.utc)
    assert frise.heure_fichier("statiques/zone=manche", "x.parquet") is None


def _base(fn):
    asyncpg = pytest.importorskip("asyncpg")

    async def go():
        c = await asyncpg.connect(URL)
        tr = c.transaction()
        await tr.start()
        try:
            vid = await c.fetchval("INSERT INTO vessels (mmsi, name, ship_type, ais_class) VALUES (636999555, "
                                   "'STATS', 'Cargo', 'A') RETURNING id")
            # 3 h de positions, deux par minute, au large de Brest
            await c.execute("INSERT INTO positions (vessel_id, ts, geom) SELECT $1, $2::timestamptz + k * interval "
                            "'30 seconds', ST_MakePoint(-5.5, 48.3)::geography FROM generate_series(0, 359) k", vid, T)
            await fn(c)
        finally:
            await tr.rollback()
            await c.close()
    asyncio.run(go())


@pytest.mark.skipif(not URL, reason="MARS_TEST_DATABASE_URL absent")
def test_hole_is_rebuilt_and_rebuild_is_idempotent():
    async def essai(c):
        r = await frise.reconstruire(c, T, T + timedelta(hours=3))
        assert r["minutes_ajoutees"] == 180 and r["tranches_ajoutees"] == 18 and r["minutes_corrigees"] == 0
        assert r["regions_ajoutees"] >= 18                       # Bretagne (et Manche, qui la recouvre en partie)
        # Trou simulé de 31 minutes, et une minute fausse
        await c.execute("DELETE FROM stats_minute WHERE minute >= $1 AND minute < $2", T + timedelta(minutes=60),
                        T + timedelta(minutes=91))
        await c.execute("UPDATE stats_minute SET positions = 1 WHERE minute = $1", T + timedelta(minutes=120))
        manque = await frise.minutes_manquantes(c, T, T + timedelta(hours=3))
        assert len(manque) == 31 and frise.plages(manque) == [(T + timedelta(minutes=60), T + timedelta(minutes=91))]
        r = await frise.reconstruire(c, T, T + timedelta(hours=3))
        assert (r["minutes_ajoutees"], r["minutes_corrigees"], r["tranches_ajoutees"], r["tranches_corrigees"]) == \
            (31, 1, 0, 0)
        assert await frise.minutes_manquantes(c, T, T + timedelta(hours=3)) == []
        assert await c.fetchval("SELECT positions FROM stats_minute WHERE minute = $1", T + timedelta(minutes=120)) == 2
        # Second passage : rien n'est réécrit
        r = await frise.reconstruire(c, T, T + timedelta(hours=3))
        assert sum(r.values()) == 0
    _base(essai)


@pytest.mark.skipif(not URL, reason="MARS_TEST_DATABASE_URL absent")
def test_late_files_are_counted_even_outside_the_window():
    """Reprise après incident : un fichier écrit à T + 1 h 30, chargé 30 h plus tard, fait recompter son heure."""
    async def essai(c):
        await c.execute("INSERT INTO stats_suivi (cle, valeur) VALUES ('registre', $1) ON CONFLICT (cle) DO UPDATE "
                        "SET valeur = EXCLUDED.valeur", T + timedelta(hours=29))
        await c.execute("INSERT INTO ingested_files (folder, name, rows_read, rows_kept, ingested_at) VALUES "
                        "('positions/zone=bretagne/date=2031-01-01', '013000.parquet', 60, 60, $1)",
                        T + timedelta(hours=30))
        now = T + timedelta(hours=31)
        out = await frise.refresh_stats(c, now)
        # de T + 30 min à T + 1 h 30, prolongé jusqu'au bout de sa tranche de 10 minutes (T + 1 h 40)
        assert out["rattrapage"]["minutes_ajoutees"] == 70
        assert await frise.minutes_manquantes(c, T + timedelta(minutes=30), T + timedelta(minutes=91)) == []
        assert await c.fetchval("SELECT valeur FROM stats_suivi WHERE cle = 'registre'") == T + timedelta(hours=30)
        # Cycle suivant : rien de nouveau au registre, aucun rattrapage
        assert "rattrapage" not in await frise.refresh_stats(c, now + timedelta(minutes=5))
    _base(essai)


class _Ntfy:
    def __init__(self):
        self.envois = []

    def envoyer(self, titre, message, **_k):
        self.envois.append((titre, message))
        return 200


class _Secours:
    def __init__(self):
        self.ntfy = _Ntfy()


def _taches(monkeypatch, manques: list[list], reconstruction):
    sys.path.insert(0, "scripts")
    import taches
    appels, signes, runs = [], [], []
    monkeypatch.setattr(taches, "_asyncpg", lambda _fn: manques.pop(0))
    monkeypatch.setattr(taches, "stats_reconstruire", lambda a, b: (appels.append((a, b)), reconstruction(a, b))[1])
    monkeypatch.setattr(taches.supervision, "signe", lambda _c, _service, _t, detail=None: signes.append(detail))

    def run(_conn, task, fn, **_k):
        runs.append(task)
        try:
            return fn()
        except Exception:
            return None
    monkeypatch.setattr(taches, "run", run)
    return taches, appels, signes, runs


def test_guard_rebuilds_beyond_ten_missing_minutes(monkeypatch):
    trou = [T + timedelta(minutes=k) for k in range(40)] + [T + timedelta(hours=5)]
    taches, appels, signes, runs = _taches(monkeypatch, [trou, []], lambda _a, _b: {"minutes_ajoutees": 1})
    s = _Secours()
    out = taches.stats_garde(None, s)
    assert runs == ["stats_reconstruction"] and len(appels) == 2          # deux plages, une requête chacune
    assert out["reconstruction"]["minutes_ajoutees"] == 2 and out["minutes_manquantes"] == 0
    assert signes[-1]["minutes_manquantes"] == 0 and s.ntfy.envois == []


def test_guard_ignores_a_few_missing_minutes(monkeypatch):
    taches, appels, signes, runs = _taches(monkeypatch, [[T, T + timedelta(minutes=1)]], lambda _a, _b: {})
    assert taches.stats_garde(None, _Secours())["minutes_manquantes"] == 2
    assert runs == [] and appels == [] and signes[-1] == {"minutes_manquantes": 2, "plages": 1}


def test_guard_failure_goes_to_the_phone(monkeypatch):
    def panne(_a, _b):
        raise OSError("No space left on device")
    taches, _appels, signes, _runs = _taches(monkeypatch, [[T + timedelta(minutes=k) for k in range(30)]], panne)
    monkeypatch.setattr(taches.supervision, "grafana_repond", lambda: False)
    s = _Secours()
    out = taches.stats_garde(None, s)
    assert out["reconstruction"] == "echec" and signes[-1]["minutes_manquantes"] == 30
    assert len(s.ntfy.envois) == 1 and "30 minutes absentes" in s.ntfy.envois[0][1]


def test_guard_failure_is_left_to_grafana_when_it_answers(monkeypatch):
    def panne(_a, _b):
        raise OSError("No space left on device")
    taches, _appels, _signes, runs = _taches(monkeypatch, [[T + timedelta(minutes=k) for k in range(30)]], panne)
    monkeypatch.setattr(taches.supervision, "grafana_repond", lambda: True)
    s = _Secours()
    assert taches.stats_garde(None, s)["reconstruction"] == "echec" and runs == ["stats_reconstruction"]
    assert s.ntfy.envois == []                                   # règle Grafana : une seule notification


def test_gap_with_a_trickle_of_positions_is_a_real_outage():
    """14/10/2026, 10:17 à 12:59 : 154 positions en 162 min pour environ 190 par minute d'habitude (0,5 %)."""
    sys.path.insert(0, "scripts")
    import taches
    assert taches.nature_trou(154, 162, 190).startswith("vrai arrêt")
    assert taches.nature_trou(0, 30, 190).startswith("vrai arrêt")
    assert taches.nature_trou(31 * 60 * 180, 31 * 60, 190).startswith("trou des statistiques")    # 18/10 : 95 %
    assert taches.nature_trou(1600, 162, 190).startswith("trou des statistiques")                 # 5,2 %
