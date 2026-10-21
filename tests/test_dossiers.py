"""Dossiers (lot U) : regroupement par navire et par point, niveau de suspicion et ses contributions, phrase
d'explication ; puis l'API réelle sur la base de test (MARS_TEST_DATABASE_URL) : liste à l'instant, récit et preuve,
décision appliquée aux seules alertes ouvertes et journalisée alerte par alerte, rejeu tel qu'il était, devis d'une
vérification satellite refusé en pause."""
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

from mars import dossiers as D
from mars.config import ROOT, load_rules

RULES = load_rules()
P = RULES["dossiers"]
T = datetime(2031, 1, 10, 12, tzinfo=timezone.utc)
URL = os.environ.get("MARS_TEST_DATABASE_URL")


def alerte(i, typ, sev, h, vid=None, details=None, lon=-5.0, lat=48.5, dets=None, statut="nouvelle", infra=None):
    return {"id": i, "type": typ, "severity": sev, "status": statut, "event_time": T - timedelta(hours=h),
            "details": details or {}, "lon": lon, "lat": lat, "vessel_id": vid, "detections": dets or [],
            "infrastructure": infra}


NAVIRES = {7: {"nom": "ESSAI SEPT", "pavillon": "RU"}, 8: {"nom": "ESSAI HUIT", "pavillon": "PA"}}


# Calcul pur

def test_vessel_dossier_with_linked_echo_is_urgent_and_explained():
    xs = [alerte(1, "WATCHLIST", "elevee", 30, 7, {"niveau": "sanctionne"}),
          alerte(2, "AIS_GAP", "elevee", 10, 7, {"duree_min": 1081, "verification_satellite": {"resultat": "present_sans_ais"}},
                 dets=[55], infra={"nom": None, "type": "Câble télécom"}),
          alerte(3, "DARK_SHIP", "elevee", 2, None, {"source": "sentinel1"}, lon=-4.9, dets=[55])]
    (d,) = D.regrouper(xs, P, NAVIRES)
    assert d["cle"] == "navire:7" and d["alertes"] == [1, 2, 3], "l'écho relié par la détection rejoint le navire"
    assert d["niveau"] == 3 and d["niveau_cle"] == "urgent"
    assert d["phrase"] == "Sanctionné, AIS coupé 18 h près d'un câble télécom, revu au radar sans AIS"
    assert d["contributions"] == ["gravité elevee : niveau 2", "signaux recoupés (AIS, liste, satellite) : plus un",
                                  "vérification satellite, vu sans AIS : plus un"]
    assert d["icones"] == ["WATCHLIST", "AIS_GAP", "DARK_SHIP"]


def test_listed_vessel_alone_depends_on_where_it_is():
    xs = [alerte(1, "WATCHLIST", "critique", 3, 7, {"niveau": "flotte_fantome"})]
    assert D.regrouper(xs, P, NAVIRES)[0]["niveau_cle"] == "a_noter"                      # à quai ou en transit
    for ctx in ({"en_mer_pres_infra": True}, {"sejour_prolonge": True}):
        d = D.regrouper(xs, P, NAVIRES, {7: ctx})[0]
        assert d["niveau_cle"] == "a_examiner" and d["phrase"] == "Flotte fantôme"


def test_gap_alone_levels_and_unconfirmed_points():
    assert D.regrouper([alerte(1, "AIS_GAP", "elevee", 3, 8, {"duree_min": 300})], P, NAVIRES)[0]["niveau_cle"] == "a_examiner"
    assert D.regrouper([alerte(1, "AIS_GAP", "faible", 3, 8, {"duree_min": 150})], P, NAVIRES)[0]["niveau_cle"] == "a_noter"
    d = D.regrouper([alerte(1, "AIS_GAP", "moyenne", 30, 8, {"duree_min": 200}),
                     alerte(2, "AIS_GAP", "faible", 3, 8, {"duree_min": 120})], P, NAVIRES)[0]
    assert d["phrase"] == "AIS coupé 2 fois" and d["niveau"] == 1
    v = D.regrouper([alerte(5, "DARK_SHIP", "moyenne", 3, None, {"source": "viirs", "a_confirmer": True})], P, NAVIRES)[0]
    assert (v["genre"], v["nom"], v["niveau"], v["icones"]) == ("point", "Lumière en mer", 1, ["VIIRS"])
    assert v["contributions"][-1] == "tous les signaux sont à confirmer : ramené à 1"


