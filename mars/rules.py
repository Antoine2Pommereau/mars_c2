"""Moteur de règles comportementales, partagé par l'API (lancement manuel par journée) et le conteneur taches
(évaluation continue toutes les quelques minutes, scripts/taches.py). Requêtes asyncpg.

Les alertes sont enregistrées par clé stable (colonne rule_key) : une nouvelle évaluation met à jour une alerte
existante sans toucher à son statut ni au journal des décisions des opérateurs. Seules les alertes encore vierges
(statut nouvelle, aucune décision) qui ne sont plus détectées sont retirées.

Règles : rendez vous (RENDEZVOUS), coupure AIS (AIS_GAP), navire d'une liste de surveillance dans nos eaux
(WATCHLIST), changement d'identité (IDENTITY_CHANGE). Les fonctions de décision des deux dernières, et la mesure des
coupures du flux, sont pures et couvertes par tests/test_rules.py.
"""
import json
import math
import re
import statistics
from datetime import datetime, timedelta, timezone

from mars.ais.live import ZONES, valid_imo
from mars.ais.mid import flag_of
from mars.evaluation import GARDE_VERIFICATION
from mars.geo import distance_m

WATCH_SEVERITY = {"fort": "critique", "sanctionne": "elevee", "flotte_fantome": "elevee", "suspect_gur": "moyenne"}
SEVERITIES = ["faible", "moyenne", "elevee", "critique"]


def stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")


# Enregistrement des alertes

async def save_alerts(c, kind: str, items: list[dict], prune_from=None, prune_to=None, time_field: str | None = None,
                      rule_version: str = "", rules: dict | None = None) -> dict:
    """Crée ou met à jour les alertes `items` (clé `key`) ; si une fenêtre et un champ de temps sont donnés, retire
    les alertes vierges de ce type, dans cette fenêtre, qui n'ont pas été retrouvées.

    Panneau de contrôle (lot P, `rules["_controle"]`) : seules les alertes d'une règle (ou d'un sous type) en mode
    actif sont écrites ; en observation, l'épisode est compté et gardé dans episodes_observes ; coupée, rien. Une
    règle qui n'est pas active ne retire aucune alerte (un changement de mode ne supprime rien). Chaque alerte porte
    l'empreinte de l'état du panneau."""
    from mars import controle
    rules = rules or {}
    created = updated = observed = cut = 0
    ctrl = rules.get("_controle")
    emp = ctrl["empreinte"] if ctrl else None
    async with c.transaction():
        if ctrl:
            await c.execute(controle.EMPREINTE_SQL.format(p1="$1", p2="$2::text::jsonb"), emp, json.dumps(ctrl["etat"]))
        for it in items:
            mode, regle, sous = controle.decision(rules, kind, it["details"])
            # Zone de vigilance (lot P) : zone et profil inscrits dans l'alerte, gravité relevée au minimum du profil
            it = {**it, "severity": controle.marquer_zone(rules, it["severity"], it["details"], it["lon"], it["lat"])}
            if mode == "observation":
                observed += 1
                await c.execute(controle.EPISODE_SQL.format(p1="$1", p2="$2", p3="$3", p4="$4", p5="$5"),
                                regle, sous, it["key"], it["severity"], emp)
                continue
            if mode == "coupe":
                cut += 1
                continue
            r = await c.fetchrow(
                """INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key,
                                       controle_empreinte)
                   VALUES ($1, $2, $3, ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography, $6, $7, $8, $9)
                   ON CONFLICT (type, rule_key) WHERE rule_key IS NOT NULL DO UPDATE
                   SET event_time = EXCLUDED.event_time, geom = EXCLUDED.geom, rule_version = EXCLUDED.rule_version,
                       controle_empreinte = EXCLUDED.controle_empreinte,
                       """ + GARDE_VERIFICATION + """
                   RETURNING id, (xmax = 0) AS created""",
                kind, it["severity"], it["event_time"], float(it["lon"]), float(it["lat"]), it["details"],
                rule_version, it["key"], emp)
            created += r["created"]
            updated += not r["created"]
            await c.executemany("INSERT INTO alert_evidence VALUES ($1, 'vessel', $2) ON CONFLICT DO NOTHING",
                                [(r["id"], v) for v in dict.fromkeys(it["vessels"]) if v is not None])
        removed = 0
        actif = controle.mode_effectif(rules, controle.regle_de(kind, {})[0]) == "actif"
        if time_field and prune_from is not None and actif:
            removed = int((await c.execute(
                f"""DELETE FROM alerts a
                    WHERE a.type = $1 AND a.status = 'nouvelle'
                      AND (a.details->>'{time_field}')::timestamptz >= $2 AND (a.details->>'{time_field}')::timestamptz < $3
                      AND NOT (coalesce(a.rule_key, '') = ANY($4::text[]))
                      AND NOT EXISTS (SELECT 1 FROM alert_actions x WHERE x.alert_id = a.id)""",
                kind, prune_from, prune_to, [it["key"] for it in items])).split()[-1])
    out = {"nouvelles": created, "mises_a_jour": updated, "retirees": removed}
    if observed or cut:
        out.update({"observees": observed, "coupees": cut})
    return out


RENDEZVOUS_SQL = """
WITH slots AS (
    -- Dernière position de chaque navire dans chaque tranche de temps
    SELECT DISTINCT ON (p.vessel_id, slot)
           p.vessel_id, slot, p.geom, p.sog_kn, p.nav_status
    FROM (
        SELECT vessel_id, geom, sog_kn, nav_status, ts,
               to_timestamp(floor(extract(epoch FROM ts) / ($3::int * 60)) * ($3::int * 60)) AS slot
        FROM positions
        WHERE ts >= $1::timestamptz AND ts < $2::timestamptz
    ) p
    JOIN vessels v ON v.id = p.vessel_id
    WHERE NOT (coalesce(v.ship_type, '') = ANY($8::text[]))
    ORDER BY p.vessel_id, slot, p.ts DESC
),
slow AS (
    -- Navires lents, hors statuts exclus et hors abords immédiats de la côte : un navire à quai ou dans un bassin
    -- ne peut appartenir qu'à un épisode écarté plus bas comme côtier ; l'écarter ici évite l'appariement deux à
    -- deux des ports, qui faisait l'essentiel du coût (plusieurs milliers d'épisodes calculés puis rejetés).
    SELECT * FROM slots s
    WHERE sog_kn IS NOT NULL AND sog_kn < $4::float8 AND NOT (coalesce(nav_status, -1) = ANY($10::int[]))
      AND NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, s.geom, $12::float8))
),
pairs AS (
    SELECT a.slot, a.vessel_id AS v1, b.vessel_id AS v2,
           ST_Distance(a.geom, b.geom) AS d,
           (a.nav_status = 1 OR b.nav_status = 1) AS anchored,
           (a.nav_status = 5 OR b.nav_status = 5) AS moored,
           ST_Centroid(ST_Collect(a.geom::geometry, b.geom::geometry)) AS mid
    FROM slow a JOIN slow b
      ON a.slot = b.slot AND a.vessel_id < b.vessel_id AND ST_DWithin(a.geom, b.geom, $5::float8)
),
marked AS (
    SELECT *, CASE WHEN lag(slot) OVER w IS NULL OR slot - lag(slot) OVER w > make_interval(mins => $6::int) THEN 1 ELSE 0 END AS new_ep
    FROM pairs WINDOW w AS (PARTITION BY v1, v2 ORDER BY slot)
),
episodes AS (
    SELECT v1, v2, sum(new_ep) OVER (PARTITION BY v1, v2 ORDER BY slot) AS ep, slot, d, mid, anchored, moored FROM marked
),
grouped AS (
    SELECT v1, v2, ep, min(slot) AS t_start, max(slot) + make_interval(mins => $3::int) AS t_end,
           count(*) AS n_slots, min(d) AS d_min, avg(d) AS d_avg,
           avg(CASE WHEN anchored THEN 1.0 ELSE 0.0 END) AS anchored_share,
           avg(CASE WHEN moored THEN 1.0 ELSE 0.0 END) AS moored_share,
           ST_Centroid(ST_Collect(mid)) AS mid
    FROM episodes GROUP BY v1, v2, ep
),
filtered AS (
    -- Test rapide de proximité de la côte (index spatial, rayon borné) avant tout calcul de distance exact
    SELECT g.*, EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, g.mid::geography, $11::float8)) AS near_coast
    FROM grouped g
    WHERE g.t_end - g.t_start >= make_interval(mins => $9::int)
)
SELECT f.v1, f.v2, f.t_start, f.t_end, f.n_slots, f.d_min, f.d_avg, f.anchored_share::float8 AS anchored_share,
       f.moored_share::float8 AS moored_share,
       ST_X(f.mid) AS lon, ST_Y(f.mid) AS lat, f.near_coast, c.coast_m,
       EXISTS (SELECT 1 FROM stationary_zones z WHERE ST_DWithin(z.geom, f.mid::geography, $7::float8)) AS in_stationary_zone
FROM filtered f
LEFT JOIN LATERAL (
    -- Distance exacte à la côte, calculée seulement pour les épisodes au large
    SELECT min(ST_Distance(l.geom, f.mid::geography)) AS coast_m FROM land l
    WHERE NOT f.near_coast AND ST_DWithin(l.geom, f.mid::geography, 100000)
) c ON true
"""


def balise(v: dict | None, cfg: dict | None) -> str | None:
    """Balise de pêche ou engin signalé par AIS (jamais un navire) : motif, ou None. `v` : mmsi, name, length_m."""
    if not v or not cfg:
        return None
    lo, hi = cfg.get("mmsi_navire") or (200000000, 799999999)
    if v.get("mmsi") is not None and not lo <= int(v["mmsi"]) <= hi:
        return "MMSI hors de l'intervalle des navires"
    nom = (v.get("name") or "").upper().strip()
    if nom and any(re.search(m, nom) for m in cfg.get("noms") or []):
        return "nom de balise"
    longueur = v.get("length_m")
    if (nom and cfg.get("codes") and re.fullmatch(cfg["codes"], nom)
            and (longueur is None or longueur <= float(cfg.get("longueur_max_m", 6)))):
        return "nom réduit à un code, sans longueur de navire"
    return None


TRAVAUX_PARC_SQL = """SELECT id, coalesce(name, 'parc éolien') AS name
                      FROM infrastructure, (SELECT ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography AS g) x
                      WHERE kind = 'windfarm' AND ST_DWithin(geom, x.g, $3) ORDER BY geom <-> x.g LIMIT 1"""
TRAVAUX_PRESENCE_SQL = """SELECT p.vessel_id, count(DISTINCT (p.ts AT TIME ZONE 'UTC')::date) AS jours
                          FROM positions p, infrastructure i
                          WHERE i.id = $1 AND p.vessel_id = ANY($2::bigint[]) AND p.ts >= $3 AND p.ts < $4
                            AND ST_DWithin(p.geom, i.geom, $5)
                          GROUP BY p.vessel_id"""


