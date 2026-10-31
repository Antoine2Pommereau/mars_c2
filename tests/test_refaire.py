"""Réanalyse d'un passage Sentinel 1 (taches.py essai-reel --lancer ID --refaire, 25/10/2026) : tout refus affiché
comme un refus avec son motif et un code de sortie non nul ; « analyse en cours » distinguée de « passage déjà
analysé » ; nouvelle analyse liée à la précédente (colonne refait), l'ancienne et ses détections gardées ; refusée
pendant une analyse en cours ; index unique : une analyse d'origine et une réanalyse par analyse, jamais deux
lancements simultanés. En base : MARS_TEST_DATABASE_URL."""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mars import essai_reel, sentinel
from mars import travailleurs as T
from mars.config import load_rules

ROOT = Path(__file__).resolve().parents[1]
RULES = load_rules()
URL = os.environ.get("MARS_TEST_DATABASE_URL")


@pytest.mark.parametrize("r, motif", [
    ({"refus": "passage 779 déjà analysé (analyse 103)"}, "déjà analysé"),
    ({"en_cours": "passage déjà en analyse (lancement concurrent, au même instant)"}, "lancement concurrent"),
    ({"en_cours": 42}, "travailleur 42 encore en vie"),
    ({"occupe": 42}, "travailleur 42 encore en vie"),
    ({"en_pause": "Sentinel 1 en pause"}, "source en pause"),
    ({"passages": 0, "en_attente_de_publication": [779]}, "aucune analyse lancée"),
    ({}, "rien à analyser"),
])
def test_every_launch_without_a_worker_is_a_refusal_with_its_reason(r, motif):
    assert motif in essai_reel.refus_lancement({"passage": 779, "mission": "S1", **r})


@pytest.mark.parametrize("r", [{"travailleur": 7, "type": "GP1-S"}, {"attente_calcul": [120], "motif": "rupture"}])
def test_a_worker_or_a_wait_for_a_processor_is_not_a_refusal(r):
    assert essai_reel.refus_lancement(r) is None


@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL)
    c.execute("UPDATE travailleurs SET detruit_le = now() WHERE detruit_le IS NULL")    # annulé avec la transaction
    yield c
    c.rollback()
    c.close()


def _passage(conn):
    return conn.execute("INSERT INTO sar_passes (product_name, acquired_at, mission, satellite, statut, mode) VALUES "
                        "(%s, now() - interval '5 hours', 'S1', 'S1D', 'acquis', 'IW') RETURNING id",
                        (f"REFAIRE_{datetime.now().timestamp()}",)).fetchone()[0]


def _analyse(conn, pid, status="done", refait=None):
    aid = conn.execute(
        "INSERT INTO analyses (pass_id, aoi, mode, status, source, completed_at, refait) VALUES (%s, "
        "ST_MakeEnvelope(-2.5, 50.4, -2.2, 50.7, 4326)::geography, 'full', %s, 'sentinel1', %s, %s) RETURNING id",
        (pid, status, datetime(2026, 10, 23, 18, 30, tzinfo=timezone.utc) if status == "done" else None,
         refait)).fetchone()[0]
    conn.execute("INSERT INTO detections (analysis_id, geom, score) VALUES (%s, "
                 "ST_SetSRID(ST_MakePoint(-2.42, 50.59), 4326)::geography, 0.909)", (aid,))
    return aid


def test_finished_analysis_is_already_analysed_not_a_concurrent_launch(conn):
    pid = _passage(conn)
    a103 = _analyse(conn, pid)
    motif, refait = sentinel.garde_passage(conn, pid)
    assert refait is None and "déjà analysé" in motif and f"analyse {a103}" in motif and "--refaire" in motif
    assert "concurrent" not in motif and "23/10/2026" in motif
    # Le vrai lancement refuse avant toute recherche au catalogue (session None : aucun appel réseau)
    r = sentinel.launch(conn, None, RULES, datetime.now(timezone.utc), None, pid, log=lambda _m: None)
    assert "déjà analysé" in r["refus"]


def test_running_analysis_refuses_with_or_without_refaire(conn):
    pid = _passage(conn)
    a = _analyse(conn, pid, "running")
    for refaire in (False, True):
        motif, refait = sentinel.garde_passage(conn, pid, refaire=refaire)
        assert motif == f"passage {pid} : analyse {a} en cours (running), attendre sa fin" and refait is None
        r = sentinel.launch(conn, None, RULES, datetime.now(timezone.utc), None, pid, log=lambda _m: None,
                            refaire=refaire)
        assert "en cours" in r["refus"]


