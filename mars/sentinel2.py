"""Sentinel 2 (lot S2) : détection optique des navires, preuve en couleurs réelles, branchée sur le tip and cue.

Même mécanique que Sentinel 1 (mars/sentinel.py), dont il reprend les points de déclenchement (navire des listes en
mer près d'une infrastructure, alerte VIIRS, demandes d'observation), les fenêtres de 24 km, la fusion avec l'AIS et
les alertes ; budget mensuel partagé, plafond quotidien propre (sentinel2.scenes_max_par_jour, 0 à la livraison).

Particularités de l'optique (mesures du 20/10/2026, docs/sentinel2.md) :
* une scène est une tuile L1C de 110 km ; le modèle rslearn d'AI2 lit ses 13 bandes ;
* heure propre à chaque tuile (SENSING_TIME de MTD_TL.xml, 4 Ko lus) : celle du catalogue est le début de la prise de
  vue sur l'orbite, plusieurs minutes plus tôt (387 s, soit 2 km à 10 nœuds, pour T30UXA le 17/10/2026) ;
* garde des nuages en deux temps : annonce de la tuile avant le lancement (au delà de nuages_annonce_max_pct, tuile
  écartée), puis masque de scène (SCL) du L2A lu par le travailleur dans chaque fenêtre (au delà de
  nuages_fenetre_max_pct, fenêtre close « nuages » sans inférence) ; le L2A est attendu l2a_attente_h après la prise de
  vue, puis l'analyse part sans masque, marquée « masque absent » ;
* navire sous un nuage écarté (« nuages »), navire AIS caché par un nuage jamais déclaré absent ;
* fusion sans tolérance Doppler.

Le serveur ne lit que des métadonnées (catalogue STAC, 4 Ko de MTD_TL.xml par tuile) ; le travailleur lit les images.
"""
import json
import math
import os
import re
from datetime import datetime, timedelta

from mars import traces
from mars.sentinel import (HEADERS, S3_HOST, SERVEUR, STAC, TRAVAILLEUR, _envelope, check_cdse, covers, fenetres,
                           pair_expired, pair_keys, s3_path)

BANDES = ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B10", "B11", "B12"]
MODELE = "allenai/sentinel2-vessel-detection sentinel2_vessels_v0.0.22"
MOTIF_ANNONCE = "nuages annoncés"
MOTIF_ATTENTE_L2A = "masque des nuages (L2A) pas encore publié"


def actif(rules: dict) -> bool:
    """Sentinel 2 en service : plafond quotidien non nul (0 à la livraison)."""
    return int((rules.get("sentinel2") or {}).get("scenes_max_par_jour") or 0) > 0


# Décisions pures

def tuile_de(f: dict) -> dict | None:
    """Tuile L1C utilisable (les 13 bandes du modèle) : identifiant, tuile MGRS, satellite, emprise, bandes, chemin de
    MTD_TL.xml, nuages annoncés, heure du catalogue (début de la prise de vue)."""
    a, p = f.get("assets", {}), f.get("properties", {})
    noms = {b: next((k for k in (b, f"{b}_10m", f"{b}_20m", f"{b}_60m") if k in a), None) for b in BANDES}
    if not all(noms.values()):
        return None
    b02 = a[noms["B02"]]["href"]
    return {"id": f["id"], "tuile": (p.get("grid:code") or "").replace("MGRS-", "T"),
            "satellite": (p.get("platform") or "").upper().replace("SENTINEL-", "S"),
            "debut": p.get("start_datetime") or p["datetime"], "emprise": f["geometry"],
            "bandes": {b: a[k]["href"] for b, k in noms.items()}, "mtd": mtd_tl(b02),
            "nuages_annonce": p.get("eo:cloud_cover"),
            "octets": sum(int(a[k].get("file:size") or 0) for k in noms.values())}


def mtd_tl(href_bande: str) -> str:
    """Chemin de MTD_TL.xml d'une tuile, d'après une bande (…/GRANULE/<granule>/IMG_DATA/… → …/<granule>/MTD_TL.xml)."""
    return href_bande.split("/IMG_DATA/")[0] + "/MTD_TL.xml"


def sensing_time(debut_xml: bytes | str) -> datetime | None:
    """Heure propre de la tuile, dans les premiers kilooctets de MTD_TL.xml."""
    txt = debut_xml.decode("utf-8", "replace") if isinstance(debut_xml, bytes) else debut_xml
    m = re.search(r"<SENSING_TIME[^>]*>([^<]+)</SENSING_TIME>", txt)
    return datetime.fromisoformat(m.group(1).strip().replace("Z", "+00:00")) if m else None


def garde_annonce(tuiles: list[dict], s2: dict) -> tuple[list[dict], list[dict]]:
    """Garde des nuages avant le lancement : tuiles annoncées au delà de nuages_annonce_max_pct écartées avec leur
    motif (3 % de fenêtres dégagées mesurées au delà de 50 % annoncés)."""
    gardees, ecartees = [], []
    for t in tuiles:
        n = t.get("nuages_annonce")
        if n is not None and n > s2["nuages_annonce_max_pct"]:
            ecartees.append({**t, "ecarte": f"{MOTIF_ANNONCE} : {n:.0f} % sur la tuile, au delà de "
                                            f"{s2['nuages_annonce_max_pct']} %"})
        else:
            gardees.append(t)
    return gardees, ecartees


def etat_masque(scl: str | None, heure: datetime, now: datetime, s2: dict) -> str:
    """Masque des nuages d'une tuile : « present » (SCL du L2A publié), « attente » (moins de l2a_attente_h après la
    prise de vue), « absent » (attente dépassée : analyse sans masque plutôt qu'abandon)."""
    if scl:
        return "present"
    return "attente" if now - heure < timedelta(hours=s2["l2a_attente_h"]) else "absent"


def sous_nuage(nuages_pct: float | None, s2: dict) -> bool:
    """Navire sous un nuage : part masquée dans son voisinage au delà de masque_part_max_pct (le navire et son sillage,
    pris pour des nuages par le SCL, n'en font que quelques pour cent)."""
    return nuages_pct is not None and nuages_pct > s2["masque_part_max_pct"]


