"""Maintien de la dernière position d'un navire à l'arrêt (bibliothèque standard seulement : partagé par la fusion
radar et optique, mars/fusion/positions.py, et par VIIRS, mars/viirs.py). Mesures et justification des durées :
mars/fusion/positions.py et config/rules.yaml (section fusion)."""
import math


KNOT = 0.514444


def metres(lon1, lat1, lon2, lat2) -> float:
    return math.hypot((lon2 - lon1) * 111_320 * math.cos(math.radians(lat1)), (lat2 - lat1) * 110_574)


def arret_params(rules: dict) -> dict:
    """Réglages du maintien à l'arrêt (section fusion des règles), communs à Sentinel 1, Sentinel 2 et VIIRS."""
    f = rules.get("fusion", {})
    return {"kn": float(f.get("arret_kn", 1.0)), "max_min": float(f.get("arret_max_min", 120)),
            "derive_m_par_min": float(f.get("arret_derive_m_par_min", 2.5)),
            "contredit_m": float(f.get("arret_contredit_m", 300)), "doute_ratio": float(f.get("doute_ratio", 0.2)),
            "base_m": float(f.get("base_radius_m", 300))}


def maintien_arret(points: list[dict], t0, arret: dict):
    """Dernière position avant t0 d'un navire à l'arrêt, encore valable : (point, âge en minutes), sinon None.
    `points` : dictionnaires ts, lon, lat, sog triés par ts. Contredite par toute position plus récente (avant ou après
    t0) en route ou à plus de `contredit_m` : le navire est reparti, sa position ancienne ne dit plus rien."""
    avant = [p for p in points if p["ts"] <= t0]
    if not avant:
        return None
    b = avant[-1]
    sog = b.get("sog")
    if sog is None or sog != sog or sog >= arret["kn"]:
        return None
    age = (t0 - b["ts"]).total_seconds() / 60
    if age > arret["max_min"]:
        return None
    for p in points:
        if p["ts"] <= b["ts"]:
            continue
        ps = p.get("sog")
        if (ps is not None and ps == ps and ps >= arret["kn"]) or \
                metres(b["lon"], b["lat"], p["lon"], p["lat"]) > arret["contredit_m"]:
            return None
    return b, age
