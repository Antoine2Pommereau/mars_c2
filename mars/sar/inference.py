"""Boucle d'inférence CircleNet (solution gagnante xView3), validée en phase 0 contre le script officiel.

Contrat du modèle : canaux VH puis VV, sigma0 en dB, sigmoïde de ((x + 20) x 0,18), tuiles de 2048 pixels
exactement au pas de 1536, sorties à demi résolution dans l'ordre présence, navire, pêche, longueur, décalage.
"""
import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import pandas as pd
import rasterio
import torch
import torch.nn.functional as F

MIDPOINT, TEMPERATURE = -20.0, 0.18
TILE, STEP, STRIDE = 2048, 1536, 2
PIX_TO_M = 10.0
MAX_LENGTH_M = 500.0   # borne physique plausible : au delà, la longueur décodée est écrêtée (les porte conteneurs
                       # les plus longs approchent 400 m ; 500 m laisse une marge sans laisser passer d'aberration)


def pick_device(preference: str = "auto") -> str:
    if preference != "auto":
        return preference
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(path, device: str):
    return torch.jit.load(str(path), map_location=device).eval()


def normalize(image_db: np.ndarray) -> np.ndarray:
    x = 1.0 / (1.0 + np.exp(-(image_db - MIDPOINT) * TEMPERATURE))
    return np.nan_to_num(x, nan=0.0).astype("float32")


def tile_starts(size: int) -> list[int]:
    if size <= TILE:
        return [0]
    return list(range(0, size - TILE, STEP)) + [size - TILE]


def _pyramid_weight() -> torch.Tensor:
    n = TILE // STRIDE
    idx = np.arange(n) + 0.5
    edge = np.minimum(idx, n - idx)
    de = np.minimum(edge[:, None], edge[None, :])
    dc = np.sqrt((idx[:, None] - n / 2) ** 2 + (idx[None, :] - n / 2) ** 2)
    w = de / (de + dc)
    return torch.from_numpy((w / w.max()).astype("float32"))[None]


def _sync(device: str) -> None:
    if device == "cuda":
        torch.cuda.synchronize()
    elif device == "mps":
        torch.mps.synchronize()


