"""Refusion VIIRS (26/10/2026) : appariement refait sur les détections en base avec les règles en vigueur. Jusqu'aux
règles 2026.10.54, VIIRS n'acceptait que 20 min d'estime : un navire au mouillage vu 40 min avant levait une alerte
DARK_SHIP fausse. Trois lumières : un navire au mouillage (alerte classée par MARS C2, demande d'observation annulée),
un navire en route vu 30 min avant (alerte abaissée en faible, à confirmer), un vrai navire sombre (alerte gardée,
ouverte) ; décisions des opérateurs gardées ; simulation sans aucune écriture. En base : MARS_TEST_DATABASE_URL."""
import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from mars import viirs
from mars.config import load_rules

RULES = load_rules()
AVANT = {**RULES, "fusion": {**RULES["fusion"], "arret_max_min": 0}}      # règles d'avant le 25/10/2026
URL = os.environ.get("MARS_TEST_DATABASE_URL")
T = datetime(2026, 10, 17, 1, 40, tzinfo=timezone.utc)
MOUILLAGE = (6.47, 42.91)                       # au large de Hyères
SOMBRE = (6.70, 42.80)                          # à 21 km, aucun AIS
DOUTE = (6.30, 43.05)


@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL)
    yield c
    c.rollback()
    c.close()


def _cas(conn) -> dict:
    for sql in ("DELETE FROM alert_evidence", "DELETE FROM alert_actions", "DELETE FROM alerts",
                "DELETE FROM demandes_origines", "DELETE FROM demandes_observation", "DELETE FROM viirs_detections",
                "DELETE FROM viirs_granules", "DELETE FROM reception_cells", "DELETE FROM stats_minute",
                "DELETE FROM positions WHERE vessel_id IN (SELECT id FROM vessels WHERE mmsi IN (273999201, 273999202, "
                "273999203))", "DELETE FROM vessels WHERE mmsi IN (273999201, 273999202, 273999203)"):
        conn.execute(sql)
    ids = {}
    for k, mmsi, name in (("mouillage", 273999201, "MOUILLE ESSAI"), ("route", 273999202, "EN ROUTE ESSAI"),
                          ("voisin", 273999203, "VOISIN ESSAI")):
        ids[k] = conn.execute("INSERT INTO vessels (mmsi, name, length_m, ais_class) VALUES (%s, %s, 120, 'A') "
                              "RETURNING id", (mmsi, name)).fetchone()[0]
    pos = [("mouillage", T - timedelta(minutes=40), MOUILLAGE[0] + 0.004, MOUILLAGE[1], 0.1, None),
           # en route plein ouest à 10 nœuds, vu 30 min avant : l'estime le place à 2,4 km de la lumière
           ("route", T - timedelta(minutes=30), DOUTE[0] + 0.155, DOUTE[1], 10.0, 270.0),
           # réception AIS alentour (évaluable) : un navire à 10 km de chaque lumière, à l'heure du passage
           ("voisin", T - timedelta(minutes=1), 6.58, 42.86, 0.0, None)]
    for k, t, lon, lat, sog, cog in pos:
        conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn, cog_deg) VALUES (%s, %s, "
                     "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s)", (ids[k], t, lon, lat, sog, cog))
    gid = conn.execute("INSERT INTO viirs_granules (nom, satellite, nuit, debut, fin, lune) VALUES "
                       "('NOAA21.A2026290.0136', 'NOAA21', '2026-10-16', %s, %s, 35) RETURNING id",
                       (T - timedelta(minutes=3), T + timedelta(minutes=3))).fetchone()[0]
    dets = {}
    for k, (lon, lat) in (("mouillage", MOUILLAGE), ("sombre", SOMBRE), ("doute", DOUTE)):
        dets[k] = conn.execute("INSERT INTO viirs_detections (granule_id, ts, geom, nanowatts, lune, distance_cote_m, "
                               "ais_navires_rayon) VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, "
                               "21.9, 35, 30000, 3) RETURNING id", (gid, T, lon, lat)).fetchone()[0]
    viirs._alerts(conn, list(dets.values()), AVANT)             # alertes levées par les règles d'avant
    alertes = {k: conn.execute("SELECT a.id FROM alerts a JOIN alert_evidence e ON e.alert_id = a.id AND "
                               "e.evidence_type = 'viirs' AND e.evidence_id = %s", (d,)).fetchone()[0]
               for k, d in dets.items()}
    return {"vessels": ids, "dets": dets, "alertes": alertes, "granule": gid}


def _demande(conn, alerte: int, etat: str, analyse: int | None = None) -> int:
    did = conn.execute(
        "INSERT INTO demandes_observation (tip, priorite, position, position_le, etat, expire_le, analysis_id) VALUES "
        "('viirs', 2, ST_SetSRID(ST_MakePoint(6.47, 42.91), 4326)::geography, %s, %s, %s, %s) RETURNING id",
        (T, etat, T + timedelta(hours=24), analyse)).fetchone()[0]
    conn.execute("INSERT INTO demandes_origines (cle, demande_id, tip, priorite, alerte_id) VALUES (%s, %s, 'viirs', 2, %s)",
                 (f"alerte:{alerte}:{etat}", did, alerte))
    return did


def test_old_rules_raised_three_alerts(conn):
    c = _cas(conn)
    rows = conn.execute("SELECT status, severity FROM alerts WHERE id = ANY(%s)", (list(c["alertes"].values()),)).fetchall()
    assert len(rows) == 3 and all(st == "nouvelle" and sev != "faible" for st, sev in rows)


