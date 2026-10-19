"""Traces OpenTelemetry (étape D2). Règle du projet : une fonctionnalité non tracée n'est pas terminée.

* Chaque commande de scripts/taches.py ouvre son span (registre COMMANDES, seule voie de l'analyse des arguments), et
  toute tâche passée par run() ouvre le sien, avec son identifiant de trace dans task_runs.
* Chaque type de travailleur de config/rules.yaml est tracé de bout en bout : son script, exécuté contre un faux
  serveur, envoie ses spans dans la trace reçue (TRACEPARENT) et transmet le contexte avec ses résultats.
* Aucune donnée sensible dans un attribut, dans le code (mars/traces.py, travailleurs/otel_mini.py) comme dans le
  collecteur (otel/collecteur.yaml ; l'essai réel du filtre du collecteur est dans scripts/verifier_images.sh traces).
* Déclencheur des alertes (base de test, MARS_TEST_DATABASE_URL) : identifiant de trace dans les détails.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mars import traces
from mars.config import ROOT

URL = os.environ.get("MARS_TEST_DATABASE_URL")
MEM = InMemorySpanExporter()
traces.init("mars-test", exportateur=MEM)


@pytest.fixture
def spans():
    MEM.clear()
    yield MEM
    MEM.clear()


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _taches():
    sys.path.insert(0, str(ROOT / "scripts"))
    return _module("taches_essai", ROOT / "scripts" / "taches.py")


# Données sensibles

def test_sensitive_attributes_are_removed_or_cleaned():
    a = traces.propres({"jeton": "abc", "http.request.header.authorization": "Bearer x", "SCW_SECRET_KEY": "k",
                        "url.full": "https://eodata.dataspace.copernicus.eu/a/b.tiff?X-Amz-Signature=zz&X-Amz-Credential=cc",
                        "url.query": "q=GELIOTROP", "http.target": "/api/search?q=GELIOTROP",
                        "detail": "réponse Bearer eyJabc.def token=123 sig=9", "tache.nom": "viirs", "n": 3})
    assert set(a) == {"url.full", "http.target", "detail", "tache.nom", "n"}
    assert a["url.full"] == "https://eodata.dataspace.copernicus.eu/a/b.tiff"
    assert a["http.target"] == "/api/search"
    assert "eyJ" not in a["detail"] and "123" not in a["detail"] and "=9" not in a["detail"]


def test_secret_paths_are_masked(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://alertes.example.org")
    assert traces.nettoyer_url("https://ntfy.sh/mars_c2_sujet_secret") == "https://ntfy.sh/…"
    assert traces.nettoyer_url("https://alertes.example.org/sujet") == "https://alertes.example.org/…"
    assert traces.nettoyer_url("https://www.vesselfinder.com/vessels/details/227000000") == "https://www.vesselfinder.com/…"
    assert traces.nettoyer_url("https://user:pw@api.scaleway.com/instance/v1/x?y=1") == "https://api.scaleway.com/instance/v1/x"
    assert traces.service_externe("cmr.earthdata.nasa.gov") == "NASA"
    assert traces.service_externe("alertes.example.org") == "ntfy"


def test_exported_spans_are_cleaned_events_and_status_included(spans):
    with pytest.raises(RuntimeError):
        with traces.span("essai.secret", nouvelle_trace=True, mot_de_passe="x", cle_r2="ok") as s:
            s.set_attribute("url.full", "https://x.r2.cloudflarestorage.com/b/k?X-Amz-Signature=abc")
            raise RuntimeError("GET https://h/p?token=SECRET refusé")
    (sp,) = [s for s in spans.get_finished_spans() if s.name == "essai.secret"]
    texte = json.dumps({"a": dict(sp.attributes), "e": [dict(e.attributes) for e in sp.events],
                        "s": sp.status.description}, ensure_ascii=False)
    assert "SECRET" not in texte and "abc" not in texte and "mot_de_passe" not in texte
    assert sp.attributes["cle_r2"] == "ok" and sp.status.status_code.name == "ERROR"


def test_log_lines_carry_trace_and_span_ids():
    import io
    flux = io.StringIO()
    sortie = traces._Lignes(flux)                       # ce que devient sys.stdout après traces.init
    with traces.span("essai.journal", nouvelle_trace=True):
        print("ligne de journal", file=sortie)
        tid = traces.trace_id()
    print("hors span", file=sortie)
    out = flux.getvalue().splitlines()
    assert re.fullmatch(rf"ligne de journal trace_id={tid} span_id=[0-9a-f]{{16}}", out[0])
    assert out[1] == "hors span"


def test_collector_filters_and_samples_as_documented():
    c = yaml.safe_load((ROOT / "otel" / "collecteur.yaml").read_text())
    pipeline = c["service"]["pipelines"]["traces"]
    assert pipeline["processors"][0] == "memory_limiter"                # premier : refuse avant de saturer
    assert pipeline["processors"].index("transform/sensible") < pipeline["processors"].index("tail_sampling")
    stmts = " ".join(st for b in c["processors"]["transform/sensible"]["trace_statements"] for st in b["statements"])
    for motif in ("authorization", "jeton", "password", "access_?key", "signature", "url.query", "bearer", "x-amz-"):
        assert motif in stmts.lower()
    pol = {p["name"]: p for p in c["processors"]["tail_sampling"]["policies"]}
    assert set(pol["taches_et_travailleurs"]["string_attribute"]["values"]) == {
        "mars-taches", "mars-collecte", "mars-ingestion", "mars-travailleur"}
    assert pol["lentes"]["latency"]["threshold_ms"] == 1000 and pol["erreurs"]["status_code"]["status_codes"] == ["ERROR"]
    assert pol["reste_api"]["probabilistic"]["sampling_percentage"] == 10
    exp = c["exporters"][pipeline["exporters"][0]]
    assert exp["sending_queue"]["storage"] == "file_storage" and exp["retry_on_failure"]["enabled"]
    rx = c["receivers"]["otlp"]["protocols"]["http"]["endpoint"]
    assert rx.endswith(":4318")
    serveur = (ROOT / "docker-compose.serveur.yml").read_text()
    bloc = serveur[serveur.index("\n  otel:\n"):serveur.index("\n  tempo:\n")]
    assert 'ports: ["127.0.0.1:4318:4318"]' in bloc and "MARS_IP_PRIVEE" not in bloc   # jamais d'adresse privée


def test_tempo_keeps_traces_72_hours():
    t = yaml.safe_load((ROOT / "otel" / "tempo.yaml").read_text())
    assert t["backend_worker"]["compaction"]["block_retention"] == "72h"
    assert t["backend_scheduler"]["provider"]["compaction"]["compaction"]["block_retention"] == "72h"
    assert t["storage"]["trace"]["backend"] == "local"


# Tâches : tracées par construction

def test_every_taches_command_opens_its_span(spans, monkeypatch):
    taches = _taches()
    choix = taches.parser()._actions[1].choices
    assert list(choix) == list(taches.COMMANDES)              # aucune commande hors du registre
    monkeypatch.setattr(taches, "connect", lambda: pytest.fail("aucune base dans cet essai"))
    for nom, (fn, _base) in list(taches.COMMANDES.items()):
        if nom == "boucle":
            continue                                    # sans fin : chaque tour (minute) et chaque tâche ont leur span
        monkeypatch.setitem(taches.COMMANDES, nom, (lambda _conn, _a: None, False))
        taches.executer(nom, Namespace(args=[]))
        noms = [s.name for s in spans.get_finished_spans()]
        assert f"commande {nom}" in noms, f"la commande {nom} n'est pas tracée"
        spans.clear()


class _Conn:
    """Connexion psycopg factice : garde les requêtes et leurs paramètres."""

    def __init__(self):
        self.sql = []

    def execute(self, q, p=None):
        self.sql.append((q, p))
        return self

    def fetchone(self):
        return (42,)


def test_run_traces_any_task_and_records_its_trace(spans):
    taches = _taches()
    conn = _Conn()
    out = taches.run(conn, "tache_future", lambda: {"elements": 3})
    assert out["elements"] == 3
    (sp,) = [s for s in spans.get_finished_spans() if s.name == "tache tache_future"]
    tid = format(sp.context.trace_id, "032x")
    insert = [p for q, p in conn.sql if q.startswith("INSERT INTO task_runs")][0]
    assert insert[2] == tid and sp.parent is None and sp.attributes["tache.elements"] == 3
    assert ("SELECT set_config('mars.trace_id', %s, false)", (tid,)) in conn.sql   # alertes de la tâche
    # Refus attendu : pas une erreur de span, le motif en attribut ; échec : span en erreur
    from mars.travailleurs import RefusLancement

    def refus():
        raise RefusLancement("plafond atteint")

    def panne():
        raise ValueError("panne")
    taches.run(conn, "refus", refus)
    taches.run(conn, "panne", panne)
    by = {s.name: s for s in spans.get_finished_spans()}
    assert by["tache refus"].status.status_code.name == "UNSET" and by["tache refus"].attributes["tache.refus"]
    assert by["tache panne"].status.status_code.name == "ERROR"


# Travailleurs : chaque type tracé de bout en bout

class _Serveur(BaseHTTPRequestHandler):
    recus: list = []

    def do_POST(self):
        corps = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _Serveur.recus.append((self.path, {k.lower(): v for k, v in self.headers.items()}, corps))
        reponse = b"{}"
        if self.path.endswith("/etat") and json.loads(corps or b"{}").get("etat") == "tache":
            reponse = json.dumps({"scenes": [], "regions": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(reponse)

    def log_message(self, *a):
        pass


@pytest.mark.parametrize("tache", sorted(yaml.safe_load((ROOT / "config" / "rules.yaml").read_text())
                                         ["travailleurs"]["types"]))
def test_every_worker_joins_the_launch_trace(tache, tmp_path, monkeypatch):
    script = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text())["travailleurs"]["types"][tache]["script"]
    assert "import otel_mini" in (ROOT / script).read_text(), f"{script} n'est pas tracé"
    srv = HTTPServer(("127.0.0.1", 0), _Serveur)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Serveur.recus = []
    trace_id, parent = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"
    task = {"retour": f"http://127.0.0.1:{srv.server_port}/api/travailleurs/7", "jeton": "j",
            "granules": [], "regions": [], "tache_par_le_reseau": tache == "sentinel"}
    (tmp_path / "tache.json").write_text(json.dumps(task))
    monkeypatch.setenv("MARS_TACHE", str(tmp_path / "tache.json"))
    monkeypatch.setenv("TRACEPARENT", f"00-{trace_id}-{parent}-01")
    monkeypatch.setattr(sys, "argv", [script])
    # Les images des travailleurs n'ont pas le kit OpenTelemetry : requests n'y est pas instrumenté (ici, il le serait
    # par traces.init et remplacerait le contexte du travailleur)
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    RequestsInstrumentor().uninstrument()
    try:
        _module(f"travailleur_{tache}", ROOT / script).main()
    finally:
        srv.shutdown()
        RequestsInstrumentor().instrument(request_hook=traces.requete_sortante)
    lots = [json.loads(c) for p, _h, c in _Serveur.recus if p.endswith("/traces")]
    assert lots, f"le travailleur {tache} n'a envoyé aucun span"
    ss = [s for lot in lots for r in lot["resourceSpans"] for sc in r["scopeSpans"] for s in sc["spans"]]
    assert {s["traceId"] for s in ss} == {trace_id}
    racine = [s for s in ss if s["name"] == f"travailleur.{tache}"]
    assert racine and racine[0]["parentSpanId"] == parent
    noms = {s["name"] for s in ss}
    assert {"travailleur.signal_demarre", "travailleur.renvoi_resultats"} <= noms
    if tache == "sentinel":
        assert "travailleur.remise_tache" in noms
    res = [h for p, h, _c in _Serveur.recus if p.endswith("/resultats")]
    assert res and res[0]["traceparent"].split("-")[1] == trace_id     # l'API rejoint la trace
    assert all(h["authorization"] == "Bearer j" for p, h, _c in _Serveur.recus if p.endswith("/traces"))
    attrs = json.dumps(ss)
    assert '"jeton"' not in attrs


def test_otel_mini_drops_secrets_and_keeps_spans_on_failure():
    sys.path.insert(0, str(ROOT / "travailleurs"))
    om = _module("otel_mini_essai", ROOT / "travailleurs" / "otel_mini.py")

    def refuse(_req, **_kw):
        raise OSError("réseau privé coupé")
    tr = om.Traceur("00-" + "a" * 32 + "-" + "b" * 16 + "-01", "http://x/traces", "j", opener=refuse)
    with tr.span("essai", jeton="secret", url="https://h/p?X-Amz-Signature=s", n=2):
        pass
    assert tr.envoyer() is False and len(tr.finis) == 1          # gardé pour le prochain envoi
    corps = json.loads(tr.corps(tr.finis))
    s = corps["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert s["parentSpanId"] == "b" * 16 and s["traceId"] == "a" * 32
    assert {a["key"] for a in s["attributes"]} == {"url", "n"}
    assert s["attributes"][0]["value"]["stringValue"] == "https://h/p"


def test_cloud_init_hands_trace_context_and_exporters_to_the_worker():
    from mars.travailleurs import cloud_init
    tp = "00-" + "c" * 32 + "-" + "d" * 16 + "-01"
    ci = cloud_init("print(1)", {"retour": "http://10.0.0.2:8090/api/travailleurs/3", "jeton": "j",
                                 "script": "travailleurs/viirs.py"}, {}, "img", "02:00:00:00:00:01", "", tp)
    assert "path: /mars/otel_mini.py" in ci and "path: /mars/traces.sh" in ci
    import base64
    env = [ln for ln in ci.splitlines() if ln.strip().startswith("content:")][1]
    assert f'TRACEPARENT="{tp}"' in base64.b64decode(env.split()[-1]).decode()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")
def test_boot_script_spans_are_valid_otlp(tmp_path):
    date = shutil.which("gdate") or "date"
    if not subprocess.run([date, "+%N"], capture_output=True, text=True).stdout.strip().isdigit():
        pytest.skip("date sans nanosecondes (macOS sans coreutils) : éprouvé dans verifier_images.sh traces")
    tp = "00-" + "e" * 32 + "-" + "f" * 16 + "-01"
    env = {**os.environ, "TRACEPARENT": tp, "SPANS": str(tmp_path / "s.json"), "RETOUR": "http://127.0.0.1:9",
           "JETON": "j", "PATH": f"{Path(date).parent}:{os.environ['PATH']}"}
    subprocess.run(["bash", "-c", f'date() {{ {date} "$@"; }}; . {ROOT}/travailleurs/traces.sh; etape a; '
                    'etape b; fin_etape 2 "motif \\"x\\""; span travailleur.demarrage $RACINE $PARENT $RACINE_DEBUT 1'],
                   env=env, check=True)
    lignes = [json.loads(ln) for ln in (tmp_path / "s.json").read_text().splitlines()]
    assert [s["name"] for s in lignes] == ["demarrage.a", "demarrage.b", "travailleur.demarrage"]
    assert lignes[-1]["parentSpanId"] == "f" * 16 and lignes[1]["status"]["code"] == 2
    assert {s["traceId"] for s in lignes} == {"e" * 32}


def test_collector_metrics_are_read_without_prometheus():
    from mars.supervision import lire_mesures_otel
    texte = """# HELP x