async def travaux_offshore(c, x: dict, navires: list, cfg: dict | None, suivis: set) -> dict | None:
    """Rendez vous de travaux dans un parc éolien : parc à moins de parc_m du milieu de la rencontre, et un des deux
    navires du chantier (type déclaré, ou présence répétée dans ce parc avant la rencontre). Jamais pour un navire des
    listes. Retourne {parc, navire, motif} ou None."""
    if not cfg or any(v and v.get("vessel_id") in suivis for v in navires):
        return None
    parc = await c.fetchrow(TRAVAUX_PARC_SQL, x["lon"], x["lat"], float(cfg["parc_m"]))
    if parc is None:
        return None
    for v in navires:
        if v and v.get("ship_type") in (cfg.get("types") or []):
            return {"parc": parc["name"], "navire": v.get("name"), "motif": f"type déclaré {v['ship_type']}"}
    ids = [v["vessel_id"] for v in navires if v]
    debut = x["t_start"] - timedelta(days=int(cfg["presence_fenetre_j"]))
    jours = {r["vessel_id"]: r["jours"] for r in await c.fetch(TRAVAUX_PRESENCE_SQL, parc["id"], ids, debut,
                                                                 x["t_start"], float(cfg["parc_m"]))}
    for v in navires:
        n = jours.get(v["vessel_id"], 0) if v else 0
        if n >= int(cfg["presence_jours"]):
            return {"parc": parc["name"], "navire": v.get("name"),
                    "motif": f"présent dans le parc {n} jours sur les {cfg['presence_fenetre_j']} précédents"}
    return None


async def run_rendezvous(c, day_start, day_end, rules: dict) -> dict:
    """Évalue la règle sur [day_start, day_end[ et met à jour les alertes RENDEZVOUS de cette période."""
    from mars import controle
    r = rules["rendezvous"]
    # Zones de vigilance (lot P) : recherche au seuil le plus bas des zones actives, chaque épisode jugé ensuite au seuil
    # de sa position (sans zone : seuil du fichier, calcul inchangé)
    duree_min = controle.seuil_min(rules, "rendezvous.min_duration_min", r["min_duration_min"])
    rows = await c.fetch(RENDEZVOUS_SQL, day_start, day_end, r["slot_min"], r["max_speed_kn"], r["max_distance_m"],
                         r["max_gap_min"], r["stationary_zone_buffer_m"], r["excluded_ship_types"],
                         int(duree_min), r["excluded_nav_status"], r["min_coast_km"] * 1000.0,
                         float(r.get("coast_prefilter_m", 0)))
    names = {}
    if rows:
        ids = list({x["v1"] for x in rows} | {x["v2"] for x in rows})
        for v in await c.fetch("SELECT id, mmsi, name, ship_type, length_m FROM vessels WHERE id = ANY($1)", ids):
            names[v["id"]] = {"vessel_id": v["id"], "mmsi": v["mmsi"], "name": v["name"],
                              "ship_type": v["ship_type"],
                              "length_m": None if v["length_m"] is None or v["length_m"] != v["length_m"] else v["length_m"]}

    items, excluded, anchorage, travaux_n = [], {"cote": 0}, 0, 0
    suivis = set()                                     # navires des listes : jamais abaissés pour travaux
    if rows and r.get("travaux_offshore"):
        suivis = {w["vessel_id"] for w in await c.fetch(
            "SELECT vessel_id FROM vessel_watch WHERE level <> 'autre_risque'")}
    for x in rows:
        if x["near_coast"]:
            excluded["cote"] += 1
            continue
        # Balise de pêche (lot Z7) : jamais un navire, rendez vous écarté et compté à part
        if balise(names.get(x["v1"]), r.get("balises")) or balise(names.get(x["v2"]), r.get("balises")):
            excluded["balise"] = excluded.get("balise", 0) + 1
            continue
        coast_km = None if x["coast_m"] is None else x["coast_m"] / 1000
        duration_min = (x["t_end"] - x["t_start"]).total_seconds() / 60
        seuil = controle.seuil_zone(rules, "rendezvous.min_duration_min", r["min_duration_min"], x["lon"], x["lat"])
        if duration_min < seuil:                      # retenu par le seuil d'une zone de vigilance, ailleurs trop court
            excluded["hors_zone_de_vigilance"] = excluded.get("hors_zone_de_vigilance", 0) + 1
            continue
        alongside = x["d_min"] <= r["alongside_m"]
        in_zone = bool(x["in_stationary_zone"])
        context = []
        if in_zone:
            context.append("Zone de mouillage connue : activité souvent légitime (avitaillement, transbordement autorisé)")
            anchorage += 1
        travaux = await travaux_offshore(c, x, [names.get(x["v1"]), names.get(x["v2"])], r.get("travaux_offshore"),
                                         suivis)
        if travaux:
            context.append(f"Travaux offshore : {travaux['parc']}, {travaux['navire']} du chantier "
                           f"({travaux['motif']})")
            travaux_n += 1
        if alongside:
            context.append(f"Bord à bord ({round(x['d_min'])} m au plus près) : transfert physiquement possible")
        moored_offshore = bool(x["moored_share"] and x["moored_share"] > 0)
        if moored_offshore:
            context.append(f"Un navire se déclare « amarré » en pleine mer sur {round(100 * x['moored_share'])} % "
                           "de la rencontre : amarrage probable à l'autre navire")
        if x["anchored_share"] and x["anchored_share"] > 0:
            context.append(f"Statut déclaré « au mouillage » sur {round(100 * x['anchored_share'])} % de la rencontre")
        if in_zone or travaux:
            severity = "faible"
        elif (duration_min >= r["high_duration_min"] or alongside or moored_offshore
              or (coast_km is not None and coast_km >= r["high_coast_km"])):
            severity = "elevee"
        else:
            severity = "moyenne"
        details = {
            "navires": [names.get(x["v1"]), names.get(x["v2"])],
            "debut": x["t_start"].isoformat(), "fin": x["t_end"].isoformat(),
            "duree_min": round(duration_min), "distance_min_m": round(x["d_min"]),
            "distance_moyenne_m": round(x["d_avg"]),
            "distance_cote_km": None if coast_km is None else round(coast_km, 1),
            "bord_a_bord": alongside, "zone_de_mouillage": in_zone, "amarre_en_mer": moored_offshore,
            "travaux_offshore": travaux,
            "contexte": context,
            "motif": f"Deux navires quasi immobiles, à moins de {r['max_distance_m']} m l'un de l'autre, pendant "
                     f"{round(seuil)} min au moins, loin des côtes",
            "parametres": {**{k: r[k] for k in ("max_distance_m", "max_speed_kn", "min_duration_min", "min_coast_km",
                                                "alongside_m")}, "min_duration_min": seuil},
        }
        # Instant où la règle aurait été franchie en direct : la durée minimale après le début de l'épisode (moins dans
        # une zone de vigilance au seuil abaissé)
        event_time = x["t_start"] + timedelta(minutes=seuil)
        items.append({"v1": x["v1"], "v2": x["v2"], "severity": severity, "event_time": event_time, "seuil": seuil,
                      "lon": x["lon"], "lat": x["lat"], "details": details, "vessels": [x["v1"], x["v2"]],
                      "t_start": x["t_start"], "t_end": x["t_end"]})

    # Un même épisode, vu tronqué par le début de la fenêtre ou prolongé depuis, garde sa clé et son début
    gap = timedelta(minutes=r["max_gap_min"])
    # Alertes existantes des mêmes paires, en une requête (une par épisode coûterait une lecture de table chacune)
    prior_by_pair: dict[tuple, list] = {}
    for p in await c.fetch(
            "SELECT rule_key, (details->>'debut')::timestamptz AS debut, (details->>'fin')::timestamptz AS fin "
            "FROM alerts WHERE type = 'RENDEZVOUS' AND rule_key IS NOT NULL "
            "AND (details->>'fin')::timestamptz >= $1::timestamptz - make_interval(mins => $2::int)",
            day_start, r["max_gap_min"]):
        prior_by_pair.setdefault(tuple(p["rule_key"].split(":")[:2]), []).append(p)
    for it in items:
        prior = prior_by_pair.get((str(it["v1"]), str(it["v2"])), [])
        same = [p for p in prior if p["debut"] <= it["t_end"] + gap and p["fin"] >= it["t_start"] - gap]
        if same:
            first = min(same, key=lambda p: p["debut"])
            it["key"] = first["rule_key"]
            if first["debut"] < it["t_start"]:
                it["details"]["debut"] = first["debut"].isoformat()
                it["details"]["duree_min"] = round((it["t_end"] - first["debut"]).total_seconds() / 60)
                it["event_time"] = first["debut"] + timedelta(minutes=it["seuil"])
        else:
            it["key"] = f"{it['v1']}:{it['v2']}:{stamp(it['t_start'])}"
    saved = await save_alerts(c, "RENDEZVOUS", items, day_start, day_end, "debut", rules["version"], rules=rules)
    return {"episodes_detectes": len(rows), "alertes": len(items), "dont_zone_de_mouillage": anchorage,
            "dont_travaux_offshore": travaux_n, "exclus_balises": excluded.get("balise", 0), "exclus": excluded,
            **saved}


STATIONARY_ZONES_SQL = """
INSERT INTO stationary_zones (geom, vessels, slow_positions, day_from, day_to)
SELECT ST_MakeEnvelope(cx * $1::float8, cy * $2::float8, (cx + 1) * $1::float8, (cy + 1) * $2::float8, 4326)::geography,
       count(DISTINCT vessel_id), count(*), $5::date, $6::date
FROM (
    SELECT vessel_id, floor(ST_X(geom::geometry) / $1::float8)::int AS cx, floor(ST_Y(geom::geometry) / $2::float8)::int AS cy
    FROM positions
    WHERE sog_kn IS NOT NULL AND sog_kn < $3::float8 AND ts >= $5::date AND ts < ($6::date + 1)
) s
GROUP BY cx, cy
HAVING count(DISTINCT vessel_id) >= $4::int AND count(*) >= $7::int
"""


async def build_stationary_zones(c, day_from, day_to, rules: dict) -> int:
    z = rules["stationary_zones"]
    async with c.transaction():
        await c.execute("DELETE FROM stationary_zones")
        await c.execute(STATIONARY_ZONES_SQL, z["cell_deg_lon"], z["cell_deg_lat"], z["max_speed_kn"],
                        z["min_vessels"], day_from, day_to, z["min_slow_positions"])
        return await c.fetchval("SELECT count(*) FROM stationary_zones")


