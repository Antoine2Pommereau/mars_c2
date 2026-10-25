"""Règle de prudence des alertes DARK_SHIP (radar, optique, VIIRS ; 25/10/2026, analyse 105) : un écho sans navire
apparié n'est jamais une alerte élevée si un navire AIS connu dans la durée bornée (`arret_max_min`) peut encore
l'expliquer : alerte de gravité faible, « à confirmer », avec ce navire en preuve.

Compatible : dernière position avant l'instant, ou première après, à `arret_max_min` au plus, projetée par l'estime
(route et vitesse déclarées) ; rayon = rayon de base + dérive à l'arrêt × âge + `doute_ratio` × chemin parcouru
pendant l'âge (changement de route ou de vitesse possible). Les navires déjà appariés à un autre écho sont écartés : un
navire n'explique qu'un écho, le second d'un bord à bord reste suspect.
"""
import math

from mars.fusion.arret import KNOT, metres


def compatible(lon: float, lat: float, t0, tracks: dict, arret: dict, exclus=()) -> dict | None:
    """Navire AIS le plus compatible avec l'écho (lon, lat) à t0, ou None. `tracks` : navire → liste triée de
    dictionnaires ts, lon, lat, sog, cog ; `exclus` : navires déjà appariés."""
    best = None
    for vid, pts in tracks.items():
        if vid in exclus or not pts:
            continue
        avant = [p for p in pts if p["ts"] <= t0]
        apres = [p for p in pts if p["ts"] > t0]
        for p in ([avant[-1]] if avant else []) + ([apres[0]] if apres else []):
            dt_s = (t0 - p["ts"]).total_seconds()
            age = abs(dt_s) / 60
            if age > arret["max_min"]:
                continue
            sog = p.get("sog") or 0.0
            sog = 0.0 if sog != sog else float(sog)
            cog = p.get("cog")
            x, y = p["lon"], p["lat"]
            chemin = sog * KNOT * abs(dt_s)
            if sog >= arret["kn"] and cog is not None and cog == cog:
                d = sog * KNOT * dt_s
                y = p["lat"] + d * math.cos(math.radians(cog)) / 110_574
                x = p["lon"] + d * math.sin(math.radians(cog)) / (111_320 * max(0.1, math.cos(math.radians(p["lat"]))))
            rayon = arret["base_m"] + arret["derive_m_par_min"] * age + arret["doute_ratio"] * chemin
            dist = metres(lon, lat, x, y)
            if dist <= rayon and (best is None or dist / rayon < best["_r"]):
                best = {"vessel_id": int(vid), "age_min": round(age, 1), "distance_m": round(dist),
                        "rayon_m": round(rayon), "vitesse_kn": round(sog, 1), "_r": dist / rayon,
                        "position_avant": dt_s >= 0}
    if best:
        best.pop("_r")
    return best


def motif_doute(c: dict, nom: str | None) -> str:
    quand = "dernière position" if c["position_avant"] else "position suivante"
    return (f"Navire AIS possiblement compatible : {nom or c['vessel_id']}, {quand} à {c['age_min']:.0f} min et "
            f"{c['distance_m']} m de l'écho (rayon admis {c['rayon_m']} m) : gravité faible, à confirmer").replace(".", ",")


def pistes(pos) -> dict:
    """Positions d'un tableau (vessel_id, ts, lon, lat, sog, cog) en pistes par navire, pour `compatible`."""
    out: dict = {}
    if pos is None or not len(pos):
        return out
    for r in pos.sort_values("ts").itertuples():
        out.setdefault(int(r.vessel_id), []).append({"ts": r.ts, "lon": float(r.lon), "lat": float(r.lat),
                                                     "sog": None if r.sog != r.sog else r.sog,
                                                     "cog": None if r.cog != r.cog else r.cog})
    return out
