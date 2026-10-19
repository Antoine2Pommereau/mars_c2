"""Essai de la lecture par fenêtres du travailleur Sentinel (lot C3), dans la vraie image d'AI2 (GDAL 3.4.3, Python 3.8).

    python3 /mars/essai_fenetres.py --lecture   lecture partielle, signature, points d'appui (environ une minute)
    python3 /mars/essai_fenetres.py --complet   chaîne d'AI2 sur la scène entière puis sur une fenêtre : mêmes
                                                détections au même endroit (environ 15 minutes sur CPU, en émulation)

Lecture : une scène synthétique au format des GRD (entiers 16 bits, tuiles de 512, géolocalisée par une grille de
points d'appui légèrement tournée et courbée, comme une vraie fauchée) est servie par un faux S3 local. Le faux S3
vérifie la signature AWS version 4 de chaque requête (clés de l'essai) et compte les octets envoyés. Le travailleur
lit une fenêtre par /vsis3/ ; on vérifie qu'il n'a lu qu'une petite part du fichier, que la fenêtre garde des points
d'appui corrects (un point de la scène retombe au même endroit, à moins d'un mètre), et qu'une signature fausse est
refusée.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import math
import os
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLE, SECRET, REGION = "CLE_ESSAI", "SECRET_ESSAI", "default"
LON0, LAT0, PX = -5.60, 48.60, 10.0


def worker():
    spec = importlib.util.spec_from_file_location("travailleur_sentinel", os.path.join(os.path.dirname(__file__),
                                                                                      "sentinel.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def geoloc(r: float, c: float) -> tuple:
    """Position d'un pixel : fauchée tournée de 12° et légèrement courbée (géolocalisation non affine)."""
    a = math.radians(12)
    x, y = c * PX, r * PX
    x2 = x * math.cos(a) + y * math.sin(a) + 2e-6 * y * y
    y2 = -x * math.sin(a) + y * math.cos(a)
    return LON0 + x2 / (111320 * math.cos(math.radians(LAT0))), LAT0 - y2 / 111320


