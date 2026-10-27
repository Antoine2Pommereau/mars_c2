"""Rendez vous après le lot Z7 (règles 2026.10.57) : durée minimale de 60 min, balises de pêche écartées, travaux
offshore dans un parc éolien marqués et abaissés, rendez vous dans le dossier des deux navires. Les cas reprennent les
épisodes réels mesurés du 06 au 24/10/2026 : NORTHERN OSPREY bord à bord de TORM ALMENA puis de PHILOTIMOS, EEMS DOVER
et BOLD TERN dans le parc de Courseulles, B1 et M2. En base : MARS_TEST_DATABASE_URL."""
import asyncio
import copy
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

from mars.config import ROOT, load_rules
from mars.rules import balise, run_rendezvous, travaux_offshore

URL = os.environ.get("MARS_TEST_DATABASE_URL")
RULES = load_rules()
D = datetime(2031, 5, 11, tzinfo=timezone.utc)
MMSI0 = 299990100                      # MMSI d'essai, effacés à la fin


def test_rules_version_and_thresholds():
    assert RULES["version"] >= "2026.10.57"
    assert RULES["rendezvous"]["min_duration_min"] == 60 and RULES["rendezvous"]["max_distance_m"] == 500
    assert RULES["ais_gap"]["min_prior_messages"] == 8 and RULES["ais_gap"]["mode"] == "observation"


def test_fishing_buoys_are_recognised_ships_are_not():
    cfg = RULES["rendezvous"]["balises"]
    assert balise({"mmsi": 227000001, "name": "B1", "length_m": None}, cfg)
    assert balise({"mmsi": 227000002, "name": "M2", "length_m": 3}, cfg)
    assert balise({"mmsi": 227000003, "name": "BUOY3 COEUR DE MARIN", "length_m": 40}, cfg)
    assert balise({"mmsi": 227000004, "name": "NET 85%", "length_m": None}, cfg)
    assert balise({"mmsi": 982270001, "name": "LE VIEUX MARIN", "length_m": None}, cfg)   # engin associé (98)
    assert balise({"mmsi": 992271234, "name": "CARDINALE NORD", "length_m": None}, cfg)   # aide à la navigation
    for v in ({"mmsi": 538005605, "name": "NORTHERN OSPREY", "length_m": 180},
              {"mmsi": 227000005, "name": "M2", "length_m": 30},           # un vrai navire nommé d'un code
              {"mmsi": 227000006, "name": "PLANET EXPLORER", "length_m": 80},
              {"mmsi": 227000007, "name": None, "length_m": None}):
        assert balise(v, cfg) is None, v


@pytest.fixture
def base(monkeypatch):
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    conn = psycopg.connect(URL, autocommit=True)
    _nettoyer(conn)
    ids = {}
    for k, (nom, typ, lg) in enumerate((("NORTHERN OSPREY", "Tanker", 180), ("TORM ALMENA", "Tanker", 183),
                                         ("PHILOTIMOS", "Cargo", 190), ("EEMS DOVER", "Cargo", 90),
                                         ("BOLD TERN", "Other", 132), ("CHANTIER UN", "Cargo", 80),
                                         ("CHANTIER DEUX", "Cargo", 60), ("B1", None, None),
                                         ("F/V ESSAI", "Fishing", 20))):
        ids[nom] = conn.execute("INSERT INTO vessels (mmsi, name, ship_type, length_m) VALUES (%s, %s, %s, %s) "
                                "RETURNING id", (MMSI0 + k, nom, typ, lg)).fetchone()[0]
    parc = conn.execute(
        """INSERT INTO infrastructure (region_id, kind, name, source, geom)
           VALUES ((SELECT id FROM regions WHERE name = 'Manche'), 'windfarm', 'Essai Courseulles', 'essai_z7',
                   ST_MakeEnvelope(-0.54, 49.45, -0.48, 49.49, 4326)::geography) RETURNING id""").fetchone()[0]

    def rencontre(a, b, debut, minutes, lon, lat, ecart_m):
        dlon = ecart_m / (111320 * 0.65)
        rows = []
        for m in range(minutes - 9):                  # tranches de 10 min : dernière tranche comprise dans la durée
            t = debut + timedelta(minutes=m)
            rows += [(ids[a], t, lon, lat), (ids[b], t, lon + dlon, lat)]
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO positions (vessel_id, ts, geom, sog_kn) VALUES (%s, %s, "
                            "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 0.4)", rows)

    # NORTHERN OSPREY : trois rencontres bord à bord à 25 km au large, de 70, 80 et 90 min (réelles : 11 et 12/10)
    rencontre("NORTHERN OSPREY", "TORM ALMENA", D + timedelta(hours=10, minutes=40), 70, -0.2489, 49.6115, 13)
    rencontre("NORTHERN OSPREY", "TORM ALMENA", D + timedelta(hours=17), 80, -0.2521, 49.6109, 8)
    rencontre("NORTHERN OSPREY", "PHILOTIMOS", D + timedelta(days=1, hours=6, minutes=30), 90, -0.2359, 49.6226, 37)
    # Chantier du parc : BOLD TERN déclaré « Other » ; puis deux cargos dont l'un revient dans le parc chaque jour
    rencontre("EEMS DOVER", "BOLD TERN", D + timedelta(hours=8, minutes=50), 130, -0.5109, 49.4688, 153)
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO positions (vessel_id, ts, geom, sog_kn) VALUES (%s, %s, "
                        "ST_SetSRID(ST_MakePoint(-0.50, 49.47), 4326)::geography, 3)",
                        [(ids["CHANTIER UN"], D - timedelta(days=j, hours=-12)) for j in (1, 2, 3)])
    rencontre("CHANTIER UN", "CHANTIER DEUX", D + timedelta(hours=14), 100, -0.52, 49.475, 120)
    # Balise de pêche et son chalutier, 90 min au large
    rencontre("B1", "F/V ESSAI", D + timedelta(hours=5, minutes=50), 90, -3.9444, 43.6395, 412)
    yield conn, ids, parc
    _nettoyer(conn)
    conn.close()


