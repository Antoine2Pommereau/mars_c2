"""Mode démonstration (lot P, phase F) : masquage des navires et rejeu tel qu'il était.

Calculs purs (pseudonymes stables, MMSI au pays gardé, OMI à clé valide, trafic en colonnes, textes libres, liens
retirés, infrastructures intactes), puis l'API réelle sur la base de test (MARS_TEST_DATABASE_URL) : réponses d'un
navire et du trafic masquées sous le cookie de session, aucun nom ni MMSI réel dans le corps, photo et GPX refusés ;
rejeu des alertes et des demandes à leur état de l'instant, sans aucune écriture ; scénarios enregistrés."""
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

from mars.config import ROOT

sys.path.insert(0, str(ROOT / "backend"))
import demo  # noqa: E402

URL = os.environ.get("MARS_TEST_DATABASE_URL")
G1, G2 = "0" * 32, "f" * 32


def test_pseudonyms_are_stable_per_session_and_keep_the_flag():
    m, n = demo.Masque(G1), demo.Masque(G2)
    assert m.nom("GELIOTROP") == m.nom("geliotrop ") and m.nom("GELIOTROP") != "GELIOTROP"
    assert m.nom("GELIOTROP") != n.nom("GELIOTROP"), "une autre session, d'autres pseudonymes"
    p = m.mmsi(273456789)
    assert isinstance(p, int) and str(p).startswith("273") and p != 273456789
    i = str(m.imo(9289518))
    assert len(i) == 7 and i != "9289518"
    assert int(i[6]) == sum(int(c) * w for c, w in zip(i[:6], range(7, 1, -1))) % 10      # clé de contrôle valide


def test_structures_columns_texts_and_links():
    m = demo.Masque(G1)
    trafic = {"at": "x", "cols": ["vessel_id", "lon", "mmsi", "name", "flag"],
              "rows": [[1, -5.0, 273456789, "GELIOTROP", "RU"], [2, -4.0, 227000000, None, "FR"]]}
    alerte = {"type": "WATCHLIST", "details": {
        "navire": {"vessel_id": 1, "mmsi": 273456789, "name": "GELIOTROP"},
        "contexte": ["Navire des listes à 3 km : GELIOTROP", "MMSI 273456789 vu au large"],
        "infrastructure": {"id": 7, "name": "Câble Celtixconnect", "type": "Câble télécom"},
        "entries": [{"source": "eu_fsf", "name": "GELIOTROP", "url": "https://www.opensanctions.org/entities/x/"}]}}
    out = m.masquer({"navires": trafic, "alertes": [alerte], "region": {"name": "Bretagne"}})
    texte = json.dumps(out, ensure_ascii=False)
    assert "GELIOTROP" not in texte and "273456789" not in texte and "opensanctions" not in texte
    assert out["navires"]["rows"][0][4] == "RU" and out["navires"]["rows"][1][3] is None
    assert out["alertes"][0]["details"]["infrastructure"]["name"] == "Câble Celtixconnect"   # données publiques
    assert out["region"]["name"] == "Bretagne"
    pseudo = out["navires"]["rows"][0][3]
    assert out["alertes"][0]["details"]["contexte"][0] == f"Navire des listes à 3 km : {pseudo}"


# API réelle sur la base de test

@pytest.fixture
def api(monkeypatch):
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    from fastapi.testclient import TestClient
    monkeypatch.setenv("DATABASE_URL", URL)
    sys.modules.pop("app", None)
    app = importlib.import_module("app")
    conn = psycopg.connect(URL, autocommit=True)
    v = conn.execute("SELECT id, mmsi, name FROM vessels WHERE name IS NOT NULL AND length(name) >= 4 LIMIT 1").fetchone()
    if v is None:
        pytest.skip("aucun navire nommé dans la base de test")
    with TestClient(app.app) as c:
        yield c, conn, v
    conn.close()


