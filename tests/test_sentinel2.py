"""Lot S2, Sentinel 2 : décisions pures (tuile, heure propre, garde des nuages, masque, grille, vignette en couleurs
réelles, octets lus, fusion sans Doppler) et, avec MARS_TEST_DATABASE_URL, la chaîne sur la base de test avec de faux
clients Copernicus et Scaleway : sélection et garde avant lancement, masque du L2A attendu puis absent, résultats
(fenêtre close « nuages », navire sous un nuage écarté, alertes de source sentinel2, vignette de contexte pour une
détection liée à une alerte), tip and cue sur un passage optique, plafond propre et budget partagé, heure des tuiles
au calendrier."""
import base64
import importlib.util
import io
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from mars import sentinel2 as S2
from mars.config import ROOT, load_rules

VERSIONNEES = load_rules()
RULES = {**VERSIONNEES, "sentinel2": {**VERSIONNEES["sentinel2"], "scenes_max_par_jour": 2}}
P2 = RULES["sentinel2"]
URL = os.environ.get("MARS_TEST_DATABASE_URL")
SQUARE = {"type": "Polygon", "coordinates": [[[-6.5, 47.8], [-5.5, 47.8], [-5.5, 48.4], [-6.5, 48.4], [-6.5, 47.8]]]}
MTD = (b'<?xml version="1.0" encoding="UTF-8"?>\n<n1:Level-1C_Tile_ID>\n  <n1:General_Info>\n'
       b'    <TILE_ID metadataLevel="Brief">S2B_OPER_MSI_L1C_TL_2BPS_20261017T131842_A050213_T30UXA_N05.13</TILE_ID>\n'
       b'    <SENSING_TIME metadataLevel="Standard">2026-10-17T11:17:16.324738Z</SENSING_TIME>\n')


