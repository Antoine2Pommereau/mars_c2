"""Tests du contrat du modèle et de la chaîne radar.

Une erreur sur ces points ne provoque aucun plantage, mais dégrade silencieusement les détections :
ordre des canaux, normalisation, rééchantillonnage, tuilage, décodage, contraste.
Lancer : python -m pytest tests
"""
import math
import os
from pathlib import Path

import numpy as np
import pytest
import torch

from mars.sar import inference as inf
from mars.sar import sentinelhub as sh

ROOT = Path(__file__).resolve().parents[1]


def test_ordre_des_canaux_vh_puis_vv():
    assert '"VH", "VV", "dataMask"' in sh.EVALSCRIPT
    assert "[s.VH, s.VV, s.dataMask]" in sh.EVALSCRIPT


def test_harmonisation_sigma0_bilineaire():
    assert sh.PROCESSING["backCoeff"] == "SIGMA0_ELLIPSOID"
    assert sh.PROCESSING["upsampling"] == "BILINEAR"
    assert sh.PROCESSING["downsampling"] == "BILINEAR"


def test_normalisation_sigmoide():
    x = np.array([[[-20.0, np.nan, 0.0]]], dtype="float32")
    y = inf.normalize(x)
    assert y[0, 0, 0] == pytest.approx(0.5)
    assert y[0, 0, 1] == 0.0                                   # pixel manquant
    assert y[0, 0, 2] == pytest.approx(1 / (1 + math.exp(-20 * 0.18)), rel=1e-5)


@pytest.mark.parametrize("size", [500, 2048, 2049, 3000, 5000, 8192])
def test_tuiles_couvrent_toute_la_zone(size):
    starts = inf.tile_starts(size)
    assert starts[0] == 0
    assert starts[-1] + inf.TILE >= size
    assert all(b - a <= inf.STEP for a, b in zip(starts, starts[1:]))


def test_decodage_position_longueur():
    n = 64
    maps = torch.zeros((6, n, n))
    maps[0, 10, 20] = 0.9                       # présence
    maps[1, 10, 20] = 0.8                       # navire
    maps[3, 10, 20] = math.log(1 + 12)          # longueur : 12 pixels
    maps[4, 10, 20] = 0.25                      # décalage en colonne
    maps[5, 10, 20] = 0.5                       # décalage en ligne
    det = inf.decode(maps, (2 * n, 2 * n), {"objectness": 0.3, "vessel": 0.338, "fishing": 0.35})
    assert len(det) == 1
    d = det.iloc[0]
    assert d.row == pytest.approx((10 + 0.5) * 2)
    assert d.col == pytest.approx((20 + 0.25) * 2)
    assert d.length_m == pytest.approx(120, rel=1e-4)


def test_ponderation_pyramidale():
    w = inf._pyramid_weight()
    assert w.shape == (1, inf.TILE // inf.STRIDE, inf.TILE // inf.STRIDE)
    assert float(w.max()) == pytest.approx(1.0)
    assert float(w.min()) > 0


def test_contraste_local():
    img = np.full((101, 101), -20.0, dtype="float32")
    img[50, 50] = 10.0
    assert inf.local_contrast(img, 50, 50, 2, 10, 40) == pytest.approx(30.0, abs=1e-3)


@pytest.mark.skipif(os.environ.get("MARS_TEST_MODEL") != "1", reason="MARS_TEST_MODEL=1 pour tester le modèle réel")
def test_modele_sorties():
    model = inf.load_model(ROOT / "models" / "xview3_membre4_v2s.jit", "cpu")
    with torch.no_grad():
        out = model(torch.zeros(1, 2, inf.TILE, inf.TILE))
    assert len(out) == 5
    assert tuple(out[4].shape) == (1, 2, inf.TILE // 2, inf.TILE // 2)


def test_fusion_des_fragments_d_un_grand_echo():
    import pandas as pd
    det = pd.DataFrame({"x": [0.0, 80.0, 1000.0], "y": [0.0, 0.0, 0.0], "objectness": [0.26, 0.53, 0.4],
                        "length_m": [61.0, 106.0, 40.0]})
    out = inf.merge_fragments(det, 150, 0.6)
    assert sorted(out.objectness.tolist()) == [0.4, 0.53]   # le pic secondaire à 80 m disparaît, l'écho distant reste
