"""Travailleur Sentinel 1 : détection des navires sur les images radar (étape 3, lot C).

Exécuté sur une instance éphémère, sur processeur (GP1-S ou POP2-8C-32G ; voie GPU retirée au lot Z4), dans l'image
publique ghcr.io/allenai/vessel-detection-sentinels
(modèle et chaîne d'AI2, licence Apache 2.0, code dans /home/vessel_detection/src). Le serveur de MARS C2 transmet ce
fichier par cloud-init (mars/travailleurs.py), et la tâche par le réseau privé ; ce script n'utilise que la bibliothèque standard et ce que
l'image contient déjà (torch, GDAL, numpy, Pillow).

Tâche remise par le réseau privé (route etat du serveur, {"etat": "tache"}, une seule fois, jeton de l'exécution),
avec la paire de clés S3 de Copernicus dédiée aux travailleurs : jamais dans le cloud-init, jamais affichée ni
journalisée, gardée en mémoire seulement. Copernicus refuse les adresses présignées (14/10/2026) : chaque image est lue
avec une signature par en tête (AWS version 4, bibliothèque standard).

Pour chaque scène (produit GRD en mode IW, tranche de 25 s) : téléchargement des images VV et VH (s3://eodata/…),
fichier vérifié (code HTTP, signature TIFF, taille), puis chaîne d'AI2 appelée directement (prepare_scenes, puis detect_vessels avec sa signature réelle : le
serveur intégré à l'image, main.py, lui passe des arguments qu'elle n'accepte pas à cette version). Pour chaque
détection dans nos régions : position, score, longueur, largeur, cap (classe la plus probable), vitesse, pêche,
contraste local mesuré sur l'image VV d'origine, et une vignette radar PNG d'environ 3 km de côté (VV en décibels,
marqueur, échelle, nord, date, satellite, polarisation) avec sa géoréférence pour superposer l'AIS. Fichiers effacés
après chaque scène. Mesures : durées de téléchargement et d'analyse, volume, mémoire du processus.

Variables : MARS_TACHE (fichier JSON de la tâche). Essai local sur des fichiers déjà présents :
    python3 sentinel.py --local tache.json   (scènes avec « vv_path » et « vh_path », résultat sur la sortie standard)

L'image d'AI2 repose sur Ubuntu 20.04 : Python 3.8. Pas d'écriture postérieure (annotations différées ci dessous, pas
de fusion de dictionnaires par « | »).
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
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
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Le code d'AI2 importe tout sous la forme src.* (src.data.retrieve, src.models…) : c'est la racine
# /home/vessel_detection qui doit être sur sys.path, pas .../src (second essai réel du 14/10/2026 : « No module named
# 'src' » ; le main.py de l'image, qui importe data.image, échoue de la même façon)
AI2 = "/home/vessel_detection"
SRC = f"{AI2}/src"
sys.path.insert(0, AI2)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # otel_mini, déposé à côté par cloud-init
import otel_mini  # noqa: E402
DETECTEUR = f"{SRC}/model_artifacts/frcnn_cmp2/3dff445"
ATTRIBUTS = f"{SRC}/model_artifacts/attr/c34aa37"
TIFF = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")        # TIFF et BigTIFF, petit ou grand boutiste
TAILLE_MIN = 50_000_000                                           # une image VV ou VH d'une scène fait 0,4 à 0,9 Go
POIDS_MIN = 1_000_000                                             # un pointeur Git LFS fait 134 octets
# Géolocalisation par les points d'appui : spline en plaque mince, exacte aux points de la grille de l'ESA. Le polynôme
# d'ordre 2 que GDAL choisit par défaut s'en écarte de 54 m en médiane et jusqu'à 544 m sur une vraie scène (S1D du
# 23/10/2026, 210 points ; 75 à 100 m en baie de Weymouth) : enquête sur l'analyse 103, tests/test_geolocalisation_s1.py
GCP_TPS = ["METHOD=GCP_TPS"]


class Arret(Exception):
    """Cause commune à toutes les scènes (chaîne non importable, poids absents, accès refusé) : le travailleur
    s'arrête."""


class FichierInvalide(Exception):
    """Réponse ou fichier inattendu pour une scène."""