def cellule(lignes: list[str], bbox: list, lon: float, lat: float) -> str | None:
    """État de la grille du masque d'une fenêtre en (lon, lat) : « 1 » nuageux, « 0 » dégagé, « . » sans donnée ; None
    hors de l'emprise (même calcul que le travailleur)."""
    if not lignes or not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
        return None
    n = len(lignes)
    x = min(n - 1, int((lon - bbox[0]) / (bbox[2] - bbox[0]) * n))
    y = min(n - 1, int((lat - bbox[1]) / (bbox[3] - bbox[1]) * n))
    return lignes[y][x]


def visible(fenetres_lues: list[dict], lon: float, lat: float, marge_m: float = 2000) -> bool:
    """Un navire AIS est il visible du capteur ? Seulement dans le cœur d'une fenêtre lue, avec masque, sur une cellule
    dégagée : sinon l'absence de navire vu ne prouve rien (AIS_UNCONFIRMED jamais émise)."""
    for f in fenetres_lues:
        if f.get("etat") != "lue" or not f.get("grille"):
            continue
        b = f["bbox"]
        dlat = marge_m / 110574.0
        dlon = marge_m / (111320.0 * math.cos(math.radians((b[1] + b[3]) / 2)))
        if (b[0] + dlon <= lon <= b[2] - dlon and b[1] + dlat <= lat <= b[3] - dlat
                and cellule(f["grille"], b, lon, lat) == "0"):
            return True
    return False


def resultat(fenetres_res: list[dict], n_detections: int) -> str:
    """Résultat d'une analyse pour Grafana : « nuages » (toutes les fenêtres closes par les nuages), « sans_detection »,
    « analysee »."""
    if fenetres_res and all(f.get("etat") == "nuages" for f in fenetres_res):
        return "nuages"
    return "analysee" if n_detections else "sans_detection"


def cout(n_fenetres: int, s2: dict, t: dict) -> float:
    """Coût estimé d'une session sur processeur : partie fixe (image de 7,2 Go, poids), puis par fenêtre."""
    from mars.travailleurs import cost, granularite_min
    return cost(s2["minutes_fixes_cpu"] + n_fenetres * s2["minutes_par_fenetre_cpu"], t["prix_heure"], granularite_min(t))


def duree(n_fenetres: int, s2: dict) -> int:
    return int(s2["minutes_fixes_cpu"] + n_fenetres * s2["minutes_par_fenetre_cpu"] + s2["cpu_marge_min"])


# Catalogue et métadonnées

def stac_tuiles(session, satellite: str, t0: datetime, t1: datetime, bbox: tuple, collection: str = "sentinel-2-l1c"
                ) -> list[dict]:
    """Tuiles d'un passage (`satellite` : S2A, S2B, S2C) sur l'intervalle de la prise de vue et nos régions."""
    body = {"collections": [collection], "bbox": list(bbox), "limit": 200,
            "datetime": f"{t0:%Y-%m-%dT%H:%M:%SZ}/{t1:%Y-%m-%dT%H:%M:%SZ}"}
    r = session.post(STAC, json=body, headers=HEADERS, timeout=60)
    r.raise_for_status()
    feats = [f for f in r.json().get("features", [])
             if (f.get("properties", {}).get("platform") or "").upper().replace("SENTINEL-", "S") == satellite]
    if collection != "sentinel-2-l1c":
        return feats
    return [t for t in (tuile_de(f) for f in feats) if t]


def scl_par_tuile(session, satellite: str, t0: datetime, t1: datetime, bbox: tuple) -> dict[str, str]:
    """Masques de scène (SCL à 20 m) des produits L2A déjà publiés du passage, par tuile MGRS."""
    out = {}
    for f in stac_tuiles(session, satellite, t0, t1, bbox, "sentinel-2-l2a"):
        a = f.get("assets", {})
        k = next((x for x in ("SCL_20m", "SCL") if x in a), None)
        if k:
            out[(f["properties"].get("grid:code") or "").replace("MGRS-", "T")] = a[k]["href"]
    return out


def lire_sensing(session, href_mtd: str, pair: dict = SERVEUR) -> datetime | None:
    """SENSING_TIME d'une tuile : 4 premiers kilooctets de MTD_TL.xml sur eodata (signature par en tête, clés du
    serveur ; le fichier fait 0,5 Mo, l'heure est à la huitième ligne). None si la lecture échoue."""
    from mars.r2_signature import signed_headers
    keys = pair_keys(pair)
    if not keys or not href_mtd.startswith("s3://"):
        return None
    path = s3_path(href_mtd)
    with traces.span("GET S3 eodata MTD_TL.xml", **{"peer.service": "Copernicus"}) as sp:
        try:
            r = session.get(f"https://{S3_HOST}{path}", timeout=30,
                            headers={"Range": "bytes=0-4095", **HEADERS, **signed_headers("GET", S3_HOST, path, *keys)})
            sp.set_attribute("http.response.status_code", r.status_code)
            if r.status_code not in (200, 206):
                return None
            return sensing_time(r.content[:4096])
        except Exception as e:
            sp.record_exception(e)
            return None


def corriger_heures(conn, session, now: datetime, jours: int = 3) -> dict:
    """Calendrier (lot S2, décision 3) : heure propre des tuiles des passages Sentinel 2 acquis récents, lue dans
    MTD_TL.xml ; acquired_at et ended_at deviennent la première et la dernière tuile sur nos régions, l'heure du
    catalogue est gardée dans heure_prise_de_vue. Un passage déjà corrigé n'est plus relu (l'upsert du calendrier ne
    réécrit plus ses heures)."""
    from mars.viirs import regions_boxes
    boxes = regions_boxes()
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    out = {"passages": 0, "tuiles": 0, "echecs": 0, "decalage_max_s": 0}
    rows = conn.execute(
        "SELECT id, satellite, acquired_at FROM sar_passes WHERE mission = 'S2' AND statut = 'acquis' "
        "AND heure_tuiles IS NULL AND acquired_at BETWEEN %s AND %s ORDER BY acquired_at",
        (now - timedelta(days=jours), now)).fetchall()
    with traces.span("passages.heures_sentinel2", **{"passages.a_corriger": len(rows)}) as sp:
        for pid, sat, t in rows:
            heures = {}
            for tl in stac_tuiles(session, sat, t - timedelta(seconds=2), t + timedelta(seconds=2), box):
                h = lire_sensing(session, tl["mtd"])
                if h is None:
                    out["echecs"] += 1
                    continue
                heures[tl["tuile"]] = h
            if not heures:
                continue
            debut, fin = min(heures.values()), max(heures.values())
            conn.execute(
                "UPDATE sar_passes SET heure_prise_de_vue = coalesce(heure_prise_de_vue, acquired_at), acquired_at = %s, "
                "ended_at = %s, heure_tuiles = %s WHERE id = %s",
                (debut, fin, json.dumps({k: v.isoformat() for k, v in sorted(heures.items())}), pid))
            out["passages"] += 1
            out["tuiles"] += len(heures)
            out["decalage_max_s"] = max(out["decalage_max_s"], round((fin - t).total_seconds()))
        sp.set_attributes({f"passages.{k}": v for k, v in out.items()})
    return out


