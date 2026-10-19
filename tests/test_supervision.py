"""Supervision (étape D), après l'incident du 18/10/2026 : disque plein, collecte figée sans s'arrêter (conteneur
« Up »), ingestion en échec pendant 21 h, personne de prévenu.

Contrôle de santé et redémarrage (scripts/sante.py), collecte et ingestion qui ne meurent plus en silence, alertes de
secours par ntfy (répétition au plus une fois par heure, retour à la normale), configuration de Compose et de Grafana,
rôle grafana_lecture et requêtes des tableaux (base de test, MARS_TEST_DATABASE_URL). Le gel réel d'une collecte dans
un conteneur, détecté et relancé par Docker, est éprouvé par scripts/verifier_images.sh sante."""
import importlib.util
import io
import json
import os
import re
import sys
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from mars import supervision as S
from mars.config import ROOT

URL = os.environ.get("MARS_TEST_DATABASE_URL")


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# 1. Contrôle de santé et redémarrage automatique

def _sante(monkeypatch, tmp_path, ages: dict, depuis_s: float = 3600, pids=(42,)):
    sante = _module("sante", ROOT / "scripts" / "sante.py")
    monkeypatch.setattr(sante, "LIVE", tmp_path)
    monkeypatch.setattr(sante, "processus", lambda _m: list(pids))
    monkeypatch.setattr(sante, "demarre_depuis_s", lambda _p: depuis_s)
    now = time.time()
    for name, age in ages.items():
        p = Path(name) if name.startswith("/") else tmp_path / name
        p.touch()
        os.utime(p, (now - age, now - age))
    return sante, now


def test_frozen_collector_is_detected_and_restarted(monkeypatch, tmp_path):
    """Cas du 18/10/2026 : processus vivant, plus aucun fichier depuis 01:27, aucune erreur d'écriture signalée."""
    sante, now = _sante(monkeypatch, tmp_path, {S.SIGNE_COLLECTE: 400})
    sain, motif, redemarrer = sante.verifier("collecte", now)
    assert (sain, redemarrer) == (False, True) and motif == "aucun fichier Parquet écrit depuis 7 min"
    killed, logged = [], []
    monkeypatch.setattr(sante, "arreter", killed.extend)
    monkeypatch.setattr(sante, "journal", lambda *a: logged.append(a))
    monkeypatch.setattr(sys, "argv", ["sante.py", "collecte"])
    assert sante.main() == 1 and killed == [42]
    assert logged[0][0] == "collecte" and logged[0][1].startswith("aucun fichier Parquet")


def test_healthy_or_starting_collector_is_left_alone(monkeypatch, tmp_path):
    sante, now = _sante(monkeypatch, tmp_path, {S.SIGNE_COLLECTE: 60})
    assert sante.verifier("collecte", now)[0]
    sante, now = _sante(monkeypatch, tmp_path / "neuf", {}, depuis_s=90)          # démarrage : délai de grâce
    assert sante.verifier("collecte", now)[0]


def test_write_failures_make_the_collector_unhealthy_without_restart(monkeypatch, tmp_path):
    """Disque plein : relancer perdrait les messages gardés en mémoire ; l'alerte disque prévient."""
    sante, now = _sante(monkeypatch, tmp_path, {S.SIGNE_COLLECTE: 900, sante_echec(): 30})
    sain, motif, redemarrer = sante.verifier("collecte", now)
    assert not sain and not redemarrer and "disque plein" in motif


def sante_echec():
    return ".echec_ecriture_collecte"


def test_frozen_ingestion_loop_is_restarted(monkeypatch, tmp_path):
    boucle = tmp_path / "boucle_ingestion"
    sante, now = _sante(monkeypatch, tmp_path, {str(boucle): 600})
    monkeypatch.setattr(sante, "BOUCLE_INGESTION", str(boucle))
    assert sante.verifier("ingestion", now) == (False, "boucle d'ingestion figée depuis 10 min", True)
    os.utime(boucle, None)
    assert sante.verifier("ingestion", time.time())[0]