def scene(folder: str, n_l: int, n_c: int, cibles=(), bruit=True) -> dict:
    import numpy as np
    from osgeo import gdal, osr
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    gcps = [gdal.GCP(*geoloc(r, c), 0, c, r)
            for r in range(0, n_l + 1, n_l // 9) for c in range(0, n_c + 1, n_c // 20)]
    rng = np.random.default_rng(3)
    paths = {}
    for pol, mer, cible in (("vv", 60, 3000), ("vh", 25, 900)):
        a = rng.rayleigh(mer, (n_l, n_c)) if bruit else np.full((n_l, n_c), mer, dtype="float64")
        for r, c in cibles:
            a[r - 8:r + 8, c - 3:c + 3] = cible * rng.uniform(0.8, 1.2, (16, 6))
        a = np.clip(a, 1, 65535).astype(np.uint16)
        paths[pol] = os.path.join(folder, f"s1c-iw-grd-{pol}-20261018t061500-20261018t061525-004321-008765-cog.tiff")
        ds = gdal.GetDriverByName("GTiff").Create(paths[pol], n_c, n_l, 1, gdal.GDT_UInt16,
                                                  ["TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512", "COMPRESS=DEFLATE"])
        ds.GetRasterBand(1).WriteArray(a)
        ds.SetGCPs(gcps, srs.ExportToWkt())
        ds = None
    return paths


# Faux S3 : signature AWS version 4 vérifiée (en têtes signés quelconques), plages d'octets, octets comptés

class S3(BaseHTTPRequestHandler):
    racine, envoyes, refus, requetes = "", 0, 0, 0

    def _signature_ok(self) -> bool:
        auth = self.headers.get("Authorization", "")
        try:
            cred = auth.split("Credential=")[1].split(",")[0]
            signes = auth.split("SignedHeaders=")[1].split(",")[0]
            sig = auth.split("Signature=")[1].strip()
            cle, jour, region, service, _ = cred.split("/")
        except IndexError:
            return False
        if cle != CLE or region != REGION or service != "s3":
            return False
        u = urllib.parse.urlsplit(self.path)
        query = "&".join(sorted(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                                for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)))
        canon_h = "".join(f"{h}:{' '.join(self.headers.get(h, '').split())}\n" for h in signes.split(";"))
        canon = "\n".join([self.command, urllib.parse.quote(urllib.parse.unquote(u.path), safe="/-_.~"), query, canon_h,
                           signes, self.headers.get("x-amz-content-sha256", "")])
        date = self.headers.get("x-amz-date", "")
        scope = f"{jour}/{region}/s3/aws4_request"
        a_signer = "\n".join(["AWS4-HMAC-SHA256", date, scope, hashlib.sha256(canon.encode()).hexdigest()])
        k = ("AWS4" + SECRET).encode()
        for part in (jour, region, "s3", "aws4_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        return hmac.compare_digest(hmac.new(k, a_signer.encode(), hashlib.sha256).hexdigest(), sig)

    def _servir(self, corps: bool):
        S3.requetes += 1
        if not self._signature_ok():
            S3.refus += 1
            self.send_response(403)
            self.end_headers()
            return
        chemin = os.path.join(S3.racine, urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).lstrip("/"))
        if not os.path.isfile(chemin):
            self.send_response(404)
            self.end_headers()
            return
        taille = os.path.getsize(chemin)
        plage = self.headers.get("Range")
        a, b = 0, taille - 1
        if plage and plage.startswith("bytes="):
            x, _, y = plage[6:].split(",")[0].partition("-")
            a, b = int(x), min(int(y) if y else taille - 1, taille - 1)
        self.send_response(206 if plage else 200)
        self.send_header("Content-Length", str(b - a + 1))
        if plage:
            self.send_header("Content-Range", f"bytes {a}-{b}/{taille}")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if corps:
            with open(chemin, "rb") as f:
                f.seek(a)
                data = f.read(b - a + 1)
            S3.envoyes += len(data)
            self.wfile.write(data)

    def do_GET(self):
        self._servir(True)

    def do_HEAD(self):
        self._servir(False)

    def log_message(self, *a):
        pass


def serveur(racine: str) -> int:
    S3.racine = racine
    srv = ThreadingHTTPServer(("127.0.0.1", 0), S3)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def metres(a, b) -> float:
    return math.hypot((a[0] - b[0]) * 111320 * math.cos(math.radians(a[1])), (a[1] - b[1]) * 111320)


def lecture(w) -> int:
    from osgeo import gdal
    racine = tempfile.mkdtemp(prefix="essai_s3_")
    dossier = os.path.join(racine, "eodata", "Sentinel-1", "essai")
    os.makedirs(dossier)
    n_l, n_c = 8000, 12000                                    # 96 millions de pixels, environ 190 Mo par polarisation
    t = time.time()
    paths = scene(dossier, n_l, n_c)
    taille = os.path.getsize(paths["vv"])
    print(f"        scène synthétique {n_c} × {n_l} pixels, {taille / 1e6:.0f} Mo par polarisation ({time.time() - t:.0f} s)")
    port = serveur(racine)
    w._S3.update({"endpoint": f"127.0.0.1:{port}", "http": True, "region": REGION, "access_key": CLE, "secret_key": SECRET})
    w.configurer_s3()
    href = "s3://eodata/Sentinel-1/essai/" + os.path.basename(paths["vv"])
    src = w.source_image(href)
    # Point d'intérêt : pixel (ligne 4100, colonne 6300) ; fenêtre de 24 km de côté autour
    cible_px = (6300.5, 4100.5)
    lon, lat = geoloc(cible_px[1], cible_px[0])
    demi = 12000
    dlat, dlon = demi / 110574.0, demi / (111320.0 * math.cos(math.radians(lat)))
    bbox = [lon - dlon, lat - dlat, lon + dlon, lat + dlat]
    ds = gdal.Open(src)
    if ds is None:
        print(f"ÉCHEC   ouverture par /vsis3/ refusée : {gdal.GetLastErrorMsg()} (refus de signature : {S3.refus})")
        return 1
    win = w.fenetre_pixels(ds, bbox)
    dst = os.path.join(tempfile.mkdtemp(), "fenetre_vv.tiff")
    avant = S3.envoyes
    t = time.time()
    est = w.decouper(src, win, dst)
    lu = S3.envoyes
    out = gdal.Open(dst)
    tr_full = gdal.Transformer(ds, None, [])
    tr_win = gdal.Transformer(out, None, [])
    ecarts = []
    for px, py in (cible_px, (win[0] + 100.5, win[1] + 100.5), (win[0] + win[2] - 100.5, win[1] + win[3] - 100.5)):
        _, (a, b, _z) = tr_full.TransformPoint(0, px, py, 0)
        _, (c, d, _z) = tr_win.TransformPoint(0, px - win[0], py - win[1], 0)
        ecarts.append(metres((a, b), (c, d)))
    print(f"        fenêtre {win[2]} × {win[3]} pixels en ({win[0]}, {win[1]}), {out.GetGCPCount()} points d'appui, "
          f"lue en {time.time() - t:.1f} s")
    print(f"        octets lus : {lu / 1e6:.1f} Mo sur {taille / 1e6:.0f} Mo ({100 * lu / taille:.1f} %), dont "
          f"{(lu - avant) / 1e6:.1f} Mo pour la découpe ; estimation par l'index du TIFF : {est / 1e6:.1f} Mo ; "
          f"{S3.requetes} requêtes signées, {S3.refus} refusées")
    print(f"        géolocalisation : écart entre scène et fenêtre {max(ecarts):.3f} m au plus (3 points)")
    w._S3["secret_key"] = "FAUX"
    w.configurer_s3()
    gdal.VSICurlClearCache()
    refus_avant = S3.refus
    faux = gdal.Open(w.source_image(href))
    refusee = faux is None and S3.refus > refus_avant
    erreurs = []
    if not refusee:
        erreurs.append("une signature fausse n'est pas refusée")
    if lu > 0.2 * taille:
        erreurs.append(f"trop d'octets lus ({100 * lu / taille:.0f} %)")
    if max(ecarts) > 1.0:
        erreurs.append(f"géolocalisation décalée de {max(ecarts):.1f} m")
    if refus_avant:
        erreurs.append(f"{refus_avant} requêtes de la vraie clé refusées : signature de GDAL incompatible")
    if erreurs:
        print("ÉCHEC   " + " ; ".join(erreurs))
        return 1
    print("OK      lecture par fenêtres : requêtes partielles signées, points d'appui recalés, signature fausse refusée")
    return 0


def complet(w) -> int:
    """Chaîne d'AI2 sur la scène entière, puis sur une fenêtre : les cibles du cœur doivent être retrouvées au même
    endroit (moins de 10 m d'écart) et rien d'autre."""
    racine = tempfile.mkdtemp(prefix="essai_complet_")
    n_l, n_c = 3200, 3200
    cibles = [(700, 800), (1600, 1500), (2300, 2400), (2900, 600)]
    paths = scene(racine, n_l, n_c, cibles)
    base = {"analyse": 1, "id": "S1C_IW_GRDH_1SDV_20261018T061500_20261018T061525_004321_008765_ABCD_COG",
            "debut": "2026-10-18T06:15:00Z", "fin": "2026-10-18T06:15:25Z", "satellite": "S1C",
            "vv_path": paths["vv"], "vh_path": paths["vh"]}
    task = {"regions": [[-7.5, 46.5, -3.0, 49.6]], "confiance": 0.5, "nms_m": 10, "vignette_km": 3, "force_cpu": True,
            "travail": racine}
    t = time.time()
    entiere = w.run({**task, "scenes": [base]}, local=True)["scenes"][0]
    t_entiere = time.time() - t
    if entiere.get("erreur"):
        print(f"ÉCHEC   scène entière : {entiere['erreur']}")
        return 1
    # Fenêtre : les cibles 2 et 3 au cœur ; 1 et 4 hors de la fenêtre
    lon_a, lat_a = geoloc(1250, 1150)
    lon_b, lat_b = geoloc(2650, 2750)
    bbox = [min(lon_a, lon_b), min(lat_a, lat_b), max(lon_a, lon_b), max(lat_a, lat_b)]
    t = time.time()
    fen = w.run({**task, "scenes": [{**base, "fenetres": [{"bbox": bbox, "marge_m": 300, "points": 2}]}]},
                local=True)["scenes"][0]
    t_fen = time.time() - t
    if fen.get("erreur"):
        print(f"ÉCHEC   fenêtre : {fen['erreur']}")
        return 1
    attendues = [d for d in entiere["detections"] if w.in_regions(d["lon"], d["lat"], [w.coeur(bbox, 300)])]
    ecarts = []
    for d in attendues:
        proche = min(fen["detections"], key=lambda e: metres((d["lon"], d["lat"]), (e["lon"], e["lat"])), default=None)
        ecarts.append(metres((d["lon"], d["lat"]), (proche["lon"], proche["lat"])) if proche else float("inf"))
    print(f"        scène entière : {len(entiere['detections'])} détections en {t_entiere:.0f} s ; fenêtre "
          f"{fen['fenetres'][0].get('srcwin')} : {len(fen['detections'])} détections en {t_fen:.0f} s, "
          f"{fen['ecartees_marge']} écartées en marge")
    print(f"        mêmes détections au cœur : {len(attendues)}, écart au plus {max(ecarts, default=0):.1f} m")
    if len(attendues) < 2 or len(fen["detections"]) != len(attendues) or max(ecarts, default=0) > 10:
        print("ÉCHEC   la fenêtre ne retrouve pas les mêmes détections au même endroit")
        return 1
    print(f"OK      fenêtre et scène entière : {len(attendues)} détections identiques à {max(ecarts):.1f} m près")
    return 0


if __name__ == "__main__":
    w = worker()
    sys.exit(complet(w) if "--complet" in sys.argv else lecture(w))
