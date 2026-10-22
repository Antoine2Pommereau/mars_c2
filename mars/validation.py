"""Validation stricte des résultats d'un travailleur éphémère (lot Z1), à la réception par l'API, avant toute écriture.

Un travailleur tourne sur une image publique ancienne avec une adresse publique : compromis, il pourrait fabriquer des
détections, donc des alertes (navire sombre, AIS non confirmé, identité incohérente) sur n'importe quel navire. Les
résultats ne sont acceptés que s'ils correspondent à la tâche confiée : mêmes granules ou scènes (et mêmes analyses),
heures cohérentes avec celles de la tâche, coordonnées dans nos zones collectées (mars/ais/live.py, avec une marge),
nombre de détections plafonné, types et bornes de chaque mesure, taille des vignettes. Un refus donne un motif lisible
(jamais de valeur reçue au delà de quelques caractères) ; l'API le journalise et passe le travailleur en échec.
Seuils : config/rules.yaml, travailleurs.validation."""
import math
from datetime import datetime

from mars.ais.live import zones_extent


class Refus(ValueError):
    """Résultats refusés : le message est le motif."""


def _date(v, ou: str) -> datetime:
    if not isinstance(v, str) or len(v) > 40:
        raise Refus(f"{ou} : date attendue")
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        raise Refus(f"{ou} : date illisible") from None
    if d.tzinfo is None:
        raise Refus(f"{ou} : date sans fuseau")
    return d


def _nombre(d: dict, cle: str, ou: str, lo: float | None = None, hi: float | None = None, requis: bool = False):
    v = d.get(cle)
    if v is None:
        if requis:
            raise Refus(f"{ou} : {cle} absent")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise Refus(f"{ou} : {cle} n'est pas un nombre fini")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise Refus(f"{ou} : {cle} hors de [{lo}, {hi}]")
    return v


def _texte(d: dict, cle: str, ou: str, n: int):
    v = d.get(cle)
    if v is not None and (not isinstance(v, str) or len(v) > n):
        raise Refus(f"{ou} : {cle} doit être un texte de {n} caractères au plus")


def _liste(d: dict, cle: str, ou: str, n: int) -> list:
    v = d.get(cle) or []
    if not isinstance(v, list):
        raise Refus(f"{ou} : {cle} doit être une liste")
    if len(v) > n:
        raise Refus(f"{ou} : {len(v)} {cle}, plafond {n}")
    if any(not isinstance(x, dict) for x in v):
        raise Refus(f"{ou} : {cle} doit contenir des objets")
    return v


def _position(det: dict, ou: str, emprise):
    lon = _nombre(det, "lon", ou, -180, 180, requis=True)
    lat = _nombre(det, "lat", ou, -90, 90, requis=True)
    x0, y0, x1, y1 = emprise
    if not (x0 <= lon <= x1 and y0 <= lat <= y1):
        raise Refus(f"{ou} : position hors de nos zones ({lon:.2f}, {lat:.2f})")


def _vignettes(det: dict, ou: str, octets: int):
    for k in ("vignette", "contexte"):
        _texte(det, k, ou, octets)
    for k in ("vignette_geo", "contexte_geo"):
        if det.get(k) is not None and not isinstance(det[k], dict):
            raise Refus(f"{ou} : {k} doit être un objet")


def _heure_coherente(recue: datetime, attendue: datetime, ou: str, ecart_h: float):
    if abs((recue - attendue).total_seconds()) > ecart_h * 3600:
        raise Refus(f"{ou} : heure à plus de {ecart_h:g} h de celle de la tâche")


def _commun(corps, ou="résultats"):
    if not isinstance(corps, dict):
        raise Refus("résultats : objet JSON attendu")
    _texte(corps, "erreur", ou, 2000)
    if corps.get("mesures") is not None and not isinstance(corps["mesures"], dict):
        raise Refus("résultats : mesures doit être un objet")


