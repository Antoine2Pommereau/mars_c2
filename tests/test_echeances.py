"""Lot Z1 : échéances des secrets (mars/echeances.py), dates du .env relevées chaque jour, notifiées 14 puis 3 jours
avant puis à l'expiration, une fois par palier et par date ; sans date : « non déclarée »."""
import os
from datetime import date

import pytest

from mars import echeances as E
from mars.config import load_rules

URL = os.environ.get("MARS_TEST_DATABASE_URL")


def test_dates_in_both_formats_and_unreadable():
    assert E.lire_date("14/11/2026") == (date(2026, 11, 14), False)
    assert E.lire_date("2026-11-14") == (date(2026, 11, 14), False)
    assert E.lire_date("") == (None, False) and E.lire_date(None) == (None, False)
    assert E.lire_date("novembre") == (None, True) and E.lire_date("31/02/2026") == (None, True)


def test_levels():
    assert [E.palier(j, [14, 3]) for j in (40, 15, 14, 4, 3, 0, -1, None)] == \
        [None, None, "j14", "j14", "j3", "j3", "expiree", None]


def test_state_reads_only_dates_and_flags_missing_ones():
    env = {"CDSE_TRAVAILLEUR_EXPIRENT_LE": "14/11/2026", "CDSE_TRAVAILLEUR_SECRET_KEY": "secret-jamais-lu"}
    e = {x["cle"]: x for x in E.etat(env, date(2026, 10, 22))}
    assert e["cdse_travailleur"]["jours"] == 23 and e["cdse_travailleur"]["palier"] is None
    assert e["earthdata"]["expire_le"] is None and not e["earthdata"]["illisible"]
    assert "secret-jamais-lu" not in repr(e)
    assert len(e) == 6


@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL, autocommit=True)
    c.execute("DELETE FROM echeances")
    yield c
    c.execute("DELETE FROM echeances")
    c.close()


def test_notified_once_per_level_and_reset_when_date_changes(conn, monkeypatch):
    for _c, _l, var in E.ECHEANCES:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CDSE_TRAVAILLEUR_EXPIRENT_LE", "14/11/2026")
    envois = []
    notifier = lambda cat, grav, titre, msg: envois.append((grav, msg))   # noqa: E731
    rules = load_rules()
    r = E.relever(conn, rules, date(2026, 10, 22), notifier)
    assert envois == [] and r["declarees"] == 1 and len(r["non_declarees"]) == 5
    E.relever(conn, rules, date(2026, 10, 31), notifier)                 # 14 jours avant
    E.relever(conn, rules, date(2026, 11, 1), notifier)                  # même palier : rien de plus
    assert [g for g, _m in envois] == ["moyenne"] and "14/11/2026" in envois[0][1]
    E.relever(conn, rules, date(2026, 11, 12), notifier)                 # 2 jours : palier j3
    E.relever(conn, rules, date(2026, 11, 15), notifier)                 # expirée
    assert [g for g, _m in envois] == ["moyenne", "elevee", "elevee"] and "expirée" in envois[-1][1]
    monkeypatch.setenv("CDSE_TRAVAILLEUR_EXPIRENT_LE", "2027-05-14")     # clés renouvelées : nouvelle date
    E.relever(conn, rules, date(2026, 11, 15), notifier)
    assert len(envois) == 3
    row = conn.execute("SELECT expire_le, notifiee FROM echeances WHERE cle = 'cdse_travailleur'").fetchone()
    assert row == (date(2027, 5, 14), None)
    assert conn.execute("SELECT count(*) FROM echeances WHERE expire_le IS NULL").fetchone()[0] == 5