def rss_mo() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def check_download(status: int, content_type: str, first: bytes, size: int | None = None):
    if status in (401, 403):
        raise Arret(f"accès refusé par Copernicus (HTTP {status}) : clés dédiées aux travailleurs "
                    "(CDSE_TRAVAILLEUR_ACCESS_KEY, CDSE_TRAVAILLEUR_SECRET_KEY) invalides ou périmées")
    if status != 200 or not first.startswith(TIFF):
        raise FichierInvalide(f"réponse inattendue : HTTP {status}, type {content_type or 'inconnu'}, "
                              f"premiers octets {first[:60]!r}")
    if size is not None and size < TAILLE_MIN:
        raise FichierInvalide(f"image tronquée : {size} octets (au moins {TAILLE_MIN} attendus)")


# Clés S3 remises avec la tâche : en mémoire seulement (jamais dans la tâche, un fichier ou la sortie)
_S3: dict = {}
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def s3_headers(host: str, path: str, access_key: str, secret_key: str, region: str = "default",
               now: datetime | None = None) -> dict:
    """En têtes d'une lecture S3 signée par en tête (AWS version 4, corps vide), comme mars/r2_signature.signed_headers
    côté serveur (vérifiés tous deux contre botocore, tests/test_cles_travailleur.py)."""
    now = now or datetime.now(timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = day + "/" + region + "/s3/aws4_request"
    names = "host;x-amz-content-sha256;x-amz-date"
    canonical = "\n".join(["GET", urllib.parse.quote(path, safe="/-_.~"), "",
                           "host:" + host + "\nx-amz-content-sha256:" + EMPTY_SHA256 + "\nx-amz-date:" + amz_date + "\n",
                           names, EMPTY_SHA256])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), day), region), "s3"), "aws4_request")
    sig = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "AWS4-HMAC-SHA256 Credential=" + access_key + "/" + scope + ", SignedHeaders=" + names
            + ", Signature=" + sig, "x-amz-date": amz_date, "x-amz-content-sha256": EMPTY_SHA256}


def _request_for(href: str) -> urllib.request.Request:
    """Requête de lecture d'une image : s3://eodata/… avec les clés remises (signature par en tête), sinon adresse
    https:// telle quelle (essais)."""
    headers = {"User-Agent": "mars-c2-travailleur"}
    if href.startswith("s3://"):
        if not _S3.get("access_key"):
            raise Arret("aucune clé S3 remise avec la tâche : lecture des images impossible")
        host, path = _S3.get("endpoint", "eodata.dataspace.copernicus.eu"), "/" + href[len("s3://"):]
        headers.update(s3_headers(host, path, _S3["access_key"], _S3["secret_key"], _S3.get("region", "default")))
        schema = "http://" if _S3.get("http") else "https://"        # http : faux S3 local des essais seulement
        return urllib.request.Request(schema + host + urllib.parse.quote(path, safe="/-_.~"), headers=headers)
    return urllib.request.Request(href, headers=headers)