@torch.no_grad()
def run_model(image_norm: np.ndarray, model, device: str, amp: bool = False):
    """Exécute le modèle tuile par tuile et fusionne les sorties. Renvoie (cartes, (h, w), nombre de tuiles)."""
    _, h, w = image_norm.shape
    ph, pw = max(h, TILE), max(w, TILE)
    padded = np.zeros((2, ph, pw), dtype="float32")
    padded[:, :h, :w] = image_norm
    n = TILE // STRIDE
    weight = _pyramid_weight()
    acc = torch.zeros((6, ph // STRIDE, pw // STRIDE))
    norm = torch.zeros((1, ph // STRIDE, pw // STRIDE))
    n_tiles = 0
    for r in tile_starts(ph):
        for c in tile_starts(pw):
            patch = padded[:, r:r + TILE, c:c + TILE]
            if not patch.any():
                continue
            x = torch.from_numpy(np.ascontiguousarray(patch)).unsqueeze(0).to(device)
            with torch.autocast(device_type=device, dtype=torch.float16, enabled=amp):
                outs = model(x)
            maps = torch.cat([o.float() for o in outs], dim=1)[0].cpu()
            rs, cs = r // STRIDE, c // STRIDE
            acc[:, rs:rs + n, cs:cs + n] += maps * weight
            norm[:, rs:rs + n, cs:cs + n] += weight
            n_tiles += 1
    _sync(device)
    return acc / norm.clamp_min(1e-6), (h, w), n_tiles


def decode(maps: torch.Tensor, shape, thresholds: dict) -> pd.DataFrame:
    obj, ves, fish, size, off = maps[0:1], maps[1:2], maps[2:3], maps[3:4], maps[4:6]
    hmax = F.max_pool2d(obj[None], 3, stride=1, padding=1)[0]
    peaks = (obj * (hmax == obj))[0]
    ys, xs = torch.nonzero(peaks >= thresholds["objectness"], as_tuple=True)
    length_m = ((torch.exp(torch.relu(size[0, ys, xs])) - 1) * PIX_TO_M).numpy()
    # Écrêtage à une longueur physiquement plausible : une sortie aberrante du modèle ne fait pas croire à un
    # navire de plusieurs kilomètres. On compte les écrêtages et on les journalise.
    n_clipped = int((length_m > MAX_LENGTH_M).sum())
    if n_clipped:
        print(f"inference.decode : {n_clipped} longueur(s) écrêtée(s) à {MAX_LENGTH_M:.0f} m", flush=True)
    length_m = np.minimum(length_m, MAX_LENGTH_M)
    df = pd.DataFrame({
        "row": ((ys.float() + off[1, ys, xs]) * STRIDE).numpy(),
        "col": ((xs.float() + off[0, ys, xs]) * STRIDE).numpy(),
        "objectness": peaks[ys, xs].numpy(),
        "vessel_score": ves[0, ys, xs].numpy(),
        "fishing_score": fish[0, ys, xs].numpy(),
        "length_m": length_m,
    })
    h, w = shape
    return df[(df.row < h) & (df.col < w)].reset_index(drop=True)


def local_contrast(band_db: np.ndarray, r: int, c: int, peak_half: int, ring_in: int, ring_out: int) -> float:
    """Écart en dB entre le pic de l'écho et la médiane de la mer dans un anneau autour de lui."""
    lin = 10 ** (band_db / 10)
    peak = np.nanmax(lin[max(r - peak_half, 0):r + peak_half + 1, max(c - peak_half, 0):c + peak_half + 1])
    win = lin[max(r - ring_out, 0):r + ring_out + 1, max(c - ring_out, 0):c + ring_out + 1]
    rr, cc = np.ogrid[:win.shape[0], :win.shape[1]]
    cr, ccen = min(r, ring_out), min(c, ring_out)
    inner = (np.abs(rr - cr) <= ring_in) & (np.abs(cc - ccen) <= ring_in)
    background = np.nanmedian(win[~inner])
    if not np.isfinite(peak) or not np.isfinite(background) or background <= 0:
        return float("nan")
    return float(10 * np.log10(peak / background))


def merge_fragments(det: pd.DataFrame, radius_m: float, length_factor: float) -> pd.DataFrame:
    """Fusionne les détections multiples d'un même grand navire.

    La suppression des non maxima du modèle (noyau de 3 pixels à demi résolution, soit 60 m) laisse subsister des
    pics secondaires sur les très grands échos ; avec un seuil de présence abaissé, ils deviennent des détections à part
    entière, qui peuvent capter l'appariement AIS du pic principal. On garde le pic le plus fort, et on écarte les autres
    situés à moins de max(radius_m, length_factor x longueur estimée du pic retenu).
    """
    if len(det) < 2:
        return det
    det = det.sort_values("objectness", ascending=False).reset_index(drop=True)
    xy = det[["x", "y"]].to_numpy(float)
    keep = np.ones(len(det), dtype=bool)
    for i in range(len(det)):
        if not keep[i]:
            continue
        reach = max(radius_m, length_factor * float(det.length_m.iloc[i]))
        d = np.linalg.norm(xy[i + 1:] - xy[i], axis=1)
        keep[i + 1:] &= d > reach
    return det[keep].reset_index(drop=True)


def detect(image_db: np.ndarray, transform, model, device: str, thresholds: dict, contrast_cfg: dict,
           amp: bool = False, merge_cfg: dict | None = None) -> tuple[pd.DataFrame, int]:
    """Détections géoréférencées (coordonnées projetées x, y) avec leur contraste local en VV."""
    maps, shape, n_tiles = run_model(normalize(image_db), model, device, amp)
    det = decode(maps, shape, thresholds)
    if len(det):
        xs, ys = rasterio.transform.xy(transform, det.row.to_numpy(), det.col.to_numpy(), offset="ul")
        det["x"], det["y"] = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
        if merge_cfg:
            det = merge_fragments(det, merge_cfg["radius_m"], merge_cfg["length_factor"])
        det["contrast_vv_db"] = [
            local_contrast(image_db[1], int(r), int(c), contrast_cfg["peak_half_px"],
                           contrast_cfg["ring_inner_px"], contrast_cfg["ring_outer_px"])
            for r, c in zip(det.row, det.col)
        ]
    else:
        det = det.assign(x=[], y=[], contrast_vv_db=[])
    return det, n_tiles
