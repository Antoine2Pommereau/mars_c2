"""Sentinel 1 robuste (lot C3, processeur seul depuis le lot Z4) : fenêtres utiles, rupture de stock, type de secours,
lots de fenêtres, coût selon le type lancé, reclassement des analyses sans scène aboutie. Les essais sur base sont ignorés sans
MARS_TEST_DATABASE_URL ; la lecture partielle des images et la géolocalisation des fenêtres sont éprouvées dans la
vraie image d'AI2 (scripts/verifier_images.sh fenetres et ai2-fenetres)."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from mars import sentinel, traces
from mars import travailleurs as T
from mars.config import load_rules

RULES = load_rules()
# Garde des types éprouvés (travailleurs.types_eprouves_seuls) levée : ces tests portent sur d'autres mécanismes,
# avec une fausse instance Scaleway ; la garde a ses tests (tests/test_essai_reel.py)
RULES["travailleurs"]["types_eprouves_seuls"] = []
S = RULES["sentinel"]
# Sentinel arrêté (plafond à 0) : les variables d'essai ne valent que pour la commande manuelle
ARRET = {**RULES, "sentinel": {**S, "scenes_max_par_jour": 0}}
URL = os.environ.get("MARS_TEST_DATABASE_URL")
NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
SCENE = {"type": "Polygon", "coordinates": [[[-6.6, 47.6], [-4.6, 47.6], [-4.6, 49.2], [-6.6, 49.2], [-6.6, 47.6]]]}


def _pt(lon, lat):
    return {"lon": lon, "lat": lat, "motif": "navire_liste"}


# Fenêtres

def test_windows_are_merged_when_they_overlap_and_kept_apart_otherwise():
    f = sentinel.fenetres(SCENE, [_pt(-6.0, 48.0), _pt(-5.95, 48.05), _pt(-5.0, 48.8)], S)
    assert len(f) == 2 and sorted(x["points"] for x in f) == [1, 2]
    seul = [x for x in f if x["points"] == 1][0]
    cote_km = (seul["bbox"][3] - seul["bbox"][1]) * 110.574
    assert abs(cote_km - 2 * (S["interet_rayon_m"] + S["fenetre_marge_m"]) / 1000) < 0.1
    assert seul["marge_m"] == S["fenetre_marge_m"] and 500 < seul["km2"] < 650
    assert sentinel.fenetres(SCENE, [_pt(-2.0, 46.0)], S) == []                  # point hors de la scène
    # Chaîne de points proches : tout se réunit, sans chevauchement restant
    chaine = sentinel.fenetres(SCENE, [_pt(-6.2 + 0.15 * k, 48.4) for k in range(8)], S)
    assert len(chaine) == 1 and chaine[0]["points"] == 8


def test_window_area_is_a_small_part_of_the_scene():
    f = sentinel.fenetres(SCENE, [_pt(-6.0, 48.0)], S)
    assert 0.01 < sentinel.part_de_scene(f, SCENE) < 0.05


# Coût selon le type réellement lancé

def test_cost_depends_on_the_launched_type_and_the_windows():
    gp1 = T.type_params(RULES, "sentinel")
    pop2 = T.type_params(RULES, "sentinel", T.variantes(RULES, "sentinel")[1])
    assert gp1["commercial_type"] == "GP1-S" and "--gpus" not in gp1["docker_opts"]
    assert sentinel.cout_calcul(2, S, gp1) == round(gp1["prix_heure"], 4)       # une heure entamée de GP1-S
    assert sentinel.cout_calcul(2, S, pop2) == round(pop2["prix_heure"], 4)
    # Toutes les sessions d'un passage : lots de fenetres_max_cpu fenêtres
    assert sentinel.sessions(9, S) == [4, 4, 1] and sentinel.sessions(4, S) == [4] and sentinel.sessions(0, S) == []
    assert sentinel.estimation(RULES, S, 9) == round(3 * gp1["prix_heure"], 4)
    # Lot C5 : durée de vie sur processeur d'après l'essai réel (5 + 3 par fenêtre + 15), au lieu de 150 min fixes ;
    # historique (25/10/2026) : 2 min de plus par fenêtre, et 3 min de moins sans lui
    assert sentinel.duree_cpu(1, S) == 25 and sentinel.duree_cpu(4, S) == 40
    sans = {**S, "historique": {**S["historique"], "passages": 0}}
    assert sentinel.duree_cpu(1, sans) == 23 and sentinel.duree_cpu(4, sans) == 32


def test_motive_says_one_session_or_lots():
    assert sentinel.motif_calcul(S["fenetres_max_cpu"], S) == sentinel.MOTIF_PETIT
    assert sentinel.motif_calcul(S["fenetres_max_cpu"] + 1, S) == sentinel.MOTIF_LOTS


def test_variants_are_tried_in_the_configured_order():
    v = T.variantes(RULES, "sentinel")
    assert [(T.type_params(RULES, "sentinel", x)["commercial_type"], x["calcul"]) for x in v] == [
        ("GP1-S", "cpu"), ("POP2-8C-32G", "cpu")]                                # voie GPU retirée (lot Z4)
    assert T.type_params(RULES, "sentinel", v[0])["volume"] == "l_ssd"           # GP1-S : disque local
    assert T.type_params(RULES, "sentinel", v[1])["volume"] == "sbs_volume"      # POP2 : volume bloc
    assert T.variante_de(RULES, "sentinel", "POP2-8C-32G", "fr-par-2") == v[1]
    assert T.variante_de(RULES, "sentinel", "L4-1-24G", "fr-par-2") is None       # ancien type : prix du journal absent
    assert T.type_params(RULES, "viirs", None)["commercial_type"] == "DEV1-M"    # VIIRS inchangé
    assert T.en_rupture('POST /instance/v1/zones/fr-par-2/servers/x/action : 412 {"type":"out_of_stock"}')


class Dispo:
    def __init__(self, etats):
        self.etats = etats

    def availability(self, zone):
        return self.etats.get(zone, {})


def test_out_of_stock_types_are_never_tried_and_pop2_backs_up_gp1s(spans):
    scw = Dispo({"fr-par-2": {"GP1-S": "shortage", "POP2-8C-32G": "scarce"}})
    with traces.span("tache sentinel", nouvelle_trace=True):
        c, dispo = sentinel.candidats(scw, RULES)
    assert [T.type_params(RULES, "sentinel", x)["commercial_type"] for x in c] == ["POP2-8C-32G"]
    assert dispo == {"GP1-S fr-par-2": "shortage", "POP2-8C-32G fr-par-2": "scarce"}
    c, _ = sentinel.candidats(Dispo({"fr-par-2": {"GP1-S": "available"}}), RULES)
    assert [T.type_params(RULES, "sentinel", x)["commercial_type"] for x in c] == ["GP1-S", "POP2-8C-32G"]
    fini = spans.get_finished_spans()
    tache = [x for x in fini if x.name == "tache sentinel"][0]
    lectures = [x for x in fini if x.name == "sentinel.disponibilite" and x.parent
                and x.parent.span_id == tache.context.span_id]
    assert len(lectures) == 1                                    # une lecture par zone, dans la trace de l'analyse


@pytest.fixture
def spans():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    mem = InMemorySpanExporter()
    traces.init("mars-test", exportateur=mem)
    yield mem
    mem.clear()


# Sur base : rupture de stock, attente, reprise, repli, abandon

@pytest.fixture
def conn(monkeypatch):
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    monkeypatch.setenv("MARS_IP_PRIVEE", "172.16.8.2")
    monkeypatch.setenv("SCW_PRIVATE_NETWORK_ID", "pn")
    monkeypatch.setenv("CDSE_TRAVAILLEUR_ACCESS_KEY", "k")
    monkeypatch.setenv("CDSE_TRAVAILLEUR_SECRET_KEY", "s")
    c = psycopg.connect(URL)
    yield c
    c.rollback()
    c.close()


def test_out_of_stock_at_start_destroys_everything_and_does_not_count(conn):
    from test_travailleurs_base import FakeScw

    class Rupture(FakeScw):
        def start(self, _zone, sid, **_kw):
            raise T.ScalewayError(f"POST /instance/v1/zones/fr-par-2/servers/{sid}/action : 412 "
                                  '{"type":"out_of_stock","message":"server type GP1-S is out of stock"}')
    conn.execute("DELETE FROM analyses WHERE travailleur_id IS NOT NULL")
    conn.execute("DELETE FROM travailleurs")
    scw = Rupture()
    r = T.launch(conn, scw, RULES, "sentinel", {"scenes": []}, {}, now=NOW, sleep=lambda _s: None)
    assert "rupture" in r and "GP1-S" in r["rupture"]
    assert scw.destroyed and not scw.servers                                      # instance supprimée
    row = conn.execute("SELECT etat, rupture_stock, detruit_le IS NOT NULL FROM travailleurs WHERE id = %s",
                       (r["travailleur"],)).fetchone()
    assert row == ("echec", True, True)
    jr = conn.execute("SELECT status, details FROM task_runs WHERE task = 'travailleur_sentinel' "
                      "ORDER BY started_at DESC LIMIT 1").fetchone()
    assert jr[1]["refus"] is True and jr[1]["cout_estime_eur"] == 0               # ni alerte, ni coût
    today = [dict(zip(("cree_le", "fini_le", "tache"), x)) for x in conn.execute(
        "SELECT cree_le, fini_le, tache FROM travailleurs WHERE scw_server_id IS NOT NULL AND NOT rupture_stock")]
    assert today == []                                                            # hors du plafond quotidien


def _passage(conn):
    from test_sentinel import FakeStac, _setup
    pid, _ids, t0 = _setup(conn, NOW)
    scene = {"id": "S1C_IW_GRDH_1SDV_C3_COG", "debut": t0.isoformat(), "fin": (t0 + timedelta(seconds=25)).isoformat(),
             "emprise": SCENE}
    return pid, t0, FakeStac([scene])


def _second_navire(conn):
    """Second navire des listes au nord du passage, sur un second câble : une seconde fenêtre dans la scène."""
    reg = conn.execute("SELECT id FROM regions WHERE lower(name) = 'bretagne'").fetchone()[0]
    conn.execute("INSERT INTO infrastructure (region_id, kind, name, source, attrs, geom) VALUES (%s, 'cable', "
                 "'CABLE NORD', 'essai', '{}', ST_GeomFromText('LINESTRING(-6.0 48.35, -5.6 48.35)', 4326)::geography)", (reg,))
    vid = conn.execute("INSERT INTO vessels (mmsi, imo, name, length_m, ais_class) VALUES (273999009, 9999011, "
                       "'LISTE NORD', 90, 'A') RETURNING id").fetchone()[0]
    t0 = conn.execute("SELECT acquired_at FROM sar_passes WHERE product_name = 'S1C_OR9999'").fetchone()[0]
    conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn, cog_deg) VALUES (%s, %s, "
                 "ST_SetSRID(ST_MakePoint(-5.8, 48.36), 4326)::geography, 10, 90)", (vid, t0))
    conn.execute("REFRESH MATERIALIZED VIEW vessel_watch")


def _resultat(conn, wid):
    """Résultat d'un lot : chaque fenêtre reçue est faite, sans détection."""
    brut = conn.execute("SELECT parametres FROM travailleurs WHERE id = %s", (wid,)).fetchone()[0]
    params = json.loads(brut) if isinstance(brut, str) else brut
    scenes = [{"analyse": sc["analyse"], "id": sc["id"], "debut": sc["debut"], "fin": sc["fin"], "detections": [],
               "octets_lus": 1000, "fenetres": [{"index": f["id"], "octets": 1000} for f in sc["fenetres"]]}
              for sc in params["scenes"]]
    return params, {"id": wid, "resultat": {"scenes": scenes}}


