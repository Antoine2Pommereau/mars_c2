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