def _viirs(corps: dict, params: dict, v: dict, emprise):
    attendues = {g.get("dnb"): g for g in params.get("granules") or [] if isinstance(g, dict)}
    for i, g in enumerate(_liste(corps, "granules", "résultats", len(attendues))):
        ou = f"granule {i}"
        p = attendues.get(g.get("dnb"))
        if p is None:
            raise Refus(f"{ou} : granule absente de la tâche")
        _texte(g, "erreur", ou, 1000)
        for k in ("debut", "fin"):
            if g.get(k) is not None and p.get("debut"):
                _heure_coherente(_date(g[k], ou), _date(p["debut"], ou), ou, v["ecart_heure_h"])
        for k in ("lune_moyenne", "eclairs", "torcheres"):
            _nombre(g, k, ou, 0)
        for j, det in enumerate(_liste(g, "detections", ou, v["detections_max_viirs"])):
            o = f"{ou}, détection {j}"
            _position(det, o, emprise)
            _nombre(det, "nanowatts", o, 0, 1e7)
            _nombre(det, "orientation", o, -360, 360)
            _nombre(det, "lune", o, 0, 100)
            _nombre(det, "ciel_clair", o, 0, 1)
            _vignettes(det, o, v["vignette_max_octets"])


def _sentinel(corps: dict, params: dict, v: dict, emprise, plafond: int):
    attendues = {(s.get("analyse"), s.get("id")): s for s in params.get("scenes") or [] if isinstance(s, dict)}
    for i, sc in enumerate(_liste(corps, "scenes", "résultats", len(attendues))):
        ou = f"scène {i}"
        p = attendues.get((sc.get("analyse"), sc.get("id")))
        if p is None:
            raise Refus(f"{ou} : scène ou analyse absente de la tâche")
        _texte(sc, "erreur", ou, 1000)
        if sc.get("tuile") is not None and p.get("tuile") is not None and sc["tuile"] != p["tuile"]:
            raise Refus(f"{ou} : tuile différente de celle de la tâche")
        for k in ("debut", "fin"):
            if sc.get(k) is not None and p.get("debut"):
                _heure_coherente(_date(sc[k], ou), _date(p["debut"], ou), ou, v["ecart_heure_h"])
        for k in ("octets_lus", "octets_scl"):
            _nombre(sc, k, ou, 0)
        _liste(sc, "fenetres", ou, v["fenetres_max"])
        for j, det in enumerate(_liste(sc, "detections", ou, plafond)):
            o = f"{ou}, détection {j}"
            _position(det, o, emprise)
            _nombre(det, "score", o, 0, 1)
            _nombre(det, "peche", o, 0, 1)
            _nombre(det, "longueur_m", o, 0, 600)
            _nombre(det, "largeur_m", o, 0, 150)
            _nombre(det, "cap_deg", o, 0, 360)
            _nombre(det, "cap_prob", o, 0, 1)
            _nombre(det, "vitesse_kn", o, 0, 80)
            _nombre(det, "contraste_db", o, -100, 100)
            _nombre(det, "nuages_pct", o, 0, 100)
            _texte(det, "type", o, 40)
            _vignettes(det, o, v["vignette_max_octets"])


def valider(tache: str, params: dict | None, corps, rules: dict) -> None:
    """Lève Refus (motif) si les résultats ne correspondent pas à la tâche `params` d'un travailleur de type `tache`."""
    v = rules["travailleurs"]["validation"]
    x0, y0, x1, y1 = zones_extent()
    m = float(v["marge_deg"])
    emprise = (x0 - m, y0 - m, x1 + m, y1 + m)
    params = params or {}
    _commun(corps)
    if tache == "viirs":
        _viirs(corps, params, v, emprise)
    elif tache == "sentinel":
        _sentinel(corps, params, v, emprise, v["detections_max_sentinel"])
    elif tache == "sentinel2":
        _sentinel(corps, params, v, emprise, v["detections_max_sentinel2"])
    else:
        raise Refus(f"type de travailleur inconnu : {tache[:20]}")