def _nettoyer(conn):
    vids = [r[0] for r in conn.execute("SELECT id FROM vessels WHERE mmsi BETWEEN %s AND %s", (MMSI0, MMSI0 + 50))]
    if vids:
        alertes = [r[0] for r in conn.execute(
            "SELECT DISTINCT alert_id FROM alert_evidence WHERE evidence_type = 'vessel' AND evidence_id = ANY(%s)",
            (vids,))]
        conn.execute("DELETE FROM alert_actions WHERE alert_id = ANY(%s)", (alertes,))
        conn.execute("DELETE FROM alert_evidence WHERE alert_id = ANY(%s)", (alertes,))
        conn.execute("DELETE FROM alerts WHERE id = ANY(%s)", (alertes,))
        conn.execute("DELETE FROM positions WHERE vessel_id = ANY(%s)", (vids,))
        conn.execute("DELETE FROM vessels WHERE id = ANY(%s)", (vids,))
    conn.execute("DELETE FROM infrastructure WHERE source = 'essai_z7'")


async def _connexion():
    """Comme le conteneur taches : JSON et JSONB décodés."""
    import asyncpg
    import json
    c = await asyncpg.connect(URL)
    for t in ("json", "jsonb"):
        await c.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    return c


def _regle(rules):
    async def go():
        c = await _connexion()
        try:
            return await run_rendezvous(c, D, D + timedelta(days=2), rules)
        finally:
            await c.close()
    return asyncio.run(go())


def _alertes(conn, ids):
    out = {}
    for aid, sev, details in conn.execute(
            "SELECT a.id, a.severity, a.details FROM alerts a WHERE a.type = 'RENDEZVOUS' AND a.id IN "
            "(SELECT alert_id FROM alert_evidence WHERE evidence_type = 'vessel' AND evidence_id = ANY(%s))",
            (list(ids.values()),)):
        noms = tuple(v["name"] for v in details["navires"])
        out.setdefault(noms, []).append((aid, sev, details))
    return out


def test_danish_thresholds_missed_northern_osprey(base):
    conn, ids, _parc = base
    danois = copy.deepcopy(RULES)
    danois["rendezvous"]["min_duration_min"] = 120
    _regle(danois)
    a = _alertes(conn, ids)
    assert not any("NORTHERN OSPREY" in k for k in a), "les rencontres de 70 à 90 min passaient sous les 120 min"
    assert list(a) == [("EEMS DOVER", "BOLD TERN")]