def _travailleur():
    spec = importlib.util.spec_from_file_location("travailleur_sentinel2", ROOT / "travailleurs" / "sentinel2.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


W = _travailleur()


def _item(tid="T30UUU", cloud=10.0, t="2026-10-17T11:10:49.024Z", sat="sentinel-2b", emprise=SQUARE):
    base = f"s3://eodata/Sentinel-2/MSI/L1C/2026/10/17/S2B_MSIL1C_X_{tid}.SAFE/GRANULE/L1C_{tid}_A1_X/IMG_DATA/{tid}_X"
    return {"id": f"S2B_MSIL1C_20261017T111049_N0513_R137_{tid}_X", "geometry": emprise,
            "properties": {"datetime": t, "platform": sat, "grid:code": f"MGRS-{tid[1:]}", "eo:cloud_cover": cloud},
            "assets": {b: {"href": f"{base}_{b}.jp2", "file:size": 1000} for b in S2.BANDES}}


# Versionné, livré coupé

def test_versioned_settings_delivered_switched_off():
    s2 = VERSIONNEES["sentinel2"]
    assert s2["scenes_max_par_jour"] == 0 and not S2.actif(VERSIONNEES) and S2.actif(RULES)
    assert s2["nuages_annonce_max_pct"] == 50 and s2["nuages_fenetre_max_pct"] == 20 and s2["l2a_attente_h"] == 3
    assert VERSIONNEES["sentinel"]["budget_mensuel_eur"] == 8.0            # budget partagé, porté par la section sentinel
    t = VERSIONNEES["travailleurs"]["types"]["sentinel2"]
    assert t["commercial_type"] == "GP1-S" and "@sha256:0ce4cbee" in t["image"] and t["tache_par_le_reseau"]
    assert [p["sha256"][:8] for p in s2["poids"]] == ["2547ad10", "303cd443", "788c3c9c"]
    assert [p.get("satlas") for p in s2["poids"]] == [None, None, "Sentinel2_SwinB_SI_MS"]


# Catalogue et heure propre de la tuile

def test_catalogue_item_becomes_a_tile_with_its_metadata_path():
    t = S2.tuile_de(_item())
    assert t["tuile"] == "T30UUU" and t["satellite"] == "S2B" and set(t["bandes"]) == set(S2.BANDES)
    assert t["mtd"].endswith("/GRANULE/L1C_T30UUU_A1_X/MTD_TL.xml") and t["nuages_annonce"] == 10.0
    incomplet = _item()
    del incomplet["assets"]["B8A"]
    assert S2.tuile_de(incomplet) is None                                  # le modèle exige les 13 bandes


def test_tile_sensing_time_is_read_in_the_first_kilobytes():
    assert S2.sensing_time(MTD) == datetime(2026, 10, 17, 11, 17, 16, 324738, tzinfo=timezone.utc)
    assert S2.sensing_time(b"<xml>sans heure</xml>") is None


# Garde des nuages avant le lancement et masque du L2A

def test_announced_clouds_drop_a_tile_with_its_reason():
    gardees, ecartees = S2.garde_annonce([{"tuile": "A", "nuages_annonce": 12.0}, {"tuile": "B", "nuages_annonce": 71.0},
                                          {"tuile": "C", "nuages_annonce": None}], P2)
    assert [t["tuile"] for t in gardees] == ["A", "C"]
    assert ecartees[0]["ecarte"] == "nuages annoncés : 71 % sur la tuile, au delà de 50 %"


def test_l2a_mask_is_awaited_three_hours_then_analysis_goes_without_it():
    t = datetime(2026, 10, 17, 11, 17, tzinfo=timezone.utc)
    assert S2.etat_masque("s3://x/SCL_20m.jp2", t, t + timedelta(hours=1), P2) == "present"
    assert S2.etat_masque(None, t, t + timedelta(hours=2, minutes=59), P2) == "attente"
    assert S2.etat_masque(None, t, t + timedelta(hours=3, minutes=1), P2) == "absent"


# Travailleur : masque, grille, vignette, octets

def test_window_cloud_share_and_grid_ignore_the_ship_and_its_wake():
    scl = np.full((100, 100), 6, dtype="uint8")             # eau
    scl[10:12, 40:60] = 9                                   # navire et sillage pris pour un nuage (2 %)
    assert W.nuages_pct(scl, P2["masque_classes"]) == 0.4
    scl[60:100, :] = 8                                      # banc de nuages sur 40 % au sud de l'image
    assert W.nuages_pct(scl, P2["masque_classes"]) == 40.4
    lons, lats = np.meshgrid(np.linspace(0, 1, 100), np.linspace(1, 0, 100))
    lignes = W.grille(lons, lats, np.isin(scl, P2["masque_classes"]), scl > 0, [0, 0, 1, 1], 4, 25)
    assert lignes[0] == "1111" and lignes[3] == "0000"       # sud nuageux, nord dégagé (navire ignoré)
    assert S2.cellule(lignes, [0, 0, 1, 1], 0.5, 0.1) == "1" and S2.cellule(lignes, [0, 0, 1, 1], 0.5, 0.9) == "0"
    assert S2.cellule(lignes, [0, 0, 1, 1], 2.0, 0.5) is None


def test_true_colour_vignette_is_small_and_georeferenced():
    from affine import Affine
    from PIL import Image
    from rasterio.crs import CRS
    rgb = np.full((600, 600, 3), 0.03, dtype="float32")      # mer sombre, 10 m par pixel
    rgb[295:305, 280:320] = 0.4                              # navire clair au centre
    T = Affine(10, 0, 500000, 0, -10, 5_400_000)
    img = {"rgb": rgb, "transform": T, "crs": CRS.from_epsg(32630)}
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(32630, 4326, always_xy=True).transform(503000, 5_397_000)
    png, geo = W.vignette(img, lon, lat, 2)
    pic = Image.open(io.BytesIO(png))
    assert pic.size == (200, 200) and pic.mode == "P" and len(png) < 40_000
    x = geo["x"][0] * lon + geo["x"][1] * lat + geo["x"][2]
    y = geo["y"][0] * lon + geo["y"][1] * lat + geo["y"][2]
    assert abs(x - 100) < 1.5 and abs(y - 100) < 1.5 and geo["m_par_px"] == 10
    big, geo10 = W.vignette(img, lon, lat, 10, pas=2)        # contexte : 10 km à 20 m, débordant de l'image
    assert Image.open(io.BytesIO(big)).size == (500, 500) and geo10["m_par_px"] == 20
    lo, hi = W.etirement(rgb)
    assert lo == pytest.approx(0.03) and hi >= lo + 0.08                      # étirement adapté à la mer


def test_bytes_read_come_from_the_gdal_journal(tmp_path):
    j = tmp_path / "gdal.log"
    j.write_text("JP2OpenJPEG: info\nVSICURL: Downloading 16384-32767 (https://x)...\nVSICURL: Downloading 0-99 (y)\n")
    assert W.octets_journal(str(j), 2) == (16384 + 100 + 2 * W.OUVERTURE, 4)
    assert j.read_text() == ""                                                # journal vidé après lecture


# Fusion sans Doppler

def test_optical_fusion_has_no_doppler_tolerance():
    import pandas as pd
    from mars.fusion.pipeline import fuse
    from mars.sentinel import track_heading
    t0 = pd.Timestamp("2026-10-17T11:17:16Z")
    # Navire AIS à 15 nœuds plein est, orbite descendante (trace vers le sud) : au radar, l'écho d'un navire qui
    # s'approche ou s'éloigne du satellite est décalé le long de la trace ; détection 900 m au nord de sa position
    pos = pd.DataFrame([{"vessel_id": 1, "mmsi": 1, "name": "A", "length_m": 120.0, "ts": t0 - pd.Timedelta(minutes=1),
                         "lat": 48.0, "lon": -6.0, "sog": 15.0, "cog": 90.0},
                        {"vessel_id": 1, "mmsi": 1, "name": "A", "length_m": 120.0, "ts": t0 + pd.Timedelta(minutes=1),
                         "lat": 48.0, "lon": -6.0 + 2 * 463 / 74450, "sog": 15.0, "cog": 90.0}])
    det = pd.DataFrame([{"id": 1, "lon": -6.0 + 463 / 74450, "lat": 48.0 + 900 / 111200, "objectness": 0.99,
                         "vessel_score": 0.99, "fishing_score": None, "length_m": 118.0, "contrast_vv_db": float("nan"),
                         "on_land": False}])
    bbox = (-6.2, 47.9, -5.8, 48.1)
    radar, *_ = fuse(det, pos, t0, bbox, {**RULES, "contrast": {**RULES["contrast"], "obligatoire": False}},
                     track_heading(48.0, False))
    optique, *_ = fuse(det, pos, t0, bbox, S2.regles_fusion(RULES), None)
    assert radar.matched_vessel_id.iloc[0] == 1                # le radar tolère le décalage Doppler
    assert optique.matched_vessel_id.iloc[0] is None           # l'optique, non : 900 m au delà du cercle de 300 m


def test_ship_under_a_cloud_and_visibility_of_ais_ships():
    assert not S2.sous_nuage(7.5, P2) and S2.sous_nuage(40.0, P2) and not S2.sous_nuage(None, P2)
    f = {"etat": "lue", "bbox": [-6.2, 47.9, -5.8, 48.1], "grille": ["00", "11"]}
    assert S2.visible([f], -6.0, 47.95)                        # cœur de la fenêtre, cellule dégagée (sud)
    assert not S2.visible([f], -6.0, 48.05)                    # cellule nuageuse (nord)
    assert not S2.visible([f], -6.199, 47.95)                  # marge de la fenêtre
    assert not S2.visible([{**f, "etat": "nuages"}], -6.0, 47.95)
    assert S2.resultat([{"etat": "nuages"}, {"etat": "nuages"}], 0) == "nuages"
    assert S2.resultat([{"etat": "nuages"}, {"etat": "lue"}], 0) == "sans_detection"
    assert S2.resultat([{"etat": "lue"}], 3) == "analysee"


# Sur la base de test

class FakeCopernicus:
    """Faux Copernicus : catalogue L1C et L2A, lecture de MTD_TL.xml et d'essai des clés."""

    def __init__(self, tuiles, scl=True, mtd=MTD):
        self.tuiles, self.scl, self.mtd, self.lectures = tuiles, scl, mtd, []

    def post(self, _url, json=None, **_kw):
        l2a = json["collections"] == ["sentinel-2-l2a"]
        feats = []
        for f in self.tuiles:
            if l2a:
                if self.scl:
                    feats.append({**f, "assets": {"SCL_20m": {"href": f["assets"]["B02"]["href"].replace("B02", "SCL_20m")}}})
            else:
                feats.append(f)

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"features": feats}
        return R()

    def get(self, url, headers=None, **_kw):
        self.lectures.append((url, (headers or {}).get("Range")))
        contenu = self.mtd if url.endswith("MTD_TL.xml") else b"\x00" * 16

        class R:
            status_code = 206
            content = contenu

            def close(self):
                pass
        return R()


