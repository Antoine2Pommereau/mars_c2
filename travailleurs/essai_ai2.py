"""Essai du script du travailleur Sentinel dans la vraie image d'AI2, sans GPU (scripts/verifier_images.sh ai2).

Exécuté dans ghcr.io/allenai/vessel-detection-sentinels (Python 3.8), sur le Mac en linux/amd64 :
    python3 /mars/essai_ai2.py --imports     chaîne d'AI2 importée par le script, poids présents (une minute)
    python3 /mars/essai_ai2.py               analyse complète sur CPU d'une scène synthétique (environ 7 minutes)

Scène synthétique au format des produits Sentinel 1 GRD : images VV et VH en entiers 16 bits, géoréférencées par
points d'appui, mer de l'Iroise (fouillis de Rayleigh), quatre navires brillants injectés. L'analyse complète passe par
la chaîne réelle (prepare_scenes, detect_vessels), le filtre des régions, le contraste et les vignettes : chaque cible
doit être retrouvée à moins de 50 m, avec sa vignette. Ajouté après le second essai réel du 14/10/2026 (« No module
named 'src' ») ; le premier essai complet a aussi révélé le seuil nms_thresh à passer en entier.
"""
from __future__ import annotations

import importlib.util
import math
import os
import sys
import tempfile
import time

CIBLES = [(600, 700), (1500, 900), (1100, 1800), (2000, 2100)]       # (ligne, colonne)
LON0, LAT0, PX = -5.35, 48.25, 10.0                                   # coin haut gauche, 10 m par pixel


def worker():
    spec = importlib.util.spec_from_file_location("travailleur_sentinel", os.path.join(os.path.dirname(__file__),
                                                                                      "sentinel.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def scene(folder: str, n: int = 2560) -> list:
    import numpy as np
    from osgeo import gdal, osr
    rng = np.random.default_rng(1)
    dlon, dlat = PX / (111320 * math.cos(math.radians(LAT0))), PX / 111320
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    gcps = [gdal.GCP(LON0 + c * dlon, LAT0 - r * dlat, 0, c, r)
            for r in range(0, n + 1, n // 4) for c in range(0, n + 1, n // 4)]
    paths = {}
    for pol, mer, cible in (("vv", 60, 3000), ("vh", 25, 900)):
        a = rng.rayleigh(mer, (n, n))
        for r, c in CIBLES:
            a[r - 8:r + 8, c - 3:c + 3] = cible * rng.uniform(0.8, 1.2, (16, 6))   # navire de 160 m sur 60 m
        a = np.clip(a, 1, 65535).astype(np.uint16)
        paths[pol] = os.path.join(folder, f"s1c-iw-grd-{pol}-20261014t172919-20261014t172944-004321-008765-cog.tiff")
        ds = gdal.GetDriverByName("GTiff").Create(paths[pol], n, n, 1, gdal.GDT_UInt16, ["TILED=YES", "COMPRESS=DEFLATE"])
        ds.GetRasterBand(1).WriteArray(a)
        ds.SetGCPs(gcps, srs.ExportToWkt())
        ds = None
    return [(LON0 + c * dlon, LAT0 - r * dlat) for r, c in CIBLES], paths


def metres(a, b) -> float:
    return math.hypot((a[0] - b[0]) * 111320 * math.cos(math.radians(a[1])), (a[1] - b[1]) * 111320)


def main() -> int:
    w = worker()
    if "--imports" in sys.argv:
        prepare, detect = w.ai2()
        device, _ = w.preflight(force_cpu=True)
        print(f"OK      chaîne d'AI2 importée ({prepare.__module__}.{prepare.__name__}, "
              f"{detect.__module__}.{detect.__name__}), poids présents, appareil {device}")
        return 0
    tmp = tempfile.mkdtemp(prefix="essai_ai2_")
    cibles, paths = scene(tmp)
    task = {"scenes": [{"analyse": 1, "id": "S1C_IW_GRDH_1SDV_20261014T172919_20261014T172944_004321_008765_ABCD_COG",
                        "debut": "2026-10-14T17:29:19Z", "fin": "2026-10-14T17:29:44Z", "satellite": "S1C",
                        "vv_path": paths["vv"], "vh_path": paths["vh"]}],
            "regions": [[-6.8, 47.3, -3.0, 49.6]], "confiance": 0.5, "nms_m": 10, "vignette_km": 3,
            "force_cpu": True, "travail": tmp}
    t = time.time()
    res = w.run(task, local=True)
    sc = res["scenes"][0]
    if res.get("erreur") or sc.get("erreur"):
        print(f"ÉCHEC   analyse en erreur : {res.get('erreur') or sc.get('erreur')}")
        return 1
    dets = sc.get("detections", [])
    manques = []
    for c in cibles:
        best = min(dets, key=lambda d: metres(c, (d["lon"], d["lat"])), default=None)
        dist = metres(c, (best["lon"], best["lat"])) if best else None
        if dist is None or dist > 50 or not best.get("vignette"):
            manques.append(f"cible {c[0]:.5f} {c[1]:.5f} : {'aucune détection' if dist is None else f'{dist:.0f} m'}"
                           + ("" if best and best.get("vignette") else ", sans vignette"))
        else:
            print(f"        cible retrouvée à {dist:.0f} m, score {best['score']:.2f}, vignette de "
                  f"{len(best['vignette']) * 3 // 4 // 1024} Ko")
    if manques:
        print("ÉCHEC   " + " ; ".join(manques))
        return 1
    print(f"OK      analyse complète sur CPU : {len(cibles)} cibles sur {len(cibles)} retrouvées, {len(dets)} détections, "
          f"{time.time() - t:.0f} s, mémoire {res['mesures']['rss_max_mo']:.0f} Mo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
