"""Tip and cue (lot T1) : un signal suspect (le tip) crée une demande d'observation (le cue).

Chaîne : tips relevés dans les alertes et les épisodes de coupure observés (collecter), regroupés par navire ou par
zone (creer), position prédite à l'heure de chaque passage Sentinel 1 candidat et passage retenu (planifier), points
de déclenchement remis à la sélection Sentinel du passage (points_passage : même session de travailleur, mêmes
plafonds, même budget), résultat cherché dans le cercle d'incertitude et rattaché aux alertes d'origine (evaluer).
Section tip_and_cue de config/rules.yaml ; docs/tip_and_cue.md. Les fonctions de calcul (predire, centres_fenetres,
categorie, ajustement, rejeu) sont pures et couvertes par tests/test_tip_and_cue.py.
"""
import json
import math
from datetime import datetime, timedelta, timezone

from mars import traces

ACTIFS = ("en_attente", "planifiee")
SEVERITES = ["faible", "moyenne", "elevee", "critique"]
MASQUES_IGNORES = ("terre", "eolienne probable", "echo fixe", "nuages")
TERRE_M = 6371000.0


def actif(rules: dict) -> bool:
    return bool((rules.get("tip_and_cue") or {}).get("actif"))


def missions_retenues(rules: dict) -> tuple[list[str], list[str]]:
    """Missions dont les passages peuvent servir une demande, et motifs des missions écartées (lot P : Sentinel 1 ou
    2 en pause, Sentinel 2 coupé)."""
    from mars import controle, sentinel2
    garde, motifs = [], []
    for mission, source in (("S1", "sentinel1"), ("S2", "sentinel2")):
        if mission == "S2" and not sentinel2.actif(rules):
            continue
        m = controle.en_pause(rules, source)
        if m:
            motifs.append(m)
        else:
            garde.append(mission)
    return garde, motifs


def _rang(g: str | None) -> int:
    return SEVERITES.index(g) if g in SEVERITES else -1


# Calculs purs

def distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * TERRE_M * math.asin(math.sqrt(h))


