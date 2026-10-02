"""Appariement des détections radar avec les positions AIS estimées à l'instant du passage."""
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment


def match(det: pd.DataFrame, ais: pd.DataFrame, base_radius_m: float, doppler_s: float) -> pd.DataFrame:
    """Affectation optimale (algorithme hongrois) dans un rayon qui s'élargit avec la vitesse du navire,
    pour absorber le décalage des navires en mouvement dans l'image radar.

    Ajoute à det : matched_idx (position dans ais, ou -1), match_cost, match_distance_m, candidates.
    """
    det = det.copy()
    n = len(det)
    matched_idx = np.full(n, -1, dtype=int)
    match_cost = np.full(n, np.nan)
    match_dist = np.full(n, np.nan)
    candidates = [[] for _ in range(n)]

    if n and len(ais):
        speed_ms = np.nan_to_num(ais.sog.to_numpy(float), nan=0.0) * 0.514444
        radius = base_radius_m + doppler_s * speed_ms
        dist = np.linalg.norm(det[["x", "y"]].to_numpy(float)[:, None] - ais[["x", "y"]].to_numpy(float)[None], axis=2)
        cost = np.where(dist <= radius[None], dist / radius[None], 1e6)
        rows, cols = linear_sum_assignment(cost)
        for r, c in zip(rows, cols):
            if cost[r, c] < 1e6:
                matched_idx[r], match_cost[r], match_dist[r] = c, cost[r, c], dist[r, c]
        # Trois navires AIS les plus proches de chaque détection, pour la fiche d'alerte
        for i in range(n):
            candidates[i] = [
                {"vessel_id": int(ais.vessel_id.iloc[j]), "mmsi": int(ais.mmsi.iloc[j]),
                 "name": None if pd.isna(ais["name"].iloc[j]) else str(ais["name"].iloc[j]),
                 "distance_m": round(float(dist[i, j])), "rayon_tolere_m": round(float(radius[j]))}
                for j in np.argsort(dist[i])[:3]
            ]

    det["matched_idx"] = matched_idx
    det["match_cost"] = match_cost
    det["match_distance_m"] = match_dist
    det["candidates"] = candidates
    return det
