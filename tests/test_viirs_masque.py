"""Masque des nuages VIIRS (lot viirs-masque, 25/10/2026) : adresses cherchées au catalogue CMR, granule sans masque
sous une lune forte « non évaluable », état des nuits du bilan, retraitement de nuits déjà traitées ; en base
(MARS_TEST_DATABASE_URL), granules écrits avec l'état de leur masque et nuit marquée « non évaluable », jamais
« 0 détection ». Le chemin du travailleur (téléchargement, vérification, transmission au modèle) est éprouvé dans
l'image d'allenai : scripts/verifier_images.sh viirs-masque."""
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from mars import viirs
from mars.config import load_rules

RULES = load_rules()


class FakeCMR:
    """Catalogue CMR : réponses par produit, une erreur pour les produits de `en_panne`."""

    def __init__(self, entries: dict, en_panne=()):
        self.entries, self.en_panne, self.produits = entries, set(en_panne), []

    def get(self, _url, params=None, **_k):
        prod = params["short_name"]
        self.produits.append(prod)
        if prod in self.en_panne:
            raise ConnectionError("catalogue injoignable")
        feed = [{"time_start": "2026-10-23T01:24:00Z",
                 "links": [{"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": u}]}
                for u in self.entries.get(prod, [])]
        return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"feed": {"entry": feed}}})()


LANCE = "https://nrt3.modaps.eosdis.nasa.gov/api/v2/content/archives/allData/5201"
SIPS = "https://sips-data.ssec.wisc.edu/nrt/1"
LAADS = "https://data.laadsdaac.earthdatacloud.nasa.gov/prod-lads"


def _dnb(sat_dnb, sat_geo, heure):
    return {sat_dnb + "_NRT": [f"{LANCE}/{sat_dnb}_NRT/2026/296/{sat_dnb}_NRT.A2026296.{heure}.021.2026296030248.nc"],
            sat_geo + "_NRT": [f"{LANCE}/{sat_geo}_NRT/2026/296/{sat_geo}_NRT.A2026296.{heure}.021.2026296030248.nc"]}


def test_mask_addresses_follow_the_rules_order_nrt_first():
    cmr = FakeCMR({**_dnb("VJ102DNB", "VJ103DNB", "0036"),
                   "CLDMSK_L2_VIIRS_NOAA20_NRT": [f"{SIPS}/CLDMSK_L2_VIIRS_NOAA20.A2026296.0036.001.nrt.nc"],
                   "CLDMSK_L2_VIIRS_NOAA20": [f"{LAADS}/CLDMSK_L2_VIIRS_NOAA20/CLDMSK_L2_VIIRS_NOAA20.A2026296.0036.002.x.nc"]})
    (g,) = viirs.sat_granules(cmr, date(2026, 10, 22), "NOAA20", ("VJ102DNB", "VJ103DNB"), RULES)
    assert g["nom"] == "NOAA20.A2026296.0036"
    assert [u.split("/")[2] for u in g["masque_urls"]] == ["sips-data.ssec.wisc.edu", "data.laadsdaac.earthdatacloud.nasa.gov"]
    assert "masque_recherche" not in g


def test_noaa21_has_only_the_standard_mask_and_a_missing_mask_is_an_empty_list():
    cmr = FakeCMR({**_dnb("VJ202DNB", "VJ203DNB", "0124"),
                   "CLDMSK_L2_VIIRS_NOAA21": [f"{LAADS}/CLDMSK_L2_VIIRS_NOAA21/CLDMSK_L2_VIIRS_NOAA21.A2026296.0124.001.x.nc"]})
    (g,) = viirs.sat_granules(cmr, date(2026, 10, 22), "NOAA21", ("VJ202DNB", "VJ203DNB"), RULES)
    assert len(g["masque_urls"]) == 1 and "NOAA21_NRT" not in " ".join(cmr.produits)
    cmr = FakeCMR(_dnb("VJ202DNB", "VJ203DNB", "0124"))           # masque pas encore publié
    (g,) = viirs.sat_granules(cmr, date(2026, 10, 22), "NOAA21", ("VJ202DNB", "VJ203DNB"), RULES)
    assert g["masque_urls"] == []