def test_points_merge_within_2_km_and_6_h_only():
    a = alerte(1, "DARK_SHIP", "moyenne", 5, None, {"source": "viirs"}, lon=-5.0, lat=48.5)
    b = alerte(2, "DARK_SHIP", "moyenne", 3, None, {"source": "sentinel1"}, lon=-5.015, lat=48.505)   # 1,2 km, 2 h
    c = alerte(3, "DARK_SHIP", "moyenne", 2, None, {"source": "viirs"}, lon=-4.8, lat=48.5)            # 15 km
    e = alerte(4, "DARK_SHIP", "moyenne", 15, None, {"source": "viirs"}, lon=-5.0, lat=48.5)           # 10 h avant b
    ds = D.regrouper([a, b, c, e], P, NAVIRES)
    assert sorted(len(d["alertes"]) for d in ds) == [1, 1, 2]


def test_closed_alerts_do_not_make_a_dossier():
    assert D.regrouper([alerte(1, "AIS_GAP", "elevee", 3, 8, statut="classee")], P, NAVIRES) == []
    d = D.regrouper([alerte(1, "AIS_GAP", "elevee", 3, 8, statut="acquittee")], P, NAVIRES)[0]
    assert d["statuts"] == ["acquittee"], "une alerte vue reste dans le dossier"


# API réelle sur la base de test

@pytest.fixture
def api(monkeypatch):
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("MARS_OPERATEURS", raising=False)
    sys.path.insert(0, str(ROOT / "backend"))
    sys.modules.pop("app", None)
    app = importlib.import_module("app")
    conn = psycopg.connect(URL, autocommit=True)
    _nettoyer(conn)
    vid = conn.execute("SELECT id FROM vessels ORDER BY id LIMIT 1").fetchone()[0]
    ids = {}
    for nom, typ, sev, h, details, avec_navire in (
            ("liste", "WATCHLIST", "elevee", 30, {"niveau": "sanctionne"}, True),
            ("coupure", "AIS_GAP", "elevee", 10, {"duree_min": 1081, "derniere_position": [-5.0, 48.5],
                                                  "dernier_message": "2031-01-10T02:00:00+00:00"}, True),
            ("echo", "DARK_SHIP", "elevee", 2, {"source": "sentinel1"}, False),
            ("lumiere", "DARK_SHIP", "moyenne", 4, {"source": "viirs", "a_confirmer": True}, False)):
        lon = -4.9 if nom == "echo" else -3.0 if nom == "lumiere" else -5.0
        ids[nom] = conn.execute(
            """INSERT INTO alerts (type, severity, event_time, detected_at, geom, details, rule_version, rule_key)
               VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, 48.5), 4326)::geography, %s, 'x', %s) RETURNING id""",
            (typ, sev, T - timedelta(hours=h), T - timedelta(hours=h) + timedelta(minutes=5), lon, json.dumps(details),
             f"essai_d:{nom}")).fetchone()[0]
        if avec_navire:
            conn.execute("INSERT INTO alert_evidence VALUES (%s, 'vessel', %s)", (ids[nom], vid))
    # Écho relié au navire par la même détection (vérification satellite)
    conn.execute("INSERT INTO alert_evidence VALUES (%s, 'detection', 999999001), (%s, 'detection', 999999001)",
                 (ids["coupure"], ids["echo"]))
    from fastapi.testclient import TestClient as TC
    with TC(app.app) as c:
        yield c, conn, vid, ids
    _nettoyer(conn)
    conn.close()


def _nettoyer(conn):
    conn.execute("DELETE FROM alert_actions WHERE alert_id IN (SELECT id FROM alerts WHERE rule_key LIKE 'essai_d:%')")
    conn.execute("DELETE FROM alerts WHERE rule_key LIKE 'essai_d:%'")
    conn.execute("UPDATE interrupteurs_satellites SET en_pause = false")


def test_api_list_story_and_proof(api):
    c, _conn, vid, ids = api
    r = c.get("/api/dossiers", params={"end": T.isoformat()}).json()
    cles = [d["cle"] for d in r["dossiers"]]
    assert cles == [f"navire:{vid}", f"point:{ids['lumiere']}"]
    assert r["compteurs"] == {"a_noter": 1, "a_examiner": 0, "urgent": 1}
    assert r["dossiers"][0]["alertes"] == sorted([ids["liste"], ids["coupure"], ids["echo"]])
    s = c.get(f"/api/dossiers/navire:{vid}", params={"end": T.isoformat()}).json()
    assert [a["type"] for a in s["alertes_detail"]] and s["preuve"]["derniere_position"]["lon"] == -5.0
    assert s["niveau_cle"] == "urgent" and len(s["contributions"]) >= 2