# Sélection

def _boite():
    from mars.viirs import regions_boxes
    boxes = regions_boxes()
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def plan(conn, session, rules: dict, now: datetime, pass_id: int | None = None) -> dict:
    """Passages Sentinel 2 à analyser maintenant : points de déclenchement (les mêmes que Sentinel 1), tuiles L1C qui
    les couvrent, garde des nuages annoncés, masque du L2A (attente, puis absent), heure propre de chaque tuile.
    `pass_id` : passage choisi à la main (navires des listes en mer de l'emprise si aucun point)."""
    from mars.sentinel import interest_points, points_liste
    from mars.tip_and_cue import priorite
    s2 = rules["sentinel2"]
    if pass_id is not None:
        rows = conn.execute("SELECT id, satellite, acquired_at, ended_at, heure_prise_de_vue, heure_tuiles, mission "
                            "FROM sar_passes WHERE id = %s", (pass_id,)).fetchall()
        if not rows:
            return {"refus": f"passage {pass_id} inconnu"}
        if rows[0][6] != "S2":
            return {"refus": f"passage {pass_id} : Sentinel 2 seulement ({rows[0][1]})"}
    else:
        rows = conn.execute(
            """SELECT sp.id, sp.satellite, sp.acquired_at, sp.ended_at, sp.heure_prise_de_vue, sp.heure_tuiles, sp.mission
               FROM sar_passes sp WHERE sp.mission = 'S2' AND sp.statut = 'acquis' AND sp.acquired_at BETWEEN %s AND %s
                 AND NOT EXISTS (SELECT 1 FROM analyses a WHERE a.pass_id = sp.id AND a.source = 'sentinel2'
                                 AND a.status <> 'failed')
               ORDER BY sp.acquired_at""", (now - timedelta(hours=s2["passages_recents_h"]), now)).fetchall()
    box = _boite()
    out, attente, ecartes, sans_interet = [], [], {}, 0
    for pid, sat, t0, t1, prise, heures, _m in rows:
        ecartes_p: list[dict] = []
        points = interest_points(conn, pid, rules, ecartes_p)
        if pass_id is not None and not points:
            points = points_liste(conn, pid, rules, ecartes_p)
        if not points:
            sans_interet += 1
            if ecartes_p:
                ecartes[pid] = ecartes_p
            continue
        debut = prise or t0
        tuiles = stac_tuiles(session, sat or "", debut - timedelta(seconds=2), debut + timedelta(seconds=2), box)
        if not tuiles:
            if now - t0 < timedelta(hours=s2["attente_publication_h"]):
                attente.append({"passage": pid, "motif": "tuiles L1C pas encore au catalogue"})
            continue
        utiles = [{**t, "points": [p for p in points if covers(t["emprise"], p["lon"], p["lat"], 0)]} for t in tuiles]
        utiles = [t for t in utiles if t["points"]]
        with traces.span("sentinel2.garde_nuages_annonce", **{"sentinel2.passage": pid,
                                                              "sentinel2.tuiles": len(utiles)}) as sp:
            utiles, nuageuses = garde_annonce(utiles, s2)
            sp.set_attributes({"sentinel2.tuiles_ecartees": len(nuageuses), "sentinel2.tuiles_gardees": len(utiles)})
        ecartes_p += [{"tuile": t["tuile"], "ecarte": t["ecarte"], "points": len(t["points"])} for t in nuageuses]
        if ecartes_p:
            ecartes[pid] = ecartes_p
        if not utiles:
            continue
        scl = scl_par_tuile(session, sat or "", debut - timedelta(seconds=2), debut + timedelta(seconds=2), box)
        connues = heures or {}
        pret, en_attente = [], False
        for t in utiles:
            h = connues.get(t["tuile"])
            heure = datetime.fromisoformat(h) if h else lire_sensing(session, t["mtd"])
            t["heure_tuile"] = heure is not None
            heure = heure or datetime.fromisoformat(t["debut"].replace("Z", "+00:00"))
            t["heure"] = heure.isoformat()
            t["scl"] = scl.get(t["tuile"])
            t["masque"] = etat_masque(t["scl"], heure, now, s2)
            if t["masque"] == "attente":
                en_attente = True
            pret.append(t)
        if en_attente:
            attente.append({"passage": pid, "motif": f"{MOTIF_ATTENTE_L2A} (jusqu'à {s2['l2a_attente_h']} h)"})
            continue
        pret.sort(key=lambda t: (-priorite(t["points"]), -len(t["points"])))
        out.append({"pass_id": pid, "satellite": sat, "acquired_at": t0, "tuiles": pret, "points": points})
    out.sort(key=lambda ps: (-priorite(ps["points"]), ps["acquired_at"]))
    return {"passages": out, "en_attente": attente, "points_ecartes": ecartes, "sans_interet": sans_interet}


def candidats_essai(conn, session, rules: dict, now: datetime, heures: int = 72) -> list[dict]:
    """Passages Sentinel 2 récents qui couvrent un navire des listes ou un point d'intérêt (essai réel) : tuiles,
    nuages annoncés, masque, fenêtres et coût, sans rien lancer ni écrire."""
    from mars import travailleurs
    s2 = rules["sentinel2"]
    t = travailleurs.type_params(rules, "sentinel2")
    out = []
    with traces.span("sentinel2.candidats_essai", **{"sentinel2.heures": heures}) as sp:
        rows = conn.execute(
            """SELECT id FROM sar_passes WHERE mission = 'S2' AND statut = 'acquis' AND acquired_at BETWEEN %s AND %s
                 AND (cardinality(watch_ids) > 0 OR EXISTS (SELECT 1 FROM demandes_observation d
                                                           WHERE d.pass_id = sar_passes.id))
               ORDER BY acquired_at DESC LIMIT 10""", (now - timedelta(hours=heures), now)).fetchall()
        for (pid,) in rows:
            p = plan(conn, session, {**rules, "sentinel2": {**s2, "l2a_attente_h": 0}}, now, pid)
            for ps in p.get("passages", []):
                fen = [f for t in ps["tuiles"] for f in fenetres(t["emprise"], t["points"], rules["sentinel"])]
                out.append({"passage": pid, "satellite": ps["satellite"], "acquis_le": ps["acquired_at"],
                            "points": len(ps["points"]), "tuiles": [
                                {"tuile": x["tuile"], "nuages_annonce": x["nuages_annonce"], "masque": x["masque"],
                                 "heure_tuile": x["heure_tuile"]} for x in ps["tuiles"]],
                            "fenetres": len(fen), "cout_estime_eur": cout(len(fen), s2, t)})
            if not p.get("passages"):
                out.append({"passage": pid, "rien": p.get("refus") or p.get("en_attente") or p.get("points_ecartes")
                            or "aucun point d'intérêt"})
        sp.set_attribute("sentinel2.candidats", len(out))
    return out