@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL)
    c.rollback()
    yield c
    c.rollback()
    c.close()


def _setup(conn, now, nuages=8.0):
    """Un passage Sentinel 2 sur l'ouest de la Bretagne, un câble, un navire sanctionné qui le longe (point de
    déclenchement), un grand navire AIS, la zone de réception fiable."""
    for sql in ("DELETE FROM demandes_origines", "DELETE FROM demandes_observation", "DELETE FROM alert_evidence",
                "DELETE FROM alerts", "DELETE FROM preuves_images", "DELETE FROM detections", "DELETE FROM analyses",
                "DELETE FROM reception_cells", "DELETE FROM fixed_echoes", "DELETE FROM stats_minute",
                "DELETE FROM travailleurs WHERE tache IN ('sentinel', 'sentinel2')",
                "DELETE FROM sar_passes WHERE product_name LIKE 'S2B_OR99%' OR product_name LIKE 'S1C_OR999%'",
                "DELETE FROM infrastructure WHERE source = 'essai'", "DELETE FROM watchlist WHERE imo = 9999021",
                "DELETE FROM positions WHERE vessel_id IN (SELECT id FROM vessels WHERE mmsi IN (273999021, 228999022))",
                "DELETE FROM vessels WHERE mmsi IN (273999021, 228999022)"):
        conn.execute(sql)
    t0 = now - timedelta(hours=5)
    pid = conn.execute(
        "INSERT INTO sar_passes (product_name, platform, acquired_at, ended_at, orbit_direction, footprint, mission, "
        "satellite, mode, statut, nuages) VALUES ('S2B_OR9999', 'sentinel-2b', %s, %s, 'descending', "
        "ST_GeomFromGeoJSON(%s)::geography, 'S2', 'S2B', 'MSI', 'acquis', %s) RETURNING id",
        (t0, t0, json.dumps(SQUARE), nuages)).fetchone()[0]
    reg = conn.execute("SELECT id FROM regions WHERE lower(name) = 'bretagne'").fetchone()[0]
    conn.execute("INSERT INTO infrastructure (region_id, kind, name, source, attrs, geom) VALUES "
                 "(%s, 'cable', 'CABLE ESSAI', 'essai', '{\"type\": \"Câble télécom\"}', "
                 "ST_GeomFromText('LINESTRING(-6.4 48.0, -5.6 48.0)', 4326)::geography)", (reg,))
    ids = {}
    for key, mmsi, imo, length in (("liste", 273999021, 9999021, 120), ("grand", 228999022, None, 200)):
        ids[key] = conn.execute("INSERT INTO vessels (mmsi, imo, name, length_m, ais_class) VALUES (%s, %s, %s, %s, 'A') "
                                "RETURNING id", (mmsi, imo, key.upper(), length)).fetchone()[0]
    conn.execute("INSERT INTO watchlist (source, imo, name, risks, datasets, sanctioned) VALUES "
                 "('opensanctions', 9999021, 'LISTE', '{sanction}', '{eu_essai}', true)")
    conn.execute("REFRESH MATERIALIZED VIEW vessel_watch")
    for key, lon0, lat in (("liste", -6.1, 48.01), ("grand", -6.15, 48.06)):
        for k in range(-20, 21):
            conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn, cog_deg) VALUES (%s, %s, "
                         "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 10, 90)",
                         (ids[key], t0 + timedelta(minutes=k), lon0 + k * 0.0041, lat))
    conn.execute("INSERT INTO reception_cells (cx, cy, messages, vessels, hours, coverage, geom) "
                 "SELECT x, y, 100, 5, 10, 0.95, ST_MakeEnvelope(x * 0.1, y * 0.05, (x + 1) * 0.1, (y + 1) * 0.05, 4326)::geography "
                 "FROM generate_series(-65, -55) x, generate_series(956, 968) y")
    return pid, ids, t0


