"""Fusion d'un navire à l'arrêt (analyse 105, 25/10/2026) : SMYRTOS au mouillage devant Weymouth, une seule position
AIS à 13 min du passage (17:44:13, 0,1 nœud), écho à 143 m, apparié à rien, alerte DARK_SHIP élevée fausse. Sa
dernière position reste candidate 120 min avec une tolérance qui croît avec l'âge ; prudence : un navire connu dans
cette durée qui peut expliquer un écho rend l'alerte faible et à confirmer ; refusion d'une analyse terminée, alerte
mise à jour en place, décisions des opérateurs gardées ; un vrai navire sombre reste une alerte. En base :
MARS_TEST_DATABASE_URL."""
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from mars import sentinel, viirs
from mars.config import load_rules
from mars.fusion.arret import arret_params, maintien_arret
from mars.fusion.doute import compatible
from mars.fusion.positions import positions_at

RULES = load_rules()
URL = os.environ.get("MARS_TEST_DATABASE_URL")
T0 = datetime(2026, 10, 23, 17, 57, 17, tzinfo=timezone.utc)          # détection 4 de l'analyse 105
T_AIS = datetime(2026, 10, 23, 17, 44, 13, tzinfo=timezone.utc)        # seule position de SMYRTOS dans l'heure
SMYRTOS = (-2.36678, 50.56301)
ECHO = (-2.36678, 50.56301 + 143 / 110_574)                           # 143 m au nord
SOMBRE = (-2.30, 50.52)                                               # à 6,5 km de SMYRTOS, aucun AIS
AVANT = {**RULES, "fusion": {**RULES["fusion"], "arret_max_min": 0}}  # règles de l'analyse 105 : rien au delà de 10 min


def _pos(points):
    return pd.DataFrame([{"vessel_id": vid, "mmsi": 1, "name": "N", "length_m": 250.0, "ts": pd.Timestamp(t),
                          "lon": lon, "lat": lat, "sog": sog, "cog": cog} for vid, t, lon, lat, sog, cog in points])


def test_smyrtos_position_at_13_minutes_is_kept_with_a_wider_tolerance():
    arret = arret_params(RULES)
    a = positions_at(_pos([(2458, T_AIS, *SMYRTOS, 0.1, 270.0)]), pd.Timestamp(T0), pd.Timedelta(minutes=10), arret)
    r = a.iloc[0]
    assert r.method == "arret" and r.age_min == pytest.approx(13.1, abs=0.1)
    assert r.tolerance_extra_m == pytest.approx(2.5 * 13.07, abs=0.5)          # 333 m de tolérance avec la base
    # Avant la correction : aucune position (13 min au delà des 10 de max_gap_min)
    assert not len(positions_at(_pos([(2458, T_AIS, *SMYRTOS, 0.1, 270.0)]), pd.Timestamp(T0), pd.Timedelta(minutes=10)))


@pytest.mark.parametrize("points, motif", [
    ([(T_AIS - timedelta(minutes=110), *SMYRTOS, 0.1, None)], "au delà de 120 min"),
    ([(T_AIS, *SMYRTOS, 6.0, 90.0)], "en route : l'estime seulement, 10 min au plus"),
    ([(T_AIS, *SMYRTOS, 0.1, None), (T0 + timedelta(minutes=5), -2.35, 50.56, 0.2, None)], "suivante à 1,2 km"),
    ([(T_AIS, *SMYRTOS, 0.1, None), (T0 + timedelta(minutes=25), *SMYRTOS, 7.0, 90.0)], "reparti en route après"),
])
def test_stationary_hold_is_bounded_and_contradicted(points, motif):
    pts = [{"ts": t, "lon": lon, "lat": lat, "sog": sog} for t, lon, lat, sog, _c in points]
    assert maintien_arret(pts, T0, arret_params(RULES)) is None, motif


def test_moored_neighbour_confirms_the_hold():
    """Positions de part et d'autre, plus de 10 min chacune, au même endroit : le navire n'a pas bougé."""
    pts = [{"ts": T_AIS, "lon": SMYRTOS[0], "lat": SMYRTOS[1], "sog": 0.1},
           {"ts": T0 + timedelta(minutes=40), "lon": SMYRTOS[0] + 0.0002, "lat": SMYRTOS[1], "sog": 0.0}]
    assert maintien_arret(pts, T0, arret_params(RULES))[1] == pytest.approx(13.07, abs=0.01)