def test_mask_catalogue_failure_never_blocks_the_granule():
    cmr = FakeCMR(_dnb("VJ102DNB", "VJ103DNB", "0036"), en_panne={"CLDMSK_L2_VIIRS_NOAA20_NRT", "CLDMSK_L2_VIIRS_NOAA20"})
    (g,) = viirs.sat_granules(cmr, date(2026, 10, 22), "NOAA20", ("VJ102DNB", "VJ103DNB"), RULES)
    assert g["masque_urls"] == [] and "catalogue injoignable" in g["masque_recherche"]


@pytest.mark.parametrize("masque, lune, attendu", [
    ({"etat": "fourni"}, 90.0, False),          # masque donné : la conclusion vaut
    ({"etat": "absent"}, 90.0, True),           # nuits du 22 et du 23/10 : scène éteinte par le modèle
    ({"etat": "absent"}, 80.0, False),          # seuil d'allenai : strictement au dessus de 80 %
    ({"etat": "absent"}, 50.0, False),          # le masque ne sert pas au modèle sous 80 %
    (None, 90.0, True),                         # ancien travailleur, sans état du masque
    ({"etat": "absent"}, None, False),
])
def test_granule_without_mask_under_bright_moon_is_not_evaluable(masque, lune, attendu):
    motif = viirs.granule_non_evaluable({"masque": masque, "lune_moyenne": lune}, RULES)
    assert (motif is not None) == attendu
    if attendu:
        assert "masque des nuages absent" in motif and "aucune conclusion" in motif


def test_night_state_never_reads_as_zero_detections():
    n = {"granules": 12, "en_erreur": 0, "granules_non_evaluables": 12}
    assert viirs.etat_nuit(n) == "non évaluable"
    assert viirs.etat_nuit({**n, "granules_non_evaluables": 4}) == "partielle"
    assert viirs.etat_nuit({**n, "granules_non_evaluables": 0}) == "évaluable"
    assert viirs.etat_nuit({"granules": 3, "en_erreur": 3, "granules_non_evaluables": 0}) == "aucune granule exploitable"


class FakeConn:
    def __init__(self, faits):
        self.faits, self.requetes = faits, []

    def execute(self, sql, params=None):
        self.requetes.append(sql)
        rows = [(n,) for n in self.faits] if "FROM viirs_granules" in sql else []
        return type("C", (), {"fetchall": lambda s: rows})()


def test_reprocessing_named_nights_takes_granules_already_done(monkeypatch):
    vus = []
    monkeypatch.setattr(viirs, "sat_granules", lambda _s, n, sat, _p, _r: vus.append(n) or [
        {"nom": f"{sat}.A2026296.0036", "satellite": sat, "dnb": "x", "dnb_url": "u", "geo_url": "v", "debut": "t",
         "masque_urls": []}])
    conn = FakeConn({"NOAA20.A2026296.0036"})
    now = datetime(2026, 10, 25, 7, tzinfo=timezone.utc)
    p = viirs.plan(conn, None, RULES, now, [date(2026, 10, 22), date(2026, 10, 23)])
    assert set(vus) == {date(2026, 10, 22), date(2026, 10, 23)}
    assert "NOAA20.A2026296.0036" in {g["nom"] for g in p["granules"]}
    assert not any("FROM viirs_granules" in q for q in conn.requetes)     # ni « déjà traitée » ni rattrapage
    vus.clear()
    p = viirs.plan(conn, None, RULES, now)                                # rattrapage ordinaire : déjà traitée, écartée
    assert "NOAA20.A2026296.0036" not in {g["nom"] for g in p["granules"]}
    vus.clear()
    viirs.plan(conn, None, RULES, now + timedelta(days=1))                # le 26/10 : la nuit du 22 n'est plus relue
    assert date(2026, 10, 22) not in vus


# En base de test

URL = os.environ.get("MARS_TEST_DATABASE_URL")