def test_api_decision_needs_a_reason_and_an_open_dossier(api):
    c, _conn, vid, _ids = api
    assert c.post(f"/api/dossiers/navire:{vid}/decision", json={"action": "classer"}).status_code == 422
    assert c.post(f"/api/dossiers/navire:{vid}/decision", json={"action": "effacer"}).status_code == 422
    # La décision vaut à l'instant réel : le dossier de 2031 n'y est pas encore ouvert
    r = c.post(f"/api/dossiers/navire:{vid}/decision", json={"action": "vu"})
    assert r.status_code == 404 and r.json()["detail"] == "Dossier introuvable ou clos à cet instant"


def test_api_decision_at_the_dossier_instant(api, monkeypatch):
    c, conn, vid, ids = api
    import dossiers as api_dossiers
    real = api_dossiers.datetime

    class Fige(real):
        @classmethod
        def now(cls, tz=None):                       # noqa: ARG003
            return T
    monkeypatch.setattr(api_dossiers, "datetime", Fige)
    conn.execute("UPDATE alerts SET status = 'classee' WHERE id = %s", (ids["liste"],))
    r = c.post(f"/api/dossiers/navire:{vid}/decision", json={"action": "classer", "motif": "faux_positif",
                                                             "author": "Antoine"}).json()
    assert sorted(r["alertes"]) == sorted([ids["coupure"], ids["echo"]]), "seules les alertes ouvertes"
    lignes = conn.execute("SELECT alert_id, action, motif, author, note FROM alert_actions WHERE alert_id = ANY(%s) "
                          "ORDER BY alert_id", (list(ids.values()),)).fetchall()
    assert [(a, b, m, au) for a, b, m, au, _n in lignes] == [(ids["coupure"], "classer", "faux_positif", "Antoine"),
                                                            (ids["echo"], "classer", "faux_positif", "Antoine")]
    assert lignes[0][4] == f"Décision prise sur le dossier navire:{vid}"
    assert conn.execute("SELECT count(*) FROM alerts WHERE rule_key LIKE 'essai_d:%'").fetchone()[0] == 4, "rien supprimé"
    assert [d["cle"] for d in c.get("/api/dossiers", params={"end": T.isoformat()}).json()["dossiers"]] == \
        [f"point:{ids['lumiere']}"], "dossier clos"
    r = c.post(f"/api/dossiers/navire:{vid}/decision", json={"action": "rouvrir"}).json()
    assert sorted(r["alertes"]) == sorted([ids["liste"], ids["coupure"], ids["echo"]])


def test_api_verification_quote_refused_in_pause(api, monkeypatch):
    c, conn, vid, _ids = api
    import dossiers as api_dossiers
    real = api_dossiers.datetime

    class Fige(real):
        @classmethod
        def now(cls, tz=None):                       # noqa: ARG003
            return T
    monkeypatch.setattr(api_dossiers, "datetime", Fige)
    r = c.get(f"/api/dossiers/navire:{vid}/verification").json()
    assert r["refus"] is None and r["cout_estime_eur"] > 0
    conn.execute("UPDATE interrupteurs_satellites SET en_pause = true, motif = 'budget' WHERE source = 'tip_and_cue'")
    assert c.get(f"/api/dossiers/navire:{vid}/verification").json()["refus"] == "Vérification satellite en pause : budget"


def test_api_infrastructures_within_a_mile_of_a_dossier(api):
    """Carte calme de la Veille : seuls les câbles à moins d'un mille d'un dossier restent nets."""
    c, conn, _vid, _ids = api
    region = conn.execute("SELECT id FROM regions ORDER BY id LIMIT 1").fetchone()[0]
    proche, loin = (conn.execute(
        """INSERT INTO infrastructure (region_id, kind, name, source, geom)
           VALUES (%s, 'cable', %s, 'essai_d', ST_GeogFromText(%s)) RETURNING id""",
        (region, nom, wkt)).fetchone()[0] for nom, wkt in (
            ("proche", "LINESTRING(-4.91 48.505, -4.89 48.505)"), ("loin", "LINESTRING(-4.91 48.6, -4.89 48.6)")))
    try:
        r = c.get("/api/dossiers", params={"end": T.isoformat()}).json()
        assert proche in r["infrastructures_proches"] and loin not in r["infrastructures_proches"]
    finally:
        conn.execute("DELETE FROM infrastructure WHERE source = 'essai_d'")
