"""`taches.py sentinel-diagnostic` : lecture seule (aucune méthode autre que GET chez Scaleway), une ligne par point,
PRÊT, MANQUE ou À VÉRIFIER, avec de faux clients Copernicus et Scaleway."""
import json
from datetime import datetime, timezone
from pathlib import Path

from mars import diagnostic as D
from mars.config import load_rules
from mars.travailleurs import Scaleway

RULES = load_rules()
NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
ITEM = json.loads((Path(__file__).parent / "donnees" / "stac_s1_cog.json").read_text())


class Resp:
    def __init__(self, code, body=None):
        self.status_code, self._b = code, body
        self.content = b"x" if body is not None else b""
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        if self._b is None:
            raise ValueError
        return self._b

    def raise_for_status(self):
        pass

    def close(self):
        pass


class Copernicus:
    def __init__(self, read=206):
        self.read, self.gets = read, []

    def post(self, _url, **_kw):
        return Resp(200, {"features": [ITEM]})

    def get(self, url, **_kw):
        self.gets.append(url)
        return Resp(self.read)


class ScalewayApi:
    """Faux Scaleway : réponses par chemin ; toute méthode autre que GET fait échouer le test."""
    headers = {}

    def __init__(self, **over):
        self.calls = []
        self.routes = {
            "/instance/v1/zones/fr-par-2/servers": (200, {"servers": []}),
            "/marketplace/v2/local-images": (200, {"local_images": [{"id": "gpuos", "arch": "x86_64", "type": "instance_sbs",
                                                                     "compatible_commercial_types": ["L4-1-24G"]}]}),
            "/instance/v1/zones/fr-par-2/images/gpuos": (200, {"image": {"id": "gpuos", "name": "Ubuntu Noble GPU OS 13",
                "arch": "x86_64", "root_volume": {"id": "snap", "volume_type": "sbs_snapshot", "size": 0}}}),
            "/block/v1alpha1/zones/fr-par-2/snapshots/snap": (200, {"id": "snap", "size": 20_000_000_000}),
            "/instance/v1/zones/fr-par-2/products/servers/availability": (200, {"servers": {"L4-1-24G": {"availability": "available"}}}),
            "/account/v3/projects/p": (200, {"organization_id": "org"}),
            "/iam/v1alpha1/quota": (200, {"quota": [{"name": "instances_l4_1_24g", "limits": [{"zone": "fr-par-2", "limit": 1}]}]}),
            "/block/v1alpha1/zones/fr-par-2/volumes": (200, {"volumes": []}),
            "/iam/v1alpha1/api-keys/SCWACCESS": (200, {"application_id": "app"}),
            "/iam/v1alpha1/policies": (200, {"policies": [{"id": "pol"}]}),
            "/iam/v1alpha1/rules": (200, {"rules": [{"permission_set_names": ["InstancesFullAccess", "PrivateNetworksFullAccess",
                                                                                "BlockStorageFullAccess"]}]}),
            "/vpc/v2/regions/fr-par/private-networks/pn": (200, {"name": "mars-c2-travailleurs", "region": "fr-par"}),
        }
        self.routes.update(over)

    def request(self, method, url, **_kw):
        assert method == "GET", f"diagnostic en écriture : {method} {url}"
        path = url.replace("https://api.scaleway.com", "").split("?", 1)[0]
        self.calls.append(path)
        code, body = self.routes.get(path, (404, {"message": "inconnu"}))
        return Resp(code, body)


def _env(monkeypatch):
    for k, v in {"CDSE_ACCESS_KEY": "k", "CDSE_SECRET_KEY": "s", "SCW_PRIVATE_NETWORK_ID": "pn",
                 "MARS_IP_PRIVEE": "172.16.8.2", "SCW_ACCESS_KEY": "SCWACCESS"}.items():
        monkeypatch.setenv(k, v)


def _run(api, cop=None, rules=RULES):
    shown = []
    lines = D.run(rules, cop or Copernicus(), Scaleway("cle", "p", session=api, protected=set()), NOW, shown.append)
    return {r: (s, d) for s, r, d in lines}, shown


def test_everything_ready(monkeypatch):
    _env(monkeypatch)
    rules = {**RULES, "sentinel": {**RULES["sentinel"], "scenes_max_par_jour": 2}}
    lines, shown = _run(ScalewayApi(), rules=rules)
    assert all(s in (D.PRET, D.INFO) for s, _d in lines.values()), lines
    assert "16 octets lus de S1C_IW_GRDH_1SDV_20261010T180430" in lines["Clés Copernicus valides"][1]
    assert lines["Image GPU"][1].startswith("Ubuntu Noble GPU OS 13 (gpuos), volume racine de 20 Go")
    assert any('"sbs_snapshot"' in x for x in shown)                       # fiche brute affichée
    assert "Tout est prêt" in D.report(list((s, r, d) for r, (s, d) in lines.items()))


def test_missing_and_unreadable_points_are_named(monkeypatch):
    """Cas probable du 11/10 : clé sans droit de lecture IAM, stockage bloc refusé, image à taille nulle sans instantané
    lisible ; Sentinel arrêté (plafond à 0)."""
    _env(monkeypatch)
    api = ScalewayApi(**{"/block/v1alpha1/zones/fr-par-2/snapshots/snap": (403, {"message": "denied"}),
                         "/block/v1alpha1/zones/fr-par-2/volumes": (403, {"message": "denied"}),
                         "/account/v3/projects/p": (403, {"message": "denied"}),
                         "/iam/v1alpha1/api-keys/SCWACCESS": (403, {"message": "denied"})})
    rules = {**RULES, "sentinel": {**RULES["sentinel"], "scenes_max_par_jour": 0}}
    lines, shown = _run(api, rules=rules)
    assert lines["Image GPU"][0] == D.MANQUE and "taille du volume racine inconnue" in lines["Image GPU"][1]
    assert any("Fiche brute de l'image gpuos" in x for x in shown)
    assert lines["Stockage bloc"][0] == D.MANQUE and "BlockStorageFullAccess" in lines["Stockage bloc"][1]
    assert lines["Quota L4-1-24G"][0] == D.A_VERIFIER and lines["Droits de la clé"][0] == D.A_VERIFIER
    assert lines["Plafond de scènes"][0] == D.INFO and "Sentinel arrêté" in lines["Plafond de scènes"][1]
    assert lines["Clés Copernicus valides"][0] == D.PRET


def test_copernicus_is_checked_first_and_refused_keys_are_reported(monkeypatch):
    _env(monkeypatch)
    lines, _ = _run(ScalewayApi(), Copernicus(read=403))
    assert lines["Clés Copernicus valides"][0] == D.MANQUE and "HTTP 403" in lines["Clés Copernicus valides"][1]
    monkeypatch.delenv("CDSE_SECRET_KEY")
    lines, _ = _run(ScalewayApi())
    assert lines["Clés Copernicus"] == (D.MANQUE, "absentes du .env : CDSE_SECRET_KEY")
