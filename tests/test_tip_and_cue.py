"""Tip and cue (lot T1) : un signal suspect crée une demande d'observation satellite. Calculs purs (estime, cercle,
fenêtres, résultat, gravité, rejeu), puis sur la base de test (MARS_TEST_DATABASE_URL) : tips relevés et regroupés,
passage rétroactif ou futur, point trop près de la côte, expiration, priorité dans le plafond, refus de budget,
résultat rattaché à l'alerte et gravité ajustée, demande manuelle par l'API."""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mars import tip_and_cue as tc
from mars.config import load_rules

VERSIONNEES = load_rules()
RULES = {**VERSIONNEES, "tip_and_cue": {**VERSIONNEES["tip_and_cue"], "actif": True}}
COUPE = {**VERSIONNEES, "tip_and_cue": {**VERSIONNEES["tip_and_cue"], "actif": False}}
P, S = RULES["tip_and_cue"]["prediction"], RULES["sentinel"]
URL = os.environ.get("MARS_TEST_DATABASE_URL")
NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
LARGE = (-6.1, 48.01)                          # au large d'Ouessant, à plus de 50 km de la côte
QUAI = (5.341, 43.333)                         # bassins nord de Marseille, à 92 m de la côte
EMPRISE = {"type": "Polygon", "coordinates": [[[-7.0, 47.5], [-5.0, 47.5], [-5.0, 48.8], [-7.0, 48.8], [-7.0, 47.5]]]}
MARSEILLE = {"type": "Polygon", "coordinates": [[[4.8, 43.0], [5.8, 43.0], [5.8, 43.6], [4.8, 43.6], [4.8, 43.0]]]}


def test_versioned_settings_of_the_activation():
    """Activé le 20/10/2026 (règles 2026.10.32) : watchlist coupé, VIIRS à 12 h au plus, budget de 8 € ; estime d'un
    navire muet bornée et pêcheurs écartés des coupures (2026.10.33)."""
    t = VERSIONNEES["tip_and_cue"]
    assert t["actif"] is True and t["tips"]["watchlist"]["actif"] is False and t["tips"]["viirs"]["delai_max_h"] == 12
    assert all(t["tips"][k]["actif"] for k in ("coupure_ais", "viirs", "rendezvous", "identite", "operateur"))
    assert t["tips"]["coupure_ais"]["types_exclus"] == [30] and t["tips"]["coupure_ais"]["exclure_declassees"] is True
    assert VERSIONNEES["sentinel"]["budget_mensuel_eur"] == 8.0 and VERSIONNEES["sentinel"]["scenes_max_par_jour"] == 2
    assert tc.cycle(None, COUPE, NOW) == {"inactif": True} and tc.points_passage(None, COUPE, 1) == []


# Calculs purs

def test_dead_reckoning_and_growing_circle():
    """Ancien cercle (2 km plus 30 % du parcours, borné), gardé en repli quand la table du cercle appris manque."""
    ancien = {k: v for k, v in P.items() if k != "cercle_appris"}
    pr = tc.predire(*LARGE, NOW, 10.0, 90.0, NOW + timedelta(hours=3), ancien)
    assert pr["mode"] == "estime"
    assert tc.distance_m(*LARGE, pr["lon"], pr["lat"]) == pytest.approx(3 * 10 * 1852, rel=1e-3)
    assert pr["lon"] > LARGE[0] and pr["lat"] == pytest.approx(LARGE[1], abs=0.01)          # plein est
    assert pr["rayon_m"] == pytest.approx(P["rayon_depart_m"] + P["part_vitesse_par_h"] * 3 * 10 * 1852)
    loin = tc.predire(*LARGE, NOW, 15.0, 0.0, NOW + timedelta(hours=30), ancien)
    assert loin["rayon_m"] == P["rayon_max_m"]                                             # borné


def test_learned_circle_by_speed_band_and_horizon():
    """Cercle appris (règles 2026.10.44) : rayon de la tranche de vitesse, interpolé entre les horizons mesurés."""
    c = {"vitesses_kn": [1, 5, 10, 15], "horizons_h": [1, 2, 3], "rayon_min_m": 1000,
         "rayons_m": [[3000, 6000, 9000], [4000, 8000, 12000], [5000, 10000, 15000], [6000, 12000, 18000]]}
    assert tc.rayon_appris(c, 12.0, 3) == 15000
    assert tc.rayon_appris(c, 12.0, 2.5) == pytest.approx(12500)
    assert tc.rayon_appris(c, 12.0, 0.5) == pytest.approx(3000), "de 1 km à 0 h vers le rayon de la première heure"
    assert tc.rayon_appris(c, 20.0, 10) == 18000, "au delà du dernier horizon : le dernier rayon"
    assert tc.rayon_appris(c, 0.5, 1) == 3000, "sous la première borne : première tranche"
    pr = tc.predire(*LARGE, NOW, 10.0, 90.0, NOW + timedelta(hours=3), P)
    assert pr["rayon_m"] == pytest.approx(tc.rayon_appris(P["cercle_appris"], 10.0, 3.0))


