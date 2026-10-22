"""Lot T, phase 1 : préparation des trajets et références (mars/trajectoire.py), sur des trajectoires synthétiques."""
import math

import numpy as np
import pandas as pd
import pytest

from mars import trajectoire as T

P = dict(T.PARAMS)


def route(mmsi, t0, heures, sog=12.0, cog=90.0, lat=48.0, lon=-6.0, pas_s=60):
    """Navire en ligne droite, une position par minute."""
    n = int(heures * 3600 / pas_s) + 1
    t = t0 + np.arange(n) * pas_s
    la, lo = T.avancer(lat, lon, cog, sog * T.NOEUD * (t - t0))
    return pd.DataFrame({"mmsi": mmsi, "t": t.astype(float), "lat": la, "lon": lo, "sog": sog, "cog": cog})


def test_cuts_on_silence_stop_and_destination_and_drops_jumps():
    a = route(1, 0, 6)
    b = route(1, 6 * 3600 + 3 * 3600, 6, lat=float(a.lat.iloc[-1]), lon=float(a.lon.iloc[-1]) + 0.5)   # 3 h de silence
    arret = route(1, b.t.iloc[-1] + 60, 2, sog=0.1, lat=float(b.lat.iloc[-1]), lon=float(b.lon.iloc[-1]))
    c = route(1, arret.t.iloc[-1] + 60, 6, lat=float(b.lat.iloc[-1]), lon=float(b.lon.iloc[-1]))
    df = pd.concat([a, b, arret, c], ignore_index=True)
    saut = df.iloc[[100]].copy()
    saut["lat"] += 1.0                                     # 111 km en une seconde : impossible
    saut["t"] += 1
    df = pd.concat([df, saut], ignore_index=True)
    changement = float(c.t.iloc[180])                       # nouvelle destination au milieu du dernier tronçon
    trs, st = T.trajets(df, P, destinations={1: [changement]})
    assert st["saut_impossible"] == 1
    assert (st["coupure_silence"], st["coupure_arret"], st["coupure_destination"]) == (1, 1, 1)
    durees = sorted(round(t.duree_h, 1) for t in trs)
    assert durees == [6.0, 6.0], "le dernier tronçon coupé en deux moitiés de 3 h tombe sous les 4 h"
    assert st["trop_courts"] == 2


def test_resampling_every_10_minutes_and_estime_exact_on_a_straight_line():
    trs, _ = T.trajets(route(7, 30, 8), P)
    tr = trs[0]
    assert np.all(np.diff(tr.t) == 600) and tr.t[0] % 600 == 0
    ev = T.evaluer(trs, None, P)
    assert len(ev) == 3, "7 h 50 sur la grille : fenêtres à 3 h, 4 h et 5 h (3 h d'historique, 3 h à prévoir)"
    for h in (1, 2, 3):
        assert ev[f"estime_fde_{h}h"].max() < 0.05 and ev[f"estime_mesuree_fde_{h}h"].max() < 0.05
        assert ev[f"dans_cercle_{h}h"].all()


def test_frequent_routes_follow_a_turning_lane_where_dead_reckoning_drifts():
    """Une voie qui tourne de 90 degrés : l'estime file tout droit, les routes fréquentes prennent le virage."""
    def virage(mmsi, t0):
        a = route(mmsi, t0, 4, cog=90.0)
        b = route(mmsi, t0 + 4 * 3600 + 60, 4, cog=180.0, lat=float(a.lat.iloc[-1]), lon=float(a.lon.iloc[-1]))
        return pd.concat([a, b], ignore_index=True)
    train, _ = T.trajets(pd.concat([virage(m, 0) for m in range(10, 20)], ignore_index=True), P)
    test, _ = T.trajets(virage(99, 0), P)
    ev = T.evaluer(test, T.Routes(P).apprendre(train), P)
    avant = ev[ev.t == 10200]                                # dernière position observée 1 h 10 avant le virage
    assert avant["routes_fde_3h"].iloc[0] < 2.0
    assert avant["estime_fde_3h"].iloc[0] > 15.0
    assert T.resumer(ev)[0]["routes_3h"]["moyenne"] < T.resumer(ev)[0]["estime_3h"]["moyenne"]


