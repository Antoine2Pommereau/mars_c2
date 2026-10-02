"""Moteur de fusion d'une analyse radar : positions AIS à t0, filtres, appariement, alertes « navire sombre ».

Fonctions pures (tableaux en entrée et en sortie), utilisées par le backend. L'écriture en base est ailleurs.
"""
import numpy as np
import pandas as pd
from pyproj import Transformer

from mars.ais.dma import positions_at
from mars.fusion.match import match
from mars.geo import utm_epsg


def fuse(det: pd.DataFrame, pos: pd.DataFrame, t0: pd.Timestamp, bbox, rules: dict):
    """det : lon, lat, objectness, vessel_score, fishing_score, length_m, contrast_vv_db.
    pos : vessel_id, mmsi, name, length_m, ts, lat, lon, sog, cog (positions brutes autour de t0).

    Renvoie (det enrichi, alertes, nombre de navires AIS à t0 dans la zone).
    det enrichi : mask_reason, matched_vessel_id, match_cost, match_distance_m, candidates.
    """
    f, thr, c_min = rules["fusion"], rules["model"]["thresholds"], rules["contrast"]["min_vv_db"]
    epsg = utm_epsg((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)

    det = det.reset_index(drop=True).copy()
    det["x"], det["y"] = (to_utm.transform(det.lon.to_numpy(), det.lat.to_numpy()) if len(det)
                          else (np.array([]), np.array([])))

    ais = positions_at(pos, t0, pd.Timedelta(minutes=f["max_gap_min"])) if len(pos) else pd.DataFrame()
    if len(ais):
        ais = ais[ais.lon.between(bbox[0], bbox[2]) & ais.lat.between(bbox[1], bbox[3])].reset_index(drop=True)
        ais["x"], ais["y"] = to_utm.transform(ais.lon.to_numpy(), ais.lat.to_numpy())

    # Masques, dans l'ordre de priorité : terre, contraste, classification
    det["mask_reason"] = None
    if len(det):
        if "on_land" in det:
            det.loc[det.on_land.astype(bool), "mask_reason"] = "terre"
        free = det.mask_reason.isna()
        det.loc[free & det.contrast_vv_db.isna(), "mask_reason"] = "contraste indetermine"
        free = det.mask_reason.isna()
        det.loc[free & (det.contrast_vv_db < c_min), "mask_reason"] = "contraste faible"
        free = det.mask_reason.isna()
        det.loc[free & (det.vessel_score < thr["vessel"]), "mask_reason"] = "non navire"

    kept = det[det.mask_reason.isna()]
    matched = match(kept, ais, f["base_radius_m"], f["doppler_s"])
    det = det.join(matched[["matched_idx", "match_cost", "match_distance_m", "candidates"]])
    det["matched_vessel_id"] = pd.Series(
        [int(ais.vessel_id.iloc[int(m)]) if pd.notna(m) and m >= 0 else None for m in det.matched_idx],
        index=det.index, dtype=object)

    alerts = []
    dsr = rules["dark_ship"]
    for i, d in det.iterrows():
        if pd.notna(d.mask_reason) or pd.notna(d.matched_vessel_id):
            continue
        cands = d.candidates if isinstance(d.candidates, list) else []
        critical = d.length_m >= dsr["critical_length_m"] and d.contrast_vv_db >= dsr["critical_contrast_db"]
        alerts.append({
            "det_index": i,
            "severity": "critique" if critical else "elevee",
            "vessel_ids": [c["vessel_id"] for c in cands],
            "details": {
                "objectness": round(float(d.objectness), 3), "vessel_score": round(float(d.vessel_score), 3),
                "length_m": round(float(d.length_m), 1), "contrast_vv_db": round(float(d.contrast_vv_db), 1),
                "candidats_ais": cands,
                "motif": "Écho radar confirmé par le contraste local, sans navire AIS dans le rayon toléré",
                "parametres": {"seuil_contraste_db": c_min, "rayon_base_m": f["base_radius_m"],
                               "doppler_s": f["doppler_s"], "seuils_modele": thr},
            },
        })
    return det, alerts, len(ais)
