"""Absence d'un navire AIS au radar (analyse 106, 27/10/2026) : jugée contre tous les échos, y compris ceux sous le
seuil de détection que le travailleur transmet désormais (échos faibles) ; un écho faible ne fait ni détection ni
alerte."""
import pandas as pd

from mars.config import load_rules
from mars.fusion.pipeline import fuse

RULES = load_rules()
T0 = pd.Timestamp("2026-10-25T17:40:55Z")
BBOX = (1.56, 51.09, 1.90, 51.31)                 # fenêtre ALTAI de l'analyse 106


def _pos(lon, lat, sog=16.8, cog=40.0):
    return pd.DataFrame([{"vessel_id": 1, "mmsi": 538000001, "name": "SEAPEAK YAMAL", "length_m": 294.0,
                          "ts": T0 - pd.Timedelta(minutes=2), "lat": lat, "lon": lon, "sog": sog, "cog": cog},
                         {"vessel_id": 1, "mmsi": 538000001, "name": "SEAPEAK YAMAL", "length_m": 294.0,
                          "ts": T0 + pd.Timedelta(minutes=2), "lat": lat + 0.0083, "lon": lon + 0.0113, "sog": sog,
                          "cog": cog}])


def _vide():
    return pd.DataFrame(columns=["id", "lon", "lat", "objectness", "vessel_score", "fishing_score", "length_m",
                                 "contrast_vv_db", "width_m", "heading_deg", "heading_prob", "on_land"])


def test_ship_without_any_echo_is_unconfirmed():
    _d, _a, n, extras = fuse(_vide(), _pos(1.70, 51.20), T0, BBOX, RULES, 348.0)
    assert n == 1 and [u["name"] for u in extras["unconfirmed"]] == ["SEAPEAK YAMAL"]


def test_weak_echo_in_the_tolerance_is_enough_and_raises_nothing():
    faibles = pd.DataFrame([{"lon": 1.7062, "lat": 51.2044, "score": 0.62, "longueur_m": 280.0}])
    det, alertes, _n, extras = fuse(_vide(), _pos(1.70, 51.20), T0, BBOX, RULES, 348.0, None, faibles)
    assert extras["unconfirmed"] == [] and alertes == [] and len(det) == 0


def test_weak_echo_far_away_does_not_hide_the_absence():
    faibles = pd.DataFrame([{"lon": 1.80, "lat": 51.25, "score": 0.7, "longueur_m": 250.0}])
    _d, _a, _n, extras = fuse(_vide(), _pos(1.70, 51.20), T0, BBOX, RULES, 348.0, None, faibles)
    assert len(extras["unconfirmed"]) == 1


def test_no_absence_where_the_image_has_no_data():
    """Fenêtre NOSTOS de l'analyse 106 : les six colonnes ouest de la grille (17 % des cases, mesure sur l'image réelle)
    sont hors de l'image radar ; un navire AIS qui s'y trouve n'est pas « visible »."""
    from mars.sentinel import dans_coeur, fenetres_lues
    sc = {"fenetres": [{"bbox": [0.13453, 50.4663, 0.47401, 50.68334], "marge_m": 2000, "id": 0}]}
    vide = {"n": 24, "cases": [[i, j] for i in range(6) for j in range(24)]}
    lues = fenetres_lues(sc, [{"index": 0, "sans_donnees": vide}])
    assert lues[0]["sans_donnees"] == vide
    dx = (0.47401 - 0.13453) / 24
    assert not dans_coeur(lues, 0.13453 + 4.5 * dx, 50.57)             # colonne 4 : pas de données
    assert dans_coeur(lues, 0.13453 + 10.5 * dx, 50.57)                # colonne 10 : image lue
    assert dans_coeur(fenetres_lues(sc, None), 0.13453 + 4.5 * dx, 50.57)   # sans mesure : comme avant


# Analyse 106, données réelles (tests/donnees/s106_cas.json) : positions AIS de la base autour du passage du 25/10
# 17:40, détections de production (3, seuil prudent de 0,95 faute d'historique) et échos bruts du rejeu gratuit sur la
# copie publique (au dessus de 0,5)