def test_stock_out_waits_retries_then_runs_by_lots_until_every_window_is_done(conn, monkeypatch):
    pid, t0, stac = _passage(conn)
    conn.execute("DELETE FROM vessels WHERE mmsi = 273999009")
    _second_navire(conn)
    # Grand passage : deux fenêtres, au delà d'une fenêtre par session sur processeur
    rules = {**RULES, "sentinel": {**S, "scenes_max_par_jour": 2, "fenetres_max_cpu": 1}}
    lances, types, stock = [], [], {"epuise": True}

    def faux_lancement(c, _scw, _rules, tache, params, _env, **kw):
        variante = kw["variante"]
        t = T.type_params(rules, tache, variante)
        lances.append(t["commercial_type"])
        types.append(t)
        if stock["epuise"]:
            return {"rupture": f"rupture de stock chez Scaleway ({t['commercial_type']})", "travailleur": None}
        wid = c.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, verrou) "
                        "VALUES ('sentinel', %s, 'fr-par-2', 'x', %s, 'sentinel') RETURNING id",
                        (t["commercial_type"], json.dumps(params))).fetchone()[0]
        return {"travailleur": wid}
    monkeypatch.setattr(T, "launch", faux_lancement)
    # 1. GP1-S et POP2 en rupture : l'analyse attend, sans échec ; ses fenêtres sont calculées et gardées
    r = sentinel.launch(conn, None, rules, NOW, stac)
    aid = r["analyses"][0]
    assert r["attente_gpu"] == [aid] and r["prochain_essai_min"] == S["intervalle_min"] and r["fenetres"] >= 1
    assert lances == ["GP1-S", "POP2-8C-32G"]                                     # aucun GPU essayé
    st = conn.execute("SELECT status, essais_calcul, fenetres, calcul_motif FROM analyses WHERE id = %s", (aid,)).fetchone()
    assert st[0] == "attente_gpu" and st[1] == 1 and st[2] and "rupture de stock" in st[3]
    assert sentinel.plan(conn, stac, rules, NOW)["passages"] == []               # le passage n'est pas relancé à neuf
    # 2. Cycle suivant : reprise de la même analyse, toujours en attente
    lances.clear()
    r = sentinel.launch(conn, None, rules, NOW + timedelta(minutes=30), stac)
    assert r.get("reprise") and r["attente_gpu"] == [aid] and lances == ["GP1-S", "POP2-8C-32G"]
    # 3. Stock revenu : GP1-S, lot d'une fenêtre sur deux
    lances.clear()
    stock["epuise"] = False
    plus_tard = NOW + timedelta(hours=1)
    r = sentinel.launch(conn, None, rules, plus_tard, stac)
    assert r["calcul"] == "cpu" and r["type"] == "GP1-S" and lances == ["GP1-S"] and r["fenetres_lot"] == 1
    row = conn.execute("SELECT status, calcul, calcul_type, calcul_motif, cout_estime_eur FROM analyses WHERE id = %s",
                       (aid,)).fetchone()
    assert row[:3] == ("running", "cpu", "GP1-S fr-par-2") and row[3].startswith(sentinel.MOTIF_LOTS) and row[4] > 0
    assert "lot de 1 fenêtre(s) sur 2" in row[3]
    # Durée de vie, coût prévu et facturation transmis au lancement (enregistrés par travailleurs.launch)
    t = types[-1]
    assert (t["duree_max_min"], t["cout_prevu_eur"], t["facturation"]) == (sentinel.duree_cpu(1, S),
                                                                           r["cout_estime_eur"], "heure_entamee")
    params, w = _resultat(conn, r["travailleur"])
    assert "force_cpu" not in params and params["scenes"][0]["vv"]
    premier = params["scenes"][0]["fenetres"][0]["id"]
    # 4. Premier lot reçu : une fenêtre faite, l'analyse repart en attente du lot suivant, sans fusion
    sentinel.ingest(conn, w, rules)
    st = conn.execute("SELECT status, fenetres_faites, calcul_motif, summary FROM analyses WHERE id = %s", (aid,)).fetchone()
    assert st[0] == "attente_gpu" and list(st[1].values()) == [[premier]] and "1 restante" in st[2] and not st[3]
    conn.execute("UPDATE travailleurs SET detruit_le = now(), etat = 'termine' WHERE id = %s", (r["travailleur"],))
    # 5. Lot suivant : l'autre fenêtre seulement, puis fusion et analyse terminée
    lances.clear()
    r = sentinel.launch(conn, None, rules, plus_tard + timedelta(minutes=30), stac)
    assert r.get("reprise") and r["calcul"] == "cpu" and r["fenetres_lot"] == 1
    params, w = _resultat(conn, r["travailleur"])
    assert params["scenes"][0]["fenetres"][0]["id"] != premier
    sentinel.ingest(conn, w, rules)
    st = conn.execute("SELECT status, fenetres_faites, octets_lus FROM analyses WHERE id = %s", (aid,)).fetchone()
    assert st[0] == "done" and sorted(list(st[1].values())[0]) == [0, 1] and st[2] == 2000


