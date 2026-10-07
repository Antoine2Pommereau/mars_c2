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
    s = lambda sid, age_min, tags=(TAG,): {"id": sid, "tags": list(tags),
                                           "creation_date": (T0 - age_min * M).isoformat().replace("+00:00", "Z")}
    servers = [s("actif", 10), s("inconnu", 3), s("vieux", 80), s("autre", 5, tags=("prod",))]
    active = {"actif": T0 - 10 * M, "vieux": T0 - 80 * M}
    assert orphans(servers, active, T0, RULES["travailleurs"]["duree_max_min"]) == ["inconnu", "vieux"]


def test_cloud_init_carries_script_task_and_secrets():
    text = cloud_init("print('ok')", {"script": "travailleurs/viirs.py", "jeton": "abc"}, {"EARTHDATA_TOKEN": "s3cret"},
                      "ghcr.io/allenai/vessel-detection-viirs@sha256:00")
    assert text.startswith("#cloud-config") and "s3cret" not in text            # secrets encodés, jamais en clair
    files = dict(re.findall(r"path: (\S+)\n    encoding: b64\n    permissions: '\d+'\n    content: (\S+)", text))
    assert base64.b64decode(files["/mars/env"]).decode() == "EARTHDATA_TOKEN=s3cret\n"
    assert '"jeton": "abc"' in base64.b64decode(files["/mars/tache.json"]).decode()
    assert "/mars/viirs.py" in files and "shutdown -h now" in text and "--gpus" not in text


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
