"""Lot Z6, revue des échecs Sentinel 2 du 20/10/2026 sur le passage 325 : chaque cause rejouée sur le vrai chemin,
sans essai payant.

* Analyse 98, travailleur 29 : 403 AccessDenied au téléchargement des poids. Le jeton R2 principal est filtré sur
  l'IP du serveur ; une adresse qu'il présigne est refusée depuis toute autre IP, donc depuis un travailleur. Ici, un
  faux R2 local vérifie la signature des adresses présignées (AWS version 4, signature dans la requête) et refuse le
  jeton principal comme le fait le filtre IP vu d'un travailleur. Le serveur présigne par sa vraie fonction
  (`mars.r2_signature`), le travailleur télécharge par son vrai code (`travailleurs/sentinel2.py`, `poids`), et le vrai
  script de démarrage lit un octet de chaque adresse avec le vrai `curl`, avant Docker.
* Analyse 99, travailleur 30 (et l'essai qui l'a précédé) : EnvError de rasterio, qui refuse les clés AWS passées en
  options GDAL. Le vrai chemin (image rslearn d'AI2, rasterio et GDAL réels, faux S3 qui vérifie chaque signature) est
  `scripts/verifier_images.sh s2-s3` ; ici, le choix du chemin des clés (`tests/test_lectures_s3.py`) et l'absence de
  toute clé AWS dans les options GDAL du travailleur.

Ce qu'aucun de ces essais ne prouve, faute d'un vrai travailleur : que R2 accepte le jeton des modèles depuis une IP
de Scaleway (le filtre IP n'existe pas sur ce jeton, mais seul R2 en décide), que eodata accepte les lectures signées
du travailleur, et les durées, octets et mémoire sur un vrai GP1-S."""
import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mars import r2_signature

ROOT = Path(__file__).resolve().parents[1]
POIDS = b"poids du modele d'AI2 " * 5000
CLE_OBJET = "modeles/sentinel2/projects/a/best.ckpt"
JETONS = {"CLEMODELES": "secretmodeles", "CLEPRINCIPALE": "secretprincipal"}
FILTRE_IP = {"CLEPRINCIPALE"}         # jeton principal : filtré sur l'IP du serveur, refusé vu d'un travailleur


def _signature_presignee(methode: str, chemin: str, hote: str) -> str:
    """Contrôle d'une adresse présignée comme R2 : '' si elle est valide et admise, sinon le code d'erreur S3."""
    u = urllib.parse.urlsplit(chemin)
    q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    try:
        cle, jour, region, service, _ = q["X-Amz-Credential"].split("/")
        recue = q.pop("X-Amz-Signature")
    except (KeyError, ValueError):
        return "AccessDenied"
    if cle not in JETONS:
        return "InvalidAccessKeyId"
    if cle in FILTRE_IP:
        return "AccessDenied"
    query = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                     for k, v in sorted(q.items()))
    canon = "\n".join([methode, urllib.parse.quote(urllib.parse.unquote(u.path), safe="/-_.~"), query,
                       f"host:{hote}\n", "host", "UNSIGNED-PAYLOAD"])
    scope = f"{jour}/{region}/{service}/aws4_request"
    a_signer = "\n".join(["AWS4-HMAC-SHA256", q["X-Amz-Date"], scope, hashlib.sha256(canon.encode()).hexdigest()])
    k = ("AWS4" + JETONS[cle]).encode()
    for part in (jour, region, service, "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    ok = hmac.compare_digest(hmac.new(k, a_signer.encode(), hashlib.sha256).hexdigest(), recue)
    return "" if ok else "SignatureDoesNotMatch"


class Local:
    """Faux R2 (seau des modèles, GET avec plage) et faux retour des travailleurs (POST …/etat, journal, traces)."""

    def __init__(self):
        self.etats, self.lectures, self.adresses = [], [], []
        local = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                local.lectures.append(self.path.split("?")[0])
                motif = _signature_presignee("GET", self.path, self.headers.get("Host", ""))
                if motif:
                    xml = (f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{motif}</Code>'
                           "<Message>Access Denied</Message></Error>").encode()
                    self.send_response(403)
                    self.send_header("Content-Type", "application/xml")
                    self.send_header("Content-Length", str(len(xml)))
                    self.end_headers()
                    self.wfile.write(xml)
                    return
                plage = self.headers.get("Range")
                a, b = 0, len(POIDS) - 1
                if plage and plage.startswith("bytes="):
                    x, _, y = plage[6:].partition("-")
                    a, b = int(x), min(int(y or b), b)
                self.send_response(206 if plage else 200)
                self.send_header("Content-Length", str(b - a + 1))
                self.end_headers()
                self.wfile.write(POIDS[a:b + 1])

            def do_POST(self):
                corps = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                reponse = b'{"ok": true}'
                if self.path.endswith("/etat"):
                    etat = json.loads(corps or b"{}")
                    local.etats.append(etat)
                    if etat.get("etat") == "acces":
                        reponse = json.dumps({"adresses": local.adresses}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(reponse)))
                self.end_headers()
                self.wfile.write(reponse)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def local(monkeypatch):
    srv = Local()
    monkeypatch.setenv("R2_ENDPOINT", srv.url)
    monkeypatch.setenv("R2_BUCKET", "mars-c2")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "CLEPRINCIPALE")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", JETONS["CLEPRINCIPALE"])
    monkeypatch.setenv("R2_MODELES_BUCKET", "mars-c2-modeles")
    monkeypatch.setenv("R2_MODELES_ACCESS_KEY_ID", "CLEMODELES")
    monkeypatch.setenv("R2_MODELES_SECRET_ACCESS_KEY", JETONS["CLEMODELES"])
    yield srv
    srv.srv.shutdown()