def test_small_pass_goes_to_cpu_at_once_and_pop2_backs_up_gp1s(conn, monkeypatch):
    _pid, _t0, stac = _passage(conn)
    rules = {**RULES, "sentinel": {**S, "scenes_max_par_jour": 2}}
    lances = []

    def faux_lancement(c, _scw, _rules, tache, params, _env, **kw):
        t = T.type_params(rules, tache, kw["variante"])
        lances.append(t["commercial_type"])
        if t["commercial_type"] == "GP1-S":
            return {"rupture": "rupture de stock chez Scaleway (GP1-S)", "travailleur": None}
        wid = c.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, verrou) "
                        "VALUES ('sentinel', %s, 'fr-par-2', 'x', %s, 'sentinel') RETURNING id",
                        (t["commercial_type"], json.dumps(params))).fetchone()[0]
        return {"travailleur": wid}
    monkeypatch.setattr(T, "launch", faux_lancement)
    r = sentinel.launch(conn, None, rules, NOW, stac)
    assert lances == ["GP1-S", "POP2-8C-32G"]                                     # aucun GPU essayé
    motif = conn.execute("SELECT calcul_motif FROM analyses WHERE id = %s", (r["analyses"][0],)).fetchone()[0]
    assert motif.startswith(sentinel.MOTIF_PETIT) and "GP1-S (fr-par-2) : rupture de stock" in motif