@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL, autocommit=True)
    for t in ("viirs_detections", "viirs_granules"):
        c.execute(f"DELETE FROM {t}")
    yield c
    c.close()


def _travailleur(conn, granules: list[tuple]) -> dict:
    """granules : (heure, lune, masque, détections) ; nuit du 22/10/2026."""
    params, res = [], []
    for heure, lune, masque, n in granules:
        start = datetime(2026, 10, 23, int(heure[:2]), int(heure[2:]), tzinfo=timezone.utc)
        nom = f"NOAA20.A2026296.{heure}"
        params.append({"nom": nom, "satellite": "NOAA20", "dnb": f"VJ102DNB_NRT.A2026296.{heure}.nc",
                       "debut": start.isoformat(), "nuit": "2026-10-22"})
        g = {"dnb": f"VJ102DNB_NRT.A2026296.{heure}.nc", "debut": start.isoformat(),
             "fin": (start + timedelta(minutes=6)).isoformat(), "statut": ["processed"], "lune_moyenne": lune,
             "emprise": [[-10, 52], [10, 52], [10, 40], [-10, 40], [-10, 52]],
             "detections": [{"lon": -6.5 + k * 0.1, "lat": 48.5, "nanowatts": 90.0} for k in range(n)]}
        if masque == "fourni":
            g["masque"] = {"etat": "fourni", "source": "data.laadsdaac.earthdatacloud.nasa.gov",
                           "fichier": "CLDMSK_L2_VIIRS_NOAA20.A2026296.nc", "ciel_clair_part": 0.42}
        elif masque == "absent":
            g["masque"] = {"etat": "absent", "motif": "masque des nuages absent : aucun masque publié pour ce granule"}
        res.append(g)
    wid = conn.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, etat, resultat) "
                       "VALUES ('viirs', 'DEV1-M', 'fr-par-1', 'x', %s, 'resultats', %s) RETURNING id",
                       (json.dumps({"granules": params}), json.dumps({"granules": res, "mesures": {}}))).fetchone()[0]
    return {"id": wid, "parametres": {"granules": params}, "resultat": {"granules": res}}


def test_bright_night_without_mask_is_not_evaluable_in_base_and_bilan(conn):
    out = viirs.ingest(conn, _travailleur(conn, [("0036", 90.0, "absent", 0), ("0218", 90.0, None, 0)]), RULES)
    assert out["granules_non_evaluables"] == 2 and out.get("masques_fournis", 0) == 0
    rows = conn.execute("SELECT masque, masque_motif, non_evaluable FROM viirs_granules ORDER BY nom").fetchall()
    assert [r[0] for r in rows] == ["absent", "absent"]
    assert "aucun masque publié" in rows[0][1] and "non transmis par le travailleur" in rows[1][1]
    assert all("lune à 90 %" in r[2] for r in rows)
    (n,) = [x for x in viirs.bilan(conn, date(2026, 10, 22))["nuits"] if x["nuit"] == date(2026, 10, 22)]
    assert n["etat"] == "non évaluable" and n["masques"] == 0 and float(n["lune"]) == 90


def test_masked_granule_concludes_and_the_night_is_partial(conn):
    viirs.ingest(conn, _travailleur(conn, [("0036", 90.0, "fourni", 2), ("0218", 90.0, "absent", 0),
                                           ("0130", 50.0, "absent", 1)]), RULES)
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT nom, masque, round(ciel_clair_part::numeric, 2)::float8, non_evaluable IS NULL FROM viirs_granules").fetchall()}
    assert rows["NOAA20.A2026296.0036"] == ("fourni", 0.42, True)
    assert rows["NOAA20.A2026296.0218"][2] is False            # sans masque sous la lune : aucune conclusion
    assert rows["NOAA20.A2026296.0130"][2] is True             # sous 80 % : la conclusion vaut sans masque
    (n,) = [x for x in viirs.bilan(conn, date(2026, 10, 22))["nuits"] if x["nuit"] == date(2026, 10, 22)]
    assert n["etat"] == "partielle" and n["masques"] == 1 and n["granules_non_evaluables"] == 1 and n["detections"] == 3
