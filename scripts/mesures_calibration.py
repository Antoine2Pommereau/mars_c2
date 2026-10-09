"""Mesures pour la recalibration des règles sur les données françaises, sans changer aucun seuil.

Mesure, sur les positions accumulées en base (donc après allègement : un point par minute en route, un toutes les
dix minutes à l'arrêt, les mêmes données que celles que voient les règles) :
  1. les intervalles entre messages, par navire, par type de navire et par zone ;
  2. les paires d'intervalles par cellule de la zone de réception fiable, et la surface que donneraient d'autres
     valeurs de reception.min_pairs, du seuil de continuité et de l'intervalle jugé normal ;
  3. les épisodes et les alertes par règle et par jour (journal des cycles des règles, table alerts) ;
puis écrit un rapport Markdown : pour chaque seuil de config/rules.yaml, sa valeur, ce que montrent les mesures et
une proposition argumentée. Rien n'est modifié en base ni dans la configuration.

Le calcul se fait journée par journée (tri borné, mémoire de quelques dizaines de Mo) : adapté au petit serveur.

Usage (sur le serveur, dans le conteneur taches) :
    docker compose exec -T taches python scripts/mesures_calibration.py --jours 14 > docs/mesures_calibration.md
    docker compose exec -T taches python scripts/taches.py mesures --jours 14 > docs/mesures_calibration.md
"""
import argparse
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from mars.ais.live import ZONES
from mars.config import database_url, load_env, load_rules

# Bornes des classes d'intervalles (secondes) ; la dernière classe est « au delà de 2 h »
BOUNDS = [30, 60, 90, 120, 150, 180, 240, 300, 420, 600, 900, 1200, 1800, 2700, 3600, 5400, 7200]
CATEGORIES = ["Cargo", "Pétrolier", "Passagers", "Pêche", "Plaisance et voile", "Remorquage et service", "Autre",
              "Inconnu"]
CATEGORY_SQL = """CASE
    WHEN v.ship_type_code BETWEEN 70 AND 79 THEN 'Cargo'
    WHEN v.ship_type_code BETWEEN 80 AND 89 THEN 'Pétrolier'
    WHEN v.ship_type_code BETWEEN 60 AND 69 THEN 'Passagers'
    WHEN v.ship_type_code = 30 THEN 'Pêche'
    WHEN v.ship_type_code IN (36, 37) THEN 'Plaisance et voile'
    WHEN v.ship_type_code IN (31, 32, 33, 34, 50, 51, 52, 53, 54, 55, 58) THEN 'Remorquage et service'
    WHEN v.ship_type_code IS NULL OR v.ship_type_code = 0 THEN 'Inconnu'
    ELSE 'Autre' END"""
# Zone d'une position : la première zone de mars/ais/live.py qui la contient (Bretagne et Manche se recouvrent)
ZONE_SQL = "CASE " + " ".join(
    f"WHEN lat BETWEEN {a} AND {c} AND lon BETWEEN {b} AND {d} THEN '{k}'" for k, (a, b, c, d) in ZONES.items()) \
    + " ELSE 'hors zone' END"
INTERVALS_GRID = [120, 180, 300, 600]
PAIRS_GRID = [25, 50, 100, 200]
COVERAGE_GRID = [0.80, 0.90, 0.95]

DAY_SQL = """
WITH p AS (
    SELECT p.vessel_id, p.ts, p.sog_kn, ST_X(p.geom::geometry) AS lon, ST_Y(p.geom::geometry) AS lat,
           extract(epoch FROM lead(p.ts) OVER (PARTITION BY p.vessel_id ORDER BY p.ts) - p.ts) AS dt
    FROM positions p
    WHERE p.ts >= %(d0)s AND p.ts < %(d1)s
)
SELECT p.vessel_id, {cat} AS cat, {zone} AS zone, coalesce(v.ais_class, '?') AS classe,
       (coalesce(p.sog_kn, 0) >= 1) AS en_route, p.dt, p.lon, p.lat
FROM p JOIN vessels v ON v.id = p.vessel_id
WHERE p.dt IS NOT NULL
"""


def bucket(dt: float) -> int:
    for k, b in enumerate(BOUNDS):
        if dt <= b:
            return k
    return len(BOUNDS)


def label(k: int) -> str:
    if k == len(BOUNDS):
        return f"> {fmt_s(BOUNDS[-1])}"
    return f"≤ {fmt_s(BOUNDS[k])}"


def fmt_s(s: float) -> str:
    if s is None:
        return "n.d."
    if math.isinf(s):
        return "au delà de 2 h"
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:g} min".replace(".", ",")
    return f"{s / 3600:g} h".replace(".", ",")