def test_learned_circle_is_capped_to_the_allowed_windows():
    """Un cercle appris trop grand pour 4 fenêtres est ramené au rayon qu'elles couvrent, au lieu d'abandonner."""
    tcr = RULES["tip_and_cue"]
    assert tc.rayon_couvrable(S, tcr["fenetres_max_par_demande"]) == pytest.approx(20000, abs=10)
    pr = tc.predire(*LARGE, NOW, 15.0, 90.0, NOW + timedelta(hours=3), P)
    assert pr["rayon_m"] > 40000
    centres = tc.fenetres_demande(pr, S, tcr)
    assert len(centres) <= tcr["fenetres_max_par_demande"]
    assert pr["rayon_m"] == pytest.approx(20000, abs=10) and pr["rayon_appris_m"] > 40000
    arret = {"lon": LARGE[0], "lat": LARGE[1], "rayon_m": 60000.0, "mode": "arret"}
    assert len(tc.fenetres_demande(arret, S, tcr)) > 4 and arret["rayon_m"] == 60000.0, "seule l'estime est plafonnée"
    sans = {**tcr, "prediction": {**P, "cercle_appris": {**P["cercle_appris"], "plafonner": False}}}
    pr2 = tc.predire(*LARGE, NOW, 15.0, 90.0, NOW + timedelta(hours=3), P)
    assert len(tc.fenetres_demande(pr2, S, sans)) > 4


def test_stopped_or_anchored_vessel_and_point_without_vessel():
    for kw in ({"sog": 0.3, "cog": 120.0, "a_l_arret": False}, {"sog": 12.0, "cog": 120.0, "a_l_arret": True},
               {"sog": None, "cog": None, "a_l_arret": False}):
        pr = tc.predire(*LARGE, NOW, kw["sog"], kw["cog"], NOW + timedelta(hours=10), P, a_l_arret=kw["a_l_arret"])
        assert (pr["lon"], pr["lat"], pr["rayon_m"], pr["mode"]) == (*LARGE, P["rayon_arret_m"], "arret")
    pr = tc.predire(*LARGE, NOW, None, None, NOW + timedelta(hours=10), P, navire=False)
    assert (pr["rayon_m"], pr["mode"]) == (P["rayon_point_m"], "point")


def test_mute_vessel_estimate_is_bounded_and_slow_mute_vessel_stays_put():
    """Navire muet (dernière position il y a 3 h) : en route, estime valable 6 h et dans nos zones seulement ; lent
    (moins de 3 nœuds), position fixe et petit cercle ; navire qui émet encore : estime sans borne."""
    t0 = NOW - timedelta(hours=3)
    assert P["muet_min"] == 30 and P["lent_kn"] == 3.0 and P["estime_max_h"] == 6
    loin = tc.predire_bornee((*LARGE, t0, 12.0, 0.0), NOW + timedelta(hours=4), NOW, P)
    assert "au delà des 6 h de l'estime" in loin["hors_portee"]                           # 7 h après la position
    dehors = tc.predire_bornee((*LARGE, t0, 12.0, 270.0), NOW + timedelta(hours=2), NOW, P)
    assert "hors de nos zones collectées" in dehors["hors_portee"]                       # 110 km à l'ouest
    assert not tc.dans_zones(dehors["lon"], dehors["lat"]) and tc.dans_zones(*LARGE)
    dedans = tc.predire_bornee((*LARGE, t0, 12.0, 90.0), NOW + timedelta(hours=2), NOW, P)
    assert dedans["mode"] == "estime" and "hors_portee" not in dedans
    lent = tc.predire_bornee((*LARGE, t0, 2.5, 270.0), NOW + timedelta(hours=20), NOW, P)
    assert (lent["lon"], lent["lat"], lent["rayon_m"], lent["mode"]) == (*LARGE, P["rayon_arret_m"], "arret")
    emet = tc.predire_bornee((*LARGE, NOW - timedelta(minutes=5), 12.0, 270.0), NOW + timedelta(hours=10), NOW, P)
    assert emet["mode"] == "estime" and "hors_portee" not in emet                         # replanifié à chaque cycle
    r = tc.rejeu([{"tip": "coupure_ais", "lon": LARGE[0], "lat": LARGE[1], "t": t0, "connu": NOW, "vessel_id": 7,
                   "sog": 12.0, "cog": 270.0}], [{"id": 1, "t": NOW + timedelta(hours=2), "emprise": EMPRISE}], RULES,
                 lambda _x, _y: None)
    assert r["par_tip"]["coupure_ais"]["transit"] == 1 and r["sessions"] == 0


def test_circle_becomes_24_km_windows():
    assert len(tc.centres_fenetres(*LARGE, 5000, S)) == 1
    assert len(tc.centres_fenetres(*LARGE, 18000, S)) == 4
    assert len(tc.centres_fenetres(*LARGE, 30000, S)) == 9          # au delà de fenetres_max_par_demande (4)
    # chaque point du cercle est dans le cœur d'une fenêtre (24 km moins la marge)
    centres = tc.centres_fenetres(*LARGE, 18000, S)
    for cap in range(0, 360, 15):
        lon, lat = tc.destination(*LARGE, cap, 17500)
        assert min(max(abs(lon - c[0]) * 111_320 * 0.668, abs(lat - c[1]) * 110_574) for c in centres) <= 10_050


def test_result_categories():
    apparie = {"id": 1, "matched_vessel_id": 7, "mask_reason": None}
    muet = {"id": 2, "matched_vessel_id": None, "mask_reason": None}
    terre = {"id": 3, "matched_vessel_id": None, "mask_reason": "terre"}
    autre = {"id": 4, "matched_vessel_id": 9, "mask_reason": None}
    assert tc.categorie([muet, apparie], {7}, True) == ("present_apparie", apparie)
    assert tc.categorie([terre, muet], {7}, True) == ("present_sans_ais", muet)
    assert tc.categorie([terre], {7}, True) == ("absent", None)
    assert tc.categorie([autre], {7}, True) == ("absent", None)                 # un autre navire, pas le nôtre
    assert tc.categorie([autre], set(), False)[0] == "present_apparie"         # lumière VIIRS : un navire AIS
    assert tc.categorie([{"id": 5, "matched_vessel_id": None, "mask_reason": "non_evaluable"}], set(), False)[0] == \
        "non_evaluable"