# Lancement

def launch(conn, scw, rules: dict, now: datetime, session, pass_id: int | None = None, log=print) -> dict:
    """Une session GP1-S pour les passages Sentinel 2 à analyser : analyses en attente d'un processeur reprises d'abord,
    sinon passages nouveaux. Plafond propre, budget partagé avec Sentinel 1, clés vérifiées, puis GP1-S (POP2 en
    secours). Retourne le résumé, ou {"refus": motif}."""
    from mars import tip_and_cue
    from mars.sentinel import guard, usage
    s2 = dict(rules["sentinel2"])
    s1 = rules["sentinel"]
    essai = os.environ.get("SENTINEL_PLAFOND_ESSAI")
    if pass_id is not None and essai:
        s2["scenes_max_par_jour"] = int(essai)          # essai réel à la main : cette commande seulement
        traces.attributs(**{"sentinel2.plafond_essai": int(essai)})
    if int(s2["scenes_max_par_jour"]) <= 0:
        return {"refus": "Sentinel 2 coupé (sentinel2.scenes_max_par_jour: 0) : essai réel avec "
                         "SENTINEL_PLAFOND_ESSAI=1 sur la commande manuelle"}
    attente = conn.execute("SELECT id, scenes, cout_estime_eur FROM analyses WHERE source = 'sentinel2' "
                           "AND status = 'attente_gpu' AND (prochain_essai IS NULL OR prochain_essai <= %s) ORDER BY id",
                           (now,)).fetchall() if pass_id is None else []
    if attente:
        _s, spent = usage(conn, now, "sentinel2")
        return {**_calculer(conn, scw, rules, s2, now, [(a, sc or []) for a, sc, _c in attente], spent, log),
                "reprise": True}
    if pass_id is not None:
        from mars.sentinel import garde_passage
        motif, _r = garde_passage(conn, pass_id, "sentinel2")          # --refaire : Sentinel 1 seulement
        if motif:
            traces.attributs(**{"sentinel2.garde_refus": motif})
            return {"refus": motif.replace(" : --refaire pour une nouvelle analyse liée à celle ci, qui reste telle quelle",
                                           " (--refaire : Sentinel 1 seulement)")}
    with traces.span("sentinel2.selection", **{"sentinel2.passage_demande": pass_id}) as sp:
        p = plan(conn, session, rules, now, pass_id)
        sp.set_attributes({"sentinel2.passages": len(p.get("passages") or []),
                           "sentinel2.en_attente": len(p.get("en_attente") or [])})
    if "refus" in p:
        return p
    if not p["passages"]:
        return {"passages": 0, **{k: v for k, v in p.items() if k != "passages"}}
    scenes_today, spent = usage(conn, now, "sentinel2")
    room = max(0, s2["scenes_max_par_jour"] - scenes_today)
    selected = []
    for ps in p["passages"]:
        keep = ps["tuiles"][:max(0, room - sum(len(x["tuiles"]) for x in selected))]
        if keep:
            selected.append({**ps, "tuiles": keep})

    def demandes(tuiles):
        return sorted({pt["demande"] for t in tuiles for pt in t.get("points") or [] if pt.get("demande")})
    gardees = {ps["pass_id"]: demandes(ps["tuiles"]) for ps in selected}
    toutes = sorted({d for ps in p["passages"] for d in demandes(ps["tuiles"])})
    tip_and_cue.refuser(conn, [d for d in toutes if d not in {x for v in gardees.values() for x in v}],
                        f"plafond de {s2['scenes_max_par_jour']} tuiles Sentinel 2 par jour atteint")
    with traces.span("sentinel2.fenetres") as sp:
        for ps in selected:
            for t in ps["tuiles"]:
                t["fenetres"] = fenetres(t["emprise"], t["points"], s1)
                for k, f in enumerate(t["fenetres"]):
                    f["id"] = k
        n_fen = sum(len(t["fenetres"]) for ps in selected for t in ps["tuiles"])
        sp.set_attribute("sentinel2.fenetres", n_fen)
    if n_fen > s2["fenetres_max"]:
        return {"refus": f"{n_fen} fenêtres, au delà de {s2['fenetres_max']} par session (sentinel2.fenetres_max)"}
    from mars import travailleurs
    estimate = cout(n_fen, s2, travailleurs.type_params(rules, "sentinel2"))
    n_tuiles = sum(len(ps["tuiles"]) for ps in selected) or sum(len(ps["tuiles"]) for ps in p["passages"])
    motif = guard(n_tuiles, scenes_today, spent, estimate,
                  {**s2, "budget_mensuel_eur": s1["budget_mensuel_eur"]})
    if motif:
        tip_and_cue.refuser(conn, toutes, motif)
        return {"refus": motif.replace("scènes", "tuiles Sentinel 2")}
    with traces.span("sentinel2.verification_cles"):
        motif = pair_expired(TRAVAILLEUR, now) or check_cdse(session, selected[0]["tuiles"][0]["bandes"]["B02"],
                                                              TRAVAILLEUR)
    if motif:
        return {"refus": motif}
    import psycopg
    analyses = []
    try:
        with conn.transaction():
            for ps in selected:
                env = _envelope(ps["tuiles"])
                motifs = sorted({pt["motif"] for pt in ps["points"]}) or ["demande"]
                scenes = [{k: t[k] for k in ("id", "tuile", "satellite", "heure", "heure_tuile", "emprise", "bandes",
                                             "scl", "masque", "nuages_annonce", "octets", "fenetres")} for t in ps["tuiles"]]
                aid = conn.execute(
                    """INSERT INTO analyses (pass_id, aoi, mode, status, model_version, source, scenes, declenchement,
                                             cout_estime_eur, requested_at, trace_id, fenetres, attente_depuis, calcul)
                       VALUES (%s, ST_MakeEnvelope(%s, %s, %s, %s, 4326)::geography, 'full', 'pending', %s, 'sentinel2',
                               %s, %s, %s, %s, %s, %s, %s, 'cpu') RETURNING id""",
                    (ps["pass_id"], *env, MODELE, json.dumps(scenes),
                     json.dumps({"motifs": motifs, "points": ps["points"]}, default=str),
                     round(estimate * len(ps["tuiles"]) / max(n_tuiles, 1), 3), now, traces.trace_id(),
                     json.dumps({t["id"]: t["fenetres"] for t in ps["tuiles"]}), now)).fetchone()[0]
                analyses.append((aid, scenes))
                tip_and_cue.rattacher(conn, ps["pass_id"], aid, gardees.get(ps["pass_id"], []))
    except psycopg.errors.UniqueViolation:
        return {"en_cours": "passage déjà en analyse (lancement concurrent, au même instant)"}
    r = _calculer(conn, scw, rules, s2, now, analyses, spent, log)
    return {**r, "analyses": [a for a, _ in analyses], "tuiles": n_tuiles, "fenetres": n_fen,
            "en_attente": p["en_attente"]}