def test_restart_is_recorded_in_task_runs(monkeypatch):
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    sante = _module("sante", ROOT / "scripts" / "sante.py")
    monkeypatch.setenv("DATABASE_URL", URL)
    sante.journal("collecte", "aucun fichier Parquet écrit depuis 7 min", {"processus": [42]})
    with psycopg.connect(URL, autocommit=True) as c:
        row = c.execute("SELECT status, details FROM task_runs WHERE task = 'redemarrage_collecte' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
        c.execute("DELETE FROM task_runs WHERE task = 'redemarrage_collecte'")
    assert row[0] == "echec" and row[1]["motif"] == "aucun fichier Parquet écrit depuis 7 min"


# Collecte et ingestion : plus de mort silencieuse

def test_collector_keeps_messages_when_writing_fails(monkeypatch, tmp_path):
    live = _module("ais_live_essai", ROOT / "scripts" / "ais_live.py")
    monkeypatch.setattr(live, "OUT", tmp_path)
    monkeypatch.setattr(live, "MAX_EN_ATTENTE", 3)
    buf = live.Buffer()
    for k in range(5):
        buf.add({"MessageType": "PositionReport", "MetaData": {"latitude": 48.3, "longitude": -5.2, "MMSI": k,
                                                               "time_utc": "2026-10-18 01:27:00"},
                 "Message": {"PositionReport": {"Sog": 12}}})

    def plein(*_a, **_k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(live.pd.DataFrame, "to_parquet", plein)
    with pytest.raises(OSError):
        buf.flush()
    assert len(buf.pos) == 3 and [r["mmsi"] for r in buf.pos] == [2, 3, 4]     # plafond : les plus anciens partent
    assert not list(tmp_path.rglob("*.tmp"))                                    # aucun fichier partiel laissé
    monkeypatch.undo()
    monkeypatch.setattr(live, "OUT", tmp_path)
    assert buf.flush() == 1 and not buf.pos and len(list(tmp_path.rglob("*.parquet"))) == 1


def test_collector_exits_when_an_internal_task_dies(monkeypatch, tmp_path):
    """Jamais de collecte à moitié vivante : une tâche interne morte arrête le processus (Docker le relance)."""
    import asyncio
    import types
    live = _module("ais_live_essai2", ROOT / "scripts" / "ais_live.py")
    monkeypatch.setattr(live, "OUT", tmp_path)

    class Ws:
        async def __aenter__(self):
            raise RuntimeError("boucle cassée")

        async def __aexit__(self, *_a):
            return False

    async def explode(*_a):
        raise MemoryError("tâche morte")
    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=lambda *_a, **_k: Ws()))
    monkeypatch.setattr(live.asyncio, "sleep", explode)
    with pytest.raises(SystemExit) as e:
        asyncio.run(asyncio.wait_for(live.collect("cle"), 10))
    assert e.value.code == 1