def test_severity_table_raises_or_lowers_never_removes():
    t = RULES["tip_and_cue"]["ajustements"]
    assert tc.ajustement("coupure_ais", "present_sans_ais", "moyenne", t) == "elevee"
    assert tc.ajustement("viirs", "present_apparie", "elevee", t) == "faible"
    assert tc.ajustement("viirs", "absent", "elevee", t) is None
    assert tc.ajustement("coupure_ais", "present_sans_ais", "elevee", t) is None          # déjà à ce niveau


def test_replay_groups_finds_passes_and_excludes_the_coast():
    passages = [{"id": 1, "t": NOW + timedelta(hours=5), "emprise": EMPRISE},
                {"id": 2, "t": NOW + timedelta(hours=8), "emprise": MARSEILLE}]
    tips = [{"tip": "viirs", "lon": LARGE[0], "lat": LARGE[1], "t": NOW, "vessel_id": None},
            {"tip": "identite", "lon": QUAI[0], "lat": QUAI[1], "t": NOW, "vessel_id": 11, "sog": 0, "cog": 0},
            {"tip": "watchlist", "lon": QUAI[0], "lat": QUAI[1], "t": NOW + timedelta(hours=1), "vessel_id": 11},
            {"tip": "rendezvous", "lon": 0.0, "lat": 60.0, "t": NOW, "vessel_id": 12}]
    r = tc.rejeu(tips, passages, RULES, lambda lon, _lat: 90.0 if lon > 5 else None)
    issues = {(x["tip"], x["issue"]) for x in r["lignes"]}
    assert issues == {("viirs", "passage"), ("identite", "cote"), ("watchlist", "regroupee"),
                      ("rendezvous", "sans_passage_36h")}
    assert r["sessions"] == 1 and r["fenetres"] == 1 and r["par_tip"]["watchlist"]["regroupees"] == 1


# Sur la base de test

@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL)
    # Transaction annulée en fin de test : les alertes laissées par d'autres essais (heure réelle) sont écartées
    for sql in ("DELETE FROM demandes_origines", "DELETE FROM demandes_observation",
                "DELETE FROM sar_passes WHERE product_name LIKE 'TIP_%'", "DELETE FROM alerts"):
        c.execute(sql)
    yield c
    c.rollback()
    c.close()


def _passage(conn, nom, t, emprise=EMPRISE, statut="prevu"):
    return conn.execute(
        "INSERT INTO sar_passes (product_name, platform, acquired_at, ended_at, footprint, mission, satellite, mode, "
        "statut) VALUES (%s, 'sentinel-1c', %s, %s, ST_GeomFromGeoJSON(%s)::geography, 'S1', 'S1C', 'IW', %s) "
        "RETURNING id", (nom, t, t + timedelta(minutes=1), json.dumps(emprise), statut)).fetchone()[0]


def _alerte(conn, typ, sev, lon, lat, details, vessel=None, t=NOW):
    aid = conn.execute(
        "INSERT INTO alerts (type, severity, event_time, detected_at, geom, details, rule_version) VALUES (%s, %s, %s, "
        "%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, 'essai') RETURNING id",
        (typ, sev, t, t, lon, lat, json.dumps(details))).fetchone()[0]
    if vessel:
        conn.execute("INSERT INTO alert_evidence VALUES (%s, 'vessel', %s)", (aid, vessel))
    return aid


def _navire(conn, mmsi, lon, lat, sog=10.0, cog=90.0, t=NOW - timedelta(minutes=10)):
    conn.execute("DELETE FROM positions WHERE vessel_id IN (SELECT id FROM vessels WHERE mmsi = %s)", (mmsi,))
    conn.execute("DELETE FROM vessels WHERE mmsi = %s", (mmsi,))
    vid = conn.execute("INSERT INTO vessels (mmsi, name, ais_class) VALUES (%s, %s, 'A') RETURNING id",
                       (mmsi, f"ESSAI {mmsi}")).fetchone()[0]
    conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn, cog_deg) VALUES (%s, %s, "
                 "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s)", (vid, t, lon, lat, sog, cog))
    return vid


def _demande(conn, did):
    return dict(zip(("etat", "pass_id", "retro", "rayon", "prediction", "fenetres", "motif", "priorite"), conn.execute(
        "SELECT etat, pass_id, passage_retro, rayon_m, prediction, fenetres, motif, priorite FROM demandes_observation "
        "WHERE id = %s", (did,)).fetchone()))


def test_viirs_tip_is_planned_on_the_next_covering_pass(conn):
    _passage(conn, "TIP_AILLEURS", NOW + timedelta(hours=2), MARSEILLE)
    pid = _passage(conn, "TIP_FUTUR", NOW + timedelta(hours=7))
    aid = _alerte(conn, "DARK_SHIP", "moyenne", *LARGE, {"source": "viirs", "detection_id": 1, "trace_id_creation": "a" * 32})
    _alerte(conn, "DARK_SHIP", "faible", -6.0, 48.0, {"source": "viirs"})               # sous la gravité minimale
    out = tc.cycle(conn, RULES, NOW)
    assert out["tips"] == {"viirs": 1} and out["planifiees"] == 1
    did = conn.execute("SELECT demande_id FROM demandes_origines WHERE alerte_id = %s", (aid,)).fetchone()[0]
    d = _demande(conn, did)
    assert d["etat"] == "planifiee" and d["pass_id"] == pid and d["retro"] is False
    assert d["prediction"] == "point" and d["rayon"] == P["rayon_point_m"] and len(d["fenetres"]) == 1
    assert tc.cycle(conn, RULES, NOW)["tips"] == {"viirs": 1} and \
        conn.execute("SELECT count(*) FROM demandes_observation").fetchone()[0] == 1       # clé déjà vue
    pts = tc.points_passage(conn, RULES, pid)
    assert pts == [{"motif": "tip_and_cue", "tip": "viirs", "demande": did, "priorite": 4, "lon": LARGE[0],
                    "lat": LARGE[1]}]


