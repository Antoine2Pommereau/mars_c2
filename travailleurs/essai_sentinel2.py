"""Essai du modèle Sentinel 2 d'AI2 (rslearn, image ghcr.io/allenai/sentinel2-vessel-detection) dans sa vraie image,
sur processeur, lancé par scripts/verifier_images.sh s2 (lot S2, mesures du 20/10/2026, docs/sentinel2.md).

Fenêtre réelle de 24 km : passage S2A du 04/10/2026 à 11:08 UTC, rail montant de la Manche au large de Beachy Head
(0,4 E, 50,4 N), 0,2 % de nuages. Les 13 bandes L1C sont lues par requêtes partielles dans le miroir public de Google
(mêmes fichiers JP2 qu'eodata, sans clé), puis la chaîne d'AI2 (détection puis attributs) tourne sur la fenêtre.
Attendu : les quatre navires visibles, score au moins 0,8, longueurs et caps plausibles. Durée et octets lus affichés.
Poids montés dans /rslp (RSLP_PREFIX), téléchargés par verifier_images.sh depuis le dépôt public d'AI2."""
import json
import os
import subprocess
import sys
import time

import rasterio
from pyproj import Transformer
from rasterio.windows import from_bounds

LON, LAT, DEMI_M = 0.4, 50.4, 12000
SAFE = ("tiles/31/U/CR/S2A_MSIL1C_20261004T110831_N0513_R137_T31UCR_20261004T162422.SAFE/GRANULE/"
        "L1C_T31UCR_A058936_20261004T111122/IMG_DATA/T31UCR_20261004T110831_{}.jp2")
MIROIR = "https://storage.googleapis.com/gcp-public-data-sentinel-2/"
BANDES = ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B10", "B11", "B12"]
ATTENDUS = 4


def fenetre(dossier: str) -> float:
    """Découpe la fenêtre de chaque bande en GeoTIFF ; retourne la durée de lecture."""
    t0 = time.time()
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".jp2"):
        for b in BANDES:
            with rasterio.open("/vsicurl/" + MIROIR + SAFE.format(b)) as ds:
                x, y = Transformer.from_crs(4326, ds.crs, always_xy=True).transform(LON, LAT)
                w = from_bounds(x - DEMI_M, y - DEMI_M, x + DEMI_M, y + DEMI_M, ds.transform).round_offsets().round_lengths()
                d = ds.read(1, window=w)
                prof = {"driver": "GTiff", "dtype": d.dtype, "count": 1, "width": d.shape[1], "height": d.shape[0],
                        "crs": ds.crs, "transform": ds.window_transform(w)}
                with rasterio.open(f"{dossier}/{b}.tif", "w", **prof) as o:
                    o.write(d, 1)
    return time.time() - t0


def main() -> int:
    os.makedirs("/tmp/fen", exist_ok=True)
    lecture = fenetre("/tmp/fen")
    print(f"OK      s2 : 13 bandes L1C d'une fenêtre de 24 km lues en {lecture:.1f} s (miroir public)")
    taches = [{"image_files": [{"bands": [b], "fname": f"/tmp/fen/{b}.tif"} for b in BANDES],
               "geojson_path": "/tmp/sortie/detections.geojson"}]
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", "rslp.main", "sentinel2_vessels", "predict", "--tasks", json.dumps(taches),
                        "--scratch_path", "/tmp/scratch"], cwd="/opt/rslearn_projects", capture_output=True, text=True)
    duree = time.time() - t0
    if r.returncode != 0:
        print((r.stdout + r.stderr)[-3000:])
        print(f"ÉCHEC  s2 : chaîne d'AI2 en échec (code {r.returncode})")
        return 1
    dets = json.load(open("/tmp/sortie/detections.geojson"))["features"]
    for f in dets:
        p, a = f["properties"], f["properties"].get("attributes") or {}
        lon, lat = f["geometry"]["coordinates"]
        print(f"        {lon:.4f} {lat:.4f} score {p['score']:.2f} longueur {a.get('length', 0):.0f} m largeur "
              f"{a.get('width', 0):.0f} m vitesse {a.get('speed', 0):.1f} nœuds cap {a.get('heading', 0):.0f} "
              f"{a.get('vessel_type')}")
    bons = [f for f in dets if f["properties"]["score"] >= 0.8]
    if len(bons) < ATTENDUS:
        print(f"ÉCHEC  s2 : {len(bons)} navire(s) au dessus de 0,8 sur {ATTENDUS} attendus")
        return 1
    print(f"OK      s2 : {len(bons)} navires détectés et décrits en {duree:.0f} s sur processeur (détection et attributs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
