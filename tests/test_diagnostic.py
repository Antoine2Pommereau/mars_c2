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
    """Faux Copernicus : catalogue, et lecture S3 signée par en tête ; `refused` : clés d'accès refusées en 403."""

    def __init__(self, refused=()):
        self.refused, self.gets = set(refused), []

    def post(self, _url, **_kw):
        return Resp(200, {"features": [ITEM]})

    def get(self, url, headers=None, **_kw):
        auth = (headers or {}).get("Authorization", "")
        assert "?" not in url and auth.startswith("AWS4-HMAC-SHA256 Credential="), "signature par en tête attendue"
        key = auth.split("Credential=", 1)[1].split("/", 1)[0]
        self.gets.append((url, key))
        r = Resp(403 if key in self.refused else 206)
        if key in self.refused:
            r.text = "<?xml version='1.0'?><Error><Code>InvalidAccessKeyId</Code><Message>expired</Message></Error>"
        return r


class ScalewayApi:
    """Faux Scaleway : réponses par chemin ; toute méthode autre que GET fait échouer le test."""
    headers = {}

    def __init__(self, **over):
        self.calls = []
        self.routes = {
            "/instance/v1/zones/fr-par-2/servers": (200, {"servers": []}),
            "/marketplace/v2/local-images": (200, {"local_images": [{"id": "noble", "arch": "x86_64", "type": "instance_local",
                                                                     "compatible_commercial_types": ["GP1-S"]}]}),
            "/instance/v1/zones/fr-par-2/images/noble": (200, {"image": {"id": "noble", "name": "Ubuntu 24.04 Noble Numbat",
                "arch": "x86_64", "root_volume": {"id": "vol", "volume_type": "l_ssd", "size": 10_000_000_000}}}),
            "/instance/v1/zones/fr-par-2/products/servers/availability": (200, {"servers": {
                k: {"availability": "available"} for k in ("GP1-S", "POP2-8C-32G")}}),
            "/instance/v1/zones/fr-par-2/products/servers": (200, {"servers": {
                "GP1-S": {"hourly_price": 0.19074}, "POP2-8C-32G": {"hourly_price": 0.29}}}),
            "/block/v1alpha1/zones/fr-par-2/volumes": (200, {"volumes": []}),
            "/iam/v1alpha1/api-keys/SCWACCESS": (200, {"application_id": "app"}),
            "/iam/v1alpha1/policies": (200, {"policies": [{"id": "pol"}]}),
            "/iam/v1alpha1/rules": (200, {"rules": [{"permission_set_names": ["InstancesFullAccess", "PrivateNetworksFullAccess",
                                                                                "BlockStorageFullAccess"]}]}),
            "/vpc/v2/regions/fr-par/private-networks/pn": (200, {"name": "mars-c2-travailleurs", "region": "fr-par"}),
        }
        self.routes.update(over)

    def request(self, method, url, **kw):
        path, _, query = url.replace("https://api.scaleway.com", "").partition("?")
        if method == "POST" and path.endswith("/security_groups"):
            # Seule écriture permise (lot Z1) : le groupe de sécurité des travailleurs, entrée refusée, sortie libre
            b = kw["json"]
            assert b["name"] == "mars-travailleurs" and b["inbound_default_policy"] == "drop"
            assert b["outbound_default_policy"] == "accept" and b["stateful"]
            self.calls.append(f"creation {path}")
            return Resp(201, {"security_group": {"id": "sg-" + path.split("/")[4], **b}})
        assert method == "GET", f"diagnostic en écriture : {method} {url}"
        self.calls.append(path)
        if path.endswith("/products/servers/availability") and "pages" in self.__dict__:
            page = int(dict(q.split("=") for q in query.split("&")).get("page", 1))
            return Resp(200, self.pages[page - 1] if page <= len(self.pages) else {"servers": {}, "total_count": 0})
        code, body = self.routes.get(path, (404, {"message": "inconnu"}))
        return Resp(code, body)


