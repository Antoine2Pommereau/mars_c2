"""Service d'inférence de MARS C2.

Reçoit une zone et un instant d'acquisition, récupère l'extrait radar, exécute le modèle et renvoie les
détections géoréférencées. Le modèle est chargé et chauffé une seule fois au démarrage.

Sur un Mac, lancer ce service hors de Docker pour profiter du GPU Apple (MPS) :
    uvicorn inference.app:app --port 8001
La progression est renvoyée ligne par ligne (NDJSON), puis le résultat final.
"""
import asyncio
import json
import os
import struct
import threading
import time
import zlib
from contextlib import asynccontextmanager
from datetime import datetime

import numpy as np
import pandas as pd
import torch
import rasterio
from fastapi import FastAPI, Response
from rasterio.windows import Window
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pyproj import Transformer

from mars.config import ROOT, load_env, load_rules
from mars.sar.inference import TILE, detect, load_model, pick_device, run_model
from mars.sar.sentinelhub import SentinelHub

load_env()
RULES = load_rules()
STATE = {}
LOCK = threading.Lock()   # une inférence à la fois : le GPU n'est pas partagé


def _warmup(model, device, amp):
    x = np.zeros((2, TILE, TILE), dtype="float32")
    x[:, 1000:1004, 1000:1004] = 0.9
    t = time.time()
    run_model(x, model, device, amp)
    return time.time() - t


@asynccontextmanager
async def lifespan(_app):
    device = pick_device(os.environ.get("INFERENCE_DEVICE", "auto"))
    model = load_model(ROOT / RULES["model"]["file"], device)
    # Précision mixte : utile sur GPU NVIDIA ; sur GPU Apple (MPS) elle ralentit le calcul (16 s contre 6 s mesurés
    # pour 4 tuiles) sans rien changer aux détections. Forçable avec INFERENCE_AMP=on ou off.
    amp_env = os.environ.get("INFERENCE_AMP", "auto")
    amp = (device == "cuda") if amp_env == "auto" else (amp_env == "on")
    try:
        warm_s = _warmup(model, device, amp)
    except Exception:
        amp = False   # précision mixte non prise en charge sur cet accélérateur
        warm_s = _warmup(model, device, amp)
    STATE.update(model=model, device=device, amp=amp, warmup_s=round(warm_s, 1))
    print(f"Modèle prêt sur {device}, précision mixte {amp}, chauffe {warm_s:.1f} s", flush=True)
    yield


app = FastAPI(title="MARS C2, inférence", lifespan=lifespan)


class AnalyzeRequest(BaseModel):
    bbox: list[float] = Field(min_length=4, max_length=4)
    acquired_at: datetime
    mode: str = "fast"


@app.get("/v1/health")
def health():
    return {"status": "ok" if STATE else "starting", "device": STATE.get("device"), "amp": STATE.get("amp"),
            "warmup_s": STATE.get("warmup_s"), "model_version": RULES["model"]["version"]}


def png_gray(a: np.ndarray) -> bytes:
    """Encode une image en niveaux de gris (uint8) au format PNG, sans dépendance supplémentaire."""
    a = np.ascontiguousarray(a, dtype=np.uint8)
    h, w = a.shape
    raw = b"".join(b"\x00" + a[r].tobytes() for r in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


@app.get("/v1/chip")
def chip(lon: float, lat: float, acquired_at: datetime, size_m: float = 800):
    """Vignette VV autour d'un point, lue dans les extraits radar en cache pour ce passage."""
    t0 = pd.Timestamp(acquired_at)
    t0 = t0.tz_localize("UTC") if t0.tzinfo is None else t0.tz_convert("UTC")
    paths = sorted((ROOT / "data" / "sar").glob(f"extrait_{t0:%Y%m%dT%H%M%S}_*.tif"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        with rasterio.open(path) as src:
            x, y = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True).transform(lon, lat)
            row, col = src.index(x, y)
            if not (0 <= row < src.height and 0 <= col < src.width):
                continue
            half = max(int(size_m / 2 / abs(src.transform.a)), 8)
            vv = src.read(2, window=Window(col - half, row - half, 2 * half, 2 * half), boundless=True, fill_value=0)
        db = np.where(vv > 0, 10 * np.log10(np.maximum(vv, 1e-10)), np.nan)
        if np.isnan(db).all():
            break
        lo = float(np.nanpercentile(db, 2))
        hi = max(lo + 15.0, float(np.nanpercentile(db, 99.7)))
        img = np.nan_to_num((np.clip((db - lo) / (hi - lo), 0, 1) * 255), nan=0).astype(np.uint8)
        return Response(png_gray(img), media_type="image/png", headers={"Cache-Control": "max-age=3600"})
    return Response(status_code=404)


@app.post("/v1/analyze")
async def analyze(req: AnalyzeRequest):
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(obj):
        loop.call_soon_threadsafe(queue.put_nowait, obj)

    def work():
        timings = {}
        rules = load_rules()   # relues à chaque analyse : un changement de seuil s'applique sans redémarrage
        try:
            with LOCK:
                t0 = pd.Timestamp(req.acquired_at)
                t0 = t0.tz_convert("UTC") if t0.tzinfo else t0.tz_localize("UTC")
                emit({"type": "progress", "step": "extraction", "state": "start"})
                t = time.time()
                sh = SentinelHub(os.environ.get("SH_CLIENT_ID"), os.environ.get("SH_CLIENT_SECRET"))
                image_db, transform, epsg, cached, n_req = sh.fetch_extract(req.bbox, t0, ROOT / "data" / "sar")
                timings["extraction_s"] = round(time.time() - t, 2)
                emit({"type": "progress", "step": "extraction", "state": "done", "seconds": timings["extraction_s"],
                      "detail": "depuis le cache" if cached else f"{n_req} requête(s) Sentinel Hub"})

                emit({"type": "progress", "step": "inference", "state": "start"})
                t = time.time()
                det, n_tiles = detect(image_db, transform, STATE["model"], STATE["device"],
                                      rules["model"]["thresholds"], rules["contrast"], amp=STATE["amp"],
                                      merge_cfg=rules["model"].get("merge"))
                timings["inference_s"] = round(time.time() - t, 2)
                emit({"type": "progress", "step": "inference", "state": "done", "seconds": timings["inference_s"],
                      "detail": f"{len(det)} détections sur {n_tiles} tuile(s), {STATE['device']}"})

                to_wgs = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
                lon, lat = to_wgs.transform(det.x.to_numpy(), det.y.to_numpy()) if len(det) else ([], [])
                detections = [
                    {"lon": float(lo), "lat": float(la), "objectness": float(d.objectness),
                     "vessel_score": float(d.vessel_score), "fishing_score": float(d.fishing_score),
                     "length_m": float(d.length_m),
                     "contrast_vv_db": None if np.isnan(d.contrast_vv_db) else float(d.contrast_vv_db)}
                    for lo, la, d in zip(lon, lat, det.itertuples())
                ]
                emit({"type": "result", "detections": detections, "timings": timings, "n_tiles": n_tiles,
                      "device": STATE["device"], "amp": STATE["amp"], "model_version": rules["model"]["version"],
                      "thresholds": rules["model"]["thresholds"]})
        except Exception as e:
            emit({"type": "error", "message": f"{type(e).__name__} : {e}"})
        finally:
            if STATE.get("device") == "mps":
                torch.mps.empty_cache()
            emit(None)

    threading.Thread(target=work, daemon=True).start()

    async def lines():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")