def test_manual_pass_without_any_window_is_refused_rather_than_read_whole(conn, monkeypatch):
    """Voie GPU retirée (lot Z4) : une scène entière n'est jamais analysée ; le refus le dit, sans appel à Scaleway."""
    pid, _t0, stac = _passage(conn)
    conn.execute("DELETE FROM positions WHERE vessel_id IN (SELECT id FROM vessels WHERE mmsi = 273999001)")
    conn.execute("DELETE FROM watchlist WHERE imo = 9999011")
    conn.execute("REFRESH MATERIALIZED VIEW vessel_watch")
    lances = []
    monkeypatch.setattr(T, "launch", lambda *_a, **_k: lances.append(1))
    rules = {**RULES, "sentinel": {**S, "scenes_max_par_jour": 2}}
    r = sentinel.launch(conn, None, rules, NOW, stac, pass_id=pid)
    assert "scène entière n'est jamais analysée sur processeur" in r["refus"] and lances == []


def test_trigger_point_at_quay_is_dropped_offshore_and_anchorage_are_kept(conn):
    conn.execute("INSERT INTO stationary_zones (geom, vessels, slow_positions, day_from, day_to) VALUES "
                 "(ST_Buffer(ST_SetSRID(ST_MakePoint(-4.50, 48.33), 4326)::geography, 500), 5, 50, '2026-10-01', "
                 "'2026-10-07')")
    pts = [{"motif": "navire_liste", "navire": "VICTOR ANDRYUKHIN", "lon": 5.3410, "lat": 43.3330},  # bassins nord
           {"motif": "navire_liste", "navire": "AU LARGE", "lon": -6.1, "lat": 48.01},
           {"motif": "corridor_liste", "navire": "RADE", "lon": -4.50, "lat": 48.33},              # mouillage, 1,8 km
           {"motif": "navire_liste", "navire": "PORT DE BREST", "lon": -4.495, "lat": 48.378},
           {"motif": "alerte_viirs", "lon": 5.3410, "lat": 43.3330}]
    kept, dropped = sentinel.en_mer(conn, pts, RULES)
    assert [p.get("navire") for p in kept] == ["AU LARGE", "RADE", None]
    assert kept[1]["en_mer"] == "au mouillage hors port" and kept[0]["distance_cote_m"] is None
    assert [(p["navire"], p["ecarte"]) for p in dropped] == [("VICTOR ANDRYUKHIN", sentinel.MOTIF_A_QUAI),
                                                              ("PORT DE BREST", sentinel.MOTIF_A_QUAI)]
    assert 50 < dropped[0]["distance_cote_m"] < 500


