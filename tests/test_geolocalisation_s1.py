"""Géolocalisation d'une fenêtre Sentinel 1 sur la vraie géométrie d'un GRD (enquête sur l'analyse 103, 24/10/2026).

Points d'appui : les 210 du produit S1D du 23/10/2026 à 17:57 UTC (passage ascendant du soir, image retournée par
rapport au nord, 26 314 × 16 675 pixels), lus dans la copie publique de Planetary Computer
(tests/donnees/s1d_20261023_points_appui.json). Ce sont les points de la grille de géolocalisation de l'ESA : leur
longitude et leur latitude font foi, à une dizaine de mètres près.

Chemin éprouvé, celui du travailleur et de la chaîne d'AI2 : fenêtre en pixels couvrant une emprise décentrée (même
calcul que fenetre_pixels), points d'appui recalés sur la fenêtre (ce que fait gdal.Translate avec srcWin, vérifié par
l'essai `essai_fenetres.py --geolocalisation` dans l'image d'AI2 avec le vrai code du travailleur), reprojection en Web
Mercator au pas d'AI2 (spline en plaque mince des points d'appui, rééchantillonnage bilinéaire, comme warp_tps), pic de
la cible, puis longitude et latitude par la transformation de l'image reprojetée. Chaque cible est posée au pixel d'un
point d'appui : elle doit revenir à moins de 50 m de la position de l'ESA.

Constat qui a motivé la correction : le polynôme d'ordre 2 que GDAL ajuste par défaut sur ces 210 points s'en écarte
de 54 m en médiane et de 544 m au plus (75 à 100 m en baie de Weymouth) ; la chaîne d'origine d'AI2 plaçait ainsi les
cibles à 80 m de leur position.
"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.control import GroundControlPoint  # noqa: E402
from rasterio.transform import GCPTransformer  # noqa: E402
from rasterio.warp import Resampling, calculate_default_transform, reproject  # noqa: E402

DONNEES = json.loads((Path(__file__).parent / "donnees" / "s1d_20261023_points_appui.json").read_text())
COTE_MIN_PX = 2300                       # comme travailleurs/sentinel.py
WEB_MERCATOR_M = 2 * math.pi * 6378137


def _metres(a, b) -> float:
    return math.hypot((a[0] - b[0]) * 111320 * math.cos(math.radians(b[1])), (a[1] - b[1]) * 110574)


def _gcps(dx: float = 0, dy: float = 0) -> list:
    return [GroundControlPoint(row=r - dy, col=c - dx, x=x, y=y, z=z) for c, r, x, y, z in DONNEES["points_appui"]]


def _fenetre_pixels(tr, W: int, H: int, bbox: list) -> list:
    """Même calcul que travailleurs/sentinel.fenetre_pixels (bords de l'emprise par les points d'appui)."""
    x0g, y0g, x1g, y1g = bbox
    xs, ys = [], []
    for k in range(5):
        for lon, lat in ((x0g + (x1g - x0g) * k / 4, y0g), (x0g + (x1g - x0g) * k / 4, y1g),
                         (x0g, y0g + (y1g - y0g) * k / 4), (x1g, y0g + (y1g - y0g) * k / 4)):
            r, c = tr.rowcol(lon, lat, op=lambda v: v)
            xs.append(c)
            ys.append(r)
    x0, x1 = max(0, int(min(xs))), min(W, int(math.ceil(max(xs))))
    y0, y1 = max(0, int(min(ys))), min(H, int(math.ceil(max(ys))))
    if x1 - x0 < COTE_MIN_PX:
        c = (x0 + x1) // 2
        x0 = max(0, min(c - COTE_MIN_PX // 2, W - COTE_MIN_PX))
        x1 = min(W, x0 + COTE_MIN_PX)
    if y1 - y0 < COTE_MIN_PX:
        c = (y0 + y1) // 2
        y0 = max(0, min(c - COTE_MIN_PX // 2, H - COTE_MIN_PX))
        y1 = min(H, y0 + COTE_MIN_PX)
    return [x0, y0, x1 - x0, y1 - y0]


def _pas_ai2(px_m: float = 10.0) -> float:
    """Pas de sortie de src/data/warp.py : premier niveau de Web Mercator plus fin que 1,1 fois le pixel d'origine."""
    for zoom in range(20):
        pas = WEB_MERCATOR_M / 512 / (2 ** zoom)
        if pas < px_m * 1.1:
            return pas
    return pas


def test_points_appui_reels_ascendant():
    """Le produit est bien un passage ascendant : les lignes montent vers le nord, les colonnes vont vers l'est."""
    W, H = DONNEES["taille"]
    tr = GCPTransformer(_gcps())
    lon_a, lat_a = tr.xy(H / 2, W / 2)
    lon_n, lat_n = tr.xy(H / 2 + 1000, W / 2)
    lon_e, lat_e = tr.xy(H / 2, W / 2 + 1000)
    assert len(DONNEES["points_appui"]) == 210
    assert lat_n > lat_a + 0.05 and lon_e > lon_a + 0.1


