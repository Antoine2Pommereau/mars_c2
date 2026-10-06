"""Frise : coupures du flux AIS et histogramme de densité, calculés sur les statistiques par minute et par tranche."""
from datetime import datetime, timedelta, timezone

from mars.frise import outage_intervals, rebin

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)


def counts(minutes: int, cut: range = range(0), value: int = 500) -> dict:
    return {T0 + k * M: value for k in range(minutes) if k not in cut}


def test_outage_found_at_the_minute():
    out = outage_intervals(counts(240, range(60, 100)), T0, T0 + 240 * M, ratio=0.2)
    assert out == [(T0 + 60 * M, T0 + 100 * M)]


def test_short_dips_ignored_and_close_cuts_merged():
    c = counts(240, range(30, 32))                          # 2 minutes : bruit d'écriture, ignoré
    for k in [*range(100, 110), *range(112, 120)]:          # deux coupures séparées de 2 minutes : réunies
        c.pop(T0 + k * M, None)
    out = outage_intervals(c, T0, T0 + 240 * M, ratio=0.2)
    assert out == [(T0 + 100 * M, T0 + 120 * M)]


def test_low_but_alive_feed_is_not_an_outage():
    c = counts(240)
    for k in range(60, 100):
        c[T0 + k * M] = 300                                  # nuit plus calme : 60 % de la médiane, pas une coupure
    assert outage_intervals(c, T0, T0 + 240 * M, ratio=0.2) == []
    assert outage_intervals({}, T0, T0 + 240 * M, ratio=0.2) == []


def test_rebin_keeps_holes_distinct_from_zero():
    rows = [(T0 + timedelta(minutes=10 * k), 100 + k) for k in range(12) if k not in (4, 5)]
    out = rebin(rows, T0, T0 + timedelta(hours=2), 6)
    assert [b["navires"] for b in out] == [100, 102, None, 106, 108, 110]