def test_waiting_analysis_is_abandoned_after_the_useful_window(conn, monkeypatch):
    pid, t0, stac = _passage(conn)
    rules = {**RULES, "sentinel": {**S, "scenes_max_par_jour": 2}}
    monkeypatch.setattr(T, "launch", lambda *_a, **_k: {"rupture": "rupture de stock", "travailleur": None})
    aid = sentinel.launch(conn, None, rules, NOW, stac)["analyses"][0]
    tard = t0 + timedelta(hours=S["fenetre_utile_h"], minutes=1)
    r = sentinel.launch(conn, None, rules, tard, stac)
    assert aid in r["abandonnees"]
    st, err = conn.execute("SELECT status, error FROM analyses WHERE id = %s", (aid,)).fetchone()
    assert st == "failed" and err.startswith("processeur indisponible pendant toute la fenêtre utile de 24 h")
    # La règle d'alerte de Grafana le voit (une fois, pendant l'heure qui suit)
    regles = (__import__("pathlib").Path(__file__).parents[1] / "grafana" / "provisioning" / "alerting"
              / "regles.yaml").read_text()
    assert "LIKE '%indisponible pendant toute la fenêtre utile%'" in regles


def test_manual_trial_ceiling_applies_to_the_manual_command_only(conn, monkeypatch):
    pid, t0, stac = _passage(conn)
    monkeypatch.setattr(T, "launch", lambda *_a, **_k: {"rupture": "rupture de stock", "travailleur": None})
    assert "plafond de 0 scènes" in sentinel.launch(conn, None, ARRET, NOW, stac, pass_id=pid)["refus"]
    monkeypatch.setenv("SENTINEL_PLAFOND_ESSAI", "1")                             # essai réel : commande manuelle
    assert "plafond de 0 scènes" in sentinel.launch(conn, None, ARRET, NOW, stac)["refus"]   # la boucle : toujours 0
    r = sentinel.launch(conn, None, ARRET, NOW, stac, pass_id=pid)
    assert "attente_gpu" in r and r["fenetres"] >= 1                              # passage choisi : fenêtres seules