def test_ingestion_survives_disk_full_and_says_so(monkeypatch, capsys, tmp_path):
    import psycopg
    ing = _module("ais_ingest_essai", ROOT / "scripts" / "ais_ingest.py")
    calls, recorded = [], []

    class Fake:
        conn = type("C", (), {"closed": False})()
        retard_s = 0.0

        def __init__(self, *_a):
            pass

        def seed(self):
            return 0

        def run_once(self, recent_only=True):
            calls.append(recent_only)
            if len(calls) <= 2:
                raise psycopg.errors.DiskFull("could not extend file: No space left on device")
            return {"fichiers": 0}

    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 4:
            raise KeyboardInterrupt
    monkeypatch.setattr(ing, "Ingestor", Fake)
    monkeypatch.setattr(ing, "connect", lambda: None)
    monkeypatch.setattr(ing, "load_rules", lambda: {"ingestion": {}})
    monkeypatch.setattr(ing, "signe", lambda *a: recorded.append(("signe", a[1])))
    monkeypatch.setattr(ing, "record_failure", lambda m, _n: recorded.append(("echec", m)))
    monkeypatch.setattr(ing, "toucher", lambda _p: None)
    monkeypatch.setattr(ing.time, "sleep", sleep)
    monkeypatch.setattr(sys, "argv", ["ais_ingest.py", "--root", str(tmp_path)])
    with pytest.raises(KeyboardInterrupt):
        ing.main()
    out = capsys.readouterr().out
    assert "ÉCHEC DE L'INGESTION (1) : DISQUE PLEIN, la base refuse d'écrire" in out
    assert "Ingestion rétablie après 2 passage(s) en échec" in out
    assert recorded[0][0] == "echec" and recorded[-1] == ("signe", "ingestion")
    assert sleeps[:2] == [30.0, 60.0]                                    # attente croissante
    assert len([r for r in recorded if r[0] == "echec"]) == 1           # une fois par heure au plus


# 2. Alertes sur le téléphone

class Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class Opener:
    def __init__(self, error=None):
        self.error, self.requests = error, []

    def __call__(self, req, **_kw):
        self.requests.append(req)
        if self.error:
            raise self.error
        return Resp(b"{}")


def test_ntfy_message_is_short_and_the_topic_never_leaks():
    op = Opener()
    S.Ntfy("sujet-secret-123", "https://ntfy.sh", op).envoyer("MARS C2 : disque presque plein", "12 % libres")
    req = op.requests[0]
    assert req.full_url == "https://ntfy.sh/sujet-secret-123" and req.data == "12 % libres".encode()
    assert req.headers["Title"].startswith("=?UTF-8?B?") and req.headers["Priority"] == "high"
    op = Opener(error=urllib.error.HTTPError("https://ntfy.sh/sujet-secret-123", 429, "x", {}, None))
    with pytest.raises(RuntimeError) as e:
        S.Ntfy("sujet-secret-123", None, op).envoyer("t", "m")
    assert "sujet-secret-123" not in str(e.value) and "429" in str(e.value)


def test_backup_alerts_repeat_hourly_at_most_and_announce_recovery():
    op = Opener()
    sec = S.Secours(S.Ntfy("s", None, op))
    etat = {"disque": "9 % libres (0,8 Go), seuil 15 %", "collecte": None, "ingestion": None}
    assert sec.evaluer(etat, now=0, log=lambda _m: None) == ["disque"]
    assert sec.evaluer(etat, now=1800, log=lambda _m: None) == []              # pas de répétition dans l'heure
    assert sec.evaluer(etat, now=3600, log=lambda _m: None) == ["disque"]
    assert sec.evaluer({**etat, "disque": None}, now=3700, log=lambda _m: None) == ["disque_retablie"]
    titles = [r.headers["Title"] for r in op.requests]
    assert len(titles) == 3
    import base64
    last = base64.b64decode(titles[-1][len("=?UTF-8?B?"):-2]).decode()
    assert last == "MARS C2 : retour à la normale"
    for r in op.requests:                                                      # rien de sensible
        assert not re.search(r"\d{9}|IMO|\d+\.\d{3,}", r.data.decode())


def test_backup_alert_not_sent_is_retried_next_minute():
    sec = S.Secours(S.Ntfy("s", None, Opener(error=OSError("réseau"))))
    assert sec.evaluer({"collecte": "aucun fichier"}, now=0, log=lambda _m: None) == []
    sec.ntfy.opener = Opener()
    assert sec.evaluer({"collecte": "aucun fichier"}, now=60, log=lambda _m: None) == ["collecte"]


class Conn:
    def __init__(self, row=None, error=None):
        self.row, self.error = row, error

    def execute(self, *_a):
        if self.error:
            raise self.error
        return self

    def fetchone(self):
        return self.row


