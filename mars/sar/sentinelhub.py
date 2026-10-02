"""Extraits radar Sentinel 1 via l'API de traitement de Sentinel Hub (Copernicus Data Space Ecosystem).

Harmonisation mesurée en phase 0 : sigma0, orthorectifié, rééchantillonnage bilinéaire.
L'API refuse les images de plus de 2500 pixels de côté : les zones plus grandes sont découpées en morceaux
récupérés en parallèle, puis réassemblées. Les extraits sont mis en cache sur disque.
"""
import copy
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Transformer
from rasterio.transform import from_origin

from mars.geo import utm_epsg
from mars.sar.catalog import SH_BASE, get_token, iso, search_passes

RES_M = 10
MAX_PX = 2400

EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: ["VH", "VV", "dataMask"], output: { bands: 3, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) { return [s.VH, s.VV, s.dataMask]; }
"""

PROCESSING = {
    "backCoeff": "SIGMA0_ELLIPSOID",
    "orthorectify": True,
    "demInstance": "COPERNICUS_30",
    "upsampling": "BILINEAR",
    "downsampling": "BILINEAR",
}


class SentinelHub:
    def __init__(self, client_id: str, client_secret: str):
        self.headers = {"Authorization": f"Bearer {get_token(client_id, client_secret)}"}
        self._token = self.headers["Authorization"].split(" ", 1)[1]

    def search_passes(self, bbox, t_from: pd.Timestamp, t_to: pd.Timestamp) -> list[dict]:
        return search_passes(self._token, bbox, t_from, t_to)

    def _fetch_chunk(self, epsg, bounds, t0, width, height):
        payload = {
            "input": {
                "bounds": {"bbox": list(bounds), "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"}},
                "data": [{
                    "type": "sentinel-1-grd",
                    "dataFilter": {
                        "timeRange": {"from": iso(t0 - pd.Timedelta(minutes=2)), "to": iso(t0 + pd.Timedelta(minutes=2))},
                        "acquisitionMode": "IW", "polarization": "DV", "resolution": "HIGH",
                        "mosaickingOrder": "mostRecent",
                    },
                    "processing": copy.deepcopy(PROCESSING),
                }],
            },
            "output": {"width": width, "height": height,
                       "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
            "evalscript": EVALSCRIPT,
        }
        r = requests.post(f"{SH_BASE}/api/v1/process", headers=self.headers, json=payload, timeout=300)
        if r.status_code != 200:
            raise RuntimeError(f"Sentinel Hub {r.status_code} : {r.text[:400]}")
        with rasterio.open(io.BytesIO(r.content)) as src:
            return src.read().astype("float32")

    def fetch_extract(self, bbox, t0: pd.Timestamp, cache_dir: Path):
        """Renvoie (image_db, transform, epsg, depuis_le_cache, nombre_de_requetes).

        image_db a la forme (2, H, W), canaux VH puis VV, en dB.
        """
        epsg = utm_epsg((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        xs, ys = to_utm.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
        xmin, xmax = round(min(xs), -1), round(max(xs), -1)
        ymin, ymax = round(min(ys), -1), round(max(ys), -1)
        width, height = int(round((xmax - xmin) / RES_M)), int(round((ymax - ymin) / RES_M))
        transform = from_origin(xmin, ymax, RES_M, RES_M)

        key = hashlib.sha1(f"{list(bbox)}{t0.isoformat()}{PROCESSING}".encode()).hexdigest()[:12]
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = cache_dir / f"extrait_{t0:%Y%m%dT%H%M%S}_{key}.tif"

        jobs = [(i, j, min(MAX_PX, width - i), min(MAX_PX, height - j))
                for j in range(0, height, MAX_PX) for i in range(0, width, MAX_PX)]
        from_cache = cache.exists()
        if from_cache:
            with rasterio.open(cache) as src:
                full = src.read().astype("float32")
        else:
            def run(job):
                i, j, w, h = job
                bx0, by_top = xmin + i * RES_M, ymax - j * RES_M
                return job, self._fetch_chunk(epsg, (bx0, by_top - h * RES_M, bx0 + w * RES_M, by_top), t0, w, h)

            full = np.zeros((3, height, width), dtype="float32")
            with ThreadPoolExecutor(max_workers=4) as pool:
                for (i, j, w, h), arr in pool.map(run, jobs):
                    full[:, j:j + h, i:i + w] = arr[:, :h, :w]
            with rasterio.open(cache, "w", driver="GTiff", height=height, width=width, count=3, dtype="float32",
                               crs=f"EPSG:{epsg}", transform=transform, compress="deflate") as dst:
                dst.write(full)

        vh, vv, mask = full
        ok = (mask > 0) & (vh > 0) & (vv > 0)
        image_db = np.full((2, height, width), np.nan, dtype="float32")
        image_db[0][ok] = 10 * np.log10(vh[ok])
        image_db[1][ok] = 10 * np.log10(vv[ok])
        return image_db, transform, epsg, from_cache, len(jobs)