def test_sixty_minutes_buoys_and_offshore_works(base):
    conn, ids, _parc = base
    res = _regle(RULES)
    a = _alertes(conn, ids)
    osprey = a[("NORTHERN OSPREY", "TORM ALMENA")] + a[("NORTHERN OSPREY", "PHILOTIMOS")]
    assert len(osprey) == 3 and {s for _i, s, _d in osprey} == {"elevee"}         # bord à bord, à plus de 20 km
    assert all(d["bord_a_bord"] and not d["travaux_offshore"] for _i, _s, d in osprey)
    assert "60 min au moins" in osprey[0][2]["motif"]
    # Chantier : gardé, marqué, abaissé ; par le type déclaré, puis par la présence répétée dans le parc
    (_i, sev, d), = a[("EEMS DOVER", "BOLD TERN")]
    assert sev == "faible" and "Courseulles" in d["travaux_offshore"]["parc"]   # le vrai parc, ou celui de l'essai
    assert d["travaux_offshore"]["navire"] == "BOLD TERN" and "Other" in d["travaux_offshore"]["motif"]
    assert any(t.startswith("Travaux offshore") for t in d["contexte"])
    (_i, sev, d), = a[("CHANTIER UN", "CHANTIER DEUX")]
    assert sev == "faible" and d["travaux_offshore"]["navire"] == "CHANTIER UN"
    assert "3 jours" in d["travaux_offshore"]["motif"]
    # Balise : aucune alerte, comptée à part
    assert not any("B1" in k for k in a)
    assert res["exclus_balises"] == 1 and res["dont_travaux_offshore"] == 2


def test_offshore_works_never_lower_a_watched_vessel(base):
    conn, ids, _parc = base

    async def go():
        c = await _connexion()
        try:
            x = {"lon": -0.5109, "lat": 49.4688, "t_start": D + timedelta(hours=9)}
            navires = [{"vessel_id": ids["EEMS DOVER"], "name": "EEMS DOVER", "ship_type": "Cargo"},
                       {"vessel_id": ids["BOLD TERN"], "name": "BOLD TERN", "ship_type": "Other"}]
            cfg = RULES["rendezvous"]["travaux_offshore"]
            return (await travaux_offshore(c, x, navires, cfg, set()),
                    await travaux_offshore(c, x, navires, cfg, {ids["EEMS DOVER"]}),
                    await travaux_offshore(c, {**x, "lon": -0.2489, "lat": 49.6115}, navires, cfg, set()))
        finally:
            await c.close()
    marque, suivi, hors_parc = asyncio.run(go())
    assert marque and suivi is None and hors_parc is None


def test_northern_osprey_is_one_dossier_and_partners_see_it_too(base, monkeypatch):
    """Ce que montre la Veille : la route /api/dossiers lue par l'interface."""
    conn, ids, _parc = base
    _regle(RULES)
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("MARS_OPERATEURS", raising=False)
    sys.path.insert(0, str(ROOT / "backend"))
    sys.modules.pop("app", None)
    app = importlib.import_module("app")
    from fastapi.testclient import TestClient
    fin = D + timedelta(days=1, hours=9)
    with TestClient(app.app) as c:
        r = c.get("/api/dossiers", params={"end": fin.isoformat()})
        assert r.status_code == 200
        ds = {d["cle"]: d for d in r.json()["dossiers"]}
        osprey = ds[f"navire:{ids['NORTHERN OSPREY']}"]
        assert len(osprey["alertes"]) == 3 and osprey["niveau_cle"] == "a_examiner"
        assert osprey["phrase"] == "Rendez vous 3 fois"
        # Chaque partenaire a la rencontre dans son propre dossier (avant : seulement le plus petit identifiant)
        assert len(ds[f"navire:{ids['TORM ALMENA']}"]["alertes"]) == 2
        assert len(ds[f"navire:{ids['PHILOTIMOS']}"]["alertes"]) == 1
        # Les travaux du parc restent à noter ; la balise n'a pas de dossier
        assert ds[f"navire:{ids['BOLD TERN']}"]["niveau_cle"] == "a_noter"
        assert f"navire:{ids['B1']}" not in ds
        recit = c.get(f"/api/dossiers/navire:{ids['NORTHERN OSPREY']}", params={"end": fin.isoformat()})
        assert recit.status_code == 200 and len(recit.json()["alertes_detail"]) == 3


def test_message_rate_bands_of_the_injection():
    from mars.rules import tranche_de
    t = [(6, 9), (10, None)]
    assert (tranche_de(5, t), tranche_de(6, t), tranche_de(9, t), tranche_de(10, t), tranche_de(40, t)) == \
        (None, "6-9", "6-9", "10+", "10+")


def test_min_pairs_scale_with_the_number_of_days():
    """Socle A : 50 paires pour 18 jours, 39 pour 14 (la zone de 14 jours ne retombe plus à 1 626 cellules)."""
    from mars.rules import min_paires
    z = RULES["reception"]
    assert (min_paires(z, 18), min_paires(z, 14), min_paires(z, 19), min_paires(z, None)) == (50, 39, 53, 50)
    assert min_paires({"min_pairs": 50}, 14) == 50                 # sans période de référence : valeur telle quelle
