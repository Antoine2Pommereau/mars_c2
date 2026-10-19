"""Frise : statistiques du trafic (positions par minute, navires par tranche de 10 minutes), coupures du flux AIS
et histogramme de densité, pour des plages de 1 heure à 30 jours.

Les statistiques sont tenues par le conteneur taches à chaque cycle (refresh_stats) ; l'API les lit au lieu des
positions, ce qui garde les plages de 7 et 30 jours légères sur un serveur de 2 Go. Les fonctions de calcul
(outage_intervals, rebin) sont pures et couvertes par tests/test_frise.py.
"""
import re
import statistics
from datetime import datetime, timedelta, timezone

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

# Reconstruction (lot C5) : mêmes calculs, mais une ligne déjà juste n'est pas réécrite ; chaque requête rend le nombre
# de lignes ajoutées et corrigées
_SEULEMENT_SI_DIFFERENT = {
    REFRESH_MINUTE: ("stats_minute", "positions = EXCLUDED.positions",
                     "stats_minute.positions IS DISTINCT FROM EXCLUDED.positions"),
    REFRESH_10MIN: ("stats_10min", "navires = EXCLUDED.navires, positions = EXCLUDED.positions",
                    "(stats_10min.navires, stats_10min.positions) IS DISTINCT FROM (EXCLUDED.navires, EXCLUDED.positions)"),
    REFRESH_10MIN_REGION: ("stats_10min_region", "navires = EXCLUDED.navires, positions = EXCLUDED.positions",
                           "(stats_10min_region.navires, stats_10min_region.positions) IS DISTINCT FROM "
                           "(EXCLUDED.navires, EXCLUDED.positions)"),
}


def _compte(q: str) -> str:
    table, sets, cond = _SEULEMENT_SI_DIFFERENT[q]
    base = q.split("DO UPDATE SET")[0]
    return (f"WITH r AS ({base}DO UPDATE SET {sets} WHERE {cond} RETURNING (xmax = 0) AS neuf) "
            "SELECT count(*) FILTER (WHERE neuf) AS ajoutees, count(*) FILTER (WHERE NOT neuf) AS corrigees FROM r")


async def reconstruire(c, debut: datetime, fin: datetime) -> dict:
    """Recalcule stats_minute, stats_10min et stats_10min_region depuis les positions sur [debut, fin), journée par
    journée, sans réécrire les lignes déjà justes : idempotent (un second passage n'ajoute ni ne corrige rien)."""
    from mars import traces
    out = {"minutes_ajoutees": 0, "minutes_corrigees": 0, "tranches_ajoutees": 0, "tranches_corrigees": 0,
           "regions_ajoutees": 0, "regions_corrigees": 0}
    noms = {REFRESH_MINUTE: "minutes", REFRESH_10MIN: "tranches", REFRESH_10MIN_REGION: "regions"}
    # Bornes alignées sur les tranches de 10 minutes : une tranche coupée serait recalculée sur une partie seulement
    debut = debut.replace(minute=debut.minute - debut.minute % 10, second=0, microsecond=0)
    fin = fin.replace(second=0, microsecond=0)
    if fin.minute % 10:
        fin = fin.replace(minute=fin.minute - fin.minute % 10) + timedelta(minutes=10)
    with traces.span("frise.reconstruction", **{"frise.debut": debut.isoformat(), "frise.fin": fin.isoformat()}) as sp:
        jour = debut
        while jour < fin:
            nxt = min(jour + timedelta(days=1), fin)
            for q, nom in noms.items():
                args = (jour, nxt, FRANCE) if q is REFRESH_10MIN_REGION else (jour, nxt)
                r = await c.fetchrow(_compte(q), *args)
                out[f"{nom}_ajoutees"] += r["ajoutees"]
                out[f"{nom}_corrigees"] += r["corrigees"]
            jour = nxt
        sp.set_attributes({f"frise.{k}": v for k, v in out.items()})
    return out