def _calculer(conn, scw, rules: dict, s2: dict, now: datetime, analyses: list, spent: float, log) -> dict:
    """Lance le travailleur sur le premier type disponible (GP1-S, puis POP2) ; en rupture partout, analyses en attente
    et nouvel essai au cycle suivant (pas un échec)."""
    from mars import travailleurs
    from mars.viirs import regions_boxes
    ids = [a for a, _ in analyses]
    tasks = [{"analyse": aid, "id": sc["id"], "tuile": sc["tuile"], "satellite": sc["satellite"], "debut": sc["heure"],
              "fin": sc["heure"], "bandes": sc["bandes"], "scl": sc.get("scl") if sc.get("masque") == "present" else None,
              "fenetres": sc["fenetres"]} for aid, scs in analyses for sc in scs]
    n_fen = sum(len(t["fenetres"]) for t in tasks)
    params = {"scenes": tasks, "regions": regions_boxes(), "confiance": s2["confiance"],
              "nuages_fenetre_max_pct": s2["nuages_fenetre_max_pct"], "masque_classes": s2["masque_classes"],
              "masque_voisinage_m": s2["masque_voisinage_m"], "masque_part_max_pct": s2["masque_part_max_pct"],
              "vignette_km": s2["vignette_km"], "contexte_km": s2["contexte_km"],
              # satlas : squelette servi localement par le travailleur (sans lui, tout téléchargement est refusé)
              "poids": [{k: p[k] for k in ("chemin", "r2", "sha256", "octets", "satlas") if k in p}
                        for p in s2["poids"]]}
    essais = []
    with traces.span("sentinel2.choix_calcul", **{"sentinel2.analyses": len(ids), "sentinel2.fenetres": n_fen}) as sp:
        for v in travailleurs.variantes(rules, "sentinel2"):
            t = travailleurs.type_params(rules, "sentinel2", v)
            zone = t.get("zone", rules["travailleurs"]["zone"])
            if scw is not None:
                try:
                    etat = (scw.availability(zone) or {}).get(t["commercial_type"], "inconnue")
                except Exception as e:
                    sp.record_exception(e)
                    etat = "inconnue"
                if etat == "shortage":
                    essais.append(f"{t['commercial_type']} ({zone}) : épuisé")
                    continue
            est = cout(n_fen, s2, t)
            if spent + est > rules["sentinel"]["budget_mensuel_eur"]:
                essais.append(f"{t['commercial_type']} : budget mensuel dépassé ({est:.2f} € estimés)".replace(".", ","))
                continue
            variante = {**v, "cout_prevu_eur": est, "duree_max_min": duree(n_fen, s2)}
            with traces.span("sentinel2.essai_type", **{"sentinel2.type": t["commercial_type"], "scaleway.zone": zone,
                                                        "sentinel2.cout_estime": est}) as se:
                try:
                    r = travailleurs.launch(conn, scw, rules, "sentinel2", params, {}, now=now, log=log, variante=variante)
                except Exception as e:
                    conn.execute("UPDATE analyses SET status = 'failed', error = %s, completed_at = now() "
                                 "WHERE id = ANY(%s)", (f"lancement du travailleur : {e}"[:1000], ids))
                    raise
                if "rupture" in r:
                    se.set_attribute("sentinel2.rupture_stock", True)
                    essais.append(f"{t['commercial_type']} ({zone}) : rupture de stock")
                    continue
            if "travailleur" not in r:
                conn.execute("UPDATE analyses SET status = 'failed', error = %s, completed_at = now() WHERE id = ANY(%s)",
                             (r.get("refus") or f"travailleur occupé ({r.get('occupe')})", ids))
                return r
            motif = f"processeur {t['commercial_type']} ({n_fen} fenêtre(s))" + (f" ({' ; '.join(essais)})" if essais else "")
            sp.set_attributes({"sentinel2.type": t["commercial_type"], "sentinel2.motif": motif[:300]})
            conn.execute("UPDATE analyses SET status = 'running', travailleur_id = %s, calcul = 'cpu', calcul_type = %s, "
                         "calcul_motif = %s, essais_calcul = essais_calcul + 1, prochain_essai = NULL, "
                         "cout_estime_eur = %s / greatest(%s, 1) WHERE id = ANY(%s)",
                         (r["travailleur"], f"{t['commercial_type']} {zone}", motif[:1000], est, len(ids), ids))
            return {**r, "type": t["commercial_type"], "zone": zone, "motif": motif, "cout_estime_eur": est}
        motif = "processeur indisponible chez Scaleway (" + " ; ".join(essais) + ")"
        sp.set_attributes({"sentinel2.calcul": "attente", "sentinel2.motif": motif[:300]})
        conn.execute("UPDATE analyses SET status = 'attente_gpu', essais_calcul = essais_calcul + 1, prochain_essai = %s, "
                     "calcul_motif = %s WHERE id = ANY(%s)",
                     (now + timedelta(minutes=rules["sentinel"]["intervalle_min"]), motif[:1000], ids))
        log(f"Sentinel 2 : {motif} ; nouvel essai au cycle suivant")
        return {"attente": ids, "motif": motif}