def _faux_lancement(monkeypatch, lances):
    from mars import travailleurs

    def fake(c, _scw, _rules, tache, params, env, **kw):
        lances.append((tache, params, env, kw.get("variante")))
        wid = c.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, verrou) "
                        "VALUES (%s, 'GP1-S', 'fr-par-2', 'x', %s, %s) RETURNING id",
                        (tache, json.dumps(params), tache)).fetchone()[0]
        return {"travailleur": wid}
    monkeypatch.setattr(travailleurs, "launch", fake)
    monkeypatch.setenv("CDSE_TRAVAILLEUR_ACCESS_KEY", "k")
    monkeypatch.setenv("CDSE_TRAVAILLEUR_SECRET_KEY", "s")
    monkeypatch.setenv("CDSE_ACCESS_KEY", "ks")
    monkeypatch.setenv("CDSE_SECRET_KEY", "ss")


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 40, 90)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_switched_off_launches_nothing(conn):
    now = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
    _setup(conn, now)
    r = S2.launch(conn, None, VERSIONNEES, now, FakeCopernicus([_item()]))
    assert r["refus"].startswith("Sentinel 2 coupé (sentinel2.scenes_max_par_jour: 0)")
    assert conn.execute("SELECT count(*) FROM analyses WHERE source = 'sentinel2'").fetchone()[0] == 0