def test_recent_acquired_pass_is_used_at_once_and_vessel_tips_are_grouped(conn):
    pid = _passage(conn, "TIP_RETRO", NOW - timedelta(hours=2), statut="acquis")
    vid = _navire(conn, 636999701, *LARGE, sog=8.0, cog=0.0, t=NOW - timedelta(hours=4))
    a1 = _alerte(conn, "IDENTITY_CHANGE", "moyenne", *LARGE, {"navire": {"vessel_id": vid}}, vid)
    a2 = _alerte(conn, "RENDEZVOUS", "moyenne", *LARGE, {"navires": [{"vessel_id": vid}], "distance_cote_km": 40}, vid)
    out = tc.cycle(conn, RULES, NOW)
    assert out["nouvelles"] == 1 and out["regroupees"] == 1
    did = conn.execute("SELECT id FROM demandes_observation").fetchone()[0]
    assert {r[0] for r in conn.execute("SELECT alerte_id FROM demandes_origines WHERE demande_id = %s", (did,))} == {a1, a2}
    d = _demande(conn, did)
    assert d["etat"] == "planifiee" and d["pass_id"] == pid and d["retro"] is True and d["priorite"] == 4
    assert d["prediction"] == "estime" and d["rayon"] == pytest.approx(tc.rayon_appris(P["cercle_appris"], 8.0, 2.0), rel=0.02)


def test_point_too_close_to_the_coast_has_no_useful_pass(conn):
    _passage(conn, "TIP_MARSEILLE", NOW + timedelta(hours=3), MARSEILLE)
    vid = _navire(conn, 636999702, *QUAI, sog=0.0)
    _alerte(conn, "IDENTITY_CHANGE", "elevee", *QUAI, {"navire": {"vessel_id": vid}}, vid)
    tc.cycle(conn, RULES, NOW)
    d = _demande(conn, conn.execute("SELECT id FROM demandes_observation").fetchone()[0])
    assert d["etat"] == "sans_passage" and "invisible au radar" in d["motif"] and "92 m" in d["motif"]


def test_viirs_light_older_than_12_hours_at_the_pass_is_dropped_with_its_reason(conn):
    _passage(conn, "TIP_TARDIF", NOW + timedelta(hours=14))
    _alerte(conn, "DARK_SHIP", "elevee", *LARGE, {"source": "viirs"})
    tc.cycle(conn, RULES, NOW)
    d = _demande(conn, conn.execute("SELECT id FROM demandes_observation").fetchone()[0])
    assert d["etat"] == "sans_passage" and "plus de 12 h après le signal" in d["motif"]
    r = tc.rejeu([{"tip": "viirs", "lon": LARGE[0], "lat": LARGE[1], "t": NOW, "vessel_id": None}],
                 [{"id": 1, "t": NOW + timedelta(hours=14), "emprise": EMPRISE}], RULES, lambda _x, _y: None)
    assert r["par_tip"]["viirs"]["delai"] == 1 and r["sessions"] == 0


def test_waiting_request_expires_after_36_hours(conn):
    _alerte(conn, "DARK_SHIP", "elevee", *LARGE, {"source": "viirs"})
    tc.cycle(conn, RULES, NOW)
    did = conn.execute("SELECT id FROM demandes_observation").fetchone()[0]
    d = _demande(conn, did)
    assert d["etat"] == "en_attente" and d["motif"].startswith("aucun passage Sentinel 1 prévu au calendrier")
    tc.planifier(conn, RULES, NOW + timedelta(hours=37))
    d = _demande(conn, did)
    assert d["etat"] == "expiree" and "36 h" in d["motif"]


@pytest.mark.parametrize("cog, heures, attendu", [
    (270.0, 2, "hors de nos zones collectées"),             # route vers l'ouest : sort de la zone Bretagne
    (90.0, 5, "au delà des 6 h de l'estime"),                # passage 8 h après la dernière position
])
def test_mute_fast_vessel_in_transit_has_no_pass_and_says_why(conn, cog, heures, attendu):
    _passage(conn, "TIP_TRANSIT", NOW + timedelta(hours=heures))
    vid = _navire(conn, 636999711, *LARGE, sog=12.0, cog=cog, t=NOW - timedelta(hours=3))
    r = tc.creer(conn, RULES, NOW, cle="coupure:essai", tip="coupure_ais", lon=LARGE[0], lat=LARGE[1],
                 position_le=NOW - timedelta(hours=3), vessel_id=vid, sog=12.0, cog=cog, motif="coupure observée")
    out = tc.planifier(conn, RULES, NOW)
    d = _demande(conn, r["demande"])
    assert out["sans_passage"] == 1 and d["etat"] == "sans_passage"
    assert d["motif"].startswith("navire en transit, hors de portée au prochain passage") and attendu in d["motif"]