# Poids du modèle dans le seau des modèles (taches.py sentinel2-poids)

def _sha256_flux(flux, h=None):
    """Lecteur qui calcule l'empreinte SHA 256 de ce qui passe (copie en flux, sans disque)."""
    import hashlib
    h = h or hashlib.sha256()

    class Lecteur:
        empreinte = h

        def read(self, n=-1):
            b = flux.read(n)
            h.update(b)
            return b
    return Lecteur()


def copier_poids(s2: dict, principal, modeles, session, nettoyer: bool = False, log=print) -> dict:
    """Poids dans le seau des modèles (jeton dédié, sans filtre IP, seul à présigner pour un travailleur) : copiés en
    flux depuis l'ancienne copie du seau principal si elle est complète, sinon depuis le dépôt public d'AI2, empreinte
    SHA 256 vérifiée pendant l'envoi (objet faux retiré). `nettoyer` : l'ancienne copie du seau principal est
    supprimée, après relecture complète de la nouvelle et vérification de son empreinte (le quota gratuit de 10 Go
    de R2 est par compte : pas de doublon de 2,7 Go)."""
    out = {"copies": 0, "deja": 0, "retires": 0, "octets": 0}
    for p in s2["poids"]:
        cle = p["r2"]
        with traces.span("sentinel2.poids_seau_modeles", **{"sentinel2.poids": p["chemin"], "r2.objet": cle,
                                                              "sentinel2.octets": p["octets"]}) as sp:
            verifie = False
            if modeles.taille(cle) == p["octets"]:
                log(f"{cle} : déjà dans le seau des modèles ({p['octets']} octets)")
                out["deja"] += 1
            else:
                ancienne = principal.taille(cle) if principal is not None else None
                if ancienne == p["octets"]:
                    source, flux, ferme = "seau principal", principal.flux(cle), None
                else:
                    # Source propre au fichier (squelette SatlasPretrain sur Hugging Face), sinon dépôt public d'AI2
                    url = p.get("source") or \
                        f"{s2['source_poids']}/{p['chemin'].replace('/best.ckpt', '/checkpoints/best.ckpt')}"
                    resp = session.get(url, stream=True, timeout=120)
                    resp.raise_for_status()
                    source = "dépôt Hugging Face d'AI2" if p.get("source") else "dépôt public d'AI2"
                    flux, ferme = resp.raw, resp
                sp.set_attribute("sentinel2.source", source)
                lecteur = _sha256_flux(flux)
                try:
                    envoi = modeles.put_stream(lecteur, cle)
                finally:
                    if ferme is not None:
                        ferme.close()
                if lecteur.empreinte.hexdigest() != p["sha256"] or envoi["bytes"] != p["octets"]:
                    modeles.delete(cle)
                    traces.erreur("empreinte ou taille fausse")
                    raise RuntimeError(f"{p['chemin']} : empreinte ou taille fausse ({envoi['bytes']} octets copiés "
                                       f"depuis le {source}), objet retiré du seau des modèles")
                log(f"{cle} : copié depuis le {source} et vérifié ({envoi['bytes']} octets, SHA 256 conforme)")
                out["copies"] += 1
                out["octets"] += envoi["bytes"]
                verifie = True
            if nettoyer and principal is not None and principal.taille(cle) is not None:
                if not verifie:
                    lecteur = _sha256_flux(modeles.flux(cle))
                    while lecteur.read(8 << 20):
                        pass
                    if lecteur.empreinte.hexdigest() != p["sha256"]:
                        traces.erreur("copie du seau des modèles fausse")
                        raise RuntimeError(f"{cle} : la copie du seau des modèles a une empreinte fausse, ancienne "
                                           "copie gardée ; relancer sans --nettoyer pour la refaire")
                principal.delete(cle)
                log(f"{cle} : ancienne copie retirée du seau principal (nouvelle copie relue et vérifiée)")
                out["retires"] += 1
    traces.attributs(**{f"sentinel2.poids_{k}": v for k, v in out.items()})
    return out


# Résultats du travailleur