def test_conditions_are_read_without_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(S.shutil, "disk_usage", lambda _p: type("U", (), {"total": 100, "free": 9})())
    now = datetime.now(timezone.utc)
    stale = tmp_path / S.SIGNE_COLLECTE
    stale.touch()
    os.utime(stale, (time.time() - 900, time.time() - 900))
    c = S.conditions(Conn((now - timedelta(minutes=30), {"retard_s": 0})), tmp_path, now=now)
    assert c["disque"].startswith("9 % libres") and c["collecte"] == "aucun fichier écrit depuis 15 min"
    assert c["ingestion"] == "aucun passage réussi depuis 30 min"
    monkeypatch.setattr(S.shutil, "disk_usage", lambda _p: type("U", (), {"total": 100, "free": 50})())
    os.utime(stale, None)
    c = S.conditions(Conn((now, {"retard_s": 1200})), tmp_path, now=now)
    assert c == {"disque": None, "collecte": None, "ingestion": "en retard de 20 min"}
    assert S.conditions(Conn(error=RuntimeError("base")), tmp_path, now=now)["ingestion"].startswith("base illisible")


def test_tester_alerte_without_topic_sends_nothing(monkeypatch, capsys):
    taches = _module("taches_essai", ROOT / "scripts" / "taches.py")
    monkeypatch.delenv("NTFY_SUJET", raising=False)
    monkeypatch.setattr(taches, "load_env", lambda: None)
    monkeypatch.setattr(sys, "argv", ["taches.py", "tester-alerte"])
    with pytest.raises(SystemExit):
        taches.main()
    assert "NTFY_SUJET absent" in capsys.readouterr().out


def test_grafana_health():
    assert S.grafana_repond("http://g", lambda *_a, **_k: Resp(b'{"database": "ok"}'))
    assert not S.grafana_repond("http://g", lambda *_a, **_k: (_ for _ in ()).throw(OSError()))


# 3. Grafana et Compose

def test_compose_restarts_frozen_services_and_grafana_is_locked_down():
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    for svc, arg in (("collector", "collecte"), ("ingest", "ingestion")):
        s = base[svc]
        assert s["init"] is True and s["restart"] == "unless-stopped"
        assert s["healthcheck"]["test"] == ["CMD", "python", "scripts/sante.py", arg]
        assert "config/supervision.env" in (s["env_file"] if isinstance(s["env_file"], list) else [s["env_file"]])
    text = (ROOT / "docker-compose.serveur.yml").read_text().replace("!reset", "").replace("!override", "")
    g = yaml.safe_load(text)["services"]["grafana"]
    assert re.fullmatch(r"grafana/grafana:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", g["image"])
    assert g["mem_limit"] == "256m" and g["ports"] == ["127.0.0.1:3000:3000"]
    assert g["environment"]["GF_SECURITY_ADMIN_PASSWORD"].startswith("${GRAFANA_ADMIN_MOT_DE_PASSE:?")
    assert g["volumes"][0] == "./grafana/provisioning:/etc/grafana/provisioning:ro"


def test_thresholds_live_in_one_file():
    env = dict(line.split("=", 1) for line in (ROOT / "config" / "supervision.env").read_text().splitlines()
               if line and not line.startswith("#"))
    assert env == {"SEUIL_DISQUE_LIBRE_PCT": "15", "SEUIL_COLLECTE_MIN": "5", "SEUIL_INGESTION_MIN": "10",
                   "HEURE_NUIT_VIIRS": "8", "ECHECS_SUCCESSIFS": "2", "SEUIL_COLLECTEUR_MUET_MIN": "15"}
    rules = (ROOT / "grafana" / "provisioning" / "alerting" / "regles.yaml").read_text()
    for cle in ("seuil_disque_pct", "seuil_collecte_min", "seuil_ingestion_min", "seuil_heure_nuit_viirs",
                "seuil_echecs_successifs", "seuil_collecteur_muet_min"):
        assert f"cle = '{cle}'" in rules
    assert "$SEUIL" not in rules and "$$" not in rules        # Grafana ne les remplacerait pas