def test_prudence_rule_names_a_compatible_vessel_and_spares_a_matched_one():
    arret = arret_params(RULES)
    # Navire en route vu 30 min avant à 10 nœuds plein est : l'estime le place à 9,3 km ; écho à 1 km de là
    t = T0 - timedelta(minutes=30)
    lon_est = SMYRTOS[0] + 10 * 0.514444 * 1800 / (111_320 * 0.6355)
    tracks = {7: [{"ts": t, "lon": SMYRTOS[0], "lat": SMYRTOS[1], "sog": 10.0, "cog": 90.0}]}
    c = compatible(lon_est + 0.014, SMYRTOS[1], T0, tracks, arret)
    assert c and c["vessel_id"] == 7 and c["age_min"] == 30 and c["distance_m"] < c["rayon_m"]
    assert compatible(lon_est + 0.014, SMYRTOS[1], T0, tracks, arret, exclus={7}) is None   # déjà apparié ailleurs
    assert compatible(*SOMBRE, T0, {2458: [{"ts": T_AIS, "lon": SMYRTOS[0], "lat": SMYRTOS[1], "sog": 0.1,
                                            "cog": None}]}, arret) is None             # vrai navire sombre


def test_viirs_matches_a_vessel_at_anchor_seen_40_minutes_before():
    track = [{"ts": T0 - timedelta(minutes=40), "lon": SMYRTOS[0], "lat": SMYRTOS[1], "sog": 0.0, "cog": None}]
    dets = [{"lon": SMYRTOS[0] + 0.01, "lat": SMYRTOS[1], "ts": T0}]               # 700 m, pixel de 750 m
    assert viirs.match(dets, {2458: track}, RULES) == {0: (2458, pytest.approx(707, abs=5))}
    assert viirs.match(dets, {2458: track}, AVANT) == {}                           # avant : 20 min d'estime au plus


# En base : le cas exact, de l'analyse à la refusion

@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL)
    yield c
    c.rollback()                                           # rien n'est gardé
    c.close()


def _cas_105(conn) -> tuple[int, int, dict]:
    for sql in ("DELETE FROM alert_evidence", "DELETE FROM alert_actions", "DELETE FROM alerts", "DELETE FROM detections",
                "DELETE FROM analyses", "DELETE FROM reception_cells", "DELETE FROM fixed_echoes", "DELETE FROM stats_minute",
                "DELETE FROM watchlist WHERE imo = 9999105",
                "DELETE FROM positions WHERE vessel_id IN (SELECT id FROM vessels WHERE mmsi = 273999105)",
                "DELETE FROM vessels WHERE mmsi = 273999105"):
        conn.execute(sql)
    vid = conn.execute("INSERT INTO vessels (mmsi, imo, name, length_m, ais_class) VALUES (273999105, 9999105, "
                       "'SMYRTOS ESSAI', 250, 'A') RETURNING id").fetchone()[0]
    conn.execute("INSERT INTO watchlist (source, imo, name, risks, datasets, sanctioned) VALUES "
                 "('opensanctions', 9999105, 'SMYRTOS ESSAI', '{sanction}', '{eu_essai}', true)")
    conn.execute("REFRESH MATERIALIZED VIEW vessel_watch")
    conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn, cog_deg) VALUES (%s, %s, "
                 "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 0.1, NULL)", (vid, T_AIS, *SMYRTOS))
    conn.execute("INSERT INTO reception_cells (cx, cy, messages, vessels, hours, coverage, geom) "
                 "SELECT x, y, 100, 5, 10, 0.95, ST_MakeEnvelope(x * 0.1, y * 0.05, (x + 1) * 0.1, (y + 1) * 0.05, 4326)::geography "
                 "FROM generate_series(-27, -21) x, generate_series(1008, 1014) y")
    pid = conn.execute(
        "INSERT INTO sar_passes (product_name, platform, acquired_at, ended_at, orbit_direction, mission, satellite, mode, "
        "statut) VALUES ('S1D_OR9105', 'sentinel-1d', %s, %s, 'ascending', 'S1', 'S1D', 'IW', 'acquis') RETURNING id",
        (T0 - timedelta(seconds=12), T0 + timedelta(seconds=13))).fetchone()[0]
    emprise = {"type": "Polygon", "coordinates": [[[-2.6, 50.4], [-2.1, 50.4], [-2.1, 50.7], [-2.6, 50.7], [-2.6, 50.4]]]}
    scene = {"id": "S1D_ESSAI_105", "debut": (T0 - timedelta(seconds=12)).isoformat(),
             "fin": (T0 + timedelta(seconds=13)).isoformat(), "orbite": "ascending", "satellite": "S1D",
             "emprise": emprise}
    aid = conn.execute(
        "INSERT INTO analyses (pass_id, aoi, mode, status, source, scenes, completed_at) VALUES (%s, "
        "ST_MakeEnvelope(-2.6, 50.4, -2.1, 50.7, 4326)::geography, 'full', 'done', 'sentinel1', %s, now()) RETURNING id",
        (pid, json.dumps([scene]))).fetchone()[0]
    for lon, lat, longueur in ((*ECHO, 252.0), (*SOMBRE, 80.0)):
        conn.execute("INSERT INTO detections (analysis_id, geom, objectness, vessel_score, length_m, scene, ts, score) "
                     "VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 0.966, 0.966, %s, 'S1D_ESSAI_105', "
                     "%s, 0.966)", (aid, lon, lat, longueur, T0))
    return aid, vid, scene


