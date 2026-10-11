"""Travailleur Sentinel 1 : détection des navires sur les images radar (étape 3, lot C).

Exécuté sur une instance GPU éphémère (NVIDIA L4), dans l'image publique ghcr.io/allenai/vessel-detection-sentinels
(modèle et chaîne d'AI2, licence Apache 2.0, code dans /home/vessel_detection/src). Le serveur de MARS C2 transmet ce
fichier et la tâche par cloud-init (mars/travailleurs.py) ; ce script n'utilise que la bibliothèque standard et ce que
l'image contient déjà (torch, GDAL, numpy, Pillow).

Pour chaque scène (produit GRD en mode IW, tranche de 25 s) : téléchargement des images VV et VH par des adresses de
lecture signées par le serveur (le travailleur ne reçoit aucune clé), fichier vérifié (code HTTP, signature TIFF,
taille), puis chaîne d'AI2 appelée directement (prepare_scenes, puis detect_vessels avec sa signature réelle : le
serveur intégré à l'image, main.py, lui passe des arguments qu'elle n'accepte pas à cette version). Pour chaque
détection dans nos régions : position, score, longueur, largeur, cap (classe la plus probable), vitesse, pêche,
contraste local mesuré sur l'image VV d'origine, et une vignette radar PNG d'environ 3 km de côté (VV en décibels,
marqueur, échelle, nord, date, satellite, polarisation) avec sa géoréférence pour superposer l'AIS. Fichiers effacés
après chaque scène. Mesures : durées de téléchargement et d'analyse, volume, mémoire GPU et mémoire du processus.

Variables : MARS_TACHE (fichier JSON de la tâche). Essai local sur des fichiers déjà présents :
    python3 sentinel.py --local tache.json   (scènes avec « vv_path » et « vh_path », résultat sur la sortie standard)

L'image d'AI2 repose sur Ubuntu 20.04 : Python 3.8. Pas d'écriture postérieure (annotations différées ci dessous, pas
de fusion de dictionnaires par « | »).
"""
from __future__ import annotations

import base64
import csv
import io
import json
import math
import os
import resource
import shutil
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

SRC = "/home/vessel_detection/src"
sys.path.insert(0, SRC)
DETECTEUR = f"{SRC}/model_artifacts/frcnn_cmp2/3dff445"
ATTRIBUTS = f"{SRC}/model_artifacts/attr/c34aa37"
TIFF = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")        # TIFF et BigTIFF, petit ou grand boutiste
TAILLE_MIN = 50_000_000                                           # une image VV ou VH d'une scène fait 0,4 à 0,9 Go
POIDS_MIN = 1_000_000                                             # un pointeur Git LFS fait 134 octets


class Arret(Exception):
    """Cause commune à toutes les scènes (GPU absent, poids absents, accès refusé) : le travailleur s'arrête."""


class FichierInvalide(Exception):
    """Réponse ou fichier inattendu pour une scène."""


def rss_mo() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def check_download(status: int, content_type: str, first: bytes, size: int | None = None):
    if status in (401, 403):
        raise Arret(f"accès refusé par Copernicus (HTTP {status}) : adresse signée expirée, ou clés S3 du serveur "
                    "(CDSE_ACCESS_KEY, CDSE_SECRET_KEY) invalides ou périmées")
    if status != 200 or not first.startswith(TIFF):
        raise FichierInvalide(f"réponse inattendue : HTTP {status}, type {content_type or 'inconnu'}, "
                              f"premiers octets {first[:60]!r}")
    if size is not None and size < TAILLE_MIN:
        raise FichierInvalide(f"image tronquée : {size} octets (au moins {TAILLE_MIN} attendus)")


