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


# Outils de supervision : jamais tracés

def test_supervision_tools_never_send_spans():
    texte = (ROOT / "docker-compose.serveur.yml").read_text().replace("!reset", "").replace("!override", "")
    services = yaml.safe_load(texte)["services"]
    for svc in ("grafana", "otel", "tempo"):
        env = services[svc].get("environment") or {}
        assert not [k for k in env if k.startswith(("GF_TRACING", "OTEL_"))], f"{svc} enverrait ses spans"
    c = yaml.safe_load((ROOT / "otel" / "collecteur.yaml").read_text())
    assert set(c["receivers"]["otlp"]["protocols"]) == {"http"}     # pas de port gRPC ouvert pour Grafana
    procs = c["service"]["pipelines"]["traces"]["processors"]
    assert procs.index("filter/outils") < procs.index("tail_sampling")
    conds = " ".join(c["processors"]["filter/outils"]["traces"]["span"])
    for outil in ('"grafana"', '"tempo"', "otelcol"):
        assert outil in conds, f"le collecteur ne filtre pas {outil}"


# Appels externes : service distant en attribut, nom lisible de l'opération

ATTENDUS = [
    ("GET", "https://cmr.earthdata.nasa.gov/search/granules.json?short_name=VJ102DNB_NRT&page_size=200", None,
     "NASA", "GET CMR granules VJ102DNB_NRT"),
    ("POST", "https://stac.dataspace.copernicus.eu/v1/search", b'{"collections": ["sentinel-1-grd"]}',
     "Copernicus", "POST STAC sentinel-1-grd"),
    ("GET", "https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-l2a/items", None,
     "Copernicus", "GET STAC sentinel-2-l2a"),
    ("GET", "https://eodata.dataspace.copernicus.eu/Sentinel-1/SAR/x-cog.tiff", None, "Copernicus", "GET S3 eodata"),
    ("GET", "https://sentinels.copernicus.eu/copernicus/sentinel-1/acquisition-plans", None, "ESA",
     "GET plans d'acquisition"),
    ("POST", "https://api.scaleway.com/instance/v1/zones/fr-par-1/servers/abc/action", None, "Scaleway",
     "POST Scaleway action"),
    ("GET", "https://api.scaleway.com/marketplace/v2/local-images?image_label=ubuntu", None, "Scaleway",
     "GET Scaleway local-images"),
    ("DELETE", "https://api.scaleway.com/block/v1alpha1/zones/fr-par-2/volumes/xyz", None, "Scaleway",
     "DELETE Scaleway volumes"),
    ("GET", "https://compte.eu.r2.cloudflarestorage.com/mars/vignettes/viirs/2026-10-18/g/12.png", None, "R2",
     "GET R2 preuve"),
    ("GET", "https://data.opensanctions.org/datasets/latest/maritime/maritime.csv", None, "OpenSanctions",
     "GET OpenSanctions maritime"),
    ("GET", "https://api.github.com/repos/o/r/contents/Vessels1.db", None, "GitHub", "GET GitHub version de Vessels1.db"),
    ("GET", "https://raw.githubusercontent.com/o/r/main/Vessels1.db", None, "GitHub", "GET GitHub Vessels1.db"),
    ("GET", "https://www.vesselfinder.com/vessels/details/227000000", None, "VesselFinder", "GET VesselFinder fiche"),
    ("POST", "https://ntfy.sh/sujet_secret", None, "ntfy", "POST ntfy alerte"),
]


@pytest.mark.parametrize("methode,url,corps,service,nom", ATTENDUS)
def test_external_calls_have_readable_names(methode, url, corps, service, nom):
    assert traces.nom_operation(methode, url, corps) == (service, nom)
    assert not re.match(r"^\w+(GET|POST)", nom) and nom.split()[1] != service or service in nom   # pas de « NASAGET NASA »


def test_requests_hook_names_the_span():
    class Span:
        def __init__(self):
            self.attrs, self.name = {}, "GET"

        def is_recording(self):
            return True

        def set_attribute(self, k, v):
            self.attrs[k] = v

        def update_name(self, n):
            self.name = n

    class Requete:
        method, url, body = "GET", "https://cmr.earthdata.nasa.gov/search/granules.json?short_name=VNP02DNB_NRT", None
    sp = Span()
    traces.requete_sortante(sp, Requete())
    assert sp.name == "GET CMR granules VNP02DNB_NRT" and sp.attrs["peer.service"] == "NASA"
    assert traces.nom_operation("GET", "http://backend:8000/api/health") == (None, None)   # appel interne : inchangé


def test_every_hand_made_external_span_is_named_by_its_operation():
    """Tout span écrit à la main qui porte peer.service s'appelle « MÉTHODE opération »."""
    motif = re.compile(r"\.span\(\s*(f?\"[^\"]*\"(?:\s*\+[^,]+)?)\s*,[^)]*peer\.service", re.S)
    trouves = []
    for f in [*(ROOT / "mars").rglob("*.py"), *(ROOT / "scripts").glob("*.py"), *(ROOT / "backend").glob("*.py"),
              *(ROOT / "travailleurs").glob("*.py")]:
        for m in motif.finditer(f.read_text()):
            trouves.append((f.name, m.group(1)))
            assert re.match(r'f?"(GET|PUT|POST|HEAD|DELETE|LIST|CONNECT|\{VERBES\[op\]\}) ', m.group(1)), (f.name, m.group(1))
    assert len(trouves) >= 4                  # R2, ntfy, AISStream, téléchargements des travailleurs


