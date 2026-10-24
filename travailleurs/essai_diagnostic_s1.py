"""Diagnostic gratuit d'une fenêtre Sentinel 1, sur le Mac, dans l'image d'AI2 (enquête sur l'analyse 103, 24/10/2026).

Lit la fenêtre dans la copie publique de la scène chez Planetary Computer (mêmes images que Copernicus : l'analyse 103 y
est reproduite au chiffre près), découpe et analyse comme le travailleur (fenetre_pixels, decouper, chaîne d'AI2
avec warp_tps), au seuil de 0,01 et sans aucun filtre (ni régions, ni marge, ni masque de terre), avec ou sans les
deux passages antérieurs que le modèle attend. Écrit les détections brutes (JSON) et une image PNG de la fenêtre
entière : VV en décibels (nord en bas pour un passage ascendant : géométrie de l'image radar), trait de côte GSHHG,
positions AIS données, détections brutes avec leur score. Aucun coût, aucune clé : la copie publique se lit avec un
jeton anonyme. Une scène absente de Planetary Computer ne peut pas être diagnostiquée ainsi.

    docker run --rm --platform linux/amd64 --shm-size=4g -v "$PWD/travailleurs:/mars:ro" \\
      -v "$PWD/data/masks:/masques:ro" -v "$DOSSIER:/d" --entrypoint python3 <image d'AI2> \\
      /mars/essai_diagnostic_s1.py /d/diagnostic.json

diagnostic.json : {"produit": "S1D_IW_GRDH_1SDV_…", "bbox": [lon_min, lat_min, lon_max, lat_max],
                   "historique": ["S1C_IW_GRDH_…", "S1D_IW_GRDH_…"] (facultatif, même orbite relative),
                   "ais": [{"nom": "SMYRTOS", "lon": …, "lat": …, "evidence": true}, …] (facultatif),
                   "reperes": [{"nom": "détection 3", "lon": …, "lat": …}] (facultatif)}
Sortie dans le même dossier : brutes_sans.json, brutes_historique.json, diagnostic_sans.png, diagnostic_historique.png.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import urllib.request

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd/items/"
JETON = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-grd"
GSHHG = "/masques/GSHHS_f_L1.shp"


def worker():
    spec = importlib.util.spec_from_file_location("travailleur_sentinel", "/mars/sentinel.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def adresses(produit: str, jeton: str) -> dict:
    item = json.load(urllib.request.urlopen(STAC + produit, timeout=60))
    return {p: "/vsicurl/" + item["assets"][p]["href"] + "?" + jeton for p in ("vv", "vh")}


def fenetre(w, raw: str, sid: str, urls: dict, bbox: list) -> tuple:
    """Fenêtre découpée comme le travailleur ; noms de fichiers à la façon de Copernicus (AI2 lit la polarisation dans
    le quatrième champ du nom)."""
    from osgeo import gdal
    mdir = os.path.join(raw, sid, "measurement")
    os.makedirs(mdir, exist_ok=True)
    win = w.fenetre_pixels(gdal.Open(urls["vv"]), bbox)
    files = {}
    for pol in ("vv", "vh"):
        files[pol] = os.path.join(mdir, "s1x-iw-grd-%s-diagnostic-cog.tiff" % pol)
        w.decouper(urls[pol], win, files[pol])
    return win, files


def analyser(w, cfg: dict, jeton: str, mode: str, sortie: str) -> tuple:
    import torch
    tmp = tempfile.mkdtemp(prefix="diag_", dir=sortie)
    raw = os.path.join(tmp, "raw")
    sid = "S1_DIAGNOSTIC.SAFE"
    win, files = fenetre(w, raw, sid, adresses(cfg["produit"], jeton), cfg["bbox"])
    hist = []
    if mode == "historique":
        for k, p in enumerate(cfg.get("historique", [])[:2]):
            fenetre(w, raw, "S1_HISTORIQUE_%d.SAFE" % k, adresses(p, jeton), cfg["bbox"])
            hist.append("S1_HISTORIQUE_%d.SAFE" % k)
    prepare_scenes, detect_vessels = w.ai2()
    dev = torch.device("cpu")
    scratch, out = os.path.join(tmp, "scratch"), os.path.join(tmp, "sortie")
    os.makedirs(scratch)
    os.makedirs(out)
    cat, base = os.path.join(scratch, sid + "_cat.npy"), os.path.join(scratch, sid + "_base.tif")
    prepare_scenes(raw, scratch, sid, hist[0] if hist else None, hist[1] if len(hist) > 1 else None, "sentinel1",
                   cat, base, dev, w.DETECTEUR, w.ATTRIBUTS)
    detect_vessels(w.DETECTEUR, w.ATTRIBUTS, raw, sid, cat, base, out, 2048, 400, 20, 0.01, 10, False, dev,
                   "sentinel1", None)
    brutes = sorted(({"lon": round(float(r["lon"]), 5), "lat": round(float(r["lat"]), 5),
                      "score": round(float(r["score"]), 3), "longueur_m": round(float(r["vessel_length_m"]))}
                     for r in csv.DictReader(open(os.path.join(out, "predictions.csv")))), key=lambda d: -d["score"])
    vv = os.path.join(sortie, "fenetre_vv.tiff")
    shutil.copy(files["vv"], vv)
    shutil.rmtree(tmp, ignore_errors=True)
    return win, hist, brutes, vv


def cote(bbox: list) -> list:
    """Anneaux GSHHG qui touchent l'emprise élargie de 10 km (lecture par OGR, présent dans l'image)."""
    from osgeo import ogr
    if not os.path.exists(GSHHG):
        return []
    ds = ogr.Open(GSHHG)
    lyr = ds.GetLayer()
    m = 0.15
    lyr.SetSpatialFilterRect(bbox[0] - m, bbox[1] - m, bbox[2] + m, bbox[3] + m)
    anneaux = []
    for f in lyr:
        g = f.GetGeometryRef()
        for k in range(g.GetGeometryCount()):
            r = g.GetGeometryRef(k)
            anneaux.append([(r.GetX(i), r.GetY(i)) for i in range(r.GetPointCount())])
    return anneaux


def dessiner(w, vv: str, brutes: list, cfg: dict, titre: str, chemin: str):
    """Image de la fenêtre entière, environ 1 400 pixels de côté (2 × 2 pixels de l'image radar par point)."""
    import numpy as np
    from PIL import Image, ImageDraw
    radar = w.Radar(vv)
    a = radar.band.ReadAsArray().astype("float64")
    K = 2
    h2, w2 = a.shape[0] // K * K, a.shape[1] // K * K
    db = 10 * np.log10(np.maximum((a[:h2, :w2] ** 2).reshape(h2 // K, K, w2 // K, K).mean(axis=(1, 3)), 1))
    lo, hi = np.percentile(db, 2), np.percentile(db, 99.7)
    pic = Image.fromarray((np.clip((db - lo) / (hi - lo), 0, 1) * 255).astype("uint8")).convert("RGB")
    d = ImageDraw.Draw(pic)

    def px(lon, lat):
        x, y = radar.pixel(lon, lat)
        return x / K, y / K

    for ring in cote(cfg["bbox"]):
        pts = []
        for q in ring:
            try:
                pts.append(px(*q))
            except ValueError:
                pass
        if len(pts) > 1:
            d.line(pts, fill=(80, 220, 255), width=2)
    for v in cfg.get("ais", []):
        x, y = px(v["lon"], v["lat"])
        coul = (232, 91, 199) if v.get("evidence") else (95, 211, 165)
        d.ellipse([x - 12, y - 12, x + 12, y + 12], outline=coul, width=3 if v.get("evidence") else 1)
        d.text((x + 15, y - 14), v.get("nom", "AIS"), fill=coul)
    for v in cfg.get("reperes", []):
        x, y = px(v["lon"], v["lat"])
        d.line([x - 14, y, x + 14, y], fill=(255, 255, 255), width=1)
        d.line([x, y - 14, x, y + 14], fill=(255, 255, 255), width=1)
        d.text((x + 15, y + 4), v.get("nom", ""), fill=(255, 255, 255))
    for det in brutes:
        if det["score"] < 0.2:
            continue
        try:
            x, y = px(det["lon"], det["lat"])
        except ValueError:
            continue
        coul = (255, 220, 0) if det["score"] >= 0.9 else (255, 140, 0) if det["score"] >= 0.5 else (150, 150, 150)
        r = 9 if det["score"] >= 0.5 else 6
        d.rectangle([x - r, y - r, x + r, y + r], outline=coul, width=2)
        if det["score"] >= 0.3:
            d.text((x + r + 2, y - r - 2), ("%.2f" % det["score"]).replace(".", ","), fill=coul)
    d.rectangle([0, 0, pic.size[0], 16], fill=(14, 20, 25))
    d.text((4, 2), titre, fill=(230, 236, 240))
    pic.save(chemin, optimize=True)


def main():
    from osgeo import gdal
    cfg_path = sys.argv[1]
    sortie = os.path.dirname(os.path.abspath(cfg_path))
    cfg = json.load(open(cfg_path))
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    w = worker()
    sys.path.insert(0, w.AI2)
    jeton = json.load(urllib.request.urlopen(JETON, timeout=60))["token"]
    modes = ["sans"] + (["historique"] if cfg.get("historique") else [])
    for mode in modes:
        win, hist, brutes, vv = analyser(w, cfg, jeton, mode, sortie)
        json.dump(brutes, open(os.path.join(sortie, "brutes_%s.json" % mode), "w"), indent=1)
        titre = ("%s, VV, fenetre %s, %s ; cote GSHHG en cyan ; carres : detections brutes (jaune >= 0,9 ; orange "
                 ">= 0,5)" % (cfg["produit"][:32], win, "avec 2 passages anterieurs" if hist else "sans historique"))
        dessiner(w, vv, brutes, cfg, titre, os.path.join(sortie, "diagnostic_%s.png" % mode))
        print(json.dumps({"mode": mode, "srcwin": win, "historique": hist, "detections": len(brutes),
                          "au_dessus_0_9": sum(d["score"] >= 0.9 for d in brutes), "premieres": brutes[:8]}),
              flush=True)


if __name__ == "__main__":
    main()