RECEPTION_SQL = """
INSERT INTO reception_cells (cx, cy, messages, vessels, hours, coverage, geom)
SELECT cx, cy, count(*), count(DISTINCT vessel_id), count(DISTINCT date_trunc('hour', ts)),
       avg(CASE WHEN dt <= $3::float8 THEN 1.0 ELSE 0.0 END),
       ST_MakeEnvelope(cx * $1::float8, cy * $2::float8, (cx + 1) * $1::float8, (cy + 1) * $2::float8, 4326)::geography
FROM (
    SELECT p.vessel_id, p.ts,
           floor(ST_X(p.geom::geometry) / $1::float8)::int AS cx, floor(ST_Y(p.geom::geometry) / $2::float8)::int AS cy,
           extract(epoch FROM lead(p.ts) OVER (PARTITION BY p.vessel_id ORDER BY p.ts) - p.ts) AS dt,
           p.sog_kn
    FROM positions p JOIN vessels v ON v.id = p.vessel_id
    WHERE v.ais_class = 'A' AND ($7::timestamptz IS NULL OR p.ts >= $7::timestamptz)
) s
WHERE sog_kn >= 1 AND dt IS NOT NULL AND dt < 7200
GROUP BY cx, cy
HAVING count(*) >= $5::int AND count(DISTINCT vessel_id) >= $6::int
   AND avg(CASE WHEN dt <= $3::float8 THEN 1.0 ELSE 0.0 END) >= $4::float8
"""


def min_paires(z: dict, jours: float | None) -> int:
    """Minimum de paires d'une cellule pour une reconstruction sur `jours` jours : `min_pairs` est donné pour
    `min_pairs_jours` jours (sans eux, ou sans durée connue, la valeur telle quelle)."""
    ref = z.get("min_pairs_jours")
    if not ref or not jours:
        return int(z["min_pairs"])
    return max(1, round(z["min_pairs"] * float(jours) / float(ref)))


async def build_reception_cells(c, rules: dict, since=None) -> dict:
    """Zone de réception fiable : cellules où les trajectoires des navires en route sont continues. `since` borne
    les positions examinées (calcul plus léger en mémoire et en fichiers temporaires sur un petit serveur)."""
    z = rules["reception"]
    debut = since or await c.fetchval("SELECT min(ts) FROM positions")
    fin = await c.fetchval("SELECT max(ts) FROM positions")
    jours = (fin - debut).total_seconds() / 86400 if debut and fin else None
    paires = min_paires(z, jours)
    from mars import traces
    traces.attributs(**{"reception.jours": round(jours or 0, 1), "reception.min_pairs": paires})
    async with c.transaction():
        await c.execute("DELETE FROM reception_cells")
        await c.execute(RECEPTION_SQL, z["cell_deg_lon"], z["cell_deg_lat"], z["max_interval_s"],
                        z["min_coverage"], paires, z["min_vessels"], since)
        r = await c.fetchrow("SELECT count(*) AS n, round((sum(ST_Area(geom)) / 1e6)::numeric) AS km2, "
                             "round(avg(coverage)::numeric, 3) AS couverture FROM reception_cells")
    return {"cellules_fiables": r["n"], "surface_km2": float(r["km2"] or 0), "continuite_moyenne": float(r["couverture"] or 0),
            "jours": round(jours or 0, 1), "min_pairs": paires}


AIS_GAP_SQL = """
WITH p AS (
    SELECT vessel_id, ts, geom, sog_kn, cog_deg, nav_status,
           lead(ts) OVER w AS next_ts, lead(geom) OVER w AS next_geom,
           count(*) OVER (PARTITION BY vessel_id ORDER BY ts
                          RANGE BETWEEN make_interval(mins => $11::int) PRECEDING AND CURRENT ROW) AS prior_n
    FROM positions
    WHERE ts >= $1::timestamptz AND ts < $2::timestamptz
    WINDOW w AS (PARTITION BY vessel_id ORDER BY ts)
),
gaps AS MATERIALIZED (
    SELECT p.*, coalesce(p.next_ts, $2::timestamptz) - p.ts AS duration,
           -- Position estimée à l'estime : $13 minutes après le dernier message, ou $14 sans réapparition
           CASE WHEN p.cog_deg IS NULL THEN NULL
                ELSE ST_Project(p.geom, p.sog_kn * 0.514444 * 60 *
                                (CASE WHEN p.next_ts IS NULL THEN $14::float8 ELSE $13::float8 END),
                                radians(p.cog_deg))::geometry END AS proj
    FROM p
    WHERE coalesce(p.next_ts, $2::timestamptz) - p.ts >= make_interval(mins => $3::int)
      AND coalesce(p.sog_kn, 0) >= $4::float8
      AND p.prior_n >= $12::int
),
-- MATERIALIZED : sinon PostgreSQL déplie ces sous requêtes et recalcule le bord en mer (union du trait de côte) pour
-- chaque silence : 68 s au lieu de 0,7 s sur 147 000 positions réelles du 11/10/2026, mêmes 139 silences (12/10/2026).
-- Couverture des données de chaque journée : union des zones collectées (à défaut, rectangle de l'import). Son bord
-- n'est une sortie possible qu'en mer : les portions de bord qui passent sur la terre sont retirées.
area AS MATERIALIZED (
    SELECT d.day, coalesce(d.coverage, ST_MakeEnvelope(d.lon_min, d.lat_min, d.lon_max, d.lat_max, 4326)) AS g
    FROM ais_days d
    WHERE d.day BETWEEN ($1::timestamptz AT TIME ZONE 'UTC')::date AND ($2::timestamptz AT TIME ZONE 'UTC')::date
),
edge AS MATERIALIZED (
    SELECT a.day, a.g AS area,
           coalesce(ST_Difference(ST_Boundary(a.g),
                                  (SELECT ST_Union(l.geom::geometry) FROM land l
                                   WHERE ST_Intersects(l.geom::geometry, ST_Boundary(a.g)))),
                    ST_Boundary(a.g)) AS sea_edge
    FROM area a
),
-- Bord de la zone de réception fiable (lot E01, 29/10/2026) : calculé une fois, utilisé pour les silences sans réapparition
-- Cellules refaites depuis leurs indices et accrochées à une grille : l'union des géométries stockées, dont les bords
-- voisins diffèrent de quelques ulp, laisse des frontières internes (un bord à 5 km au milieu de la zone)
zone_bord AS MATERIALIZED (
    SELECT ST_Boundary(ST_Union(ST_SnapToGrid(ST_MakeEnvelope(cx * $5::float8, cy * $6::float8, (cx + 1) * $5::float8,
                                                              (cy + 1) * $6::float8, 4326), 1e-7))) AS b
    FROM reception_cells
)
SELECT g.vessel_id, v.mmsi, v.name, v.ship_type, v.length_m, v.ais_class, g.prior_n,
       g.ts AS t_last, g.next_ts AS t_next, extract(epoch FROM g.duration)::float8 / 60 AS duration_min,
       g.sog_kn, g.nav_status,
       ST_X(g.geom::geometry) AS lon, ST_Y(g.geom::geometry) AS lat,
       ST_X(g.next_geom::geometry) AS next_lon, ST_Y(g.next_geom::geometry) AS next_lat,
       CASE WHEN g.next_geom IS NULL THEN NULL ELSE ST_Distance(g.geom, g.next_geom) END AS displacement_m,
       rc.messages AS cell_messages, rc.vessels AS cell_vessels, rc.hours AS cell_hours, rc.coverage AS cell_coverage,
       (g.proj IS NULL OR (
            EXISTS (SELECT 1 FROM reception_cells r2
                    WHERE r2.cx = floor(ST_X(g.proj) / $5::float8)::int AND r2.cy = floor(ST_Y(g.proj) / $6::float8)::int)
            AND ST_Within(g.proj, e.area) AND NOT ST_DWithin(g.proj, e.sea_edge, $9::float8))) AS projection_couverte,
       -- Route déclarée (dernier cap, dernière vitesse) échantillonnée toutes les $16 minutes jusqu'à $15 minutes :
       -- couverte si chaque point reste dans une cellule de la zone fiable (recherche par clé de cellule, sans géométrie)
       (g.cog_deg IS NULL OR $15::int <= 0 OR NOT EXISTS (
            SELECT 1 FROM generate_series($16::int, $15::int, $16::int) AS m(t),
                 LATERAL (SELECT ST_Project(g.geom, g.sog_kn * 0.514444 * 60 * m.t, radians(g.cog_deg))::geometry AS q) qq
            WHERE NOT EXISTS (SELECT 1 FROM reception_cells r3
                              WHERE r3.cx = floor(ST_X(qq.q) / $5::float8)::int AND r3.cy = floor(ST_Y(qq.q) / $6::float8)::int)
       )) AS route_couverte,
       g.cog_deg AS cog_deg,
       -- Silence sans réapparition : distance de la dernière position au point le plus proche du bord de la zone
       -- fiable, seulement si ce point est en mer (à plus de $17 m de la terre) ; le bord qui longe la côte n'est pas
       -- une sortie de notre réception, et les abords des atterrages doivent garder leur gravité
       (SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, q.cp::geography, $17::float8))
                    THEN ST_Distance(g.geom, q.cp::geography) END
        FROM (SELECT ST_ClosestPoint(zb.b, g.geom::geometry) AS cp FROM zone_bord zb) q
        WHERE g.next_ts IS NULL AND q.cp IS NOT NULL) AS bord_mer_m
FROM gaps g
JOIN vessels v ON v.id = g.vessel_id
JOIN reception_cells rc
  ON rc.cx = floor(ST_X(g.geom::geometry) / $5::float8)::int AND rc.cy = floor(ST_Y(g.geom::geometry) / $6::float8)::int
JOIN edge e ON e.day = (g.ts AT TIME ZONE 'UTC')::date
WHERE coalesce(v.ais_class, '') = ANY($7::text[])
  AND NOT (coalesce(v.ship_type, '') = ANY($8::text[]))
  AND ST_Within(g.geom::geometry, e.area) AND NOT ST_DWithin(g.geom::geometry, e.sea_edge, $9::float8)
  AND NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, g.geom, $10::float8))
"""

PARTNERS_SQL = """
SELECT p.vessel_id, v.mmsi, v.name, v.ship_type, count(*) AS positions,
       round(min(ST_Distance(p.geom, $4::geography)))::int AS distance_min_m,
       min(p.ts) AS debut, max(p.ts) AS fin,
       bool_or(EXISTS (SELECT 1 FROM stationary_zones z WHERE ST_DWithin(z.geom, p.geom, 1000))) AS au_mouillage
FROM positions p JOIN vessels v ON v.id = p.vessel_id
WHERE p.ts BETWEEN $1::timestamptz AND $2::timestamptz
  AND p.vessel_id <> $3::bigint
  AND p.sog_kn IS NOT NULL AND p.sog_kn < $5::float8
  AND ST_DWithin(p.geom, $4::geography, $6::float8)
GROUP BY p.vessel_id, v.mmsi, v.name, v.ship_type
HAVING count(*) >= $7::int AND max(p.ts) - min(p.ts) >= make_interval(mins => $8::int)
ORDER BY count(*) DESC
LIMIT 3
"""


