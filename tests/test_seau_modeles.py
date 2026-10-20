"""Seau des poids des modèles (essai Sentinel 2 du 20/10/2026, travailleur 29 : 403 AccessDenied au téléchargement des
poids, le jeton R2 principal étant filtré sur l'IP du serveur). Adresses remises aux travailleurs présignées par le
jeton dédié du seau des modèles, jamais par le jeton principal ; contrôle d'accès avant Docker (état « acces ») ;
copie des poids vers ce seau et nettoyage de l'ancienne copie ; diagnostic ; refus de R2 lisible chez le travailleur.
Routes de l'API sur la base de test (MARS_TEST_DATABASE_URL), le reste sans base."""
import hashlib
import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from mars import diagnostic
from mars import sentinel2 as S2
from mars.config import ROOT
from mars.r2 import R2
from mars.r2_signature import modeles_manquantes, r2_modeles_get_url

ENDPOINT = "https://compte.eu.r2.cloudflarestorage.com"
CLE = "modeles/sentinel2/a/best.ckpt"
XML_403 = (b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code><Message>Access Denied</Message>'
           b'</Error>')


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("R2_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "CLEPRINCIPALE")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secretprincipal")
    monkeypatch.setenv("R2_BUCKET", "mars-c2")
    monkeypatch.setenv("R2_MODELES_BUCKET", "mars-c2-modeles")
    monkeypatch.setenv("R2_MODELES_ACCESS_KEY_ID", "CLEMODELES")
    monkeypatch.setenv("R2_MODELES_SECRET_ACCESS_KEY", "secretmodeles")


@pytest.mark.usefixtures("env")
def test_worker_addresses_are_signed_by_the_models_token_only(monkeypatch):
    u = urlparse(r2_modeles_get_url(CLE, expires=60))
    q = parse_qs(u.query)
    assert u.netloc == "compte.eu.r2.cloudflarestorage.com" and u.path == f"/mars-c2-modeles/{CLE}"
    assert q["X-Amz-Credential"][0].startswith("CLEMODELES/") and q["X-Amz-Expires"] == ["60"]
    assert "CLEPRINCIPALE" not in u.geturl()
    # Le backend n'a plus aucun moyen de présigner une adresse de travailleur par le jeton principal
    assert "r2_get_url" not in (ROOT / "backend" / "travailleurs.py").read_text()
    monkeypatch.delenv("R2_MODELES_SECRET_ACCESS_KEY")
    assert modeles_manquantes() == ["R2_MODELES_SECRET_ACCESS_KEY"] and r2_modeles_get_url(CLE) is None


# Faux seaux S3 (boto3 : head_object, get_object, upload_fileobj, delete_object, liste)

class FauxSeau:
    def __init__(self, objets=None):
        self.objets = dict(objets or {})
        self.supprimes = []

    def head_object(self, Key, **_kw):
        from botocore.exceptions import ClientError
        if Key not in self.objets:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": len(self.objets[Key])}

    def get_object(self, Key, **_kw):
        return {"Body": io.BytesIO(self.objets[Key])}

    def upload_fileobj(self, f, _bucket, key, **_kw):
        self.objets[key] = b"".join(iter(lambda: f.read(1 << 16), b""))

    def delete_object(self, Key, **_kw):
        self.supprimes.append(Key)
        self.objets.pop(Key, None)

    def get_paginator(self, _nom):
        seau = self

        class P:
            def paginate(self, Prefix, **_kw):
                return [{"Contents": [{"Key": k, "Size": len(v), "LastModified": None}
                                      for k, v in seau.objets.items() if k.startswith(Prefix)]}]
        return P()


POIDS = b"poids du modele " * 4096


def _s2(contenu=POIDS):
    return {"source_poids": "https://ai2.test", "poids": [{"chemin": "projects/a/data/best.ckpt", "r2": CLE,
            "sha256": hashlib.sha256(contenu).hexdigest(), "octets": len(contenu)}]}


class Session:
    def __init__(self, contenu=POIDS, code=206, texte=""):
        self.contenu, self.code, self.texte, self.demandes = contenu, code, texte, []

    def get(self, url, **kw):
        self.demandes.append((url, kw))
        s = self

        class Rep:
            status_code, text, raw = s.code, s.texte, io.BytesIO(s.contenu)

            def raise_for_status(self):
                pass

            def close(self):
                pass
        return Rep()


