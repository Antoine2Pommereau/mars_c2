"""Lectures S3 réelles contre un faux S3 qui vérifie la signature AWS version 4 (travailleurs/faux_s3.py).

Après l'essai réel Sentinel 2 du 20/10/2026 (EnvError de rasterio sur les clés AWS en options GDAL, alors que les
essais lisaient des fichiers locaux ou un miroir sans clé), chaque bibliothèque de lecture S3 est éprouvée par son
chemin S3 réel, requête signée comprise, jamais seulement par le chemin des fichiers locaux :
* serveur, bibliothèque standard et requests : heure propre des tuiles (MTD_TL.xml, 4 Ko) et contrôle des clés ;
* travailleur Sentinel 1, urllib : téléchargement d'une image ;
* travailleur Sentinel 2, rasterio : remise des clés (AWSSession ou environnement) contrôlée ici avec un rasterio
  factice qui refuse les clés en options GDAL comme rasterio 1.4 ; la lecture réelle (GDAL, SCL et 13 bandes, image
  rslearn) est la cible s2-s3 de scripts/verifier_images.sh, obligatoire avant publication.
"""
import importlib.util
import os
import sys
import types
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
import requests

from mars.config import ROOT

sys.path.insert(0, str(ROOT / "travailleurs"))
import faux_s3  # noqa: E402

CLE, SECRET = "CLETESTS3", "SECRETTESTS3"
MTD = "eodata/Sentinel-2/MSI/L1C/2026/10/04/S2A_MSIL1C_20261004T110831_N0513_R137_T31UCR.SAFE/GRANULE/L1C_T31UCR/MTD_TL.xml"
IMAGE = "eodata/Sentinel-1/SAR/IW_GRDH_1S-COG/2026/10/14/S1D.SAFE/measurement/s1d-iw-grd-vv-cog.tiff"


def _module(nom: str, fichier: str):
    spec = importlib.util.spec_from_file_location(nom, ROOT / "travailleurs" / fichier)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def s3(tmp_path):
    mtd = tmp_path / MTD
    mtd.parent.mkdir(parents=True)
    mtd.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<n1:Level-1C_Tile_ID>\n<General_Info>\n'
                   '<SENSING_TIME metadataLevel="Standard">2026-10-04T11:08:31.024Z</SENSING_TIME>\n'
                   + "<x/>\n" * 3000 + "</General_Info>\n</n1:Level-1C_Tile_ID>\n")
    img = tmp_path / IMAGE
    img.parent.mkdir(parents=True)
    img.write_bytes(b"II*\x00" + bytes(range(256)) * 400)
    faux = faux_s3.FauxS3(CLE, SECRET, racine=str(tmp_path))
    port = faux.demarrer()
    yield faux, f"127.0.0.1:{port}"
    faux.arreter()


class _HttpLocal(requests.Session):
    """Le serveur lit eodata en https ; le faux S3 local répond en http, même hôte signé."""

    def request(self, method, url, *a, **kw):
        return super().request(method, url.replace("https://", "http://", 1), *a, **kw)


@pytest.fixture
def serveur(s3, monkeypatch):
    import mars.sentinel
    import mars.sentinel2
    faux, hote = s3
    for m in (mars.sentinel, mars.sentinel2):
        monkeypatch.setattr(m, "S3_HOST", hote)
    for var, val in (("CDSE_ACCESS_KEY", CLE), ("CDSE_SECRET_KEY", SECRET), ("CDSE_TRAVAILLEUR_ACCESS_KEY", CLE),
                     ("CDSE_TRAVAILLEUR_SECRET_KEY", SECRET)):
        monkeypatch.setenv(var, val)
    return faux


def test_serveur_lit_mtd_tl_par_s3_signe(serveur, monkeypatch):
    from mars.sentinel2 import lire_sensing
    href = "s3://" + MTD
    assert lire_sensing(_HttpLocal(), href) == datetime(2026, 10, 4, 11, 8, 31, 24000, tzinfo=timezone.utc)
    assert serveur.refus == 0 and serveur.envoyes == 4096, "4 premiers kilooctets seulement, signature acceptée"
    monkeypatch.setenv("CDSE_SECRET_KEY", "FAUX")
    assert lire_sensing(_HttpLocal(), href) is None
    assert serveur.refus == 1


def test_serveur_controle_les_cles_par_s3_signe(serveur, monkeypatch):
    from mars.sentinel import check_cdse
    assert check_cdse(_HttpLocal(), "s3://" + IMAGE) is None
    monkeypatch.setenv("CDSE_TRAVAILLEUR_SECRET_KEY", "FAUX")
    motif = check_cdse(_HttpLocal(), "s3://" + IMAGE)
    assert "HTTP 403" in motif and "SignatureDoesNotMatch" in motif and "FAUX" not in motif
    monkeypatch.setenv("CDSE_TRAVAILLEUR_ACCESS_KEY", "INCONNUE")
    assert "InvalidAccessKeyId" in check_cdse(_HttpLocal(), "s3://" + IMAGE)
    assert serveur.refus == 2 and serveur.envoyes == 16, "16 octets lus avec la bonne paire, deux refus"