def test_done_analysis_without_any_analysed_scene_is_reclassified(conn):
    from test_sentinel import _setup
    pid, _ids, _t0 = _setup(conn, NOW)
    migration = (__import__("pathlib").Path(__file__).parents[1] / "db" / "init" / "28_sentinel_robuste.sql").read_text()
    for timings, attendu in (({"S": {"erreur": "No module named 'src'"}}, "failed"),     # essai 20, analyse 94
                             ({"S": {"erreur": None, "analyse_s": 120}}, "done"),        # scène aboutie, mer vide
                             (None, "failed")):                                          # aucune scène relevée
        aid = conn.execute(
            "INSERT INTO analyses (pass_id, aoi, mode, status, source, timings) VALUES (%s, "
            "ST_MakeEnvelope(-6, 48, -5, 49, 4326)::geography, 'full', 'done', 'sentinel1', %s) RETURNING id",
            (pid, json.dumps(timings) if timings else None)).fetchone()[0]
        conn.execute(migration)
        assert conn.execute("SELECT status FROM analyses WHERE id = %s", (aid,)).fetchone()[0] == attendu
        conn.execute("UPDATE analyses SET status = 'failed' WHERE id = %s", (aid,))      # place au cas suivant


# Lot C4 : échec signalé, instance arrêtée, sondages regroupés