def test_mute_slow_vessel_keeps_its_position_and_is_planned(conn):
    pid = _passage(conn, "TIP_LENT", NOW + timedelta(hours=9))
    vid = _navire(conn, 636999712, *LARGE, sog=2.0, cog=270.0, t=NOW - timedelta(hours=3))
    r = tc.creer(conn, RULES, NOW, cle="coupure:lent", tip="coupure_ais", lon=LARGE[0], lat=LARGE[1],
                 position_le=NOW - timedelta(hours=3), vessel_id=vid, sog=2.0, cog=270.0, motif="coupure observée")
    tc.planifier(conn, RULES, NOW)
    d = _demande(conn, r["demande"])
    assert d["etat"] == "planifiee" and d["pass_id"] == pid
    assert d["prediction"] == "arret" and d["rayon"] == P["rayon_arret_m"] and len(d["fenetres"]) == 1


def test_fishing_vessel_and_downgraded_gap_are_dropped_with_their_reason(conn, monkeypatch):
    monkeypatch.setattr(tc, "_pres_infrastructure", lambda *_a: True)
    peche = _navire(conn, 636999713, *LARGE, sog=4.0, t=NOW - timedelta(hours=3))
    conn.execute("UPDATE vessels SET ship_type_code = 30 WHERE id = %s", (peche,))
    sortie = _navire(conn, 636999714, *LARGE, sog=11.0, t=NOW - timedelta(hours=3))
    cargo = _navire(conn, 636999715, -5.5, 48.2, sog=0.5, t=NOW - timedelta(hours=3))
    ep = {"dernier_message": (NOW - timedelta(hours=3)).isoformat(), "derniere_position": list(LARGE), "gravite": "elevee"}
    conn.execute("DELETE FROM task_runs WHERE task = 'regles'")
    conn.execute("INSERT INTO task_runs (task, run_day, status, started_at, details) VALUES ('regles', %s, 'ok', %s, %s)",
                 (NOW.date(), NOW - timedelta(minutes=2), json.dumps({"ais_gap": {"episodes": [
                     {**ep, "vessel_id": peche},
                     {**ep, "vessel_id": sortie, "gravite": "faible", "gravite_avant_declassement": "elevee",
                      "point_collectif": 6},
                     {**ep, "vessel_id": cargo, "derniere_position": [-5.5, 48.2]}]}})))
    out = tc.collecter(conn, RULES, NOW)
    assert out["tips"] == {"coupure_ais": 1} and out["ecartes"] == {"type": 1, "declassee": 1}
    motifs = {e["vessel_id"]: e["motif"] for e in out["ecartes_detail"]}
    assert motifs[peche].startswith("navire de pêche (type AIS 30)") and "sortie de couverture" in motifs[sortie]
    assert [r[0] for r in conn.execute("SELECT vessel_id FROM demandes_observation")] == [cargo]
    # Demande créée avant le filtre sur un pêcheur : close au cycle suivant, avec le motif
    r = tc.creer(conn, RULES, NOW, cle="coupure:ancienne", tip="coupure_ais", lon=LARGE[0], lat=LARGE[1],
                 position_le=NOW - timedelta(hours=3), vessel_id=peche, sog=4.0, cog=90.0, motif="coupure observée")
    tc.planifier(conn, RULES, NOW)
    d = _demande(conn, r["demande"])
    assert d["etat"] == "sans_passage" and d["motif"].startswith("navire de pêche")


def test_no_request_waits_without_a_reason(conn):
    """Point sans passage au calendrier, navire qui émet hors des emprises, navire muet lent hors des emprises : en
    attente, chacun avec la raison d'attendre ; la planification n'écrit jamais d'attente sans motif."""
    _passage(conn, "TIP_MARSEILLE", NOW + timedelta(hours=3), MARSEILLE)
    tc.creer(conn, RULES, NOW, cle="op:point", tip="operateur", lon=-4.0, lat=46.5, position_le=NOW, motif="demande manuelle")
    emet = _navire(conn, 636999716, -4.5, 46.0, sog=11.0, cog=180.0, t=NOW - timedelta(minutes=5))
    tc.creer(conn, RULES, NOW, cle="op:emet", tip="operateur", lon=-4.5, lat=46.0, position_le=NOW - timedelta(minutes=5),
             vessel_id=emet, sog=11.0, cog=180.0, motif="demande manuelle")
    out = tc.planifier(conn, RULES, NOW)
    rows = conn.execute("SELECT etat, motif FROM demandes_observation ORDER BY id").fetchall()
    assert out["en_attente"] == 2 and [r[0] for r in rows] == ["en_attente", "en_attente"]
    assert all(r[1].startswith("estime hors des passages : position prédite à ") and "km de nos emprises au passage du"
               in r[1] for r in rows)
    conn.execute("DELETE FROM sar_passes WHERE product_name LIKE 'TIP_%'")
    tc.planifier(conn, RULES, NOW)
    assert {r[0] for r in conn.execute("SELECT motif FROM demandes_observation")} == {
        f"aucun passage Sentinel 1 prévu au calendrier avant l'expiration le {NOW + timedelta(hours=36):%d/%m %H:%M}"}