def test_api_masks_vessels_traffic_and_blocks_photos(api):
    c, conn, (vid, mmsi, nom) = api
    clair = c.get(f"/api/vessels/{vid}")
    assert clair.status_code == 200 and nom in clair.text
    r = c.post("/api/controle/demo", json={"masquage": True})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    masque = c.get(f"/api/vessels/{vid}")
    assert masque.status_code == 200 and nom not in masque.text and str(mmsi) not in masque.text
    assert masque.json()["name"] != nom and str(masque.json()["mmsi"])[:3] == str(mmsi)[:3]
    t = conn.execute("SELECT max(ts) FROM positions WHERE vessel_id = %s", (vid,)).fetchone()[0]
    if t is not None:
        tr = c.get("/api/traffic", params={"at": t.isoformat()})
        assert nom not in tr.text and str(mmsi) not in tr.text
    assert c.get(f"/api/vessels/{vid}/photo").status_code == 404
    assert c.get("/api/controle/demo").json() == {"masquage": True}
    c.post("/api/controle/demo", json={"masquage": False})
    assert nom in c.get(f"/api/vessels/{vid}").text
    assert conn.execute("SELECT count(*) FROM journal_controle WHERE action = 'demonstration'").fetchone()[0] >= 2


def test_replay_as_it_was_reads_without_writing(api):
    c, conn, (vid, _m, _n) = api
    conn.execute("DELETE FROM alerts WHERE rule_key LIKE 'essai_r:%'")
    t0 = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
    aid = conn.execute(
        """INSERT INTO alerts (type, severity, event_time, detected_at, geom, details, rule_version, rule_key, status)
           VALUES ('WATCHLIST', 'elevee', %s, %s, ST_SetSRID(ST_MakePoint(-6, 48), 4326)::geography, '{}', 'x',
                   'essai_r:1', 'classee') RETURNING id""", (t0, t0 + timedelta(minutes=5))).fetchone()[0]
    conn.execute("INSERT INTO alert_actions (alert_id, action, author, at) VALUES (%s, 'acquitter', 'antoine', %s), "
                 "(%s, 'classer', 'antoine', %s)", (aid, t0 + timedelta(hours=1), aid, t0 + timedelta(hours=3)))
    try:
        avant = conn.execute("SELECT count(*), max(xmin::text::bigint) FROM alerts").fetchone()
        p = {"start": (t0 - timedelta(hours=1)).isoformat(), "end": (t0 + timedelta(hours=6)).isoformat()}

        def statut(instant):
            fc = c.get("/api/alerts", params={**p, "a_l_instant": instant.isoformat()}).json()
            return [f["properties"]["status"] for f in fc["features"] if f["properties"]["id"] == aid]
        assert statut(t0) == [], "pas encore levée à cet instant"
        assert statut(t0 + timedelta(minutes=30)) == ["nouvelle"]
        assert statut(t0 + timedelta(hours=2)) == ["acquittee"]
        assert statut(t0 + timedelta(hours=4)) == ["classee"]
        assert [f["properties"]["status"] for f in c.get("/api/alerts", params=p).json()["features"]
                if f["properties"]["id"] == aid] == ["classee"]
        assert conn.execute("SELECT count(*), max(xmin::text::bigint) FROM alerts").fetchone() == avant, "rien d'écrit"
    finally:
        conn.execute("DELETE FROM alerts WHERE id = %s", (aid,))


def test_scenarios_saved_by_an_operator(api, monkeypatch):
    from mars import controle
    c, conn, _v = api
    monkeypatch.setenv("MARS_OPERATEURS", f"antoine:{controle.hacher('bon mot de passe', iterations=1000)}")
    conn.execute("DELETE FROM scenarios_demo")
    adresse = "?mode=plage&duree=24h&debut=2026-10-19T00:00:00.000Z&fin=2026-10-20T00:00:00.000Z&sel=alerte:12&region=bretagne"
    assert c.post("/api/controle/scenarios", json={"nom": "Analyse 97", "adresse": adresse}).status_code == 401
    c.post("/api/controle/connexion", json={"operateur": "antoine", "mot_de_passe": "bon mot de passe"})
    assert c.post("/api/controle/scenarios", json={"nom": "x", "adresse": "?admin=1"}).status_code == 422
    sid = c.post("/api/controle/scenarios", json={"nom": "Analyse 97", "adresse": adresse}).json()["id"]
    s = c.get("/api/controle/scenarios").json()
    assert s[0]["id"] == sid and s[0]["region"] == "bretagne" and s[0]["selection"] == "alerte:12"
    assert s[0]["adresse"] == adresse[1:]
    assert c.post(f"/api/controle/scenarios/{sid}/retirer", json={}).status_code == 200
    assert c.get("/api/controle/scenarios").json() == []
    conn.execute("DELETE FROM scenarios_demo")
    conn.execute("DELETE FROM sessions_operateur")