otelcol_receiver_accepted_spans{receiver="otlp",transport="http"} 120
otelcol_receiver_refused_spans{receiver="otlp",transport="http"} 3
otelcol_processor_memory_limiter_refused_spans_total{processor="memory_limiter"} 2
otelcol_exporter_sent_spans{exporter="otlp_grpc/tempo"} 100
otelcol_exporter_queue_size{exporter="otlp_grpc/tempo"} 4
otelcol_processor_tail_sampling_count_traces_sampled{policy="erreurs",sampled="true"} 7
otelcol_processor_tail_sampling_count_traces_sampled{policy="erreurs",sampled="false"} 70
otelcol_process_memory_rss 5.2e+07
"""
    m = lire_mesures_otel(texte)
    assert m["recus"] == 120 and m["refuses"] == 5 and m["envoyes"] == 100 and m["file_attente"] == 4
    assert m["traces_gardees"] == 7 and m["memoire_octets"] == 5.2e7 and m["echecs_envoi"] is None


# Base : identifiant de trace des alertes

def test_alerts_carry_the_trace_that_wrote_them():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute((ROOT / "db" / "init" / "27_traces.sql").read_text())
        with c.transaction(force_rollback=True):
            c.execute("SELECT set_config('mars.trace_id', %s, true)", ("1" * 32,))
            aid = c.execute("INSERT INTO alerts (type, severity, event_time, geom, details, rule_version) VALUES "
                            "('DARK_SHIP', 'faible', now(), 'SRID=4326;POINT(0 45)', '{\"a\": 1}', 'essai') "
                            "RETURNING id").fetchone()[0]
            c.execute("SELECT set_config('mars.trace_id', '', true)")
            c.execute("UPDATE alerts SET status = 'acquittee', details = details || '{\"b\": 2}' WHERE id = %s", (aid,))
            d = c.execute("SELECT details FROM alerts WHERE id = %s", (aid,)).fetchone()[0]
            assert d["trace_id"] == "1" * 32 and d["trace_id_creation"] == "1" * 32 and d["b"] == 2
            c.execute("SELECT set_config('mars.trace_id', %s, true)", ("2" * 32,))
            c.execute("UPDATE alerts SET details = '{\"c\": 3}' WHERE id = %s", (aid,))
            d = c.execute("SELECT details FROM alerts WHERE id = %s", (aid,)).fetchone()[0]
            assert d["trace_id"] == "2" * 32 and d["trace_id_creation"] == "1" * 32
