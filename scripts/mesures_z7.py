"""Mesures du lot Z7 (calibration, 27/10/2026) : ce que scripts/mesures_calibration.py ne mesure pas encore. Rien n'est
écrit : la connexion est ouverte en lecture seule (default_transaction_read_only), aucune table temporaire, aucune
règle changée. Écrit un rapport Markdown (et ses chiffres en JSON) au nom horodaté.

  1. Trous du flux AIS de la période (stats_minute) et journées exclues (le trou du 18/10 par défaut).
  2. Coupures AIS observées depuis le 11/10 (règle en observation) : chaque épisode (table episodes_observes, complétée
     par le journal des cycles) recalculé depuis les positions : région, durée jusqu'à la réapparition, type de
     navire, distance à la côte et aux infrastructures, gravité hypothétique ; sorties de couverture et points de
     disparition collectifs par jour (journal des cycles).
  3. Zone de réception fiable recalculée sur la période avec le réglage en service et quelques variantes, comparée aux
     cellules en service (gagnées, perdues, par région).
  4. Rendez vous au large : la requête de la règle, par jour, avec les seuils danois (500 m, 120 min) et deux
     variantes ; distances et durées des paires, épisodes par jour, exemples.
  5. Zones d'arrêt (masques) recalculées sur la période, comparées à la table en service.
  6. VIIRS sous la lune actuelle : par nuit, lune, granules non évaluables, détections et alertes ; ce qui sera à
     refaire après le 29/10.

Le test par injection des coupures (efface des positions dans une transaction annulée) n'est pas ici : il n'est pas en
lecture seule ; il se lance à part (taches.py coupures-injection), voir docs/calibration.md.

Usage, sur le serveur, dans un conteneur à part (jamais dans celui en service) :
    TACHES_MEM_LIMIT=1200m docker compose run --rm --no-deps -v /tmp/z7:/sortie taches \\
        python scripts/mesures_z7.py --jours 14 --exclure-jour 2026-10-18 --sortie /sortie
"""
import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from mars.ais.live import ZONES  # noqa: E402
from mars.config import database_url, load_env, load_rules  # noqa: E402
from mars.rules import RENDEZVOUS_SQL, RECEPTION_SQL, STATIONARY_ZONES_SQL  # noqa: E402

ORDRE_REGIONS = ("mediterranee", "gascogne", "bretagne", "manche")      # Bretagne et Manche se chevauchent
NOMS = {"mediterranee": "Méditerranée", "gascogne": "Gascogne", "bretagne": "Bretagne", "manche": "Manche"}


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def region(lon: float, lat: float) -> str:
    for k in ORDRE_REGIONS:
        a, b, c, d = ZONES[k]
        if a <= lat <= c and b <= lon <= d:
            return k
    return "hors zone"


def categorie(code) -> str:
    if code is None:
        return "Inconnu"
    if 70 <= code <= 79:
        return "Cargo"
    if 80 <= code <= 89:
        return "Pétrolier"
    if 60 <= code <= 69:
        return "Passagers"
    if code == 30:
        return "Pêche"
    if code in (36, 37):
        return "Plaisance et voile"
    if code in (31, 32, 33, 50, 51, 52, 53, 54, 55, 58):
        return "Remorquage et service"
    return "Autre"


def classe(x, bornes: list, unite: str) -> str:
    if x is None:
        return f"au delà de {bornes[-1]} {unite}"
    for b in bornes:
        if x < b:
            return f"moins de {b} {unite}"
    return f"au delà de {bornes[-1]} {unite}"


def table(head: list, rows: list) -> str:
    out = ["| " + " | ".join(str(h) for h in head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def pct(n, tot) -> str:
    return f"{100 * n / tot:.0f} %" if tot else "·"


def q(xs: list, f: float):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(f * len(xs)))] if xs else None


def jours_periode(first: date, last: date, exclus: set) -> list[date]:
    out, d = [], first
    while d <= last:
        if d not in exclus:
            out.append(d)
        d += timedelta(days=1)
    return out