def _travailleur():
    spec = importlib.util.spec_from_file_location("travailleur_sentinel2_z6", ROOT / "travailleurs" / "sentinel2.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Tr:
    def span(self, *_a, **_k):
        class Sp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def set(self, **_k):
                pass

            def erreur(self, _m):
                pass
        return Sp()


def _tache(url: str) -> dict:
    return {"poids": [{"chemin": "projects/a/best.ckpt", "url": url, "sha256": hashlib.sha256(POIDS).hexdigest(),
                       "octets": len(POIDS)}]}


def test_cause_98_main_token_address_is_refused_from_a_worker_on_the_real_download_path(local, tmp_path):
    """Ce que le travailleur 29 a vécu : adresse présignée par le jeton principal, refusée hors de l'IP du serveur."""
    w = _travailleur()
    url = r2_signature.presign("GET", urllib.parse.urlsplit(local.url).netloc, f"/mars-c2/{CLE_OBJET}",
                               "CLEPRINCIPALE", JETONS["CLEPRINCIPALE"], scheme="http")
    with pytest.raises(w.Arret) as e:
        w.poids(_tache(url), str(tmp_path), _Tr())
    assert str(e.value) == ("poids projects/a/best.ckpt : R2 refuse la lecture (HTTP 403 AccessDenied : "
                            "Access Denied)")
    assert "Signature" not in str(e.value)


def test_cause_98_fixed_models_token_address_downloads_and_checks_the_weights(local, tmp_path):
    """Correctif : seule l'adresse du seau des modèles, par son jeton, est remise ; signature acceptée, empreinte
    vérifiée. Une adresse dont la signature est abîmée reste refusée (le faux R2 vérifie vraiment)."""
    w = _travailleur()
    url = r2_signature.r2_modeles_get_url(CLE_OBJET, expires=300)
    assert url.startswith(f"{local.url}/mars-c2-modeles/") and "CLEMODELES%2F" in url
    r = w.poids(_tache(url), str(tmp_path), _Tr())
    assert r["octets"] == len(POIDS) and (tmp_path / "projects/a/best.ckpt").read_bytes() == POIDS
    abimee = url[:-4] + ("0000" if not url.endswith("0000") else "1111")
    with pytest.raises(w.Arret, match="SignatureDoesNotMatch"):
        w.poids(_tache(abimee), str(tmp_path / "x"), _Tr())


DATE = shutil.which("gdate") or "date"
NANO = subprocess.run([DATE, "+%N"], capture_output=True, text=True).stdout.strip().isdigit()


def _demarrage(tmp_path: Path, local: Local, adresses: list) -> dict:
    """Vrai script de démarrage, vrai curl (vers le faux retour et le faux R2) ; seuls ip, docker et shutdown simulés."""
    import test_demarrage as D
    local.adresses = adresses
    mars = tmp_path / "mars"
    mars.mkdir()
    shutil.copy(ROOT / "travailleurs" / "traces.sh", mars / "traces.sh")
    (mars / "env").write_text("")
    (mars / "demarrage.env").write_text(
        f'RETOUR="{local.url}/api/travailleurs/29"\nJETON="j"\nMAC="{D.MAC}"\nIMAGE="img"\n'
        f'SCRIPT="sentinel2.py"\nDOCKER_OPTS="--shm-size=16g"\nTRACEPARENT="{D.TP}"\n')
    net = tmp_path / "net" / "eth9"
    net.mkdir(parents=True)
    (net / "address").write_text(D.MAC + "\n")
    b = D._outils(tmp_path, 0, "analyse terminée")
    (b / "curl").unlink()                                       # le vrai curl
    env = {**os.environ, "PATH": f"{b}:{os.environ['PATH']}", "MARS_DIR": str(mars), "MARS_LOG": str(tmp_path / "log"),
           "MARS_CONSOLE": "/dev/null", "MARS_NET_DIR": str(tmp_path / "net"), "SPANS": str(tmp_path / "spans.json")}
    p = subprocess.run(["bash", str(ROOT / "travailleurs" / "demarrage.sh")], env=env, capture_output=True, text=True,
                       timeout=90)
    docker = (tmp_path / "docker.log").read_text() if (tmp_path / "docker.log").exists() else ""
    return {"code": p.returncode, "docker": docker, "etats": local.etats, "log": (tmp_path / "log").read_text()}


@pytest.mark.skipif(not NANO, reason="date sans nanosecondes (installer coreutils : gdate)")
def test_cause_98_boot_script_stops_before_docker_on_the_real_curl_path(local, tmp_path):
    """Le refus est vu dès le réseau privé joint, avant Docker et l'image de 7,2 Go (4 min 23 s au travailleur 29)."""
    url = r2_signature.presign("GET", urllib.parse.urlsplit(local.url).netloc, f"/mars-c2/{CLE_OBJET}",
                               "CLEPRINCIPALE", JETONS["CLEPRINCIPALE"], scheme="http")
    r = _demarrage(tmp_path, local, [{"nom": "poids projects/a/best.ckpt", "url": url}])
    assert r["code"] == 1 and r["docker"] == ""                                    # ni Docker, ni image, ni analyse
    fin = r["etats"][-1]
    assert fin["etat"] == "echec" and fin["etape"] == "controle_r2"
    assert fin["message"] == ("poids projects/a/best.ckpt : R2 refuse la lecture (HTTP 403 AccessDenied : "
                              "Access Denied)")
    assert "Signature" not in json.dumps(r["etats"])


@pytest.mark.skipif(not NANO, reason="date sans nanosecondes (installer coreutils : gdate)")
def test_cause_98_fixed_boot_script_reads_one_byte_then_runs_the_analysis(local, tmp_path):
    url = r2_signature.r2_modeles_get_url(CLE_OBJET, expires=300)
    r = _demarrage(tmp_path, local, [{"nom": "poids projects/a/best.ckpt", "url": url}])
    assert r["code"] == 0 and "run --rm --shm-size=16g" in r["docker"]
    assert "R2 : 1 adresse(s) lisible(s) depuis le travailleur" in r["log"]
    assert any(x.startswith("/mars-c2-modeles/") for x in local.lectures)


@pytest.mark.parametrize("avec_boto3", [True, False])
def test_cause_99_no_aws_key_is_ever_a_gdal_option_in_the_worker(monkeypatch, avec_boto3, tmp_path):
    """EnvError du travailleur 30 : rasterio refuse AWS_ACCESS_KEY_ID et AWS_SECRET_ACCESS_KEY en options de
    rasterio.Env. Le vrai `env_gdal` du travailleur est exécuté avec un rasterio simulé qui lève la même erreur que le
    vrai dans ce cas : avec boto3, les clés passent par une AWSSession ; sans boto3, par l'environnement le temps de la
    lecture, retirées ensuite. Vrai rasterio et vrai GDAL : verifier_images.sh s2-s3."""
    import sys
    import types
    vus = {}

    class EnvError(Exception):
        pass

    class Env:
        def __init__(self, session=None, **opts):
            if {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"} & set(opts):
                raise EnvError("GDAL's AWS config options can not be directly set. AWS credentials are handled "
                               "exclusively by boto3.")
            vus.update(session=session, opts=opts)

        def __enter__(self):
            vus["env_pendant"] = {k: os.environ.get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")}
            return self

        def __exit__(self, *_a):
            return False

    class AWSSession:
        def __init__(self, **kw):
            self.kw = kw

    class DummySession:
        pass
    rio = types.ModuleType("rasterio")
    rio.Env = Env
    sess = types.ModuleType("rasterio.session")
    sess.AWSSession, sess.DummySession = AWSSession, DummySession
    monkeypatch.setitem(sys.modules, "rasterio", rio)
    monkeypatch.setitem(sys.modules, "rasterio.session", sess)
    if avec_boto3:
        monkeypatch.setitem(sys.modules, "boto3", types.ModuleType("boto3"))
    else:
        monkeypatch.setitem(sys.modules, "boto3", None)              # import boto3 : ImportError
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    w = _travailleur()
    w._S3.update({"access_key": "CLECDSE", "secret_key": "secretcdse", "endpoint": "eodata.dataspace.copernicus.eu"})
    with w.env_gdal(str(tmp_path / "journal")):
        pass
    assert not {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"} & set(vus["opts"])
    assert vus["opts"]["AWS_S3_ENDPOINT"] == "eodata.dataspace.copernicus.eu" and vus["opts"]["AWS_VIRTUAL_HOSTING"] == "FALSE"
    if avec_boto3:
        assert isinstance(vus["session"], AWSSession) and vus["session"].kw["aws_access_key_id"] == "CLECDSE"
        assert vus["env_pendant"] == {"AWS_ACCESS_KEY_ID": None, "AWS_SECRET_ACCESS_KEY": None}
    else:
        assert vus["env_pendant"] == {"AWS_ACCESS_KEY_ID": "CLECDSE", "AWS_SECRET_ACCESS_KEY": "secretcdse"}
    assert os.environ.get("AWS_ACCESS_KEY_ID") is None and os.environ.get("AWS_SECRET_ACCESS_KEY") is None