def test_travailleur_sentinel1_telecharge_par_s3_signe(s3, tmp_path):
    faux, hote = s3
    w = _module("travailleur_sentinel_s3", "sentinel.py")
    w.TAILLE_MIN = 1000
    w._S3.update({"endpoint": hote, "http": True, "region": "default", "access_key": CLE, "secret_key": SECRET})
    n = w.download("s3://" + IMAGE, str(tmp_path / "vv.tiff"))
    assert n == (tmp_path / IMAGE).stat().st_size and faux.refus == 0
    w._S3["secret_key"] = "FAUX"
    with pytest.raises(w.Arret, match="HTTP 403"):
        w.download("s3://" + IMAGE, str(tmp_path / "vv2.tiff"))
    assert faux.refus == 1


# Travailleur Sentinel 2 : remise des clés à rasterio

class EnvError(Exception):
    pass


def _faux_rasterio(monkeypatch, journal: list):
    """rasterio factice : Env refuse les clés AWS en options, comme rasterio 1.4.4 de l'image rslearn."""
    rio = types.ModuleType("rasterio")
    session = types.ModuleType("rasterio.session")

    class DummySession:
        pass

    class AWSSession:
        def __init__(self, **kw):
            self.kw = kw

    @contextmanager
    def Env(session=None, **opts):
        if "AWS_ACCESS_KEY_ID" in opts or "AWS_SECRET_ACCESS_KEY" in opts:
            raise EnvError("GDAL's AWS config options can not be directly set. "
                           "AWS credentials are handled exclusively by boto3.")
        journal.append({"session": session, "opts": opts,
                        "environ": {k: os.environ.get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")}})
        yield

    rio.Env, rio.session = Env, session
    session.DummySession, session.AWSSession = DummySession, AWSSession
    monkeypatch.setitem(sys.modules, "rasterio", rio)
    monkeypatch.setitem(sys.modules, "rasterio.session", session)
    return session


def test_sentinel2_cles_par_aws_session_avec_boto3(monkeypatch):
    journal = []
    session = _faux_rasterio(monkeypatch, journal)
    monkeypatch.setitem(sys.modules, "boto3", types.ModuleType("boto3"))
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    w = _module("travailleur_sentinel2_s3a", "sentinel2.py")
    w._S3.update({"endpoint": "eodata.dataspace.copernicus.eu", "region": "default", "access_key": CLE,
                  "secret_key": SECRET})
    with w.env_gdal("/tmp/gdal.log"):
        pass
    (e,) = journal
    assert isinstance(e["session"], session.AWSSession)
    assert e["session"].kw == {"aws_access_key_id": CLE, "aws_secret_access_key": SECRET, "region_name": "default",
                               "endpoint_url": "eodata.dataspace.copernicus.eu"}
    assert e["opts"]["AWS_S3_ENDPOINT"] == "eodata.dataspace.copernicus.eu" and e["opts"]["AWS_VIRTUAL_HOSTING"] == "FALSE"
    assert CLE not in str(e["opts"]) and SECRET not in str(e["opts"])
    assert e["environ"]["AWS_ACCESS_KEY_ID"] is None, "pas de clé dans l'environnement quand boto3 est là"


def test_sentinel2_cles_par_environnement_sans_boto3(monkeypatch):
    journal = []
    session = _faux_rasterio(monkeypatch, journal)
    monkeypatch.setitem(sys.modules, "boto3", None)                    # import boto3 : ImportError
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "PRECEDENTE")
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    w = _module("travailleur_sentinel2_s3b", "sentinel2.py")
    w._S3.update({"access_key": CLE, "secret_key": SECRET})
    with pytest.raises(RuntimeError):
        with w.env_gdal("/tmp/gdal.log"):
            raise RuntimeError("lecture en échec")
    (e,) = journal
    assert isinstance(e["session"], session.DummySession)
    assert e["environ"] == {"AWS_ACCESS_KEY_ID": CLE, "AWS_SECRET_ACCESS_KEY": SECRET}
    assert CLE not in str(e["opts"]) and SECRET not in str(e["opts"])
    assert os.environ.get("AWS_ACCESS_KEY_ID") == "PRECEDENTE" and "AWS_SECRET_ACCESS_KEY" not in os.environ, \
        "environnement rétabli après la lecture, même en échec"