def test_requests_go_first_under_the_daily_ceiling_and_budget_refusal_is_recorded(conn, monkeypatch):
    """Deux passages acquis : l'un porte une demande d'observation, l'autre seulement la sélection habituelle (alerte
    VIIRS) ; avec une seule scène par jour, la demande passe d'abord. Puis budget nul : refus enregistré."""
    sys.path.insert(0, str(Path(__file__).parent))
    from test_sentinel import FakeStac
    from mars import sentinel, travailleurs
    monkeypatch.setenv("CDSE_TRAVAILLEUR_ACCESS_KEY", "k")
    monkeypatch.setenv("CDSE_TRAVAILLEUR_SECRET_KEY", "s")
    conn.execute("DELETE FROM analyses")
    t0 = NOW - timedelta(hours=1)
    p_habituel = _passage(conn, "TIP_HABITUEL", t0 - timedelta(minutes=30), statut="acquis")
    ouest = {"type": "Polygon", "coordinates": [[[-7.0, 47.5], [-5.8, 47.5], [-5.8, 48.8], [-7.0, 48.8], [-7.0, 47.5]]]}
    p_demande = _passage(conn, "TIP_DEMANDE", t0, ouest, statut="acquis")     # ne couvre pas l'alerte VIIRS
    _alerte(conn, "DARK_SHIP", "moyenne", -5.5, 48.5, {"source": "viirs"}, t=t0 - timedelta(hours=2))
    did = conn.execute(
        """INSERT INTO demandes_observation (tip, priorite, position, position_le, etat, expire_le, pass_id, heure_passage,
                                             position_predite, rayon_m, prediction, fenetres)
           VALUES ('coupure_ais', 5, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, 'planifiee', %s, %s, %s,
                   ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 3000, 'point', %s) RETURNING id""",
        (*LARGE, t0, NOW + timedelta(hours=30), p_demande, t0, *LARGE, json.dumps([list(LARGE)]))).fetchone()[0]
    # Sans tip and cue actif, le passage de la demande n'a aucun point : seule la sélection habituelle
    sc = {"id": "S1C_IW_GRDH_1SDV_TIP_COG", "debut": t0.isoformat(), "fin": (t0 + timedelta(seconds=25)).isoformat(),
          "emprise": EMPRISE}
    stac = FakeStac([sc])
    assert [p["pass_id"] for p in sentinel.plan(conn, stac, COUPE, NOW)["passages"]] == [p_habituel]
    p = sentinel.plan(conn, stac, RULES, NOW)
    assert [x["pass_id"] for x in p["passages"]] == [p_demande, p_habituel]          # la demande d'abord
    lances = []

    def faux(c, _scw, _rules, _tache, params, _env, **_kw):
        lances.append(params)
        wid = c.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres) VALUES "
                        "('sentinel', 'GP1-S', 'fr-par-2', 'x', '{}') RETURNING id").fetchone()[0]
        return {"travailleur": wid}
    monkeypatch.setattr(travailleurs, "launch", faux)
    un = {**RULES, "sentinel": {**S, "scenes_max_par_jour": 1}}
    r = sentinel.launch(conn, None, un, NOW, stac)
    aid = r["analyses"][0]
    assert conn.execute("SELECT pass_id FROM analyses WHERE id = %s", (aid,)).fetchone()[0] == p_demande
    assert conn.execute("SELECT analysis_id, etat FROM demandes_observation WHERE id = %s", (did,)).fetchone() == \
        (aid, "planifiee")
    # Budget : une nouvelle demande sur le passage restant est refusée avec le motif
    conn.execute("UPDATE demandes_observation SET analysis_id = NULL WHERE id = %s", (did,))
    conn.execute("DELETE FROM analyses WHERE id = %s", (aid,))                 # le passage est de nouveau libre
    zero = {**RULES, "sentinel": {**S, "scenes_max_par_jour": 2, "budget_mensuel_eur": 0.0}}
    r = sentinel.launch(conn, None, zero, NOW, stac)
    assert "budget" in r["refus"]
    d = _demande(conn, did)
    assert d["etat"] == "refusee_budget" and "budget" in d["motif"]


def _analyse(conn, pid, t, detections):
    aid = conn.execute("INSERT INTO analyses (pass_id, aoi, mode, status, model_version, source) VALUES (%s, "
                       "ST_MakeEnvelope(-7, 47, -5, 49, 4326)::geography, 'full', 'done', 'essai', 'sentinel1') "
                       "RETURNING id", (pid,)).fetchone()[0]
    for lon, lat, vid, mask in detections:
        conn.execute("INSERT INTO detections (analysis_id, geom, scene, ts, matched_vessel_id, mask_reason, length_m) "
                     "VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 'SC', %s, %s, %s, 150)",
                     (aid, lon, lat, t, vid, mask))
    return aid


@pytest.mark.parametrize("cas, attendu, gravite", [
    ("muet", "present_sans_ais", "elevee"), ("apparie", "present_apparie", "moyenne"), ("rien", "absent", "moyenne")])
def test_result_is_attached_to_the_alert_and_severity_adjusted(conn, cas, attendu, gravite):
    t = NOW + timedelta(hours=2)
    pid = _passage(conn, "TIP_RESULTAT", t, statut="acquis")
    vid = _navire(conn, 636999703, *LARGE, sog=0.2)
    aid_alerte = _alerte(conn, "AIS_GAP", "moyenne", *LARGE, {"navire": {"vessel_id": vid}}, vid)
    did = tc.creer(conn, RULES, NOW, cle=f"alerte:{aid_alerte}", tip="coupure_ais", lon=LARGE[0], lat=LARGE[1],
                   position_le=NOW, vessel_id=vid, sog=0.2, cog=0.0, alerte_id=aid_alerte)["demande"]
    conn.execute("UPDATE demandes_observation SET etat = 'planifiee', pass_id = %s, heure_passage = %s, rayon_m = 1500, "
                 "position_predite = position WHERE id = %s", (pid, t, did))
    proche = tc.destination(*LARGE, 45, 600)
    dets = {"muet": [(*proche, None, None), (LARGE[0] + 0.001, LARGE[1], None, "terre")],
            "apparie": [(*proche, vid, None)], "rien": [(LARGE[0] + 0.5, LARGE[1], None, None)]}[cas]
    an = _analyse(conn, pid, t, dets)
    conn.execute("UPDATE demandes_observation SET analysis_id = %s WHERE id = %s", (an, did))
    envois = []
    out = tc.evaluer(conn, an, RULES, notifier=lambda titre, msg: envois.append((titre, msg)))
    assert out == [{"alerte": aid_alerte, "gravite_avant": "moyenne", "gravite_apres": gravite}]
    etat, res, dist = conn.execute("SELECT etat, resultat, distance_m FROM demandes_observation WHERE id = %s",
                                   (did,)).fetchone()
    assert (etat, res) == ("analysee", attendu)
    sev, det = conn.execute("SELECT severity, details FROM alerts WHERE id = %s", (aid_alerte,)).fetchone()
    v = det["verification_satellite"]
    assert sev == gravite and v["resultat"] == attendu and v["demande"] == did and v["gravite_avant"] == "moyenne"
    preuves = {r[0] for r in conn.execute("SELECT evidence_type FROM alert_evidence WHERE alert_id = %s", (aid_alerte,))}
    assert "demande" in preuves and ("detection" in preuves) == (cas != "rien")
    if cas == "muet":
        assert dist == pytest.approx(600, abs=5) and v["gravite_ajustee"] == "elevee"
        assert len(envois) == 1 and "relevée à elevee" in envois[0][0]                    # notification
    else:
        assert envois == []
    assert conn.execute("SELECT count(*) FROM alerts WHERE id = %s", (aid_alerte,)).fetchone()[0] == 1   # jamais retirée


