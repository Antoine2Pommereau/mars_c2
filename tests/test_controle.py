"""Panneau de contrôle (lot P), phase A : capteurs et dépenses, connexion des opérateurs, journal.

Calculs purs (réglages bornés, pauses dans les règles appliquées, mot de passe haché, date de reprise), puis sur la base
de test (MARS_TEST_DATABASE_URL) : pause et reprise journalisées et notifiées, reprise automatique et notification de
la veille, aucune création de travailleur en pause, commande manuelle refusée puis acceptée avec --malgre-pause, tâche
VIIRS consignée une fois par jour en pause puis due à la reprise, tip and cue en pause (aucune nouvelle demande, les
actives attendent avec le motif), budget du mois ; routes du panneau (écriture refusée sans connexion, connexion,
pause, plafond borné, demande d'observation d'un point, journal rempli pour chaque action)."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mars import controle
from mars import tip_and_cue as tc
from mars.config import ROOT, load_rules

URL = os.environ.get("MARS_TEST_DATABASE_URL")
RULES = load_rules()
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def _taches():
    sys.path.insert(0, str(ROOT / "scripts"))
    import taches
    return taches


# Calculs purs

def test_versioned_bounds_and_effective_rules():
    assert RULES["controle"]["plafonds"]["sentinel1"] == {"min": 0, "max": 4}
    r = controle.appliquer_reglages(RULES, {"plafond:sentinel1": 3, "plafond:sentinel2": 9}, {})
    assert r["sentinel"]["scenes_max_par_jour"] == 3 and r["sentinel2"]["scenes_max_par_jour"] == 4    # borné
    assert RULES["sentinel"]["scenes_max_par_jour"] == 2, "le fichier n'est jamais modifié"
    with pytest.raises(ValueError, match="hors de la plage 0 à 4"):
        controle.verifier_plafond(RULES, "sentinel1", 5)
    with pytest.raises(ValueError):
        controle.verifier_plafond(RULES, "viirs", 1)


def test_pause_in_effective_rules_and_tip_and_cue_missions():
    assert controle.en_pause(RULES, "sentinel1") is None                    # règles du fichier seules : jamais en pause
    fin = datetime(2026, 10, 24, 6, tzinfo=timezone.utc)
    r = controle.appliquer_reglages(RULES, {"plafond:sentinel2": 1}, {"sentinel1": {"motif": "budget", "jusqu_a": fin}})
    assert controle.en_pause(r, "sentinel1") == "Sentinel 1 en pause (budget, reprise le 24/10/2026 à 08:00)"
    assert tc.missions_retenues(r) == (["S2"], [controle.en_pause(r, "sentinel1")])
    r2 = controle.appliquer_reglages(RULES, {}, {"tip_and_cue": {"motif": "démonstration"}})
    assert tc.points_passage(None, r2, 1) == []                            # les planifiées attendent la reprise
    assert tc.missions_retenues(r2) == (["S1"], [])                        # Sentinel 2 coupé dans le fichier


def test_password_hash_and_operators():
    h = controle.hacher("correct horse", iterations=1000)
    assert h.startswith("pbkdf2_sha256:1000:") and "$" not in h and " " not in h
    assert controle.verifier_mot_de_passe("correct horse", h) and not controle.verifier_mot_de_passe("faux", h)
    assert not controle.verifier_mot_de_passe("x", "md5:abc")
    assert controle.operateurs(f"antoine:{h}, invite:{h}") == {"antoine": h, "invite": h}
    j, e = controle.jeton_session()
    assert controle.empreinte_jeton(j) == e and j not in e


def test_resume_date_in_paris_time():
    t = _taches()
    assert t.date_reprise("24/10/2026 08:00") == datetime(2026, 10, 24, 6, tzinfo=timezone.utc)   # heure d'été
    assert t.date_reprise("26/10/2026") == datetime(2026, 10, 26, 7, tzinfo=timezone.utc)         # heure d'hiver, 08:00


# Base de test

@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL, autocommit=True)
    _remise_a_zero(c)
    yield c
    _remise_a_zero(c)
    c.close()


def _remise_a_zero(c):
    c.execute("UPDATE interrupteurs_satellites SET en_pause = false, depuis = NULL, jusqu_a = NULL, motif = NULL, "
              "auteur = NULL, veille_notifiee_le = NULL")
    for sql in ("DELETE FROM journal_controle", "DELETE FROM reglages_controle", "DELETE FROM sessions_operateur",
                "DELETE FROM task_runs WHERE task IN ('viirs', 'sentinel', 'sentinel2')",
                "DELETE FROM demandes_origines", "DELETE FROM demandes_observation"):
        c.execute(sql)


class Envois(list):
    def __call__(self, categorie, gravite, titre, message):
        self.append((categorie, gravite, titre, message))


def test_pause_and_resume_are_journaled_and_notified(conn):
    envois = Envois()
    with pytest.raises(ValueError, match="motif obligatoire"):
        controle.basculer(conn, "sentinel1", True, "antoine", envoi=envois)
    fin = datetime.now(timezone.utc) + timedelta(days=2)
    e = controle.basculer(conn, "sentinel1", True, "antoine", "budget du mois", fin, envoi=envois)
    assert e["en_pause"] and e["motif"] == "budget du mois" and e["auteur"] == "antoine"
    assert controle.en_pause(controle.appliquer(conn, RULES), "sentinel1").startswith("Sentinel 1 en pause (budget du mois")
    controle.basculer(conn, "sentinel1", False, "antoine", envoi=envois)
    j = conn.execute("SELECT auteur, action, cible, avant->>'en_pause', apres->>'en_pause', motif FROM journal_controle "
                     "ORDER BY id").fetchall()
    assert j == [("antoine", "pause", "sentinel1", "false", "true", "budget du mois"),
                 ("antoine", "reprise", "sentinel1", "true", "false", None)]
    assert [x[2] for x in envois] == ["MARS C2 : Sentinel 1 en pause", "MARS C2 : Sentinel 1 repris"]


def test_automatic_resume_and_notice_the_day_before(conn):
    envois = Envois()
    now = datetime.now(timezone.utc)
    controle.basculer(conn, "viirs", True, "antoine", "maintenance", now + timedelta(hours=20), envoi=envois)
    controle.basculer(conn, "sentinel2", True, "antoine", "essai", now + timedelta(days=3), envoi=envois)
    del envois[:]
    assert controle.echeances(conn, now, envois) == {"reprises": [], "veilles": ["viirs"]}
    assert controle.echeances(conn, now, envois) == {"reprises": [], "veilles": []}          # une seule fois
    out = controle.echeances(conn, now + timedelta(hours=21), envois)
    assert out["reprises"] == ["viirs"]
    assert [x[2] for x in envois] == ["MARS C2 : reprise de VIIRS demain", "MARS C2 : VIIRS repris"]
    assert conn.execute("SELECT auteur, action FROM journal_controle WHERE cible = 'viirs' ORDER BY id DESC LIMIT 1"
                        ).fetchone() == ("automatique", "reprise")
    assert set(controle.pauses(conn)) == {"sentinel2"}


def test_no_worker_in_pause_manual_refused_then_forced(conn, monkeypatch):
    t = _taches()
    from mars import travailleurs
    lances = []
    monkeypatch.setattr(travailleurs, "launch", lambda *a, **_k: lances.append(a) or {"refus": "essai"})
    monkeypatch.setattr(travailleurs.Scaleway, "from_env", classmethod(lambda _cls: None))
    controle.basculer(conn, "viirs", True, "antoine", "essai du panneau", envoi=Envois())
    controle.basculer(conn, "sentinel1", True, "antoine", "essai du panneau", envoi=Envois())
    controle.basculer(conn, "sentinel2", True, "antoine", "essai du panneau", envoi=Envois())
    assert t.launch_viirs(conn) == {"en_pause": "VIIRS en pause (essai du panneau)"}
    assert t.launch_sentinel(conn, auto=True)["en_pause"].startswith("Sentinel 1 en pause")
    assert t.launch_sentinel2(conn)["en_pause"].startswith("Sentinel 2 en pause")
    with pytest.raises(travailleurs.RefusLancement, match="--malgre-pause"):
        t.launch_viirs(conn, manuel=True)
    with pytest.raises(travailleurs.RefusLancement, match="--malgre-pause"):
        t.launch_sentinel(conn, 12345)
    with pytest.raises(travailleurs.RefusLancement, match="--malgre-pause"):
        t.launch_sentinel2(conn, 12345)
    assert lances == []
    # Malgré la pause : consigné, puis le lancement suit son cours (ici, clés Scaleway absentes)
    with pytest.raises(travailleurs.RefusLancement, match="clés absentes"):
        t.launch_viirs(conn, manuel=True, malgre_pause=True, motif="nuit à rattraper")
    assert conn.execute("SELECT auteur, action, cible, motif FROM journal_controle WHERE action = 'malgre_pause'"
                        ).fetchall() == [("terminal", "malgre_pause", "viirs", "nuit à rattraper")]


def test_viirs_task_logged_once_a_day_in_pause_and_due_after_resume(conn):
    t = _taches()
    now = datetime.now(timezone.utc).replace(hour=23, minute=0)
    controle.basculer(conn, "viirs", True, "antoine", "essai", envoi=Envois())
    assert t.due(conn, "viirs", now, 1, "06:30")
    assert t.run(conn, "viirs", lambda: t.launch_viirs(conn), quiet=True) == {"en_pause": "VIIRS en pause (essai)",
                                                                             "duree_s": pytest.approx(0, abs=1)}
    assert not t.due(conn, "viirs", now, 1, "06:30"), "décision consignée une fois par jour"
    controle.basculer(conn, "viirs", False, "antoine", envoi=Envois())
    assert t.due(conn, "viirs", now, 1, "06:30"), "nuit rattrapée le jour même de la reprise"


def test_tip_and_cue_in_pause_creates_nothing_and_keeps_active_requests(conn):
    rules = {**RULES, "tip_and_cue": {**RULES["tip_and_cue"], "actif": True}}
    now = datetime.now(timezone.utc)
    ids = [conn.execute(
        """INSERT INTO demandes_observation (tip, priorite, position, position_le, etat, expire_le)
           VALUES ('operateur', 5, ST_SetSRID(ST_MakePoint(-6.1, 48.0), 4326)::geography, %s, %s, %s) RETURNING id""",
        (now, etat, now + timedelta(hours=h))).fetchone()[0] for etat, h in (("planifiee", 10), ("en_attente", -1))]
    controle.basculer(conn, "tip_and_cue", True, "antoine", "démonstration", envoi=Envois())
    r = tc.cycle(conn, controle.appliquer(conn, rules), now)
    assert r == {"en_pause": "Tip and cue en pause (démonstration)", "expirees": 1, "mises_en_attente": 1}
    assert conn.execute("SELECT etat, motif FROM demandes_observation WHERE id = %s", (ids[0],)).fetchone() == \
        ("planifiee", "Tip and cue en pause (démonstration)")
    assert conn.execute("SELECT count(*) FROM demandes_observation").fetchone()[0] == 2


def test_month_budget(conn):
    now = datetime.now(timezone.utc)
    b = controle.budget(conn, RULES, now)
    assert set(b["sources"]) == {"sentinel1", "sentinel2", "viirs"}
    assert b["budget_sentinel"]["plafond"] == RULES["sentinel"]["budget_mensuel_eur"]
    assert b["budget_sentinel"]["restant"] == pytest.approx(
        max(b["budget_sentinel"]["plafond"] - b["budget_sentinel"]["engage"], 0), abs=0.01)


# Routes du panneau

@pytest.fixture
def client(conn, monkeypatch):
    """Application réduite aux routes du panneau, sur la base de test remise à zéro par `conn`."""
    assert not conn.closed
    sys.path.insert(0, str(ROOT / "backend"))
    import asyncpg
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import controle as api
    monkeypatch.setenv("MARS_OPERATEURS", f"antoine:{controle.hacher('bon mot de passe', iterations=1000)}")
    monkeypatch.setattr(controle, "notifier", Envois())
    api._echecs.clear()

    @asynccontextmanager
    async def vie(app):
        app.state.pool = await asyncpg.create_pool(URL, min_size=1, max_size=2)
        yield
        await app.state.pool.close()
    app = FastAPI(lifespan=vie)
    app.include_router(api.router)
    with TestClient(app) as c:
        yield c


def test_write_routes_refused_without_login(client):
    for path, body in (("/api/controle/satellites/sentinel1", {"en_pause": True, "motif": "x"}),
                       ("/api/controle/plafonds/sentinel1", {"valeur": 1, "motif": "essai"}),
                       ("/api/controle/demandes", {"lon": -6, "lat": 48, "rayon_m": 5000, "motif": "essai"})):
        assert client.post(path, json=body).status_code == 401
    assert client.get("/api/controle/etat").status_code == 200                     # lecture sans connexion
    assert client.get("/api/controle/session").json() == {"operateur": None, "configure": True}


def test_login_actions_and_journal(client, conn):
    assert client.post("/api/controle/connexion", json={"operateur": "antoine", "mot_de_passe": "faux"}).status_code == 401
    r = client.post("/api/controle/connexion", json={"operateur": "antoine", "mot_de_passe": "bon mot de passe"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=43200" in cookie
    assert conn.execute("SELECT count(*) FROM sessions_operateur WHERE operateur = 'antoine'").fetchone()[0] == 1
    assert client.get("/api/controle/session").json()["operateur"] == "antoine"
    # Autre origine : refusée même connecté
    assert client.post("/api/controle/plafonds/sentinel1", json={"valeur": 1, "motif": "essai"},
                       headers={"origin": "https://ailleurs.example"}).status_code == 403
    assert client.post("/api/controle/satellites/sentinel1", json={"en_pause": True}).status_code == 422   # motif
    r = client.post("/api/controle/satellites/sentinel1", json={"en_pause": True, "motif": "budget",
                                                                "jusqu_a": "2099-01-01T08:00:00+01:00"})
    assert r.status_code == 200 and r.json()["en_pause"]
    assert client.post("/api/controle/plafonds/sentinel1", json={"valeur": 9, "motif": "essai"}).status_code == 422
    assert client.post("/api/controle/plafonds/sentinel1", json={"valeur": 1, "motif": "un seul passage"}).status_code == 200
    assert controle.appliquer(conn, RULES)["sentinel"]["scenes_max_par_jour"] == 1
    assert client.post("/api/controle/demandes", json={"lon": -6.1, "lat": 48.0, "rayon_m": 99999,
                                                       "motif": "essai"}).status_code == 422
    d = client.post("/api/controle/demandes", json={"lon": -6.1, "lat": 48.0, "rayon_m": 8000, "motif": "nappe vue"})
    assert d.status_code == 200
    assert conn.execute("SELECT tip, auteur, rayon_demande_m, motif FROM demandes_observation WHERE id = %s",
                        (d.json()["id"],)).fetchone() == ("operateur", "antoine", 8000.0, "nappe vue")
    e = client.get("/api/controle/etat").json()
    assert e["plafonds"]["sentinel1"] == {"valeur": 1, "defaut": 2, "min": 0, "max": 4, "regle_par_le_panneau": True}
    assert [i for i in e["interrupteurs"] if i["source"] == "sentinel1"][0]["en_pause"]
    client.post("/api/controle/deconnexion")
    assert client.post("/api/controle/plafonds/sentinel1", json={"valeur": 2, "motif": "essai"}).status_code == 401
    actions = [(x["auteur"], x["action"], x["cible"]) for x in reversed(client.get("/api/controle/journal").json())]
    assert actions == [("antoine", "connexion_refusee", "operateur"), ("antoine", "connexion", "operateur"),
                       ("antoine", "pause", "sentinel1"), ("antoine", "plafond", "sentinel1"),
                       ("antoine", "demande", "tip_and_cue"), ("antoine", "deconnexion", "operateur")]


def test_login_throttled_after_five_failures(client):
    for _ in range(5):
        assert client.post("/api/controle/connexion", json={"operateur": "x", "mot_de_passe": "y"}).status_code == 401
    assert client.post("/api/controle/connexion", json={"operateur": "antoine",
                                                        "mot_de_passe": "bon mot de passe"}).status_code == 429


# Phase B : état du système

def test_container_memory_from_own_cgroup(tmp_path):
    from mars import supervision
    assert supervision.memoire_conteneur(str(tmp_path)) is None                  # hors conteneur : omise
    (tmp_path / "memory.current").write_text("314572800\n")
    (tmp_path / "memory.max").write_text("max\n")
    assert supervision.memoire_conteneur(str(tmp_path)) == {"memoire_mo": 300, "memoire_max_mo": None}
    (tmp_path / "memory.max").write_text(str(1 << 30))
    assert supervision.memoire_conteneur(str(tmp_path)) == {"memoire_mo": 300, "memoire_max_mo": 1024}


def test_trace_link_opens_tempo_in_grafana(monkeypatch):
    monkeypatch.setenv("MARS_GRAFANA_URL", "http://localhost:3000/")
    lien = controle.lien_trace("4bf92f3577b34da6a3ce929d0e0e4736")
    assert lien.startswith("http://localhost:3000/explore?schemaVersion=1&orgId=1&panes=")
    assert "4bf92f3577b34da6a3ce929d0e0e4736" in lien and controle.lien_trace(None) is None


def test_system_route_services_failures_and_links(client, conn):
    conn.execute("INSERT INTO task_runs (task, run_day, status, details, trace_id, started_at) VALUES "
                 "('essai_systeme', current_date, 'echec', '{\"erreur\": \"disque plein\"}', 'abc', now()), "
                 "('essai_systeme', current_date, 'echec', '{\"erreur\": \"plafond\", \"refus\": true}', 'def', now())")
    try:
        s = client.get("/api/controle/systeme").json()
        assert any(x["service"] == "api" and x["age_s"] == 0 for x in s["services"])
        mine = [e for e in s["echecs"] if e["tache"] == "essai_systeme"]
        assert [e["erreur"] for e in mine] == ["disque plein"], "refus attendus exclus"
        assert mine[0]["lien_trace"].startswith(s["grafana"] + "/explore")
    finally:
        conn.execute("DELETE FROM task_runs WHERE task = 'essai_systeme'")


def test_rules_file_untouched():
    """Le panneau ne modifie jamais config/rules.yaml (une modification locale bloque git pull sur le serveur)."""
    src = Path(controle.__file__).read_text() + (ROOT / "backend" / "controle.py").read_text()
    assert "rules.yaml\", \"w" not in src and "write_text" not in src