def test_weights_are_copied_from_the_main_bucket_then_the_old_copy_is_removed_after_rereading():
    principal, modeles = FauxSeau({CLE: POIDS}), FauxSeau()
    p, m = R2(principal, "mars-c2"), R2(modeles, "mars-c2-modeles")
    session = Session()
    r = S2.copier_poids(_s2(), p, m, session, log=lambda _t: None)
    assert r == {"copies": 1, "deja": 0, "retires": 0, "octets": len(POIDS)}
    assert modeles.objets[CLE] == POIDS and CLE in principal.objets and session.demandes == []   # pas d'AI2
    # Second passage avec --nettoyer : nouvelle copie relue et vérifiée, puis ancienne retirée ; rien n'est recopié
    r = S2.copier_poids(_s2(), p, m, session, nettoyer=True, log=lambda _t: None)
    assert r == {"copies": 0, "deja": 1, "retires": 1, "octets": 0} and principal.supprimes == [CLE]
    assert S2.copier_poids(_s2(), p, m, session, nettoyer=True, log=lambda _t: None)["retires"] == 0


def test_without_an_old_copy_weights_come_from_ai2_and_a_wrong_hash_is_removed():
    modeles = FauxSeau()
    m = R2(modeles, "mars-c2-modeles")
    session = Session()
    assert S2.copier_poids(_s2(), R2(FauxSeau(), "mars-c2"), m, session, log=lambda _t: None)["copies"] == 1
    assert session.demandes[0][0] == "https://ai2.test/projects/a/data/checkpoints/best.ckpt"
    faux = FauxSeau()
    with pytest.raises(RuntimeError, match="empreinte ou taille fausse"):
        S2.copier_poids(_s2(), R2(FauxSeau(), "mars-c2"), R2(faux, "b"), Session(contenu=POIDS[:-1] + b"x"),
                        log=lambda _t: None)
    assert faux.objets == {} and faux.supprimes == [CLE]


def test_cleaning_keeps_the_old_copy_when_the_new_one_is_corrupt():
    principal = FauxSeau({CLE: POIDS})
    modeles = FauxSeau({CLE: POIDS[:-1] + b"x"})          # même taille, contenu altéré
    with pytest.raises(RuntimeError, match="ancienne copie gardée"):
        S2.copier_poids(_s2(), R2(principal, "mars-c2"), R2(modeles, "m"), Session(), nettoyer=True,
                        log=lambda _t: None)
    assert principal.supprimes == [] and CLE in principal.objets


def _etats(lignes):
    return {r: s for s, r, _d in lignes}


@pytest.mark.usefixtures("env")
def test_diagnostic_names_what_is_missing_in_each_container(monkeypatch):
    rules = {"sentinel2": _s2()}
    ok = {k: True for k in ("R2_ENDPOINT", "R2_MODELES_BUCKET", "R2_MODELES_ACCESS_KEY_ID",
                            "R2_MODELES_SECRET_ACCESS_KEY")}
    modeles, principal = FauxSeau({CLE: POIDS}), FauxSeau({CLE: POIDS})
    session = Session()
    lignes = diagnostic.sentinel2(rules, session, lambda: ok, R2(modeles, "mars-c2-modeles"), R2(principal, "mars-c2"))
    e = _etats(lignes)
    assert diagnostic.MANQUE not in e.values(), lignes
    assert e["Poids a"] == diagnostic.PRET and e["Adresse présignée des poids"] == diagnostic.PRET
    assert e["Ancienne copie des poids"] == diagnostic.INFO
    url, kw = session.demandes[0]
    assert "CLEMODELES" in url and kw["headers"] == {"Range": "bytes=0-0"}
    rapport = diagnostic.report(lignes)
    assert "secretmodeles" not in rapport and "CLEMODELES" not in rapport            # jamais une valeur
    # Variable absente du backend, poids absent, adresse refusée
    lignes = diagnostic.sentinel2(rules, Session(code=403, texte=XML_403.decode()),
                                  lambda: {**ok, "R2_MODELES_ACCESS_KEY_ID": False}, R2(FauxSeau(), "m"), None)
    e = _etats(lignes)
    assert e["R2_MODELES_ACCESS_KEY_ID (backend)"] == diagnostic.MANQUE and e["Poids a"] == diagnostic.MANQUE
    # Variable absente du conteneur taches : MANQUE, et aucun appel à R2
    monkeypatch.delenv("R2_MODELES_BUCKET")
    e = _etats(diagnostic.sentinel2(rules, Session(), lambda: ok, None, None))
    assert e["R2_MODELES_BUCKET (taches)"] == diagnostic.MANQUE and "Seau des modèles" not in e


