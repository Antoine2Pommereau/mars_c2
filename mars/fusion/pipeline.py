"""Moteur de fusion d'une analyse radar : positions AIS à t0, filtres, appariement, alertes.

Fonctions pures (tableaux en entrée et en sortie), utilisées par le backend. L'écriture en base est ailleurs.
"""
import numpy as np
import pandas as pd

from mars.fusion.doute import compatible, pistes
from mars.fusion.arret import arret_params
from mars.fusion.positions import positions_at
from mars.fusion.match import match, normalized_distance, tolerance
from mars.geo import local_projector


def fuse(det: pd.DataFrame, pos: pd.DataFrame, t0: pd.Timestamp, bbox, rules: dict, heading_deg: float | None = None,
         fixed_points: pd.DataFrame | None = None, echos_faibles: pd.DataFrame | None = None):
    """det : lon, lat, objectness, vessel_score, fishing_score, length_m, contrast_vv_db (et on_land).
    pos : vessel_id, mmsi, name, length_m, ts, lat, lon, sog, cog (positions brutes autour de t0).
    heading_deg : direction de vol du satellite, pour la tolérance orientée (None : tolérance circulaire).
    fixed_points : lon, lat, source, ref (échos fixes connus et échos sans AIS observés à d'autres dates).
    echos_faibles : lon, lat (et score) des échos sous le seuil de détection (analyse 106) : ils ne font ni détection ni
    alerte, mais un navire AIS qui en a un dans sa tolérance n'est pas « sans écho ».

    Renvoie (det enrichi, alertes navire sombre, nombre de navires AIS à t0, extras) ; extras contient
    les positions AIS à t0, les navires AIS non confirmés par le radar et la statistique des décalages.
    """
    f, thr, c_min = rules["fusion"], rules["model"]["thresholds"], rules["contrast"]["min_vv_db"]
    to_xy = local_projector((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    det = det.reset_index(drop=True).copy()
    det["x"], det["y"] = (to_xy(det.lon.to_numpy(), det.lat.to_numpy()) if len(det)
                          else (np.array([]), np.array([])))

    arret = arret_params(rules)
    ais = positions_at(pos, t0, pd.Timedelta(minutes=f["max_gap_min"]), arret) if len(pos) else pd.DataFrame()
    if len(ais):
        # Navires candidats jusqu'à 3 km au delà du bord : l'écho d'un navire voisin peut tomber dans la zone
        m = rules["fusion"].get("edge_margin_m", 3000)
        dlat = m / 110570
        dlon = m / (111320 * np.cos(np.radians((bbox[1] + bbox[3]) / 2)))
        ais = ais[ais.lon.between(bbox[0] - dlon, bbox[2] + dlon) & ais.lat.between(bbox[1] - dlat, bbox[3] + dlat)]
        ais = ais.reset_index(drop=True)
        ais["inside"] = ais.lon.between(bbox[0], bbox[2]) & ais.lat.between(bbox[1], bbox[3])
        ais["x"], ais["y"] = to_xy(ais.lon.to_numpy(), ais.lat.to_numpy())

    # Masques, dans l'ordre de priorité : terre, contraste, classification
    det["mask_reason"] = None
    if len(det):
        if "on_land" in det:
            det.loc[det.on_land.astype(bool), "mask_reason"] = "terre"
        free = det.mask_reason.isna()
        if rules["contrast"].get("obligatoire", True):       # Sentinel 1 d'AI2 : contraste facultatif
            det.loc[free & det.contrast_vv_db.isna(), "mask_reason"] = "contraste indetermine"
        free = det.mask_reason.isna()
        det.loc[free & (det.contrast_vv_db < c_min), "mask_reason"] = "contraste faible"
        free = det.mask_reason.isna()
        det.loc[free & (det.vessel_score < thr["vessel"]), "mask_reason"] = "non navire"

    kept = det[det.mask_reason.isna()]
    matched = match(kept, ais, f["base_radius_m"], f["doppler_s"], heading_deg)
    det = det.join(matched[["matched_idx", "match_cost", "match_distance_m", "offset_along_m", "offset_cross_m",
                            "candidates"]])
    det["matched_vessel_id"] = pd.Series(
        [int(ais.vessel_id.iloc[int(m)]) if pd.notna(m) and m >= 0 else None for m in det.matched_idx],
        index=det.index, dtype=object)
    # Écho voisin du même navire (analyse 108, 27/10/2026) : un grand navire rend parfois deux échos proches (VICTORINE,
    # 150 m, deux détections à 106 m l'une de l'autre) ; l'appariement un pour un laissait le second « sans AIS » et levait
    # une alerte critique. Un écho resté seul, à moins de echo_voisin_m d'un écho apparié au navire V et de longueur
    # proche (écart relatif sous echo_voisin_longueur), est expliqué par V
    det["echo_voisin_de"] = None
    voisin = float(f.get("echo_voisin_m", 0))
    if voisin > 0 and len(det):
        apparies = det[det.matched_vessel_id.notna()]
        for i, d in det[det.matched_vessel_id.isna() & det.mask_reason.isna()].iterrows():
            for j, m in apparies.iterrows():
                if np.hypot(d.x - m.x, d.y - m.y) > voisin:
                    continue
                la, lb = pd.to_numeric(d.length_m, errors="coerce"), pd.to_numeric(m.length_m, errors="coerce")
                if pd.notna(la) and pd.notna(lb) and max(la, lb) > 0 and \
                        abs(la - lb) / max(la, lb) > float(f.get("echo_voisin_longueur", 0.4)):
                    continue
                det.at[i, "matched_vessel_id"] = m.matched_vessel_id
                det.at[i, "matched_idx"] = m.matched_idx
                det.at[i, "echo_voisin_de"] = int(m.id) if "id" in det and pd.notna(m.id) else int(j)
                break
    # Règle de prudence (mars/fusion/doute.py) : un navire AIS connu dans la durée bornée peut il expliquer l'écho ?
    tracks = pistes(pos) if len(pos) else {}
    apparies = {int(v) for v in det.matched_vessel_id if v is not None}
    det["doute"] = pd.Series([compatible(float(d.lon), float(d.lat), t0, tracks, arret, apparies)
                              if d.matched_vessel_id is None and pd.isna(d.mask_reason) and tracks else None
                              for d in det.itertuples()], index=det.index, dtype=object)

    # Persistance : un écho sans AIS revu au même endroit à une autre date est un écho fixe, pas un navire
    fixed_hits = []
    det["fixed_refs"] = [[] for _ in range(len(det))]
    if fixed_points is not None and len(fixed_points) and len(det):
        fx, fy = to_xy(fixed_points.lon.to_numpy(), fixed_points.lat.to_numpy())
        fxy = np.stack([fx, fy], axis=1)
        radius = rules["persistence"]["radius_m"]
        for i, d in det.iterrows():
            if d.mask_reason == "terre" or pd.notna(d.matched_vessel_id):
                continue
            dist = np.linalg.norm(fxy - np.array([d.x, d.y]), axis=1)
            near = np.where(dist <= radius)[0]
            if len(near):
                refs = [{"source": fixed_points.source.iloc[j], "ref": int(fixed_points.ref.iloc[j]),
                         "distance_m": round(float(dist[j]))} for j in near]
                det.at[i, "fixed_refs"] = refs
                if pd.isna(d.mask_reason):
                    det.at[i, "mask_reason"] = "echo fixe"
                fixed_hits.append({"det_index": i, "refs": refs})

    alerts = []
    dsr = rules["dark_ship"]
    for i, d in det.iterrows():
        if pd.notna(d.mask_reason) or pd.notna(d.matched_vessel_id):
            continue
        cands = d.candidates if isinstance(d.candidates, list) else []
        critical = d.length_m >= dsr["critical_length_m"] and d.contrast_vv_db >= dsr["critical_contrast_db"]
        alerts.append({
            "det_index": i,
            "severity": "faible" if d.doute else ("critique" if critical else "elevee"),
            "vessel_ids": [c["vessel_id"] for c in cands],
            "details": {
                "objectness": round(float(d.objectness), 3), "vessel_score": round(float(d.vessel_score), 3),
                "length_m": round(float(d.length_m), 1), "contrast_vv_db": round(float(d.contrast_vv_db), 1),
                "candidats_ais": cands,
                "motif": "Écho radar confirmé par le contraste local, sans navire AIS dans la tolérance",
                "parametres": {"seuil_contraste_db": c_min, "rayon_base_m": f["base_radius_m"],
                               "doppler_s": f["doppler_s"], "direction_de_vol_deg": heading_deg,
                               "seuils_modele": thr},
            },
        })

    inside = ais[ais.inside].reset_index(drop=True) if len(ais) else ais
    faibles = None
    if echos_faibles is not None and len(echos_faibles):
        fx, fy = to_xy(echos_faibles.lon.to_numpy(float), echos_faibles.lat.to_numpy(float))
        faibles = np.stack([fx, fy], axis=1)
    extras = {"ais": ais, "unconfirmed": unconfirmed(inside, det, rules, heading_deg, faibles), "offsets": offsets(det),
              "fixed_hits": fixed_hits}
    return det, alerts, int(ais.inside.sum()) if len(ais) else 0, extras


def unconfirmed(ais: pd.DataFrame, det: pd.DataFrame, rules: dict, heading_deg: float | None,
                faibles: np.ndarray | None = None) -> list[dict]:
    """Grands navires AIS sans aucun écho radar compatible, même écarté par un filtre (hors terre), ni écho faible sous
    le seuil de détection (`faibles` : coordonnées projetées x, y)."""
    u, f = rules["unconfirmed"], rules["fusion"]
    if not len(ais):
        return []
    big = ais[pd.to_numeric(ais.length_m, errors="coerce") >= u["min_length_m"]].reset_index(drop=True)
    if not len(big):
        return []
    echoes = det[det.mask_reason.fillna("") != "terre"] if len(det) else det
    xy = echoes[["x", "y"]].to_numpy(float) if len(echoes) else np.zeros((0, 2))
    if faibles is not None and len(faibles):
        xy = np.concatenate([xy, np.asarray(faibles, float)])
    semi_along, semi_cross, along, cross = tolerance(big, f["base_radius_m"] * u["tolerance_factor"],
                                                     f["doppler_s"] * u["tolerance_factor"], heading_deg)
    if len(xy):
        nd = normalized_distance(xy, big[["x", "y"]].to_numpy(float), semi_along, semi_cross, along, cross)
        dist = np.linalg.norm(xy[:, None, :] - big[["x", "y"]].to_numpy(float)[None], axis=2)
        nearest_nd, nearest_m = nd.min(axis=0), dist.min(axis=0)
    else:
        nearest_nd = np.full(len(big), np.inf)
        nearest_m = np.full(len(big), np.nan)
    out = []
    for j, a in big.iterrows():
        if nearest_nd[j] <= 1:
            continue
        out.append({
            "vessel_id": int(a.vessel_id), "mmsi": int(a.mmsi),
            "name": None if pd.isna(a["name"]) else str(a["name"]),
            "length_m": float(a.length_m), "lon": float(a.lon), "lat": float(a.lat),
            "sog_kn": None if pd.isna(a.sog) else round(float(a.sog), 1),
            "methode_position": a.method,
            "echo_le_plus_proche_m": None if np.isnan(nearest_m[j]) else round(float(nearest_m[j])),
            "tolerance_le_long_m": round(float(semi_along[j])), "tolerance_en_travers_m": round(float(semi_cross[j])),
        })
    return out


def offsets(det: pd.DataFrame) -> dict:
    """Décalages observés entre écho radar et position AIS, le long de la trace et en travers."""
    m = det.dropna(subset=["offset_along_m"]) if "offset_along_m" in det else det.iloc[0:0]
    if not len(m):
        return {}
    return {"paires": int(len(m)),
            "le_long_median_abs_m": round(float(m.offset_along_m.abs().median())),
            "en_travers_median_abs_m": round(float(m.offset_cross_m.abs().median()))}