def test_rules_cycle_keeps_the_satellite_verification(conn):
    """Une alerte mise à jour en place par sa règle (toutes les 5 minutes) garde sa vérification et sa gravité."""
    from mars.rules import save_alerts
    import asyncpg
    conn.rollback()                                   # rien de la fixture n'est validé ; l'essai nettoie après lui

    async def go():
        c = await asyncpg.connect(URL)
        await c.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        try:
            item = {"key": "essai:tip", "severity": "moyenne", "event_time": NOW, "lon": LARGE[0], "lat": LARGE[1],
                    "details": {"motif": "essai"}, "vessels": []}
            await save_alerts(c, "RENDEZVOUS", [item], rule_version="essai")
            await c.execute("UPDATE alerts SET severity = 'elevee', details = details || '{\"verification_satellite\": "
                            "{\"resultat\": \"present_sans_ais\", \"gravite_ajustee\": \"elevee\"}}' WHERE rule_key = 'essai:tip'")
            await save_alerts(c, "RENDEZVOUS", [{**item, "details": {"motif": "essai, cycle suivant"}}], rule_version="essai")
            return await c.fetchrow("SELECT severity, details FROM alerts WHERE rule_key = 'essai:tip'")
        finally:
            await c.execute("DELETE FROM alerts WHERE rule_key = 'essai:tip'")
            await c.close()
    r = asyncio.run(go())
    assert r["severity"] == "elevee" and r["details"]["motif"] == "essai, cycle suivant"
    assert r["details"]["verification_satellite"]["resultat"] == "present_sans_ais"