def fetch_task(task: dict, traceparent: str | None = None) -> dict:
    """Tâche remise par le réseau privé, une seule fois : paramètres et clés S3. Les clés vont dans _S3, pas dans la
    tâche renvoyée. Trois essais en cas d'erreur réseau ; un refus (403 jeton, 409 déjà remise, 503 clés absentes du
    serveur) arrête le travailleur avec son motif."""
    data = json.dumps({"etat": "tache"}).encode()
    for k in range(3):
        try:
            headers = {"Authorization": "Bearer " + task["jeton"], "Content-Type": "application/json"}
            if traceparent:
                headers["traceparent"] = traceparent
            req = urllib.request.Request(task["retour"] + "/etat", data=data, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.loads(r.read().decode())
            _S3.update((body.pop("identifiants", None) or {}).get("s3") or {})
            return {**body, "retour": task["retour"], "jeton": task["jeton"]}
        except urllib.error.HTTPError as e:
            raise Arret("tâche refusée par le serveur (HTTP " + str(e.code) + ") : " + e.read(300).decode("utf-8", "replace"))
        except Exception as e:
            print("tâche non reçue (" + type(e).__name__ + "), nouvel essai", flush=True)
            time.sleep(10 * (k + 1))
    raise Arret("tâche non reçue du serveur après trois essais (réseau privé)")


def download(url: str, dest: str) -> int:
    req = _request_for(url)
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
        self.tr = gdal.Transformer(self.ds, None, GCP_TPS)           # points d'appui (GCP) vers longitude, latitude

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


def warp_tps(image, in_path: str, out_path: str, projection: str | None = None):
    """src.data.warp.warp d'AI2 (Web Mercator, pas du premier niveau plus fin que 1,1 fois le pixel, bilinéaire),
    avec la spline en plaque mince des points d'appui (« -tps ») au lieu du polynôme d'ordre 2 de gdalwarp."""
    import subprocess
    from osgeo import gdal
    from src.data import warp as w
    if projection != "epsg:3857":
        return w.warp(image, in_path, out_path, projection)
    pas, zoom = None, None
    for zoom in range(20):
        pas = w.web_mercator_m / 512 / (2 ** zoom)
        if pas < (image.pixel_size or 10) * 1.1:
            break
    subprocess.check_output(["gdalwarp", "-tps", "-r", "bilinear", "-t_srs", projection, "-tr", str(pas), str(pas),
                             in_path, out_path, "-overwrite"])
    raster = gdal.Open(out_path)
    gt = raster.GetGeoTransform()
    return w.ImageInfo(width=raster.RasterXSize, height=raster.RasterYSize, bounds=w.get_wgs84_bounds(raster),
                       column=int((gt[0] + w.web_mercator_m / 2) / pas),
                       row=int((w.web_mercator_m - (gt[3] + w.web_mercator_m / 2)) / pas), zoom=zoom,
                       projection=projection)


def ai2():
    """Chaîne d'AI2 (prepare_scenes, detect_vessels), importée sous la forme src.*. Un import en échec est une cause
    commune à toutes les scènes : arrêt avant tout téléchargement. Sa reprojection passe par warp_tps (remplacée dans
    le module, héritée par les processus du Pool d'AI2, créés par fork)."""
    try:
        import src.data.image as image
        from src.data.image import prepare_scenes
        from src.inference.pipeline import detect_vessels
    except Exception as e:
        raise Arret(f"chaîne d'AI2 non importable ({type(e).__name__}: {e})")
    image.warp = warp_tps
    return prepare_scenes, detect_vessels


def preflight():
    """Chaîne d'AI2 importable et poids des modèles présents dans l'image (sinon, arrêt avec la cause) ; calcul sur
    processeur."""
    import torch
    ai2()
    for d in (DETECTEUR, ATTRIBUTS):
        w = os.path.join(d, "best.pth")
        if not os.path.exists(w) or os.path.getsize(w) < POIDS_MIN:
            raise Arret(f"poids du modèle absents de l'image ({w} : "
                        f"{os.path.getsize(w) if os.path.exists(w) else 'introuvable'} octets, pointeur Git LFS ?)")
    return torch.device("cpu"), {"torch": torch.__version__}


def seuil(task: dict, n_historique: int) -> float:
    """Seuil de détection : celui d'AI2 avec au moins un passage antérieur, un seuil plus prudent sans historique
    (le modèle prend alors les structures fixes pour des navires : brise lames de Portland à 0,909, analyse 103)."""
    if n_historique:
        return float(task.get("confiance", 0.9))
    return float(task.get("confiance_sans_historique", task.get("confiance", 0.9)))


def _detecter(raw: str, scene_id: str, files: dict, scene: dict, task: dict, device, tmp: str, tr, coeur=None,
              historiques: list | None = None) -> dict:
    """Chaîne d'AI2 sur une image (scène entière ou fenêtre, rangée en SAFE sous `raw`), puis mesures et vignettes
    sur l'image VV d'origine. `coeur` (lon_min, lat_min, lon_max, lat_max) : seules les détections dedans sont gardées
    (fenêtre : les objets coupés au bord sont écartés). `historiques` : noms SAFE, sous `raw`, des passages antérieurs
    de la même fenêtre (deux au plus), que le détecteur d'AI2 compare à l'image pour éteindre ce qui ne bouge pas ;
    AI2 les aligne sur l'image (géoréférence, puis recalage de 32 pixels au plus)."""
    prepare_scenes, detect_vessels = ai2()
    r = {}
    t = time.time()
    base = os.path.join(tmp, "travail_" + scene_id)
    scratch, out = os.path.join(base, "scratch"), os.path.join(base, "sortie")
    os.makedirs(scratch, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    cat_path, base_path = os.path.join(scratch, scene_id + "_cat.npy"), os.path.join(scratch, scene_id + "_base.tif")
    hist = list(historiques or [])[:2]
    conf = seuil(task, len(hist))
    with tr.span("sentinel.preparation_scene", **{"sentinel.historique": len(hist)}):
        prepare_scenes(raw, scratch, scene_id, hist[0] if hist else None, hist[1] if len(hist) > 1 else None,
                       "sentinel1", cat_path, base_path, device, DETECTEUR, ATTRIBUTS)
    # nms_thresh : annoncé float mais utilisé en division entière par la grille de la chaîne d'AI2 (range) ; un décimal
    # la fait échouer (« 'float' object cannot be interpreted as an integer », essai dans l'image du 15/10/2026)
    with tr.span("sentinel.inference", **{"sentinel.appareil": device.type, "sentinel.historique": len(hist),
                                          "sentinel.seuil": conf}):
        detect_vessels(DETECTEUR, ATTRIBUTS, raw, scene_id, cat_path, base_path, out, 2048, 400, 20,
                       conf, int(round(float(task.get("nms_m", 10)))), False, device, "sentinel1", None)
    r["analyse_s"] = round(time.time() - t, 1)
    r["confiance"] = conf
    with tr.span("sentinel.mesures_et_vignettes") as ms:
        radar = Radar(files["vv"])
        dets, hors, bord = [], 0, 0
        with open(os.path.join(out, "predictions.csv")) as f:
            for row in csv.DictReader(f):
                lon, lat = num(row.get("lon")), num(row.get("lat"))
                if lon is None or lat is None or not in_regions(lon, lat, task["regions"]):
                    hors += 1
                    continue
                if coeur and not in_regions(lon, lat, [coeur]):
                    bord += 1                                # marge de la fenêtre : vue par une autre, ou coupée
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
        ms.set(**{"sentinel.detections": len(dets), "sentinel.hors_regions": hors, "sentinel.marge": bord})
    r.update({"detections": dets, "hors_regions": hors, "ecartees_marge": bord})
    return r


def nom_local(pol: str) -> str:
    """Nom d'une image découpée : la chaîne d'AI2 lit la polarisation dans le quatrième champ (séparé par « - ») ;
    nommer soi même la copie rend la lecture indépendante du nom chez la source (Copernicus, Planetary Computer)."""
    return f"s1-iw-grd-{pol}-fenetre-cog.tiff"


def lire_historique(tmp: str, k: int, j: int, h: dict, bbox: list, local: bool, ouverts: dict, tr) -> tuple:
    """Même emprise découpée dans le passage antérieur `j` (VV et VH), sa propre géolocalisation : nom SAFE sous la
    racine de la fenêtre, octets lus. FenetreInvalide si le passage ne couvre pas la fenêtre."""
    from osgeo import gdal
    srcs = {pol: (h[f"{pol}_path"] if local else source_image(h[pol])) for pol in ("vv", "vh")}
    if j not in ouverts:
        ouverts[j] = gdal.Open(srcs["vv"])
    if ouverts[j] is None:
        raise FenetreInvalide(f"passage antérieur illisible : {gdal.GetLastErrorMsg()[:200]}")
    win = fenetre_pixels(ouverts[j], bbox)
    sid = f"HIST{j}_F{k}.SAFE"
    mdir = os.path.join(tmp, "raw_f" + str(k), sid, "measurement")
    os.makedirs(mdir, exist_ok=True)
    n = 0
    for pol in ("vv", "vh"):
        with tr.span("GET S3 eodata " + pol.upper() + " fenêtre historique",
                     **{"peer.service": "Copernicus", "sentinel.polarisation": pol, "sentinel.historique": j}) as sd:
            m = decouper(srcs[pol], win, os.path.join(mdir, nom_local(pol)))
            sd.set(**{"sentinel.octets": m})
            n += m
    return sid, win, n


def analyse(scene: dict, task: dict, device, tmp: str, local: bool, tr=None) -> dict:
    """Scène entière : images VV et VH téléchargées (passage choisi à la main sans point d'intérêt)."""
    tr = tr or otel_mini.Traceur(None, None, None)
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
            with tr.span("GET S3 eodata " + pol.upper(), **{"peer.service": "Copernicus",
                                                            "sentinel.polarisation": pol}) as sd:
                n = download(scene[pol], files[pol])
                sd.set(**{"sentinel.octets": n})
                octets += n
    r["telecharge_s"], r["telecharge_mo"], r["octets_lus"] = round(time.time() - t, 1), round(octets / 1e6, 1), octets
    r.update(_detecter(raw, scene_id, files, scene, task, device, tmp, tr))
    return r


# Lecture par fenêtres (lot C3) : l'image COG reste chez Copernicus, GDAL n'en lit que les tuiles utiles par requêtes
# partielles (plages d'octets), signées par en tête avec la paire dédiée (AWS version 4, comme s3_headers)

COTE_MIN_PX = 2300            # carreaux de 2 048 pixels Web Mercator de la chaîne d'AI2, après reprojection


class FenetreInvalide(Exception):
    """Fenêtre hors de l'image, ou trop petite pour la chaîne d'AI2 (bord de scène)."""


def configurer_s3():
    """Accès /vsis3/ de GDAL à Copernicus : clés en mémoire seulement (options du processus, jamais écrites)."""
    from osgeo import gdal
    host = _S3.get("endpoint", "eodata.dataspace.copernicus.eu")
    for k, v in (("AWS_S3_ENDPOINT", host), ("AWS_VIRTUAL_HOSTING", "FALSE"),
                 ("AWS_HTTPS", "NO" if _S3.get("http") else "YES"), ("AWS_REGION", _S3.get("region", "default")),
                 ("AWS_ACCESS_KEY_ID", _S3.get("access_key", "")), ("AWS_SECRET_ACCESS_KEY", _S3.get("secret_key", "")),
                 ("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR"), ("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tiff,.tif"),
                 ("GDAL_HTTP_MAX_RETRY", "3"), ("GDAL_HTTP_RETRY_DELAY", "2"), ("VSI_CACHE", "TRUE"),
                 ("CPL_VSIL_NETWORK_STATS_ENABLED", "YES")):
        gdal.SetConfigOption(k, v)


def source_image(href: str) -> str:
    """Chemin GDAL d'une image : s3://eodata/… devient /vsis3/eodata/… ; un chemin local reste tel quel (essais)."""
    return "/vsis3/" + href[len("s3://"):] if href.startswith("s3://") else href


def octets_tuiles(band, x0: int, y0: int, w: int, h: int) -> int:
    """Octets des tuiles de l'image qui recouvrent la fenêtre (index du TIFF : ce que GDAL doit lire)."""
    bw, bh = band.GetBlockSize()
    total = 0
    for by in range(y0 // bh, (y0 + h - 1) // bh + 1):
        for bx in range(x0 // bw, (x0 + w - 1) // bw + 1):
            total += int(band.GetMetadataItem(f"BLOCK_SIZE_{bx}_{by}", "TIFF") or 0)
    return total


def fenetre_pixels(ds, bbox: list) -> list:
    """Fenêtre en pixels (x0, y0, largeur, hauteur) couvrant l'emprise lon, lat, par les points d'appui de l'image
    (les GRD ne sont pas géoréférencés par une transformation affine). Agrandie au côté minimal dans l'image si besoin ;
    FenetreInvalide si l'image est trop petite ou ne la couvre pas."""
    from osgeo import gdal
    tr = gdal.Transformer(ds, None, GCP_TPS)
    x0g, y0g, x1g, y1g = bbox
    xs, ys = [], []
    for k in range(5):
        for lon, lat in ((x0g + (x1g - x0g) * k / 4, y0g), (x0g + (x1g - x0g) * k / 4, y1g),
                         (x0g, y0g + (y1g - y0g) * k / 4), (x1g, y0g + (y1g - y0g) * k / 4)):
            ok, (x, y, _) = tr.TransformPoint(1, lon, lat, 0)
            if ok:
                xs.append(x)
                ys.append(y)
    if not xs:
        raise FenetreInvalide("fenêtre hors de la géolocalisation de l'image")
    W, H = ds.RasterXSize, ds.RasterYSize
    x0, x1 = max(0, int(min(xs))), min(W, int(math.ceil(max(xs))))
    y0, y1 = max(0, int(min(ys))), min(H, int(math.ceil(max(ys))))
    if x1 <= x0 or y1 <= y0:
        raise FenetreInvalide("fenêtre hors de l'image")
    if x1 - x0 < COTE_MIN_PX:                        # fenêtre agrandie dans l'image, autour de son centre
        c = (x0 + x1) // 2
        x0 = max(0, min(c - COTE_MIN_PX // 2, W - COTE_MIN_PX))
        x1 = min(W, x0 + COTE_MIN_PX)
    if y1 - y0 < COTE_MIN_PX:
        c = (y0 + y1) // 2
        y0 = max(0, min(c - COTE_MIN_PX // 2, H - COTE_MIN_PX))
        y1 = min(H, y0 + COTE_MIN_PX)
    if x1 - x0 < COTE_MIN_PX or y1 - y0 < COTE_MIN_PX:
        raise FenetreInvalide(f"image trop petite pour une fenêtre de {COTE_MIN_PX} pixels ({W} × {H})")
    return [x0, y0, x1 - x0, y1 - y0]


def decouper(src: str, srcwin: list, dst: str) -> int:
    """Copie locale de la fenêtre (GeoTIFF tuilé), points d'appui recalés sur la fenêtre par GDAL ; retourne les
    octets des tuiles lues."""
    from osgeo import gdal
    ds = gdal.Open(src)
    if ds is None:
        raise FenetreInvalide(f"image illisible : {gdal.GetLastErrorMsg()[:200]}")
    n = octets_tuiles(ds.GetRasterBand(1), *srcwin)
    out = gdal.Translate(dst, ds, srcWin=srcwin, creationOptions=["TILED=YES"])
    if out is None or out.GetGCPCount() == 0:
        raise FenetreInvalide("fenêtre découpée sans points d'appui : géolocalisation perdue")
    out = None
    return n


def coeur(bbox: list, marge_m: float) -> list:
    """Emprise de la fenêtre réduite de sa marge : seules les détections dedans sont gardées."""
    lat = (bbox[1] + bbox[3]) / 2
    dlat, dlon = marge_m / 110574.0, marge_m / (111320.0 * math.cos(math.radians(lat)))
    return [bbox[0] + dlon, bbox[1] + dlat, bbox[2] - dlon, bbox[3] - dlat]


def analyse_fenetres(scene: dict, task: dict, device, tmp: str, local: bool, tr=None) -> dict:
    """Seules les fenêtres utiles de la scène (calculées par le serveur) : lecture partielle des images COG, découpe
    avec points d'appui, chaîne d'AI2 sur chaque fenêtre, détections du cœur seulement."""
    from osgeo import gdal
    tr = tr or otel_mini.Traceur(None, None, None)
    r = {"analyse": scene["analyse"], "id": scene["id"], "debut": scene["debut"], "fin": scene["fin"], "fenetres": []}
    if not local:
        configurer_s3()
    srcs = {pol: (scene[f"{pol}_path"] if local else source_image(scene[pol])) for pol in ("vv", "vh")}
    vv = gdal.Open(srcs["vv"])
    if vv is None:
        raise Arret(f"image VV illisible chez Copernicus : {gdal.GetLastErrorMsg()[:200]}")
    r["image_px"] = [vv.RasterXSize, vv.RasterYSize]
    passes = list(scene.get("historique") or [])[:2]
    ouverts: dict = {}
    r["historique"] = {"prevus": [h.get("id") for h in passes], "passages_min": None}
    dets, octets, t_lecture, t_analyse, ecartees = [], 0, 0.0, 0.0, 0
    for j, f in enumerate(scene["fenetres"]):
        k = f.get("id", j)              # identifiant donné par le serveur (lots sur processeur, lot C5)
        base = scene["id"].replace(".SAFE", "")
        sid = f"{base}_F{k}.SAFE"
        mdir = os.path.join(tmp, "raw_f" + str(k), sid, "measurement")
        os.makedirs(mdir, exist_ok=True)
        rf = {"index": k, "bbox": f["bbox"]}
        with tr.span("sentinel.fenetre", **{"sentinel.fenetre": k, "sentinel.points": f.get("points"),
                                            "sentinel.km2": f.get("km2")}) as sf:
            try:
                win = fenetre_pixels(vv, f["bbox"])
                rf["srcwin"] = win
                t = time.time()
                files = {}
                for pol in ("vv", "vh"):
                    files[pol] = os.path.join(mdir, nom_local(pol))
                    with tr.span("GET S3 eodata " + pol.upper() + " fenêtre",
                                 **{"peer.service": "Copernicus", "sentinel.polarisation": pol}) as sd:
                        n = decouper(srcs[pol], win, files[pol])
                        sd.set(**{"sentinel.octets": n, "sentinel.largeur_px": win[2], "sentinel.hauteur_px": win[3]})
                        octets += n
                        rf["octets"] = rf.get("octets", 0) + n
                rf["lecture_s"] = round(time.time() - t, 1)
                t_lecture += time.time() - t
                # Passages antérieurs de la même orbite, même emprise ; un passage illisible ou qui ne couvre pas la
                # fenêtre est écarté avec son motif, l'analyse se fait avec ce qui reste
                th, hist, rf["historique"], rf["octets_historique"] = time.time(), [], [], 0
                for j, h in enumerate(passes):
                    try:
                        hs, _w, n = lire_historique(tmp, k, j, h, f["bbox"], local, ouverts, tr)
                        hist.append(hs)
                        rf["historique"].append(h.get("id"))
                        rf["octets_historique"] += n
                        octets += n
                    except Exception as e:
                        rf.setdefault("historique_erreurs", []).append(f"{h.get('id')}: {type(e).__name__}: {e}"[:200])
                rf["lecture_historique_s"] = round(time.time() - th, 1)
                t_lecture += time.time() - th
                n_h = len(hist)
                r["historique"]["passages_min"] = (n_h if r["historique"]["passages_min"] is None
                                                   else min(n_h, r["historique"]["passages_min"]))
                d = _detecter(os.path.dirname(os.path.dirname(mdir)), sid, files, scene, task, device, tmp, tr,
                              coeur(f["bbox"], f.get("marge_m", 2000)), hist)
                rf["confiance"] = d["confiance"]
                t_analyse += d["analyse_s"]
                ecartees += d["ecartees_marge"]
                dets += d["detections"]
                rf.update({"analyse_s": d["analyse_s"], "detections": len(d["detections"]),
                           "ecartees_marge": d["ecartees_marge"]})
                sf.set(**{"sentinel.detections": len(d["detections"]), "sentinel.octets": rf["octets"],
                          "sentinel.historique": n_h, "sentinel.octets_historique": rf["octets_historique"],
                          "sentinel.seuil": d["confiance"]})
            except FenetreInvalide as e:
                rf["erreur"] = str(e)[:300]
                sf.erreur(rf["erreur"])
            finally:
                shutil.rmtree(os.path.join(tmp, "raw_f" + str(k)), ignore_errors=True)
                shutil.rmtree(os.path.join(tmp, "travail_" + sid), ignore_errors=True)
        r["fenetres"].append(rf)
    try:
        r["reseau_octets"] = int(json.loads(gdal.NetworkStatsGetAsSerializedJSON() or "{}").get("methods", {})
                                 .get("GET", {}).get("downloaded_bytes", 0)) or None
    except Exception:
        r["reseau_octets"] = None
    r.update({"detections": dets, "octets_lus": octets, "telecharge_mo": round(octets / 1e6, 1),
              "telecharge_s": round(t_lecture, 1), "analyse_s": round(t_analyse, 1), "ecartees_marge": ecartees})
    if r["fenetres"] and all(x.get("erreur") for x in r["fenetres"]):
        r["erreur"] = "aucune fenêtre analysée : " + r["fenetres"][0]["erreur"]
    return r


def run(task: dict, local: bool = False, tr=None) -> dict:
    t0 = time.time()
    tr = tr or otel_mini.Traceur(None, None, None)
    out, stop, octets = [], None, 0.0
    with tr.span("sentinel.preparation") as sp:
        try:
            device, prep = preflight()
            sp.set(**{"sentinel.torch": prep.get("torch")})
        except Arret as e:
            sp.erreur(str(e))
            return {"scenes": [], "erreur": str(e)[:500], "mesures": {"rss_max_mo": rss_mo()}}
    print(json.dumps({"preparation": prep, "s": round(time.time() - t0, 1)}), flush=True)
    for k, scene in enumerate(task["scenes"]):
        tmp = tempfile.mkdtemp(prefix="scene_", dir=task.get("travail"))
        with tr.span("sentinel.scene", **{"sentinel.index": k, "sentinel.analyse": scene.get("analyse"),
                                          "sentinel.satellite": scene.get("satellite"),
                                          "sentinel.fenetres": len(scene.get("fenetres") or [])}) as sc:
            try:
                fn = analyse_fenetres if scene.get("fenetres") else analyse
                r = fn(scene, task, device, tmp, local, tr)
                sc.set(**{"sentinel.octets_lus": r.get("octets_lus")})
                octets += r.get("telecharge_mo", 0)
                sc.set(**{"sentinel.detections": len(r.get("detections", []))})
            except (Arret, FichierInvalide) as e:
                r = {"analyse": scene["analyse"], "id": scene["id"], "debut": scene["debut"], "fin": scene["fin"],
                     "erreur": str(e)[:500]}
                sc.erreur(r["erreur"])
                if isinstance(e, Arret) or k == 0:           # cause commune, ou première scène déjà en échec
                    stop = str(e)[:500]
            except Exception as e:                           # une autre scène en échec n'arrête pas les suivantes
                r = {"analyse": scene["analyse"], "id": scene["id"], "debut": scene["debut"], "fin": scene["fin"],
                     "erreur": f"{type(e).__name__}: {e}"[:500]}
                sc.exception(e)
                traceback.print_exc()
            finally:
                shutil.rmtree(tmp, ignore_errors=True)       # images effacées après chaque scène
        tr.envoyer()                                         # spans envoyés après chaque scène
        r["rss_max_mo"] = rss_mo()
        out.append(r)
        print(json.dumps({**{x: v for x, v in r.items() if x != "detections"}, "n": len(r.get("detections", []))}),
              flush=True)
        if stop:
            print(f"arrêt du travailleur : {stop}", flush=True)
            break
    result = {"scenes": out, "mesures": {"duree_s": round(time.time() - t0, 1), "rss_max_mo": rss_mo(),
                                         "telecharge_mo": round(octets, 1), **prep}}
    if stop:
        result["erreur"] = stop
    elif out and all(sc.get("erreur") for sc in out):
        # Aucune scène n'a abouti : l'exécution est un échec, avec le motif de la première (essai du 14/10/2026, fini
        # en « code 0 » et compté comme terminé alors que son unique scène avait échoué)
        result["erreur"] = f"aucune scène analysée sur {len(out)} : {out[0]['erreur']}"[:500]
    return result


def send(task: dict, path: str, body: dict, traceparent: str | None = None):
    """Envoi au serveur par le réseau privé, avec quelques nouvelles tentatives (le serveur peut redémarrer) ; le
    contexte de trace suit la requête (l'API rejoint la trace du travailleur)."""
    data = json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {task['jeton']}", "Content-Type": "application/json"}
    if traceparent:
        headers["traceparent"] = traceparent
    for k in range(6):
        try:
            req = urllib.request.Request(f"{task['retour']}{path}", data=data, method="POST", headers=headers)
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
    tr = otel_mini.depuis_tache(task, **{"travailleur.tache": "sentinel"})
    with tr.span("travailleur.sentinel") as racine:
        with tr.span("travailleur.signal_demarre"):
            send(task, "/etat", {"etat": "demarre"}, tr.traceparent())
        try:
            if task.get("tache_par_le_reseau"):
                with tr.span("travailleur.remise_tache"):
                    task = fetch_task(task, tr.traceparent())
            racine.set(**{"sentinel.scenes": len(task.get("scenes", []))})
            result = run(task, tr=tr)
        except Exception as e:
            result = {"scenes": [], "erreur": f"{type(e).__name__}: {e}"[:1000], "mesures": {"rss_max_mo": rss_mo()}}
        if result.get("erreur"):
            racine.erreur(result["erreur"])
        with tr.span("travailleur.renvoi_resultats") as sr:
            sr.set(**{"http.response.status_code": send(task, "/resultats", result, tr.traceparent())})
    tr.envoyer()


if __name__ == "__main__":
    main()