def ingest(conn, w: dict, rules: dict) -> dict:
    """Résultat d'un travailleur Sentinel 2 : détections, vignettes, fusion, alertes, résultat des demandes
    d'observation. Appelé par la surveillance des travailleurs, dans une transaction."""
    import base64
    from mars import tip_and_cue
    res = w["resultat"] or {}
    out = {"tuiles": 0, "detections": 0, "fenetres_nuages": 0, "vignettes": 0, "contextes": 0}
    par_analyse: dict[int, list[dict]] = {}
    for sc in res.get("scenes", []):
        par_analyse.setdefault(int(sc["analyse"]), []).append(sc)
    termines, images, contextes = [], [], {}
    for aid, scenes in par_analyse.items():
        erreurs = [f"{sc['id']} : {sc['erreur']}" for sc in scenes if sc.get("erreur")]
        mes, octets, n_det = {}, 0, 0
        for sc in scenes:
            mes[sc["id"]] = {"masque": sc.get("masque"), "octets_lus": sc.get("octets_lus"),
                             "octets_scl": sc.get("octets_scl"), "erreur": sc.get("erreur"),
                             "fenetres": [{k: f.get(k) for k in ("index", "bbox", "etat", "nuages_pct", "octets",
                                                                  "requetes", "lecture_s", "analyse_s", "detections",
                                                                  "ecartees_marge", "erreur", "grille")}
                                          for f in sc.get("fenetres") or []]}
            octets += int(sc.get("octets_lus") or 0) + int(sc.get("octets_scl") or 0)
            out["fenetres_nuages"] += sum(1 for f in sc.get("fenetres") or [] if f.get("etat") == "nuages")
            if sc.get("erreur"):
                continue
            out["tuiles"] += 1
            ts = datetime.fromisoformat(sc["debut"].replace("Z", "+00:00"))
            for d in sc.get("detections", []):
                cle = f"{sc['id']}:{d['lon']:.5f}:{d['lat']:.5f}"
                did = conn.execute(
                    """INSERT INTO detections (analysis_id, geom, objectness, vessel_score, length_m, scene, ts, score,
                                               width_m, heading_deg, speed_kn, type_navire, nuages_pct, masque_nuages, cle)
                       VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s)
                       ON CONFLICT (analysis_id, cle) WHERE cle IS NOT NULL DO NOTHING RETURNING id""",
                    (aid, d["lon"], d["lat"], d.get("score"), d.get("score"), d.get("longueur_m"), sc["id"], ts,
                     d.get("score"), d.get("largeur_m"), d.get("cap_deg"), d.get("vitesse_kn"), d.get("type"),
                     d.get("nuages_pct"), sc.get("masque"), cle)).fetchone()
                if did is None:
                    continue
                out["detections"] += 1
                n_det += 1
                if d.get("vignette"):
                    images.append((did[0], base64.b64decode(d["vignette"]), d.get("vignette_geo"), ts, sc["tuile"],
                                   "vignette"))
                if d.get("contexte"):
                    contextes[did[0]] = (did[0], base64.b64decode(d["contexte"]), d.get("contexte_geo"), ts,
                                         sc["tuile"], "contexte")
        with traces.span("sentinel2.fusion_et_alertes", **{"sentinel2.analyse": aid}) as sp:
            resume = fuse_analysis(conn, aid, rules, mes) if any(not sc.get("erreur") for sc in scenes) else {}
            sp.set_attributes({f"sentinel2.{k}": v for k, v in resume.items() if isinstance(v, (int, float))})
        fen = [f for m in mes.values() for f in m["fenetres"]]
        resume["resultat"] = resultat(fen, n_det)
        resume["fenetres_nuages"] = sum(1 for f in fen if f.get("etat") == "nuages")
        resume["masque"] = sorted({m.get("masque") for m in mes.values() if m.get("masque")})
        status = "done" if len(erreurs) < len(scenes) else "failed"
        conn.execute("UPDATE analyses SET status = %s, completed_at = now(), summary = %s, timings = %s, error = %s, "
                     "octets_lus = %s WHERE id = %s",
                     (status, json.dumps(resume, default=str), json.dumps(mes), "; ".join(erreurs)[:1000] or None,
                      octets or None, aid))
        traces.attributs(**{"sentinel2.octets_lus": octets, "sentinel2.resultat": resume["resultat"]})
        termines.append(aid)
    # Vignettes de 2 km : toutes ; contexte de 10 km : détections liées à une alerte seulement
    lies = {r[0] for r in conn.execute("SELECT DISTINCT evidence_id FROM alert_evidence WHERE evidence_type = 'detection' "
                                       "AND evidence_id = ANY(%s)", (list(contextes),)).fetchall()} if contextes else set()
    with traces.span("sentinel2.vignettes", **{"sentinel2.vignettes": len(images), "sentinel2.contextes": len(lies)}):
        out.update(ranger(conn, images + [contextes[d] for d in lies]))
    termines += [r[0] for r in conn.execute(
        "UPDATE analyses SET status = 'failed', completed_at = now(), error = coalesce(error, %s) "
        "WHERE travailleur_id = %s AND status IN ('pending', 'running') RETURNING id",
        (res.get("erreur") or "aucun résultat du travailleur pour cette analyse", w["id"])).fetchall()]
    for aid in termines:
        out.setdefault("demandes_evaluees", []).extend(tip_and_cue.evaluer(conn, aid, rules))
    out["mesures_travailleur"] = res.get("mesures", {})
    return out


def ranger(conn, images: list[tuple]) -> dict:
    """Vignettes en couleurs réelles sur R2 et au registre des preuves (source sentinel2, rôle vignette ou contexte) ;
    sans R2, détections gardées sans image. Tailles mesurées (octets par rôle)."""
    from mars import preuves
    if not images:
        return {"vignettes": 0}
    try:
        from mars.r2 import R2
        r2 = R2()
    except Exception as e:
        return {"vignettes": 0, "vignettes_non_rangees": len(images), "vignettes_motif": str(e)[:200]}
    out = {"vignettes": 0, "contextes": 0, "vignettes_octets": 0, "contextes_octets": 0}
    for did, png, geo, ts, tuile, role in images:
        lot = tuile if role == "vignette" else f"{tuile}/contexte"
        try:
            pid = preuves.store(conn, r2, "sentinel2", did, png, geo, ts, preuves.key("sentinel2", f"{ts:%Y-%m-%d}", lot, did))
            conn.execute("UPDATE preuves_images SET role = %s WHERE id = %s", (role, pid))
            out["vignettes" if role == "vignette" else "contextes"] += 1
            out[f"{'vignettes' if role == 'vignette' else 'contextes'}_octets"] += len(png)
        except Exception:
            out["non_rangees"] = out.get("non_rangees", 0) + 1
    return out


def regles_fusion(rules: dict) -> dict:
    """Règles de fusion de l'optique : cercle de fusion_rayon_m, sans tolérance Doppler (l'optique ne décale pas un
    navire en mouvement), contraste radar sans objet."""
    return {**rules, "contrast": {**rules["contrast"], "obligatoire": False},
            "fusion": {**rules["fusion"], "base_radius_m": rules["sentinel2"]["fusion_rayon_m"], "doppler_s": 0}}