def quantile(hist: list[int], q: float) -> float | None:
    """Quantile approché d'un histogramme par classes (borne haute de la classe atteinte)."""
    n = sum(hist)
    if not n:
        return None
    acc = 0
    for k, c in enumerate(hist):
        acc += c
        if acc >= q * n:
            return BOUNDS[k] if k < len(BOUNDS) else math.inf
    return math.inf


def num(x, nd=0) -> str:
    if x is None:
        return "n.d."
    if isinstance(x, float) and math.isinf(x):
        return "au delà de 2 h"
    s = f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")
    return s


class Mesures:
    def __init__(self, cell_lon: float, cell_lat: float):
        self.cell = (cell_lon, cell_lat)
        nb = len(BOUNDS) + 1
        self.by_cat = defaultdict(lambda: [0] * nb)          # (catégorie, classe AIS, en route) : histogramme
        self.by_zone = defaultdict(lambda: [0] * nb)         # (zone, en route)
        self.vessel_median = defaultdict(lambda: [0] * nb)   # catégorie : histogramme des médianes par navire
        self.cells = defaultdict(lambda: [0] * (1 + len(INTERVALS_GRID)))   # (cx, cy) : paires, paires sous chaque seuil
        self.cell_vessels = defaultdict(set)
        self.hourly = [0] * 61                                # messages par heure d'un navire en route, plafonnés à 60
        self.silences = defaultdict(lambda: defaultdict(int))  # jour : seuil (min) : silences en route, classe A
        self.positions = 0
        self.vessels = set()

    def add_day(self, d: date, rows):
        per_vessel = defaultdict(list)
        for vid, cat, zone, cls, moving, dt, lon, lat in rows:
            self.positions += 1
            self.vessels.add(vid)
            b = bucket(dt)
            self.by_cat[(cat, cls, moving)][b] += 1
            self.by_zone[(zone, moving)][b] += 1
            if moving:
                per_vessel[(vid, cat)].append(dt)
            if cls == "A" and moving:
                if dt < 7200:                     # même filtre que la zone de réception (RECEPTION_SQL)
                    key = (math.floor(lon / self.cell[0]), math.floor(lat / self.cell[1]))
                    c = self.cells[key]
                    c[0] += 1
                    for k, s in enumerate(INTERVALS_GRID):
                        if dt <= s:
                            c[k + 1] += 1
                    self.cell_vessels[key].add(vid)
                for m in (30, 60, 120, 240):
                    if dt >= 60 * m:
                        self.silences[d][m] += 1
        for (vid, cat), dts in per_vessel.items():
            if len(dts) >= 10:
                dts.sort()
                self.vessel_median[cat][bucket(dts[len(dts) // 2])] += 1

    def add_hourly(self, rows):
        for (n,) in rows:
            self.hourly[min(int(n), 60)] += 1

    def cell_area_km2(self, cy: int) -> float:
        lat = (cy + 0.5) * self.cell[1]
        return self.cell[0] * 111.32 * math.cos(math.radians(lat)) * self.cell[1] * 110.57

    def reception_grid(self, min_vessels: int) -> dict:
        """Cellules retenues et surface pour chaque combinaison (intervalle, paires, continuité)."""
        out = {}
        for k, s in enumerate(INTERVALS_GRID):
            for p in PAIRS_GRID:
                for cov in COVERAGE_GRID:
                    n = km2 = 0
                    for key, c in self.cells.items():
                        if c[0] >= p and len(self.cell_vessels[key]) >= min_vessels and c[k + 1] / c[0] >= cov:
                            n += 1
                            km2 += self.cell_area_km2(key[1])
                    out[(s, p, cov)] = (n, km2)
        return out


def measure(conn, days: int, rules: dict, log) -> tuple[Mesures, dict]:
    end = conn.execute("SELECT max(ts) FROM positions").fetchone()[0]
    if end is None:
        raise SystemExit("Aucune position en base")
    last = end.astimezone(timezone.utc).date()
    first_avail = conn.execute("SELECT min(ts) FROM positions").fetchone()[0].astimezone(timezone.utc).date()
    first = max(first_avail, last - timedelta(days=days - 1))
    z = rules["reception"]
    m = Mesures(z["cell_deg_lon"], z["cell_deg_lat"])
    sql = DAY_SQL.format(cat=CATEGORY_SQL, zone=ZONE_SQL)
    d = first
    while d <= last:
        t = time.time()
        d0 = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        params = {"d0": d0, "d1": d0 + timedelta(days=1)}
        with conn.cursor(name=f"jour_{d:%Y%m%d}") as cur:      # curseur côté serveur : lecture par paquets
            cur.itersize = 20000
            cur.execute(sql, params)
            m.add_day(d, cur)
        # Messages par heure des navires de classe A en route toute l'heure (critère min_prior_messages)
        m.add_hourly(conn.execute(
            """SELECT count(*) FROM positions p JOIN vessels v ON v.id = p.vessel_id
               WHERE p.ts >= %(d0)s AND p.ts < %(d1)s AND v.ais_class = 'A'
               GROUP BY p.vessel_id, date_trunc('hour', p.ts) HAVING min(coalesce(p.sog_kn, 0)) >= 1""",
            params).fetchall())
        log(f"{d:%d/%m/%Y} mesuré en {time.time() - t:.1f} s")
        d += timedelta(days=1)

    # Épisodes : chaque cycle des règles consigne ses comptes (fenêtre de 24 h) ; on garde le plus haut du jour
    episodes = conn.execute(
        """SELECT run_day,
                  max((details->'rendezvous'->>'episodes_detectes')::int),
                  max((details->'rendezvous'->'exclus'->>'cote')::int),
                  max((details->'rendezvous'->>'alertes')::int),
                  max((details->'ais_gap'->>'silences_detectes')::int),
                  max((details->'ais_gap'->>'exclus_sortie_de_couverture')::int),
                  max((details->'ais_gap'->>'coupures_retenues')::int),
                  max((details->'ais_gap'->>'minutes_de_flux_coupe')::int),
                  max((details->'watchlist'->>'passages')::int),
                  max((details->'identite'->>'changements')::int),
                  max((details->>'duree_s')::float8), count(*)
           FROM task_runs WHERE task = 'regles' AND status = 'ok' AND run_day BETWEEN %s AND %s
             AND jsonb_typeof(details->'rendezvous') = 'object'
           GROUP BY run_day ORDER BY run_day""", (first, last)).fetchall()
    alerts = conn.execute(
        """SELECT type, (event_time AT TIME ZONE 'UTC')::date AS jour, severity, status, count(*)
           FROM alerts WHERE event_time >= %s AND event_time < %s
           GROUP BY 1, 2, 3, 4 ORDER BY 1, 2""",
        (datetime(first.year, first.month, first.day, tzinfo=timezone.utc),
         datetime(last.year, last.month, last.day, tzinfo=timezone.utc) + timedelta(days=1))).fetchall()
    identity = conn.execute(
        """SELECT count(*) FILTER (WHERE details->>'changement' = 'nom'),
                  count(*) FILTER (WHERE details->>'changement' = 'omi_autre_mmsi'),
                  count(*) FILTER (WHERE details ? 'militaire' AND details->>'militaire' IS NOT NULL)
           FROM alerts WHERE type = 'IDENTITY_CHANGE' AND event_time >= %s""",
        (datetime(first.year, first.month, first.day, tzinfo=timezone.utc),)).fetchone()
    zones = conn.execute("SELECT count(*), coalesce(sum(vessels), 0) FROM stationary_zones").fetchone()
    viirs = measure_viirs(conn, first, last)
    reception_now = conn.execute(
        "SELECT count(*), coalesce(round((sum(ST_Area(geom)) / 1e6)::numeric), 0) FROM reception_cells").fetchone()
    return m, {"first": first, "last": last, "episodes": episodes, "alerts": alerts, "identity": identity,
               "stationary_zones": zones, "reception_now": reception_now, "viirs": viirs}


VIIRS_DIST = [500, 1000, 1500, 2000, 3000, 5000, 10000]
L_REGION = {"bretagne": "Bretagne", "manche": "Manche", "gascogne": "Gascogne", "mediterranee": "Méditerranée"}


def measure_viirs(conn, first: date, last: date) -> dict:
    """Détections VIIRS de la période : par nuit et par région (une détection du recouvrement Bretagne et Manche compte
    dans les deux), statut, luminosité ; distance au navire AIS le plus proche ; distance à la côte des sans AIS."""
    rows = conn.execute(
        """SELECT g.nuit, lower(r.name),
                  CASE WHEN d.mask_reason = 'non_evaluable' THEN 'non_evaluable' WHEN d.mask_reason IS NOT NULL THEN 'ecartee'
                       WHEN d.matched_vessel_id IS NOT NULL THEN 'avec_ais' ELSE 'sans_ais' END,
                  d.nanowatts, d.ais_proche_m, d.ais_proche_ecart_s, d.distance_cote_m
           FROM viirs_detections d JOIN viirs_granules g ON g.id = d.granule_id
           LEFT JOIN regions r ON lower(r.name) = ANY(%s) AND ST_Intersects(r.geom::geometry, d.geom::geometry)
           WHERE g.nuit BETWEEN %s AND %s""",
        (["bretagne", "manche", "gascogne", "mediterranee"], first - timedelta(days=1), last)).fetchall()
    return {"lignes": rows}


def viirs_section(x: dict, rules: dict) -> list[str]:
    """Section du rapport : répartition par nuit et par région, distances à l'AIS, tolérances, distance à la côte."""
    rows = x["viirs"]["lignes"]
    if not rows:
        return ["## 4. Détections nocturnes VIIRS", "", "Aucune détection sur la période.", ""]
    stat = ("avec_ais", "ecartee", "sans_ais", "non_evaluable")
    groups = defaultdict(lambda: defaultdict(list))
    for nuit, region, st, nw, *_ in rows:
        groups[(nuit, region or "hors région")][st].append(nw or 0)
    table_rows = []
    for (nuit, region), g in sorted(groups.items()):
        cells = [f"{len(g[s])} ({num(statistics.median(g[s]), 0)} nW)" if g[s] else "0" for s in stat]
        table_rows.append([f"{nuit:%d/%m/%Y}", L_REGION.get(region, region), *cells])
    # Une détection par ligne pour les distances (le recouvrement des régions la compterait deux fois)
    seen = {}
    for nuit, region, st, nw, dist, ecart, cote in rows:
        seen[(nuit, st, nw, dist, cote)] = (st, dist, ecart, cote)
    uniq = list(seen.values())
    evaluated = [u for u in uniq if u[0] in ("avec_ais", "sans_ais")]
    def bucket_d(d):
        if d is None:
            return "aucun navire connu"
        return next((f"≤ {num(b)} m" for b in VIIRS_DIST if d <= b), "> 10 km")
    order = [f"≤ {num(b)} m" for b in VIIRS_DIST] + ["> 10 km", "aucun navire connu"]
    dist_rows = []
    for st in ("avec_ais", "sans_ais", "non_evaluable"):
        c = Counter(bucket_d(u[1]) for u in uniq if u[0] == st)
        dist_rows.append([{"avec_ais": "Avec AIS", "sans_ais": "Sans AIS", "non_evaluable": "Non évaluables"}[st],
                          *(num(c[k]) for k in order)])
    tol_rows = [[f"{num(t)} m", pct(sum(1 for u in evaluated if u[1] is not None and u[1] <= t), len(evaluated))]
                for t in (1000, 1500, 2000, 3000, 5000)]
    far = sorted(u[3] / 1000 for u in uniq if u[0] == "sans_ais" and u[3] is not None)
    far_rows = [[f"{s} km", num(sum(1 for d in far if d >= s))] for s in (5, 12, 22, 30, 50)]
    v = rules["viirs"]
    return ["## 4. Détections nocturnes VIIRS", "",
            "Par nuit et par région : nombre de détections et luminosité médiane (nW/cm²/sr) selon le statut. Non "
            "évaluable : coupure du flux AIS ou aucune réception AIS autour (aucune alerte).", "",
            table(["Nuit", "Région", "Avec AIS", "Écartées", "Sans AIS", "Non évaluables"], table_rows), "",
            "Distance au navire AIS le plus proche à l'heure du passage (position interpolée, ou estimée sur "
            f"{v['estime_max_min']} minutes au plus) :", "",
            table(["Statut", *order], dist_rows), "",
            f"Part des détections évaluées appariables selon la tolérance (actuelle : {v['tolerance_m']} m) :", "",
            table(["Tolérance", "Appariées"], tol_rows), "",
            f"Détections sans AIS selon la distance à la côte (seuil « très au large » actuel : {v['tres_au_large_km']} km), "
            f"sur {len(far)} :", "",
            table(["Au delà de", "Détections"], far_rows), ""]


# Rapport

def table(head: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def pct(n, total) -> str:
    return "n.d." if not total else f"{100 * n / total:.0f} %".replace(".", ",")


def report(m: Mesures, x: dict, rules: dict) -> str:
    z = rules["reception"]
    first, last = x["first"], x["last"]
    days = (last - first).days + 1
    L = ["# Mesures pour la recalibration des règles",
         "",
         f"Rapport produit par `scripts/mesures_calibration.py` le "
         f"{datetime.now(timezone.utc):%d/%m/%Y à %H:%M} UTC, règles version {rules['version']}. Données : "
         f"{num(m.positions)} intervalles entre positions de {num(len(m.vessels))} navires, du {first:%d/%m/%Y} au "
         f"{last:%d/%m/%Y} ({days} jour{'s' if days > 1 else ''}). Aucun seuil n'est modifié : les propositions "
         "sont à valider une par une, puis à reporter dans `config/rules.yaml` en montant la version.",
         "",
         "Les intervalles sont mesurés sur les positions en base, après allègement (un point par minute en route, "
         "un toutes les dix minutes à l'arrêt) : ce sont les données que voient les règles. Un intervalle de "
         "60 s en route est donc un message reçu à chaque minute, pas la cadence réelle de l'émetteur (2 à 10 s "
         "pour la classe A en route). Les intervalles qui chevauchent minuit ne sont pas comptés.",
         ""]

    # 1. Intervalles
    L += ["## 1. Intervalles entre messages", "", "### Par type de navire (en route, au moins 1 nœud)", ""]
    rows = []
    for cat in CATEGORIES:
        for cls in ("A", "B"):
            h = m.by_cat.get((cat, cls, True))
            if not h or sum(h) < 50:
                continue
            n = sum(h)
            rows.append([cat, cls, num(n), fmt_s(quantile(h, 0.5)), fmt_s(quantile(h, 0.9)),
                         fmt_s(quantile(h, 0.95)), pct(sum(h[:BOUNDS.index(180) + 1]), n),
                         pct(sum(h[BOUNDS.index(5400) + 1:]), n)])
    L += [table(["Type", "Classe", "Intervalles", "Médiane", "9e décile", "95e centile", "Sous 3 min",
                 "Au delà de 1,5 h"], rows), ""]
    L += ["### Par navire : médiane de chaque navire en route (10 intervalles au moins)", ""]
    rows = []
    for cat in CATEGORIES:
        h = m.vessel_median.get(cat)
        if not h or not sum(h):
            continue
        rows.append([cat, num(sum(h)), fmt_s(quantile(h, 0.1)), fmt_s(quantile(h, 0.5)), fmt_s(quantile(h, 0.9))])
    L += [table(["Type", "Navires", "1er décile des médianes", "Médiane des médianes", "9e décile des médianes"],
                rows), ""]
    L += ["### Par zone", ""]
    rows = []
    for zone in list(ZONES) + ["hors zone"]:
        for moving, lib in ((True, "en route"), (False, "à l'arrêt")):
            h = m.by_zone.get((zone, moving))
            if not h or not sum(h):
                continue
            n = sum(h)
            rows.append([zone, lib, num(n), fmt_s(quantile(h, 0.5)), fmt_s(quantile(h, 0.9)), fmt_s(quantile(h, 0.99)),
                         pct(sum(h[:BOUNDS.index(180) + 1]), n)])
    L += [table(["Zone", "État", "Intervalles", "Médiane", "9e décile", "99e centile", "Sous 3 min"], rows), ""]
    L += ["Distribution complète, classe A en route, tous types :", ""]
    tot = [sum(m.by_cat[k][i] for k in m.by_cat if k[1] == "A" and k[2]) for i in range(len(BOUNDS) + 1)]
    n_tot = sum(tot)
    acc, rows = 0, []
    for i, c in enumerate(tot):
        acc += c
        if c:
            rows.append([label(i), num(c), pct(c, n_tot), pct(acc, n_tot)])
    L += [table(["Intervalle", "Nombre", "Part", "Cumul"], rows), ""]

    # 2. Réception
    grid = m.reception_grid(z["min_vessels"])
    cur_key = (z["max_interval_s"], z["min_pairs"], z["min_coverage"])
    L += ["## 2. Zone de réception fiable", "",
          f"Cellules de {z['cell_deg_lon']} × {z['cell_deg_lat']} degrés ; paires d'intervalles successifs d'un "
          f"navire de classe A en route, sous 2 h ; {z['min_vessels']} navires distincts au moins par cellule "
          f"(sur toute la période). Zone actuellement en base : {num(x['reception_now'][0])} cellules, "
          f"{num(float(x['reception_now'][1]))} km².", ""]
    pairs = sorted(c[0] for c in m.cells.values())
    if pairs:
        def qp(q):
            return pairs[min(len(pairs) - 1, int(q * len(pairs)))]
        L += [f"{num(len(pairs))} cellules traversées ; paires par cellule : médiane {num(qp(0.5))}, 1er quartile "
              f"{num(qp(0.25))}, 3e quartile {num(qp(0.75))}, 9e décile {num(qp(0.9))}. "
              f"{pct(sum(1 for p in pairs if p >= z['min_pairs']), len(pairs))} des cellules atteignent "
              f"{z['min_pairs']} paires.", ""]
    for s in INTERVALS_GRID:
        rows = []
        for p in PAIRS_GRID:
            row = [f"{p}"]
            for cov in COVERAGE_GRID:
                n, km2 = grid[(s, p, cov)]
                mark = " (actuel)" if (s, p, cov) == cur_key else ""
                row.append(f"{num(n)} cellules, {num(km2)} km²{mark}")
            rows.append(row)
        L += [f"Intervalle jugé normal : {fmt_s(s)}" + (" (actuel)" if s == z["max_interval_s"] else ""), "",
              table(["Paires au moins"] + [f"Continuité {num(100 * c)} %" for c in COVERAGE_GRID], rows), ""]

    # 3. Épisodes et alertes
    L += ["## 3. Épisodes et alertes par règle et par jour", "",
          "Épisodes : comptes du cycle des règles le plus chargé de la journée (fenêtre glissante de 24 h, donc une "
          "même rencontre est vue par plusieurs cycles) ; journal `task_runs`.", ""]
    rows = [[f"{r[0]:%d/%m/%Y}", num(r[1]), num(r[2]), num(r[3]), num(r[4]), num(r[5]), num(r[6]), num(r[7]),
             num(r[8]), num(r[9]), num(r[10], 1) + " s", num(r[11])] for r in x["episodes"]]
    L += [table(["Jour", "Rendez vous détectés", "dont côtiers", "Rendez vous retenus", "Silences détectés",
                 "Sorties de couverture", "Coupures retenues", "Minutes de flux coupé", "Passages des listes",
                 "Changements d'identité", "Cycle le plus long", "Cycles"], rows) if rows
          else "Aucun cycle des règles consigné sur la période.", ""]
    by = defaultdict(lambda: defaultdict(int))
    sev = defaultdict(lambda: defaultdict(int))
    for typ, jour, severity, status, n in x["alerts"]:
        by[typ][jour] += n
        sev[typ][severity] += n
        if status == "classee":
            sev[typ]["classées"] += n
    jours = sorted({j for t in by.values() for j in t})
    rows = [[t] + [num(by[t].get(j, 0)) for j in jours] + [num(sum(by[t].values())),
            ", ".join(f"{k} {v}" for k, v in sorted(sev[t].items()))] for t in sorted(by)]
    L += ["Alertes par type et par jour de l'événement :", "",
          table(["Type"] + [f"{j:%d/%m}" for j in jours] + ["Total", "Gravité et décisions"], rows) if rows
          else "Aucune alerte sur la période.", ""]

    # 4. Seuils
    L += viirs_section(x, rules)
    L += ["## 5. Seuils de config/rules.yaml : valeur, mesure, proposition", ""]
    L += [table(["Seuil", "Valeur", "Ce que montrent les mesures", "Proposition"], proposals(m, x, rules, grid)), ""]
    return "\n".join(L) + "\n"


def proposals(m: Mesures, x: dict, rules: dict, grid: dict) -> list[list]:
    z, g, rv = rules["reception"], rules["ais_gap"], rules["rendezvous"]
    out = []
    days = max(1, (x["last"] - x["first"]).days + 1)
    hA = [sum(m.by_cat[k][i] for k in m.by_cat if k[1] == "A" and k[2]) for i in range(len(BOUNDS) + 1)]
    nA = sum(hA)

    # Réception
    s_cur, p_cur, c_cur = z["max_interval_s"], z["min_pairs"], z["min_coverage"]
    n_cur, km_cur = grid.get((s_cur, p_cur, c_cur), (0, 0)) if s_cur in INTERVALS_GRID else (None, None)
    p90 = quantile(hA, 0.9)
    s_prop = next((s for s in INTERVALS_GRID if p90 is not None and s >= p90), INTERVALS_GRID[-1])
    out.append(["reception.max_interval_s", f"{s_cur} s",
                f"Classe A en route : 9e décile des intervalles {fmt_s(p90)}, {pct(sum(hA[:BOUNDS.index(180) + 1]), nA)} "
                "sous 3 min.",
                "Garder" if s_prop <= s_cur else
                f"Porter à {s_prop} s : un intervalle normal doit couvrir 9 intervalles sur 10 d'un navire bien reçu, "
                "sinon la continuité mesure l'allègement plutôt que la réception"])
    # min_pairs : plancher statistique (écart type de la continuité sous 3 points à 50 paires) et gain de surface
    p_prop = p_cur
    for p in sorted(PAIRS_GRID):
        if p >= 50 and s_cur in INTERVALS_GRID:
            n, km = grid[(s_cur, p, c_cur)]
            if km_cur and km >= 1.2 * km_cur:
                p_prop = p
                break
    gains = ", ".join(f"{p} paires {num(grid[(s_cur, p, c_cur)][1])} km²" for p in PAIRS_GRID) \
        if s_cur in INTERVALS_GRID else "n.d."
    out.append(["reception.min_pairs", str(p_cur), f"Surface fiable selon le minimum de paires : {gains}.",
                "Garder pour l'instant : avec la continuité et l'intervalle actuels, aucune cellule ne passe, quel que "
                "soit le nombre de paires ; régler d'abord max_interval_s, puis revoir ce seuil (tableaux de la "
                "section 2)" if not km_cur and not any(grid[(s_cur, p, c_cur)][1] for p in PAIRS_GRID)
                else "Garder" if p_prop == p_cur else
                f"Abaisser à {p_prop} : au moins 20 % de surface en plus, et à 50 paires l'écart type d'une "
                "continuité de 95 % reste sous 3 points (valeur danoise de 200 calibrée sur un AIS non allégé)"])
    covs = []
    for key, c in m.cells.items():
        if c[0] >= p_cur and len(m.cell_vessels[key]) >= z["min_vessels"] and s_cur in INTERVALS_GRID:
            covs.append(c[INTERVALS_GRID.index(s_cur) + 1] / c[0])
    covs.sort()
    q25 = covs[len(covs) // 4] if covs else None
    c_prop = c_cur if q25 is None or q25 >= c_cur else max(0.80, math.floor(q25 * 20) / 20)
    out.append(["reception.min_coverage", f"{c_cur}",
                f"Continuité des cellules assez fréquentées : 1er quartile {num(q25, 3) if q25 is not None else 'n.d.'}, "
                f"médiane {num(covs[len(covs) // 2], 3) if covs else 'n.d.'} ({len(covs)} cellules).",
                "Garder" if c_prop == c_cur else
                f"Abaisser à {num(c_prop, 2)}, plancher retenu : même ainsi, moins de trois cellules fréquentées sur "
                "quatre passent ; l'écart vient de l'intervalle jugé normal, à régler d'abord (max_interval_s)"
                if q25 < 0.80 else
                f"Abaisser à {num(c_prop, 2)} : trois cellules fréquentées sur quatre y satisfont ; à éprouver par le test par "
                "injection (5 sur 5 attendu)"])
    out.append(["reception.min_vessels", str(z["min_vessels"]),
                f"{sum(1 for k, c in m.cells.items() if c[0] >= p_cur and len(m.cell_vessels[k]) < z['min_vessels'])}"
                " cellules ont assez de paires mais moins de navires.", "Garder : protège contre un seul navire "
                "bien reçu qui ferait passer une cellule pour fiable"])
    out.append(["reception.cell_deg_lon, cell_deg_lat", f"{z['cell_deg_lon']} × {z['cell_deg_lat']}",
                f"{num(len(m.cells))} cellules traversées.", "Garder : la taille fixe la finesse de la zone, à revoir "
                "seulement si la carte de réception paraît trop morcelée"])

    # Coupure AIS
    h = m.hourly
    nh = sum(h)

    def hq(q):
        acc = 0
        for k, c in enumerate(h):
            acc += c
            if acc >= q * nh:
                return k
        return None
    p10 = hq(0.1) if nh else None
    out.append(["ais_gap.min_prior_messages (sur prior_window_min)", f"{g['min_prior_messages']} en {g['prior_window_min']} min",
                f"Positions par heure d'un navire de classe A en route toute l'heure : 1er décile {num(p10)}, "
                f"médiane {num(hq(0.5) if nh else None)} ({num(nh)} heures navire).",
                "Garder" if p10 is None or p10 >= g["min_prior_messages"] else
                f"Abaisser à {max(3, p10)} : 1 heure navire sur 10 n'atteint pas le seuil, et la coupure de ces navires "
                "ne serait jamais vue"])
    sil = {mn: sum(v.get(mn, 0) for v in m.silences.values()) / days for mn in (30, 60, 120, 240)}
    out.append(["ais_gap.min_gap_min", str(g["min_gap_min"]),
                "Silences par jour, classe A en route au dernier message, d'au moins : " +
                ", ".join(f"{fmt_s(60 * k)} {num(v, 1)}" for k, v in sil.items()) + ".",
                "Garder : deux heures restent bien au delà des intervalles normaux (section 1) ; le nombre de "
                "silences retenus se règle par les filtres (sortie de couverture, flux coupé), pas par la durée"])
    ep = x["episodes"]
    if ep:
        kept = [r[6] or 0 for r in ep]
        out.append(["ais_gap (ensemble des filtres)", "voir config",
                    f"Coupures retenues par jour : de {min(kept)} à {max(kept)} ; sorties de couverture écartées : "
                    f"jusqu'à {max(r[5] or 0 for r in ep)}.",
                    "Garder ; examiner au cas par cas chaque coupure retenue avant de toucher aux seuils"])
    for k in ("min_speed_kn", "min_coast_km", "edge_margin_deg", "partner_radius_m", "partner_min_slow_min",
              "projection_min", "stop_ratio", "continue_ratio", "high_duration_min"):
        out.append([f"ais_gap.{k}", str(g[k]), "Pas de mesure directe dans ce rapport.",
                    "Garder : à juger sur les coupures retenues, une par une"])

    # Rendez vous
    if ep:
        det = [r[1] or 0 for r in ep]
        cote = [r[2] or 0 for r in ep]
        ret = [r[3] or 0 for r in ep]
        dur = [r[10] or 0 for r in ep]
        msg = (f"Épisodes par jour : {min(det)} à {max(det)}, dont côtiers {min(cote)} à {max(cote)} ; alertes "
               f"retenues {min(ret)} à {max(ret)} ; cycle le plus long {num(max(dur), 1)} s.")
    else:
        msg = "Aucun cycle consigné."
    out.append(["rendezvous.min_coast_km", str(rv["min_coast_km"]), msg,
                "Garder : presque tous les épisodes sont des navires à quai ou au mouillage près des côtes, écartés "
                "à juste titre ; les positions à moins de 1 km de la terre ne sont plus appariées (coast_prefilter_m)"])
    for k in ("max_distance_m", "max_speed_kn", "min_duration_min", "slot_min", "max_gap_min", "alongside_m",
              "high_duration_min", "high_coast_km", "stationary_zone_buffer_m"):
        out.append([f"rendezvous.{k}", str(rv[k]), "Voir les alertes par jour (section 3).",
                    "Garder tant que le nombre d'alertes reste examinable une par une"])
    sz = x["stationary_zones"]
    out.append(["stationary_zones (cellules, 4 navires)", f"{rules['stationary_zones']['min_vessels']} navires",
                f"{num(sz[0])} zones de mouillage en base.", "Garder ; reconstruire les zones sur 7 jours au moins"])

    # Identité, listes, ingestion
    idn = x["identity"]
    out.append(["identite.confirmation_min", str(rules["identite"]["confirmation_min"]),
                f"Alertes de changement d'identité sur la période : {idn[0]} de nom, {idn[1]} d'OMI, dont {idn[2]} "
                "bâtiments militaires.", "Garder"])
    out.append(["watchlist.passage_gap_h", str(rules["watchlist"]["passage_gap_h"]),
                "Voir les alertes WATCHLIST par jour.", "Garder"])
    ing = rules["ingestion"]
    hm = [sum(m.by_cat[k][i] for k in m.by_cat if k[2]) for i in range(len(BOUNDS) + 1)]
    out.append(["ingestion.moving_interval_s, stopped_interval_s", f"{ing['moving_interval_s']} s, {ing['stopped_interval_s']} s",
                f"Médiane en route {fmt_s(quantile(hm, 0.5))}.", "Garder : paramètres d'allègement, pas de calibration"])

    # Radar : pas de mesure AIS
    for k in ("model.thresholds", "contrast.min_vv_db", "masks.land_buffer_m", "fusion", "persistence",
              "unconfirmed", "dark_ship"):
        out.append([k, "voir config", "Aucune mesure AIS : seuils du radar.",
                    "Garder ; à évaluer à l'étape 3 sur des passages Sentinel 1 français"])
    return out


def main(days: int = 14, out: str | None = None):
    load_env()
    rules = load_rules()

    def log(msg):
        print(msg, file=sys.stderr, flush=True)

    with psycopg.connect(database_url()) as conn:
        conn.execute("SET statement_timeout = '15min'")
        m, x = measure(conn, days, rules, log)
    text = report(m, x, rules)
    if out:
        Path(out).write_text(text)
        log(f"Rapport écrit dans {out}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Mesures pour la recalibration des règles")
    ap.add_argument("--jours", type=int, default=14)
    ap.add_argument("--sortie", help="fichier Markdown (par défaut : sortie standard)")
    a = ap.parse_args()
    main(a.jours, a.sortie)
