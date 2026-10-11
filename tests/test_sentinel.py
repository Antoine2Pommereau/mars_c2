"""Lot C, Sentinel 1 : décisions pures (cap, incohérences, plafonds, budget, choix des scènes, adresses signées),
script du travailleur, volumes bloc des travailleurs GPU ; avec MARS_TEST_DATABASE_URL, la chaîne complète sur la base
de test (sélection des passages, lancement, résultats reçus deux fois, fusion et alertes) avec de faux clients
Copernicus et Scaleway."""
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mars import sentinel as S
from mars.config import ROOT, load_rules
from mars.travailleurs import Scaleway

RULES = load_rules()
SR = RULES["sentinel"]
URL = os.environ.get("MARS_TEST_DATABASE_URL")


def _worker():
    spec = importlib.util.spec_from_file_location("travailleur_sentinel", ROOT / "travailleurs" / "sentinel.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_track_heading_matches_the_configured_values():
    assert round(S.track_heading(47.0, True)) == 348 and round(S.track_heading(47.0, False)) == 192
    assert RULES["fusion"]["flight_heading_deg"] == {"ascending": 348, "descending": 192}


def test_heading_buckets_and_angles():
    probs = [0.01] * 16
    probs[4] = 0.85
    assert S.heading_from_buckets(probs) == (101.2, 0.85)            # centre de la classe 4 : 4,5 × 22,5°
    assert S.heading_from_buckets([0.1] * 15) == (None, None)
    assert S.angle_diff(350, 10) == 20 and S.angle_diff(90, 270) == 180


def test_length_and_heading_mismatches_follow_the_tolerances():
    ais = {"length_m": 100, "cog": 90, "sog": 12}
    assert S.mismatches({"longueur_m": 130}, ais, SR) == []                      # 30 m : sous 40 m
    assert S.mismatches({"longueur_m": 135}, {"length_m": 300, "cog": 90, "sog": 12}, SR) == [
        "Longueur mesurée 135 m contre 300 m déclarés (-165 m)"]
    out = S.mismatches({"longueur_m": 190, "cap_deg": 180, "cap_prob": 0.7}, ais, SR)
    assert len(out) == 2 and out[0].startswith("Longueur mesurée 190 m contre 100 m déclarés (plus 90 m)")
    # Avant et arrière confondus (180°) : pas d'incohérence ; classe peu probable ou navire lent non plus
    assert S.mismatches({"cap_deg": 270, "cap_prob": 0.9}, ais, SR) == []
    assert S.mismatches({"cap_deg": 180, "cap_prob": 0.3}, ais, SR) == []
    assert S.mismatches({"cap_deg": 180, "cap_prob": 0.9}, {"length_m": 100, "cog": 90, "sog": 1}, SR) == []
    assert S.mismatches({"longueur_m": 90}, {"length_m": 5, "cog": None, "sog": None}, SR) == []   # longueur AIS douteuse


def test_daily_cap_and_monthly_budget():
    t = RULES["travailleurs"]["types"]["sentinel"]
    est = S.cost_estimate(2, SR, t)
    assert est == round((SR["minutes_fixes"] + 2 * SR["minutes_par_scene"]) / 60 * t["prix_heure"], 3)
    assert S.guard(2, 0, 0.0, est, SR) is None
    assert S.guard(1, 2, 0.0, est, SR).startswith("plafond de 2 scènes par jour")
    assert S.guard(1, 0, SR["budget_mensuel_eur"] - 0.05, est, SR).startswith("budget mensuel de 6,00 €")


SQUARE = {"type": "Polygon", "coordinates": [[[-6.5, 47.8], [-5.5, 47.8], [-5.5, 48.4], [-6.5, 48.4], [-6.5, 47.8]]]}
OTHER = {"type": "Polygon", "coordinates": [[[-4.0, 46.0], [-3.0, 46.0], [-3.0, 46.5], [-4.0, 46.5], [-4.0, 46.0]]]}


def test_scenes_are_kept_only_when_they_cover_a_point_of_interest():
    scenes = [{"id": "A", "debut": "1", "emprise": SQUARE}, {"id": "B", "debut": "2", "emprise": OTHER}]
    pts = [{"lon": -6.0, "lat": 48.0, "motif": "corridor_liste"}, {"lon": -5.45, "lat": 48.0, "motif": "alerte_viirs"}]
    out = S.pick_scenes(scenes, pts, 10_000)
    assert [s["id"] for s in out] == ["A"] and len(out[0]["points"]) == 2      # le second point est à 4 km du bord
    assert S.pick_scenes(scenes, pts[1:], 1_000) == []


def test_signed_urls_never_carry_the_secret(monkeypatch):
    monkeypatch.setenv("CDSE_ACCESS_KEY", "CLEPUBLIQUE")
    monkeypatch.setenv("CDSE_SECRET_KEY", "secretdecopernicus")
    sc = {"vv": "s3://eodata/Sentinel-1/SAR/IW_GRDH_1S-COG/2026/10/10/X_COG.SAFE/measurement/s1c-iw-grd-vv-1-cog.tiff",
          "vh": "s3://eodata/Sentinel-1/SAR/IW_GRDH_1S-COG/2026/10/10/X_COG.SAFE/measurement/s1c-iw-grd-vh-1-cog.tiff"}
    urls = S.signed_urls(sc, now=datetime(2026, 10, 11, 12, tzinfo=timezone.utc))
    assert urls["vv"].startswith("https://eodata.dataspace.copernicus.eu/eodata/Sentinel-1/SAR/")
    assert "secretdecopernicus" not in urls["vv"] + urls["vh"]
    assert "%2Fdefault%2Fs3%2Faws4_request" in urls["vv"] and "X-Amz-Expires=10800" in urls["vv"]
    monkeypatch.delenv("CDSE_SECRET_KEY")
    with pytest.raises(RuntimeError, match="CDSE_SECRET_KEY"):
        S.signed_urls(sc)


def test_catalogue_item_becomes_a_scene():
    item = json.loads((Path(__file__).parent / "donnees" / "stac_s1_cog.json").read_text())
    sc = S.scene_from_item(item)
    assert sc["satellite"] == "S1C" and sc["orbite"] == "ascending" and sc["octets"] == 422757937 + 568518437
    assert sc["vv"].endswith("-vv-20261010t180430-20261010t180455-009826-01393a-001-cog.tiff")
    assert S.scene_from_item({**item, "properties": {**item["properties"], "sar:instrument_mode": "EW"}}) is None


def test_worker_checks_its_downloads_and_reads_headings():
    w = _worker()
    with pytest.raises(w.Arret, match="adresse signée expirée"):
        w.check_download(403, "application/xml", b"<Error>")
    with pytest.raises(w.FichierInvalide, match="réponse inattendue"):
        w.check_download(200, "text/html", b"<html>")
    with pytest.raises(w.FichierInvalide, match="tronquée"):
        w.check_download(200, "image/tiff", b"II*\x00rest", 1000)
    w.check_download(200, "image/tiff", b"II+\x00big", 600_000_000)
    probs = [0.0] * 16
    probs[0] = 0.6
    assert w.heading(probs) == (11.2, 0.6) and w.in_regions(-6, 48, [[-6.8, 47.3, -3.0, 49.6]])


# Volumes bloc des travailleurs GPU (les L4 n'ont pas de disque local)

class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._b = status, body
        self.content = b"x" if body is not None else b""
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._b


class BlockApi:
    """Instance GPU à volume bloc : terminate interdit (le volume survivrait), suppression de l'instance puis du volume
    par l'API Block Storage ; le volume reste « in_use » une minute après la suppression de l'instance."""

    def __init__(self, state="running"):
        self.state, self.volume, self.calls, self.in_use = state, True, [], 1
        self.headers = {}

    def request(self, method, url, **kw):
        path = url.replace("https://api.scaleway.com", "")
        self.calls.append((method, path, kw.get("json")))
        srv = "/instance/v1/zones/fr-par-2/servers/gpu"
        if method == "GET" and path == srv:
            if self.state is None:
                return _Resp(404)
            return _Resp(200, {"server": {"id": "gpu", "name": "mars-travailleur-7", "project": "p",
                                          "tags": ["mars-c2-travailleur", "run-7"], "state": self.state,
                                          "volumes": {"0": {"id": "blk", "volume_type": "sbs_volume"}}}})
        if method == "POST" and path == srv + "/action":
            assert kw["json"]["action"] == "poweroff", "terminate laisserait le volume bloc"
            self.state = "stopped"
            return _Resp(202, {})
        if method == "DELETE" and path == srv:
            assert self.state == "stopped"
            self.state = None
            return _Resp(204)
        if method == "DELETE" and path == "/block/v1alpha1/zones/fr-par-2/volumes/blk":
            if self.in_use:
                self.in_use -= 1
                r = _Resp(412, {"type": "precondition_failed", "message": "volume in_use"})
                return r
            self.volume = False
            return _Resp(204)
        raise AssertionError(f"appel inattendu {method} {path}")


def test_gpu_worker_is_powered_off_deleted_and_its_block_volume_removed():
    api = BlockApi()
    scw = Scaleway("cle", "p", session=api, protected=set())
    steps = 0
    while not scw.destroy("fr-par-2", "gpu", ["blk"]):
        steps += 1
        assert steps < 6
    assert not api.volume and all(c[2] != {"action": "terminate"} for c in api.calls)
    assert steps == 2                                    # extinction, suppression (volume encore attaché), volume


def test_sbs_image_is_resolved_from_the_block_catalogue():
    class Api:
        headers = {}

        def request(self, _method, url, **_kw):
            if "/marketplace/" in url:
                assert "type=instance_sbs" in url and "zone=fr-par-2" in url
                return _Resp(200, {"local_images": [{"id": "gpuos", "arch": "x86_64", "type": "instance_sbs",
                                                     "compatible_commercial_types": ["L4-1-24G"]}]})
            return _Resp(200, {"image": {"id": "gpuos", "name": "Ubuntu Noble GPU OS 13", "arch": "x86_64",
                                         "root_volume": {"id": "snap", "volume_type": "sbs_snapshot", "size": 20_000_000_000}}})
    img = Scaleway("cle", "p", session=Api(), protected=set()).resolve_image(
        "fr-par-2", "ubuntu_noble_gpu_os_13_nvidia", "L4-1-24G", "x86_64", "sbs_volume")
    assert img == {"id": "gpuos", "name": "Ubuntu Noble GPU OS 13", "arch": "x86_64", "root_type": "sbs_volume",
                   "root_size": 20_000_000_000}


# Chaîne complète sur la base de test

class FakeStac:
    """Faux catalogue Copernicus : les scènes données, quel que soit le passage demandé."""

    def __init__(self, scenes):
        self.scenes, self.calls = scenes, 0

    def post(self, _url, **_kw):
        self.calls += 1
        feats = [{"id": s["id"], "geometry": s["emprise"],
                  "properties": {"start_datetime": s["debut"], "end_datetime": s["fin"], "datetime": s["debut"],
                                 "sat:orbit_state": "descending", "platform": "sentinel-1c", "sar:instrument_mode": "IW"},
                  "assets": {"vv": {"href": f"s3://eodata/x/{s['id']}/vv.tiff", "file:size": 5},
                             "vh": {"href": f"s3://eodata/x/{s['id']}/vh.tiff", "file:size": 5}}}
                 for s in self.scenes]

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"features": feats}
        return R()


