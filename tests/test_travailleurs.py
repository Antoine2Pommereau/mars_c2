"""Travailleurs éphémères : plafonds, coût, orphelins, cloud-init ; détections VIIRS : nuits, granules, appariement."""
import base64
import re
from datetime import date, datetime, timedelta, timezone

import yaml

from mars.config import ROOT
from mars.travailleurs import TAG, cloud_init, cost, orphans, quota, type_params
from mars.viirs import (detection_time, granule_key, last_night, match, night_of, pair_granules, position_at,
                        severity)

RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text())
T0 = datetime(2026, 10, 8, 6, 30, tzinfo=timezone.utc)
M = timedelta(minutes=1)


# Garde fous

def test_daily_caps():
    w = lambda h, minutes: {"cree_le": T0 - h * 60 * M, "fini_le": T0 - h * 60 * M + minutes * M}
    assert quota(RULES, [w(3, 10)], T0) is None
    assert "travailleurs" in quota(RULES, [w(k, 5) for k in range(RULES["travailleurs"]["max_par_jour"])], T0)
    assert "minutes" in quota(RULES, [w(4, 100), w(2, 90)], T0)


def test_cost_is_billed_by_started_hour_on_cpu_and_by_minute_on_gpu():
    viirs, gpu = type_params(RULES, "viirs"), type_params(RULES, "sentinel")
    assert cost(7, viirs["prix_heure"], viirs["facturation_min_min"]) == 0.0202        # une heure entamée
    assert cost(61, viirs["prix_heure"], viirs["facturation_min_min"]) == 0.0404
    assert cost(12, gpu["prix_heure"], gpu["facturation_min_min"]) == round(12 / 60 * 0.7875, 4)


def test_orphans_unknown_or_too_old_instances_are_destroyed():
    s = lambda sid, n, age_min, tags=None: {"id": sid, "name": f"mars-travailleur-{n}",
                                            "tags": list(tags or (TAG, f"run-{n}")),
                                            "creation_date": (T0 - age_min * M).isoformat().replace("+00:00", "Z")}
    servers = [s("actif", 1, 10), s("inconnu", 2, 3), s("vieux", 3, 80), s("autre", 4, 5, tags=("prod",))]
    active = {"actif": T0 - 10 * M, "vieux": T0 - 80 * M}
    assert orphans(servers, active, T0, RULES["travailleurs"]["duree_max_min"]) == ["inconnu", "vieux"]


def test_cloud_init_carries_boot_script_task_and_secrets():
    text = cloud_init("print('ok')", {"script": "travailleurs/viirs.py", "jeton": "abc", "retour": "http://172.16.8.2:8090/api/travailleurs/3"},
                      {"EARTHDATA_TOKEN": "s3cret"}, "ghcr.io/allenai/vessel-detection-viirs@sha256:00", "02:00:00:AA:BB:CC")
    assert text.startswith("#cloud-config") and "s3cret" not in text            # secrets encodés, jamais en clair
    files = dict(re.findall(r"path: (\S+)\n    encoding: b64\n    permissions: '\d+'\n    content: (\S+)", text))
    assert base64.b64decode(files["/mars/env"]).decode() == "EARTHDATA_TOKEN=s3cret\n"
    assert '"jeton": "abc"' in base64.b64decode(files["/mars/tache.json"]).decode()
    boot = base64.b64decode(files["/mars/demarrage.env"]).decode()
    assert 'MAC="02:00:00:aa:bb:cc"' in boot and 'RETOUR="http://172.16.8.2:8090/api/travailleurs/3"' in boot
    assert 'GPU=""' in boot and 'SCRIPT="viirs.py"' in boot
    script = base64.b64decode(files["/mars/demarrage.sh"]).decode()
    assert "netplan" in script and "signal reseau" in script and "/journal" in script
    assert "/mars/viirs.py" in files and "runcmd:\n  - [bash, /mars/demarrage.sh]" in text