def test_coast_filter_cuts_the_track_and_current_circle():
    trs, st = T.trajets(route(3, 0, 10), P, pres_cote=lambda _lat, lon: (lon > -5.6) & (lon < -5.4))
    assert st["filtre_cotier"] and st["points_pres_cote"] > 0
    assert len(trs) == 1 and st["trop_courts"] == 1, "avant la côte : 1 h 20 seulement ; après : 8 h gardées"
    assert T.rayon_actuel(12.0, 3) == pytest.approx(2000 + 0.3 * 12 * 1852 * 3)
    assert T.rayon_actuel(30.0, 3) == 30000
    assert T.categorie(72) == "cargo" and T.categorie(84) == "petrolier" and T.categorie(None) == "inconnu"
    assert math.isclose(T.haversine_m(48, -5, 48, -5), 0.0)


def test_coast_filter_from_land_polygons_in_metres():
    """Un carré de terre : un point à 1 km de son bord est près de la côte, un point à 3 km ne l'est pas."""
    import shapely
    terre = shapely.box(-5.0, 48.0, -4.9, 48.1)
    masque = T.filtre_cotier([shapely.to_wkb(terre)], 1852.0)
    m1, m3 = 1000 / (111_320 * math.cos(math.radians(48))), 3000 / (111_320 * math.cos(math.radians(48)))
    assert list(masque([48.05, 48.05, 48.05], [-4.9 + m1, -4.9 + m3, -4.95])) == [True, False, True]
    assert T.zone_de(48.5, -4.5) == "bretagne" and T.zone_de(50.5, 1.0) == "manche" and T.zone_de(30, 0) is None
    assert T.categorie_libelle("Tanker") == "petrolier" and T.categorie_libelle("Tug") == "autre"


def test_a_lasting_jump_cuts_the_track_instead_of_bridging_it():
    """Deux navires sous un même MMSI : le second apparaît à 150 km et continue ; le trajet est coupé, pas interpolé."""
    a = route(5, 0, 6)
    b = route(5, 6 * 3600 + 60, 6, lat=49.5, lon=-4.0)
    trs, st = T.trajets(pd.concat([a, b], ignore_index=True), P)
    assert st["coupure_saut"] == 1 and st["saut_impossible"] == 0
    assert len(trs) == 2 and all(abs(t.duree_h - 6) < 0.2 for t in trs)


def test_truth_after_a_silence_and_long_horizon_quantiles():
    """Vérité de la vérification satellite : la position où le navire réapparaît, même après un silence ; quantiles
    croissants avec le niveau et l'horizon."""
    a = route(9, 0, 6)
    b = route(9, 9 * 3600, 6, lat=float(a.lat.iloc[-1]), lon=float(a.lon.iloc[-1]) + 0.6)   # 3 h de silence
    df = pd.concat([a, b], ignore_index=True)
    v = T.Verite(df)
    assert v.position(9, 4 * 3600) is not None and v.position(9, 7.5 * 3600) is None, "muet : pas de vérité"
    trs, _ = T.trajets(df, P)
    ev = T.evaluer_longs(trs, v, horizons=(1, 4, 6))
    assert ev["err_1h"].max() < 0.1, "ligne droite : estime exacte à 1 h"
    assert ev["err_4h"].notna().any() and ev["err_4h"].isna().any(), "à 4 h, vu après le silence ou muet"
    tab = T.table_quantiles(ev, vitesses=(1.0,), horizons=(1, 4, 6), min_n=1)
    for ligne in tab["quantiles_m"][0]:
        assert ligne == sorted(ligne)
    assert all(b >= a for a, b in zip(tab["quantiles_m"][0][0], tab["quantiles_m"][0][1]))