def test_selection_launch_results_and_alerts_end_to_end(conn, monkeypatch):
    from mars import r2 as r2mod
    now = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
    pid, ids, t0 = _setup(conn, now)
    cop = FakeCopernicus([_item("T30UUU", 8.0), _item("T30UVU", 80.0)])
    lances = []
    _faux_lancement(monkeypatch, lances)                    # clés du serveur : lecture de MTD_TL.xml
    # Essai réel : candidats en lecture seule (navire des listes dans l'emprise), tuiles, masque et coût
    conn.execute("UPDATE sar_passes SET watch_ids = %s WHERE id = %s", ([ids["liste"]], pid))
    c = S2.candidats_essai(conn, cop, RULES, now)
    assert c[0]["passage"] == pid and c[0]["fenetres"] == 1 and c[0]["cout_estime_eur"] == pytest.approx(0.1907)
    assert c[0]["tuiles"] == [{"tuile": "T30UUU", "nuages_annonce": 8.0, "masque": "present", "heure_tuile": True}]
    assert conn.execute("SELECT count(*) FROM analyses WHERE source = 'sentinel2'").fetchone()[0] == 0
    p = S2.plan(conn, cop, RULES, now)
    assert [x["pass_id"] for x in p["passages"]] == [pid]
    tuiles = p["passages"][0]["tuiles"]
    assert [t["tuile"] for t in tuiles] == ["T30UUU"]                               # nuages annoncés : T30UVU écartée
    assert p["points_ecartes"][pid][0]["ecarte"].startswith("nuages annoncés : 80 %")
    assert tuiles[0]["masque"] == "present" and tuiles[0]["heure_tuile"]
    assert tuiles[0]["heure"] == "2026-10-17T11:17:16.324738+00:00"                 # heure propre lue dans MTD_TL.xml
    assert any(u.endswith("MTD_TL.xml") and rg == "bytes=0-4095" for u, rg in cop.lectures)
    r = S2.launch(conn, None, RULES, now, cop)
    tache, params, env, variante = lances[0]
    assert tache == "sentinel2" and env == {} and variante["duree_max_min"] == 10 + 3 + 15
    assert '"k"' not in json.dumps(params) and all("url" not in x for x in params["poids"])   # ni clé ni adresse
    assert [p.get("satlas") for p in params["poids"]] == [None, None, "Sentinel2_SwinB_SI_MS"]  # servi localement
    assert params["scenes"][0]["scl"].endswith("SCL_20m.jp2") and len(params["scenes"][0]["fenetres"]) == 1
    aid = r["analyses"][0]
    assert conn.execute("SELECT source, calcul FROM analyses WHERE id = %s", (aid,)).fetchone() == ("sentinel2", "cpu")
    assert S2.plan(conn, cop, RULES, now)["passages"] == []                         # une analyse par passage

    # Résultat : navire des listes vu (apparié), navire sans AIS dégagé (DARK_SHIP optique), navire sous un nuage
    # (écarté) ; le grand navire AIS est dans une cellule dégagée de la fenêtre, sans navire vu (AIS_UNCONFIRMED)
    ts = datetime.fromisoformat(tuiles[0]["heure"])
    off = (ts - t0).total_seconds() / 60 * 0.0041
    f = params["scenes"][0]["fenetres"][0]
    n = 48
    grille = ["0" * n] * n
    res = {"scenes": [{"analyse": aid, "id": tuiles[0]["id"], "tuile": "T30UUU", "debut": tuiles[0]["heure"],
                       "fin": tuiles[0]["heure"], "satellite": "S2B", "masque": "present", "octets_lus": 33_600_000,
                       "octets_scl": 16384,
                       "fenetres": [{"index": 0, "bbox": f["bbox"], "etat": "lue", "nuages_pct": 4.0, "octets": 33_600_000,
                                     "requetes": 201, "lecture_s": 5.0, "analyse_s": 133, "detections": 3,
                                     "grille": grille}],
                       "detections": [
                           {"lon": -6.1 + off, "lat": 48.01, "score": 0.99, "longueur_m": 121, "largeur_m": 18,
                            "vitesse_kn": 10, "cap_deg": 91, "type": "cargo", "nuages_pct": 1.0, "vignette": _png(),
                            "vignette_geo": {"x": [1, 0, 0], "y": [0, 1, 0]}, "contexte": _png()},
                           {"lon": -6.03, "lat": 47.97, "score": 0.97, "longueur_m": 45, "largeur_m": 9, "vitesse_kn": 4,
                            "cap_deg": 200, "type": "fishing", "nuages_pct": 0.0, "vignette": _png(), "contexte": _png()},
                           {"lon": -6.02, "lat": 48.02, "score": 0.9, "longueur_m": 80, "largeur_m": 12, "vitesse_kn": 0,
                            "cap_deg": 10, "type": "cargo", "nuages_pct": 60.0, "vignette": _png(), "contexte": _png()}]}],
           "mesures": {"duree_s": 300, "rss_max_mo": 4300}}
    rangees = []

    class FauxR2:
        def put_verified(self, data, key):
            rangees.append((key, len(data)))
            return {}
    monkeypatch.setattr(r2mod, "R2", FauxR2)
    wid = conn.execute("SELECT travailleur_id FROM analyses WHERE id = %s", (aid,)).fetchone()[0]
    out = S2.ingest(conn, {"id": wid, "resultat": res}, RULES)
    assert out["detections"] == 3
    masques = dict(conn.execute("SELECT type_navire || ':' || round(length_m)::text, coalesce(mask_reason, '') "
                                "FROM detections WHERE analysis_id = %s", (aid,)).fetchall())
    assert masques["cargo:80"] == "nuages"                                         # navire sous un nuage écarté
    alertes = {r[0]: r[1] for r in conn.execute("SELECT type, details FROM alerts").fetchall()}
    assert set(alertes) == {"DARK_SHIP", "AIS_UNCONFIRMED"}
    assert alertes["DARK_SHIP"]["source"] == "sentinel2" and alertes["DARK_SHIP"]["type_navire"] == "fishing"
    assert alertes["DARK_SHIP"]["motif"].startswith("Navire vu en optique (Sentinel 2)")
    assert alertes["AIS_UNCONFIRMED"]["navire"]["vessel_id"] == ids["grand"]
    summary = conn.execute("SELECT summary, status, octets_lus FROM analyses WHERE id = %s", (aid,)).fetchone()
    assert summary[0]["resultat"] == "analysee" and summary[1] == "done" and summary[2] == 33_600_000 + 16384
    assert summary[0]["tuiles"][tuiles[0]["id"]]["heure"] == tuiles[0]["heure"]
    # Vignettes de 2 km pour toutes, contexte de 10 km pour la seule détection liée à une alerte
    roles = dict(conn.execute("SELECT role, count(*) FROM preuves_images WHERE source = 'sentinel2' GROUP BY 1").fetchall())
    assert roles == {"vignette": 3, "contexte": 1}
    assert any("/contexte/" in k for k, _n in rangees)
    assert S2.ingest(conn, {"id": wid, "resultat": res}, RULES)["detections"] == 0      # reçu deux fois