def _worker():
    spec = importlib.util.spec_from_file_location("travailleur_sentinel2_r2", ROOT / "travailleurs" / "sentinel2.py")
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


def test_worker_reports_the_r2_refusal_with_its_code_and_message(tmp_path, monkeypatch):
    w = _worker()
    assert w.refus_r2(403, XML_403) == "R2 refuse la lecture (HTTP 403 AccessDenied : Access Denied)"
    assert w.refus_r2(502, b"<html>") == "R2 refuse la lecture (HTTP 502)"

    def refus(req, **_kw):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(XML_403))
    monkeypatch.setattr(w.urllib.request, "urlopen", refus)
    tache = {"poids": [{"chemin": "projects/a/best.ckpt", "url": "https://r2.test/x?X-Amz-Signature=abc",
                        "sha256": "0", "octets": 1}]}
    with pytest.raises(w.Arret) as e:
        w.poids(tache, str(tmp_path), _Tr())
    assert str(e.value) == "poids projects/a/best.ckpt : R2 refuse la lecture (HTTP 403 AccessDenied : Access Denied)"
    assert "Signature" not in str(e.value)


# Routes de l'API sur la base de test

@pytest.mark.usefixtures("env")
def test_access_check_and_delivery_use_the_models_bucket(monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_cles_travailleur import URL, _client
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    monkeypatch.setenv("CDSE_TRAVAILLEUR_ACCESS_KEY", "KTRAV")
    monkeypatch.setenv("CDSE_TRAVAILLEUR_SECRET_KEY", "SECRETTRAV")
    c = psycopg.connect(URL, autocommit=True)
    try:
        params = {"scenes": [], "poids": _s2()["poids"]}
        wid = c.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, etat) VALUES "
                        "('sentinel2', 'GP1-S', 'fr-par-2', %s, %s, 'demarre') RETURNING id",
                        (hashlib.sha256(b"jeton-bon").hexdigest(), json.dumps(params))).fetchone()[0]
        url, h = f"/api/travailleurs/{wid}/etat", {"Authorization": "Bearer jeton-bon"}
        with _client() as client:
            v = client.get("/api/travailleurs/variables").json()
            assert v["R2_MODELES_BUCKET"] is True and v["CDSE_TRAVAILLEUR_ACCESS_KEY"] is True
            assert "secretmodeles" not in json.dumps(v)
            assert client.post(url, json={"etat": "acces"}, headers={"Authorization": "Bearer faux"}).status_code == 403
            a = client.post(url, json={"etat": "acces"}, headers=h).json()["adresses"]
            assert [x["nom"] for x in a] == ["poids projects/a/data/best.ckpt"]
            q = parse_qs(urlparse(a[0]["url"]).query)
            assert q["X-Amz-Credential"][0].startswith("CLEMODELES/") and q["X-Amz-Expires"] == ["300"]
            assert "/mars-c2-modeles/" in a[0]["url"] and "CLEPRINCIPALE" not in a[0]["url"]
            # Seau des modèles absent du backend : contrôle et remise refusés en 503, tâche non marquée remise
            monkeypatch.delenv("R2_MODELES_ACCESS_KEY_ID")
            r = client.post(url, json={"etat": "acces"}, headers=h)
            assert r.status_code == 503 and "R2_MODELES_ACCESS_KEY_ID" in r.json()["detail"]
            assert client.post(url, json={"etat": "tache"}, headers=h).status_code == 503
            monkeypatch.setenv("R2_MODELES_ACCESS_KEY_ID", "CLEMODELES")
            t = client.post(url, json={"etat": "tache"}, headers=h).json()          # le contrôle n'a rien consommé
            q = parse_qs(urlparse(t["poids"][0]["url"]).query)
            assert q["X-Amz-Credential"][0].startswith("CLEMODELES/") and q["X-Amz-Expires"] == ["10800"]
        row = c.execute("SELECT parametres::text FROM travailleurs WHERE id = %s", (wid,)).fetchone()[0]
        assert "X-Amz" not in row                                                   # aucune adresse en base
    finally:
        c.execute("DELETE FROM travailleurs")
        c.close()
