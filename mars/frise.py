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

# Même histogramme par région (sélecteur de la barre d'état) : une position compte dans chaque région qui la contient
REFRESH_10MIN_REGION = """
INSERT INTO stats_10min_region (region, tranche, navires, positions)
SELECT lower(r.name), to_timestamp(floor(extract(epoch FROM p.ts) / 600) * 600), count(DISTINCT p.vessel_id), count(*)
FROM positions p JOIN regions r ON lower(r.name) = ANY($3::text[]) AND ST_Intersects(r.geom::geometry, p.geom::geometry)
WHERE p.ts >= $1::timestamptz AND p.ts < $2::timestamptz GROUP BY 1, 2
ON CONFLICT (region, tranche) DO UPDATE SET navires = EXCLUDED.navires, positions = EXCLUDED.positions
"""
FRANCE = ["bretagne", "manche", "gascogne", "mediterranee"]


async def _start(c, table: str, first: datetime, now: datetime, hours: int) -> datetime:
    if not await c.fetchval(f"SELECT EXISTS (SELECT 1 FROM {table})"):
        return first.replace(hour=0, minute=0, second=0, microsecond=0)
    return (now - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)


async def refresh_stats(c, now: datetime, hours: int = 3) -> dict:
    """Recalcule les statistiques des `hours` dernières heures (les fichiers arrivés en retard y sont comptés). Au
    premier passage, table vide : rattrapage de toutes les positions en base, journée par journée."""
    first = await c.fetchval("SELECT min(ts) FROM positions")
    if first is None:
        return {"minutes": 0}
    out = {}
    for table, queries in (("stats_10min", (REFRESH_MINUTE, REFRESH_10MIN)), ("stats_10min_region", (REFRESH_10MIN_REGION,))):
        start = day = await _start(c, table, first, now, hours)
        while day < now:
            nxt = min(day + timedelta(days=1), now)
            for q in queries:
                await (c.execute(q, day, nxt, FRANCE) if q is REFRESH_10MIN_REGION else c.execute(q, day, nxt))
            day = nxt
        out[table] = start.isoformat()
    # Au delà de la conservation des positions (30 jours), les statistiques ne servent plus à la frise
    for table, col in (("stats_minute", "minute"), ("stats_10min", "tranche"), ("stats_10min_region", "tranche")):
        await c.execute(f"DELETE FROM {table} WHERE {col} < $1::timestamptz - interval '35 days'", now)
    return out


async def timeline(c, start: datetime, end: datetime, bins: int, ratio: float, region: str | None = None) -> dict:
    """Histogramme (de toute la France, ou d'une région) et coupures du flux (toujours globales : le flux est commun
    aux régions)."""
    # Pas plus d'intervalles que de tranches de 10 minutes : sinon des intervalles vides entre deux tranches
    bins = max(1, min(bins, int((end - start) / timedelta(minutes=10))))
    if region:
        rows = await c.fetch("SELECT tranche, navires FROM stats_10min_region WHERE region = $3::text "
                             "AND tranche >= $1::timestamptz AND tranche < $2::timestamptz ORDER BY tranche",
                             start, end, region)
    else:
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