def test_cloudy_window_is_closed_without_inference_and_no_absence_is_claimed(conn, monkeypatch):
    now = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
    pid, ids, _t0 = _setup(conn, now)
    lances = []
    _faux_lancement(monkeypatch, lances)
    r = S2.launch(conn, None, RULES, now, FakeCopernicus([_item()]))
    aid, params = r["analyses"][0], lances[0][1]
    sc = params["scenes"][0]
    res = {"scenes": [{"analyse": aid, "id": sc["id"], "tuile": sc["tuile"], "debut": sc["debut"], "fin": sc["fin"],
                       "masque": "present", "octets_lus": 0, "octets_scl": 300_000, "detections": [],
                       "fenetres": [{"index": 0, "bbox": sc["fenetres"][0]["bbox"], "etat": "nuages",
                                     "nuages_pct": 83.0}]}]}
    wid = conn.execute("SELECT travailleur_id FROM analyses WHERE id = %s", (aid,)).fetchone()[0]
    out = S2.ingest(conn, {"id": wid, "resultat": res}, RULES)
    assert out["fenetres_nuages"] == 1
    s = conn.execute("SELECT summary FROM analyses WHERE id = %s", (aid,)).fetchone()[0]
    assert s["resultat"] == "nuages" and s["alertes_non_confirme"] == 0                 # le grand navire n'est pas « absent »
    assert S2.nuageux(conn, aid, -6.1, 48.01) is True


def test_missing_l2a_waits_then_runs_without_mask_and_alerts_are_to_confirm(conn, monkeypatch):
    now = datetime(2026, 10, 17, 13, tzinfo=timezone.utc)                     # 1 h 43 après la tuile
    _setup(conn, now)
    cop = FakeCopernicus([_item()], scl=False)
    p = S2.plan(conn, cop, RULES, now)
    assert p["passages"] == [] and p["en_attente"][0]["motif"].startswith("masque des nuages (L2A) pas encore publié")
    tard = datetime(2026, 10, 17, 14, 30, tzinfo=timezone.utc)
    p = S2.plan(conn, cop, RULES, tard)
    assert p["passages"][0]["tuiles"][0]["masque"] == "absent"                # analyse sans masque plutôt qu'abandon
    lances = []
    _faux_lancement(monkeypatch, lances)
    r = S2.launch(conn, None, RULES, tard, cop)
    sc = lances[0][1]["scenes"][0]
    assert sc["scl"] is None
    aid = r["analyses"][0]
    res = {"scenes": [{"analyse": aid, "id": sc["id"], "tuile": sc["tuile"], "debut": sc["debut"], "fin": sc["fin"],
                       "masque": "absent", "octets_lus": 1, "fenetres": [{"index": 0, "bbox": sc["fenetres"][0]["bbox"],
                                                                         "etat": "lue"}],
                       "detections": [{"lon": -6.03, "lat": 47.97, "score": 0.97, "longueur_m": 45, "type": "fishing"}]}]}
    wid = conn.execute("SELECT travailleur_id FROM analyses WHERE id = %s", (aid,)).fetchone()[0]
    S2.ingest(conn, {"id": wid, "resultat": res}, RULES)
    types = {r[0]: r[1] for r in conn.execute("SELECT type, details FROM alerts").fetchall()}
    assert set(types) == {"DARK_SHIP"}                                        # aucun AIS_UNCONFIRMED sans masque
    assert types["DARK_SHIP"]["a_confirmer"] and "Masque des nuages absent" in types["DARK_SHIP"]["contexte"][0]


