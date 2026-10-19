"""Sentinel 1 robuste (lot C3) : fenêtres utiles, rupture de stock des GPU, types de secours, repli sur processeur,
coût selon le type lancé, reclassement des analyses sans scène aboutie. Les essais sur base sont ignorés sans
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
S = RULES["sentinel"]
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
    gpu = T.type_params(RULES, "sentinel")
    cpu = T.type_params(RULES, "sentinel", T.variantes(RULES, "sentinel")[-1])
    assert cpu["commercial_type"] == "GP1-S" and "--gpus" not in cpu["docker_opts"]
    deux = sentinel.cout_calcul(2, 0, S, gpu, "gpu")
    assert deux == round((S["minutes_fixes"] + 2 * S["minutes_par_fenetre_gpu"]) / 60 * gpu["prix_heure"], 4)
    assert deux < sentinel.cout_calcul(0, 1, S, gpu, "gpu")                     # moins cher qu'une scène entière
    assert sentinel.cout_calcul(2, 0, S, cpu, "cpu") == round(cpu["prix_heure"], 4)   # une heure entamée de GP1-S


def test_variants_are_tried_in_the_configured_order():
    v = T.variantes(RULES, "sentinel")
    assert [(T.type_params(RULES, "sentinel", x)["commercial_type"], x["calcul"]) for x in v] == [
        ("L4-1-24G", "gpu"), ("L4-1-24G", "gpu"), ("L40S-1-48G", "gpu"), ("RENDER-S", "gpu"), ("GP1-S", "cpu")]
    assert T.type_params(RULES, "sentinel", v[3])["volume"] == "l_ssd"           # RENDER-S : disque local
    assert T.variante_de(RULES, "sentinel", "L40S-1-48G", "fr-par-2") == v[2]
    assert T.type_params(RULES, "viirs", None)["commercial_type"] == "DEV1-M"    # VIIRS inchangé
    assert T.en_rupture('POST /instance/v1/zones/fr-par-2/servers/x/action : 412 {"type":"out_of_stock"}')


class Dispo:
    def __init__(self, etats):
        self.etats = etats

    def availability(self, zone):
        return self.etats.get(zone, {})


def test_out_of_stock_types_are_never_tried_and_cpu_waits_for_its_delay(spans):
    scw = Dispo({"fr-par-2": {"L4-1-24G": "shortage", "L40S-1-48G": "shortage", "RENDER-S": "scarce",
                              "GP1-S": "available"},
                 "fr-par-1": {"L4-1-24G": "shortage"}})
    with traces.span("tache sentinel", nouvelle_trace=True):
        c, dispo = sentinel.candidats(scw, RULES, NOW - timedelta(hours=1), NOW)
    assert [T.type_params(RULES, "sentinel", x)["commercial_type"] for x in c] == ["RENDER-S"]
    assert dispo["L4-1-24G fr-par-2"] == "shortage" and dispo["GP1-S fr-par-2"] == "available"
    c, _ = sentinel.candidats(scw, RULES, NOW - timedelta(hours=S["repli_cpu_apres_h"]), NOW)
    assert [x["calcul"] for x in c] == ["gpu", "cpu"]                             # repli après le délai
    c, _ = sentinel.candidats(scw, RULES, NOW - timedelta(hours=9), NOW, cpu_possible=False)
    assert [x["calcul"] for x in c] == ["gpu"]                                    # scène entière : jamais sur CPU
    fini = spans.get_finished_spans()
    tache = [x for x in fini if x.name == "tache sentinel"][0]
    lectures = [x for x in fini if x.name == "sentinel.disponibilite" and x.parent
                and x.parent.span_id == tache.context.span_id]
    assert len(lectures) == 2                                    # une lecture par zone, dans la trace de l'analyse


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
                                  '{"type":"out_of_stock","message":"server type L4-1-24G is out of stock"}')
    conn.execute("DELETE FROM analyses WHERE travailleur_id IS NOT NULL")
    conn.execute("DELETE FROM travailleurs")
    scw = Rupture()
    r = T.launch(conn, scw, RULES, "sentinel", {"scenes": []}, {}, now=NOW, sleep=lambda _s: None)
    assert "rupture" in r and "L4-1-24G" in r["rupture"]
    assert scw.destroyed and not scw.servers                                      # instance et volume supprimés
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


def test_stock_out_waits_retries_falls_back_to_cpu_then_runs(conn, monkeypatch):
    pid, t0, stac = _passage(conn)
    rules = {**RULES, "sentinel": {**S, "scenes_max_par_jour": 2}}
    lances = []

    def faux_lancement(c, _scw, _rules, tache, params, _env, **kw):
        variante = kw["variante"]
        t = T.type_params(rules, tache, variante)
        lances.append((t["commercial_type"], variante["calcul"], params.get("force_cpu")))
        if variante["calcul"] == "gpu":
            return {"rupture": f"rupture de stock chez Scaleway ({t['commercial_type']})", "travailleur": None}
        wid = c.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, verrou) "
                        "VALUES ('sentinel', %s, 'fr-par-2', 'x', %s, 'sentinel') RETURNING id",
                        (t["commercial_type"], json.dumps(params))).fetchone()[0]
        return {"travailleur": wid}
    monkeypatch.setattr(T, "launch", faux_lancement)
    # 1. Tous les GPU en rupture : l'analyse attend, sans échec ; ses fenêtres sont calculées et gardées
    r = sentinel.launch(conn, None, rules, NOW, stac)
    aid = r["analyses"][0]
    assert r["attente_gpu"] == [aid] and r["prochain_essai_min"] == S["intervalle_min"] and r["fenetres"] >= 1
    assert [x[1] for x in lances] == ["gpu"] * 4                                  # CPU pas encore permis
    st = conn.execute("SELECT status, essais_calcul, fenetres, calcul_motif FROM analyses WHERE id = %s", (aid,)).fetchone()
    assert st[0] == "attente_gpu" and st[1] == 1 and st[2] and "rupture de stock" in st[3]
    assert sentinel.plan(conn, stac, rules, NOW)["passages"] == []               # le passage n'est pas relancé à neuf
    # 2. Cycle suivant, moins de 3 h après : reprise de la même analyse, toujours en attente
    lances.clear()
    r = sentinel.launch(conn, None, rules, NOW + timedelta(minutes=30), stac)
    assert r.get("reprise") and r["attente_gpu"] == [aid] and all(x[1] == "gpu" for x in lances)
    # 3. Après repli_cpu_apres_h : repli sur processeur, fenêtres seules, choix et raison enregistrés
    lances.clear()
    r = sentinel.launch(conn, None, rules, NOW + timedelta(hours=S["repli_cpu_apres_h"], minutes=5), stac)
    assert r["calcul"] == "cpu" and r["type"] == "GP1-S" and lances[-1] == ("GP1-S", "cpu", True)
    row = conn.execute("SELECT status, calcul, calcul_type, calcul_motif, cout_estime_eur FROM analyses WHERE id = %s",
                       (aid,)).fetchone()
    assert row[:3] == ("running", "cpu", "GP1-S fr-par-2") and "processeur" in row[3] and row[4] > 0
    brut = conn.execute("SELECT parametres FROM travailleurs WHERE id = %s", (r["travailleur"],)).fetchone()[0]
    params = json.loads(brut) if isinstance(brut, str) else brut
    assert params["force_cpu"] is True and params["scenes"][0]["fenetres"] and params["scenes"][0]["vv"]


def test_waiting_analysis_is_abandoned_after_the_useful_window(conn, monkeypatch):
    pid, t0, stac = _passage(conn)
    rules = {**RULES, "sentinel": {**S, "scenes_max_par_jour": 2}}
    monkeypatch.setattr(T, "launch", lambda *_a, **_k: {"rupture": "rupture de stock", "travailleur": None})
    aid = sentinel.launch(conn, None, rules, NOW, stac)["analyses"][0]
    tard = t0 + timedelta(hours=S["fenetre_utile_h"], minutes=1)
    r = sentinel.launch(conn, None, rules, tard, stac)
    assert aid in r["abandonnees"]
    st, err = conn.execute("SELECT status, error FROM analyses WHERE id = %s", (aid,)).fetchone()
    assert st == "failed" and err.startswith("GPU indisponible pendant toute la fenêtre utile de 24 h")
    # La règle d'alerte de Grafana le voit (une fois, pendant l'heure qui suit)
    regles = (__import__("pathlib").Path(__file__).parents[1] / "grafana" / "provisioning" / "alerting"
              / "regles.yaml").read_text()
    assert "GPU indisponible pendant toute la fenêtre utile%" in regles


def test_manual_trial_ceiling_applies_to_the_manual_command_only(conn, monkeypatch):
    pid, t0, stac = _passage(conn)
    monkeypatch.setattr(T, "launch", lambda *_a, **_k: {"rupture": "rupture de stock", "travailleur": None})
    assert "plafond de 0 scènes" in sentinel.launch(conn, None, RULES, NOW, stac, pass_id=pid)["refus"]
    monkeypatch.setenv("SENTINEL_PLAFOND_ESSAI", "1")                             # essai réel : commande manuelle
    assert "plafond de 0 scènes" in sentinel.launch(conn, None, RULES, NOW, stac)["refus"]   # la boucle : toujours 0
    r = sentinel.launch(conn, None, RULES, NOW, stac, pass_id=pid)
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