def test_alert_rules_cover_every_case():
    doc = yaml.safe_load((ROOT / "grafana" / "provisioning" / "alerting" / "regles.yaml").read_text())
    titles = [r["title"] for r in doc["groups"][0]["rules"]]
    assert titles == ["Disque presque plein", "Collecte AIS figée", "Ingestion AIS en échec ou en retard",
                      "Nuit VIIRS absente", "Tâche en échec plusieurs fois de suite",
                      "Collecteur de traces muet", "Collecteur de traces saturé"]
    ntfy = yaml.safe_load((ROOT / "grafana" / "provisioning" / "alerting" / "ntfy.yaml").read_text())
    assert ntfy["policies"][0]["repeat_interval"] == "1h"
    r = ntfy["contactPoints"][0]["receivers"][0]
    assert r["disableResolveMessage"] is False and r["settings"]["url"].startswith("$NTFY_URL/$NTFY_SUJET?")


def test_dashboards_are_generated_and_written_without_dashes():
    gen = _module("generer", ROOT / "grafana" / "generer.py")
    files = gen.generer()
    assert set(files) == {"vue_ensemble.json", "direct.json", "disque.json", "taches.json", "satellites.json",
                          "alertes.json", "traces.json"}
    for name, text in files.items():
        assert (gen.OUT / name).read_text() == text, f"{name} à régénérer : python grafana/generer.py"
        d = json.loads(text)
        visible = [d["title"], d["description"]] + [p["title"] for p in d["panels"]] + \
                  [p.get("description", "") for p in d["panels"]]
        assert not [v for v in visible if re.search(r"[‐–—]|\w-\w| - ", v)], name
        for p in d["panels"]:
            for t in p.get("targets", []):
                if "rawSql" not in t:               # panneaux Tempo (TraceQL)
                    assert t["datasource"]["uid"] == "tempo"
                    continue
                assert "positions " not in t["rawSql"].replace("positions_octets", "") or "FROM positions" \
                    not in t["rawSql"], f"{p['title']} lit la table des positions"


def test_overview_is_home_with_six_tiles_on_alert_thresholds():
    gen = _module("generer", ROOT / "grafana" / "generer.py")
    d = json.loads(gen.generer()["vue_ensemble.json"])
    tuiles = [p for p in d["panels"] if p["type"] == "stat"]
    assert [p["title"] for p in tuiles] == ["Collecte", "Ingestion", "Disque", "Tâches", "Satellites", "Traces"]
    uids = {json.loads(t)["uid"] for t in gen.generer().values()}
    seuils = {"Collecte": "seuil_collecte_min", "Ingestion": "seuil_ingestion_min", "Disque": "seuil_disque_pct",
              "Tâches": "seuil_echecs_successifs", "Satellites": "seuil_heure_nuit_viirs",
              "Traces": "seuil_collecteur_muet_min"}
    for p in tuiles:
        sql = p["targets"][0]["rawSql"]
        assert f"cle = '{seuils[p['title']]}'" in sql, p["title"]          # seuils de config/supervision.env
        assert " AS metric" in sql and " AS value" in sql and p["options"]["textMode"] == "name"
        lien = p["fieldConfig"]["defaults"]["links"][0]["url"]
        assert lien.split("?")[0].removeprefix("/d/") in uids, lien      # un clic mène au tableau détaillé
    (table,) = [p for p in d["panels"] if p["type"] == "table"]
    assert table["fieldConfig"]["defaults"]["noValue"] == "aucune analyse satellite tracée depuis le déploiement"
    assert "trace_id IS NOT NULL" in table["targets"][0]["rawSql"]
    assert any(o["matcher"]["options"] == "Trace" for o in table["fieldConfig"]["overrides"])
    compose = (ROOT / "docker-compose.serveur.yml").read_text()
    assert "HOME_DASHBOARD_PATH: /etc/grafana/provisioning/dashboards/mars/vue_ensemble.json" in compose
    assert "GF_TRACING_OPENTELEMETRY_OTLP_ADDRESS: otel:4317" in compose      # Grafana tracé lui même
    assert '"127.0.0.1:4317' not in compose and ":4317:4317" not in compose   # gRPC jamais publié


