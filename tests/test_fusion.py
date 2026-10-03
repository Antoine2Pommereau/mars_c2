"""Tests du moteur de fusion : masques, appariement, alerte « navire sombre »."""
import pandas as pd
import pytest

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
    out, alerts, n_ais, extras = fuse(det, pos, T0, BBOX, load_rules())
    assert n_ais == 1
    assert out.mask_reason.tolist() == [None, None, "contraste faible", "non navire", "terre"]
    assert out.matched_vessel_id.iloc[0] == 1
    assert pd.isna(out.matched_vessel_id.iloc[1])


def test_alerte_navire_sombre():
    det, pos = _cas()
    _, alerts, _, _ = fuse(det, pos, T0, BBOX, load_rules())
    assert len(alerts) == 1
    a = alerts[0]
    assert a["det_index"] == 1
    assert a["severity"] == "elevee"            # 43 m : sous le seuil de longueur critique
    assert a["vessel_ids"] == [1]               # le navire AIS examiné figure dans les preuves


def test_tolerance_orientee():
    """Un écho décalé de 700 m le long de la trace est apparié ; le même écart en travers ne l'est pas."""
    det, pos = _cas()
    pos = pos.assign(cog=90.0)                    # navire filant vers l'est, trace orientée nord sud
    lat0 = 57.9010
    for dlat, dlon, expected in [(700 / 110570, 0.0, 1), (0.0, 700 / (111320 * 0.531), None)]:
        d = det.iloc[[0]].assign(lat=lat0 + dlat, lon=10.5 + dlon)
        out, _, _, _ = fuse(d, pos, T0, BBOX, load_rules(), heading_deg=0.0)
        assert out.matched_vessel_id.iloc[0] == expected


def test_position_non_confirmee():
    det, pos = _cas()
    far = det.iloc[[1]]                           # aucun écho près du navire AIS de 120 m
    _, _, _, extras = fuse(far, pos, T0, BBOX, load_rules())
    assert [u["vessel_id"] for u in extras["unconfirmed"]] == [1]


@pytest.mark.parametrize(
    "contrast_db, expect_masked",
    [
        (9.9, True),    # juste sous le seuil de 10 dB du fichier de règles : masqué pour contraste faible
        (10.1, False),  # juste au dessus : la détection passe le filtre de contraste
    ],
)
def test_seuil_contraste(contrast_db, expect_masked):
    """La frontière de 10 dB de config/rules.yaml se comporte comme attendu de part et d'autre."""
    rules = load_rules()
    assert rules["contrast"]["min_vv_db"] == 10.0   # le test suit le seuil versionné
    det, pos = _cas()
    # On isole l'écho de 43 m sans AIS proche (celui qui déclenche le navire sombre) et on fait varier son
    # seul contraste autour de la frontière, toutes choses égales par ailleurs.
    d = det.iloc[[1]].assign(contrast_vv_db=contrast_db)
    out, alerts, _, _ = fuse(d, pos, T0, BBOX, rules)
    if expect_masked:
        assert out.mask_reason.iloc[0] == "contraste faible"
        assert alerts == []                         # un écho masqué ne déclenche pas de navire sombre
    else:
        assert out.mask_reason.iloc[0] is None
        assert len(alerts) == 1                     # écho net sans AIS : alerte de navire sombre


def test_echo_fixe_ne_declenche_pas_d_alerte():
    det, pos = _cas()
    fixed = pd.DataFrame({"source": ["detection"], "ref": [42], "lon": [10.6002], "lat": [57.9501]})
    out, alerts, _, extras = fuse(det, pos, T0, BBOX, load_rules(), fixed_points=fixed)
    assert out.mask_reason.iloc[1] == "echo fixe"       # l'écho de 43 m sans AIS, revu à 25 m près
    assert alerts == []
    assert extras["fixed_hits"][0]["refs"][0]["ref"] == 42