def test_r2_spans_are_named_by_operation_and_object_kind(spans):
    from mars.r2 import R2

    class S3:
        def put_object(self, **k):
            pass

        def head_object(self, **_k):
            import hashlib
            return {"ContentLength": 3, "ETag": hashlib.md5(b"abc").hexdigest()}

        def delete_object(self, **k):
            pass
    r = R2(client=S3(), bucket="b")
    r.put_verified(b"abc", "ais/positions/zone=bretagne/date=2026-10-18/x.parquet")
    r.delete("sauvegardes/mars_20261019.dump")
    noms = [s.name for s in spans.get_finished_spans()]
    assert noms == ["PUT R2 archive", "HEAD R2 archive", "DELETE R2 sauvegarde"]
    assert all(s.attributes["peer.service"] == "R2" for s in spans.get_finished_spans())


# Recherche des granules VIIRS : en parallèle, bornée, chaque appel dans la trace de la tâche

def test_cmr_searches_run_in_parallel_within_the_task_trace(spans):
    import threading
    import time
    from datetime import datetime, timezone
    from mars import viirs
    from mars.config import load_rules
    etat = {"en_cours": 0, "max": 0, "appels": 0}
    verrou = threading.Lock()

    class Reponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"feed": {"entry": []}}

    class Session:
        def get(self, _url, **k):
            with verrou:
                etat["en_cours"] += 1
                etat["appels"] += 1
                etat["max"] = max(etat["max"], etat["en_cours"])
            with traces.span(f"GET CMR granules {k['params']['short_name']}", **{"peer.service": "NASA"}):
                time.sleep(0.05)
            with verrou:
                etat["en_cours"] -= 1
            return Reponse()

    class Conn:
        def execute(self, *_a):
            return self

        def fetchall(self):
            return []
    with traces.span("tache viirs", nouvelle_trace=True):
        p = viirs.plan(Conn(), Session(), load_rules(), datetime(2026, 10, 19, 7, tzinfo=timezone.utc))
    # 3 nuits × 3 satellites × (DNB, géolocalisation) × (temps quasi réel vide, puis produit standard)
    assert p["granules"] == [] and etat["appels"] == 36
    assert 1 < etat["max"] <= viirs.CMR_SIMULTANES
    fini = spans.get_finished_spans()
    tache = [s for s in fini if s.name == "tache viirs"][0]
    recherche = [s for s in fini if s.name == "viirs.recherche_granules"][0]
    appels = [s for s in fini if s.name.startswith("GET CMR granules")]
    assert len(appels) == 36 and recherche.parent.span_id == tache.context.span_id
    assert all(a.parent.span_id == recherche.context.span_id and a.context.trace_id == tache.context.trace_id
               for a in appels)


def test_log_filter_keeps_args_for_uvicorn_access_lines():
    """22/10/2026 : le filtre des journaux remettait args à None ; le formateur d'accès d'uvicorn, qui lit args
    directement, levait « cannot unpack non-iterable NoneType » à chaque requête tracée (journal d'accès perdu)."""
    import logging
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from mars import traces as tr
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:1", "GET", "/api/health", "1.1", 200), None)
    with TracerProvider().get_tracer("essai").start_as_current_span("requete"):
        assert trace.get_current_span().get_span_context().is_valid
        tr._FiltreJournal().filter(rec)
    assert rec.args == ("127.0.0.1:1", "GET", "/api/health", "1.1", 200)
    message = rec.getMessage()
    assert message.startswith('127.0.0.1:1 - "GET /api/health HTTP/1.1" 200') and "trace_id=" in message


def test_manual_command_output_has_no_trace_suffix_but_service_logs_do():
    """24/10/2026 : la sortie d'une commande lue par un humain (diagnostic, tableau) reste sans « trace_id=… span_id=… »,
    les journaux des services (sortie de la boucle, logging) le gardent."""
    import subprocess
    code = ("import logging, sys; from mars import traces; traces.init('essai', sortie_lisible=sys.argv[1] == '1'); "
            "logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True); traces.journaux(sortie=sys.argv[1] != '1')\n"
            "with traces.span('commande essai'):\n    print('ligne lue', flush=True); logging.getLogger('mars').info('journal')")
    env = {k: v for k, v in os.environ.items() if k not in ("OTEL_SDK_DISABLED", "OTEL_EXPORTER_OTLP_ENDPOINT")}
    env["PYTHONPATH"] = str(ROOT)                     # spans valides, rien d'envoyé (aucune adresse de collecteur)
    commande = subprocess.run([sys.executable, "-c", code, "1"], capture_output=True, text=True, env=env, cwd=ROOT)
    service = subprocess.run([sys.executable, "-c", code, "0"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert commande.stdout.strip() == "ligne lue", commande.stdout + commande.stderr
    assert "trace_id=" in commande.stderr, "le journal (logging) garde le suffixe"
    assert "ligne lue trace_id=" in service.stdout


def test_only_the_service_loop_keeps_the_suffix_on_its_output():
    """taches.py : la boucle (service) garde le suffixe ; toute commande lancée à la main (sentinel-diagnostic,
    controle-diagnostic, satellites-pause, essai-restauration, deploiement) a une sortie lisible."""
    t = (ROOT / "scripts" / "taches.py").read_text()
    assert 'traces.init("mars-taches", sortie_lisible=a.commande != "boucle")' in t
    assert "sortie_lisible=True" in (ROOT / "scripts" / "build_masks.py").read_text()
    for s in ("backend/app.py", "scripts/ais_live.py", "scripts/ais_ingest.py"):
        assert "sortie_lisible" not in (ROOT / s).read_text(), f"{s} : service, journaux avec suffixe"