def test_manual_request_through_the_api_is_planned_by_the_next_cycle(conn, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    import asyncpg
    import demandes as api
    conn.rollback()                                   # nettoyage de la fixture annulé : seul l'essai est validé
    vid = _navire(conn, 636999704, *LARGE, sog=0.1)
    pid = _passage(conn, "TIP_MANUEL", NOW + timedelta(hours=4))
    conn.commit()

    class Req:
        class app:
            class state:
                pool = None

    async def go(rules):
        monkeypatch.setattr(api, "load_rules", lambda: rules)
        Req.app.state.pool = await asyncpg.create_pool(URL, min_size=1, max_size=2)
        try:
            return await api.demander(Req, api.NouvelleDemande(vessel_id=vid, author="Antoine"))
        finally:
            await Req.app.state.pool.close()
    try:
        with pytest.raises(Exception) as refus:                                 # tip and cue coupé : refus clair
            asyncio.run(go(COUPE))
        assert "coupé" in str(refus.value.detail)
        r = asyncio.run(go(RULES))
        assert r["etat"] == "en_attente" and not r["regroupee"]
        assert asyncio.run(go(RULES))["id"] == r["id"]                           # même navire : regroupée
        tc.planifier(conn, RULES, NOW)
        d = _demande(conn, r["id"])
        assert d["etat"] == "planifiee" and d["pass_id"] == pid and d["priorite"] == 6
        assert conn.execute("SELECT count(*) FROM demandes_origines WHERE demande_id = %s AND tip = 'operateur'",
                            (r["id"],)).fetchone()[0] == 2
    finally:
        conn.rollback()
        conn.execute("DELETE FROM demandes_origines WHERE demande_id IN (SELECT id FROM demandes_observation "
                     "WHERE vessel_id = %s)", (vid,))
        conn.execute("DELETE FROM demandes_observation WHERE vessel_id = %s", (vid,))
        conn.execute("DELETE FROM sar_passes WHERE product_name = 'TIP_MANUEL'")
        conn.execute("DELETE FROM positions WHERE vessel_id = %s", (vid,))
        conn.execute("DELETE FROM vessels WHERE id = %s", (vid,))
        conn.commit()


def test_request_trace_links_the_alert_and_the_analysis():
    """La trace d'une demande relie celle de l'alerte (identifiant seul) et celle de l'analyse (traceparent complet)."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from mars import traces
    mem = InMemorySpanExporter()
    traces.init("mars-test", exportateur=mem)
    alerte, analyse = "a" * 32, "00-" + "b" * 32 + "-" + "c" * 16 + "-01"
    with traces.span("tip_and_cue.resultat", nouvelle_trace=True, liens=[alerte, analyse, None, "pas une trace"]):
        pass
    s = [x for x in mem.get_finished_spans() if x.name == "tip_and_cue.resultat"][-1]
    assert [(format(link.context.trace_id, "032x"), format(link.context.span_id, "016x")) for link in s.links] == [
        ("a" * 32, "a" * 16), ("b" * 32, "c" * 16)]
    mem.clear()


def test_replay_on_the_database_reads_tips_passes_and_costs(conn):
    """Rejeu (taches.py tip-rejeu) : une alerte VIIRS au large avec un passage 8 h plus tard, un navire à quai ;
    rien n'est écrit."""
    conn.execute("DELETE FROM task_runs WHERE task = 'regles' AND started_at > %s", (NOW - timedelta(days=2),))
    _passage(conn, "TIP_REJEU", NOW + timedelta(hours=8), statut="acquis")
    _passage(conn, "TIP_REJEU_M", NOW + timedelta(hours=9), MARSEILLE, statut="acquis")
    _alerte(conn, "DARK_SHIP", "elevee", *LARGE, {"source": "viirs"})
    vid = _navire(conn, 636999705, *QUAI, sog=0.0)
    _alerte(conn, "IDENTITY_CHANGE", "moyenne", *QUAI, {"navire": {"vessel_id": vid}}, vid)
    avant = conn.execute("SELECT count(*) FROM demandes_observation").fetchone()[0]
    r = tc.rejeu_base(conn, RULES, NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert r["par_tip"]["viirs"]["passage"] == 1 and r["par_tip"]["identite"]["cote"] == 1
    assert r["sessions"] == 1 and r["fenetres"] == 1 and r["cout_eur"] == pytest.approx(0.19, abs=0.01)
    assert conn.execute("SELECT count(*) FROM demandes_observation").fetchone()[0] == avant


def test_observations_route_order_region_and_active_count(conn):
    """Panneau « Observations » : planifiées par passage le plus proche, puis en attente, puis analysées les plus
    récentes, closes sans résultat en bas ; région de la barre d'état ; compteur des demandes actives."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    import asyncpg
    import demandes as api
    conn.rollback()                                   # l'essai écrit pour de vrai (pool asyncpg), puis nettoie
    vid = _navire(conn, 636999706, *LARGE)
    aid = _alerte(conn, "DARK_SHIP", "elevee", 5.2, 43.0, {"source": "viirs"})
    ids = {}
    for nom, etat, heure, lon, lat, fini in (
            ("plan_tard", "planifiee", NOW + timedelta(hours=9), *LARGE, None),
            ("plan_tot", "planifiee", NOW + timedelta(hours=2), *LARGE, None),
            ("attente", "en_attente", None, *LARGE, None),
            ("analysee_vieille", "analysee", NOW - timedelta(hours=10), *LARGE, NOW - timedelta(hours=9)),
            ("analysee_recente", "analysee", NOW - timedelta(hours=3), *LARGE, NOW - timedelta(hours=2)),
            ("expiree", "expiree", None, *LARGE, NOW - timedelta(hours=1)),
            ("marseille", "planifiee", NOW + timedelta(hours=1), 5.2, 43.0, None),
            ("ancienne", "expiree", None, *LARGE, NOW - timedelta(days=5))):
        ids[nom] = conn.execute(
            """INSERT INTO demandes_observation (cree_le, mis_a_jour_le, tip, priorite, vessel_id, position, position_le,
                                                 etat, expire_le, heure_passage, fini_le, resultat)
               VALUES (%s, %s, %s, 4, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (NOW - timedelta(days=6 if nom == "ancienne" else 12 / 24), fini or NOW, "viirs" if nom == "marseille" else
             "coupure_ais", None if nom == "marseille" else vid, lon, lat, NOW, etat, NOW + timedelta(hours=30), heure,
             fini, "present_sans_ais" if etat == "analysee" else None)).fetchone()[0]
    conn.execute("INSERT INTO demandes_origines (cle, demande_id, tip, priorite, alerte_id) VALUES ('essai:obs', %s, "
                 "'viirs', 4, %s)", (ids["marseille"], aid))
    conn.commit()

    class Req:
        class app:
            class state:
                pool = None

    async def go(**kw):
        Req.app.state.pool = await asyncpg.create_pool(URL, min_size=1, max_size=2)
        try:
            return await api.observations(Req, start=NOW - timedelta(hours=24), end=NOW, **kw)
        finally:
            await Req.app.state.pool.close()
    try:
        r = asyncio.run(go())
        noms = {v: k for k, v in ids.items()}
        ordre = [noms[d["id"]] for d in r["demandes"]]
        assert ordre == ["marseille", "plan_tot", "plan_tard", "attente", "analysee_recente", "analysee_vieille",
                         "expiree"]                                   # « ancienne » hors de la plage
        assert r["actives"] == 4
        m = next(d for d in r["demandes"] if d["id"] == ids["marseille"])
        assert m["alerte_id"] == aid and m["alerte_type"] == "DARK_SHIP" and m["navire"] is None
        b = asyncio.run(go(region="bretagne"))
        assert ids["marseille"] not in [d["id"] for d in b["demandes"]] and b["actives"] == 3
        assert [noms[d["id"]] for d in asyncio.run(go(etat="analysee"))["demandes"]] == ["analysee_recente",
                                                                                      "analysee_vieille"]
    finally:
        conn.rollback()
        conn.execute("DELETE FROM demandes_observation WHERE id = ANY(%s)", (list(ids.values()),))
        conn.execute("DELETE FROM alerts WHERE id = %s", (aid,))
        conn.execute("DELETE FROM positions WHERE vessel_id = %s", (vid,))
        conn.execute("DELETE FROM vessels WHERE id = %s", (vid,))
        conn.commit()
