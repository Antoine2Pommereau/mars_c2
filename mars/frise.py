"""Frise : statistiques du trafic (positions par minute, navires par tranche de 10 minutes), coupures du flux AIS
et histogramme de densité, pour des plages de 1 heure à 30 jours.

Les statistiques sont tenues par le conteneur taches à chaque cycle (refresh_stats) ; l'API les lit au lieu des
positions, ce qui garde les plages de 7 et 30 jours légères sur un serveur de 2 Go. Les fonctions de calcul
(outage_intervals, rebin) sont pures et couvertes par tests/test_frise.py.
"""
import statistics
from datetime import datetime, timedelta

MINUTE = timedelta(minutes=1)


def outage_intervals(counts: dict, start: datetime, end: datetime, ratio: float,
                     min_minutes: int = 3, merge_minutes: int = 2) -> list[tuple[datetime, datetime]]:
    """Intervalles où le flux AIS était coupé : minutes sous `ratio` fois la médiane des positions par minute de la
    plage (minutes sans aucune position comprises). Les intervalles séparés de `merge_minutes` au plus sont réunis ;
    ceux de moins de `min_minutes` sont ignorés (bruit d'une écriture de fichier à cheval sur deux minutes)."""
    first = start.replace(second=0, microsecond=0)
    n = int((end - first) / MINUTE)
    if n <= 0:
        return []
    series = [counts.get(first + k * MINUTE, 0) for k in range(n)]
    observed = [v for v in series if v > 0]
    if not observed:
        return []
    threshold = ratio * statistics.median(observed)
    out: list[list[datetime]] = []
    for k, v in enumerate(series):
        if v >= threshold:
            continue
        t = first + k * MINUTE
        if out and t - out[-1][1] <= merge_minutes * MINUTE:
            out[-1][1] = t + MINUTE
        else:
            out.append([t, t + MINUTE])
    return [(a, b) for a, b in out if b - a >= min_minutes * MINUTE]


def rebin(rows: list[tuple[datetime, int]], start: datetime, end: datetime, bins: int) -> list[dict]:
    """Moyenne des navires par tranche de 10 minutes, regroupée en `bins` intervalles égaux de la plage ; un
    intervalle sans donnée vaut None (trou de la collecte, distinct d'un trafic nul)."""
    width = (end - start) / bins
    acc: list[list[int]] = [[] for _ in range(bins)]
    for t, v in rows:
        k = int((t - start) / width)
        if 0 <= k < bins:
            acc[k].append(v)
    return [{"t": (start + k * width).isoformat(), "navires": round(sum(a) / len(a)) if a else None}
            for k, a in enumerate(acc)]


REFRESH_MINUTE = """
INSERT INTO stats_minute (minute, positions)
SELECT date_trunc('minute', ts), count(*) FROM positions
WHERE ts >= $1::timestamptz AND ts < $2::timestamptz GROUP BY 1
ON CONFLICT (minute) DO UPDATE SET positions = EXCLUDED.positions
"""
REFRESH_10MIN = """
INSERT INTO stats_10min (tranche, navires, positions)
SELECT to_timestamp(floor(extract(epoch FROM ts) / 600) * 600), count(DISTINCT vessel_id), count(*) FROM positions
WHERE ts >= $1::timestamptz AND ts < $2::timestamptz GROUP BY 1
ON CONFLICT (tranche) DO UPDATE SET navires = EXCLUDED.navires, positions = EXCLUDED.positions
"""


async def refresh_stats(c, now: datetime, hours: int = 3) -> dict:
    """Recalcule les statistiques des `hours` dernières heures (les fichiers arrivés en retard y sont comptés). Au
    premier passage, table vide : rattrapage de toutes les positions en base, journée par journée."""
    first = await c.fetchval("SELECT min(ts) FROM positions")
    if first is None:
        return {"minutes": 0}
    if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM stats_10min)"):
        start = first.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = (now - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    day = start
    while day < now:
        nxt = min(day + timedelta(days=1), now)
        await c.execute(REFRESH_MINUTE, day, nxt)
        await c.execute(REFRESH_10MIN, day, nxt)
        day = nxt
    # Au delà de la conservation des positions (30 jours), les statistiques ne servent plus à la frise
    await c.execute("DELETE FROM stats_minute WHERE minute < $1::timestamptz - interval '35 days'", now)
    await c.execute("DELETE FROM stats_10min WHERE tranche < $1::timestamptz - interval '35 days'", now)
    return {"depuis": start.isoformat()}


async def timeline(c, start: datetime, end: datetime, bins: int, ratio: float) -> dict:
    # Pas plus d'intervalles que de tranches de 10 minutes : sinon des intervalles vides entre deux tranches
    bins = max(1, min(bins, int((end - start) / timedelta(minutes=10))))
    rows = await c.fetch("SELECT tranche, navires FROM stats_10min WHERE tranche >= $1::timestamptz "
                         "AND tranche < $2::timestamptz ORDER BY tranche", start, end)
    minutes = await c.fetch("SELECT minute, positions FROM stats_minute WHERE minute >= $1::timestamptz "
                            "AND minute < $2::timestamptz", start, end)
    counts = {r["minute"]: r["positions"] for r in minutes}
    # Les coupures ne sont cherchées que là où des statistiques existent : avant la première minute connue, on ne
    # sait rien ; après la dernière, les statistiques n'ont pas encore été rafraîchies (toutes les 5 minutes) et la
    # dernière minute est souvent incomplète. Une coupure en cours est signalée par la barre d'état.
    cuts = []
    if counts:
        cuts = outage_intervals(counts, max(start, min(counts)), min(end, max(counts)), ratio)
    return {"debut": start.isoformat(), "fin": end.isoformat(),
            "densite": rebin([(r["tranche"], r["navires"]) for r in rows], start, end, bins),
            "coupures": [{"debut": a.isoformat(), "fin": b.isoformat()} for a, b in cuts]}
