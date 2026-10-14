"""Clés Copernicus dédiées aux travailleurs (14/10/2026) : Copernicus Data Space refuse les adresses présignées
(403 InvalidAccessKeyId, même avec des clés valides), le travailleur lit donc les images S3 lui même, signature par en
tête, avec une paire qui lui est remise une seule fois par le réseau privé ; la paire du serveur ne le quitte jamais.

Signatures vérifiées contre botocore ; route de remise, cloud-init et travailleur éprouvés (route et lancement sur la
base de test, MARS_TEST_DATABASE_URL)."""
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mars.config import ROOT, load_rules
from mars.r2_signature import EMPTY_SHA256, signed_headers

URL = os.environ.get("MARS_TEST_DATABASE_URL")
HOST = "eodata.dataspace.copernicus.eu"
PATH = "/eodata/Sentinel-1/SAR/IW_GRDH_1S-COG/2026/10/14/S1D_X_005015_0097A4_7746_COG.SAFE/measurement/s1d-iw-grd-vv-x-cog.tiff"


def _worker():
    spec = importlib.util.spec_from_file_location("travailleur_sentinel_cles", ROOT / "travailleurs" / "sentinel.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_header_signature_matches_botocore():
    from botocore.auth import S3SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials
    req = AWSRequest(method="GET", url=f"https://{HOST}{PATH}")
    req.headers["X-Amz-Content-SHA256"] = EMPTY_SHA256
    S3SigV4Auth(Credentials("AKIDEXEMPLE", "secretexemple"), "s3", "default").add_auth(req)
    now = datetime.strptime(req.headers["X-Amz-Date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    ours = signed_headers("GET", HOST, PATH, "AKIDEXEMPLE", "secretexemple", now=now)
    assert ours["Authorization"] == req.headers["Authorization"]
    # Le travailleur (bibliothèque standard, Python 3.8) signe exactement comme le serveur
    assert _worker().s3_headers(HOST, PATH, "AKIDEXEMPLE", "secretexemple", now=now) == ours


def test_worker_fetches_its_task_and_keeps_keys_out_of_it(monkeypatch):
    w = _worker()
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def urlopen(req, **_kw):
        calls.append(req)
        body = {"scenes": [{"id": "S1D_X", "vv": "s3://eodata" + PATH[len("/eodata"):]}], "confiance": 0.9,
                "identifiants": {"s3": {"endpoint": HOST, "region": "default", "access_key": "KTRAV",
                                        "secret_key": "SECRET"}}}
        return Resp(json.dumps(body).encode())
    monkeypatch.setattr(w.urllib.request, "urlopen", urlopen)
    task = w.fetch_task({"retour": "http://172.16.8.2:8090/api/travailleurs/7", "jeton": "jeton7",
                         "tache_par_le_reseau": True})
    req = calls[0]
    assert req.full_url == "http://172.16.8.2:8090/api/travailleurs/7/etat" and req.get_method() == "POST"
    assert json.loads(req.data) == {"etat": "tache"} and req.headers["Authorization"] == "Bearer jeton7"
    assert "identifiants" not in task and "SECRET" not in json.dumps(task) and task["scenes"][0]["id"] == "S1D_X"
    # Lecture d'une image : adresse sans signature, signature par en tête avec la clé remise
    r = w._request_for(task["scenes"][0]["vv"])
    assert r.full_url == f"https://{HOST}{PATH}" and "?" not in r.full_url
    assert r.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=KTRAV/")
    w._S3.clear()
    with pytest.raises(w.Arret, match="aucune clé S3 remise"):
        w._request_for(task["scenes"][0]["vv"])


def test_refused_task_stops_the_worker_with_its_reason(monkeypatch):
    w = _worker()

    def urlopen(_req, **_kw):
        raise w.urllib.error.HTTPError("u", 409, "conflit", {}, io.BytesIO(b'{"detail":"T\\u00e2che d\\u00e9j\\u00e0 remise"}'))
    monkeypatch.setattr(w.urllib.request, "urlopen", urlopen)
    with pytest.raises(w.Arret, match="HTTP 409"):
        w.fetch_task({"retour": "http://x/api/travailleurs/7", "jeton": "j", "tache_par_le_reseau": True})


# Sur la base de test : route de remise, lancement

@pytest.fixture
def conn(monkeypatch):
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    monkeypatch.setenv("MARS_IP_PRIVEE", "172.16.8.2")
    monkeypatch.setenv("SCW_PRIVATE_NETWORK_ID", "pn")
    c = psycopg.connect(URL, autocommit=True)
    for t in ("viirs_detections", "viirs_granules", "travailleurs"):
        c.execute(f"DELETE FROM {t}")
    c.execute("DELETE FROM task_runs WHERE task LIKE 'travailleur%'")
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
    async def lifespan(app):
        app.state.pool = await asyncpg.create_pool(URL, init=init, min_size=1, max_size=2)
        yield
        await app.state.pool.close()
    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    return TestClient(app)


def test_task_route_delivers_once_to_the_expected_worker(conn, monkeypatch):
    wid = conn.execute(
        "INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, etat) VALUES "
        "('sentinel', 'L4-1-24G', 'fr-par-2', %s, %s, 'demarre') RETURNING id",
        (hashlib.sha256(b"jeton-bon").hexdigest(), json.dumps({"scenes": [{"id": "S1D_X", "vv": "s3://eodata/x"}]}))).fetchone()[0]
    url = f"/api/travailleurs/{wid}/etat"
    monkeypatch.delenv("CDSE_TRAVAILLEUR_ACCESS_KEY", raising=False)
    monkeypatch.delenv("CDSE_TRAVAILLEUR_SECRET_KEY", raising=False)
    with _client() as client:
        assert client.post(url, json={"etat": "tache"}, headers={"Authorization": "Bearer faux"}).status_code == 403
        # Paire absente du .env : refus, rien n'est marqué remis (la demande pourra être refaite)
        r = client.post(url, json={"etat": "tache"}, headers={"Authorization": "Bearer jeton-bon"})
        assert r.status_code == 503 and "CDSE_TRAVAILLEUR_ACCESS_KEY" in r.json()["detail"]
        monkeypatch.setenv("CDSE_TRAVAILLEUR_ACCESS_KEY", "KTRAV")
        monkeypatch.setenv("CDSE_TRAVAILLEUR_SECRET_KEY", "SECRETTRAV")
        r = client.post(url, json={"etat": "tache"}, headers={"Authorization": "Bearer jeton-bon"})
        assert r.status_code == 200
        body = r.json()
        assert body["scenes"][0]["id"] == "S1D_X"
        assert body["identifiants"]["s3"] == {"endpoint": HOST, "region": "default", "access_key": "KTRAV",
                                              "secret_key": "SECRETTRAV"}
        # Une seule fois
        r = client.post(url, json={"etat": "tache"}, headers={"Authorization": "Bearer jeton-bon"})
        assert r.status_code == 409
        # Les signes de vie ordinaires passent toujours
        assert client.post(url, json={"etat": "demarre"}, headers={"Authorization": "Bearer jeton-bon"}).json() == {"ok": True}
    row = conn.execute("SELECT row_to_json(t)::text FROM travailleurs t WHERE id = %s", (wid,)).fetchone()[0]
    assert "SECRETTRAV" not in row and "KTRAV" not in row and "tache_remise_le" in row     # aucune clé en base


def test_sentinel_cloud_init_carries_neither_task_nor_keys(conn, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_travailleurs_base import FakeScw
    from mars import travailleurs as T
    monkeypatch.setenv("CDSE_TRAVAILLEUR_ACCESS_KEY", "KTRAV")
    monkeypatch.setenv("CDSE_TRAVAILLEUR_SECRET_KEY", "SECRETTRAV")
    scw = FakeScw()
    params = {"scenes": [{"id": "S1D_X", "vv": "s3://eodata/x/vv.tiff", "vh": "s3://eodata/x/vh.tiff"}]}
    out = T.launch(conn, scw, load_rules(), "sentinel", params, {}, sleep=lambda _s: None)
    assert "travailleur" in out, out
    wid = out["travailleur"]
    text = next(iter(scw.servers.values()))["cloud"]
    files = dict(re.findall(r"path: (\S+)\n    encoding: b64\n    permissions: '\d+'\n    content: (\S*)", text))
    task = json.loads(base64.b64decode(files["/mars/tache.json"]))
    assert set(task) == {"retour", "jeton", "script", "tache_par_le_reseau"} and task["tache_par_le_reseau"] is True
    assert base64.b64decode(files["/mars/env"]) == b""
    for secret in ("KTRAV", "SECRETTRAV", "s3://eodata/x/vv.tiff", "S1D_X"):
        assert secret not in text and all(secret not in base64.b64decode(v).decode("utf-8", "replace") for v in files.values())
    # La tâche (sans clé) est en base, pour la remise
    assert conn.execute("SELECT parametres FROM travailleurs WHERE id = %s", (wid,)).fetchone()[0] == params