def t0(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


# Distances par les plus proches voisins (index spatial), jamais par un grand rayon sur la table des terres
COTE_SQL = """SELECT (SELECT min(ST_Distance(l.geom, g)) FROM (SELECT geom FROM land ORDER BY geom <-> g LIMIT 16) l),
                     (SELECT min(ST_Distance(i.geom, g)) FROM (SELECT geom FROM infrastructure ORDER BY geom <-> g
                                                               LIMIT 8) i)
              FROM (SELECT ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography AS g) x"""


# 1. Trous du flux

async def trous_flux(c, debut: datetime, fin: datetime, ratio: float) -> list[dict]:
    """Minutes sous `ratio` fois la médiane du jour, regroupées en trous d'au moins 30 minutes."""
    rows = await c.fetch("SELECT minute, positions FROM stats_minute WHERE minute >= $1 AND minute < $2 ORDER BY minute",
                         debut, fin)
    par_jour = defaultdict(list)
    for r in rows:
        par_jour[r["minute"].date()].append(r["positions"])
    med = {j: statistics.median(v) for j, v in par_jour.items() if v}
    comptes = {r["minute"]: r["positions"] for r in rows}
    trous, cur = [], None
    m = debut.replace(second=0, microsecond=0)
    while m < fin:
        bas = comptes.get(m, 0) < ratio * med.get(m.date(), 0) if m.date() in med else False
        if bas and cur is None:
            cur = m
        elif not bas and cur is not None:
            if (m - cur) >= timedelta(minutes=30):
                trous.append({"debut": cur, "fin": m, "minutes": int((m - cur).total_seconds() / 60)})
            cur = None
        m += timedelta(minutes=1)
    return trous


# 2. Coupures AIS observées

async def coupures(c, depuis: datetime, fin: datetime, exclus: set, rules: dict) -> dict:
    cles = {}
    for r in await c.fetch("SELECT cle, gravite, premiere_le FROM episodes_observes WHERE regle = 'ais_gap' "
                           "AND premiere_le >= $1", depuis):
        cles[r["cle"]] = {"gravite": r["gravite"], "source": "episodes_observes"}
    journal = await c.fetch(
        """SELECT run_day, details->'ais_gap' AS g FROM task_runs WHERE task = 'regles' AND status = 'ok'
           AND started_at >= $1 AND details ? 'ais_gap'""", depuis)
    par_jour = defaultdict(lambda: {"sorties_couverture": 0, "declassees_collectives": 0, "points_collectifs": 0,
                                    "silences": 0, "retenues": 0})
    collectifs = set()
    for r in journal:
        g = json.loads(r["g"]) if isinstance(r["g"], str) else r["g"]
        if not isinstance(g, dict):
            continue
        j = par_jour[r["run_day"]]
        for k, champ in (("sorties_couverture", "exclus_route_hors_zone"),
                         ("declassees_collectives", "coupures_declassees_disparition_collective"),
                         ("points_collectifs", "points_de_disparition_collectifs"),
                         ("silences", "silences_detectes"), ("retenues", "coupures_retenues")):
            j[k] = max(j[k], int(g.get(champ) or 0))
        for ep in g.get("episodes") or []:
            if not ep.get("vessel_id") or not ep.get("dernier_message"):
                continue
            t = datetime.fromisoformat(ep["dernier_message"])
            cle = f"{ep['vessel_id']}:{t.astimezone(timezone.utc):%Y%m%dT%H%M%S}"
            cles.setdefault(cle, {"gravite": ep.get("gravite"), "source": "journal"})
            if ep.get("point_collectif"):
                collectifs.add(cle)
    log(f"coupures : {len(cles)} épisode(s) à recalculer")
    g = rules["ais_gap"]
    eps = []
    for k, (cle, meta) in enumerate(sorted(cles.items())):
        try:
            vid, stamp = cle.split(":")
            tl = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if tl.date() in exclus:
            continue
        p = await c.fetchrow(
            """SELECT ST_X(p.geom::geometry) AS lon, ST_Y(p.geom::geometry) AS lat, p.sog_kn, v.ship_type_code, v.name,
                      v.mmsi, v.length_m,
                      r.ts AS retour, ST_Distance(p.geom::geography, r.geom::geography) AS deplacement_m
               FROM positions p JOIN vessels v ON v.id = p.vessel_id
               LEFT JOIN LATERAL (SELECT ts, geom FROM positions q WHERE q.vessel_id = p.vessel_id AND q.ts > p.ts
                                  ORDER BY q.ts LIMIT 1) r ON true
               WHERE p.vessel_id = $1 AND p.ts < $2::timestamptz + interval '1 second'
               ORDER BY p.ts DESC LIMIT 1""", int(vid), tl)
        # La clé tronque l'heure à la seconde, la position garde ses fractions : sans la seconde de marge, on prenait la
        # position d'avant, et la « réapparition » était le dernier message lui même (durée nulle, rapport du 25/10)
        if p is None:
            continue
        cote, infra = await c.fetchrow(COTE_SQL, p["lon"], p["lat"])
        duree = (p["retour"] - tl).total_seconds() / 60 if p["retour"] else None
        vitesse = (p["deplacement_m"] / 1852) / (duree / 60) if duree and p["deplacement_m"] is not None else None
        ratio = vitesse / p["sog_kn"] if vitesse is not None and p["sog_kn"] else None
        eps.append({"cle": cle, "vessel_id": int(vid), "name": p["name"], "mmsi": p["mmsi"], "t": tl.isoformat(),
                    "region": region(p["lon"], p["lat"]), "lon": round(p["lon"], 4), "lat": round(p["lat"], 4),
                    "sog_kn": p["sog_kn"], "categorie": categorie(p["ship_type_code"]), "longueur_m": p["length_m"],
                    "duree_min": None if duree is None else round(duree), "reapparu": p["retour"] is not None,
                    "cote_m": None if cote is None else round(cote), "infra_m": None if infra is None else round(infra),
                    "gravite": meta["gravite"], "point_collectif": cle in collectifs,
                    "deplacement_km": None if p["deplacement_m"] is None else round(p["deplacement_m"] / 1000, 1),
                    "vitesse_implicite_kn": None if vitesse is None else round(vitesse, 1),
                    "conduite": None if ratio is None else "arret" if ratio < g["stop_ratio"] and p["sog_kn"] >=
                    g["stop_min_speed_kn"] else "route_poursuivie" if ratio > g["continue_ratio"] else "intermediaire"})
        if k % 200 == 199:
            log(f"  {k + 1} épisodes recalculés")
    jours = {str(j): v for j, v in sorted(par_jour.items()) if j not in exclus}
    return {"episodes": eps, "par_jour": jours, "jours_exclus": sorted(str(j) for j in exclus)}


def section_coupures(x: dict, rules: dict) -> list[str]:
    eps = x["episodes"]
    n = len(eps)
    g = rules["ais_gap"]
    L = ["## 2. Coupures AIS observées (règle en observation)", "",
         f"{n} épisodes distincts depuis le {x['depuis']} (table episodes_observes, complétée par le journal des cycles "
         f"pour les jours d'avant), journées exclues : {', '.join(x['jours_exclus']) or 'aucune'}. Durée : jusqu'à la "
         "position suivante du navire (non réapparu : au delà de la période). Seuils en vigueur : silence d'au moins "
         f"{g['min_gap_min']} min, en route à {g['min_speed_kn']} nœud au moins. Région : première zone collectée qui "
         "contient la dernière position (Bretagne avant Manche, qui se chevauchent).", ""]
    if not n:
        return L + ["Aucun épisode.", ""]

    def repart(cle, ordre=None):
        cnt = Counter(e[cle] for e in eps)
        keys = ordre or [k for k, _ in cnt.most_common()]
        return [[k, cnt.get(k, 0), pct(cnt.get(k, 0), n)] for k in keys if cnt.get(k, 0)]
    L += ["### Par région", "", table(["Région", "Épisodes", "Part"], [[NOMS.get(r[0], r[0])] + r[1:]
                                                                        for r in repart("region")]), ""]
    durees = ["moins de 180 min", "moins de 360 min", "moins de 720 min", "moins de 1440 min", "au delà de 1440 min"]
    for e in eps:
        e["classe_duree"] = classe(e["duree_min"], [180, 360, 720, 1440], "min") if e["reapparu"] else "non réapparu"
    L += ["### Par durée du silence", "", table(["Durée", "Épisodes", "Part"],
                                               repart("classe_duree", durees + ["non réapparu"])), ""]
    L += ["### Par type de navire", "", table(["Type", "Épisodes", "Part"], repart("categorie")), ""]
    for e in eps:
        e["classe_cote"] = classe(e["cote_m"] / 1000 if e["cote_m"] is not None else None, [2, 5, 12, 22, 50], "km")
        e["classe_infra"] = classe(e["infra_m"] / 1000 if e["infra_m"] is not None else None, [1, 3.7, 10, 30], "km")
    L += ["### Distance à la côte de la dernière position", "",
          table(["Distance", "Épisodes", "Part"], repart("classe_cote", [f"moins de {b} km" for b in (2, 5, 12, 22, 50)]
                                                         + ["au delà de 50 km"])), ""]
    L += ["### Distance à l'infrastructure la plus proche (corridor : 3,7 km)", "",
          table(["Distance", "Épisodes", "Part"], repart("classe_infra", [f"moins de {b} km" for b in (1, 3.7, 10, 30)]
                                                          + ["au delà de 30 km"])), ""]
    L += ["### Gravité qu'aurait eue l'alerte", "", table(["Gravité", "Épisodes", "Part"], repart("gravite")), ""]
    for e in eps:
        e["conduite"] = e.get("conduite") or ("non réapparu" if not e["reapparu"] else "inconnue")
    L += ["### Conduite pendant le silence (vitesse implicite contre vitesse déclarée)", "",
          table(["Conduite", "Épisodes", "Part"], repart("conduite")), ""]
    croise = Counter((e["gravite"], e["classe_duree"], "collectif" if e["point_collectif"] else "isolé") for e in eps)
    L += ["### Gravité selon la durée et le point de disparition", "",
          table(["Gravité", "Durée", "Point", "Épisodes"], [[*k, v] for k, v in sorted(croise.items())]), ""]
    jours_ep = Counter(e["t"][:10] for e in eps)
    L += ["### Épisodes par jour du dernier message", "",
          table(["Jour", "Épisodes", "dont élevée", "dont moyenne", "dont isolés"],
                [[j, v, sum(1 for e in eps if e["t"][:10] == j and e["gravite"] == "elevee"),
                  sum(1 for e in eps if e["t"][:10] == j and e["gravite"] == "moyenne"),
                  sum(1 for e in eps if e["t"][:10] == j and not e["point_collectif"])]
                 for j, v in sorted(jours_ep.items())]), ""]
    hautes = [e for e in eps if e["gravite"] in ("elevee", "moyenne")]
    L += [f"### Épisodes de gravité élevée ou moyenne : {len(hautes)}", "",
          table(["Navire", "MMSI", "Type", "Dernier message (UTC)", "Position", "Durée", "Déplacement",
                 "Vitesse implicite", "Conduite", "Côte", "Infrastructure", "Gravité", "Collectif"],
                [[e["name"], e["mmsi"], e["categorie"], e["t"][:16].replace("T", " "), f"{e['lat']:.3f}, {e['lon']:.3f}",
                  f"{e['duree_min']} min" if e["reapparu"] else "non réapparu",
                  "·" if e.get("deplacement_km") is None else f"{e['deplacement_km']} km",
                  "·" if e.get("vitesse_implicite_kn") is None else f"{e['vitesse_implicite_kn']} nd ({e['sog_kn']:.1f})",
                  e["conduite"], "·" if e["cote_m"] is None else f"{e['cote_m'] / 1000:.1f} km",
                  "·" if e["infra_m"] is None else f"{e['infra_m']} m", e["gravite"],
                  "oui" if e["point_collectif"] else "non"]
                 for e in sorted(hautes, key=lambda e: (e["gravite"] != "elevee", -(e["duree_min"] or 10 ** 6)))[:40]]),
          ""]
    coll = sum(e["point_collectif"] for e in eps)
    L += [f"Points de disparition collectifs parmi ces épisodes : {coll} ({pct(coll, n)}), d'après le journal des "
          "cycles (30 épisodes les plus longs par cycle : borne basse).", ""]
    jr = x["par_jour"]
    L += ["### Par jour (cycle le plus chargé du jour, journal des règles)", "",
          table(["Jour", "Silences détectés", "Retenus", "Sorties de couverture écartées", "Déclassés (collectifs)",
                 "Points collectifs"],
                [[j, v["silences"], v["retenues"], v["sorties_couverture"], v["declassees_collectives"],
                  v["points_collectifs"]] for j, v in jr.items()]), ""]
    corr = [e for e in eps if e["infra_m"] is not None and e["infra_m"] <= 3704 and (e["cote_m"] or 0) >= 2000]
    L += [f"### Exemples : corridor (moins de 3,7 km d'une infrastructure) et au large (plus de 2 km de la côte) : "
          f"{len(corr)} épisodes", "",
          table(["Navire", "MMSI", "Type", "Région", "Dernier message (UTC)", "Durée", "Côte", "Infrastructure", "Gravité"],
                [[e["name"], e["mmsi"], e["categorie"], NOMS.get(e["region"], e["region"]), e["t"][:16].replace("T", " "),
                  f"{e['duree_min']} min" if e["reapparu"] else "non réapparu", f"{e['cote_m'] / 1000:.1f} km"
                  if e["cote_m"] is not None else "·", f"{e['infra_m']} m", e["gravite"]]
                 for e in sorted(corr, key=lambda e: -(e["duree_min"] or 10 ** 6))[:15]]), ""]
    return L


# 3. Zone de réception fiable recalculée

VARIANTES_RECEPTION = [("en service", 600, 0.90, 50), ("300 s", 300, 0.90, 50), ("900 s", 900, 0.90, 50),
                       ("95 %", 600, 0.95, 50), ("100 paires", 600, 0.90, 100), ("200 paires", 600, 0.90, 200)]

RECEPTION_JOUR_SQL = """
SELECT cx, cy, count(*) AS n, count(*) FILTER (WHERE dt <= 300) AS ok300, count(*) FILTER (WHERE dt <= 600) AS ok600,
       count(*) FILTER (WHERE dt <= 900) AS ok900, array_agg(DISTINCT vessel_id) AS navires
FROM (
    SELECT p.vessel_id, p.sog_kn,
           floor(ST_X(p.geom::geometry) / $1::float8)::int AS cx, floor(ST_Y(p.geom::geometry) / $2::float8)::int AS cy,
           extract(epoch FROM lead(p.ts) OVER (PARTITION BY p.vessel_id ORDER BY p.ts) - p.ts) AS dt
    FROM positions p JOIN vessels v ON v.id = p.vessel_id
    WHERE v.ais_class = 'A' AND p.ts >= $3 AND p.ts < $4
) s
WHERE sog_kn >= 1 AND dt IS NOT NULL AND dt < 7200
GROUP BY cx, cy"""


async def reception(c, jours: list[date], rules: dict) -> dict:
    z = rules["reception"]
    assert "lead(p.ts) OVER (PARTITION BY p.vessel_id ORDER BY p.ts) - p.ts" in RECEPTION_SQL   # même définition
    cells: dict = defaultdict(lambda: {"n": 0, "ok300": 0, "ok600": 0, "ok900": 0, "navires": set()})
    for d in jours:
        t = time.time()
        for r in await c.fetch(RECEPTION_JOUR_SQL, z["cell_deg_lon"], z["cell_deg_lat"], t0(d), t0(d) + timedelta(days=1)):
            x = cells[(r["cx"], r["cy"])]
            for k in ("n", "ok300", "ok600", "ok900"):
                x[k] += r[k]
            x["navires"].update(r["navires"])
        log(f"réception {d:%d/%m} en {time.time() - t:.1f} s ({len(cells)} cellules vues)")
    en_service = {(r["cx"], r["cy"]) for r in await c.fetch("SELECT cx, cy FROM reception_cells")}

    def surface(k):
        cx, cy = k
        lat = (cy + 0.5) * z["cell_deg_lat"]
        return z["cell_deg_lon"] * 111.32 * math.cos(math.radians(lat)) * z["cell_deg_lat"] * 110.57

    out = []
    for nom, inter, cov, paires in VARIANTES_RECEPTION:
        ok = {k for k, x in cells.items() if x["n"] >= paires and len(x["navires"]) >= z["min_vessels"]
              and x[f"ok{inter}"] / x["n"] >= cov}
        par_region = Counter()
        for k in ok:
            par_region[region((k[0] + 0.5) * z["cell_deg_lon"], (k[1] + 0.5) * z["cell_deg_lat"])] += 1
        out.append({"variante": nom, "intervalle_s": inter, "continuite": cov, "paires": paires, "cellules": len(ok),
                    "km2": round(sum(surface(k) for k in ok)), "gagnees": len(ok - en_service),
                    "perdues": len(en_service - ok), "par_region": dict(par_region)})
    return {"variantes": out, "en_service": len(en_service), "en_service_km2": round(sum(surface(k) for k in en_service)),
            "cellules_vues": len(cells), "jours": [str(d) for d in jours]}


def section_reception(x: dict, rules: dict) -> list[str]:
    z = rules["reception"]
    L = ["## 3. Zone de réception fiable recalculée", "",
         f"Positions de classe A en route sur {len(x['jours'])} jours (mêmes définitions que la règle, intervalle mesuré "
         f"dans la journée) ; en service : {x['en_service']} cellules, {x['en_service_km2']} km² (réglage provisoire du "
         f"11/10 : {z['max_interval_s']} s, {100 * z['min_coverage']:.0f} %, {z['min_pairs']} paires, "
         f"{z['min_vessels']} navires). Cellules vues : {x['cellules_vues']}.", ""]
    rows = []
    for v in x["variantes"]:
        reg = ", ".join(f"{NOMS.get(k, k)} {n}" for k, n in sorted(v["par_region"].items()))
        rows.append([v["variante"], v["intervalle_s"], f"{100 * v['continuite']:.0f} %", v["paires"], v["cellules"],
                     v["km2"], v["gagnees"], v["perdues"], reg])
    return L + [table(["Variante", "Intervalle (s)", "Continuité", "Paires", "Cellules", "km²", "Gagnées",
                       "Perdues", "Par région"], rows), ""]


# 4. Rendez vous au large

VARIANTES_RDV = [("danois", 500, 120), ("1 000 m, 120 min", 1000, 120), ("500 m, 60 min", 500, 60)]


async def rendezvous(c, jours: list[date], rules: dict) -> dict:
    r = rules["rendezvous"]
    out = {}
    noms = {}
    for nom, dmax, duree in VARIANTES_RDV:
        eps = []
        for d in jours:
            t = time.time()
            rows = await c.fetch(RENDEZVOUS_SQL, t0(d), t0(d) + timedelta(days=1), r["slot_min"], r["max_speed_kn"],
                                 float(dmax), r["max_gap_min"], r["stationary_zone_buffer_m"],
                                 r["excluded_ship_types"], int(duree), r["excluded_nav_status"],
                                 r["min_coast_km"] * 1000.0, float(r.get("coast_prefilter_m", 0)))
            for x in rows:
                if x["near_coast"] or (x["coast_m"] is not None and x["coast_m"] < r["min_coast_km"] * 1000):
                    continue
                eps.append({"jour": str(d), "v1": x["v1"], "v2": x["v2"], "debut": x["t_start"].isoformat(),
                            "duree_min": round((x["t_end"] - x["t_start"]).total_seconds() / 60),
                            "d_min": round(x["d_min"]), "d_moy": round(x["d_avg"]),
                            "cote_km": None if x["coast_m"] is None else round(x["coast_m"] / 1000, 1),
                            "mouillage": x["in_stationary_zone"], "lon": round(x["lon"], 4), "lat": round(x["lat"], 4),
                            "region": region(x["lon"], x["lat"])})
            log(f"rendez vous {nom} {d:%d/%m} en {time.time() - t:.1f} s")
        ids = {e["v1"] for e in eps} | {e["v2"] for e in eps}
        manque = [i for i in ids if i not in noms]
        if manque:
            for v in await c.fetch("SELECT id, name FROM vessels WHERE id = ANY($1)", manque):
                noms[v["id"]] = v["name"]
        for e in eps:
            e["noms"] = f"{noms.get(e['v1']) or e['v1']} et {noms.get(e['v2']) or e['v2']}"
        out[nom] = eps
    return {"variantes": out, "jours": [str(d) for d in jours]}


def section_rendezvous(x: dict, rules: dict) -> list[str]:
    r = rules["rendezvous"]
    n_j = len(x["jours"])
    L = ["## 4. Rendez vous au large", "",
         f"Requête de la règle par journée (épisode coupé à minuit), vitesse sous {r['max_speed_kn']} nœuds, tranches de "
         f"{r['slot_min']} min, au moins {r['min_coast_km']} km de la côte ; avant les filtres de gravité et de mouillage "
         f"(la colonne « mouillage » le dit). {n_j} jours.", ""]
    rows = []
    for nom, eps in x["variantes"].items():
        rows.append([nom, len(eps), f"{len(eps) / max(n_j, 1):.1f}", sum(e["mouillage"] for e in eps),
                     q([e["duree_min"] for e in eps], .5), q([e["duree_min"] for e in eps], .9),
                     q([e["d_min"] for e in eps], .5), q([e["d_moy"] for e in eps], .5)])
    L += [table(["Variante", "Épisodes", "Par jour", "Dans un mouillage", "Durée médiane (min)", "Durée p90",
                 "Distance min médiane (m)", "Distance moyenne médiane (m)"], rows), ""]
    base = {(e["v1"], e["v2"], e["jour"]) for e in x["variantes"]["danois"]}
    for nom, eps in x["variantes"].items():
        if nom == "danois":
            continue
        plus = [e for e in eps if (e["v1"], e["v2"], e["jour"]) not in base]
        L += [f"### {nom} : {len(plus)} épisodes de plus que les seuils danois", "",
              table(["Navires", "Jour", "Durée (min)", "Distance min (m)", "Côte (km)", "Région", "Mouillage"],
                    [[e["noms"], e["jour"], e["duree_min"], e["d_min"], e["cote_km"], NOMS.get(e["region"], e["region"]),
                      "oui" if e["mouillage"] else "non"]
                     for e in sorted(plus, key=lambda e: -e["duree_min"])[:12]]), ""]
    da = x["variantes"]["danois"]
    L += ["### Seuils danois : les plus longs", "",
          table(["Navires", "Jour", "Durée (min)", "Distance min (m)", "Côte (km)", "Région", "Mouillage"],
                [[e["noms"], e["jour"], e["duree_min"], e["d_min"], e["cote_km"], NOMS.get(e["region"], e["region"]),
                  "oui" if e["mouillage"] else "non"] for e in sorted(da, key=lambda e: -e["duree_min"])[:12]]), ""]
    return L


# 5. Zones d'arrêt

async def zones_arret(c, first: date, last: date, rules: dict) -> dict:
    z = rules["stationary_zones"]
    # Même définition que la requête en service (mars/rules.py), sans l'insertion
    for frag in ("sog_kn < $3::float8", "count(DISTINCT vessel_id) >= $4::int AND count(*) >= $7::int",
                 "ts >= $5::date AND ts < ($6::date + 1)"):
        assert frag in STATIONARY_ZONES_SQL, frag
    sql = """SELECT cx, cy, count(DISTINCT vessel_id) AS vessels, count(*) AS slow_positions
             FROM (SELECT vessel_id, floor(ST_X(geom::geometry) / $1::float8)::int AS cx,
                          floor(ST_Y(geom::geometry) / $2::float8)::int AS cy
                   FROM positions WHERE sog_kn IS NOT NULL AND sog_kn < $3::float8 AND ts >= $5::date
                     AND ts < ($6::date + 1)) s
             GROUP BY cx, cy HAVING count(DISTINCT vessel_id) >= $4::int AND count(*) >= $7::int"""
    t = time.time()
    rows = await c.fetch(sql, z["cell_deg_lon"], z["cell_deg_lat"], z["max_speed_kn"], z["min_vessels"], first, last,
                         z["min_slow_positions"])
    log(f"zones d'arrêt recalculées en {time.time() - t:.1f} s")
    nouv = {(r["cx"], r["cy"]): r for r in rows}
    cur = await c.fetch(
        """SELECT floor(ST_X(ST_Centroid(geom::geometry)) / $1::float8)::int AS cx,
                  floor(ST_Y(ST_Centroid(geom::geometry)) / $2::float8)::int AS cy, vessels, day_from, day_to
           FROM stationary_zones""", z["cell_deg_lon"], z["cell_deg_lat"])
    ancien = {(r["cx"], r["cy"]): r for r in cur}
    reg = Counter(region((k[0] + 0.5) * z["cell_deg_lon"], (k[1] + 0.5) * z["cell_deg_lat"]) for k in nouv)
    periode = {(r["day_from"], r["day_to"]) for r in cur}
    return {"recalculees": len(nouv), "en_service": len(ancien), "communes": len(set(nouv) & set(ancien)),
            "nouvelles": len(set(nouv) - set(ancien)), "disparues": len(set(ancien) - set(nouv)),
            "par_region": dict(reg), "periode_en_service": [[str(a), str(b)] for a, b in periode],
            "navires_median": q([r["vessels"] for r in rows], .5)}


def section_zones(x: dict, first: date, last: date) -> list[str]:
    reg = ", ".join(f"{NOMS.get(k, k)} {n}" for k, n in sorted(x["par_region"].items()))
    return ["## 5. Zones d'arrêt (masques des mouillages)", "",
            f"Recalculées du {first:%d/%m} au {last:%d/%m} avec la requête et les seuils en service : "
            f"{x['recalculees']} cellules ({reg}), navires distincts médians {x['navires_median']} ; en service "
            f"{x['en_service']} cellules (période {x['periode_en_service']}) ; communes {x['communes']}, nouvelles "
            f"{x['nouvelles']}, disparues {x['disparues']}. Rien n'est écrit : la table en service reste celle de "
            "build_masks.py.", ""]


# 6. VIIRS sous la lune actuelle

async def viirs(c, first: date, rules: dict) -> dict:
    rows = await c.fetch(
        """SELECT g.nuit, count(DISTINCT g.id) AS granules, round(avg(g.lune)::numeric) AS lune,
                  count(DISTINCT g.id) FILTER (WHERE g.masque = 'fourni') AS masques,
                  count(DISTINCT g.id) FILTER (WHERE g.erreur IS NULL AND g.non_evaluable IS NOT NULL) AS non_eval,
                  count(d.id) AS detections, count(d.id) FILTER (WHERE d.matched_vessel_id IS NOT NULL) AS avec_ais,
                  count(d.id) FILTER (WHERE d.mask_reason IS NULL AND d.matched_vessel_id IS NULL) AS sans_ais
           FROM viirs_granules g LEFT JOIN viirs_detections d ON d.granule_id = g.id
           WHERE g.nuit >= $1 GROUP BY g.nuit ORDER BY g.nuit""", first)
    alertes = await c.fetch(
        """SELECT (details->>'nuit')::date AS nuit, severity, status, count(*) FROM alerts
           WHERE type = 'DARK_SHIP' AND details->>'source' = 'viirs' AND (details->>'nuit')::date >= $1
           GROUP BY 1, 2, 3""", first)
    al = defaultdict(Counter)
    for a in alertes:
        al[a["nuit"]][f"{a['severity']} {a['status']}"] += a["count"]
    return {"nuits": [{**dict(r), "nuit": str(r["nuit"]), "lune": None if r["lune"] is None else int(r["lune"]),
                       "alertes": dict(al.get(r["nuit"], {}))} for r in rows],
            "seuil_absence": rules["viirs"].get("lune_max_absence")}


def section_viirs(x: dict) -> list[str]:
    s = x["seuil_absence"]
    rows = [[n["nuit"], n["lune"], n["granules"], n["masques"], n["non_eval"], n["detections"], n["avec_ais"],
             n["sans_ais"], ", ".join(f"{k} {v}" for k, v in sorted(n["alertes"].items())) or "·"] for n in x["nuits"]]
    concl = [n for n in x["nuits"] if n["lune"] is not None and n["lune"] <= s]
    return ["## 6. VIIRS sous la lune actuelle", "",
            f"Absence de détection concluante sous {s} % de lune seulement (règles 2026.10.53). Nuits concluantes de la "
            f"période : {len(concl)} sur {len(x['nuits'])}. Les tolérances et la gravité des détections sans AIS ne se "
            "calibrent que sur ces nuits ; après le 29/10 (lune sous 55 % vers le 30/10), refaire cette section et la "
            "section VIIRS de mesures_calibration sur au moins sept nuits avec masque des nuages.", "",
            table(["Nuit", "Lune (%)", "Granules", "Masques", "Non évaluables", "Détections", "Avec AIS", "Sans AIS",
                   "Alertes (gravité, statut)"], rows), ""]


SECTIONS = ("coupures", "reception", "rendezvous", "zones", "viirs")


async def mesurer(jours_n: int, exclus: set, sortie: Path, sections=SECTIONS):
    load_env()
    rules = load_rules()
    c = await asyncpg.connect(database_url(), server_settings={"default_transaction_read_only": "on",
                                                               "statement_timeout": "900000"})
    t_debut = time.time()
    try:
        assert await c.fetchval("SHOW default_transaction_read_only") == "on"
        fin = await c.fetchval("SELECT max(ts) FROM positions")
        last = fin.astimezone(timezone.utc).date() - timedelta(days=1)        # dernière journée complète
        first = max((await c.fetchval("SELECT min(ts) FROM positions")).astimezone(timezone.utc).date(),
                    last - timedelta(days=jours_n - 1))
        jours = jours_periode(first, last, exclus)
        log(f"période du {first} au {last}, {len(jours)} jours mesurés, exclus {sorted(map(str, exclus))}")
        res = {"periode": [str(first), str(last)], "jours": [str(d) for d in jours], "exclus": sorted(map(str, exclus)),
               "version_regles": rules["version"]}
        res["trous"] = [{**t, "debut": t["debut"].isoformat(), "fin": t["fin"].isoformat()} for t in
                        await trous_flux(c, t0(first), t0(last) + timedelta(days=1), rules["continu"]["flux_min_ratio"])]
        depuis = datetime(2026, 10, 11, tzinfo=timezone.utc)
        if "coupures" in sections:
            res["coupures"] = {**await coupures(c, depuis, t0(last) + timedelta(days=1), exclus, rules),
                               "depuis": f"{depuis:%d/%m/%Y}"}
        if "reception" in sections:
            res["reception"] = await reception(c, jours, rules)
        if "rendezvous" in sections:
            res["rendezvous"] = await rendezvous(c, jours, rules)
        if "zones" in sections:
            res["zones"] = await zones_arret(c, first, last, rules)
        if "viirs" in sections:
            res["viirs"] = await viirs(c, first, rules)
    finally:
        await c.close()
    res["duree_s"] = round(time.time() - t_debut)
    L = [f"# Mesures du lot Z7 (calibration), {datetime.now(timezone.utc):%d/%m/%Y %H:%M} UTC", "",
         f"Période du {res['periode'][0]} au {res['periode'][1]}, {len(res['jours'])} journées mesurées, journées "
         f"exclues : {', '.join(res['exclus']) or 'aucune'} ; règles {res['version_regles']} ; lecture seule ; "
         f"{res['duree_s']} s.", "", "## 1. Trous du flux AIS (au moins 30 min sous "
         f"{100 * rules['continu']['flux_min_ratio']:.0f} % de la médiane du jour)", "",
         table(["Début (UTC)", "Fin (UTC)", "Minutes"], [[t["debut"][:16], t["fin"][:16], t["minutes"]] for t in res["trous"]])
         if res["trous"] else "Aucun trou de 30 min ou plus.", ""]
    if "coupures" in res:
        L += section_coupures(res["coupures"], rules)
    if "reception" in res:
        L += section_reception(res["reception"], rules)
    if "rendezvous" in res:
        L += section_rendezvous(res["rendezvous"], rules)
    if "zones" in res:
        L += section_zones(res["zones"], date.fromisoformat(res["periode"][0]), date.fromisoformat(res["periode"][1]))
    if "viirs" in res:
        L += section_viirs(res["viirs"])
    sortie.mkdir(parents=True, exist_ok=True)
    nom = f"z7_mesures_{datetime.now(timezone.utc):%Y%m%dT%H%M}"
    (sortie / f"{nom}.md").write_text("\n".join(L))
    (sortie / f"{nom}.json").write_text(json.dumps(res, default=str, ensure_ascii=False))
    log(f"rapport écrit : {sortie / (nom + '.md')} et .json ({res['duree_s']} s)")
    return sortie / f"{nom}.md"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Mesures du lot Z7 (lecture seule)")
    ap.add_argument("--jours", type=int, default=14)
    ap.add_argument("--exclure-jour", type=date.fromisoformat, action="append", default=None)
    ap.add_argument("--sortie", default="/sortie")
    ap.add_argument("--sections", default=",".join(SECTIONS),
                    help="sections à mesurer, séparées par des virgules (trous du flux toujours)")
    a = ap.parse_args()
    exclus = set(a.exclure_jour) if a.exclure_jour is not None else {date(2026, 10, 18)}
    sections = tuple(x for x in a.sections.split(",") if x)
    assert set(sections) <= set(SECTIONS), f"sections inconnues : {set(sections) - set(SECTIONS)}"
    asyncio.run(mesurer(a.jours, exclus, Path(a.sortie), sections))