def test_refaire_links_the_new_analysis_and_keeps_the_old_one_with_its_detections(conn):
    import psycopg
    pid = _passage(conn)
    old = _analyse(conn, pid)
    assert sentinel.garde_passage(conn, pid, refaire=True) == (None, old)
    new = _analyse(conn, pid, "pending", refait=old)           # ce qu'insère le lancement avec --refaire
    rows = conn.execute("SELECT a.id, a.status, a.refait, count(d.id) FROM analyses a LEFT JOIN detections d "
                        "ON d.analysis_id = a.id WHERE a.pass_id = %s GROUP BY 1 ORDER BY 1", (pid,)).fetchall()
    assert rows == [(old, "done", None, 1), (new, "pending", old, 1)]
    # Pendant la réanalyse, une autre est refusée ; une fois finie, la suivante se lie à la plus récente
    assert "en cours" in sentinel.garde_passage(conn, pid, refaire=True)[0]
    conn.execute("UPDATE analyses SET status = 'done', completed_at = now() WHERE id = %s", (new,))
    assert sentinel.garde_passage(conn, pid, refaire=True) == (None, new)
    # Index unique : une seule analyse d'origine, une seule réanalyse de chaque analyse
    for refait in (None, old):
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            _analyse(conn, pid, "pending", refait=refait)
    # Une réanalyse échouée ne bloque rien
    conn.execute("UPDATE analyses SET status = 'failed' WHERE id = %s", (new,))
    assert sentinel.garde_passage(conn, pid, refaire=True) == (None, old)


def test_never_analysed_or_only_failed_passes_launch_normally(conn):
    pid = _passage(conn)
    assert sentinel.garde_passage(conn, pid) == (None, None)
    _analyse(conn, pid, "failed")
    assert sentinel.garde_passage(conn, pid) == (None, None)


def _args(**kw):
    a = argparse.Namespace(args=[], lancer=None, motif=None, malgre_pause=False, du=None, refaire=False)
    a.__dict__.update(kw)
    return a


def test_essai_reel_prints_a_refusal_and_exits_non_zero(conn, monkeypatch, capsys):
    """Cas du 25/10/2026 : « Essai lancé : travailleur None » après un refus de la garde."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import taches
    pid = _passage(conn)
    monkeypatch.setattr(T, "ESSAI_EPROUVES_SEULS", False)
    monkeypatch.setenv("SENTINEL_PLAFOND_ESSAI", "x")        # posée par la commande : retirée après le test
    monkeypatch.delenv("SENTINEL_PLAFOND_ESSAI")
    vu = {}

    def faux(_c, pass_id, malgre_pause=False, motif_pause=None, refaire=False):
        vu["refaire"] = refaire
        return {"en_cours": "passage déjà en analyse (lancement concurrent, au même instant)"}
    monkeypatch.setattr(taches, "launch_sentinel", faux)
    with pytest.raises(SystemExit) as e:
        taches._essai_reel(conn, _args(lancer=pid, refaire=True))
    assert str(e.value).startswith("REFUS : passage déjà en analyse") and vu["refaire"] is True
    assert "Essai lancé" not in capsys.readouterr().out
    # Refus du lancement réel (passage déjà analysé, sans --refaire) : même forme
    _analyse(conn, pid)
    monkeypatch.setattr(taches, "launch_sentinel", lambda c, p, **k: sentinel.launch(
        c, None, RULES, datetime.now(timezone.utc), None, p, log=lambda _m: None, refaire=k.get("refaire", False)))
    monkeypatch.setattr(taches, "run", lambda _c, _t, f, **_k: f())
    with pytest.raises(SystemExit, match="REFUS : passage .* déjà analysé"):
        taches._essai_reel(conn, _args(lancer=pid))
    assert "Essai lancé" not in capsys.readouterr().out


def test_refaire_is_refused_for_sentinel_2(conn, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import taches
    pid = conn.execute("INSERT INTO sar_passes (product_name, acquired_at, mission, satellite, statut) VALUES "
                       "(%s, now() - interval '5 hours', 'S2', 'S2B', 'acquis') RETURNING id",
                       (f"REFAIRE_S2_{datetime.now().timestamp()}",)).fetchone()[0]
    monkeypatch.setattr(taches, "launch_sentinel2", lambda *_a, **_k: pytest.fail("Sentinel 2 lancé"))
    monkeypatch.setattr(T, "ESSAI_EPROUVES_SEULS", False)
    monkeypatch.setenv("SENTINEL_PLAFOND_ESSAI", "x")
    monkeypatch.delenv("SENTINEL_PLAFOND_ESSAI")
    with pytest.raises(SystemExit, match="Sentinel 1 seulement"):
        taches._essai_reel(conn, _args(lancer=pid, refaire=True))
