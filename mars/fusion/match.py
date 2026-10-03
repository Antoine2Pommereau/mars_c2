"""Appariement des détections radar avec les positions AIS estimées à l'instant du passage.

Tolérance orientée : un navire en mouvement apparaît décalé dans l'image radar le long de la trace du satellite
(direction de vol), d'une distance proche de (R/V) x composante de sa vitesse dirigée vers le radar, c'est à dire
perpendiculaire à la trace. La zone de tolérance est donc une ellipse allongée le long de la trace, et non un cercle.
"""
import math

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment


def tolerance(ais: pd.DataFrame, base_radius_m: float, doppler_s: float, heading_deg: float | None):
    """Demi axes de l'ellipse de tolérance (le long de la trace, en travers) pour chaque navire AIS,
    et vecteurs unitaires de la trace et de la direction en travers, en coordonnées projetées (x est, y nord)."""
    speed = np.nan_to_num(ais.sog.to_numpy(float), nan=0.0) * 0.514444
    if heading_deg is None:
        # Orientation inconnue : cercle, avec la vitesse totale comme borne du décalage
        r = base_radius_m + doppler_s * speed
        return r, r, None, None
    h = math.radians(heading_deg)
    along = np.array([math.sin(h), math.cos(h)])
    cross = np.array([math.cos(h), -math.sin(h)])
    cog = np.radians(np.nan_to_num(ais.cog.to_numpy(float), nan=0.0))
    v = np.stack([np.sin(cog), np.cos(cog)], axis=1) * speed[:, None]
    v_range = np.abs(v @ cross)            # composante de la vitesse dirigée vers le radar
    semi_along = base_radius_m + doppler_s * v_range
    semi_cross = np.full(len(ais), base_radius_m)
    return semi_along, semi_cross, along, cross


def normalized_distance(det_xy: np.ndarray, ais_xy: np.ndarray, semi_along, semi_cross, along, cross):
    """Distance de chaque détection à chaque navire, rapportée à l'ellipse de tolérance (1 = sur le bord)."""
    d = det_xy[:, None, :] - ais_xy[None, :, :]
    if along is None:
        return np.linalg.norm(d, axis=2) / semi_along[None, :]
    da = d @ along
    dc = d @ cross
    return np.sqrt((da / semi_along[None, :]) ** 2 + (dc / semi_cross[None, :]) ** 2)


def match(det: pd.DataFrame, ais: pd.DataFrame, base_radius_m: float, doppler_s: float,
          heading_deg: float | None = None, score_weight: float = 0.2) -> pd.DataFrame:
    """Affectation optimale (algorithme hongrois) dans l'ellipse de tolérance de chaque navire.

    Ajoute à det : matched_idx (position dans ais, ou -1), matched_vessel_id (identifiant du navire apparié, ou
    None), match_cost, match_distance_m, offset_along_m, offset_cross_m, candidates.
    """
    det = det.copy()
    n = len(det)
    matched_idx = np.full(n, -1, dtype=int)
    matched_vessel_id = [None] * n
    match_cost = np.full(n, np.nan)
    match_dist = np.full(n, np.nan)
    off_along = np.full(n, np.nan)
    off_cross = np.full(n, np.nan)
    candidates = [[] for _ in range(n)]

    if n and len(ais):
        semi_along, semi_cross, along, cross = tolerance(ais, base_radius_m, doppler_s, heading_deg)
        det_xy = det[["x", "y"]].to_numpy(float)
        ais_xy = ais[["x", "y"]].to_numpy(float)
        dist = np.linalg.norm(det_xy[:, None, :] - ais_xy[None, :, :], axis=2)
        nd = normalized_distance(det_xy, ais_xy, semi_along, semi_cross, along, cross)
        # À distance égale, l'appariement préfère la détection la plus sûre : une détection faible ne capte pas
        # le navire AIS d'un écho voisin plus net
        weak = (1 - det.objectness.to_numpy(float))[:, None] if "objectness" in det else 0.0
        cost = np.where(nd <= 1, nd + score_weight * weak, 1e6)
        rows, cols = linear_sum_assignment(cost)
        for r, c in zip(rows, cols):
            if cost[r, c] < 1e6:
                matched_idx[r], match_cost[r], match_dist[r] = c, cost[r, c], dist[r, c]
                # L'identifiant est lu ici, où l'ordre de ais et de ais_xy coïncide : le pipeline n'a plus à
                # supposer que l'ordre de ais est resté le même.
                matched_vessel_id[r] = int(ais.vessel_id.iloc[c])
                if along is not None:
                    delta = det_xy[r] - ais_xy[c]
                    off_along[r], off_cross[r] = float(delta @ along), float(delta @ cross)
        # Trois navires AIS les plus proches de chaque détection, pour la fiche d'alerte
        for i in range(n):
            candidates[i] = [
                {"vessel_id": int(ais.vessel_id.iloc[j]), "mmsi": int(ais.mmsi.iloc[j]),
                 "name": None if pd.isna(ais["name"].iloc[j]) else str(ais["name"].iloc[j]),
                 "distance_m": round(float(dist[i, j])),
                 "tolerance_le_long_m": round(float(semi_along[j])), "tolerance_en_travers_m": round(float(semi_cross[j])),
                 "distance_normalisee": round(float(nd[i, j]), 2)}
                for j in np.argsort(nd[i])[:3]
            ]

    det["matched_idx"] = matched_idx
    det["matched_vessel_id"] = pd.Series(matched_vessel_id, index=det.index, dtype=object)
    det["match_cost"] = match_cost
    det["match_distance_m"] = match_dist
    det["offset_along_m"] = off_along
    det["offset_cross_m"] = off_cross
    det["candidates"] = candidates
    return det