def test_ceiling_is_its_own_and_the_budget_is_shared_with_sentinel_1(conn):
    from mars import sentinel
    now = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
    _setup(conn, now)
    # Sentinel 1 a déjà dépensé presque tout le budget du mois
    conn.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, cree_le, detruit_le, "
                 "cout_reel_eur) VALUES ('sentinel', 'GP1-S', 'fr-par-2', 'x', '{}', %s, %s, 7.9)",
                 (now - timedelta(days=2), now - timedelta(days=2)))
    assert sentinel.usage(conn, now, "sentinel2") == (0, pytest.approx(7.9))
    r = S2.launch(conn, None, RULES, now, FakeCopernicus([_item()]))
    assert r["refus"].startswith("budget mensuel de 8,00 € : 7,90 € engagés")
    # Et Sentinel 2 compte dans le budget de Sentinel 1
    conn.execute("UPDATE travailleurs SET cout_reel_eur = 0.5 WHERE tache = 'sentinel'")
    conn.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, cree_le, detruit_le, "
                 "cout_reel_eur) VALUES ('sentinel2', 'GP1-S', 'fr-par-2', 'x', '{}', %s, %s, 7.4)",
                 (now - timedelta(days=1), now - timedelta(days=1)))
    assert sentinel.usage(conn, now)[1] == pytest.approx(7.9)
    # Plafond propre : une tuile par jour, déjà prise
    one = {**RULES, "sentinel2": {**P2, "scenes_max_par_jour": 1}}
    conn.execute("DELETE FROM travailleurs WHERE tache IN ('sentinel', 'sentinel2')")
    autre = conn.execute("INSERT INTO sar_passes (product_name, platform, acquired_at, mission, satellite, statut) "
                         "VALUES ('S2B_OR9998', 'x', %s, 'S2', 'S2B', 'acquis') RETURNING id", (now,)).fetchone()[0]
    conn.execute("INSERT INTO analyses (pass_id, aoi, mode, status, source, scenes, requested_at) VALUES (%s, "
                 "ST_MakeEnvelope(-6, 48, -5, 49, 4326)::geography, 'full', 'done', 'sentinel2', '[{\"id\": \"x\"}]', %s)",
                 (autre, now - timedelta(hours=1)))
    r = S2.launch(conn, None, one, now, FakeCopernicus([_item()]))
    assert r["refus"].startswith("plafond de 1 tuiles Sentinel 2 par jour")


def test_tip_and_cue_picks_an_optical_pass_and_skips_a_cloudy_one(conn):
    from mars import tip_and_cue as tc
    now = datetime(2026, 10, 17, 9, tzinfo=timezone.utc)
    for sql in ("DELETE FROM demandes_origines", "DELETE FROM demandes_observation", "DELETE FROM alerts",
                "DELETE FROM analyses", "DELETE FROM sar_passes WHERE product_name LIKE 'TIP2_%'"):
        conn.execute(sql)

    def passage(nom, mission, t, nuages=None, statut="prevu"):
        return conn.execute(
            "INSERT INTO sar_passes (product_name, platform, acquired_at, ended_at, footprint, mission, satellite, mode, "
            "statut, nuages) VALUES (%s, 'x', %s, %s, ST_GeomFromGeoJSON(%s)::geography, %s, %s, %s, %s, %s) RETURNING id",
            (nom, t, t, json.dumps(SQUARE), mission, "S2B" if mission == "S2" else "S1C",
             "MSI" if mission == "S2" else "IW", statut, nuages)).fetchone()[0]
    s1 = passage("TIP2_S1", "S1", now + timedelta(hours=8))
    s2 = passage("TIP2_S2", "S2", now + timedelta(hours=2))
    rules = {**RULES, "tip_and_cue": {**RULES["tip_and_cue"], "actif": True}}
    r = tc.creer(conn, rules, now, cle="op:optique", tip="operateur", lon=-6.0, lat=48.0, position_le=now)
    tc.planifier(conn, rules, now, cote=lambda *_: None)
    assert conn.execute("SELECT pass_id, etat FROM demandes_observation WHERE id = %s", (r["demande"],)).fetchone() == \
        (s2, "planifiee")                                                    # passage optique, plus tôt
    # Sentinel 2 coupé : seul le radar compte
    tc.planifier(conn, {**rules, "sentinel2": {**P2, "scenes_max_par_jour": 0}}, now, cote=lambda *_: None)
    assert conn.execute("SELECT pass_id FROM demandes_observation WHERE id = %s", (r["demande"],)).fetchone()[0] == s1
    # Passage optique acquis et annoncé nuageux : écarté
    conn.execute("UPDATE sar_passes SET statut = 'acquis', nuages = 85, acquired_at = %s WHERE id = %s",
                 (now - timedelta(hours=1), s2))
    tc.planifier(conn, rules, now, cote=lambda *_: None)
    assert conn.execute("SELECT pass_id FROM demandes_observation WHERE id = %s", (r["demande"],)).fetchone()[0] == s1


