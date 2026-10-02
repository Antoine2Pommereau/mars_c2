"""Tests du moteur de fusion : masques, appariement, alerte « navire sombre »."""
import pandas as pd

from mars.config import load_rules
from mars.fusion.pipeline import fuse

T0 = pd.Timestamp("2024-06-05T17:02:26Z")
BBOX = [10.30, 57.80, 10.80, 58.07]


def _cas():
    det = pd.DataFrame({
        "lon": [10.50, 10.60, 10.40, 10.45, 10.70], "lat": [57.90, 57.95, 58.00, 57.85, 58.02],
        "objectness": [0.6, 0.5, 0.6, 0.4, 0.7], "vessel_score": [0.9, 0.9, 0.9, 0.2, 0.9],
        "fishing_score": [0.1] * 5, "length_m": [120, 43, 30, 20, 60], "contrast_vv_db": [25, 19, 4, 30, 25],
        "on_land": [False, False, False, False, True],
    })
    pos = pd.DataFrame({
        "vessel_id": [1, 1], "mmsi": [219000001] * 2, "name": ["A", "A"], "length_m": [120.0] * 2,
        "ts": [T0 - pd.Timedelta(seconds=30), T0 + pd.Timedelta(seconds=30)],
        "lat": [57.9005, 57.9015], "lon": [10.5, 10.5], "sog": [10.0] * 2, "cog": [0.0] * 2,
    })
    return det, pos


def test_masques_et_appariement():
    det, pos = _cas()
    out, alerts, n_ais = fuse(det, pos, T0, BBOX, load_rules())
    assert n_ais == 1
    assert out.mask_reason.tolist() == [None, None, "contraste faible", "non navire", "terre"]
    assert out.matched_vessel_id.iloc[0] == 1
    assert pd.isna(out.matched_vessel_id.iloc[1])


def test_alerte_navire_sombre():
    det, pos = _cas()
    _, alerts, _ = fuse(det, pos, T0, BBOX, load_rules())
    assert len(alerts) == 1
    a = alerts[0]
    assert a["det_index"] == 1
    assert a["severity"] == "elevee"            # 43 m : sous le seuil de longueur critique
    assert a["vessel_ids"] == [1]               # le navire AIS examiné figure dans les preuves