def test_plan_classifies_explains_and_keeps_the_true_candidate(conn):
    c = _cas(conn)
    p = viirs.refusion_plan(conn, RULES, date(2026, 10, 16), date(2026, 10, 16))
    act = {a["alerte"]: a for a in p["alertes"]}
    m = act[c["alertes"]["mouillage"]]
    assert m["action"] == "classer" and m["navire"]["name"] == "MOUILLE ESSAI" and m["navire"]["methode"] == "arret"
    assert m["navire"]["age_min"] == 40 and "MOUILLE ESSAI" in m["motif"]
    d = act[c["alertes"]["doute"]]
    assert d["action"] == "faible" and d["navire_compatible"]["vessel_id"] == c["vessels"]["route"]
    assert act[c["alertes"]["sombre"]]["action"] == "garder"
    assert p["appariees_avant"] == 0 and p["appariees_apres"] == 1
    assert sum(p["ouvertes_avant"].values()) == 3 and p["ouvertes_apres"].get("faible", 0) >= 1
    assert sum(p["ouvertes_apres"].values()) == 2                         # une classée, deux ouvertes


def test_apply_updates_in_place_closes_the_request_and_never_deletes(conn):
    c = _cas(conn)
    active = _demande(conn, c["alertes"]["mouillage"], "en_attente")
    analysee = _demande(conn, c["alertes"]["mouillage"], "analysee")
    p = viirs.refusion_plan(conn, RULES)
    r = viirs.refusion_appliquer(conn, p, RULES)
    assert r["classees"] == [c["alertes"]["mouillage"]] and r["faibles"] == [c["alertes"]["doute"]]
    assert r["demandes_annulees"] == [active] and r["demandes_notees"] == [analysee]
    st, details = conn.execute("SELECT status, details FROM alerts WHERE id = %s", (c["alertes"]["mouillage"],)).fetchone()
    assert st == "classee" and details["navire_apparie"]["name"] == "MOUILLE ESSAI"
    assert conn.execute("SELECT action, motif, author FROM alert_actions WHERE alert_id = %s",
                        (c["alertes"]["mouillage"],)).fetchone() == ("classer", "faux_positif", "MARS C2")
    assert conn.execute("SELECT matched_vessel_id FROM viirs_detections WHERE id = %s",
                        (c["dets"]["mouillage"],)).fetchone()[0] == c["vessels"]["mouillage"]
    sev, st_d, det_d = conn.execute("SELECT severity, status, details FROM alerts WHERE id = %s",
                                    (c["alertes"]["doute"],)).fetchone()
    assert sev == "faible" and st_d == "nouvelle" and det_d["a_confirmer"] is True
    assert det_d["contexte"][0].startswith("Navire AIS possiblement compatible : EN ROUTE ESSAI")
    assert conn.execute("SELECT status, severity FROM alerts WHERE id = %s",
                        (c["alertes"]["sombre"],)).fetchone()[0] == "nouvelle"
    assert conn.execute("SELECT count(*) FROM alerts WHERE type = 'DARK_SHIP'").fetchone()[0] == 3     # rien d'effacé
    e, motif = conn.execute("SELECT etat, motif FROM demandes_observation WHERE id = %s", (active,)).fetchone()
    assert e == "annulee" and "MOUILLE ESSAI" in motif
    e2, rd = conn.execute("SELECT etat, resultat_details FROM demandes_observation WHERE id = %s", (analysee,)).fetchone()
    assert e2 == "analysee" and "MOUILLE ESSAI" in rd["refusion_viirs"]
    # Seconde refusion : rien de plus
    r2 = viirs.refusion_appliquer(conn, viirs.refusion_plan(conn, RULES), RULES)
    assert r2["classees"] == [] and r2["appariements_changes"] == 0
    assert conn.execute("SELECT count(*) FROM alert_actions WHERE alert_id = %s",
                        (c["alertes"]["mouillage"],)).fetchone()[0] == 1


@pytest.mark.parametrize("statut", ["classee", "confirmee", "acquittee"])
def test_operator_decision_is_kept(conn, statut):
    c = _cas(conn)
    a = c["alertes"]["mouillage"]
    conn.execute("UPDATE alerts SET status = %s WHERE id = %s", (statut, a))
    r = viirs.refusion_appliquer(conn, viirs.refusion_plan(conn, RULES), RULES)
    assert r["decisions_gardees"] == [a] and r["classees"] == []
    st, details = conn.execute("SELECT status, details FROM alerts WHERE id = %s", (a,)).fetchone()
    assert st == statut and details["refusion"]["decision_gardee"] == statut
    assert conn.execute("SELECT count(*) FROM alert_actions WHERE alert_id = %s", (a,)).fetchone()[0] == 0


def _args(**kw):
    a = argparse.Namespace(args=[], nuit=None, du=None, au=None, toutes=False, simulation=False)
    a.__dict__.update(kw)
    return a


def test_simulation_writes_nothing_and_lists_the_changes(conn, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import taches
    c = _cas(conn)
    with pytest.raises(SystemExit, match="Préciser la portée"):
        taches._viirs_refusionner(conn, _args())
    taches._viirs_refusionner(conn, _args(toutes=True, simulation=True))
    out = capsys.readouterr().out
    assert "Classées en faux positif (navire qui les explique) : 1" in out and "MOUILLE ESSAI" in out
    assert "Abaissées en faible, à confirmer" in out and "Restent ouvertes, vrais candidats" in out
    assert "SIMULATION : rien n'a été écrit" in out
    assert conn.execute("SELECT count(*) FROM alerts WHERE id = ANY(%s) AND status = 'nouvelle'",
                        (list(c["alertes"].values()),)).fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM alert_actions").fetchone()[0] == 0
    assert conn.execute("SELECT matched_vessel_id FROM viirs_detections WHERE id = %s",
                        (c["dets"]["mouillage"],)).fetchone()[0] is None