def _travailleur(conn, scw, etat: str, ctype="GP1-S", zone="fr-par-2", mesures=None, erreur=None, age_min=10):
    """Travailleur Sentinel actif avec une analyse en cours, et son instance dans le faux Scaleway."""
    from test_sentinel import _setup
    pid, _ids, _t0 = _setup(conn, NOW)
    wid = conn.execute(
        "INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, etat, erreur, mesures, cree_le, demarre_le, "
        "traceparent) VALUES ('sentinel', %s, %s, 'x', %s, %s, %s, now() - make_interval(mins => %s), now(), %s) "
        "RETURNING id", (ctype, zone, etat, erreur, json.dumps(mesures or {}), age_min,
                         "00-" + "c" * 32 + "-" + "d" * 16 + "-01")).fetchone()[0]
    sid = f"srv-mars-travailleur-{wid}"
    conn.execute("UPDATE travailleurs SET scw_server_id = %s WHERE id = %s", (sid, wid))
    scw.servers[sid] = {"id": sid, "name": f"mars-travailleur-{wid}", "tags": [T.TAG, f"run-{wid}"], "project": "p",
                        "state": "running"}
    aid = conn.execute(
        "INSERT INTO analyses (pass_id, aoi, mode, status, source, travailleur_id, scenes, requested_at) VALUES (%s, "
        "ST_MakeEnvelope(-6, 48, -5, 49, 4326)::geography, 'full', 'running', 'sentinel1', %s, '[]', now()) RETURNING id",
        (pid, wid)).fetchone()[0]
    return wid, aid, sid


@pytest.mark.parametrize("etape,attendu", [("analyse_docker", "attente_gpu"), ("telechargement_image", "attente_gpu"),
                                           ("analyse", "failed")])
def test_signalled_failure_is_handled_at_once(conn, spans, etape, attendu):
    from test_travailleurs_base import FakeScw
    scw = FakeScw()
    wid, aid, sid = _travailleur(conn, scw, "echec", erreur=f"échec à l'étape {etape} : code de sortie 125",
                                 mesures={"fin_signalee": "echec", "etape_echec": etape})
    T.supervise(conn, scw, RULES, {})
    assert sid in scw.destroyed
    st, err = conn.execute("SELECT status, coalesce(error, calcul_motif) FROM analyses WHERE id = %s", (aid,)).fetchone()
    assert st == attendu and "code de sortie 125" in err
    sp = [s for s in spans.get_finished_spans() if s.name == "travailleur.echec"][-1]
    assert sp.status.status_code.name == "ERROR" and sp.attributes["travailleur.etape"] == etape
    T.supervise(conn, scw, RULES, {})                                             # une seule fois
    assert [s.name for s in spans.get_finished_spans()].count("travailleur.echec") == 1


