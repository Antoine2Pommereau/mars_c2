"""Réévaluation VIIRS (26/10/2026, règles 2026.10.58). Après la reconstruction de la zone de réception, viirs-reevaluer
avait relevé l'alerte 50277 (abaissée par la refusion : ATHENA SEAWAYS compatible) et levé 9 alertes élevées sur les
feux du parc d'Yeu et Noirmoutier. Désormais : fusion actuelle appliquée (refusion), abaissements gardés, prudence
cherchée jusqu'à 100 km, lumières d'un parc en service écartées, lumière fixe dès 2 nuits, parcs éoliens hors du
corridor, simulation sans écriture. En base : MARS_TEST_DATABASE_URL."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from mars import viirs
from mars.config import load_rules

RULES = load_rules()
URL = os.environ.get("MARS_TEST_DATABASE_URL")
T1 = datetime(2026, 10, 7, 2, 0, tzinfo=timezone.utc)            # nuit du 06/10
T2 = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)           # nuit du 14/10, 8 jours plus tard
YEU = (-2.50, 46.88)                                             # dans le parc d'Yeu et Noirmoutier
FGL = (0.12, 50.09)                                              # parc en projet de Fécamp Grand Large (80241)
FIXE = (-3.30, 47.00)                                            # au large, hors de tout parc
ATHENA = (-1.00, 49.90)                                          # lumière de l'alerte 50277 (cas reconstruit)
MMSI = (273999301, 273999302)


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
                "DELETE FROM viirs_granules", "DELETE FROM viirs_lumieres_fixes", "DELETE FROM reception_cells",
                "DELETE FROM stats_minute"):
        conn.execute(sql)
    conn.execute("DELETE FROM positions WHERE vessel_id IN (SELECT id FROM vessels WHERE mmsi = ANY(%s))", (list(MMSI),))
    conn.execute("DELETE FROM vessels WHERE mmsi = ANY(%s)", (list(MMSI),))
    athena = conn.execute("INSERT INTO vessels (mmsi, name, length_m, ais_class) VALUES (%s, 'ATHENA SEAWAYS', 180, "
                          "'A') RETURNING id", (MMSI[0],)).fetchone()[0]
    voisin = conn.execute("INSERT INTO vessels (mmsi, name, length_m, ais_class) VALUES (%s, 'VOISIN ESSAI', 90, 'A') "
                          "RETURNING id", (MMSI[1],)).fetchone()[0]
    # ATHENA SEAWAYS vu 60 min avant la lumière, à 37 km au sud, cap au nord à 20 nœuds : hors de l'ancienne boîte de
    # 0,2 degré en latitude, l'estime le place sur la lumière
    conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn, cog_deg) VALUES (%s, %s, "
                 "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 20, 0)",
                 (athena, T2 - timedelta(minutes=60), ATHENA[0], ATHENA[1] - 20 * 1852 / 110_574))
    # Réception AIS alentour de chaque lumière (évaluable), à 10 km, à l'heure du passage
    for t in (T1, T2):
        for lon, lat in (YEU, FGL, FIXE, ATHENA):
            conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn) VALUES (%s, %s, "
                         "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 0)", (voisin, t, lon + 0.13, lat))
    gr = {}
    for t, nuit in ((T1, "2026-10-06"), (T2, "2026-10-14")):
        gr[t] = conn.execute("INSERT INTO viirs_granules (nom, satellite, nuit, debut, fin, lune) VALUES (%s, 'NOAA20', "
                             "%s, %s, %s, 10) RETURNING id",
                             (f"NOAA20.ESSAI.{nuit}", nuit, t - timedelta(minutes=3), t + timedelta(minutes=3))).fetchone()[0]
    dets = {}
    for k, t, (lon, lat) in (("yeu1", T1, YEU), ("yeu2", T2, (YEU[0] - 0.003, YEU[1] + 0.001)), ("fgl", T1, FGL),
                             ("fixe1", T1, FIXE), ("fixe2", T2, (FIXE[0] + 0.003, FIXE[1])), ("athena", T2, ATHENA)):
        dets[k] = conn.execute("INSERT INTO viirs_detections (granule_id, ts, geom, nanowatts, lune, distance_cote_m, "
                               "ais_navires_rayon) VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, "
                               "20, 10, 30000, 1) RETURNING id", (gr[t], t, lon, lat)).fetchone()[0]
    # État laissé par les règles d'avant : alertes levées sans masque ni lumière fixe à 2 nuits
    avant = {**RULES, "viirs": {**RULES["viirs"], "parcs_en_service": [], "persistance_nuits": 3}}
    viirs._alerts(conn, [dets[k] for k in ("yeu1", "yeu2", "fgl", "fixe1", "fixe2")], avant)
    alertes = {k: conn.execute("SELECT a.id FROM alerts a JOIN alert_evidence e ON e.alert_id = a.id AND "
                               "e.evidence_type = 'viirs' AND e.evidence_id = %s", (dets[k],)).fetchone()[0]
               for k in ("yeu1", "yeu2", "fgl", "fixe1", "fixe2")}
    # 50277 : abaissée par la refusion du 25/10 (navire compatible), toujours nouvelle
    nom = "NOAA20.ESSAI.2026-10-14"
    alertes["athena"] = conn.execute(
        """INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key)
           VALUES ('DARK_SHIP', 'faible', %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, '2026.10.56', %s)
           RETURNING id""",
        (T2, ATHENA[0], ATHENA[1], json.dumps({
            "source": "viirs", "nuit": "2026-10-14", "a_confirmer": True,
            "navire_compatible": {"vessel_id": athena, "name": "ATHENA SEAWAYS", "age_min": 60.0},
            "refusion": {"version_regles": "2026.10.56", "motif": "Navire AIS possiblement compatible"},
            "contexte": ["Navire AIS possiblement compatible : ATHENA SEAWAYS"]}),
         f"viirs:{nom}:{ATHENA[0]:.4f}:{ATHENA[1]:.4f}")).fetchone()[0]
    conn.execute("INSERT INTO alert_evidence VALUES (%s, 'viirs', %s)", (alertes["athena"], dets["athena"]))
    return {"dets": dets, "alertes": alertes, "athena": athena}


def _alerte(conn, aid):
    return conn.execute("SELECT severity, status, details FROM alerts WHERE id = %s", (aid,)).fetchone()


def test_previous_rules_raised_the_wind_farm_lights_high(conn):
    c = _cas(conn)
    sev, st, d = _alerte(conn, c["alertes"]["yeu1"])
    assert st == "nouvelle" and sev == "elevee" and d["infrastructure"]      # un câble du parc passe tout près


def test_prudence_now_reaches_a_ship_seen_37_km_away():
    """La boîte de 0,3 × 0,2 degré perdait le navire ; la portée de 100 km le retrouve."""
    dlon, dlat = viirs._portee_deg(ATHENA[1], RULES["viirs"]["doute_portee_km"])
    assert dlat > 20 * 1852 / 110_574 > 0.2 and dlon > 0.3


def test_reevaluation_keeps_50277_lowered_masks_farms_and_fixed_lights(conn):
    c = _cas(conn)
    res = viirs.reevaluate(conn, RULES)
    a = c["alertes"]
    # 50277 : reste faible et nouvelle, avec son navire compatible
    sev, st, d = _alerte(conn, a["athena"])
    assert (sev, st) == ("faible", "nouvelle") and d["navire_compatible"]["name"] == "ATHENA SEAWAYS"
    assert d["refusion"]["version_regles"] == RULES["version"]          # la fusion actuelle le retrouve
    # Parc d'Yeu et Noirmoutier, en service : lumières écartées, alertes classées par MARS C2 avec le motif
    for k in ("yeu1", "yeu2"):
        sev, st, d = _alerte(conn, a[k])
        assert st == "classee" and "Iles d'Yeu et de Noirmoutier" in d["ecartee"]
        assert conn.execute("SELECT mask_reason FROM viirs_detections WHERE id = %s",
                            (c["dets"][k],)).fetchone()[0] == "eolienne"
        assert conn.execute("SELECT author, motif FROM alert_actions WHERE alert_id = %s",
                            (a[k],)).fetchone() == ("MARS C2", "faux_positif")
    # Lumière fixe revue 2 nuits, 8 jours d'écart, sans AIS : écartée, alertes classées
    for k in ("fixe1", "fixe2"):
        sev, st, d = _alerte(conn, a[k])
        assert st == "classee" and d["ecartee"].startswith("Lumière fixe : revue 2 nuits")
    # Parc en projet (Fécamp Grand Large) : pas masqué, et plus « près d'une infrastructure » à cause du parc
    sev, st, d = _alerte(conn, a["fgl"])
    assert st == "nouvelle" and "ecartee" not in d
    assert d["infrastructure"] is None or d["infrastructure"]["type"] != "Parc éolien"
    assert res["eoliennes"] == 2 and res["lumieres_fixes"] >= 1
    assert {x["alerte"] for x in res["changements"] if x["changement"] == "classee"} == \
        {a["yeu1"], a["yeu2"], a["fixe1"], a["fixe2"]}
    assert not [x for x in res["changements"] if x["changement"] == "relevee"]
    # Une seconde réévaluation ne change plus rien
    assert viirs.reevaluate(conn, RULES)["changements"] == []


def test_lowered_alert_never_rises_even_without_the_ship(conn):
    """Abaissement du système gardé même si le navire compatible n'est plus retrouvé (données purgées)."""
    c = _cas(conn)
    conn.execute("DELETE FROM positions WHERE vessel_id = %s", (c["athena"],))
    viirs.reevaluate(conn, RULES)
    sev, st, d = _alerte(conn, c["alertes"]["athena"])
    assert (sev, st) == ("faible", "nouvelle") and d["navire_compatible"]["name"] == "ATHENA SEAWAYS"


def test_simulation_lists_the_changes_and_writes_nothing(conn):
    c = _cas(conn)
    etat = conn.execute("SELECT id, severity, status, details FROM alerts ORDER BY id").fetchall()
    masques = conn.execute("SELECT id, mask_reason FROM viirs_detections ORDER BY id").fetchall()
    res = viirs.reevaluate(conn, RULES, simulation=True)
    assert "simulation" in res
    assert {x["alerte"] for x in res["changements"] if x["changement"] == "classee"} == \
        {c["alertes"][k] for k in ("yeu1", "yeu2", "fixe1", "fixe2")}
    assert any("parc éolien" in (x.get("motif") or "") for x in res["changements"])
    assert conn.execute("SELECT id, severity, status, details FROM alerts ORDER BY id").fetchall() == etat
    assert conn.execute("SELECT id, mask_reason FROM viirs_detections ORDER BY id").fetchall() == masques
    assert conn.execute("SELECT count(*) FROM alert_actions").fetchone()[0] == 0