@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL)
    yield c
    c.rollback()                                          # rien n'est gardé : chaque test s'annule
    c.close()


def _setup(conn, now):
    """Un passage Sentinel 1 sur l'ouest de la Bretagne ; un câble ; un navire sanctionné qui le longe ; un grand navire
    AIS ; une zone de réception fiable autour ; un parc éolien."""
    for sql in ("DELETE FROM alert_evidence", "DELETE FROM alerts", "DELETE FROM detections", "DELETE FROM analyses",
                "DELETE FROM reception_cells", "DELETE FROM fixed_echoes", "DELETE FROM stats_minute",
                "DELETE FROM sar_passes WHERE product_name LIKE 'S1C_OR999%'", "DELETE FROM infrastructure WHERE source = 'essai'",
                "DELETE FROM watchlist WHERE imo = 9999011",
                "DELETE FROM positions WHERE vessel_id IN (SELECT id FROM vessels WHERE mmsi IN (273999001, 228999002, 228999003))",
                "DELETE FROM vessels WHERE mmsi IN (273999001, 228999002, 228999003)"):
        conn.execute(sql)
    t0 = now - timedelta(hours=3)
    pid = conn.execute(
        "INSERT INTO sar_passes (product_name, platform, acquired_at, ended_at, orbit_direction, footprint, mission, "
        "satellite, mode, statut) VALUES ('S1C_OR9999', 'sentinel-1c', %s, %s, 'descending', "
        "ST_GeomFromGeoJSON(%s)::geography, 'S1', 'S1C', 'IW', 'acquis') RETURNING id",
        (t0, t0 + timedelta(minutes=2), json.dumps(SQUARE))).fetchone()[0]
    reg = conn.execute("SELECT id FROM regions WHERE lower(name) = 'bretagne'").fetchone()[0]
    conn.execute("INSERT INTO infrastructure (region_id, kind, name, source, attrs, geom) VALUES "
                 "(%s, 'cable', 'CABLE ESSAI', 'essai', '{\"type\": \"Câble télécom\"}', "
                 "ST_GeomFromText('LINESTRING(-6.4 48.0, -5.6 48.0)', 4326)::geography)", (reg,))
    conn.execute("INSERT INTO infrastructure (region_id, kind, name, source, attrs, geom) VALUES "
                 "(%s, 'windfarm', 'PARC ESSAI', 'essai', '{\"type\": \"Parc éolien\"}', "
                 "ST_GeomFromText('POLYGON((-6.32 48.28,-6.28 48.28,-6.28 48.32,-6.32 48.32,-6.32 48.28))', 4326)::geography)",
                 (reg,))
    ids = {}
    for key, mmsi, imo, length in (("liste", 273999001, 9999011, 120), ("grand", 228999002, None, 200),
                                   ("menteur", 228999003, None, 100)):
        ids[key] = conn.execute("INSERT INTO vessels (mmsi, imo, name, length_m, ais_class) VALUES (%s, %s, %s, %s, 'A') "
                                "RETURNING id", (mmsi, imo, key.upper(), length)).fetchone()[0]
    conn.execute("INSERT INTO watchlist (source, imo, name, risks, datasets, sanctioned) VALUES "
                 "('opensanctions', 9999011, 'LISTE', '{sanction}', '{eu_essai}', true)")
    conn.execute("REFRESH MATERIALIZED VIEW vessel_watch")
    # Trajectoires à 10 nœuds vers l'est, une position par minute autour de l'acquisition
    for key, lon0, lat in (("liste", -6.1, 48.01), ("grand", -5.9, 48.2), ("menteur", -6.2, 47.9)):
        for k in range(-20, 21):
            conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn, cog_deg) VALUES (%s, %s, "
                         "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 10, 90)",
                         (ids[key], t0 + timedelta(minutes=1) + timedelta(minutes=k), lon0 + k * 0.0041, lat))
    # Zone de réception fiable sur toute la scène
    conn.execute("INSERT INTO reception_cells (cx, cy, messages, vessels, hours, coverage, geom) "
                 "SELECT x, y, 100, 5, 10, 0.95, ST_MakeEnvelope(x * 0.1, y * 0.05, (x + 1) * 0.1, (y + 1) * 0.05, 4326)::geography "
                 "FROM generate_series(-65, -55) x, generate_series(956, 968) y")
    return pid, ids, t0


