"""Analyse radar d'une zone pour le passage Sentinel 1 le plus proche d'un instant donné.

Enchaîne : recherche du passage, extrait Sentinel Hub, détection (mode rapide), positions AIS à t0,
appariement, filtre de contraste, alertes « navire sombre » avec leurs preuves.

Exemple :
    python scripts/analyze_zone.py --bbox 10.30 57.80 10.80 58.07 --time 2024-06-05T17:02:26Z
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from pyproj import Transformer

from mars.ais.dma import positions_at
from mars.config import ROOT, load_env, load_rules
from mars.db import connect
from mars.fusion.match import match
from mars.sar.inference import detect, load_model, pick_device
from mars.sar.sentinelhub import SentinelHub


def aoi_wkt(b):
    return (f"SRID=4326;POLYGON(({b[0]} {b[1]},{b[2]} {b[1]},{b[2]} {b[3]},{b[0]} {b[3]},{b[0]} {b[1]}))")


def main():
    ap = argparse.ArgumentParser(description="Analyse radar d'une zone")
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    ap.add_argument("--time", required=True, help="Instant proche du passage voulu, en UTC (ISO 8601)")
    ap.add_argument("--search-hours", type=float, default=2.0, help="Fenêtre de recherche du passage autour de --time")
    ap.add_argument("--device", default="auto", help="auto, cuda, mps ou cpu")
    ap.add_argument("--amp", action="store_true", help="Précision mixte (recommandée sur GPU NVIDIA)")
    args = ap.parse_args()

    load_env()
    rules = load_rules()
    bbox = args.bbox
    t_req = pd.Timestamp(args.time).tz_convert("UTC") if pd.Timestamp(args.time).tzinfo else pd.Timestamp(args.time, tz="UTC")
    timings = {}

    # 1. Passage
    sh = SentinelHub(os.environ.get("SH_CLIENT_ID"), os.environ.get("SH_CLIENT_SECRET"))
    window = pd.Timedelta(hours=args.search_hours)
    passes = sh.search_passes(bbox, t_req - window, t_req + window)
    if not passes:
        sys.exit("Aucun passage Sentinel 1 trouvé autour de cet instant.")
    p = min(passes, key=lambda x: abs(x["acquired_at"] - t_req))
    t0 = p["acquired_at"]
    print(f"Passage retenu : {p['product_name']} ({t0})")

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sar_passes (product_name, platform, acquired_at, orbit_direction, footprint) "
            "VALUES (%s, %s, %s, %s, ST_GeomFromGeoJSON(%s)::geography) "
            "ON CONFLICT (product_name) DO UPDATE SET acquired_at = EXCLUDED.acquired_at RETURNING id",
            (p["product_name"], p["platform"], t0, p["orbit_direction"],
             json.dumps(p["footprint"]) if p["footprint"] else None))
        pass_id = cur.fetchone()[0]
        cur.execute("INSERT INTO analyses (pass_id, aoi, mode, status, model_version) "
                    "VALUES (%s, %s::geography, 'fast', 'running', %s) RETURNING id",
                    (pass_id, aoi_wkt(bbox), rules["model"]["version"]))
        analysis_id = cur.fetchone()[0]
        conn.commit()
    print(f"Analyse n° {analysis_id}")

    try:
        # 2. Extrait radar
        t = time.time()
        image_db, transform, epsg = sh.fetch_extract(bbox, t0, ROOT / "data" / "sar")
        timings["extraction_s"] = round(time.time() - t, 1)

        # 3. Détection
        device = pick_device(args.device)
        model = load_model(ROOT / rules["model"]["file"], device)
        t = time.time()
        det, n_tiles = detect(image_db, transform, model, device, rules["model"]["thresholds"], rules["contrast"],
                              amp=args.amp)
        timings["inference_s"] = round(time.time() - t, 1)
        print(f"{len(det)} détections sur {n_tiles} tuiles ({device}) en {timings['inference_s']} s")

        to_wgs = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
        to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        if len(det):
            det["lon"], det["lat"] = to_wgs.transform(det.x.to_numpy(), det.y.to_numpy())

        # 4. Positions AIS à t0
        t = time.time()
        f = rules["fusion"]
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT p.vessel_id, v.mmsi, v.name, v.length_m, p.ts, ST_Y(p.geom::geometry), ST_X(p.geom::geometry), "
                "p.sog_kn, p.cog_deg FROM positions p JOIN vessels v ON v.id = p.vessel_id "
                "WHERE ST_DWithin(p.geom, %s::geography, 5000) AND p.ts BETWEEN %s AND %s",
                (aoi_wkt(bbox), t0 - pd.Timedelta(minutes=f["ais_window_min"]), t0 + pd.Timedelta(minutes=f["ais_window_min"])))
            rows = cur.fetchall()
        pos = pd.DataFrame(rows, columns=["vessel_id", "mmsi", "name", "length_m", "ts", "lat", "lon", "sog", "cog"])
        if len(pos):
            pos["ts"] = pd.to_datetime(pos.ts, utc=True)
            for col in ["lat", "lon", "sog", "cog", "length_m"]:
                pos[col] = pd.to_numeric(pos[col], errors="coerce")
        ais = positions_at(pos, t0, pd.Timedelta(minutes=f["max_gap_min"])) if len(pos) else pd.DataFrame()
        if len(ais):
            ais["x"], ais["y"] = to_utm.transform(ais.lon.to_numpy(), ais.lat.to_numpy())
            x0, y0 = transform.c, transform.f
            x1 = x0 + transform.a * image_db.shape[2]
            y1 = y0 + transform.e * image_db.shape[1]
            ais = ais[ais.x.between(x0, x1) & ais.y.between(y1, y0)].reset_index(drop=True)
        print(f"{len(ais)} navires AIS positionnés à t0 dans la zone")

        # 5. Filtre de contraste, puis appariement des détections classées navire
        c_min = rules["contrast"]["min_vv_db"]
        thr = rules["model"]["thresholds"]
        det["mask_reason"] = None
        if len(det):
            det.loc[det.contrast_vv_db.isna(), "mask_reason"] = "contraste indetermine"
            det.loc[det.contrast_vv_db < c_min, "mask_reason"] = "contraste faible"
            det.loc[det.mask_reason.isna() & (det.vessel_score < thr["vessel"]), "mask_reason"] = "non navire"
        candidates = det[det.mask_reason.isna()].copy()
        matched = match(candidates, ais, f["base_radius_m"], f["doppler_s"])
        det = det.join(matched[["matched_idx", "match_cost", "match_distance_m", "candidates"]])
        timings["fusion_s"] = round(time.time() - t, 1)

        # 6. Enregistrement des détections et des alertes
        with connect() as conn, conn.cursor() as cur:
            n_alerts = 0
            for d in det.itertuples():
                midx = int(d.matched_idx) if pd.notna(d.matched_idx) and d.matched_idx >= 0 else None
                vessel_id = int(ais.vessel_id.iloc[midx]) if midx is not None else None
                cur.execute(
                    "INSERT INTO detections (analysis_id, geom, objectness, vessel_score, fishing_score, length_m, "
                    "contrast_vv_db, mask_reason, matched_vessel_id, match_cost) "
                    "VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (analysis_id, float(d.lon), float(d.lat), float(d.objectness), float(d.vessel_score),
                     float(d.fishing_score), float(d.length_m),
                     None if pd.isna(d.contrast_vv_db) else float(d.contrast_vv_db),
                     d.mask_reason, vessel_id, None if pd.isna(d.match_cost) else float(d.match_cost)))
                det_id = cur.fetchone()[0]

                if d.mask_reason is None and vessel_id is None:
                    dsr = rules["dark_ship"]
                    critical = d.length_m >= dsr["critical_length_m"] and d.contrast_vv_db >= dsr["critical_contrast_db"]
                    details = {
                        "analysis_id": analysis_id, "pass": p["product_name"],
                        "objectness": round(float(d.objectness), 3), "vessel_score": round(float(d.vessel_score), 3),
                        "length_m": round(float(d.length_m), 1), "contrast_vv_db": round(float(d.contrast_vv_db), 1),
                        "candidats_ais": d.candidates if isinstance(d.candidates, list) else [],
                        "motif": "Écho radar confirmé par le contraste local, sans navire AIS dans le rayon toléré",
                        "parametres": {"seuil_contraste_db": c_min, "rayon_base_m": f["base_radius_m"],
                                       "doppler_s": f["doppler_s"], "seuils_modele": thr},
                    }
                    cur.execute(
                        "INSERT INTO alerts (type, severity, event_time, geom, details, rule_version) "
                        "VALUES ('DARK_SHIP', %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s) RETURNING id",
                        ("critique" if critical else "elevee", t0, float(d.lon), float(d.lat),
                         json.dumps(details, ensure_ascii=False), rules["version"]))
                    alert_id = cur.fetchone()[0]
                    evidence = [("analysis", analysis_id), ("detection", det_id)]
                    evidence += [("vessel", int(ais.vessel_id[ais.mmsi == c["mmsi"]].iloc[0]))
                                 for c in (d.candidates if isinstance(d.candidates, list) else [])]
                    cur.executemany("INSERT INTO alert_evidence VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                                    [(alert_id, et, eid) for et, eid in evidence])
                    n_alerts += 1

            cur.execute("UPDATE analyses SET status = 'done', completed_at = now() WHERE id = %s", (analysis_id,))
            conn.commit()

        n_ships = int(det.mask_reason.isna().sum()) if len(det) else 0
        n_matched = int((det.matched_idx >= 0).sum()) if len(det) else 0
        print(f"Navires retenus : {n_ships}, appariés à l'AIS : {n_matched}, alertes « navire sombre » : {n_alerts}")
        print("Durées :", timings)
    except Exception:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE analyses SET status = 'failed', completed_at = now() WHERE id = %s", (analysis_id,))
            conn.commit()
        raise


if __name__ == "__main__":
    main()