def test_boot_script_is_valid_bash():
    import subprocess
    r = subprocess.run(["bash", "-n", str(ROOT / "travailleurs" / "demarrage.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# VIIRS

def test_nights_and_last_completed_night():
    assert night_of(datetime(2026, 10, 7, 1, 30, tzinfo=timezone.utc), RULES) == date(2026, 10, 6)
    assert night_of(datetime(2026, 10, 7, 22, 0, tzinfo=timezone.utc), RULES) == date(2026, 10, 7)
    assert last_night(T0, RULES) == date(2026, 10, 7)                              # 06:30 : nuit du 07 terminée
    assert last_night(T0.replace(hour=5), RULES) == date(2026, 10, 6)              # 05:00 : pas encore


def test_dnb_and_geolocation_files_are_paired():
    dnb = [{"name": "VJ102DNB_NRT.A2026280.0036.021.2026280030537.nc", "url": "u1", "start": "2026-10-07T00:36:00Z"},
           {"name": "VJ102DNB_NRT.A2026280.0042.021.2026280030543.nc", "url": "u2", "start": "2026-10-07T00:42:00Z"}]
    geo = [{"name": "VJ103DNB_NRT.A2026280.0036.021.2026280021744.nc", "url": "g1", "start": "2026-10-07T00:36:00Z"}]
    (p,) = pair_granules("NOAA20", dnb, geo)
    assert p["nom"] == "NOAA20.A2026280.0036" and p["geo_url"] == "g1"
    assert granule_key("VNP02DNB.A2026280.0018.002.2026280085539.nc") == "A2026280.0018"


def test_detection_time_follows_the_scan_along_track():
    start = datetime(2026, 10, 7, 0, 36, tzinfo=timezone.utc)
    frame = [[-8, 52], [6, 53], [4, 32], [-10, 31], [-8, 52]]                       # descendante : du nord au sud
    t = detection_time(-2, 42, start, start + 6 * M, frame)
    assert 2.8 * 60 < (t - start).total_seconds() < 3.2 * 60


def test_matching_uses_interpolated_ais_and_sensor_tolerance():
    t = datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc)
    track = [{"ts": t - 5 * M, "lon": -5.000, "lat": 48.000, "sog": 12, "cog": 90},
             {"ts": t + 5 * M, "lon": -4.950, "lat": 48.000, "sog": 12, "cog": 90}]
    lon, lat, est = position_at(track, t, 20)
    assert abs(lon + 4.975) < 1e-6 and est == 0.0
    dets = [{"lon": -4.970, "lat": 48.003, "ts": t},          # à environ 500 m : appariée
            {"lon": -4.800, "lat": 48.100, "ts": t}]          # à plus de 10 km : sans AIS
    assert list(match(dets, {7: track}, RULES)) == [0]
    alone = [{"ts": t - 30 * M, "lon": -5, "lat": 48, "sog": 0, "cog": None}]
    assert position_at(alone, t, 20) is None                  # au delà de 20 min d'estime : inconnu


def test_dark_ship_severity():
    assert severity(False, False) == "moyenne" and severity(True, False) == "elevee"
    assert severity(False, True) == "elevee" and severity(True, True) == "critique"


# Garde de destruction : jamais une instance qui n'est pas un travailleur, jamais le serveur principal

import pytest  # noqa: E402

from mars.travailleurs import RefusDestruction, Scaleway, protected_ids, refus  # noqa: E402

PROJET = "projet-mars"
PRINCIPAL = "11111111-serveur-principal"


def instance(sid, name, tags, project=PROJET, protected=False, state="running"):
    return {"id": sid, "name": name, "tags": tags, "project": project, "protected": protected, "state": state,
            "zone": "fr-par-1", "volumes": {"0": {"id": f"vol-{sid}"}},
            "creation_date": (T0 - 10 * M).isoformat().replace("+00:00", "Z")}


PARC = {s["id"]: s for s in [
    instance(PRINCIPAL, "mars-c2", [TAG, "run-1"]),                       # serveur principal, même mal étiqueté
    instance("deguise", "mars-travailleur-1", [TAG, "run-1"]),            # nom et étiquettes de travailleur, mais id protégé
    instance("sans-etiquette", "mars-travailleur-7", ["run-7"]),
    instance("etiquette-discordante", "mars-travailleur-8", [TAG, "run-9"]),
    instance("autre-nom", "mars-c2-travailleur", [TAG, "run-3"]),
    instance("nom-suffixe", "mars-travailleur-4-bis", [TAG, "run-4"]),
    instance("protegee", "mars-travailleur-5", [TAG, "run-5"], protected=True),
    instance("autre-projet", "mars-travailleur-6", [TAG, "run-6"], project="autre"),
    instance("vrai", "mars-travailleur-42", [TAG, "run-42", "tache-viirs"]),
]}


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self.body = status, body
        self.content = b"x" if body is not None else b""
        self.headers = {"content-type": "application/json"}
        self.text = ""

    def json(self):
        return self.body


class FakeApi:
    """Faux service Scaleway : enregistre chaque requête. `ignore_tags` simule une API qui ne filtre pas."""

    def __init__(self, ignore_tags=True):
        self.headers, self.calls, self.bodies, self.ignore_tags = {}, [], [], ignore_tags

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        self.bodies.append(kw.get("json"))
        m = re.search(r"/servers/([^/?]+)(/action)?$", url)
        if method == "GET" and "/servers?" in url:
            servers = list(PARC.values()) if self.ignore_tags else [s for s in PARC.values() if TAG in s["tags"]]
            return FakeResponse(200, {"servers": servers})
        if method == "GET" and m:
            return FakeResponse(200, {"server": PARC[m.group(1)]}) if m.group(1) in PARC else FakeResponse(404)
        return FakeResponse(200, {})

    def destructive(self):
        return [c for c in self.calls if c[0] in ("DELETE", "PATCH") or (c[0] == "POST" and c[1].endswith("/action"))]


def client(api):
    return Scaleway("cle", PROJET, session=api, protected={PRINCIPAL, "deguise"})


def test_only_true_workers_pass_the_guard():
    assert refus(PARC["vrai"], PROJET, {PRINCIPAL}) is None
    for sid, s in PARC.items():
        if sid != "vrai":
            assert refus(s, PROJET, {PRINCIPAL, "deguise"}) is not None, sid


@pytest.mark.parametrize("sid", [s for s in PARC if s != "vrai"])
def test_destroy_refuses_every_non_worker_without_any_destructive_call(sid):
    api = FakeApi()
    with pytest.raises(RefusDestruction):
        client(api).destroy("fr-par-1", sid)
    assert api.destructive() == []


def test_main_server_is_refused_before_even_reading_it():
    api = FakeApi()
    with pytest.raises(RefusDestruction):
        client(api).destroy("fr-par-1", PRINCIPAL)
    assert api.calls == []


def test_true_worker_is_terminated():
    api = FakeApi()
    assert client(api).destroy("fr-par-1", "vrai") is False                 # destruction en cours
    assert api.destructive() == [("POST", "https://api.scaleway.com/instance/v1/zones/fr-par-1/servers/vrai/action")]
    assert {"action": "terminate"} in api.bodies


def test_listing_and_orphans_keep_only_workers_even_if_the_api_ignores_the_tag_filter():
    api = FakeApi(ignore_tags=True)
    scw = client(api)
    assert [s["id"] for s in scw.tagged("fr-par-1")] == ["vrai"]
    assert orphans(list(PARC.values()), {}, T0, 45, PROJET, {PRINCIPAL, "deguise"}) == ["vrai"]


def test_emergency_stop_destroys_only_workers():
    """Même boucle que « taches.py detruire-travailleurs »."""
    api = FakeApi(ignore_tags=True)
    scw = client(api)
    for s in scw.tagged("fr-par-1"):
        scw.destroy(s["zone"], s["id"])
    assert {c[1].split("/servers/")[1] for c in api.destructive()} == {"vrai/action"}


def test_destructive_actions_cannot_bypass_destroy():
    api = FakeApi()
    for action in ("terminate", "poweroff", "stop_in_place", "reboot"):
        with pytest.raises(RefusDestruction):
            client(api).action("fr-par-1", "vrai", action)
    assert api.calls == []


def test_protected_ids_from_env_and_instance_metadata(monkeypatch):
    class Meta:
        def get(self, url, timeout):
            assert url.startswith("http://169.254.42.42/") and timeout <= 5      # jamais bloquant au démarrage
            r = FakeResponse(200, {"id": "depuis-metadonnees"})
            r.ok = True
            return r
    monkeypatch.setenv("SCW_SERVEUR_PRINCIPAL", "a, b")
    assert protected_ids(Meta()) == {"a", "b", "depuis-metadonnees"}



# Ordre de création : instance éteinte, réseau privé rattaché et prêt, cloud-init avec la MAC, démarrage vérifié

class ProvisionApi(FakeApi):
    """Faux Scaleway qui suit l'état d'une instance créée : carte privée « syncing » puis « available », démarrage."""

    def __init__(self, nic_states=("syncing", "syncing", "available")):
        super().__init__()
        self.nic_states, self.state, self.order = list(nic_states), "stopped", []

    def request(self, method, url, **kw):
        path = url.replace("https://api.scaleway.com", "")
        if "/marketplace/" in path:
            return FakeResponse(200, {"local_images": [{"id": "img", "compatible_commercial_types": ["DEV1-M"]}]})
        if method == "POST" and path.endswith("/servers"):
            self.order.append("creation")
            body = kw["json"]
            assert "dynamic_ip_required" in body and body["name"] == "mars-travailleur-3"
            return FakeResponse(201, {"server": {"id": "neuf", "state": "stopped", "name": body["name"]}})
        if method == "POST" and path.endswith("/private_nics"):
            assert self.state == "stopped", "réseau privé rattaché après le démarrage"
            self.order.append("rattachement")
            return FakeResponse(201, {"private_nic": {"id": "nic", "state": self.nic_states.pop(0),
                                                      "mac_address": "02:00:00:AA:BB:CC", "private_network_id": "pn"}})
        if method == "GET" and "/private_nics/" in path:
            self.order.append("verification")
            return FakeResponse(200, {"private_nic": {"id": "nic", "state": self.nic_states.pop(0),
                                                      "mac_address": "02:00:00:AA:BB:CC", "private_network_id": "pn"}})
        if method == "PATCH" and "/user_data/cloud-init" in path:
            assert self.state == "stopped" and "rattachement" in self.order
            self.order.append("cloud-init")
            self.cloud = kw["data"].decode()
            return FakeResponse(204)
        if method == "POST" and path.endswith("/action"):
            assert kw["json"] == {"action": "poweron"} and self.order[-1] == "cloud-init"
            self.order.append("demarrage")
            self.state = "running"
            return FakeResponse(202, {"task": {}})
        if method == "GET" and path.endswith("/servers/neuf"):
            self.order.append(f"etat {self.state}")
            return FakeResponse(200, {"server": {"id": "neuf", "state": self.state}})
        raise AssertionError(f"appel inattendu {method} {path}")


T_VIIRS = {"commercial_type": "DEV1-M", "image_label": "docker", "disque_go": 20}


def test_provision_order_create_attach_verify_cloud_init_start():
    from mars.travailleurs import provision
    api = ProvisionApi()
    scw = Scaleway("cle", PROJET, session=api, protected=set())
    created = []
    r = provision(scw, "fr-par-1", "mars-travailleur-3", T_VIIRS, [TAG, "run-3"], "pn", lambda mac: f"#cloud-config {mac}",
                  sleep=lambda _s: None, on_created=created.append)
    assert api.order == ["creation", "rattachement", "verification", "verification", "cloud-init", "demarrage",
                         "etat running"]
    assert created == ["neuf"] and r["mac"] == "02:00:00:AA:BB:CC" and "02:00:00:AA:BB:CC" in api.cloud


def test_provision_stops_before_start_when_private_network_fails():
    from mars.travailleurs import ScalewayError, provision
    api = ProvisionApi(nic_states=("syncing", "syncing_error"))
    scw = Scaleway("cle", PROJET, session=api, protected=set())
    with pytest.raises(ScalewayError, match="réseau privé"):
        provision(scw, "fr-par-1", "mars-travailleur-3", T_VIIRS, [TAG, "run-3"], "pn", lambda _mac: "x",
                  sleep=lambda _s: None)
    assert "demarrage" not in api.order and "cloud-init" not in api.order