def test_polynome_par_defaut_ecarte_spline_exacte():
    """Le polynôme par défaut de GDAL s'écarte de la grille de l'ESA de plusieurs centaines de mètres ; la spline en
    plaque mince (GCP_TPS, choisie par le travailleur) passe par chaque point."""
    poly, tps = GCPTransformer(_gcps()), GCPTransformer(_gcps(), tps=True)
    e_poly = [_metres(poly.xy(r, c, offset="ul"), (x, y)) for c, r, x, y, _z in DONNEES["points_appui"]]
    e_tps = [_metres(tps.xy(r, c, offset="ul"), (x, y)) for c, r, x, y, _z in DONNEES["points_appui"]]
    assert max(e_poly) > 300 and sorted(e_poly)[len(e_poly) // 2] > 30
    assert max(e_tps) < 1


@pytest.mark.parametrize("decalage_km", [(0, 0), (7, -5), (-6, 4)])
def test_fenetre_decentree_reprojetee_comme_ai2(tmp_path, decalage_km):
    """Fenêtre de 24 km décentrée autour de la baie de Weymouth ; cibles aux points d'appui intérieurs : pic retrouvé
    après reprojection en Web Mercator, à moins de 50 m de la position de l'ESA."""
    W, H = DONNEES["taille"]
    tr = GCPTransformer(_gcps(), tps=True)
    lon_c = -2.36678 + decalage_km[0] * 1000 / (111320 * math.cos(math.radians(50.563)))
    lat_c = 50.56301 + decalage_km[1] * 1000 / 110574
    demi = 12000
    dlat, dlon = demi / 110574, demi / (111320 * math.cos(math.radians(lat_c)))
    bbox = [lon_c - dlon, lat_c - dlat, lon_c + dlon, lat_c + dlat]
    x0, y0, w, h = _fenetre_pixels(tr, W, H, bbox)
    cibles = [(c, r, x, y) for c, r, x, y, _z in DONNEES["points_appui"]
              if x0 + 60 <= c < x0 + w - 60 and y0 + 60 <= r < y0 + h - 60]
    # Ajoute une cible hors grille au centre de l'emprise (position par le polynôme de la scène entière)
    rc, cc = tr.rowcol(lon_c, lat_c, op=lambda v: v)
    cibles.append((cc, rc, lon_c, lat_c))
    assert len(cibles) >= 2

    # Fenêtre découpée : points d'appui recalés de l'origine de la fenêtre (comportement de gdal.Translate srcWin)
    fen = np.zeros((h, w), dtype="float32")
    for c, r, _x, _y in cibles:
        cx, cy = int(round(c)) - x0, int(round(r)) - y0
        fen[cy - 1:cy + 2, cx - 1:cx + 2] = 3000.0
    gcps_f = _gcps(x0, y0)
    pas = _pas_ai2()
    dst_tr, dw, dh = calculate_default_transform("EPSG:4326", "EPSG:3857", w, h, gcps=gcps_f, resolution=pas,
                                                 SRC_METHOD="GCP_TPS")
    out = np.zeros((dh, dw), dtype="float32")
    reproject(fen, out, gcps=gcps_f, src_crs="EPSG:4326", dst_transform=dst_tr, dst_crs="EPSG:3857",
              resampling=Resampling.bilinear, SRC_METHOD="GCP_TPS")
    from pyproj import Transformer
    vers_merc = Transformer.from_crs(4326, 3857, always_xy=True)
    vers_geo = Transformer.from_crs(3857, 4326, always_xy=True)
    inv = ~dst_tr
    ecarts = []
    for _c, _r, lon, lat in cibles:
        mx, my = vers_merc.transform(lon, lat)
        col, row = inv @ (mx, my)
        r0, c0 = int(row), int(col)
        bloc = out[max(0, r0 - 40):r0 + 41, max(0, c0 - 40):c0 + 41]
        assert bloc.max() > 100, "cible absente à moins de 40 pixels de sa position attendue"
        pr, pc = np.unravel_index(np.argmax(bloc), bloc.shape)
        # Comme AI2 : colonne et ligne entières du pic, puis transformation de l'image reprojetée
        gx, gy = dst_tr @ (max(0, c0 - 40) + pc, max(0, r0 - 40) + pr)
        ecarts.append(_metres(vers_geo.transform(gx, gy), (lon, lat)))
    assert max(ecarts) < 50, f"cible retrouvée à {max(ecarts):.0f} m de sa position (fenêtre {[x0, y0, w, h]})"