def download(url: str, dest: str) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": "mars-c2-travailleur"})
    try:
        r = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as e:
        check_download(e.code, e.headers.get("content-type", ""), e.read(200))
        raise
    with r:
        first = r.read(1 << 20)
        check_download(r.status, r.headers.get("content-type", ""), first)
        n = len(first)
        with open(dest, "wb") as f:
            f.write(first)
            while True:
                chunk = r.read(8 << 20)
                if not chunk:
                    break
                f.write(chunk)
                n += len(chunk)
    check_download(200, "", first, n)
    return n


def heading(probs: list[float]) -> tuple[float | None, float | None]:
    """Cap de l'écho : centre de la classe la plus probable parmi 16 classes de 22,5° depuis le nord."""
    if len(probs) != 16 or any(p is None for p in probs):
        return None, None
    j = max(range(16), key=lambda k: probs[k])
    return round((j + 0.5) * 22.5, 1), round(probs[j], 3)


def num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def in_regions(lon: float, lat: float, boxes: list) -> bool:
    return any(x0 <= lon <= x1 and y0 <= lat <= y1 for x0, y0, x1, y1 in boxes)


class Radar:
    """Image VV d'origine (amplitude, géolocalisée par ses points d'appui) : pixel d'un point, fenêtre autour."""

    def __init__(self, path: str):
        from osgeo import gdal
        self.ds = gdal.Open(path)
        self.band = self.ds.GetRasterBand(1)
        self.tr = gdal.Transformer(self.ds, None, [])                # points d'appui (GCP) vers longitude, latitude

    def pixel(self, lon: float, lat: float) -> tuple[float, float]:
        ok, (x, y, _) = self.tr.TransformPoint(1, lon, lat, 0)      # 1 : de la géographie vers l'image
        if not ok:
            raise ValueError("point hors de l'image")
        return x, y

    def lonlat(self, x: float, y: float) -> tuple[float, float]:
        ok, (lon, lat, _) = self.tr.TransformPoint(0, x, y, 0)
        return lon, lat

    def window(self, cx: int, cy: int, half: int):
        import numpy as np
        x0, y0 = max(0, cx - half), max(0, cy - half)
        x1, y1 = min(self.ds.RasterXSize, cx + half), min(self.ds.RasterYSize, cy + half)
        a = self.band.ReadAsArray(x0, y0, x1 - x0, y1 - y0).astype("float64")
        return a, x0, y0, np


def contrast_db(radar: Radar, lon: float, lat: float, peak_half: int = 2, ring_in: int = 10, ring_out: int = 40):
    """Contraste local en décibels : intensité maximale du pic (5 × 5 pixels) contre la médiane d'un anneau de 10 à
    40 pixels. Rapport d'intensités : la calibration absolue s'annule, la mesure vaut en sigma0 comme en compte brut."""
    x, y = radar.pixel(lon, lat)
    a, x0, y0, np = radar.window(int(round(x)), int(round(y)), ring_out)
    inten = a ** 2
    cy, cx = int(round(y)) - y0, int(round(x)) - x0
    peak = inten[max(0, cy - peak_half):cy + peak_half + 1, max(0, cx - peak_half):cx + peak_half + 1]
    yy, xx = np.mgrid[0:inten.shape[0], 0:inten.shape[1]]
    r = np.hypot(yy - cy, xx - cx)
    ring = inten[(r >= ring_in) & (r <= ring_out) & (inten > 0)]
    if not peak.size or not ring.size or np.median(ring) <= 0:
        return None
    return round(float(10 * np.log10(peak.max() / np.median(ring))), 1)