def _alertes(conn):
    return {round(r[3], 3): r[:3] for r in conn.execute(
        "SELECT id, status, severity, ST_Y(geom::geometry) FROM alerts WHERE type = 'DARK_SHIP'").fetchall()}


def test_case_105_before_the_fix_raises_the_false_alert(conn):
    aid, vid, _sc = _cas_105(conn)
    sentinel.fuse_analysis(conn, aid, AVANT)
    a = _alertes(conn)
    # Alerte 75182 : fausse, élevée (critique ici : la base de test a de vraies infrastructures près de Portland)
    assert a[round(ECHO[1], 3)][1] == "nouvelle" and a[round(ECHO[1], 3)][2] in ("elevee", "critique")
    assert conn.execute("SELECT count(*) FROM detections WHERE analysis_id = %s AND matched_vessel_id IS NOT NULL",
                        (aid,)).fetchone()[0] == 0


def test_refusion_matches_smyrtos_and_classifies_the_alert_in_place(conn):
    aid, vid, _sc = _cas_105(conn)
    sentinel.fuse_analysis(conn, aid, AVANT)
    fausse, sombre = (_alertes(conn)[round(y, 3)][0] for y in (ECHO[1], SOMBRE[1]))
    r = sentinel.refusionner(conn, aid, RULES)
    assert r["appariees_avant"] == 0 and r["appariees"] == 1 and r["alertes_classees"] == [fausse]
    d = conn.execute("SELECT matched_vessel_id FROM detections WHERE analysis_id = %s AND length_m = 252",
                     (aid,)).fetchone()[0]
    assert d == vid
    st, details = conn.execute("SELECT status, details FROM alerts WHERE id = %s", (fausse,)).fetchone()
    m = details["refusion"]["motif"]                       # 143 m à plat, 144 m géodésiques (PostGIS)
    assert st == "classee" and "SMYRTOS ESSAI" in m and ("143 m" in m or "144 m" in m)
    assert conn.execute("SELECT action, motif, author FROM alert_actions WHERE alert_id = %s", (fausse,)).fetchone() == \
        ("classer", "faux_positif", "MARS C2")
    # Le vrai navire sombre reste une alerte, levée en place (même identifiant), toujours élevée et ouverte
    assert sombre in r["alertes_levees"]
    st_s, sev_s = conn.execute("SELECT status, severity FROM alerts WHERE id = %s", (sombre,)).fetchone()
    assert st_s == "nouvelle" and sev_s in ("elevee", "critique")
    assert conn.execute("SELECT count(*) FROM alerts WHERE type = 'DARK_SHIP'").fetchone()[0] == 2    # rien d'effacé
    # Refaire une seconde fois ne change rien
    r2 = sentinel.refusionner(conn, aid, RULES)
    assert r2["alertes_classees"] == [] and r2["appariees"] == 1
    assert conn.execute("SELECT count(*) FROM alert_actions WHERE alert_id = %s", (fausse,)).fetchone()[0] == 1


@pytest.mark.parametrize("statut", ["classee", "confirmee", "acquittee"])
def test_refusion_keeps_an_operator_decision(conn, statut):
    aid, _vid, _sc = _cas_105(conn)
    sentinel.fuse_analysis(conn, aid, AVANT)
    fausse = _alertes(conn)[round(ECHO[1], 3)][0]
    action = {"classee": "classer", "confirmee": "confirmer", "acquittee": "acquitter"}[statut]
    conn.execute("UPDATE alerts SET status = %s WHERE id = %s", (statut, fausse))
    conn.execute("INSERT INTO alert_actions (alert_id, action, motif, note, author) VALUES (%s, %s, %s, 'à la main', "
                 "'antoine')", (fausse, action, "activite_legitime" if action == "classer" else None))
    r = sentinel.refusionner(conn, aid, RULES)
    assert r["decisions_gardees"] == [fausse] and r["alertes_classees"] == []
    st, details = conn.execute("SELECT status, details FROM alerts WHERE id = %s", (fausse,)).fetchone()
    assert st == statut and details["refusion"]["decision_gardee"] == statut
    assert conn.execute("SELECT array_agg(author) FROM alert_actions WHERE alert_id = %s", (fausse,)).fetchone()[0] == ["antoine"]