def test_cloudy_circle_sends_the_request_back_to_wait_instead_of_absent(conn):
    from mars import tip_and_cue as tc
    now = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
    pid, _ids, _t0 = _setup(conn, now)
    rules = {**RULES, "tip_and_cue": {**RULES["tip_and_cue"], "actif": True}}
    r = tc.creer(conn, rules, now, cle="op:nuage", tip="operateur", lon=-6.0, lat=48.0, position_le=now)
    bbox = [-6.2, 47.9, -5.8, 48.1]
    aid = conn.execute("INSERT INTO analyses (pass_id, aoi, mode, status, source, scenes, requested_at, timings) VALUES "
                       "(%s, ST_MakeEnvelope(-6, 48, -5, 49, 4326)::geography, 'full', 'done', 'sentinel2', '[]', %s, %s) "
                       "RETURNING id",
                       (pid, now, json.dumps({"T": {"masque": "present", "fenetres": [
                           {"index": 0, "bbox": bbox, "etat": "lue", "grille": ["11", "11"]}]}}))).fetchone()[0]
    conn.execute("UPDATE demandes_observation SET etat = 'planifiee', pass_id = %s, analysis_id = %s, heure_passage = %s, "
                 "position_predite = ST_SetSRID(ST_MakePoint(-6.0, 48.0), 4326)::geography, rayon_m = 1500 WHERE id = %s",
                 (pid, aid, now - timedelta(hours=4), r["demande"]))
    tc.evaluer(conn, aid, rules)
    etat, motif, an = conn.execute("SELECT etat, motif, analysis_id FROM demandes_observation WHERE id = %s",
                                   (r["demande"],)).fetchone()
    assert etat == "en_attente" and an is None and "sous les nuages au cercle : replanifiée" in motif


def test_calendar_takes_the_tile_time_and_keeps_it(conn, monkeypatch):
    from mars import satellites
    monkeypatch.setenv("CDSE_ACCESS_KEY", "ks")              # clés du serveur : 4 Ko de MTD_TL.xml par tuile
    monkeypatch.setenv("CDSE_SECRET_KEY", "ss")
    now = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
    pid, _ids, t0 = _setup(conn, now)
    cop = FakeCopernicus([_item("T30UUU"), _item("T30UVU")], mtd=MTD)
    out = S2.corriger_heures(conn, cop, now)
    assert out["passages"] == 1 and out["tuiles"] == 2
    acq, prise, heures = conn.execute("SELECT acquired_at, heure_prise_de_vue, heure_tuiles FROM sar_passes WHERE id = %s",
                                      (pid,)).fetchone()
    assert acq == datetime(2026, 10, 17, 11, 17, 16, 324738, tzinfo=timezone.utc) and prise == t0
    assert set(heures) == {"T30UUU", "T30UVU"}
    assert S2.corriger_heures(conn, cop, now)["passages"] == 0                     # déjà corrigé, pas relu
    # Le calendrier du lendemain ne remet pas l'heure du catalogue
    conn.execute("UPDATE sar_passes SET product_name = 'S2B_OR9999' WHERE id = %s", (pid,))
    satellites.upsert(conn, [{"key": "S2B_OR9999", "platform": "sentinel-2b", "start": t0, "end": t0, "mission": "S2",
                              "satellite": "S2B", "mode": "MSI", "statut": "acquis", "source": "catalogue",
                              "geoms": [SQUARE], "nuages": 8.0}])
    assert conn.execute("SELECT acquired_at FROM sar_passes WHERE id = %s", (pid,)).fetchone()[0] == acq


def test_nothing_to_analyse_is_said_plainly():
    """C02 : essai-reel affichait le dictionnaire brut des points écartés ; motifs propres à l'optique."""
    from mars.sentinel2 import motif_rien
    assert motif_rien({"refus": "passage 9 inconnu"}, 9) == "passage 9 inconnu"
    assert motif_rien({"en_attente": [{"passage": 9, "motif": "tuiles L1C pas encore au catalogue"}]}, 9) == \
        "en attente : tuiles L1C pas encore au catalogue"
    p = {"points_ecartes": {9: [{"tuile": "30UVU", "points": 2, "ecarte": "nuages annoncés : 64 % sur la tuile"},
                                {"name": "SMYRTOS", "ecarte": "à quai ou au mouillage près de la côte"}]}}
    m = motif_rien(p, 9)
    assert m == ("tuile 30UVU (2 point(s)) : nuages annoncés : 64 % sur la tuile ; SMYRTOS : à quai ou au mouillage "
                 "près de la côte").replace(" ; ", "; ")
    assert "{" not in m and motif_rien({}, 9).startswith("aucun navire des listes")