async def minutes_manquantes(c, debut: datetime, fin: datetime) -> list[datetime]:
    """Minutes qui ont des positions mais aucune ligne dans stats_minute (garde contre un trou des statistiques)."""
    return [r["m"] for r in await c.fetch(
        """SELECT m FROM (SELECT DISTINCT date_trunc('minute', ts) AS m FROM positions
                          WHERE ts >= $1::timestamptz AND ts < $2::timestamptz) p
           WHERE NOT EXISTS (SELECT 1 FROM stats_minute s WHERE s.minute = p.m) ORDER BY m""", debut, fin)]


def plages(minutes: list[datetime], marge: timedelta = timedelta(minutes=10)) -> list[tuple[datetime, datetime]]:
    """Minutes regroupées en plages continues (écart de `marge` au plus), pour reconstruire peu de requêtes."""
    out: list[list[datetime]] = []
    for m in sorted(minutes):
        if out and m - out[-1][1] <= marge:
            out[-1][1] = m + MINUTE
        else:
            out.append([m, m + MINUTE])
    return [(a, b) for a, b in out]


_DOSSIER = re.compile(r"date=(\d{4}-\d{2}-\d{2})$")
_NOM = re.compile(r"^(\d{2})(\d{2})(\d{2})")


def heure_fichier(folder: str, name: str) -> datetime | None:
    """Heure d'écriture d'un fichier de la collecte : date du dossier, heure du nom (positions/…/date=2026-10-18,
    013012.parquet)."""
    d, n = _DOSSIER.search(folder or ""), _NOM.match(name or "")
    if not d or not n:
        return None
    return datetime.fromisoformat(f"{d.group(1)}T{n.group(1)}:{n.group(2)}:{n.group(3)}+00:00")


async def _en_retard(c, avant: datetime) -> tuple[list[tuple[datetime, datetime]], datetime | None]:
    """Fichiers chargés depuis le dernier passage dont l'heure précède `avant` (rattrapage après un incident, chargement
    tardif) : plages d'une heure avant leur écriture (les positions d'un fichier précèdent de peu sa réception)."""
    dernier = await c.fetchval("SELECT valeur FROM stats_suivi WHERE cle = 'registre'")
    rows = await c.fetch("SELECT folder, name, ingested_at FROM ingested_files WHERE ingested_at > $1::timestamptz "
                         "AND folder LIKE 'positions/%'", dernier or datetime(1970, 1, 1, tzinfo=timezone.utc))
    nouveau = max((r["ingested_at"] for r in rows), default=None)
    if dernier is None:                         # premier passage après le déploiement : on part de maintenant
        return [], nouveau
    minutes = []
    for r in rows:
        t = heure_fichier(r["folder"], r["name"])
        if t is not None and t < avant:
            minutes += [t.replace(second=0) - timedelta(minutes=k) for k in range(0, 61, 10)]
    return plages(minutes, timedelta(minutes=30)), nouveau


async def _start(c, table: str, first: datetime, now: datetime, hours: int) -> datetime:
    if not await c.fetchval(f"SELECT EXISTS (SELECT 1 FROM {table})"):
        return first.replace(hour=0, minute=0, second=0, microsecond=0)
    return (now - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)


async def refresh_stats(c, now: datetime, hours: int = 3) -> dict:
    """Recalcule les statistiques des `hours` dernières heures, puis celles des fichiers chargés en retard depuis le
    dernier passage, même anciens (lot C5 : après l'incident du 18/10/2026, les positions de la période chargées en
    retard n'avaient jamais été comptées). Au premier passage, table vide : rattrapage de toutes les positions en base,
    journée par journée."""
    first = await c.fetchval("SELECT min(ts) FROM positions")
    if first is None:
        return {"minutes": 0}
    out = {}
    fenetre = (now - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    retard, registre = await _en_retard(c, fenetre)
    for a, b in retard:
        r = await reconstruire(c, a, b)
        out["rattrapage"] = {k: out.get("rattrapage", {}).get(k, 0) + v for k, v in r.items()}
    if registre is not None:
        await c.execute("INSERT INTO stats_suivi (cle, valeur) VALUES ('registre', $1) ON CONFLICT (cle) "
                        "DO UPDATE SET valeur = EXCLUDED.valeur", registre)
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