def fuse_analysis(conn, aid: int, rules: dict, mes: dict | None = None) -> dict:
    """Fusion d'une analyse Sentinel 2 avec l'AIS, tuile par tuile, à l'heure propre de la tuile, sans tolérance
    Doppler ; navires sous un nuage écartés ; navire AIS déclaré absent seulement là où le capteur voyait la mer."""
    import pandas as pd
    from mars.fusion.pipeline import fuse
    from mars.sentinel import _fixed_points, _r, _record_fixed, _screen_and_alert, flux_coupe
    from mars.evaluation import critere
    s2 = rules["sentinel2"]
    mes = mes or (conn.execute("SELECT timings FROM analyses WHERE id = %s", (aid,)).fetchone()[0] or {})
    rules_f = regles_fusion(rules)
    out = {"appariees": 0, "alertes_dark_ship": 0, "alertes_non_confirme": 0, "alertes_incoherence": 0,
           "non_evaluables": 0, "echos_fixes": 0, "sous_nuages": 0, "tuiles": {}}
    meta = {sc["id"]: sc for sc in (conn.execute("SELECT scenes FROM analyses WHERE id = %s", (aid,)).fetchone()[0] or [])}
    cells = conn.execute("SELECT EXISTS (SELECT 1 FROM reception_cells)").fetchone()[0]
    for sid, sc in meta.items():
        m = mes.get(sid) or {}
        if m.get("erreur"):
            continue
        t0 = datetime.fromisoformat(sc["heure"])
        lues = [f for f in m.get("fenetres") or [] if f.get("etat") == "lue"]
        rows = conn.execute(
            """SELECT d.id, ST_X(d.geom::geometry), ST_Y(d.geom::geometry), d.score, d.length_m, d.width_m,
                      d.heading_deg, d.speed_kn, d.type_navire, d.nuages_pct,
                      EXISTS (SELECT 1 FROM land l WHERE ST_DWithin(l.geom, d.geom, %s)) AS terre
               FROM detections d WHERE d.analysis_id = %s AND d.scene = %s ORDER BY d.id""",
            (float(rules["masks"]["land_buffer_m"]), aid, sid)).fetchall()
        det = pd.DataFrame([{"id": r[0], "lon": r[1], "lat": r[2], "objectness": r[3] or 0.0, "vessel_score": r[3] or 0.0,
                             "fishing_score": None, "length_m": r[4], "contrast_vv_db": float("nan"), "width_m": r[5],
                             "heading_deg": r[6], "heading_prob": None, "speed_kn": r[7], "type_navire": r[8],
                             "nuages_pct": r[9], "on_land": bool(r[10])} for r in rows],
                           columns=["id", "lon", "lat", "objectness", "vessel_score", "fishing_score", "length_m",
                                    "contrast_vv_db", "width_m", "heading_deg", "heading_prob", "speed_kn",
                                    "type_navire", "nuages_pct", "on_land"])
        for col in ("length_m", "contrast_vv_db"):
            det[col] = pd.to_numeric(det[col], errors="coerce")
        boites = [f["bbox"] for f in lues] or [list(_envelope([sc]))]
        bbox = (min(b[0] for b in boites), min(b[1] for b in boites), max(b[2] for b in boites), max(b[3] for b in boites))
        f = rules["fusion"]
        pos_rows = conn.execute(
            """SELECT p.vessel_id, v.mmsi, v.name, v.length_m, p.ts, ST_Y(p.geom::geometry), ST_X(p.geom::geometry),
                      p.sog_kn, p.cog_deg
               FROM positions p JOIN vessels v ON v.id = p.vessel_id
               WHERE p.ts BETWEEN %s AND %s
                 AND ST_Intersects(p.geom::geometry, ST_Expand(ST_MakeEnvelope(%s, %s, %s, %s, 4326), 0.05))""",
            (t0 - timedelta(minutes=f["ais_window_min"]), t0 + timedelta(minutes=f["ais_window_min"]), *bbox)).fetchall()
        pos = pd.DataFrame(pos_rows, columns=["vessel_id", "mmsi", "name", "length_m", "ts", "lat", "lon", "sog", "cog"])
        if len(pos):
            pos["ts"] = pd.to_datetime(pos.ts, utc=True)
            for col in ("lat", "lon", "sog", "cog", "length_m"):
                pos[col] = pd.to_numeric(pos[col], errors="coerce")
        fixed = _fixed_points(conn, bbox, t0, rules)
        det_f, _a, n_ais, extras = fuse(det, pos, pd.Timestamp(t0), bbox, rules_f, None, fixed)
        n_fixed = _record_fixed(conn, det_f, extras["fixed_hits"], t0)
        cut = flux_coupe(conn, t0, rules)
        absent = sc.get("masque") != "present"
        capteur = {
            "source": "sentinel2", "cles": ("s2", "s2m", "s2u"), "confiance": s2["confiance"],
            "dark": "Navire vu en optique (Sentinel 2) sans navire AIS compatible, alors que l'AIS est reçu dans la zone",
            "incoherence": "Navire vu en optique apparié au navire AIS, mais sa longueur ou son cap contredit la "
                           "déclaration AIS : identité possiblement usurpée",
            "non_confirme": "Grand navire déclaré par l'AIS dans une fenêtre optique dégagée, sans aucun navire visible "
                            "à l'instant du passage : position possiblement falsifiée",
            "masque": lambda d: "nuages" if sous_nuage(_r(d.nuages_pct, 1), s2) else None,
            "visible": (lambda *_: False) if absent else (lambda lon, lat: visible(lues, lon, lat)),
            "contexte": (["Masque des nuages absent (L2A non publié à temps) : un nuage peut avoir été pris pour un "
                          "navire"] if absent else []),
            "a_confirmer": absent,
        }
        r = _screen_and_alert(conn, aid, sid, t0, {**sc, "orbite": None}, det_f, extras, cut, cells, None, rules, capteur)
        out["sous_nuages"] += int(conn.execute("SELECT count(*) FROM detections WHERE analysis_id = %s AND scene = %s "
                                               "AND mask_reason = 'nuages'", (aid, sid)).fetchone()[0])
        out["appariees"] += int(det_f.matched_vessel_id.notna().sum()) if len(det_f) else 0
        out["echos_fixes"] += n_fixed
        for k in ("alertes_dark_ship", "alertes_non_confirme", "alertes_incoherence", "non_evaluables"):
            out[k] += r[k]
        out["tuiles"][sid] = {"detections": len(det_f), "navires_ais": n_ais, "heure": t0.isoformat(),
                              "heure_tuile": sc.get("heure_tuile"), "masque": sc.get("masque"),
                              "decalages": extras["offsets"], "flux_coupe": cut}
    out["critere_evaluation"] = critere(cells)
    return out


def nuageux(conn, aid: int, lon: float, lat: float) -> bool | None:
    """Le point (lon, lat) était il caché au capteur dans l'analyse `aid` ? True : fenêtre close par les nuages ou
    cellule nuageuse ; False : visible ; None : hors des fenêtres de l'analyse (tip and cue)."""
    mes = conn.execute("SELECT timings FROM analyses WHERE id = %s AND source = 'sentinel2'", (aid,)).fetchone()
    if not mes or not mes[0]:
        return None
    vu = None
    for m in mes[0].values():
        for f in (m or {}).get("fenetres") or []:
            b = f.get("bbox") or []
            if len(b) != 4 or not (b[0] <= lon <= b[2] and b[1] <= lat <= b[3]):
                continue
            if f.get("etat") == "nuages":
                return True
            c = cellule(f.get("grille") or [], b, lon, lat)
            if c == "1":
                return True
            if c == "0" or (f.get("etat") == "lue" and not f.get("grille")):
                vu = False
    return vu