def _finite(v):
    return None if v is None or v != v else v


def outage_minutes(counts: dict, ratio: float) -> set:
    """Minutes où le flux AIS était coupé : moins de `ratio` fois la médiane des messages par minute de la fenêtre
    (minutes sans aucun message comprises, `counts` ne les contient pas)."""
    if not counts:
        return set()
    start, end = min(counts), max(counts)
    full = [counts.get(start + timedelta(minutes=k), 0) for k in range(int((end - start).total_seconds() // 60) + 1)]
    med = statistics.median(full)
    return {start + timedelta(minutes=k) for k, n in enumerate(full) if n < ratio * med}


def outage_overlap_min(outage: set, t0: datetime, t1: datetime) -> float:
    """Minutes de coupure du flux comprises dans [t0, t1[."""
    m = t0.replace(second=0, microsecond=0)
    n = 0
    while m < t1:
        n += m in outage
        m += timedelta(minutes=1)
    return float(n)


async def feed_outage(c, start, end, ratio: float) -> set:
    rows = await c.fetch("SELECT date_trunc('minute', ts) AS m, count(*) AS n FROM positions "
                         "WHERE ts >= $1::timestamptz AND ts < $2::timestamptz GROUP BY 1", start, end)
    return outage_minutes({r["m"]: r["n"] for r in rows}, ratio)


async def find_gaps(c, day_start, day_end, rules: dict, min_prior: int | None = None):
    """Silences candidats. `min_prior` : historique exigé avant le silence (par défaut `min_prior_messages`) ; abaissé
    par run_ais_gap quand le flux a été coupé, l'exigence étant alors appliquée silence par silence au prorata."""
    from mars import controle
    g, rc = rules["ais_gap"], rules["reception"]
    # Zones de vigilance (lot P) : recherche au seuil le plus bas des zones actives (sans zone : celui du fichier)
    return await c.fetch(AIS_GAP_SQL, day_start, day_end,
                         int(controle.seuil_min(rules, "ais_gap.min_gap_min", g["min_gap_min"])), g["min_speed_kn"],
                         rc["cell_deg_lon"], rc["cell_deg_lat"], g["ais_classes"], g["excluded_ship_types"],
                         g["edge_margin_deg"], g["min_coast_km"] * 1000.0, g["prior_window_min"],
                         g["min_prior_messages"] if min_prior is None else min_prior,
                         g["projection_min"], g["projection_open_min"],
                         int(g.get("route_horizon_min", 0)), max(1, int(g.get("route_pas_min", 10))),
                         float(g.get("bord_zone_terre_km", 6)) * 1000.0)


def _cell(lon: float, lat: float, size: list) -> tuple[int, int]:
    return math.floor(lon / size[0]), math.floor(lat / size[1])


def collective_points(rows: list, g: dict) -> dict:
    """Points de disparition collectifs : cellules (`disparition_cellule_deg`) où, le même jour (UTC), au moins
    `disparition_min_navires` navires sans lien se sont tus (silences détectés, sorties de couverture comprises). Deux
    navires qui se taisent à moins de `partner_radius_m` et 30 minutes l'un de l'autre comptent pour un (ils peuvent
    être liés : rendez vous, convoi). Des navires sans lien qui se taisent au même endroit signalent notre réception,
    pas des navires sombres. Retourne {(cx, cy, jour): nombre de navires sans lien} ; `point_kind` en donne la nature."""
    size, need = g["disparition_cellule_deg"], g["disparition_min_navires"]
    groups: dict[tuple, list] = {}
    for x in rows:
        groups.setdefault((*_cell(x["lon"], x["lat"], size), x["t_last"].astimezone(timezone.utc).date()), []).append(x)
    out = {}
    for key, xs in groups.items():
        clusters: list[list] = []
        for x in sorted(xs, key=lambda r: r["t_last"]):
            for cl in clusters:
                if any(abs((x["t_last"] - y["t_last"]).total_seconds()) <= 1800
                       and _hav(x["lon"], x["lat"], y["lon"], y["lat"]) <= g["partner_radius_m"] for y in cl):
                    cl.append(x)
                    break
            else:
                clusters.append([x])
        n = len({min(r["vessel_id"] for r in cl) for cl in clusters})
        if n >= need:
            out[key] = n
    return out


def point_kind(times: list[datetime], need: int) -> str:
    """Nature d'un point collectif d'après la répartition des silences dans le temps : la moitié au moins des silences
    (et `need` au moins) en une demi heure, coupure de réception simultanée (récepteur ou relais tombé) ; étalés sur la
    journée, limite de couverture (au rythme du trafic, trois silences tombent forcément proches un moment ou un autre :
    le seul nombre ne suffit pas) (le cas du 11/10/2026 en Manche, 40 km au sud de l'île de Wight : 19 navires vers l'ouest sur la voie
    descendante du dispositif de séparation, de 00:39 à 18:51, environ un par heure)."""
    ts = sorted(times)
    burst = max((sum(1 for u in ts if t <= u <= t + timedelta(minutes=30)) for t in ts), default=0)
    return "coupure_simultanee" if burst >= need and burst >= len(ts) / 2 else "limite_de_couverture"


_hav = distance_m                    # lot Z4 : distance commune (mars/geo.py)


async def run_ais_gap(c, day_start, day_end, rules: dict) -> dict:
    """Évalue la règle des coupures AIS sur [day_start, day_end[ et met à jour les alertes AIS_GAP de cette période.
    Un silence n'est compté que pendant les minutes où le flux AIS fonctionnait (section continu de rules.yaml) : une
    coupure du flux AISStream ou de la collecte ne fait pas apparaître une coupure sur chaque navire."""
    g = rules["ais_gap"]
    outage = await feed_outage(c, day_start, day_end, rules["continu"]["flux_min_ratio"])
    # Un trou de notre collecte (déploiement, coupure d'AISStream : toutes les minutes vides à la fois) agit de trois
    # façons, toutes neutralisées ici : il allonge les silences (minutes retirées de leur durée), il cache leur vrai
    # début (silence qui commence dans le trou ou juste avant : écarté, début inconnu), et il retire des messages de
    # l'heure qui précède (historique exigé réduit au prorata des minutes où la collecte fonctionnait). Éprouvé sur la
    # copie réelle du 11/10/2026 : arrêts simulés de 20 et 140 minutes sans effet sur les coupures retenues.
    raw = await find_gaps(c, day_start, day_end, rules, min_prior=1 if outage else None)
    window, margin = g["prior_window_min"], timedelta(minutes=g.get("debut_incertain_min", 10))
    rows, real, outages, uncertain, hors_zone = [], [], 0, 0, 0
    from mars import controle
    seuils: dict = {}                                 # seuil de durée à la position du silence (zones de vigilance)
    for x in raw:
        seuil = controle.seuil_zone(rules, "ais_gap.min_gap_min", g["min_gap_min"], x["lon"], x["lat"])
        if seuil < g["min_gap_min"]:
            seuils[(x["vessel_id"], x["t_last"])] = seuil
        if ((day_end if x["t_next"] is None else x["t_next"]) - x["t_last"]).total_seconds() / 60 < seuil:
            hors_zone += 1                            # assez long pour une zone de vigilance, pas ici
            continue
        if outage:
            avail = window - outage_overlap_min(outage, x["t_last"] - timedelta(minutes=window), x["t_last"])
            if x["prior_n"] < max(1, math.ceil(g["min_prior_messages"] * max(avail, 0) / window)):
                continue                                  # historique insuffisant, comme sans coupure
        rows.append(x)
        if outage and outage_overlap_min(outage, x["t_last"], x["t_last"] + margin) > 0:
            uncertain += 1                                # début du silence inconnu : la collecte était coupée
            continue
        t_end = day_end if x["t_next"] is None else x["t_next"]
        lost = outage_overlap_min(outage, x["t_last"], t_end)
        if (t_end - x["t_last"]).total_seconds() / 60 - lost < seuil:
            outages += 1                                  # silence dû au flux coupé, pas au navire
            continue
        real.append(x)
    items, with_partner, exits, route_exits, downgraded, at_edge = [], 0, 0, 0, 0, 0
    # Points collectifs : sur les vrais silences seulement (ceux d'un trou de collecte gonfleraient chaque maille)
    collective = collective_points(real, g)
    by_point: dict[tuple, list] = {}
    for x in real:
        by_point.setdefault((*_cell(x["lon"], x["lat"], g["disparition_cellule_deg"]),
                             x["t_last"].astimezone(timezone.utc).date()), []).append(x)
    kinds = {k: point_kind([x["t_last"] for x in by_point[k]], g["disparition_min_navires"]) for k in collective}
    for x in real:
        if not x["projection_couverte"]:
            exits += 1   # cap et vitesse menaient hors de la zone fiable : sortie de couverture probable
            continue
        if not x["route_couverte"]:
            # La route déclarée quitte la zone fiable dans l'horizon : la disparition a lieu dans la zone, mais la suite
            # de la route n'est pas couverte (voie ouest en Manche, 40 km au sud de l'île de Wight, 11/10/2026)
            route_exits += 1
            continue
        open_gap = x["t_next"] is None
        t_end = day_end if open_gap else x["t_next"]
        lost = outage_overlap_min(outage, x["t_last"], t_end)   # minutes de flux coupé pendant le silence
        # Lieux où une rencontre dissimulée laisserait une trace : la dernière position et la réapparition
        if open_gap:
            ref = f"SRID=4326;POINT({x['lon']} {x['lat']})"
        else:
            ref = f"SRID=4326;MULTIPOINT(({x['lon']} {x['lat']}),({x['next_lon']} {x['next_lat']}))"
        t_partner_end = min(t_end, x["t_last"] + timedelta(minutes=g["partner_window_max_min"]))
        partners = await c.fetch(PARTNERS_SQL, x["t_last"], t_partner_end, x["vessel_id"], ref,
                                 g["partner_max_speed_kn"], g["partner_radius_m"], g["partner_min_positions"],
                                 g["partner_min_slow_min"])
        partners = [{"vessel_id": p["vessel_id"], "mmsi": p["mmsi"], "name": p["name"], "ship_type": p["ship_type"],
                     "positions_lentes": p["positions"], "distance_min_m": p["distance_min_m"],
                     "debut": p["debut"].isoformat(), "fin": p["fin"].isoformat(),
                     "au_mouillage": p["au_mouillage"]} for p in partners]
        offshore_partners = [p for p in partners if not p["au_mouillage"]]
        linked = await c.fetch(
            "SELECT a.id FROM alerts a JOIN alert_evidence e ON e.alert_id = a.id "
            "WHERE a.type = 'RENDEZVOUS' AND e.evidence_type = 'vessel' AND e.evidence_id = $1 "
            "AND (a.details->>'debut')::timestamptz < $3 AND (a.details->>'fin')::timestamptz > $2",
            x["vessel_id"], x["t_last"], t_end)

        displacement_km = None if x["displacement_m"] is None else x["displacement_m"] / 1000
        implied_kn = (None if displacement_km is None or x["duration_min"] <= 0
                      else displacement_km * 1000 / (x["duration_min"] * 60) / 0.514444)
        context = []
        if offshore_partners:
            context.append(f"{len(offshore_partners)} navire(s) resté(s) lent(s) hors mouillage à moins de "
                           f"{g['partner_radius_m']} m de la dernière position ou de la réapparition : rencontre possible")
            with_partner += 1
        if len(partners) > len(offshore_partners):
            context.append(f"{len(partners) - len(offshore_partners)} navire(s) lent(s) au mouillage à proximité : "
                           "présence habituelle, indice faible")
        if linked:
            context.append("Rendez vous suspect enregistré pendant le silence")
        behaviour = None
        if open_gap:
            context.append("Pas de réapparition avant la fin des données de la journée, alors que la route "
                           "déclarée restait dans la zone de réception fiable")
        elif implied_kn is not None and x["sog_kn"] > 0:
            ratio = implied_kn / x["sog_kn"]
            if ratio < g["stop_ratio"] and x["sog_kn"] >= g["stop_min_speed_kn"]:
                behaviour = "arret"
                context.append(f"Arrêt pendant le silence : réapparition à {displacement_km:.1f} km seulement, "
                               f"soit {implied_kn:.1f} nœuds de moyenne contre {x['sog_kn']:.1f} déclarés avant")
            elif ratio > g["continue_ratio"]:
                behaviour = "route_poursuivie"
                context.append(f"Route poursuivie pendant le silence : {displacement_km:.1f} km à {implied_kn:.1f} "
                               f"nœuds de moyenne, cohérent avec les {x['sog_kn']:.1f} déclarés : silence moins suspect")
            else:
                context.append(f"Déplacement de {displacement_km:.1f} km à {implied_kn:.1f} nœuds de moyenne, "
                               f"contre {x['sog_kn']:.1f} déclarés avant la coupure")
        if offshore_partners or linked or behaviour == "arret":
            severity = "elevee"
        elif behaviour == "route_poursuivie":
            severity = "faible"
        elif open_gap and x["duration_min"] >= g["high_duration_min"]:
            severity = "elevee"
        else:
            severity = "moyenne"
        point = (*_cell(x["lon"], x["lat"], g["disparition_cellule_deg"]), x["t_last"].astimezone(timezone.utc).date())
        before = severity
        strong = bool(offshore_partners or linked or behaviour == "arret")
        # Lot E01 : sans réapparition, près du bord en mer de la zone fiable, le navire a pu sortir de notre réception
        # sans quitter sa route (mesure du 29/10/2026 : 15 silences élevés sur 36 à moins de 10 km d'un bord en mer,
        # contre 7 sur 31 pour les réapparus ; un point de la Manche face à Newhaven revenait chaque jour sans former de
        # point collectif, à cheval sur la maille du méridien 0 et sur plusieurs jours)
        bord_km = None if x.get("bord_mer_m") is None else x["bord_mer_m"] / 1000
        au_bord = open_gap and bord_km is not None and bord_km <= g.get("bord_zone_km", 0)
        if au_bord and point not in collective and not strong:
            severity = "faible"
            at_edge += 1
            context.insert(0, f"Sans réapparition à {bord_km:.1f} km du bord en mer de la zone de réception fiable : "
                              "sortie de notre réception probable, indice faible")
        elif au_bord and point not in collective and severity != "faible":
            context.append(f"Près du bord en mer de la zone de réception fiable ({bord_km:.1f} km), mais indice propre "
                           "au navire : gravité gardée")
        if point in collective and not strong:
            # Déclassée, pas exclue : un navire sombre pourrait se cacher derrière une limite de réception. Un indice
            # propre au navire (partenaire au large, rendez vous lié, arrêt pendant le silence) garde sa gravité : seul
            # le silence long sans réapparition, signature d'une sortie de notre couverture, est déclassé
            severity = "faible"
            downgraded += 1
            nature = ("coupure de réception simultanée" if kinds[point] == "coupure_simultanee"
                      else "limite de couverture de notre réception")
            context.insert(0, f"Point de disparition collectif : {collective[point]} navires sans lien se sont tus dans "
                              f"cette zone le même jour ({nature}) : indice faible")
        elif point in collective:
            context.append(f"Point de disparition collectif ({collective[point]} navires sans lien le même jour), mais "
                           "indice propre au navire : gravité gardée")
        details = {
            "navire": {"vessel_id": x["vessel_id"], "mmsi": x["mmsi"], "name": x["name"], "ship_type": x["ship_type"],
                       "length_m": _finite(x["length_m"]), "classe_ais": x["ais_class"]},
            "dernier_message": x["t_last"].isoformat(),
            "reapparition": None if open_gap else x["t_next"].isoformat(),
            "duree_min": round(x["duration_min"]),
            "derniere_position": [x["lon"], x["lat"]],
            "position_reapparition": None if open_gap else [x["next_lon"], x["next_lat"]],
            "vitesse_avant_kn": round(x["sog_kn"], 1),
            "messages_heure_precedente": x["prior_n"],
            "deplacement_km": None if displacement_km is None else round(displacement_km, 1),
            "vitesse_implicite_kn": None if implied_kn is None else round(implied_kn, 1),
            "reception_cellule": {"messages": x["cell_messages"], "navires": x["cell_vessels"],
                                  "heures": x["cell_hours"], "continuite": round(x["cell_coverage"], 3)},
            "comportement": behaviour,
            "partenaires_possibles": partners,
            "rendez_vous_lies": [r["id"] for r in linked],
            "contexte": context,
            "motif": "Navire de classe A faisant route qui cesse d'émettre pendant plus de deux heures, au large, "
                     "dans une zone où la réception AIS est continue",
            "parametres": {k: g[k] for k in ("min_gap_min", "min_speed_kn", "min_coast_km", "partner_radius_m")},
        }
        if lost:
            context.append(f"Flux AIS interrompu {round(lost)} min pendant le silence : durée effective "
                           f"{round(x['duration_min'] - lost)} min")
        details["flux_interrompu_min"] = round(lost)
        details["disparition_collective"] = collective.get(point)
        details["nature_point_collectif"] = kinds.get(point)
        details["gravite_avant_declassement"] = before
        details["bord_zone_km"] = None if bord_km is None else round(bord_km, 1)
        event_time = x["t_last"] + timedelta(minutes=seuils.get((x["vessel_id"], x["t_last"]), g["min_gap_min"]))
        items.append({"key": f"{x['vessel_id']}:{stamp(x['t_last'])}", "severity": severity,
                      "event_time": event_time, "lon": x["lon"], "lat": x["lat"], "details": details,
                      "vessels": [x["vessel_id"]] + [p["vessel_id"] for p in partners]})

    counts = {"silences_detectes": len(rows), "exclus_sortie_de_couverture": exits,
              "exclus_route_hors_zone": route_exits, "exclus_coupure_du_flux": outages,
              "exclus_debut_pendant_coupure_du_flux": uncertain, "coupures_retenues": len(items),
              "coupures_declassees_disparition_collective": downgraded,
              "coupures_declassees_bord_de_zone": at_edge,
              "points_de_disparition_collectifs": len(collective),
              "points_collectifs": [{"lon": round((k[0] + 0.5) * g["disparition_cellule_deg"][0], 3),
                                     "lat": round((k[1] + 0.5) * g["disparition_cellule_deg"][1], 3), "jour": str(k[2]),
                                     "navires": n, "nature": kinds[k],
                                     "premier": min(x["t_last"] for x in by_point[k]).strftime("%H:%M"),
                                     "dernier": max(x["t_last"] for x in by_point[k]).strftime("%H:%M"),
                                     "cap_median": round(statistics.median([x["cog_deg"] for x in by_point[k]
                                                                            if x["cog_deg"] is not None] or [0]))}
                             for k, n in sorted(collective.items(), key=lambda kv: -kv[1])[:20]],
              "avec_partenaire_possible": with_partner, "minutes_de_flux_coupe": len(outage)}
    if hors_zone:
        counts["exclus_hors_zone_de_vigilance"] = hors_zone
    from mars import controle
    if controle.mode_effectif(rules, "ais_gap") == "observation":
        # Seuils encore danois : les épisodes sont comptés et décrits pour la recalibration, aucune alerte n'est créée,
        # mise à jour ni retirée (celles qui existeraient déjà restent telles quelles). Mode du fichier (ais_gap.mode)
        # ou du panneau de contrôle, qui prime ; épisodes gardés dans episodes_observes
        saved = await save_alerts(c, "AIS_GAP", items, rule_version=rules["version"], rules=rules)
        return {**counts, "mode": "observation", **observation_summary(items), "observees": saved.get("observees", 0)}
    # Les alertes dont le dernier message précède le début de la fenêtre plus la fenêtre d'historique de la règle ne
    # sont pas retirées : la fenêtre glissante ne voit plus leur historique, elles restent valables
    prune_from = day_start + timedelta(minutes=g["prior_window_min"])
    saved = await save_alerts(c, "AIS_GAP", items, prune_from, day_end, "dernier_message", rules["version"], rules=rules)
    return {**counts, "mode": "alertes", **saved}


OBSERVATION_MAX = 30


def observation_summary(items: list[dict]) -> dict:
    """Épisodes de coupure retenus en mode observation, pour task_runs : gravité qu'aurait eue l'alerte, et les
    OBSERVATION_MAX plus longs décrits en quelques champs (le journal reste petit : un cycle toutes les 5 minutes)."""
    by_sev: dict[str, int] = {}
    for it in items:
        by_sev[it["severity"]] = by_sev.get(it["severity"], 0) + 1
    longest = sorted(items, key=lambda it: -it["details"]["duree_min"])[:OBSERVATION_MAX]
    return {"gravites_hypothetiques": by_sev, "episodes": [
        {"vessel_id": it["details"]["navire"]["vessel_id"], "mmsi": it["details"]["navire"]["mmsi"],
         "dernier_message": it["details"]["dernier_message"], "duree_min": it["details"]["duree_min"],
         "reapparition": it["details"]["reapparition"] is not None, "comportement": it["details"]["comportement"],
         "messages_heure_precedente": it["details"]["messages_heure_precedente"],
         "derniere_position": it["details"]["derniere_position"], "gravite": it["severity"],
         "gravite_avant_declassement": it["details"].get("gravite_avant_declassement"),
         "point_collectif": it["details"].get("disparition_collective"),
         "bord_zone_km": it["details"].get("bord_zone_km")} for it in longest]}


# Test par injection de la règle des coupures (API /api/rules/ais_gap/selftest, taches.py coupures-injection)

class _Rollback(Exception):
    pass


def tranche_de(n: int, tranches) -> str | None:
    """Libellé de la tranche de messages par heure qui contient n (tranches : [(6, 9), (10, None)] donne « 6-9 »,
    « 10+ »), ou None."""
    for lo, hi in tranches:
        if n >= lo and (hi is None or n <= hi):
            return f"{lo}-{hi}" if hi is not None else f"{lo}+"
    return None


async def ais_gap_injection(c, day_start, rules: dict, essais: int = 5, heures=(11,), tranches=None) -> dict:
    """Coupures de trois heures simulées en effaçant les messages de navires réels, chacune dans une transaction
    annulée : la base n'est jamais modifiée. Pour chaque heure de coupure, les navires de classe A en route dans
    l'heure qui précède sont triés : historique insuffisant pour la règle (moins de min_prior_messages dans la fenêtre
    prior_window_min), pas de réapparition dans l'heure qui suit, dernière position près des côtes ou hors de la zone
    de réception fiable ; parmi les autres (au plus `essais`), la règle doit retrouver le silence (find_gaps, et
    projection restée dans la zone fiable). Vaut en mode observation comme en mode alertes : seule la détection est
    éprouvée, aucune alerte n'est écrite. `tranches` (27/10/2026) : au plus `essais` navires par tranche de messages
    dans l'heure et par heure de coupure, pris par identifiant (sans préférer les mieux reçus), taux par tranche ; sans
    tranches, les mieux reçus d'abord, comme avant."""
    g, z = rules["ais_gap"], rules["reception"]
    essais = max(1, min(int(essais), 50))
    trials, rejected, total_moving = [], {"historique_insuffisant": 0, "sans_reapparition": 0, "pres_des_cotes": 0,
                                          "hors_zone_de_reception_fiable": 0}, 0
    for h in heures:
        t_cut = day_start + timedelta(hours=int(h))
        t_back = t_cut + timedelta(hours=3)
        rows = await c.fetch(
            """SELECT p.vessel_id, v.mmsi, v.name,
                      count(*) FILTER (WHERE p.ts < $1::timestamptz
                                         AND p.ts >= $1::timestamptz - make_interval(mins => $4::int)) AS avant,
                      count(*) FILTER (WHERE p.ts >= $2::timestamptz) AS apres
               FROM positions p JOIN vessels v ON v.id = p.vessel_id
               WHERE p.ts >= $1::timestamptz - make_interval(mins => greatest($4::int, 60)) AND p.ts < $1::timestamptz
                     OR p.ts >= $2::timestamptz AND p.ts < $2::timestamptz + interval '1 hour'
               GROUP BY p.vessel_id, v.mmsi, v.name, v.ais_class, v.ship_type
               HAVING v.ais_class = 'A' AND NOT (coalesce(v.ship_type, '') = ANY($3::text[]))
                  AND avg(p.sog_kn) FILTER (WHERE p.ts < $1::timestamptz) >= 8""",
            t_cut, t_back, g["excluded_ship_types"], g["prior_window_min"])
        total_moving += len(rows)
        kept = 0
        par_tranche_h: dict = {}
        ordre = (sorted(rows, key=lambda r: r["vessel_id"]) if tranches else sorted(rows, key=lambda r: -r["avant"]))
        for cand in ordre:
            if cand["avant"] < g["min_prior_messages"] + 1:
                rejected["historique_insuffisant"] += 1
                continue
            if not cand["apres"]:
                rejected["sans_reapparition"] += 1
                continue
            where = await c.fetchrow(
                """SELECT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, p.geom, 10000)) AS cote,
                          EXISTS (SELECT 1 FROM reception_cells rc
                                  WHERE rc.cx = floor(ST_X(p.geom::geometry) / $3::float8)::int
                                    AND rc.cy = floor(ST_Y(p.geom::geometry) / $4::float8)::int) AS fiable
                   FROM positions p WHERE p.vessel_id = $1 AND p.ts < $2 ORDER BY p.ts DESC LIMIT 1""",
                cand["vessel_id"], t_cut, z["cell_deg_lon"], z["cell_deg_lat"])
            if where["cote"]:
                rejected["pres_des_cotes"] += 1
                continue
            if not where["fiable"]:
                rejected["hors_zone_de_reception_fiable"] += 1
                continue
            tr = tranche_de(int(cand["avant"]), tranches) if tranches else None
            if tranches:
                if tr is None or par_tranche_h.get(tr, 0) >= essais:
                    continue
                par_tranche_h[tr] = par_tranche_h.get(tr, 0) + 1
            elif kept >= essais:
                continue
            kept += 1
            result = {"mmsi": cand["mmsi"], "name": cand["name"], "coupure": f"{t_cut:%d/%m %H:%M}",
                      "messages_heure": int(cand["avant"]), **({"tranche": tr} if tr else {})}
            try:
                async with c.transaction():
                    result["messages_effaces"] = int((await c.execute(
                        "DELETE FROM positions WHERE vessel_id = $1 AND ts >= $2 AND ts < $3",
                        cand["vessel_id"], t_cut, t_back)).split()[-1])
                    found = [r for r in await find_gaps(c, t_cut - timedelta(hours=2), t_back + timedelta(hours=1), rules)
                             if r["vessel_id"] == cand["vessel_id"]]
                    hit = [r for r in found if r["projection_couverte"]]
                    result["detectee"] = bool(hit)
                    if hit:
                        result["duree_detectee_min"] = round(hit[0]["duration_min"])
                    elif found:
                        result["raison_probable"] = "route menant hors de la zone fiable : traitée comme une sortie de couverture"
                    else:
                        result["raison_probable"] = "autre filtre de la règle (vitesse, bord des données, distance à la côte)"
                    raise _Rollback()
            except _Rollback:
                pass
            trials.append(result)
    detected = sum(1 for t in trials if t.get("detectee"))
    out = {"mode": g.get("mode", "alertes"), "min_prior_messages": g["min_prior_messages"],
           "heures_de_coupure": list(heures), "duree_coupure_h": 3,
           "navires_en_route_classe_a": total_moving, "ecartes_avant_essai": rejected, "essais": len(trials),
           "detectees": detected, "taux_de_detection": round(detected / len(trials), 3) if trials else None}
    if tranches:
        out["par_tranche"] = {}
        for lo, hi in tranches:
            lab = tranche_de(lo, [(lo, hi)])
            xs = [t for t in trials if t.get("tranche") == lab]
            raisons: dict = {}
            for t in xs:
                if not t["detectee"]:
                    raisons[t["raison_probable"]] = raisons.get(t["raison_probable"], 0) + 1
            n = sum(1 for t in xs if t["detectee"])
            out["par_tranche"][lab] = {"essais": len(xs), "detectees": n,
                                       "taux": round(n / len(xs), 3) if xs else None, "manquees": raisons}
    return {**out, "details": trials}


# Navire d'une liste de surveillance dans nos eaux (WATCHLIST)

def passages(times: list[datetime], gap: timedelta) -> list[tuple[int, int]]:
    """Découpe une suite d'instants triés en passages : un silence de plus de `gap` ouvre un nouveau passage.
    Renvoie les indices (premier, dernier) de chaque passage."""
    out, first = [], 0
    for k in range(1, len(times)):
        if times[k] - times[k - 1] > gap:
            out.append((first, k - 1))
            first = k
    if times:
        out.append((first, len(times) - 1))
    return out


def stay(points: list[dict], stop_kn: float) -> dict:
    """Séjour d'un navire sur un passage (positions triées : ts, lon, lat, sog) : durée, part du temps à l'arrêt et
    lieu d'arrêt. La part se mesure sur les intervalles entre positions (l'allègement ne garde qu'un point toutes les
    dix minutes à l'arrêt, contre un par minute en route : compter les positions la sous estimerait)."""
    if len(points) < 2:
        return {"duree_h": 0.0, "arret_part": None, "lieu": None}
    total = (points[-1]["ts"] - points[0]["ts"]).total_seconds()
    stopped = sum((b["ts"] - a["ts"]).total_seconds() for a, b in zip(points, points[1:])
                  if a.get("sog") is not None and a["sog"] < stop_kn)
    halts = [p for p in points if p.get("sog") is not None and p["sog"] < stop_kn]
    place = None
    if halts:
        place = [round(statistics.median(p["lon"] for p in halts), 5), round(statistics.median(p["lat"] for p in halts), 5)]
    return {"duree_h": round(total / 3600, 1), "arret_part": round(stopped / total, 2) if total else None, "lieu": place}


def zone_names(points) -> list[str]:
    seen = []
    for lon, lat in points:
        for name, (a, b, c, d) in ZONES.items():
            if a <= lat <= c and b <= lon <= d and name not in seen:
                seen.append(name)
    return seen


def watch_severity(level: str, matched_by: str) -> str:
    """Gravité selon le niveau de signal ; un cran de moins si le navire n'est reconnu que par son MMSI."""
    s = WATCH_SEVERITY[level]
    return s if matched_by == "omi" else SEVERITIES[max(0, SEVERITIES.index(s) - 1)]


async def stay_offshore(c, points: list[dict], stop_kn: float, port_m: float) -> dict:
    """Part du séjour à l'arrêt hors port (règles 2026.10.43) : intervalles à l'arrêt dont la position de départ est à
    plus de port_m de la côte. Un port est à la côte ; un mouillage au large compte. Pondérée par le temps, comme
    arret_part. Sur le serveur du 21/10 (18 séjours en 30 jours) : 0 pour les transits et les escales à quai, 0,09
    (NOSTOS), 0,33 (SAFEEN BARONESS), 1,0 (SMYRTOS)."""
    total = (points[-1]["ts"] - points[0]["ts"]).total_seconds() if len(points) > 1 else 0
    arrets = [(a, (b["ts"] - a["ts"]).total_seconds()) for a, b in zip(points, points[1:])
              if a.get("sog") is not None and a["sog"] < stop_kn]
    if not total:
        return {"arret_hors_port_part": None, "arret_hors_port_h": None}
    loin = []
    if arrets:
        # Trait de côte subdivisé et indexé (land) ; petit rayon : un parcours d'index par point d'arrêt
        loin = [r["loin"] for r in await c.fetch(
            """SELECT NOT EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(
                        l.geom, ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography, $3)) AS loin
               FROM unnest($1::float8[], $2::float8[]) WITH ORDINALITY AS p(lon, lat, k) ORDER BY k""",
            [a["lon"] for a, _ in arrets], [a["lat"] for a, _ in arrets], float(port_m))]
    s = sum(dt for (_, dt), large in zip(arrets, loin) if large)
    return {"arret_hors_port_part": round(s / total, 2), "arret_hors_port_h": round(s / 3600, 1)}


async def stay_place(c, place) -> dict:
    """Lieu d'un arrêt : mouillage connu (zone de mouillage à moins de 1 km) et distance à la côte."""
    if not place:
        return {"mouillage_connu": None, "distance_cote_km": None}
    r = await c.fetchrow(
        """SELECT EXISTS (SELECT 1 FROM stationary_zones z WHERE ST_DWithin(z.geom, g, 1000)) AS mouillage,
                  (SELECT min(ST_Distance(l.geom, g)) FROM land l WHERE ST_DWithin(l.geom, g, 200000)) AS cote_m
           FROM (SELECT ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography AS g) p""", place[0], place[1])
    return {"mouillage_connu": r["mouillage"],
            "distance_cote_km": None if r["cote_m"] is None else round(r["cote_m"] / 1000, 1)}


def stay_text(s: dict) -> str:
    """Contexte d'un séjour prolongé, en clair."""
    txt = f"Séjour prolongé : {s['duree_h']:.0f} h dans nos eaux".replace(".", ",")
    if s.get("arret_part") is not None:
        txt += f", {round(100 * s['arret_part'])} % du temps à l'arrêt"
        if s.get("arret_hors_port_part") is not None and s["arret_part"] > 0:
            txt += f" dont {round(100 * s['arret_hors_port_part'])} % hors port"
    if s.get("lieu"):
        txt += ", au mouillage connu" if s.get("mouillage_connu") else ", hors de tout mouillage connu"
        if s.get("distance_cote_km") is not None:
            txt += f", à {s['distance_cote_km']} km des côtes".replace(".", ",")
    return txt


POS_SQL = ("SELECT ts, ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat, sog_kn AS sog FROM positions "
           "WHERE vessel_id = $1 AND ts >= $2::timestamptz AND ts < $3::timestamptz ORDER BY ts")


async def run_watchlist(c, start, end, rules: dict) -> dict:
    """Une alerte par passage dans nos eaux d'un navire des listes (niveaux fort, sanctionné, flotte fantôme,
    suspect GUR). Un passage prolongé met à jour son alerte : pas une alerte par position."""
    w = rules["watchlist"]
    gap = timedelta(hours=w["passage_gap_h"])
    vessels = await c.fetch(
        """SELECT w.vessel_id, w.level, w.matched_by, w.entries, v.mmsi, v.imo, v.name, v.flag, v.ship_type, v.length_m
           FROM vessel_watch w JOIN vessels v ON v.id = w.vessel_id WHERE w.level = ANY($1::text[])""",
        w["niveaux"])
    items = []
    for v in vessels:
        pos = await c.fetch(POS_SQL, v["vessel_id"], start, end)
        if not pos:
            continue
        prior = await c.fetch("SELECT rule_key, (details->>'debut')::timestamptz AS debut FROM alerts "
                              "WHERE type = 'WATCHLIST' AND rule_key LIKE $1 AND (details->>'fin')::timestamptz >= $2",
                              f"{v['vessel_id']}:%", pos[0]["ts"] - gap)
        former = [{"mmsi": e["mmsi"], "pavillon": flag_of(e["mmsi"]), "source": e["source"]}
                  for e in v["entries"] if e.get("mmsi") and e["mmsi"] != v["mmsi"]]
        for i, j in passages([p["ts"] for p in pos], gap):
            debut, fin = pos[i]["ts"], pos[j]["ts"]
            same = [p for p in prior if p["debut"] <= fin + gap] if i == 0 else []
            key = same[0]["rule_key"] if same else f"{v['vessel_id']}:{stamp(debut)}"
            if same and same[0]["debut"] < debut:
                debut = same[0]["debut"]
            context = []
            if v["matched_by"] != "omi":
                context.append("Reconnu par le MMSI seul : correspondance moins sûre qu'avec l'OMI")
            if former:
                context.append("MMSI différent sur la liste : " + ", ".join(
                    f"{f['mmsi']} ({f['pavillon'] or 'pavillon inconnu'})" for f in former))
            # Séjour mesuré depuis le vrai début du passage : la fenêtre des règles (24 h) le tronquerait
            seg = pos[i:j + 1] if debut >= pos[i]["ts"] else await c.fetch(
                POS_SQL, v["vessel_id"], debut, fin + timedelta(microseconds=1))
            sejour = stay([dict(p) for p in seg], w["arret_kn"])
            if sejour["duree_h"] >= w["sejour_prolonge_h"]:
                sejour.update(await stay_place(c, sejour["lieu"]))
                sejour.update(await stay_offshore(c, [dict(p) for p in seg], w["arret_kn"], w["hors_port_m"]))
                context.append(stay_text(sejour))
            else:
                sejour = None
            details = {
                "navire": {"vessel_id": v["vessel_id"], "mmsi": v["mmsi"], "imo": v["imo"], "name": v["name"],
                           "pavillon": v["flag"], "ship_type": v["ship_type"], "length_m": _finite(v["length_m"])},
                "niveau": v["level"], "reconnu_par": v["matched_by"], "sources": v["entries"],
                "autres_mmsi_sur_liste": former,
                "debut": debut.isoformat(), "fin": fin.isoformat(), "positions": j - i + 1,
                "zones": zone_names((p["lon"], p["lat"]) for p in pos[i:j + 1]),
                "entree": [pos[i]["lon"], pos[i]["lat"]], "derniere_position": [pos[j]["lon"], pos[j]["lat"]],
                "sejour": sejour,
                "contexte": context,
                "motif": "Navire d'une liste de surveillance présent dans une zone couverte",
            }
            items.append({"key": key, "severity": watch_severity(v["level"], v["matched_by"]), "event_time": debut,
                          "lon": pos[j]["lon"], "lat": pos[j]["lat"], "details": details, "vessels": [v["vessel_id"]]})
    saved = await save_alerts(c, "WATCHLIST", items, rule_version=rules["version"], rules=rules)
    return {"navires_des_listes": len(vessels), "passages": len(items), **saved}


# Changement d'identité (IDENTITY_CHANGE)

def normalize_name(name: str | None) -> str | None:
    if not name:
        return None
    s = re.sub(r"[^A-Z0-9]+", " ", name.upper()).strip()
    return s or None


def placeholder_mmsi(mmsi: int) -> bool:
    """MMSI générique : code pays suivi de six zéros (227000000, partagé par des bâtiments de la Marine nationale)."""
    return mmsi % 1_000_000 == 0


def military(names, ship_type: str | None, ship_type_code: int | None, mmsi: int, p: dict) -> str | None:
    """Raison de tenir le navire pour un bâtiment militaire, ou None. Indices : type AIS déclaré (35, Military),
    MMSI de la liste `mmsi`, nom commençant par un préfixe de marine (HMS, FS, USS…), ou nom réduit à un numéro de
    coque (« 101 », émis avant le nom de baptême, comme 101 puis HMS CATTISTOCK)."""
    if ship_type_code == 35 or ship_type == "Military":
        return "type militaire déclaré"
    if mmsi in set(p.get("mmsi", [])):
        return "MMSI de la Marine"
    prefixes = set(p.get("prefixes_nom", []))
    for n in names:
        words = (normalize_name(n) or "").split()
        if words and words[0] in prefixes and len(words) > 1:
            return f"préfixe {words[0]}"
    for n in names:
        if re.fullmatch(r"\d{1,4}", normalize_name(n) or ""):
            return "numéro de coque"
    return None


def name_changes(rows: list[dict], now: datetime, confirmation: timedelta) -> list[dict]:
    """Nouveaux noms d'un navire (lignes de vessel_identities d'un même MMSI, triées par première vue).

    Un nom nouveau n'est retenu qu'une fois confirmé : apparu depuis au moins `confirmation`, et sans qu'aucun nom
    antérieur ait été revu depuis. Un navire qui alterne entre deux noms (MUTIN et FS MUTIN, deux émetteurs) n'est
    donc jamais signalé, ni au premier basculement ni aux suivants."""
    out = []
    for k, b in enumerate(rows):
        new = normalize_name(b["name"])
        before = [a for a in rows[:k] if normalize_name(a["name"])]
        if not new or not before or new in {normalize_name(a["name"]) for a in before}:
            continue
        if b["first_seen"] > now - confirmation:
            continue
        if any(a["last_seen"] > b["first_seen"] and normalize_name(a["name"]) != new for a in rows if a is not b
               and a["first_seen"] < b["first_seen"]):
            continue                      # un nom antérieur est revu après le nouveau : alternance
        out.append({"identity_id": b["id"], "ancien": before[-1], "nouveau": b})
    return out


def imo_changes(rows: list[dict], now: datetime, confirmation: timedelta) -> list[dict]:
    """Même OMI sous un autre MMSI. `rows` : identités portant un OMI (vessel_id, mmsi, imo, first_seen, last_seen).
    Pour chaque OMI, le premier MMSI vu est la référence ; chaque autre MMSI confirmé est un changement."""
    by_imo: dict[int, dict[int, dict]] = {}
    for r in rows:
        if placeholder_mmsi(r["mmsi"]):
            continue
        cur = by_imo.setdefault(r["imo"], {}).get(r["vessel_id"])
        if cur is None:
            by_imo[r["imo"]][r["vessel_id"]] = dict(r)
        else:
            cur["first_seen"] = min(cur["first_seen"], r["first_seen"])
            cur["last_seen"] = max(cur["last_seen"], r["last_seen"])
    out = []
    for imo, vs in by_imo.items():
        if len(vs) < 2:
            continue
        ordered = sorted(vs.values(), key=lambda r: r["first_seen"])
        for later in ordered[1:]:
            if later["first_seen"] > now - confirmation:
                continue
            earlier = [r for r in ordered if r["first_seen"] < later["first_seen"]]
            out.append({"imo": imo, "nouveau": later, "anciens": earlier,
                        "simultane": any(r["last_seen"] > later["first_seen"] for r in earlier),
                        "pavillon_change": any(flag_of(r["mmsi"]) != flag_of(later["mmsi"]) for r in earlier)})
    return out


def imo_severity(ch: dict, listed: bool, mil: str | None) -> str:
    """Gravité d'un même OMI sous un autre MMSI. Un navire des listes en cause garde la gravité élevée ; un bâtiment
    militaire reçoit la gravité minimale ; même pays des deux côtés : faible (moyenne en cas d'usage simultané) ;
    changement de pavillon : élevée."""
    if listed:
        return "elevee"
    if mil:
        return "faible"
    if not ch["pavillon_change"]:
        return "moyenne" if ch["simultane"] else "faible"
    return "elevee"


async def run_identity(c, start, end, rules: dict) -> dict:
    """Nouveau nom, ou même OMI sous un autre MMSI (avec ou sans changement de pavillon), d'après vessel_identities.
    Seules les identités apparues depuis `start` sont examinées ; les MMSI génériques et ceux de la liste
    `mmsi_ignores` sont écartés."""
    p = rules["identite"]
    conf = timedelta(minutes=p["confirmation_min"])
    ignored = set(p["mmsi_ignores"])
    rows = await c.fetch(
        """SELECT i.id, i.vessel_id, v.mmsi, v.ais_class, v.ship_type, v.ship_type_code, i.name, i.imo, i.callsign, i.flag, i.first_seen, i.last_seen,
                  i.messages
           FROM vessel_identities i JOIN vessels v ON v.id = i.vessel_id
           WHERE i.vessel_id IN (SELECT vessel_id FROM vessel_identities WHERE first_seen >= $1 AND first_seen < $2)
           ORDER BY i.vessel_id, i.first_seen""", start, end)
    by_vessel: dict[int, list[dict]] = {}
    for r in rows:
        if not placeholder_mmsi(r["mmsi"]) and r["mmsi"] not in ignored:
            by_vessel.setdefault(r["vessel_id"], []).append(dict(r))
    imo_rows = await c.fetch(
        """SELECT i.vessel_id, v.mmsi, v.ship_type, v.ship_type_code, i.name, i.imo, i.first_seen, i.last_seen
           FROM vessel_identities i
           JOIN vessels v ON v.id = i.vessel_id
           WHERE i.imo IN (SELECT imo FROM vessel_identities WHERE imo IS NOT NULL AND first_seen >= $1 AND first_seen < $2)""",
        start, end)
    watched = {r["vessel_id"] for r in await c.fetch("SELECT vessel_id FROM vessel_watch WHERE level <> 'autre_risque'")}

    async def last_position(vid):
        return await c.fetchrow("SELECT ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat FROM positions "
                                "WHERE vessel_id = $1 AND ts <= $2::timestamptz ORDER BY ts DESC LIMIT 1", vid, end)

    def ident(r):
        return {"mmsi": r["mmsi"], "name": r.get("name"), "imo": r.get("imo"), "pavillon": flag_of(r["mmsi"]),
                "premiere_vue": r["first_seen"].isoformat(), "derniere_vue": r["last_seen"].isoformat()}

    items, skipped = [], 0
    for vid, ids in by_vessel.items():
        for ch in name_changes(ids, end, conf):
            if ch["nouveau"]["first_seen"] < start:
                continue
            pos = await last_position(vid)
            if pos is None:
                skipped += 1
                continue
            b, a = ch["nouveau"], ch["ancien"]
            mil = military([x["name"] for x in ids], b["ship_type"], b["ship_type_code"], b["mmsi"], p["militaire"])
            severity = ("elevee" if vid in watched else "faible" if mil or b["ais_class"] == "B" else "moyenne")
            context = ["Navire d'une liste de surveillance"] if vid in watched else []
            if mil and vid not in watched:
                context.append(f"Bâtiment militaire probable ({mil}) : gravité minimale")
            details = {"changement": "nom", "navire": {"vessel_id": vid, "mmsi": b["mmsi"], "name": b["name"]},
                       "ancien_nom": a["name"], "nouveau_nom": b["name"], "debut": b["first_seen"].isoformat(),
                       "identites": [ident(x) for x in ids],
                       "militaire": mil, "contexte": context,
                       "motif": "Le navire émet sous un nouveau nom, confirmé dans la durée"}
            items.append({"key": f"nom:{vid}:{b['id']}", "severity": severity, "event_time": b["first_seen"],
                          "lon": pos["lon"], "lat": pos["lat"], "details": details, "vessels": [vid]})
    # OMI à clé fausse ou de remplissage (1234567) : jamais un identifiant, aucun rapprochement de MMSI (identités
    # enregistrées avant le contrôle encore en base)
    imo_rows = [r for r in imo_rows if valid_imo(r["imo"])]
    for ch in imo_changes([dict(r) for r in imo_rows if r["mmsi"] not in ignored], end, conf):
        new = ch["nouveau"]
        if new["first_seen"] < start:
            continue
        pos = await last_position(new["vessel_id"])
        if pos is None:
            skipped += 1
            continue
        context = []
        if ch["pavillon_change"]:
            context.append("Changement de pavillon : " + ", ".join(
                f"{flag_of(r['mmsi']) or '?'} (MMSI {r['mmsi']})" for r in ch["anciens"])
                + f" puis {flag_of(new['mmsi']) or '?'} (MMSI {new['mmsi']})")
        if ch["simultane"]:
            context.append("L'ancien MMSI émet encore après l'apparition du nouveau : usage simultané d'un même OMI")
        involved = [r["vessel_id"] for r in ch["anciens"]] + [new["vessel_id"]]
        listed = any(v in watched for v in involved)
        names = [r["name"] for r in imo_rows if r["imo"] == ch["imo"]]
        mil = military(names, new["ship_type"], new["ship_type_code"], new["mmsi"], p["militaire"])
        severity = imo_severity(ch, listed, mil)
        if listed:
            context.append("Navire d'une liste de surveillance")
        elif mil:
            context.append(f"Bâtiment militaire probable ({mil}) : gravité minimale")
        elif not ch["pavillon_change"]:
            context.append("Ancien et nouveau MMSI du même pays : réimmatriculation administrative probable")
        details = {"changement": "omi_autre_mmsi", "omi": ch["imo"],
                   "navire": {"vessel_id": new["vessel_id"], "mmsi": new["mmsi"]},
                   "debut": new["first_seen"].isoformat(), "pavillon_change": ch["pavillon_change"],
                   "usage_simultane": ch["simultane"], "militaire": mil,
                   "identites": [ident(r) for r in ch["anciens"] + [new]], "contexte": context,
                   "motif": "Le même numéro OMI apparaît sous un autre MMSI"}
        items.append({"key": f"omi:{ch['imo']}:{new['vessel_id']}", "severity": severity,
                      "event_time": new["first_seen"], "lon": pos["lon"], "lat": pos["lat"], "details": details,
                      "vessels": involved})
    saved = await save_alerts(c, "IDENTITY_CHANGE", items, rule_version=rules["version"], rules=rules)
    return {"changements": len(items), "sans_position": skipped, **saved}


MOTIF_OMI_FACTICE = ("numéro OMI invalide ou de remplissage ({omi}) : jamais un identifiant, aucun changement "
                     "d'identité à en tirer")


def classer_omi_factices(conn, simulation: bool = False) -> dict:
    """Alertes IDENTITY_CHANGE passées fondées sur un OMI invalide ou de remplissage : classées avec leur motif si
    personne n'a encore décidé (statut « nouvelle ») ; une décision d'un opérateur reste, le motif est noté à côté.
    Connexion psycopg ; `simulation` : tout est annulé à la fin."""
    rows = conn.execute("SELECT id, status, (details->>'omi')::bigint FROM alerts WHERE type = 'IDENTITY_CHANGE' "
                        "AND details ? 'omi' ORDER BY id").fetchall()
    cibles = [(a, st, omi) for a, st, omi in rows if omi is not None and valid_imo(omi) is None]
    quand = datetime.now(timezone.utc).isoformat()
    classees, gardees = [], []
    import psycopg
    out = {}
    with conn.transaction():
        for a, st, omi in cibles:
            motif = MOTIF_OMI_FACTICE.format(omi=omi)
            note = {"le": quand, "motif": motif}
            if st == "nouvelle":
                conn.execute("UPDATE alerts SET status = 'classee', details = details || jsonb_build_object("
                             "'omi_factice', %s::jsonb) WHERE id = %s", (json.dumps(note), a))
                conn.execute("INSERT INTO alert_actions (alert_id, action, motif, note, author) VALUES "
                             "(%s, 'classer', 'faux_positif', %s, 'MARS C2')", (a, motif))
                classees.append(a)
            else:                                     # décision d'un opérateur : elle reste
                conn.execute("UPDATE alerts SET details = details || jsonb_build_object('omi_factice', %s::jsonb) "
                             "WHERE id = %s", (json.dumps({**note, "decision_gardee": st}), a))
                gardees.append(a)
        out = {"examinees": len(rows), "fondees_sur_un_omi_factice": [a for a, _s, _o in cibles],
               "classees": classees, "decisions_gardees": gardees}
        if simulation:
            raise psycopg.Rollback()                  # rien n'est écrit
    return {**out, **({"simulation": "rien n'est écrit : transaction annulée"} if simulation else {})}


# Évaluation continue

async def run_continuous(c, now: datetime, rules: dict) -> dict:
    """Un cycle des règles sur le flux en direct : fenêtre glissante, bornée par la dernière position reçue (une
    collecte en retard ne fait pas croire à des silences). Durée de chaque étape dans `durees_s` (task_runs)."""
    import time
    p = rules["continu"]
    start = now - timedelta(hours=p["fenetre_h"])
    watermark = await c.fetchval("SELECT max(ts) FROM positions WHERE ts <= $1::timestamptz", now)
    out = {"fenetre": [start.isoformat(), watermark.isoformat() if watermark else None]}
    if watermark is None or watermark < start:
        return {**out, "ignore": "aucune position dans la fenêtre"}
    durees: dict[str, float] = {}

    async def step(name, coro):
        from mars import traces
        t = time.perf_counter()
        with traces.span(f"regles.{name}") as sp:
            r = await coro
            if isinstance(r, dict):                     # alertes créées, mises à jour, retirées, épisodes
                sp.set_attributes({f"regles.{k}": v for k, v in r.items() if isinstance(v, (int, float))})
        durees[name] = round(time.perf_counter() - t, 2)
        return r
    from mars import controle
    coupees = {r for r in ("rendezvous", "ais_gap", "watchlist", "identite") if controle.regle_coupee(rules, r)}
    if "rendezvous" in coupees:                        # règle coupée au panneau : pas même calculée (lot P)
        out["rendezvous"] = {"mode": "coupe"}
    elif await c.fetchval("SELECT EXISTS (SELECT 1 FROM land)"):
        out["rendezvous"] = await step("rendezvous", run_rendezvous(c, start, watermark, rules))
    else:
        out["rendezvous"] = "trait de côte absent : lancer scripts/build_masks.py"
    if "ais_gap" in coupees:
        out["ais_gap"] = {"mode": "coupe"}
    elif await c.fetchval("SELECT EXISTS (SELECT 1 FROM reception_cells)"):
        out["ais_gap"] = await step("ais_gap", run_ais_gap(c, start, watermark, rules))
    else:
        out["ais_gap"] = "zone de réception absente : lancer scripts/build_masks.py"
    out["watchlist"] = ({"mode": "coupe"} if "watchlist" in coupees
                        else await step("watchlist", run_watchlist(c, start, now, rules)))
    out["identite"] = ({"mode": "coupe"} if "identite" in coupees else
                       await step("identite", run_identity(c, now - timedelta(hours=p["identite_fenetre_h"]), now, rules)))
    if (rules.get("_controle") or {}).get("empreinte"):
        out["controle_empreinte"] = rules["_controle"]["empreinte"]
    out["durees_s"] = durees
    return out
