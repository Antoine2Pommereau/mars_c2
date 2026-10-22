"""Lot Z1 : résultats d'un travailleur validés contre sa tâche avant toute écriture (mars/validation.py), refus journalisé
par la route de l'API (travailleur en échec, motif gardé, 422)."""
import copy
import hashlib
import json
import os
import sys

import pytest

from mars import validation
from mars.config import ROOT, load_rules

RULES = load_rules()
URL = os.environ.get("MARS_TEST_DATABASE_URL")

TACHE_VIIRS = {"granules": [{"dnb": "VNP02DNB_NRT.A2026295.0130", "debut": "2026-10-22T01:30:00Z", "satellite": "SNPP"}],
               "regions": [[-6.8, 47.3, -3.0, 49.6]]}
VIIRS = {"granules": [{"dnb": "VNP02DNB_NRT.A2026295.0130", "debut": "2026-10-22 01:30:12+00:00",
                       "fin": "2026-10-22 01:36:00+00:00", "lune_moyenne": 12.0, "eclairs": 0, "torcheres": 1,
                       "detections": [{"lon": -5.1, "lat": 48.2, "nanowatts": 42.5, "orientation": 12.0, "lune": 12.0,
                                       "ciel_clair": 0.9, "vignette": "iVBORw0KGgo=", "vignette_geo": {"a": 1}}]}],
         "mesures": {"duree_s": 300.0}}
TACHE_S1 = {"scenes": [{"analyse": 97, "id": "S1D_IW_GRDH_1SDV_20261019T054351", "debut": "2026-10-19T05:43:51Z",
                        "fin": "2026-10-19T05:44:16Z"}]}
S1 = {"scenes": [{"analyse": 97, "id": "S1D_IW_GRDH_1SDV_20261019T054351", "debut": "2026-10-19T05:43:51Z",
                  "fin": "2026-10-19T05:44:16Z", "fenetres": [{"index": 0}],
                  "detections": [{"lon": 5.27, "lat": 43.31, "score": 0.93, "longueur_m": 324, "cap_deg": 11,
                                  "vitesse_kn": 0.0, "contraste_db": 18.2}]}]}


def test_genuine_results_pass():
    validation.valider("viirs", TACHE_VIIRS, VIIRS, RULES)
    validation.valider("sentinel", TACHE_S1, S1, RULES)
    validation.valider("viirs", TACHE_VIIRS, {"granules": [], "erreur": "licence LANCE non acceptée"}, RULES)


@pytest.mark.parametrize("modif, motif", [
    (lambda r: r["granules"][0].update(dnb="AUTRE"), "absente de la tâche"),
    (lambda r: r["granules"][0]["detections"][0].update(lon=-40.0), "hors de nos zones"),
    (lambda r: r["granules"][0]["detections"][0].update(lat=float("nan")), "nombre fini"),
    (lambda r: r["granules"][0]["detections"][0].update(lat="48.2"), "nombre fini"),
    (lambda r: r["granules"][0].update(debut="2026-10-23T06:00:00+00:00"), "plus de 3 h"),
    (lambda r: r["granules"][0].update(debut="hier"), "date illisible"),
    (lambda r: r["granules"][0].update(detections=[r["granules"][0]["detections"][0]] * 501), "plafond 500"),
    (lambda r: r["granules"][0]["detections"][0].update(vignette="A" * 500_000), "caractères au plus"),
    (lambda r: r.update(granules=r["granules"] * 2), "plafond 1"),
    (lambda r: r.update(mesures=[1]), "mesures doit être un objet"),
])
def test_viirs_results_refused(modif, motif):
    r = copy.deepcopy(VIIRS)
    modif(r)
    with pytest.raises(validation.Refus, match=motif):
        validation.valider("viirs", TACHE_VIIRS, r, RULES)


@pytest.mark.parametrize("modif, motif", [
    (lambda r: r["scenes"][0].update(analyse=98), "absente de la tâche"),
    (lambda r: r["scenes"][0]["detections"][0].update(score=3.0), "hors de"),
    (lambda r: r["scenes"][0]["detections"][0].update(longueur_m=5000), "hors de"),
    (lambda r: r["scenes"][0].update(debut="2026-10-20T05:43:51Z"), "plus de 3 h"),
    (lambda r: r["scenes"][0]["detections"][0].pop("lon"), "lon absent"),
])
def test_sentinel_results_refused(modif, motif):
    r = copy.deepcopy(S1)
    modif(r)
    with pytest.raises(validation.Refus, match=motif):
        validation.valider("sentinel", TACHE_S1, r, RULES)


def test_not_an_object_and_unknown_type():
    with pytest.raises(validation.Refus, match="objet JSON"):
        validation.valider("viirs", TACHE_VIIRS, [1, 2], RULES)
    with pytest.raises(validation.Refus, match="inconnu"):
        validation.valider("autre", {}, {}, RULES)


# Route de l'API, sur la base de test

@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL, autocommit=True)
    c.execute("DELETE FROM travailleurs")
    yield c
    c.execute("DELETE FROM travailleurs")
    c.close()


def _client():
    import asyncpg
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(ROOT / "backend"))
    from travailleurs import router

    async def init(c):
        for t in ("json", "jsonb"):
            await c.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

    @asynccontextmanager
    async def vie(app):
        app.state.pool = await asyncpg.create_pool(URL, init=init, min_size=1, max_size=2)
        yield
        await app.state.pool.close()
    app = FastAPI(lifespan=vie)
    app.include_router(router)
    return TestClient(app)


def test_route_refuses_forged_results_and_records_why(conn):
    wid = conn.execute(
        "INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, etat) VALUES "
        "('viirs', 'DEV1-M', 'fr-par-1', %s, %s, 'demarre') RETURNING id",
        (hashlib.sha256(b"jv").hexdigest(), json.dumps(TACHE_VIIRS))).fetchone()[0]
    faux = copy.deepcopy(VIIRS)
    faux["granules"][0]["detections"][0]["lon"] = 30.0          # navire « sombre » inventé en mer Noire
    with _client() as client:
        r = client.post(f"/api/travailleurs/{wid}/resultats", json=faux, headers={"Authorization": "Bearer jv"})
        assert r.status_code == 422 and "hors de nos zones" in r.json()["detail"]
        # Travailleur en échec : plus rien n'est accepté de lui
        r = client.post(f"/api/travailleurs/{wid}/resultats", json=VIIRS, headers={"Authorization": "Bearer jv"})
        assert r.status_code == 409
    etat, erreur, resultat = conn.execute("SELECT etat, erreur, resultat FROM travailleurs WHERE id = %s", (wid,)).fetchone()
    assert etat == "echec" and erreur.startswith("résultats refusés") and resultat is None


def test_route_keeps_genuine_results(conn):
    wid = conn.execute(
        "INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, etat) VALUES "
        "('viirs', 'DEV1-M', 'fr-par-1', %s, %s, 'demarre') RETURNING id",
        (hashlib.sha256(b"jv2").hexdigest(), json.dumps(TACHE_VIIRS))).fetchone()[0]
    with _client() as client:
        r = client.post(f"/api/travailleurs/{wid}/resultats", json=VIIRS, headers={"Authorization": "Bearer jv2"})
        assert r.status_code == 200
    assert conn.execute("SELECT etat FROM travailleurs WHERE id = %s", (wid,)).fetchone()[0] == "resultats"