def test_selection_launch_and_results_end_to_end(conn, monkeypatch):
    from mars import sentinel, travailleurs
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    pid, ids, t0 = _setup(conn, now)
    scenes = [{"id": "S1C_IW_GRDH_1SDV_A_COG", "debut": (t0).isoformat(), "fin": (t0 + timedelta(seconds=25)).isoformat(),
               "emprise": SQUARE},
              {"id": "S1C_IW_GRDH_1SDV_B_COG", "debut": (t0 + timedelta(seconds=25)).isoformat(),
               "fin": (t0 + timedelta(seconds=50)).isoformat(), "emprise": OTHER}]
    stac = FakeStac(scenes)
    # Sélection : le navire sanctionné longe le câble dans l'emprise ; seule la scène A couvre ce point
    p = sentinel.plan(conn, stac, RULES, now)
    assert [x["pass_id"] for x in p["passages"]] == [pid]
    assert [s["id"] for s in p["passages"][0]["scenes"]] == ["S1C_IW_GRDH_1SDV_A_COG"]
    assert p["passages"][0]["points"][0]["motif"] == "corridor_liste"

    monkeypatch.setenv("CDSE_ACCESS_KEY", "k")
    monkeypatch.setenv("CDSE_SECRET_KEY", "s")
    launched = []

    def fake_launch(c, _scw, _rules, tache, params, env, **_kw):
        launched.append((tache, params, env))
        wid = c.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, verrou) "
                        "VALUES ('sentinel', 'L4-1-24G', 'fr-par-2', 'x', %s, 'sentinel') RETURNING id",
                        (json.dumps(params),)).fetchone()[0]
        return {"travailleur": wid}
    monkeypatch.setattr(travailleurs, "launch", fake_launch)
    r = sentinel.launch(conn, None, RULES, now, stac)
    assert r["scenes"] == 1 and r["scenes_ecartees_par_le_plafond"] == 0
    tache, params, env = launched[0]
    assert tache == "sentinel" and env == {} and "s3://" not in json.dumps(params)    # adresses signées, aucune clé
    assert params["scenes"][0]["vv"].startswith("https://eodata.dataspace.copernicus.eu/")
    aid = r["analyses"][0]
    # Le même passage n'est plus proposé ; un second lancement ne trouve rien
    assert sentinel.plan(conn, stac, RULES, now)["passages"] == []

    # Résultat du travailleur : un écho sur le navire sanctionné (apparié), un écho sans AIS au large (DARK_SHIP), un
    # écho près du parc éolien (écarté), un écho trop long pour le navire « menteur » (IDENTITY_MISMATCH) ; le
    # « grand » navire AIS n'a aucun écho (AIS_UNCONFIRMED)
    def det(lon, lat, length, cap=None):
        probs = [0.02] * 16
        if cap is not None:
            probs[int(cap // 22.5)] = 0.7
        return {"lon": lon, "lat": lat, "score": 0.95, "longueur_m": length, "largeur_m": 20, "cap_deg": cap,
                "cap_prob": 0.7 if cap is not None else None, "vitesse_kn": 9, "peche": 0.1, "contraste_db": 14}
    tm = t0 + timedelta(seconds=12.5)
    off = (tm - (t0 + timedelta(minutes=1))).total_seconds() / 60 * 0.0041
    res = {"scenes": [{"analyse": aid, "id": scenes[0]["id"], "debut": scenes[0]["debut"], "fin": scenes[0]["fin"],
                       "telecharge_s": 40, "telecharge_mo": 990, "analyse_s": 150, "gpu_max_mo": 6100,
                       "detections": [det(-6.1 + off, 48.01, 118, 90), det(-6.0, 48.3, 60), det(-6.30, 48.30, 90),
                                      det(-6.2 + off, 47.9, 260, 90)]}],
           "mesures": {"duree_s": 400, "gpu": "NVIDIA L4"}}
    wid = launched and conn.execute("SELECT travailleur_id FROM analyses WHERE id = %s", (aid,)).fetchone()[0]
    w = {"id": wid, "resultat": res}
    out = sentinel.ingest(conn, w, RULES)
    assert out["detections"] == 4 and out["appariees"] == 2
    assert out["alertes_dark_ship"] == 1 and out["alertes_incoherence"] == 1 and out["alertes_non_confirme"] == 1
    rows = dict(conn.execute("SELECT type, count(*) FROM alerts GROUP BY 1").fetchall())
    assert rows == {"DARK_SHIP": 1, "IDENTITY_MISMATCH": 1, "AIS_UNCONFIRMED": 1}
    masks = dict(conn.execute("SELECT round(ST_X(geom::geometry)::numeric, 2)::text, coalesce(mask_reason, '') "
                              "FROM detections WHERE analysis_id = %s", (aid,)).fetchall())
    assert masks["-6.30"] == "eolienne probable"
    mm = conn.execute("SELECT details FROM alerts WHERE type = 'IDENTITY_MISMATCH'").fetchone()[0]
    assert mm["navire"]["vessel_id"] == ids["menteur"] and mm["ecarts"][0].startswith("Longueur mesurée 260 m")
    dark = conn.execute("SELECT details, severity FROM alerts WHERE type = 'DARK_SHIP'").fetchone()
    assert dark[0]["source"] == "sentinel1" and dark[0]["critere_evaluation"] == "zone_de_reception_fiable"
    un = conn.execute("SELECT details FROM alerts WHERE type = 'AIS_UNCONFIRMED'").fetchone()[0]
    assert un["navire"]["vessel_id"] == ids["grand"]
    assert conn.execute("SELECT status FROM analyses WHERE id = %s", (aid,)).fetchone()[0] == "done"
    # Résultat reçu deux fois : ni détection ni alerte en double
    again = sentinel.ingest(conn, w, RULES)
    assert again["detections"] == 0
    assert conn.execute("SELECT count(*) FROM detections WHERE analysis_id = %s", (aid,)).fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 3


def test_daily_cap_refuses_clearly(conn, monkeypatch):
    from mars import sentinel
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    pid, _ids, t0 = _setup(conn, now)
    conn.execute("INSERT INTO analyses (pass_id, aoi, mode, status, source, scenes, requested_at) VALUES "
                 "(%s, ST_MakeEnvelope(0, 0, 1, 1, 4326)::geography, 'full', 'done', 'sentinel1', '[{}, {}]', %s)",
                 (conn.execute("INSERT INTO sar_passes (product_name, acquired_at, mission, satellite) VALUES "
                               "('S1C_OR9998', %s, 'S1', 'S1C') RETURNING id", (t0 - timedelta(days=1),)).fetchone()[0],
                  now - timedelta(hours=1)))
    monkeypatch.setenv("CDSE_ACCESS_KEY", "k")
    monkeypatch.setenv("CDSE_SECRET_KEY", "s")
    stac = FakeStac([{"id": "S1C_A", "debut": t0.isoformat(), "fin": (t0 + timedelta(seconds=25)).isoformat(),
                      "emprise": SQUARE}])
    r = sentinel.launch(conn, None, RULES, now, stac)
    assert r["refus"].startswith("plafond de 2 scènes par jour : 2 déjà analysée(s)")
    assert conn.execute("SELECT count(*) FROM analyses WHERE pass_id = %s", (pid,)).fetchone()[0] == 0