def _cas106():
    import json
    from pathlib import Path
    from mars.sentinel import track_heading
    c = json.loads((Path(__file__).parent / "donnees" / "s106_cas.json").read_text())
    pos = pd.DataFrame(c["positions"])
    pos["ts"] = pd.to_datetime(pos.ts, utc=True)
    det = pd.DataFrame([{**d, "objectness": d["score"], "vessel_score": d["score"], "fishing_score": 0.1,
                         "contrast_vv_db": 12.0, "width_m": None, "heading_deg": None, "heading_prob": None,
                         "on_land": False} for d in c["production"]])
    xs = [v for f in c["fenetres"] for v in (f["bbox"][0], f["bbox"][2])]
    ys = [v for f in c["fenetres"] for v in (f["bbox"][1], f["bbox"][3])]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    return c, pos, det, bbox, pd.Timestamp(c["t0"]), track_heading((bbox[1] + bbox[3]) / 2, True)


CINQ = {"SEAPEAK YAMAL", "VICTORINE", "MED TUNCER", "KATRE", "XIN HAI TONG 809"}


def test_106_production_declared_the_five_ships_absent():
    c, pos, det, bbox, t0, cap = _cas106()
    _d, _a, _n, extras = fuse(det, pos, t0, bbox, RULES, cap)
    assert CINQ <= {u["name"] for u in extras["unconfirmed"]}


def test_106_weak_echoes_confirm_the_five_ships_and_keep_echo_6():
    """Les échos que le travailleur transmet désormais (entre 0,5 et le seuil de 0,95) : chacun des cinq a le sien."""
    c, pos, det, bbox, t0, cap = _cas106()
    faibles = pd.DataFrame([x for k in ("nostos_sans", "altai_sans") for x in c["echos"][k] if x["score"] < 0.95])
    d, alertes, _n, extras = fuse(det, pos, t0, bbox, RULES, cap, None, faibles)
    assert not CINQ & {u["name"] for u in extras["unconfirmed"]}
    # L'écho 6 reste sans AIS : aucun navire à moins de 5 km au passage (VELOS AQUARIUS, à l'estime, à 11,6 km)
    six = d[d.id == 6].iloc[0]
    assert six.matched_vessel_id is None and any(a["det_index"] == six.name for a in alertes)
    # NOSTOS reste apparié à son écho
    assert d[d.id == 5].iloc[0].matched_vessel_id is not None


# Analyse 108 (réanalyse du passage de 106 avec historique, 27/10/2026) : 11 détections de production
DETECTIONS_108 = [(21, 1.78796, 51.15629, 0.992, 51.5), (11, 0.27765, 50.57187, 0.991, 131.7),
                  (20, 1.74831, 51.18648, 0.985, 35.7), (14, 0.31885, 50.54166, 0.984, 111.5),
                  (12, 0.32649, 50.59999, 0.982, 34.4), (13, 0.27602, 50.60010, 0.969, 29.6),
                  (18, 1.60102, 51.15085, 0.964, 150.3), (15, 0.28881, 50.55524, 0.957, 200.5),
                  (16, 0.26221, 50.51580, 0.954, 119.8), (19, 1.60008, 51.15010, 0.943, 150.3),
                  (17, 0.26315, 50.51836, 0.931, 255.9)]


def test_108_second_echo_of_victorine_is_explained_by_her():
    c, pos, _det, bbox, t0, cap = _cas106()
    det = pd.DataFrame([{"id": i, "lon": lon, "lat": lat, "score": s, "objectness": s, "vessel_score": s,
                         "fishing_score": 0.1, "length_m": L,
                         "contrast_vv_db": 1.0 if i in (16, 20) else 12.0,     # 16 et 20 : « contraste faible » en production
                         "width_m": None, "heading_deg": None, "heading_prob": None, "on_land": False}
                        for i, lon, lat, s, L in DETECTIONS_108])
    d, alertes, _n, extras = fuse(det, pos, t0, bbox, RULES, cap)
    noms = dict(zip(pos.vessel_id, pos.name))
    vu = {int(r.id): noms.get(r.matched_vessel_id) for r in d.itertuples() if r.matched_vessel_id is not None}
    assert vu[18] == vu[19] == "VICTORINE" and d[d.id == 18].iloc[0].echo_voisin_de == 19
    assert CINQ <= set(vu.values()) and extras["unconfirmed"] == []
    sans_ais = {int(d.loc[a["det_index"]].id) for a in alertes}
    assert sans_ais == {21, 13}                    # écho 6 de 106 et le petit écho de 30 m : vrais candidats
    # Sans la règle : le second écho de VICTORINE restait sans AIS
    import copy
    r = copy.deepcopy(RULES)
    r["fusion"]["echo_voisin_m"] = 0
    _d, alertes0, _n, _e = fuse(det, pos, t0, bbox, r, cap)
    assert 18 in {int(_d.loc[a["det_index"]].id) for a in alertes0}