def test_grafana_macros_are_expanded_like_grafana():
    a, b = datetime(2026, 10, 18, tzinfo=timezone.utc), datetime(2026, 10, 19, tzinfo=timezone.utc)
    sql = S.developper("SELECT $__timeGroupAlias(minute, '1m', 0), x FROM t WHERE $__timeFilter(minute) "
                       "AND y >= $__timeFrom() AND y <= $__timeTo()", a, b)
    assert "to_timestamp(floor(extract(epoch FROM minute) / 60) * 60) AS \"time\"" in sql
    assert "minute BETWEEN '2026-10-18T00:00:00+00:00'::timestamptz AND '2026-10-19T00:00:00+00:00'" in sql
    assert "$__" not in sql


def test_every_dashboard_query_runs_as_grafana_under_a_second():
    """Toutes les requêtes des tableaux, sous le rôle grafana_lecture (droits de la migration 26), en lecture seule."""
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    gen = _module("generer", ROOT / "grafana" / "generer.py")
    with psycopg.connect(URL, autocommit=True) as c:
        rows = S.mesurer_requetes(c, gen.requetes())
    assert not [r for r in rows if r.get("erreur")], [r for r in rows if r.get("erreur")]
    assert max(r["duree_s"] for r in rows) < 1


def test_grafana_role_reads_only_what_it_needs():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    with psycopg.connect(URL, autocommit=True) as c:
        for sql, allowed in (("SELECT count(*) FROM mesures_disque", True), ("SELECT count(*) FROM task_runs", True),
                             ("SELECT id, etat, erreur FROM travailleurs", True),
                             ("SELECT jeton_hash FROM travailleurs", False), ("SELECT count(*) FROM positions", False),
                             ("SELECT name FROM vessels", False), ("SELECT details FROM alerts", False),
                             ("SELECT note FROM alert_actions", False), ("SELECT count(*) FROM watchlist", False),
                             ("INSERT INTO signes_de_vie (service, dernier_le) VALUES ('x', now())", False)):
            try:
                with c.transaction(force_rollback=True):
                    c.execute("SET LOCAL ROLE grafana_lecture")
                    c.execute(sql)
                ok = True
            except psycopg.errors.InsufficientPrivilege:
                ok = False
            assert ok == allowed, sql


def test_measures_and_parameters_are_written(tmp_path):
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    from mars.config import load_rules
    folder = tmp_path / "positions" / "zone=bretagne" / f"date={datetime.now(timezone.utc):%Y-%m-%d}"
    folder.mkdir(parents=True)
    (folder / "012700.parquet").write_bytes(b"x" * 1000)
    with psycopg.connect(URL, autocommit=True) as c:
        m = S.mesurer(c, tmp_path)
        S.parametres(c, load_rules())
        row = c.execute("SELECT ais_live_octets, ais_live_fichiers, dernier_parquet IS NOT NULL, base_octets > 0 "
                        "FROM mesures_disque ORDER BY mesure_le DESC LIMIT 1").fetchone()
        params = dict(c.execute("SELECT cle, valeur FROM parametres_supervision").fetchall())
        c.execute("DELETE FROM mesures_disque WHERE mesure_le >= now() - interval '1 minute'")
    assert row == (1000, 1, True, True) and m["positions_octets"] > 0
    assert params["seuil_disque_pct"] == 15 and params["budget_sentinel_eur"] == 6 and params["ratio_coupure"] == 0.2