def test_doubtful_echo_is_low_and_to_be_confirmed_never_high(conn):
    """Un écho à 500 m de SMYRTOS (hors de sa tolérance de 333 m, dans un doute élargi par un autre navire en route) :
    alerte gardée, faible, à confirmer, avec le navire compatible en preuve."""
    aid, vid, _sc = _cas_105(conn)
    autre = conn.execute("INSERT INTO vessels (mmsi, name, length_m, ais_class) VALUES (273999106, 'EN ROUTE ESSAI', 90, "
                         "'A') RETURNING id").fetchone()[0]
    t = T0 - timedelta(minutes=30)                         # vu il y a 30 min, à 10 nœuds plein est vers l'écho sombre
    lon0 = SOMBRE[0] - 10 * 0.514444 * 1800 / (111_320 * 0.6361) - 0.01
    conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn, cog_deg) VALUES (%s, %s, "
                 "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 10, 90)", (autre, t, lon0, SOMBRE[1]))
    sentinel.fuse_analysis(conn, aid, RULES)
    sombre = _alertes(conn)[round(SOMBRE[1], 3)]
    assert sombre[1:] == ("nouvelle", "faible")
    d = conn.execute("SELECT details FROM alerts WHERE id = %s", (sombre[0],)).fetchone()[0]
    assert d["a_confirmer"] and d["navire_compatible"]["vessel_id"] == autre
    assert any("possiblement compatible" in c for c in d["contexte"])
    assert conn.execute("SELECT count(*) FROM alert_evidence WHERE alert_id = %s AND evidence_type = 'vessel' AND "
                        "evidence_id = %s", (sombre[0], autre)).fetchone()[0] == 1
    # SMYRTOS, lui, est apparié directement : aucune alerte sur son écho
    assert round(ECHO[1], 3) not in _alertes(conn)


def test_refusion_refuses_an_unfinished_or_unknown_analysis(conn):
    aid, _vid, _sc = _cas_105(conn)
    conn.execute("UPDATE analyses SET status = 'running' WHERE id = %s", (aid,))
    assert "terminée seulement" in sentinel.refusionner(conn, aid, RULES)["refus"]
    assert "inconnue" in sentinel.refusionner(conn, 10 ** 9, RULES)["refus"]


def test_command_refuses_with_its_reason_and_reports_the_refusion(conn, capsys):
    import argparse
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import taches
    with pytest.raises(SystemExit, match="REFUS : analyse 1000000000 inconnue"):
        taches._sentinel_refusionner(conn, argparse.Namespace(args=["1000000000"]))
    aid, _vid, _sc = _cas_105(conn)
    sentinel.fuse_analysis(conn, aid, AVANT)
    taches._sentinel_refusionner(conn, argparse.Namespace(args=[str(aid)]))
    out = capsys.readouterr().out
    assert f"Analyse {aid} refusionnée : 0 écho(s) apparié(s) avant, 1 après" in out
    st, details = conn.execute("SELECT status, details FROM task_runs WHERE task = 'sentinel_refusion' ORDER BY id DESC "
                               "LIMIT 1").fetchone()
    assert st == "ok" and details["appariees"] == 1


def test_smyrtos_under_way_at_the_sentinel2_pass_is_not_held():
    """Analyse 112 (Sentinel 2, 27/10/2026 11:27:09) : écho de 238 m à la position de SMYRTOS, non apparié. Le
    maintien à l'arrêt ne s'applique pas, à raison : à 11:06 SMYRTOS déclarait 3,5 nœuds en route (statut 0), à 993 m
    de l'écho ; aucun message entre 11:06 et 11:37 (31 min, au delà des 10 min de l'interpolation). Positions réelles."""
    t0 = datetime(2026, 10, 27, 11, 27, 9, tzinfo=timezone.utc)
    pts = [(datetime(2026, 10, 27, 10, 38, 23, tzinfo=timezone.utc), -2.36060, 50.57955, 2.7, 223.9),
           (datetime(2026, 10, 27, 11, 6, 23, tzinfo=timezone.utc), -2.38669, 50.56080, 3.5, 217.6),
           (datetime(2026, 10, 27, 11, 37, 52, tzinfo=timezone.utc), -2.38393, 50.55545, 1.6, 349.3),
           (datetime(2026, 10, 27, 11, 46, 53, tzinfo=timezone.utc), -2.38320, 50.55960, 1.7, 2.8)]
    arret = arret_params(RULES)
    assert maintien_arret([{"ts": t, "lon": x, "lat": y, "sog": v} for t, x, y, v, _c in pts], t0, arret) is None
    pos = pd.DataFrame([{"vessel_id": 1, "mmsi": 613411302, "name": "SMYRTOS", "length_m": 244.0, "ts": pd.Timestamp(t),
                         "lon": x, "lat": y, "sog": v, "cog": c} for t, x, y, v, c in pts])
    assert positions_at(pos, pd.Timestamp(t0), pd.Timedelta(minutes=RULES["fusion"]["max_gap_min"]), arret).empty