def test_instance_stopped_by_itself_is_treated_as_a_machine_failure(conn):
    from test_travailleurs_base import FakeScw
    scw = FakeScw()
    wid, aid, sid = _travailleur(conn, scw, "demarre", age_min=8)
    scw.servers[sid]["state"] = "stopped in place"                                # essai 26 : arrêtée, encore facturée
    T.supervise(conn, scw, RULES, {})
    assert sid in scw.destroyed
    assert conn.execute("SELECT etat FROM travailleurs WHERE id = %s", (wid,)).fetchone()[0] == "echec"
    assert conn.execute("SELECT status FROM analyses WHERE id = %s", (aid,)).fetchone()[0] == "attente_gpu"


def test_polling_is_one_span_with_its_count_and_states(spans):
    class Api:
        headers = {}

        def __init__(self):
            self.etats = iter(["stopped", "starting", "starting", "running"])

        def request(self, method, _url, **_kw):
            class R:
                status_code, content = 200, b"x"
                headers = {"content-type": "application/json"}

                def __init__(self, body):
                    self.body = body

                def json(self):
                    return self.body
            if method == "POST":
                return R({"task": {}})
            return R({"server": {"id": "s", "state": next(self.etats)}})
    scw = T.Scaleway("cle", "p", session=Api(), protected=set())
    scw.start("fr-par-2", "s", sleep=lambda _s: None)
    (sp,) = [s for s in spans.get_finished_spans() if s.name == "scaleway.attente_demarrage"]
    assert sp.attributes["sondage.nombre"] == 4 and sp.attributes["sondage.etat_final"] == "running"
    assert sp.attributes["sondage.dernier_intermediaire"] == "starting"


def test_polling_requests_are_not_traced_one_by_one(spans):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import requests

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/"
    with traces.sondage("essai.sondage") as so:
        for _ in range(3):
            with traces.sans_instrumentation():
                requests.get(url, timeout=5)
            so.etat("ok")
    requests.get(url, timeout=5)                                                  # hors sondage : tracée
    srv.shutdown()
    noms = [s.name for s in spans.get_finished_spans()]
    assert noms.count("essai.sondage") == 1 and noms.count("GET") == 1




def test_gp1s_out_of_stock_never_falls_back_on_an_unproven_pop2(conn, monkeypatch):
    """Avant la reprise de tip and cue (26/10/2026) : GP1-S en rupture, POP2 jamais éprouvé. Aucune instance POP2 n'est
    créée ; l'analyse attend le cycle suivant (pas d'échec), le motif nomme le refus."""
    from test_travailleurs_base import FakeScw
    garde = load_rules()                                         # règles réelles : types_eprouves_seuls en vigueur
    assert "sentinel" in garde["travailleurs"]["types_eprouves_seuls"]
    pid, _t0, stac = _passage(conn)
    conn.execute("UPDATE travailleurs SET etat = 'echec' WHERE commercial_type = 'POP2-8C-32G' AND etat = 'termine'")
    vrai, crees = T.launch, []

    def lancement(c, scw, rules, tache, params, env, **kw):
        t = T.type_params(rules, tache, kw.get("variante"))
        if t["commercial_type"] == "GP1-S":
            return {"rupture": "rupture de stock", "travailleur": None}
        r = vrai(c, scw, rules, tache, params, env, **kw)      # vraie garde, avant tout appel à Scaleway
        crees.append(r)
        return r
    monkeypatch.setattr(T, "launch", lancement)
    r = sentinel.launch(conn, FakeScw(), garde, NOW, stac, pass_id=pid)
    assert "attente_gpu" in r and "POP2-8C-32G en fr-par-2 jamais éprouvé" in r["motif"]
    assert crees and all(x.get("non_eprouve") for x in crees)
    st = conn.execute("SELECT status FROM analyses WHERE id = ANY(%s)", (r["attente_gpu"],)).fetchall()
    assert st and all(s[0] == "attente_gpu" for s in st)
    assert conn.execute("SELECT count(*) FROM travailleurs WHERE commercial_type = 'POP2-8C-32G' AND cree_le >= %s",
                        (NOW - timedelta(minutes=1),)).fetchone()[0] == 0