def _env(monkeypatch):
    for k, v in {"CDSE_ACCESS_KEY": "KSERVEUR", "CDSE_SECRET_KEY": "s", "CDSE_TRAVAILLEUR_ACCESS_KEY": "KTRAVAILLEUR",
                 "CDSE_TRAVAILLEUR_SECRET_KEY": "s2", "CDSE_TRAVAILLEUR_EXPIRENT_LE": "2027-01-31", "SCW_PRIVATE_NETWORK_ID": "pn",
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
    for nom in ("du serveur", "dédiées aux travailleurs"):
        assert "16 octets lus de S1C_IW_GRDH_1SDV_20261010T180430" in lines[f"Clés Copernicus {nom} valides"][1]
    assert lines["Image Sentinel 1"][1].startswith("Ubuntu 24.04 Noble Numbat (noble), volume racine de 10 Go")
    assert any('"l_ssd"' in x for x in shown)                              # fiche brute affichée
    assert "Tout est prêt" in D.report(list((s, r, d) for r, (s, d) in lines.items()))


def test_missing_and_unreadable_points_are_named(monkeypatch):
    """Cas probable du 11/10 : clé sans droit de lecture IAM, stockage bloc refusé (POP2 en secours), fiche d'image
    sans taille ; Sentinel arrêté (plafond à 0)."""
    _env(monkeypatch)
    api = ScalewayApi(**{"/instance/v1/zones/fr-par-2/images/noble": (200, {"image": {"id": "noble", "arch": "x86_64",
                             "root_volume": {"id": "vol", "volume_type": "l_ssd", "size": 0}}}),
                         "/block/v1alpha1/zones/fr-par-2/volumes": (403, {"message": "denied"}),
                         "/iam/v1alpha1/api-keys/SCWACCESS": (403, {"message": "denied"})})
    rules = {**RULES, "sentinel": {**RULES["sentinel"], "scenes_max_par_jour": 0}}
    lines, shown = _run(api, rules=rules)
    assert lines["Image Sentinel 1"][0] == D.MANQUE and "taille du volume racine absente" in lines["Image Sentinel 1"][1]
    assert any("Fiche brute de l'image noble" in x for x in shown)
    assert lines["Stockage bloc"][0] == D.MANQUE and "BlockStorageFullAccess" in lines["Stockage bloc"][1]
    assert lines["Droits de la clé"][0] == D.A_VERIFIER and not any(k.startswith("Quota") for k in lines)
    assert lines["Plafond de scènes"][0] == D.INFO and "Sentinel arrêté" in lines["Plafond de scènes"][1]
    assert lines["Clés Copernicus dédiées aux travailleurs valides"][0] == D.PRET


def test_every_calculation_type_shows_its_stock_and_price(monkeypatch):
    _env(monkeypatch)
    lines, _ = _run(ScalewayApi())
    types = {r: v for r, v in lines.items() if r.startswith("Type ")}
    assert list(types) == ["Type 1 : GP1-S en fr-par-2", "Type 2 : POP2-8C-32G en fr-par-2"]
    assert types["Type 1 : GP1-S en fr-par-2"] == (D.PRET, "processeur facturé à l'heure entamée, available, "
                                                            "0,1907 €/h au catalogue")
    api = ScalewayApi(**{"/instance/v1/zones/fr-par-2/products/servers/availability": (200, {"servers": {
        "GP1-S": {"availability": "shortage"}, "POP2-8C-32G": {"availability": "available"}}}),
        "/instance/v1/zones/fr-par-2/products/servers": (200, {"servers": {"GP1-S": {"hourly_price": 0.25},
                                                                           "POP2-8C-32G": {"hourly_price": 0.29}}})})
    lines, _ = _run(api)
    gp1 = lines["Type 1 : GP1-S en fr-par-2"]
    assert gp1[0] == D.A_VERIFIER and "shortage" in gp1[1] and "à mettre à jour" in gp1[1]   # rupture : pas un manque
    assert lines["Type 2 : POP2-8C-32G en fr-par-2"][0] == D.PRET
    api = ScalewayApi(**{"/instance/v1/zones/fr-par-2/products/servers/availability": (200, {"servers": {
        "GP1-S": {"availability": "available"}}})})
    lines, _ = _run(api)
    assert lines["Type 2 : POP2-8C-32G en fr-par-2"] == (
        D.MANQUE, "processeur facturé à l'heure entamée, absent du catalogue, 0,2900 €/h au catalogue")


def test_copernicus_is_checked_first_and_refused_keys_are_reported(monkeypatch):
    _env(monkeypatch)
    cop = Copernicus(refused={"KTRAVAILLEUR"})
    lines, _ = _run(ScalewayApi(), cop)
    assert {k for _u, k in cop.gets} == {"KSERVEUR", "KTRAVAILLEUR"}                 # deux vraies lectures
    assert lines["Clés Copernicus du serveur valides"][0] == D.PRET
    assert lines["Clés Copernicus dédiées aux travailleurs valides"][0] == D.MANQUE
    assert "HTTP 403, code InvalidAccessKeyId" in lines["Clés Copernicus dédiées aux travailleurs valides"][1]
    monkeypatch.delenv("CDSE_TRAVAILLEUR_SECRET_KEY")
    lines, _ = _run(ScalewayApi())
    assert lines["Clés Copernicus dédiées aux travailleurs"] == (D.MANQUE, "absentes du .env : CDSE_TRAVAILLEUR_SECRET_KEY")


def test_availability_is_read_across_pages(monkeypatch):
    """Réponse paginée (100 types par page) : le type cherché n'est pas sur la première page (cas du 13/10/2026, avec
    le L4)."""
    _env(monkeypatch)
    api = ScalewayApi()
    api.pages = [{"servers": {f"DEV{k}": {"availability": "available"} for k in range(100)}, "total_count": 140},
                 {"servers": {"GP1-S": {"availability": "scarce"}}, "total_count": 140}]
    lines, _ = _run(api)
    assert lines["GP1-S en fr-par-2"] == (D.PRET, "disponibilité annoncée : scarce (stock faible ou épuisé)")
    api.pages = [{"servers": {"DEV1-S": {"availability": "available"}}, "total_count": 1}]
    lines, shown = _run(api)
    assert lines["GP1-S en fr-par-2"][0] == D.A_VERIFIER and "absent des 1 types listés" in lines["GP1-S en fr-par-2"][1]
    assert any("Réponse brute de la disponibilité" in x for x in shown)


def test_key_expiry_when_reported_in_env(monkeypatch):
    _env(monkeypatch)
    for value, status, text in (("2026-10-10", D.MANQUE, "clés expirées le 10/10/2026"),
                                ("2026-10-20", D.A_VERIFIER, "encore 8 jour(s)"),
                                ("2027-01-31", D.PRET, "valables jusqu'au 31/01/2027"),
                                ("31/01/2027", D.PRET, "valables jusqu'au 31/01/2027"),     # lot Z1 : deux formats
                                ("fin janvier", D.A_VERIFIER, "format JJ/MM/AAAA ou AAAA-MM-JJ attendu")):
        monkeypatch.setenv("CDSE_TRAVAILLEUR_EXPIRENT_LE", value)
        lines, _ = _run(ScalewayApi())
        rub = "Expiration des clés dédiées aux travailleurs"
        assert lines[rub][0] == status and text in lines[rub][1]
    # Non renseignée : à vérifier pour la paire des travailleurs (surveillance demandée), information pour le serveur
    monkeypatch.delenv("CDSE_TRAVAILLEUR_EXPIRENT_LE")
    lines, _ = _run(ScalewayApi())
    assert lines["Expiration des clés dédiées aux travailleurs"][0] == D.A_VERIFIER
    assert lines["Expiration des clés du serveur"][0] == D.INFO


def test_block_volumes_are_read_through_block_storage(monkeypatch):
    """Premier essai réel du 14/10/2026 : l'API Instances donne une taille 0 pour un volume bloc. Le diagnostic lit un
    volume par l'API Block Storage, et relit ceux enregistrés pour les travailleurs Sentinel (supprimés : 404)."""
    _env(monkeypatch)
    lines, _ = _run(ScalewayApi())
    assert lines["Lecture d'un volume bloc"][0] == D.PRET and "aucun volume bloc" in lines["Lecture d'un volume bloc"][1]
    api = ScalewayApi(**{"/block/v1alpha1/zones/fr-par-2/volumes": (200, {"volumes": [{"id": "v1"}]}),
                         "/block/v1alpha1/zones/fr-par-2/volumes/v1": (200, {"id": "v1", "size": 20_000_000_000}),
                         "/block/v1alpha1/zones/fr-par-2/volumes/reste": (200, {"id": "reste", "size": 20_000_000_000,
                                                                                "status": "available"})})
    shown = []
    out = D.run(RULES, Copernicus(), Scaleway("cle", "p", session=api, protected=set()), NOW, shown.append,
                recorded=[(19, ["parti"]), (20, ["reste"])])
    lines = {r: (s, d) for s, r, d in out}
    assert lines["Lecture d'un volume bloc"] == (D.PRET, "volume v1 : 20 Go lus par l'API Block Storage")
    assert lines["Volume bloc du travailleur 19"] == (D.PRET, "parti supprimé (absent de l'API Block Storage)")
    assert lines["Volume bloc du travailleur 20"][0] == D.MANQUE and "facturé" in lines["Volume bloc du travailleur 20"][1]
    api = ScalewayApi(**{"/block/v1alpha1/zones/fr-par-2/volumes": (200, {"volumes": [{"id": "v1"}]}),
                         "/block/v1alpha1/zones/fr-par-2/volumes/v1": (200, {"id": "v1", "size": 0})})
    lines, _ = _run(api)
    assert lines["Lecture d'un volume bloc"][0] == D.A_VERIFIER


def test_security_groups_created_once_per_worker_zone(monkeypatch):
    """Lot Z1 : le diagnostic crée le groupe de sécurité des travailleurs dans chaque zone où il manque, puis le vérifie."""
    _env(monkeypatch)
    api = ScalewayApi()
    lines, _ = _run(api)
    assert lines["Groupe de sécurité en fr-par-1"][0] == D.PRET and "créé" in lines["Groupe de sécurité en fr-par-1"][1]
    assert lines["Groupe de sécurité en fr-par-2"][0] == D.PRET
    assert sorted(c for c in api.calls if c.startswith("creation")) == [
        "creation /instance/v1/zones/fr-par-1/security_groups", "creation /instance/v1/zones/fr-par-2/security_groups"]
    ouvert = {"id": "sg9", "name": "mars-travailleurs", "project": "p", "inbound_default_policy": "accept"}
    api = ScalewayApi(**{"/instance/v1/zones/fr-par-1/security_groups": (200, {"security_groups": [ouvert]})})
    lines, _ = _run(api)
    assert lines["Groupe de sécurité en fr-par-1"][0] == D.MANQUE and "politique d'entrée" in lines["Groupe de sécurité en fr-par-1"][1]
