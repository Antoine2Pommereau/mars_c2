"""Accès à Sentinel Hub (Copernicus Data Space Ecosystem) : catalogue des passages et extraits radar.

Harmonisation mesurée en phase 0 : sigma0, orthorectifié, rééchantillonnage bilinéaire.
L'API de traitement refuse les images de plus de 2500 pixels de côté : les zones plus grandes
sont découpées en morceaux récupérés en parallèle.
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

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_BASE = "https://sh.dataspace.copernicus.eu"
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


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _iso(t: pd.Timestamp) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class SentinelHub:
    def __init__(self, client_id: str, client_secret: str):
        if not client_id or not client_secret:
            raise ValueError("Identifiants Sentinel Hub manquants (SH_CLIENT_ID, SH_CLIENT_SECRET dans .env)")
        r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": client_id,
                                           "client_secret": client_secret}, timeout=30)
        r.raise_for_status()
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def search_passes(self, bbox, t_from: pd.Timestamp, t_to: pd.Timestamp) -> list[dict]:
        payload = {"bbox": list(bbox), "datetime": f"{_iso(t_from)}/{_iso(t_to)}",
                   "collections": ["sentinel-1-grd"], "limit": 100}
        r = requests.post(f"{SH_BASE}/api/v1/catalog/1.0.0/search", headers=self.headers, json=payload, timeout=60)
        r.raise_for_status()
        passes = []
        for f in r.json().get("features", []):
            p = f["properties"]
            mode = p.get("sar:instrument_mode")
            if mode and mode != "IW":
                continue
            passes.append({
                "product_name": f["id"],
                "acquired_at": pd.Timestamp(p["datetime"]).tz_convert("UTC"),
                "platform": p.get("platform"),
                "orbit_direction": p.get("sat:orbit_state"),
                "footprint": f.get("geometry"),
            })
        return sorted(passes, key=lambda x: x["acquired_at"])

    def _fetch_chunk(self, epsg, bounds, t0, width, height):
        payload = {
            "input": {
                "bounds": {"bbox": list(bounds), "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"}},
                "data": [{
                    "type": "sentinel-1-grd",
                    "dataFilter": {
                        "timeRange": {"from": _iso(t0 - pd.Timedelta(minutes=2)), "to": _iso(t0 + pd.Timedelta(minutes=2))},
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
        """Renvoie (image_db, transform, epsg) ; image_db a la forme (2, H, W), canaux VH puis VV, en dB."""
        lon_c, lat_c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        epsg = utm_epsg(lon_c, lat_c)
        to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        xs, ys = to_utm.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
        xmin, xmax = round(min(xs), -1), round(max(xs), -1)
        ymin, ymax = round(min(ys), -1), round(max(ys), -1)
        width, height = int(round((xmax - xmin) / RES_M)), int(round((ymax - ymin) / RES_M))
        transform = from_origin(xmin, ymax, RES_M, RES_M)

        key = hashlib.sha1(f"{bbox}{t0.isoformat()}{PROCESSING}".encode()).hexdigest()[:12]
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = cache_dir / f"extrait_{t0:%Y%m%dT%H%M%S}_{key}.tif"

        if cache.exists():
            with rasterio.open(cache) as src:
                full = src.read().astype("float32")
        else:
            jobs = [(i, j, min(MAX_PX, width - i), min(MAX_PX, height - j))
                    for j in range(0, height, MAX_PX) for i in range(0, width, MAX_PX)]

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
        return image_db, transform, epsg