def _hav_m(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def vignette(radar: Radar, lon: float, lat: float, km: float, legend: dict) -> tuple[bytes, dict]:
    """Vignette PNG de `km` de côté autour de la détection, VV en décibels étiré entre les centiles 2 et 99,5, dans la
    géométrie de l'image radar ; géoréférence (pixels de la vignette en fonction de lon, lat, moindres carrés sur une
    grille de points d'appui) pour superposer l'AIS dans l'interface."""
    from PIL import Image, ImageDraw, ImageFont
    x, y = radar.pixel(lon, lat)
    lon_r, lat_r = radar.lonlat(x + 100, y)
    mpp_img = _hav_m(lon, lat, lon_r, lat_r) / 100 or 10.0            # mètres par pixel de l'image (environ 10)
    half = max(20, int(km * 1000 / mpp_img / 2))
    a, x0, y0, np = radar.window(int(round(x)), int(round(y)), half)
    # Chatoiement adouci : intensité moyennée sur 2 × 2 pixels (20 m, la résolution réelle d'un GRD haute résolution),
    # 32 niveaux de gris, palette : quelques dizaines de kilooctets au lieu de 200
    k = 2
    h2, w2 = a.shape[0] // k * k, a.shape[1] // k * k
    inten = (a[:h2, :w2] ** 2).reshape(h2 // k, k, w2 // k, k).mean(axis=(1, 3))
    db = 10 * np.log10(np.maximum(inten, 1))
    lo, hi = np.percentile(db, 2), np.percentile(db, 99.5)
    v = (np.clip((db - lo) / max(hi - lo, 1e-6), 0, 1) * 31).astype("uint8") * 8
    pic = Image.fromarray(v).resize((w2, h2), Image.NEAREST).convert("RGB")
    W, H = pic.size
    # Géoréférence : grille de 5 × 5 points de la fenêtre, lon et lat par les points d'appui
    pts = [(i, j) for i in np.linspace(0, W - 1, 5) for j in np.linspace(0, H - 1, 5)]
    ll = [radar.lonlat(x0 + i, y0 + j) for i, j in pts]
    A = np.c_[[p[0] for p in ll], [p[1] for p in ll], np.ones(len(ll))]
    cx_ = np.linalg.lstsq(A, [p[0] for p in pts], rcond=None)[0]
    cy_ = np.linalg.lstsq(A, [p[1] for p in pts], rcond=None)[0]
    mpp = _hav_m(ll[0][0], ll[0][1], ll[-1][0], ll[-1][1]) / math.hypot(W - 1, H - 1)
    d = ImageDraw.Draw(pic)
    font = ImageFont.load_default()
    px, py = x - x0, y - y0
    d.ellipse([px - 12, py - 12, px + 12, py + 12], outline=(232, 91, 199), width=2)
    bar = 1000 / mpp if mpp > 0 else 0                                 # échelle de 1 km
    if 0 < bar < W - 20:
        d.rectangle([8, H - 22, 8 + bar, H - 19], fill=(230, 236, 240))
        d.text((8, H - 16), "1 km", fill=(230, 236, 240), font=font)
    d.rectangle([0, 0, W, 13], fill=(14, 20, 25))
    nx, ny = cx_[1], cy_[1]                                            # direction de la latitude croissante
    n = math.hypot(nx, ny) or 1
    ax, ay = W - 16, 40
    d.line([ax, ay, ax + 14 * nx / n, ay + 14 * ny / n], fill=(230, 236, 240), width=2)
    d.text((ax + 20 * nx / n - 3, ay + 20 * ny / n - 5), "N", fill=(230, 236, 240), font=font)
    d.text((3, 1), f"{legend.get('satellite', '')}  {legend.get('date', '')}  VV", fill=(230, 236, 240), font=font)
    buf = io.BytesIO()
    pic.convert("P", palette=Image.ADAPTIVE, colors=40).save(buf, "PNG", optimize=True)
    geo = {"x": [round(float(c), 6) for c in cx_], "y": [round(float(c), 6) for c in cy_], "largeur": W, "hauteur": H,
           "m_par_px": round(mpp, 2)}
    return buf.getvalue(), geo


def preflight(force_cpu: bool = False):
    """GPU disponible et poids des modèles présents dans l'image (sinon, arrêt avec la cause)."""
    import torch
    for d in (DETECTEUR, ATTRIBUTS):
        w = os.path.join(d, "best.pth")
        if not os.path.exists(w) or os.path.getsize(w) < POIDS_MIN:
            raise Arret(f"poids du modèle absents de l'image ({w} : "
                        f"{os.path.getsize(w) if os.path.exists(w) else 'introuvable'} octets, pointeur Git LFS ?)")
    if force_cpu:
        return torch.device("cpu"), {"gpu": None}
    if not torch.cuda.is_available():
        raise Arret("aucun GPU visible par PyTorch dans le conteneur (pilotes NVIDIA, option --gpus all, ou CUDA 11.3 "
                    "de l'image incompatible avec le pilote)")
    p = torch.cuda.get_device_properties(0)
    torch.zeros(1, device="cuda")                                     # noyau CUDA réellement exécuté
    return torch.device("cuda"), {"gpu": p.name, "gpu_memoire_mo": round(p.total_memory / 2 ** 20),
                                  "cuda": torch.version.cuda, "torch": torch.__version__}


def analyse(scene: dict, task: dict, device, tmp: str, local: bool) -> dict:
    import torch
    from data.image import prepare_scenes
    from inference.pipeline import detect_vessels
    scene_id = scene["id"] if scene["id"].endswith(".SAFE") else scene["id"] + ".SAFE"
    raw = os.path.join(tmp, "raw")
    mdir = os.path.join(raw, scene_id, "measurement")
    os.makedirs(mdir, exist_ok=True)
    r = {"analyse": scene["analyse"], "id": scene["id"], "debut": scene["debut"], "fin": scene["fin"]}
    t = time.time()
    octets, files = 0, {}
    for pol in ("vv", "vh"):
        if local:
            src = scene[f"{pol}_path"]
            files[pol] = os.path.join(mdir, os.path.basename(src))
            os.symlink(os.path.abspath(src), files[pol])
        else:
            name = scene[pol].split("?", 1)[0].rsplit("/", 1)[1]
            files[pol] = os.path.join(mdir, name)
            octets += download(scene[pol], files[pol])
    r["telecharge_s"], r["telecharge_mo"] = round(time.time() - t, 1), round(octets / 1e6, 1)
    t = time.time()
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()
    scratch, out = os.path.join(tmp, "scratch"), os.path.join(tmp, "sortie")
    os.makedirs(scratch, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    cat_path, base_path = os.path.join(scratch, scene_id + "_cat.npy"), os.path.join(scratch, scene_id + "_base.tif")
    prepare_scenes(raw, scratch, scene_id, None, None, "sentinel1", cat_path, base_path, device, DETECTEUR, ATTRIBUTS)
    detect_vessels(DETECTEUR, ATTRIBUTS, raw, scene_id, cat_path, base_path, out, 2048, 400, 20,
                   float(task.get("confiance", 0.9)), float(task.get("nms_m", 10)), False, device, "sentinel1", False)
    r["analyse_s"] = round(time.time() - t, 1)
    if device.type == "cuda":
        r["gpu_max_mo"] = round(torch.cuda.max_memory_allocated() / 2 ** 20)
    radar = Radar(files["vv"])
    dets, hors = [], 0
    with open(os.path.join(out, "predictions.csv")) as f:
        for row in csv.DictReader(f):
            lon, lat = num(row.get("lon")), num(row.get("lat"))
            if lon is None or lat is None or not in_regions(lon, lat, task["regions"]):
                hors += 1
                continue
            cap, cap_p = heading([num(row.get(f"heading_bucket_{k}")) for k in range(16)])
            det = {"lon": round(lon, 6), "lat": round(lat, 6), "score": num(row.get("score")),
                   "longueur_m": num(row.get("vessel_length_m")), "largeur_m": num(row.get("vessel_width_m")),
                   "cap_deg": cap, "cap_prob": cap_p, "vitesse_kn": num(row.get("vessel_speed_k")),
                   "peche": num(row.get("is_fishing_vessel"))}
            try:                                     # une mesure ou une vignette en échec n'empêche pas la détection
                det["contraste_db"] = contrast_db(radar, lon, lat)
                png, geo = vignette(radar, lon, lat, float(task.get("vignette_km", 3)),
                                    {"satellite": scene.get("satellite", "").replace("S1", "Sentinel 1"),
                                     "date": f"{scene['debut'][8:10]}/{scene['debut'][5:7]}/{scene['debut'][:4]} "
                                             f"{scene['debut'][11:16]} UTC"})
                det["vignette"], det["vignette_geo"] = base64.b64encode(png).decode(), geo
            except Exception as e:
                det["vignette_erreur"] = f"{type(e).__name__}: {e}"[:200]
            dets.append(det)
    r["detections"], r["hors_regions"] = dets, hors
    return r


def run(task: dict, local: bool = False) -> dict:
    t0 = time.time()
    out, stop, octets = [], None, 0.0
    try:
        device, gpu = preflight(force_cpu=bool(task.get("force_cpu")))
    except Arret as e:
        return {"scenes": [], "erreur": str(e)[:500], "mesures": {"rss_max_mo": rss_mo()}}
    print(json.dumps({"preparation": gpu, "s": round(time.time() - t0, 1)}), flush=True)
    for k, scene in enumerate(task["scenes"]):
        tmp = tempfile.mkdtemp(prefix="scene_", dir=task.get("travail"))
        try:
            r = analyse(scene, task, device, tmp, local)
            octets += r.get("telecharge_mo", 0)
        except (Arret, FichierInvalide) as e:
            r = {"analyse": scene["analyse"], "id": scene["id"], "debut": scene["debut"], "fin": scene["fin"],
                 "erreur": str(e)[:500]}
            if isinstance(e, Arret) or k == 0:               # cause commune, ou première scène déjà en échec
                stop = str(e)[:500]
        except Exception as e:                               # une autre scène en échec n'arrête pas les suivantes
            r = {"analyse": scene["analyse"], "id": scene["id"], "debut": scene["debut"], "fin": scene["fin"],
                 "erreur": f"{type(e).__name__}: {e}"[:500]}
            traceback.print_exc()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)           # images effacées après chaque scène
        r["rss_max_mo"] = rss_mo()
        out.append(r)
        print(json.dumps({**{x: v for x, v in r.items() if x != "detections"}, "n": len(r.get("detections", []))}),
              flush=True)
        if stop:
            print(f"arrêt du travailleur : {stop}", flush=True)
            break
    result = {"scenes": out, "mesures": {"duree_s": round(time.time() - t0, 1), "rss_max_mo": rss_mo(),
                                         "telecharge_mo": round(octets, 1), **gpu}}
    if stop:
        result["erreur"] = stop
    return result


def send(task: dict, path: str, body: dict):
    """Envoi au serveur par le réseau privé, avec quelques nouvelles tentatives (le serveur peut redémarrer)."""
    data = json.dumps(body).encode()
    for k in range(6):
        try:
            req = urllib.request.Request(f"{task['retour']}{path}", data=data, method="POST",
                                         headers={"Authorization": f"Bearer {task['jeton']}",
                                                  "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return e.code
        except Exception as e:
            print(f"envoi impossible ({e}), nouvel essai", flush=True)
        time.sleep(10 * (k + 1))
    return None


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--local":
        task = json.load(open(sys.argv[2]))
        print(json.dumps(run(task, local=True)))
        return
    task = json.load(open(os.environ.get("MARS_TACHE", "/mars/tache.json")))
    send(task, "/etat", {"etat": "demarre"})
    try:
        result = run(task)
    except Exception as e:
        result = {"scenes": [], "erreur": f"{type(e).__name__}: {e}"[:1000], "mesures": {"rss_max_mo": rss_mo()}}
    send(task, "/resultats", result)


if __name__ == "__main__":
    main()