def destination(lon: float, lat: float, cap_deg: float, dist_m: float) -> tuple[float, float]:
    """Point atteint en suivant le cap `cap_deg` sur `dist_m` (grand cercle)."""
    d, c = dist_m / TERRE_M, math.radians(cap_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(c))
    l2 = l1 + math.atan2(math.sin(c) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return (math.degrees(l2) + 540) % 360 - 180, math.degrees(p2)


def quantiles_erreur(c: dict, sog: float, h: float) -> list[float]:
    """Quantiles de l'erreur de l'estime (m) aux niveaux `niveaux` (règles 2026.10.45, docs/trajectoire.md), pour la
    tranche de vitesse déclarée (bornes basses `vitesses_kn`) et l'horizon h : interpolés entre les horizons mesurés,
    de `rayon_min_m` à 0 h ; au delà du dernier horizon, le dernier."""
    vit = c["vitesses_kn"]
    i = max([k for k, v in enumerate(vit) if (sog or 0) >= v] or [0])
    hs, Q = [float(x) for x in c["horizons_h"]], c["quantiles_m"][i]
    h, r0 = abs(h), float(c["rayon_min_m"])
    if h >= hs[-1]:
        return [float(q) for q in Q[-1]]
    if h <= hs[0]:
        return [r0 + (float(q) - r0) * h / hs[0] for q in Q[0]]
    j = next(k for k in range(1, len(hs)) if h <= hs[k])
    f = (h - hs[j - 1]) / (hs[j] - hs[j - 1])
    return [float(a) + (float(b) - float(a)) * f for a, b in zip(Q[j - 1], Q[j])]


def rayon_appris(c: dict, sog: float, h: float) -> float:
    """Cercle appris : rayon qui contient la part `contenance` (80 %) des positions réelles, interpolé entre niveaux."""
    return _interp(c["contenance"], [float(x) for x in c["niveaux"]], quantiles_erreur(c, sog, h))


def contenance_estimee(c: dict, sog: float, h: float, rayon_m: float) -> float:
    """Part des navires réels attendue dans un cercle de `rayon_m` autour de l'estime (fonction de répartition de
    l'erreur, interpolée entre les quantiles mesurés ; sous le premier, proportionnelle ; au delà du dernier niveau
    mesuré, ce niveau, par prudence)."""
    niv, q = [float(x) for x in c["niveaux"]], quantiles_erreur(c, sog, h)
    if rayon_m <= q[0]:
        return niv[0] * max(rayon_m, 0.0) / q[0] if q[0] > 0 else niv[0]
    if rayon_m >= q[-1]:
        return niv[-1]
    return _interp(rayon_m, q, niv)


def _interp(x: float, xs: list[float], ys: list[float]) -> float:
    if x <= xs[0]:
        return ys[0]
    for k in range(1, len(xs)):
        if x <= xs[k]:
            return ys[k - 1] + (ys[k] - ys[k - 1]) * (x - xs[k - 1]) / ((xs[k] - xs[k - 1]) or 1.0)
    return ys[-1]


def sous_contenance(pr: dict, tc: dict) -> bool:
    """Garde de probabilité (règles 2026.10.45) : passage écarté si la contenance estimée de ses fenêtres est sous
    `contenance_min` (le suivant, plus lointain, ne ferait pas mieux : on garde le plus proche au dessus du seuil)."""
    seuil = (tc["prediction"].get("cercle_appris") or {}).get("contenance_min")
    return seuil is not None and pr.get("contenance") is not None and pr["contenance"] < float(seuil)


def rayon_couvrable(s: dict, fenetres_max: int) -> float:
    """Plus grand rayon que fenetres_max fenêtres satellites couvrent (centres_fenetres) : au delà, la demande serait
    « sans passage ». Avec 4 fenêtres de 24 km (cœur de 10 km), 20 km."""
    bas, haut = 0.0, 200_000.0
    for _ in range(40):
        m = (bas + haut) / 2
        bas, haut = (m, haut) if len(centres_fenetres(0.0, 48.0, m, s)) <= fenetres_max else (bas, m)
    return bas


def fenetres_demande(pr: dict, s: dict, tc: dict) -> list[list[float]]:
    """Fenêtres satellites d'une prédiction. Cercle appris plafonné (règles 2026.10.44) : un cercle d'estime trop grand
    pour les fenêtres permises est ramené au rayon qu'elles couvrent (20 km avec 4 fenêtres) plutôt que d'abandonner le
    passage ; le rayon appris reste noté (`rayon_appris_m`). Mesuré sur la période tenue à l'écart : le navire est
    trouvé bien plus souvent ainsi (docs/trajectoire.md)."""
    centres = centres_fenetres(pr["lon"], pr["lat"], pr["rayon_m"], s)
    c = tc["prediction"].get("cercle_appris") or {}
    if len(centres) > tc["fenetres_max_par_demande"] and c.get("plafonner") and pr.get("mode") == "estime":
        r = rayon_couvrable(s, tc["fenetres_max_par_demande"])
        pr["rayon_appris_m"], pr["rayon_m"] = pr["rayon_m"], r
        if pr.get("horizon_h") is not None:                  # contenance du cercle réellement couvert
            pr["contenance"] = round(contenance_estimee(c, pr["sog_kn"], pr["horizon_h"], r), 3)
        centres = centres_fenetres(pr["lon"], pr["lat"], r, s)
    return centres


def predire(lon: float, lat: float, t_pos: datetime, sog: float | None, cog: float | None, t: datetime, p: dict,
            navire: bool = True, a_l_arret: bool = False) -> dict:
    """Position prédite à l'instant t et cercle d'incertitude. Navire en route : estime (dernier cap, dernière vitesse),
    rayon de départ plus une part de la distance parcourue, borné ; à l'arrêt ou au mouillage : position fixe, petit
    rayon ; sans navire (détection VIIRS, point de la carte) : position du tip, rayon fixe."""
    if not navire:
        return {"lon": lon, "lat": lat, "rayon_m": float(p["rayon_point_m"]), "mode": "point"}
    if a_l_arret or sog is None or cog is None or sog < p["arret_kn"]:
        return {"lon": lon, "lat": lat, "rayon_m": float(p["rayon_arret_m"]), "mode": "arret"}
    dt_h = (t - t_pos).total_seconds() / 3600
    parcours = sog * 1852.0 * abs(dt_h)
    plon, plat = destination(lon, lat, cog if dt_h >= 0 else (cog + 180) % 360, parcours)
    if p.get("cercle_appris"):
        c = p["cercle_appris"]
        rayon = rayon_appris(c, sog, dt_h)
        return {"lon": plon, "lat": plat, "rayon_m": float(rayon), "mode": "estime", "sog_kn": sog, "horizon_h": dt_h,
                "contenance": round(contenance_estimee(c, sog, dt_h, rayon), 3)}
    rayon = min(p["rayon_depart_m"] + p["part_vitesse_par_h"] * parcours, p["rayon_max_m"])   # repli d'avant 2026.10.44
    return {"lon": plon, "lat": plat, "rayon_m": float(rayon), "mode": "estime"}


def dans_zones(lon: float, lat: float) -> bool:
    """Point dans l'une de nos zones collectées (mars/ais/live.py) : au delà, aucun AIS ne confirmerait l'estime."""
    from mars.ais.live import ZONES
    return any(a <= lat <= c and b <= lon <= d for a, b, c, d in ZONES.values())


def predire_bornee(base: tuple, t: datetime, quand: datetime, p: dict, navire: bool = True,
                   a_l_arret: bool = False) -> dict:
    """predire, bornée pour un navire muet (aucune position depuis muet_min à l'instant `quand`) : lent (sous lent_kn)
    ou à l'arrêt, position fixe et petit cercle ; en route, l'estime ne vaut que estime_max_h après la dernière
    position et dans nos zones collectées, sinon la prédiction porte `hors_portee` (sa raison)."""
    lon, lat, ts, sog, cog = base
    muet = navire and quand - ts > timedelta(minutes=p["muet_min"])
    lent = sog is None or sog < p["lent_kn"]
    pr = predire(lon, lat, ts, sog, cog, t, p, navire=navire, a_l_arret=a_l_arret or (muet and lent))
    if muet and pr["mode"] == "estime":
        h = (t - ts).total_seconds() / 3600
        if h > p["estime_max_h"]:
            pr["hors_portee"] = (f"muet depuis le {ts:%d/%m %H:%M} à {sog:.0f} nœuds, passage du {t:%d/%m %H:%M} "
                                 f"{h:.0f} h plus tard, au delà des {p['estime_max_h']:g} h de l'estime")
        elif not dans_zones(pr["lon"], pr["lat"]):
            pr["hors_portee"] = (f"muet depuis le {ts:%d/%m %H:%M} à {sog:.0f} nœuds, position prédite hors de nos zones "
                                 f"collectées au passage du {t:%d/%m %H:%M}")
    return pr


def centres_fenetres(lon: float, lat: float, rayon_m: float, s: dict) -> list[list[float]]:
    """Centres des fenêtres de 24 km (mécanique du lot C3) qui couvrent le cercle : une seule si le cercle tient dans
    le cœur d'une fenêtre (sa marge exclue), sinon une grille au pas du cœur, sans les fenêtres hors du cercle."""
    demi = max(s["interet_rayon_m"] + s["fenetre_marge_m"], s["fenetre_cote_min_m"] / 2)
    coeur = demi - s["fenetre_marge_m"]
    if rayon_m <= coeur:
        return [[round(lon, 5), round(lat, 5)]]
    n = math.ceil(rayon_m / coeur)              # cases de 2 × cœur sur un diamètre de 2 × rayon
    out = []
    for i in range(n):
        for j in range(n):
            dx, dy = (i - (n - 1) / 2) * 2 * coeur, (j - (n - 1) / 2) * 2 * coeur
            # point de la case le plus proche du centre du cercle
            px, py = max(abs(dx) - coeur, 0), max(abs(dy) - coeur, 0)
            if math.hypot(px, py) <= rayon_m:
                clon = lon + dx / (111_320.0 * math.cos(math.radians(lat)))
                out.append([round(clon, 5), round(lat + dy / 110_574.0, 5)])
    return out


def categorie(detections: list[dict], attendus: set, navire: bool) -> tuple[str, dict | None]:
    """Résultat d'une demande d'après les détections du cercle, de la plus proche à la plus lointaine : écho apparié à
    un navire attendu (présent, AIS cohérent), écho sans AIS (présent mais muet : preuve forte), écho non évaluable,
    sinon absent. Sans navire attendu (VIIRS, point), un écho apparié à n'importe quel navire AIS est « apparié ».
    Échos de terre, d'éolienne ou fixes ignorés."""
    utiles = [d for d in detections if d.get("mask_reason") not in MASQUES_IGNORES]
    for d in utiles:
        if d.get("matched_vessel_id") is not None and (d["matched_vessel_id"] in attendus or not navire):
            return "present_apparie", d
    for d in utiles:
        if d.get("matched_vessel_id") is None and d.get("mask_reason") is None:
            return "present_sans_ais", d
    for d in utiles:
        if d.get("mask_reason") == "non_evaluable":
            return "non_evaluable", d
    return "absent", None


def ajustement(tip: str, resultat: str, gravite: str, table: dict) -> str | None:
    """Nouvelle gravité de l'alerte d'origine (table versionnée), ou None si elle ne change pas."""
    cible = (table.get(tip) or {}).get(resultat)
    return cible if cible in SEVERITES and cible != gravite else None


# Demandes

def creer(conn, rules: dict, now: datetime, *, cle: str, tip: str, lon: float, lat: float, position_le: datetime,
          vessel_id: int | None = None, sog: float | None = None, cog: float | None = None,
          alerte_id: int | None = None, detection_viirs_id: int | None = None, motif: str | None = None,
          auteur: str | None = None, trace_origine: str | None = None) -> dict:
    """Crée la demande d'un tip, ou rattache le tip à la demande active du même navire (ou, sans navire, d'un point à
    moins de regroupement_m). Une clé déjà vue ne fait rien (alerte mise à jour en place, épisode revu à chaque
    cycle). Retourne {"demande": id, "nouvelle": bool} ou {"deja": id}."""
    tc = rules["tip_and_cue"]
    vu = conn.execute("SELECT demande_id FROM demandes_origines WHERE cle = %s", (cle,)).fetchone()
    if vu:
        return {"deja": vu[0]}
    prio = int(tc["tips"][tip]["priorite"]) + bonus_priorite(conn, rules, lon, lat, vessel_id)
    with traces.span("tip_and_cue.demande", liens=[trace_origine], **{"tip.type": tip, "tip.cle": cle,
                                                                       "tip.navire": vessel_id}) as sp:
        if vessel_id is not None:
            row = conn.execute("SELECT id FROM demandes_observation WHERE vessel_id = %s AND etat = ANY(%s) "
                               "ORDER BY id DESC LIMIT 1", (vessel_id, list(ACTIFS))).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM demandes_observation WHERE vessel_id IS NULL AND etat = ANY(%s) AND ST_DWithin("
                "position, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s) ORDER BY id DESC LIMIT 1",
                (list(ACTIFS), lon, lat, float(tc["regroupement_m"]))).fetchone()
        if row:
            did, nouvelle = row[0], False
            # Position plus récente : remplace la précédente, et la demande est replanifiée tant qu'aucune analyse
            # ne la porte
            conn.execute(
                """UPDATE demandes_observation SET priorite = greatest(priorite, %(p)s), mis_a_jour_le = now(),
                          position = CASE WHEN %(t)s > position_le THEN ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s),
                                     4326)::geography ELSE position END,
                          sog_kn = CASE WHEN %(t)s > position_le THEN %(sog)s ELSE sog_kn END,
                          cog_deg = CASE WHEN %(t)s > position_le THEN %(cog)s ELSE cog_deg END,
                          etat = CASE WHEN %(t)s > position_le AND analysis_id IS NULL THEN 'en_attente' ELSE etat END,
                          position_le = greatest(position_le, %(t)s)
                   WHERE id = %(id)s""",
                {"p": prio, "t": position_le, "lon": lon, "lat": lat, "sog": sog, "cog": cog, "id": did})
        else:
            did, nouvelle = conn.execute(
                """INSERT INTO demandes_observation (tip, priorite, vessel_id, position, position_le, sog_kn, cog_deg,
                                                     expire_le, motif, auteur, trace_id, traceparent)
                   VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s, %s, %s, %s,
                           %s) RETURNING id""",
                (tip, prio, vessel_id, lon, lat, position_le, sog, cog, now + timedelta(hours=tc["expiration_h"]),
                 motif, auteur, traces.trace_id(), traces.traceparent())).fetchone()[0], True
        conn.execute("INSERT INTO demandes_origines (cle, demande_id, tip, priorite, alerte_id, detection_viirs_id, "
                     "motif, trace_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (cle) DO NOTHING",
                     (cle, did, tip, prio, alerte_id, detection_viirs_id, motif, trace_origine))
        sp.set_attributes({"tip.demande": did, "tip.regroupee": not nouvelle})
    return {"demande": did, "nouvelle": nouvelle}


def bonus_priorite(conn, rules: dict, lon: float, lat: float, vessel_id: int | None) -> int:
    """Lot P : priorité relevée dans une zone de vigilance (profil, priorite_plus) et, si le panneau le demande, pour un
    navire suivi (controle.suivis.priorite_plus)."""
    from mars import controle
    bonus = controle.priorite_zone(rules, lon, lat)
    ctrl = rules.get("_controle") or {}
    if ctrl.get("suivis_prioritaires") and vessel_id is not None and conn.execute(
            "SELECT 1 FROM followed_vessels WHERE vessel_id = %s", (vessel_id,)).fetchone():
        bonus += int(((rules.get("controle") or {}).get("suivis") or {}).get("priorite_plus", 1))
    return bonus


def _derniere_position(conn, vessel_id: int, avant: datetime | None = None):
    return conn.execute(
        "SELECT ST_X(geom::geometry), ST_Y(geom::geometry), ts, sog_kn, cog_deg FROM positions WHERE vessel_id = %s "
        "AND ts <= %s ORDER BY ts DESC LIMIT 1", (vessel_id, avant or datetime.now(timezone.utc))).fetchone()


def _gravites(minimum: str) -> list[str]:
    return SEVERITES[_rang(minimum):]


def collecter(conn, rules: dict, now: datetime) -> dict:
    """Tips des types actifs : alertes créées ou mises à jour depuis fenetre_tips_h, épisodes de coupure observés
    (dernier cycle des règles). Retourne le nombre de tips par type et de demandes créées ou regroupées."""
    tc = rules["tip_and_cue"]
    tips, depuis = tc["tips"], now - timedelta(hours=tc["fenetre_tips_h"])
    out = {"tips": {}, "nouvelles": 0, "regroupees": 0, "ecartes": {}, "ecartes_detail": []}

    def compter(tip, r):
        out["tips"][tip] = out["tips"].get(tip, 0) + 1
        out["nouvelles"] += bool(r.get("nouvelle"))
        out["regroupees"] += r.get("nouvelle") is False

    def ecarter(vid, motif, cle):
        # Tip écarté d'emblée, avec son motif (task_runs et trace du cycle) ; aucune demande créée
        out["ecartes"][cle] = out["ecartes"].get(cle, 0) + 1
        if len(out["ecartes_detail"]) < 20:
            out["ecartes_detail"].append({"vessel_id": vid, "motif": motif})

    gap = tips.get("coupure_ais") or {}

    def coupure_exclue(vid, details) -> bool:
        """Coupure sur un navire d'un type écarté (pêche), ou déclassée en sortie de couverture : écartée."""
        code = conn.execute("SELECT ship_type_code FROM vessels WHERE id = %s", (vid,)).fetchone()
        if code and code[0] in (gap.get("types_exclus") or []):
            ecarter(vid, motif_type(code[0]), "type")
            return True
        if gap.get("exclure_declassees") and declassee(details):
            ecarter(vid, MOTIF_DECLASSEE, "declassee")
            return True
        return False

    recente = ("greatest(a.detected_at, a.event_time, coalesce((a.details->>'fin')::timestamptz, a.event_time)) >= %s "
               "AND a.status <> 'classee'")
    if tips.get("viirs", {}).get("actif"):
        for aid, lon, lat, t, tr, det in conn.execute(
                f"""SELECT a.id, ST_X(a.geom::geometry), ST_Y(a.geom::geometry), a.event_time,
                           coalesce(a.details->>'trace_id_creation', a.details->>'trace_id'),
                           (a.details->>'detection_id')::bigint
                    FROM alerts a WHERE a.type = 'DARK_SHIP' AND a.details->>'source' = 'viirs'
                      AND a.severity = ANY(%s) AND {recente}""",
                (_gravites(tips["viirs"]["gravite_min"]), depuis)).fetchall():
            compter("viirs", creer(conn, rules, now, cle=f"alerte:{aid}", tip="viirs", lon=lon, lat=lat,
                                   position_le=t, alerte_id=aid, detection_viirs_id=det, trace_origine=tr,
                                   motif="lumière VIIRS sans AIS"))
    # Navires des alertes : dernière position connue (cap et vitesse pour l'estime)
    for tip, sql, args in (
            ("rendezvous", f"""SELECT a.id, (a.details->'navires'->0->>'vessel_id')::bigint,
                                      coalesce(a.details->>'trace_id_creation', a.details->>'trace_id')
                               FROM alerts a WHERE a.type = 'RENDEZVOUS' AND {recente}
                                 AND coalesce((a.details->>'distance_cote_km')::float8, 0) >= %s""",
             lambda t: (depuis, float(t["cote_min_km"]))),
            ("identite", f"""SELECT a.id, (a.details->'navire'->>'vessel_id')::bigint,
                                    coalesce(a.details->>'trace_id_creation', a.details->>'trace_id')
                             FROM alerts a WHERE a.type = 'IDENTITY_CHANGE' AND a.severity = ANY(%s) AND {recente}""",
             lambda t: (_gravites(t["gravite_min"]), depuis)),
            ("watchlist", f"""SELECT a.id, (a.details->'navire'->>'vessel_id')::bigint,
                                     coalesce(a.details->>'trace_id_creation', a.details->>'trace_id')
                              FROM alerts a WHERE a.type = 'WATCHLIST' AND {recente}""", lambda _t: (depuis,)),
            ("coupure_ais", f"""SELECT a.id, (a.details->'navire'->>'vessel_id')::bigint,
                                       coalesce(a.details->>'trace_id_creation', a.details->>'trace_id'),
                                       a.details || jsonb_build_object('gravite', a.severity)
                                FROM alerts a WHERE a.type = 'AIS_GAP' AND {recente} AND
                                  coalesce(a.details->>'gravite_avant_declassement', a.severity) = ANY(%s)""",
             lambda t: (depuis, _gravites(t["gravite_min"])))):
        t = tips.get(tip) or {}
        if not t.get("actif"):
            continue
        for aid, vid, tr, *det in conn.execute(sql, args(t)).fetchall():
            if vid is None or (tip == "coupure_ais" and coupure_exclue(vid, det[0] or {})):
                continue
            pos = _derniere_position(conn, vid, now)
            if pos is None:
                continue
            lon, lat, ts, sog, cog = pos
            cle = f"alerte:{aid}"
            if tip in ("watchlist", "coupure_ais"):
                # Navire des listes (ou coupure en mode alertes) près d'une infrastructure seulement ; la clé du jour
                # permet un nouveau tip le lendemain si le navire revient près d'un câble
                if not _pres_infrastructure(conn, lon, lat, t["corridor_m"]):
                    continue
                if tip == "watchlist":
                    cle = f"alerte:{aid}:{now:%Y-%m-%d}"
            compter(tip, creer(conn, rules, now, cle=cle, tip=tip, lon=lon, lat=lat, position_le=ts, vessel_id=vid,
                               sog=sog, cog=cog, alerte_id=aid, trace_origine=tr,
                               motif={"rendezvous": "rendez vous au large", "identite": "changement d'identité",
                                      "watchlist": "navire des listes près d'une infrastructure",
                                      "coupure_ais": "coupure AIS"}[tip]))
    t = tips.get("coupure_ais") or {}
    if t.get("actif") and (rules.get("ais_gap") or {}).get("mode") == "observation":
        for ep in episodes_observes(conn, depuis):
            # gravité avant un éventuel déclassement : une coupure déclassée est écartée avec son motif, pas ignorée
            if (ep.get("gravite_avant_declassement") or ep.get("gravite")) not in _gravites(t["gravite_min"]) \
                    or not ep.get("derniere_position"):
                continue
            lon, lat = ep["derniere_position"]
            if not _pres_infrastructure(conn, lon, lat, t["corridor_m"]) or coupure_exclue(ep["vessel_id"], ep):
                continue
            t_last = datetime.fromisoformat(ep["dernier_message"])
            pos = _derniere_position(conn, ep["vessel_id"], t_last)
            sog, cog = (pos[3], pos[4]) if pos else (None, None)
            compter("coupure_ais", creer(conn, rules, now, cle=f"coupure:{ep['vessel_id']}:{ep['dernier_message']}",
                                         tip="coupure_ais", lon=lon, lat=lat, position_le=t_last,
                                         vessel_id=ep["vessel_id"], sog=sog, cog=cog, motif="coupure observée",
                                         trace_origine=ep.get("trace_id")))
    if out["ecartes"]:
        traces.attributs(**{f"tip.ecartes.{k}": n for k, n in out["ecartes"].items()})
    return out


def declassee(details: dict) -> bool:
    """Coupure déclassée par la règle (point de disparition collectif sans indice propre au navire : signature d'une
    sortie de notre couverture)."""
    avant = details.get("gravite_avant_declassement")
    return avant is not None and avant != details.get("gravite")


def episodes_observes(conn, depuis: datetime) -> list[dict]:
    """Épisodes de coupure du dernier cycle des règles réussi (mode observation : décrits dans task_runs, sans
    alerte), avec l'identifiant de la trace du cycle."""
    row = conn.execute("SELECT details->'ais_gap'->'episodes', trace_id FROM task_runs WHERE task = 'regles' AND "
                       "status = 'ok' AND started_at >= %s ORDER BY started_at DESC LIMIT 1", (depuis,)).fetchone()
    if not row or not isinstance(row[0], list):
        return []
    return [{**e, "trace_id": row[1]} for e in row[0]]


def _pres_infrastructure(conn, lon: float, lat: float, rayon_m: float) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM infrastructure i WHERE ST_DWithin(i.geom, "
                        "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s))", (lon, lat, float(rayon_m))).fetchone()[0]


def distance_cote_m(conn, lon: float, lat: float, rayon_m: float) -> float | None:
    """Distance à la côte si elle est sous `rayon_m`, sinon None (masque des terres, rayon borné)."""
    return conn.execute("SELECT (SELECT min(ST_Distance(l.geom, g)) FROM land l WHERE ST_DWithin(l.geom, g, %s)) "
                        "FROM (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography AS g) x",
                        (float(rayon_m), lon, lat)).fetchone()[0]


def _a_l_arret(conn, vessel_id: int | None, t: datetime) -> bool:
    """Au mouillage ou amarré d'après le dernier statut AIS (indice, comme partout ailleurs)."""
    if vessel_id is None:
        return False
    r = conn.execute("SELECT nav_status FROM positions WHERE vessel_id = %s AND ts <= %s ORDER BY ts DESC LIMIT 1",
                     (vessel_id, t)).fetchone()
    return bool(r and r[0] in (1, 5))


TRANSIT = "navire en transit, hors de portée au prochain passage"


def choisir_passage(conn, rules: dict, d: dict, now: datetime, cote=None) -> dict:
    """Passage retenu pour une demande : d'abord un passage acquis dans les heures_retro dernières heures qui couvre la
    position prédite à son heure, puis le prochain passage prévu qui la couvre, avant l'expiration. Retourne
    {"passage": …, "prediction": …, "centres": …, "retro": bool} ; {"sans_passage": motif} si aucun passage ne sera
    utile (côte, incertitude, délai, navire muet en transit hors de portée) ; sinon {"attente": motif}, la raison
    d'attendre encore (estime hors des emprises, aucun passage au calendrier) : jamais une attente sans motif."""
    from mars import sentinel2
    from mars.sentinel import covers, distance_emprise_m
    tc, s = rules["tip_and_cue"], rules["sentinel"]
    # Lot S2 : passages optiques candidats quand Sentinel 2 est en service (jamais la nuit, garde des nuages) ; lot P :
    # passages d'une source en pause écartés
    missions, pauses = missions_retenues(rules)
    if not missions:
        return {"attente": " ; ".join(pauses) or "aucune mission satellite en service"}
    p = tc["prediction"]
    cote = cote or (lambda lon, lat: distance_cote_m(conn, lon, lat, s["point_cote_min_km"] * 1000.0))
    debut = max(d["position_le"] - timedelta(minutes=30), now - timedelta(hours=tc["heures_retro"]))
    rows = conn.execute(
        """SELECT id, satellite, acquired_at, statut, ST_AsGeoJSON(footprint)::json, mission, nuages FROM sar_passes
           WHERE mission = ANY(%s) AND (mission = 'S2' OR coalesce(mode, 'IW') = 'IW') AND footprint IS NOT NULL
             AND acquired_at BETWEEN %s AND %s AND (statut = 'acquis' OR acquired_at > %s)
           ORDER BY acquired_at""", (missions, debut, d["expire_le"], now)).fetchall()
    motif = transit = None           # raisons terminales
    proche = deja = nuageux = None   # raisons d'attendre : emprise la plus proche, passage déjà analysé, nuages
    navire = d.get("vessel_id") is not None
    delai = (tc["tips"].get(d.get("tip")) or {}).get("delai_max_h")
    for pid, sat, t, statut, emprise, mission, nuages in rows:
        if mission == "S2" and nuages is not None and nuages > rules["sentinel2"]["nuages_annonce_max_pct"]:
            nuageux = nuageux or (t, nuages)          # passage optique annoncé nuageux : écarté, motif gardé
            continue
        # Navire qui émet encore : dernière position reçue avant le passage (petit cercle) ; navire muet : la dernière
        # connue, et l'estime bornée fait le reste
        base = (d["lon"], d["lat"], d["position_le"], d.get("sog_kn"), d.get("cog_deg"))
        if navire:
            r = _derniere_position(conn, d["vessel_id"], min(t, now))
            if r is not None and r[2] > d["position_le"]:
                base = tuple(r)
        pr = predire_bornee(base, t, min(t, now), p, navire=navire,
                            a_l_arret=_a_l_arret(conn, d.get("vessel_id"), base[2]))
        if not navire and d.get("rayon_demande_m"):
            pr["rayon_m"] = float(d["rayon_demande_m"])      # point du panneau (lot P) : rayon choisi par l'opérateur
        if pr.get("hors_portee"):
            transit = transit or pr["hors_portee"]
            continue
        if not covers(emprise, pr["lon"], pr["lat"], 0):
            km = distance_emprise_m(emprise, pr["lon"], pr["lat"]) / 1000
            if proche is None or km < proche[0]:
                proche = (km, t)
            continue
        if delai is not None and t - d["position_le"] > timedelta(hours=delai):
            # Lumière VIIRS : au delà du délai, un navire sombre qui fait route a quitté la zone
            motif = motif or (f"premier passage qui couvre le point le {t:%d/%m %H:%M}, plus de {delai} h après le signal : "
                              "le navire a pu partir")
            continue
        c = cote(pr["lon"], pr["lat"])
        if c is not None:
            motif = (f"position prédite à {c:.0f} m de la côte au passage du {t:%d/%m %H:%M} : à quai ou trop près de "
                     "la côte, invisible au radar")
            continue
        centres = fenetres_demande(pr, s, tc)
        if len(centres) > tc["fenetres_max_par_demande"]:
            motif = (f"incertitude de {pr['rayon_m'] / 1000:.0f} km au passage du {t:%d/%m %H:%M} : {len(centres)} "
                     f"fenêtres, au delà de {tc['fenetres_max_par_demande']}")
            continue
        if sous_contenance(pr, tc):
            motif = (f"contenance estimée de {100 * pr['contenance']:.0f} % au passage du {t:%d/%m %H:%M}, sous le seuil "
                     f"de {100 * float(tc['prediction']['cercle_appris']['contenance_min']):.0f} %")
            continue
        retro = statut == "acquis" and t <= now
        if retro:
            source = "sentinel2" if mission == "S2" else "sentinel1"
            an = conn.execute("SELECT id, status, fenetres FROM analyses WHERE pass_id = %s AND source = %s "
                              "AND status <> 'failed'", (pid, source)).fetchone()
            if an is not None and source == "sentinel2" and sentinel2.nuageux(conn, an[0], pr["lon"], pr["lat"]):
                nuageux = nuageux or (t, None)        # cercle sous les nuages au passage optique : un autre passage
                continue
            if an is not None:
                # Passage déjà analysé (une analyse par passage) : utile seulement si ses fenêtres couvrent le cercle
                if not _fenetres_couvrent(an[2] or {}, pr["lon"], pr["lat"]):
                    deja = deja or t
                    continue
                return {"passage": (pid, sat, t), "prediction": pr, "centres": centres, "retro": True, "analyse": an[0],
                        "analyse_status": an[1]}
        return {"passage": (pid, sat, t), "prediction": pr, "centres": centres, "retro": retro}
    if motif:
        return {"sans_passage": motif}
    if transit is None and navire:
        # Aucun passage examiné au delà de la portée, mais l'estime est déjà épuisée maintenant : aucun passage à venir
        # ne la retrouvera (les passages acquis qui ne couvraient pas l'estime ne changeront plus)
        r = _derniere_position(conn, d["vessel_id"], now)
        base = tuple(r) if r is not None and r[2] > d["position_le"] else \
            (d["lon"], d["lat"], d["position_le"], d.get("sog_kn"), d.get("cog_deg"))
        pr = predire_bornee(base, now, now, p, a_l_arret=_a_l_arret(conn, d["vessel_id"], base[2]))
        if pr.get("hors_portee"):
            transit = (f"muet depuis le {base[2]:%d/%m %H:%M} à {base[3]:.0f} nœuds, estime épuisée"
                       if now - base[2] > timedelta(hours=p["estime_max_h"]) else pr["hors_portee"])
    if transit:
        # Navire muet qui fait route : l'estime ne le retrouvera plus, attendre l'expiration ne servirait à rien
        return {"sans_passage": f"{TRANSIT} ({transit})"}
    if proche:
        return {"attente": f"estime hors des passages : position prédite à {proche[0]:.0f} km de nos emprises au passage "
                           f"du {proche[1]:%d/%m %H:%M}"}
    if deja:
        return {"attente": f"passage du {deja:%d/%m %H:%M} déjà analysé sans fenêtre sur le cercle : prochain passage "
                           "attendu"}
    if nuageux:
        quoi = f"{nuageux[1]:.0f} % de nuages annoncés" if nuageux[1] is not None else "cercle sous les nuages"
        return {"attente": f"passage optique du {nuageux[0]:%d/%m %H:%M} écarté ({quoi}) : prochain passage attendu"}
    return {"attente": f"aucun passage Sentinel 1 prévu au calendrier avant l'expiration le {d['expire_le']:%d/%m %H:%M}"}


def _fenetres_couvrent(fenetres: dict, lon: float, lat: float) -> bool:
    for fs in fenetres.values():
        for f in fs or []:
            b = f.get("bbox") or []
            if len(b) == 4 and b[0] <= lon <= b[2] and b[1] <= lat <= b[3]:
                return True
    return False


COLS = ("id", "tip", "priorite", "vessel_id", "lon", "lat", "position_le", "sog_kn", "cog_deg", "etat", "expire_le",
        "pass_id", "analysis_id", "traceparent", "rayon_demande_m")


def _demandes(conn, where: str, args: tuple) -> list[dict]:
    return [dict(zip(COLS, r)) for r in conn.execute(
        f"""SELECT id, tip, priorite, vessel_id, ST_X(position::geometry), ST_Y(position::geometry), position_le, sog_kn,
                   cog_deg, etat, expire_le, pass_id, analysis_id, traceparent, rayon_demande_m
            FROM demandes_observation WHERE {where} ORDER BY priorite DESC, id""", args).fetchall()]


def _type_exclu(conn, rules: dict, d: dict) -> str | None:
    """Demande née seulement de coupures AIS sur un navire d'un type écarté (pêche : coupures fréquentes et sans
    rapport avec une menace sur les infrastructures) : motif, sinon None. Couvre les demandes créées avant le filtre."""
    exclus = (rules["tip_and_cue"]["tips"].get("coupure_ais") or {}).get("types_exclus") or []
    if d["tip"] != "coupure_ais" or d.get("vessel_id") is None or not exclus:
        return None
    r = conn.execute("SELECT v.ship_type_code FROM vessels v WHERE v.id = %s AND NOT EXISTS (SELECT 1 FROM "
                     "demandes_origines o WHERE o.demande_id = %s AND o.tip <> 'coupure_ais')",
                     (d["vessel_id"], d["id"])).fetchone()
    return motif_type(r[0]) if r and r[0] in exclus else None


def motif_type(code: int) -> str:
    return f"{'navire de pêche' if code == 30 else 'type de navire écarté'} (type AIS {code}) : coupure AIS écartée du tip and cue"


MOTIF_DECLASSEE = "coupure déclassée en sortie de couverture (point de disparition collectif) : écartée du tip and cue"


def planifier(conn, rules: dict, now: datetime, cote=None) -> dict:
    """Demandes en attente : passage retenu (planifiee), sans passage utile (sans_passage), expirées ; demandes
    planifiées dont le passage est passé sans analyse : expirées à leur échéance. Chaque décision est un span relié à
    la trace de la demande."""
    out = {"planifiees": 0, "sans_passage": 0, "expirees": 0, "en_attente": 0, "analyses_existantes": 0}
    out["expirees"] += conn.execute(
        "UPDATE demandes_observation SET etat = 'expiree', fini_le = now(), mis_a_jour_le = now(), motif = "
        "coalesce(motif || ' ; ', '') || 'aucun passage utile en ' || %s || ' h' WHERE etat = ANY(%s) "
        "AND analysis_id IS NULL AND expire_le < %s", (str(rules["tip_and_cue"]["expiration_h"]), list(ACTIFS), now)
    ).rowcount
    # En attente, ou planifiée sans analyse encore : replanifiée à chaque cycle avec la dernière position reçue
    for d in _demandes(conn, "etat = 'en_attente' OR (etat = 'planifiee' AND analysis_id IS NULL)", ()):
        with traces.span("tip_and_cue.planification", liens=[d["traceparent"]],
                         **{"tip.demande": d["id"], "tip.type": d["tip"]}) as sp:
            exclu = _type_exclu(conn, rules, d)
            c = {"sans_passage": exclu} if exclu else choisir_passage(conn, rules, d, now, cote)
            if "sans_passage" in c:
                conn.execute("UPDATE demandes_observation SET etat = 'sans_passage', motif = %s, fini_le = now(), "
                             "mis_a_jour_le = now() WHERE id = %s", (c["sans_passage"], d["id"]))
                sp.set_attributes({"tip.etat": "sans_passage", "tip.motif": c["sans_passage"]})
                out["sans_passage"] += 1
                continue
            if "attente" in c:
                # Toujours un motif : la demande dit pourquoi elle attend ; une demande planifiée dont le passage ne
                # convient plus revient en attente
                conn.execute("UPDATE demandes_observation SET etat = 'en_attente', motif = %s, pass_id = NULL, "
                             "heure_passage = NULL, passage_retro = NULL, fenetres = NULL, mis_a_jour_le = now() "
                             "WHERE id = %s", (c["attente"], d["id"]))
                sp.set_attributes({"tip.etat": "en_attente", "tip.motif": c["attente"]})
                out["en_attente"] += 1
                continue
            pid, sat, t = c["passage"]
            pr = c["prediction"]
            conn.execute(
                """UPDATE demandes_observation SET etat = 'planifiee', pass_id = %s, heure_passage = %s,
                          passage_retro = %s, position_predite = ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                          rayon_m = %s, prediction = %s, fenetres = %s, analysis_id = %s, motif = %s, contenance = %s,
                          mis_a_jour_le = now() WHERE id = %s""",
                (pid, t, c["retro"], pr["lon"], pr["lat"], pr["rayon_m"], pr["mode"], json.dumps(c["centres"]),
                 c.get("analyse"), f"passage {sat} du {t:%d/%m/%Y %H:%M} UTC" + (" (déjà acquis)" if c["retro"] else ""),
                 pr.get("contenance"), d["id"]))
            sp.set_attributes({"tip.etat": "planifiee", "tip.passage": pid, "tip.heure_passage": t.isoformat(),
                               "tip.retro": c["retro"], "tip.rayon_m": round(pr["rayon_m"]),
                               "tip.prediction": pr["mode"], "tip.fenetres": len(c["centres"]),
                               **({"tip.contenance": pr["contenance"]} if pr.get("contenance") is not None else {})})
            out["planifiees"] += 1
            if c.get("analyse") and c.get("analyse_status") == "done":
                out["analyses_existantes"] += 1                 # passage déjà analysé qui couvre le cercle
                evaluer(conn, c["analyse"], rules, demande_ids=[d["id"]])
    return out


def cycle(conn, rules: dict, now: datetime | None = None) -> dict:
    """Un cycle : tips relevés, demandes planifiées ou expirées (tâche tip_and_cue de taches)."""
    now = now or datetime.now(timezone.utc)
    if not actif(rules):
        return {"inactif": True}
    from mars import controle
    motif = controle.en_pause(rules, "tip_and_cue")
    if motif:
        return attendre_en_pause(conn, rules, now, motif)
    return {**collecter(conn, rules, now), **planifier(conn, rules, now)}


def attendre_en_pause(conn, rules: dict, now: datetime, motif: str) -> dict:
    """Tip and cue en pause (lot P) : aucune nouvelle demande ; les demandes actives gardent leur état avec le motif
    « en pause » et reprennent au cycle qui suit la reprise si elles n'ont pas expiré entre temps."""
    expirees = conn.execute(
        "UPDATE demandes_observation SET etat = 'expiree', fini_le = now(), mis_a_jour_le = now(), motif = "
        "coalesce(motif || ' ; ', '') || 'aucun passage utile en ' || %s || ' h' WHERE etat = ANY(%s) "
        "AND analysis_id IS NULL AND expire_le < %s", (str(rules["tip_and_cue"]["expiration_h"]), list(ACTIFS), now)
    ).rowcount
    en_attente = conn.execute(
        "UPDATE demandes_observation SET motif = %s, mis_a_jour_le = now() WHERE etat = ANY(%s) AND analysis_id IS NULL "
        "AND motif IS DISTINCT FROM %s", (motif, list(ACTIFS), motif)).rowcount
    return {"en_pause": motif, "expirees": expirees, "mises_en_attente": en_attente}


# Sélection Sentinel (mars/sentinel.py) : points de déclenchement, lancement, refus

def points_passage(conn, rules: dict, pass_id: int) -> list[dict]:
    """Centres des fenêtres des demandes planifiées sur ce passage, comme points de déclenchement de la sélection.
    Tip and cue en pause (lot P) : aucun, les demandes planifiées attendent la reprise."""
    from mars import controle
    if not actif(rules) or controle.en_pause(rules, "tip_and_cue"):
        return []
    out = []
    for did, tip, prio, centres in conn.execute(
            "SELECT id, tip, priorite, fenetres FROM demandes_observation WHERE pass_id = %s AND etat = 'planifiee' "
            "AND analysis_id IS NULL", (pass_id,)).fetchall():
        out += [{"motif": "tip_and_cue", "tip": tip, "demande": did, "priorite": prio, "lon": c[0], "lat": c[1]}
                for c in centres or []]
    return out


def priorite(points: list[dict]) -> int:
    """Priorité d'un passage ou d'une scène : la plus haute de ses demandes d'observation, et des points d'intérêt
    situés dans une zone de vigilance (lot P, profil priorite_sentinel)."""
    return max((p.get("priorite") or 0 for p in points if p.get("motif") == "tip_and_cue" or p.get("zone_vigilance")),
               default=-1)


def rattacher(conn, pass_id: int, aid: int, demandes: list[int]) -> int:
    """Analyse lancée pour le passage : elle porte les demandes dont les fenêtres ont été retenues."""
    if not demandes:
        return 0
    return conn.execute("UPDATE demandes_observation SET analysis_id = %s, mis_a_jour_le = now() WHERE pass_id = %s "
                        "AND id = ANY(%s) AND etat = 'planifiee' AND analysis_id IS NULL",
                        (aid, pass_id, demandes)).rowcount


def refuser(conn, demandes: list[int], motif: str) -> int:
    """Plafond ou budget : demandes refusées avec le motif (jamais en silence)."""
    if not demandes:
        return 0
    n = conn.execute("UPDATE demandes_observation SET etat = 'refusee_budget', motif = %s, fini_le = now(), "
                     "mis_a_jour_le = now() WHERE id = ANY(%s) AND etat = 'planifiee' AND analysis_id IS NULL",
                     (motif[:500], demandes)).rowcount
    traces.attributs(**{"tip.refusees": n})
    return n


# Résultat rattaché au tip

def evaluer(conn, aid: int, rules: dict, demande_ids: list[int] | None = None, notifier=None) -> list[dict]:
    """Analyse terminée : chaque demande qu'elle porte reçoit son résultat (détections dans le cercle d'incertitude),
    et chaque alerte d'origine sa preuve, sa vérification et, selon la table versionnée, une gravité relevée ou
    abaissée (jamais de retrait). Une notification part pour une alerte relevée à une gravité de ntfy_gravites."""
    tc = rules["tip_and_cue"]
    where, args = ("id = ANY(%s)", (demande_ids,)) if demande_ids else ("analysis_id = %s AND etat = 'planifiee'", (aid,))
    rows = conn.execute(
        f"""SELECT id, tip, vessel_id, ST_X(position_predite::geometry), ST_Y(position_predite::geometry), rayon_m,
                   pass_id, heure_passage, traceparent FROM demandes_observation WHERE {where}""", args).fetchall()
    if not rows:
        return []
    an = conn.execute("SELECT status, trace_id, error, source FROM analyses WHERE id = %s", (aid,)).fetchone()
    optique = bool(an and an[3] == "sentinel2")
    out = []
    for did, tip, vid, plon, plat, rayon, pid, t, tp in rows:
        with traces.span("tip_and_cue.resultat", liens=[tp, an[1] if an else None],
                         **{"tip.demande": did, "tip.type": tip, "tip.analyse": aid}) as sp:
            origines = conn.execute("SELECT alerte_id, trace_id FROM demandes_origines WHERE demande_id = %s "
                                    "AND alerte_id IS NOT NULL", (did,)).fetchall()
            attendus = {vid} if vid else set()
            for (a, _tr) in origines:
                attendus |= {r[0] for r in conn.execute("SELECT evidence_id FROM alert_evidence WHERE alert_id = %s "
                                                        "AND evidence_type = 'vessel'", (a,)).fetchall()}
            dets = [dict(zip(("id", "distance_m", "matched_vessel_id", "mask_reason", "length_m"), r)) for r in conn.execute(
                """SELECT d.id, ST_Distance(d.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography),
                          d.matched_vessel_id, d.mask_reason, d.length_m
                   FROM detections d WHERE d.analysis_id = %s
                     AND ST_DWithin(d.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
                   ORDER BY 2""", (plon, plat, aid, plon, plat, rayon)).fetchall()]
            if optique and an[0] == "done":
                from mars import sentinel2
                if sentinel2.nuageux(conn, aid, plon, plat):
                    # Cercle sous les nuages au passage optique : rien à conclure, la demande attend un autre passage
                    quand = f" du {t:%d/%m %H:%M}" if t else ""
                    m = f"passage optique{quand} sous les nuages au cercle : replanifiée"
                    conn.execute("UPDATE demandes_observation SET etat = 'en_attente', pass_id = NULL, analysis_id = NULL, "
                                 "heure_passage = NULL, fenetres = NULL, motif = %s, mis_a_jour_le = now() WHERE id = %s",
                                 (m, did))
                    sp.set_attributes({"tip.resultat": "nuages", "tip.etat": "en_attente"})
                    continue
            if an is None or an[0] != "done":
                res, det = "non_evaluable", None
                motif = f"analyse {aid} en échec : {(an[2] if an else '') or 'sans résultat'}"[:300]
            else:
                res, det = categorie(dets, attendus, vid is not None)
                quoi = "navire vu en optique" if optique else "écho"
                motif = {"present_apparie": f"{quoi} apparié au navire attendu, AIS cohérent",
                         "present_sans_ais": f"{quoi} sans AIS dans le cercle : navire présent mais muet",
                         "absent": ("aucun navire vu dans le cercle dégagé : navire absent ou parti" if optique else
                                    "aucun écho dans le cercle : navire absent ou parti (les petites unités échappent "
                                    "au radar)"),
                         "non_evaluable": f"{quoi} dans le cercle, mais zone non évaluable pour l'AIS"}[res]
            detail = {"resultat": res, "motif": motif, "detections_dans_le_cercle": len(dets), "rayon_m": round(rayon),
                      "capteur": "optique" if optique else "radar",
                      "position_predite": [round(plon, 5), round(plat, 5)], "analyse": aid, "passage": pid,
                      "heure_passage": t.isoformat() if t else None,
                      "detection": det and {k: det[k] for k in ("id", "matched_vessel_id", "length_m")},
                      "distance_m": det and round(det["distance_m"])}
            conn.execute("UPDATE demandes_observation SET etat = 'analysee', resultat = %s, resultat_details = %s, "
                         "detection_id = %s, distance_m = %s, analysis_id = %s, fini_le = now(), mis_a_jour_le = now() "
                         "WHERE id = %s", (res, json.dumps(detail), det and det["id"], det and det["distance_m"], aid, did))
            sp.set_attributes({"tip.resultat": res, "tip.detections": len(dets), "tip.distance_m": detail["distance_m"]})
            for (a, tr) in origines:
                out.append(_retour_alerte(conn, a, tr, did, tip, detail, det, tc, notifier))
    return out


def _retour_alerte(conn, alerte_id: int, trace_alerte: str | None, did: int, tip: str, detail: dict, det: dict | None,
                   tc: dict, notifier) -> dict:
    """Preuve, vérification et gravité ajustée sur l'alerte d'origine ; notification si elle est relevée."""
    row = conn.execute("SELECT severity, type FROM alerts WHERE id = %s", (alerte_id,)).fetchone()
    if row is None:
        return {"alerte": alerte_id, "absente": True}
    gravite, _type = row
    with traces.span("tip_and_cue.retour_alerte", liens=[trace_alerte],
                     **{"tip.alerte": alerte_id, "tip.resultat": detail["resultat"]}) as sp:
        nouvelle = ajustement(tip, detail["resultat"], gravite, tc.get("ajustements") or {})
        verif = {**detail, "demande": did, "tip": tip, "gravite_avant": gravite}
        if nouvelle:
            verif["gravite_ajustee"] = nouvelle
        conn.execute("UPDATE alerts SET severity = %s, details = details || jsonb_build_object('verification_satellite', "
                     "%s::jsonb) WHERE id = %s", (nouvelle or gravite, json.dumps(verif), alerte_id))
        conn.execute("INSERT INTO alert_evidence VALUES (%s, 'demande', %s) ON CONFLICT DO NOTHING", (alerte_id, did))
        if det:
            conn.execute("INSERT INTO alert_evidence VALUES (%s, 'detection', %s) ON CONFLICT DO NOTHING",
                         (alerte_id, det["id"]))
        releve = nouvelle is not None and _rang(nouvelle) > _rang(gravite)
        sp.set_attributes({"tip.gravite_avant": gravite, "tip.gravite_apres": nouvelle or gravite, "tip.relevee": releve})
        if releve and nouvelle in tc.get("ntfy_gravites", []):
            titre, message = (f"MARS C2 : alerte {alerte_id} relevée à {nouvelle}",
                              f"Vérification satellite ({tip}) : {detail['motif']}")
            if notifier:
                notifier(titre, message)
            else:                       # préférences du panneau de contrôle et heures calmes (lot P)
                from mars import controle
                controle.notifier("tip_and_cue", nouvelle, titre, message, conn=conn)
    return {"alerte": alerte_id, "gravite_avant": gravite, "gravite_apres": nouvelle or gravite}


# Rejeu (taches.py tip-rejeu) : sur les tips d'une période passée, demandes qu'ils auraient données, passages utiles
# dans les 36 h, fenêtres et coût ; rien n'est écrit

def rejeu(tips: list[dict], passages: list[dict], rules: dict, cote, suivi=None) -> dict:
    """`tips` : {tip, lon, lat, t (heure de la position), connu (heure où le tip est relevé, t par défaut), vessel_id,
    sog, cog, a_l_arret} ; `passages` : {id, t, emprise (GeoJSON)} triés
    par heure ; `cote(lon, lat)` : distance à la côte sous le seuil, ou None. Les tips d'un même navire actifs en même
    temps sont regroupés comme le ferait creer. `suivi(vessel_id, t)` : dernière position reçue avant t (lon, lat,
    ts, sog, cog), comme la replanification à chaque cycle ; sans lui, la position du tip seule. Retourne le détail
    par tip et les totaux, sessions par passage."""
    from mars.sentinel import covers
    tc, s = rules["tip_and_cue"], rules["sentinel"]
    exp, retro = timedelta(hours=tc["expiration_h"]), timedelta(hours=tc["heures_retro"])
    actives: dict = {}
    lignes, sessions = [], {}
    for tp in sorted(tips, key=lambda x: x.get("connu") or x["t"]):
        cle = tp.get("vessel_id") or (round(tp["lon"], 1), round(tp["lat"], 1))
        quand = tp.get("connu") or tp["t"]
        if cle in actives and quand < actives[cle]:
            lignes.append({**tp, "issue": "regroupee"})
            continue
        actives[cle] = quand + exp
        issue, motif = "sans_passage_36h", None
        connu = tp.get("connu") or tp["t"]
        delai = (tc["tips"].get(tp["tip"]) or {}).get("delai_max_h")
        for ps in passages:
            # comme planifier : passage acquis au plus heures_retro avant le tip, jamais avant la position, et avant
            # l'expiration de la demande
            if ps["t"] < max(tp["t"] - timedelta(minutes=30), connu - retro) or ps["t"] > connu + exp:
                continue
            base = (tp["lon"], tp["lat"], tp["t"], tp.get("sog"), tp.get("cog"))
            if suivi and tp.get("vessel_id") is not None:
                r = suivi(tp["vessel_id"], ps["t"])
                if r is not None and r[2] > tp["t"]:
                    base = r
            pr = predire_bornee(tuple(base), ps["t"], min(ps["t"], connu), tc["prediction"],
                                navire=tp.get("vessel_id") is not None,
                                a_l_arret=bool(tp.get("a_l_arret")) and base[2] == tp["t"])
            if pr.get("hors_portee"):
                motif = motif or "transit"
                continue
            if not covers(ps["emprise"], pr["lon"], pr["lat"], 0):
                continue
            if delai is not None and ps["t"] - tp["t"] > timedelta(hours=delai):
                motif = motif or "delai"
                continue
            c = cote(pr["lon"], pr["lat"])
            if c is not None:
                motif = "cote"
                continue
            centres = fenetres_demande(pr, s, tc)
            if len(centres) > tc["fenetres_max_par_demande"]:
                motif = "incertitude"
                continue
            if sous_contenance(pr, tc):
                motif = "contenance"
                continue
            issue = "passage"
            sessions.setdefault(ps["id"], 0)
            sessions[ps["id"]] += len(centres)
            lignes.append({**tp, "issue": issue, "passage": ps["id"], "delai_h": round((ps["t"] - connu).total_seconds()
                                                                                       / 3600, 1),
                           "fenetres": len(centres), "rayon_m": round(pr["rayon_m"]), "contenance": pr.get("contenance")})
            break
        if issue != "passage":
            lignes.append({**tp, "issue": motif or issue})
    par_tip: dict = {}
    for ligne in lignes:
        d = par_tip.setdefault(ligne["tip"], {"tips": 0, "regroupees": 0, "passage": 0, "cote": 0, "incertitude": 0,
                                              "contenance": 0, "delai": 0, "transit": 0, "sans_passage_36h": 0})
        d["tips"] += 1
        cle = {"regroupee": "regroupees"}.get(ligne["issue"], ligne["issue"])
        d[cle if cle in d else "sans_passage_36h"] += 1
    return {"par_tip": par_tip, "lignes": lignes, "sessions": len(sessions),
            "fenetres": sum(sessions.values()), "fenetres_par_session": sessions}


def tips_periode(conn, rules: dict, debut: datetime, fin: datetime) -> list[dict]:
    """Tips qu'auraient donnés les alertes et les épisodes de coupure observés de la période (rejeu, lecture seule) :
    position du navire au moment où le tip aurait été relevé (création de l'alerte)."""
    tips, out = rules["tip_and_cue"]["tips"], []

    def navire(tip, vid, t, extra=None):
        pos = _derniere_position(conn, vid, t)
        if pos is None:
            return
        lon, lat, ts, sog, cog = pos
        if extra and not extra(lon, lat):
            return
        out.append({"tip": tip, "lon": lon, "lat": lat, "t": ts, "connu": t, "vessel_id": vid, "sog": sog, "cog": cog,
                    "a_l_arret": _a_l_arret(conn, vid, t)})
    rows = conn.execute("SELECT id, type, severity, detected_at, ST_X(geom::geometry), ST_Y(geom::geometry), details, "
                        "event_time FROM alerts WHERE detected_at BETWEEN %s AND %s ORDER BY detected_at",
                        (debut, fin)).fetchall()
    for _aid, typ, sev, t, lon, lat, det, t_evt in rows:
        det = det or {}
        if typ == "DARK_SHIP" and det.get("source") == "viirs" and (tips.get("viirs") or {}).get("actif") \
                and sev in _gravites(tips["viirs"]["gravite_min"]):
            out.append({"tip": "viirs", "lon": lon, "lat": lat, "t": t_evt, "connu": t, "vessel_id": None})
        elif typ == "RENDEZVOUS" and (tips.get("rendezvous") or {}).get("actif") \
                and (det.get("distance_cote_km") or 0) >= tips["rendezvous"]["cote_min_km"] and det.get("navires"):
            navire("rendezvous", det["navires"][0]["vessel_id"], t)
        elif typ == "IDENTITY_CHANGE" and (tips.get("identite") or {}).get("actif") \
                and sev in _gravites(tips["identite"]["gravite_min"]) and (det.get("navire") or {}).get("vessel_id"):
            navire("identite", det["navire"]["vessel_id"], t)
        elif typ == "WATCHLIST" and (tips.get("watchlist") or {}).get("actif") and (det.get("navire") or {}).get("vessel_id"):
            navire("watchlist", det["navire"]["vessel_id"], t,
                   lambda x, y: _pres_infrastructure(conn, x, y, tips["watchlist"]["corridor_m"]))
    t = tips.get("coupure_ais") or {}
    if t.get("actif"):
        vus = set()
        for (eps,) in conn.execute("SELECT details->'ais_gap'->'episodes' FROM task_runs WHERE task = 'regles' AND "
                                   "status = 'ok' AND started_at BETWEEN %s AND %s", (debut, fin)).fetchall():
            for ep in eps if isinstance(eps, list) else []:
                cle = (ep.get("vessel_id"), ep.get("dernier_message"))
                if cle in vus or ep.get("gravite") not in _gravites(t["gravite_min"]) or not ep.get("derniere_position"):
                    continue
                code = conn.execute("SELECT ship_type_code FROM vessels WHERE id = %s", (ep["vessel_id"],)).fetchone()
                if (code and code[0] in (t.get("types_exclus") or [])) or (t.get("exclure_declassees") and declassee(ep)):
                    continue
                vus.add(cle)
                lon, lat = ep["derniere_position"]
                if not _pres_infrastructure(conn, lon, lat, t["corridor_m"]):
                    continue
                tl = datetime.fromisoformat(ep["dernier_message"])
                pos = _derniere_position(conn, ep["vessel_id"], tl)
                out.append({"tip": "coupure_ais", "lon": lon, "lat": lat, "t": tl, "vessel_id": ep["vessel_id"],
                            "sog": pos and pos[3], "cog": pos and pos[4]})
    return out


def rejeu_base(conn, rules: dict, debut: datetime, fin: datetime) -> dict:
    """Rejeu sur la base : tips de la période, passages Sentinel 1 (acquis et prévus) jusqu'à 36 h après, côte par le
    masque des terres. Coût : une session par passage retenu (processeur en premier), extrapolé à 30 jours."""
    from mars.sentinel import estimation
    s = rules["sentinel"]
    tips = tips_periode(conn, rules, debut, fin)
    passages = [{"id": r[0], "t": r[1], "emprise": r[2]} for r in conn.execute(
        """SELECT id, acquired_at, ST_AsGeoJSON(footprint)::json FROM sar_passes WHERE mission = 'S1'
           AND coalesce(mode, 'IW') = 'IW' AND footprint IS NOT NULL AND acquired_at BETWEEN %s AND %s
           ORDER BY acquired_at""", (debut - timedelta(hours=1), fin + timedelta(hours=rules["tip_and_cue"]["expiration_h"]))
    ).fetchall()]
    r = rejeu(tips, passages, rules, lambda lon, lat: distance_cote_m(conn, lon, lat, s["point_cote_min_km"] * 1000.0),
              lambda vid, t: _derniere_position(conn, vid, t))
    jours = max((fin - debut).total_seconds() / 86400, 1e-6)
    cout = sum(estimation(rules, s, n) for n in r["fenetres_par_session"].values())
    cont = [x["contenance"] for x in r["lignes"] if x["issue"] == "passage" and x.get("contenance") is not None]
    return {**{k: v for k, v in r.items() if k != "lignes"}, "jours": round(jours, 1), "passages_examines": len(passages),
            "contenance_moyenne": round(sum(cont) / len(cont), 3) if cont else None, "contenance_n": len(cont),
            "cout_eur": round(cout, 2), "cout_mois_eur": round(cout * 30 / jours, 2),
            "sessions_par_jour": round(r["sessions"] / jours, 2), "lignes": r["lignes"]}
