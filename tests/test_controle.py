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
    c.execute("DELETE FROM zones_vigilance")
    c.execute("DELETE FROM notifications_retenues")
    c.execute("DELETE FROM notifications_etat")
    c.execute("UPDATE interrupteurs_satellites SET en_pause = false, depuis = NULL, jusqu_a = NULL, motif = NULL, "
              "auteur = NULL, veille_notifiee_le = NULL")
    for sql in ("DELETE FROM journal_controle", "DELETE FROM reglages_controle", "DELETE FROM sessions_operateur",
                "DELETE FROM task_runs WHERE task IN ('viirs', 'sentinel', 'sentinel2')",
                "DELETE FROM demandes_origines", "DELETE FROM demandes_observation"):
        c.execute(sql)


ENVOYES: list = []                      # envois ntfy de l'API (controle.envoyer remplacé)


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
    monkeypatch.setattr(controle, "envoyer", lambda *a, **_k: ENVOYES.append(a) or 200)
    ENVOYES.clear()
    api._echecs.clear()

    async def codecs(c):                       # comme l'API (backend/app.py, _init) : JSON décodé
        import json as js
        for t in ("json", "jsonb"):
            await c.set_type_codec(t, encoder=js.dumps, decoder=js.loads, schema="pg_catalog")

    @asynccontextmanager
    async def vie(app):
        app.state.pool = await asyncpg.create_pool(URL, min_size=1, max_size=2, init=codecs)
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


# Phase C : modes des règles

def test_rule_of_an_alert_and_mode_priority():
    assert controle.regle_de("AIS_GAP", {}) == ("ais_gap", None)
    assert controle.regle_de("IDENTITY_CHANGE", {"changement": "omi_autre_mmsi"}) == ("identite", "omi")
    assert controle.regle_de("DARK_SHIP", {"source": "viirs"}) == ("viirs", None)
    assert controle.regle_de("AIS_UNCONFIRMED", {"source": "sentinel2"}) == ("sentinel2", "ais_unconfirmed")
    assert controle.mode_effectif(RULES, "ais_gap") == "observation"                 # ais_gap.mode du fichier
    assert controle.mode_effectif(RULES, "rendezvous") == "actif"
    r = controle.appliquer_reglages(RULES, {"mode:ais_gap": "actif", "mode:identite": "observation",
                                            "mode:identite.nom": "coupe", "mode:faux": "n'importe quoi"}, {})
    assert controle.mode_effectif(r, "ais_gap") == "actif", "le panneau prime sur le fichier"
    assert controle.mode_effectif(r, "identite", "nom") == "coupe" and controle.mode_effectif(r, "identite", "omi") == "observation"
    assert not controle.regle_coupee(r, "identite")
    r2 = controle.appliquer_reglages(RULES, {"mode:identite.nom": "coupe", "mode:identite.omi": "coupe"}, {})
    assert controle.regle_coupee(r2, "identite")
    assert r["_controle"]["empreinte"] != r2["_controle"]["empreinte"]
    assert controle.appliquer_reglages(RULES, {}, {})["_controle"]["empreinte"] == \
        controle.appliquer_reglages(RULES, {}, {"viirs": {}})["_controle"]["empreinte"], "les pauses n'influencent pas les alertes"
    with pytest.raises(ValueError):
        controle.verifier_mode(RULES, "identite", "inconnu", "actif")
    s = dict((k, v) for k, v in controle.seuils(RULES["identite"]))
    assert s["confirmation_min"] == 360 and s["militaire.mmsi"] == [232002833]      # à plat, sections imbriquées
    assert "militaire.prefixes_nom" not in s                                        # longue liste omise


def _alerte_item(cle, gravite="moyenne"):
    return {"key": cle, "severity": gravite, "event_time": datetime.now(timezone.utc), "lon": -6.1, "lat": 48.0,
            "details": {"motif": "essai", "debut": datetime.now(timezone.utc).isoformat()}, "vessels": []}


def test_save_alerts_observation_cut_and_active_with_fingerprint(conn):
    import asyncio
    import json as js
    import asyncpg
    from mars.rules import save_alerts
    conn.execute("DELETE FROM alerts WHERE rule_key LIKE 'essai_p:%'")
    conn.execute("DELETE FROM episodes_observes WHERE cle LIKE 'essai_p:%'")
    debut = datetime.now(timezone.utc) - timedelta(hours=1)
    # Alerte vierge existante, absente du cycle : elle serait retirée par une règle active
    conn.execute("""INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key)
                    VALUES ('RENDEZVOUS', 'moyenne', now(), ST_SetSRID(ST_MakePoint(-6, 48), 4326)::geography,
                            %s, 'x', 'essai_p:ancienne')""", (js.dumps({"debut": datetime.now(timezone.utc).isoformat()}),))

    async def go(regl):
        c = await asyncpg.connect(URL)
        for t in ("json", "jsonb"):
            await c.set_type_codec(t, encoder=js.dumps, decoder=js.loads, schema="pg_catalog")
        try:
            r = controle.appliquer_reglages(RULES, regl, {})
            return await save_alerts(c, "RENDEZVOUS", [_alerte_item("essai_p:1")], debut,
                                     datetime.now(timezone.utc) + timedelta(hours=1), "debut", "v", rules=r), r
        finally:
            await c.close()
    out, _r = asyncio.run(go({"mode:rendezvous": "observation"}))
    assert out == {"nouvelles": 0, "mises_a_jour": 0, "retirees": 0, "observees": 1, "coupees": 0}
    assert conn.execute("SELECT regle, gravite FROM episodes_observes WHERE cle = 'essai_p:1'").fetchone() == \
        ("rendezvous", "moyenne")
    out, _r = asyncio.run(go({"mode:rendezvous": "coupe"}))
    assert out["coupees"] == 1 and out["retirees"] == 0
    assert conn.execute("SELECT count(*) FROM alerts WHERE rule_key = 'essai_p:ancienne'").fetchone()[0] == 1, \
        "un changement de mode ne supprime aucune alerte"
    out, r = asyncio.run(go({}))
    assert out["nouvelles"] == 1
    emp = conn.execute("SELECT controle_empreinte FROM alerts WHERE rule_key = 'essai_p:1'").fetchone()[0]
    assert emp == r["_controle"]["empreinte"]
    assert conn.execute("SELECT etat FROM empreintes_controle WHERE empreinte = %s", (emp,)).fetchone()[0] == {"modes": {}}
    conn.execute("DELETE FROM alerts WHERE rule_key LIKE 'essai_p:%'")
    conn.execute("DELETE FROM episodes_observes WHERE cle LIKE 'essai_p:%'")


def test_sentinel_alert_in_observation_is_counted_not_written(conn):
    from mars import sentinel
    conn.execute("DELETE FROM episodes_observes WHERE cle LIKE 'essai_p:%'")
    r = controle.appliquer_reglages(RULES, {"mode:sentinel1.dark_ship": "observation"}, {})
    aid = sentinel._alert(conn, "DARK_SHIP", "elevee", datetime.now(timezone.utc), -6.1, 48.0,
                          {"source": "sentinel1"}, r, "essai_p:s1", [])
    assert aid is None and r["_controle"]["observees"] == {"sentinel1.dark_ship": 1}
    assert conn.execute("SELECT regle, sous_type, gravite FROM episodes_observes WHERE cle = 'essai_p:s1'").fetchone() == \
        ("sentinel1", "dark_ship", "elevee")
    conn.execute("DELETE FROM episodes_observes WHERE cle LIKE 'essai_p:%'")


def test_mode_route_cut_keeps_open_alerts_with_reason(client, conn):
    conn.execute("DELETE FROM alerts WHERE rule_key LIKE 'essai_p:%'")
    aid = conn.execute("""INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key)
                          VALUES ('WATCHLIST', 'elevee', now(), ST_SetSRID(ST_MakePoint(-6, 48), 4326)::geography,
                                  '{}', 'x', 'essai_p:w') RETURNING id""").fetchone()[0]
    client.post("/api/controle/connexion", json={"operateur": "antoine", "mot_de_passe": "bon mot de passe"})
    assert client.post("/api/controle/regles/inconnue", json={"mode": "coupe", "motif": "essai"}).status_code == 422
    r = client.post("/api/controle/regles/watchlist", json={"mode": "coupe", "motif": "listes en révision"})
    assert r.status_code == 200 and r.json()["alertes_ouvertes_annotees"] >= 1
    assert conn.execute("SELECT status FROM alerts WHERE id = %s", (aid,)).fetchone()[0] == "nouvelle"
    assert conn.execute("SELECT action, author, note FROM alert_actions WHERE alert_id = %s", (aid,)).fetchone() == \
        ("commenter", "antoine", "Règle « Navire des listes » coupée au panneau de contrôle par antoine : listes en révision")
    g = client.get("/api/controle/regles").json()
    w = [x for x in g["regles"] if x["regle"] == "watchlist"][0]
    assert w["mode"] == "coupe" and w["mode_panneau"] == "coupe" and w["alertes_7j"] >= 1 and w["seuils"]
    assert controle.mode_effectif(controle.appliquer(conn, RULES), "watchlist") == "coupe"
    j = conn.execute("SELECT action, cible, avant, apres, motif FROM journal_controle WHERE action = 'mode_regle'").fetchone()
    assert j[:2] == ("mode_regle", "watchlist") and j[2] == {"mode": None} and j[3]["mode"] == "coupe"
    conn.execute("DELETE FROM alerts WHERE rule_key LIKE 'essai_p:%'")


# Phase D : notifications

PARIS_NUIT = datetime(2026, 10, 21, 22, 30, tzinfo=timezone.utc)      # 00:30 à Paris
PARIS_JOUR = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)        # 10:00 à Paris
PARIS_HIVER = datetime(2026, 11, 3, 6, 30, tzinfo=timezone.utc)       # 07:30 à Paris (heure d'hiver)


def test_notification_decisions_and_quiet_hours():
    p = controle.preferences(RULES, None)
    assert p["heures_calmes"] == {"actif": True, "debut": "23:00", "fin": "07:00"}
    assert controle.en_heures_calmes(p, PARIS_NUIT) and not controle.en_heures_calmes(p, PARIS_JOUR)
    assert not controle.en_heures_calmes(p, PARIS_HIVER)
    d = controle.decision_notification
    assert d(p, "disque", "grave", PARIS_NUIT) == "envoyer", "une panne grave n'est jamais retenue"
    assert d(p, "pannes", "info", PARIS_NUIT) == "retenir" and d(p, "pannes", "info", PARIS_JOUR) == "envoyer"
    assert d(p, "alerte", "moyenne", PARIS_JOUR, "WATCHLIST") == "taire"                 # sous la gravité minimale
    assert d(p, "alerte", "elevee", PARIS_JOUR, "WATCHLIST") == "envoyer"
    assert d(p, "alerte", "critique", PARIS_JOUR, "AIS_UNCONFIRMED") == "taire"          # type coupé dans le fichier
    assert d(p, "alerte", "critique", PARIS_NUIT, "DARK_SHIP") == "retenir"
    p2 = controle.preferences(RULES, {"categories": {"pannes": True, "disque": False, "collecte": True,
                                                     "travailleurs": True}, "tip_and_cue": False,
                                      "heures_calmes": {"actif": True, "debut": "12:00", "fin": "14:00"}})
    assert d(p2, "disque", "grave", PARIS_JOUR) == "taire" and d(p2, "tip_and_cue", "elevee", PARIS_JOUR) == "taire"
    assert controle.en_heures_calmes(p2, datetime(2026, 10, 21, 11, 0, tzinfo=timezone.utc))    # 13:00 à Paris
    with pytest.raises(ValueError, match="heure"):
        controle.verifier_preferences(RULES, {**p, "heures_calmes": {"actif": True, "debut": "25:00", "fin": "07:00"}})
    with pytest.raises(ValueError, match="gravité"):
        controle.verifier_preferences(RULES, {**p, "alertes": {"gravite_min": "extreme"}})
    v = controle.verifier_preferences(RULES, {**p, "alertes": {"gravite_min": "moyenne", "types": {"INCONNU": True}}})
    assert "INCONNU" not in v["alertes"]["types"] and v["alertes"]["gravite_min"] == "moyenne"


def test_sql_quiet_hours_match_python(conn):
    controle.recopier_defauts(conn, RULES)
    for prefs in (None, {"heures_calmes": {"actif": True, "debut": "12:00", "fin": "14:00"}},
                  {"heures_calmes": {"actif": False, "debut": "23:00", "fin": "07:00"}},
                  {"categories": {"pannes": False}}):
        conn.execute("DELETE FROM reglages_controle WHERE cle = 'notif:preferences'")
        if prefs:
            conn.execute("INSERT INTO reglages_controle (cle, valeur) VALUES ('notif:preferences', %s)", (js_dumps(prefs),))
        p = controle.preferences(RULES, prefs)
        for t in (PARIS_NUIT, PARIS_JOUR, PARIS_HIVER, datetime(2026, 10, 21, 11, 0, tzinfo=timezone.utc)):
            assert conn.execute("SELECT heures_calmes(%s)", (t,)).fetchone()[0] == controle.en_heures_calmes(p, t), (prefs, t)
    assert conn.execute("SELECT notification_permise('pannes', true), notification_permise('disque', true)"
                        ).fetchone() == (False, True)


def js_dumps(v):
    import json
    return json.dumps(v)


def test_held_at_night_then_wake_up_summary(conn, monkeypatch):
    envoyes = []
    monkeypatch.setattr(controle, "envoyer", lambda *a, **_k: envoyes.append(a))
    assert controle.notifier("pannes", "info", "MARS C2 : essai de nuit", "x", conn=conn, now=PARIS_NUIT) == "retenir"
    assert controle.notifier("disque", "grave", "MARS C2 : disque", "x", conn=conn, now=PARIS_NUIT) == "envoyer"
    assert [e[1] for e in envoyes] == ["MARS C2 : disque"]
    assert controle.resume_du_reveil(conn, RULES, PARIS_NUIT) == {"retenues": None}            # encore la nuit
    assert controle.resume_du_reveil(conn, RULES, PARIS_JOUR) == {"retenues": 1}
    assert envoyes[-1][1] == "MARS C2 : 1 notification(s) pendant les heures calmes" and "essai de nuit" in envoyes[-1][2]
    assert controle.resume_du_reveil(conn, RULES, PARIS_JOUR) == {"retenues": 0}


def test_new_alerts_notified_by_severity_and_type(conn):
    vus = []

    def envoi(categorie, gravite, _titre, _message, **kw):
        vus.append((gravite, kw.get("type_alerte"), controle.decision_notification(
            controle.preferences(RULES, None), categorie, gravite, kw["now"], kw.get("type_alerte"))))
    conn.execute("DELETE FROM alerts WHERE rule_key LIKE 'essai_n:%'")
    assert controle.notifier_alertes(conn, RULES, PARIS_JOUR, envoi)["initialise"] >= 0      # premier passage : rien
    for k, (typ, sev) in enumerate((("WATCHLIST", "elevee"), ("RENDEZVOUS", "moyenne"), ("AIS_UNCONFIRMED", "critique"))):
        conn.execute("""INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key)
                        VALUES (%s, %s, now(), ST_SetSRID(ST_MakePoint(-6, 48), 4326)::geography, '{}', 'x', %s)""",
                     (typ, sev, f"essai_n:{k}"))
    out = controle.notifier_alertes(conn, RULES, PARIS_JOUR, envoi)
    assert out == {"nouvelles": 3, "notifiees": 1} and vus == [("elevee", "WATCHLIST", "envoyer")]
    assert controle.notifier_alertes(conn, RULES, PARIS_JOUR, envoi) == {"nouvelles": 0, "notifiees": 0}
    conn.execute("DELETE FROM alerts WHERE rule_key LIKE 'essai_n:%'")


def test_notification_routes(client, conn, monkeypatch):
    client.post("/api/controle/connexion", json={"operateur": "antoine", "mot_de_passe": "bon mot de passe"})
    g = client.get("/api/controle/notifications").json()
    assert g["par_le_panneau"] is False and g["preferences"]["alertes"]["gravite_min"] == "elevee"
    p = g["preferences"]
    p["alertes"]["gravite_min"] = "moyenne"
    p["heures_calmes"]["debut"] = "22:00"
    assert client.post("/api/controle/notifications", json={"preferences": {**p, "heures_calmes": {"debut": "x"}}}
                       ).status_code == 422
    assert client.post("/api/controle/notifications", json={"preferences": p}).status_code == 200
    g = client.get("/api/controle/notifications").json()
    assert g["par_le_panneau"] and g["preferences"]["heures_calmes"]["debut"] == "22:00"
    assert conn.execute("SELECT heures_calmes(%s)", (datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc),)
                        ).fetchone()[0], "Grafana lit les préférences du panneau (22:30 à Paris)"
    monkeypatch.delenv("NTFY_SUJET", raising=False)
    assert client.post("/api/controle/notifications/test").status_code == 503
    monkeypatch.setenv("NTFY_SUJET", "sujet_d_essai")
    assert client.post("/api/controle/notifications/test").json() == {"envoyee": True, "http": 200}
    assert ENVOYES[-1][1] == "MARS C2 : notification de test"
    actions = [x["action"] for x in client.get("/api/controle/journal").json()]
    assert "notifications" in actions and "notification_test" in actions


def test_grafana_rules_read_pauses_and_preferences_under_their_role(conn):
    """Chaque règle de Grafana passe par notification_permise ; la nuit VIIRS absente se tait en pause ; toutes les
    requêtes s'exécutent sous le rôle grafana_lecture (droits des migrations 33 à 35)."""
    import yaml
    doc = yaml.safe_load((ROOT / "grafana" / "provisioning" / "alerting" / "regles.yaml").read_text())
    regles = {r["uid"]: r for g in doc["groups"] for r in g["rules"]}
    b = {uid: [x for x in r["data"] if x["refId"] == "B"][0]["model"]["rawSql"] for uid, r in regles.items()}
    assert all("notification_permise(" in sql for sql in b.values())
    assert "notification_permise('disque', true)" in b["mars-disque"] and "source_en_pause('viirs')" in b["mars-viirs"]
    controle.basculer(conn, "viirs", True, "antoine", "essai", envoi=Envois())
    assert conn.execute("SELECT source_en_pause('viirs'), source_en_pause('sentinel1')").fetchone() == (True, False)
    for uid, r in regles.items():
        for x in r["data"]:
            if x["model"].get("rawSql"):
                with conn.transaction(force_rollback=True):
                    conn.execute("SET LOCAL ROLE grafana_lecture")
                    rows = conn.execute(x["model"]["rawSql"]).fetchall()
                    if uid == "mars-viirs" and x["refId"] == "B":
                        assert rows == [(0,)], "nuit VIIRS absente muette en pause"


# Phase E : zones de vigilance

CARRE = {"type": "Polygon", "coordinates": [[[-6.5, 47.8], [-5.5, 47.8], [-5.5, 48.4], [-6.5, 48.4], [-6.5, 47.8]]]}
TROU = {"type": "Polygon", "coordinates": [CARRE["coordinates"][0],
                                           [[-6.2, 48.0], [-5.8, 48.0], [-5.8, 48.2], [-6.2, 48.2], [-6.2, 48.0]]]}


def _zones(*profils):
    return [{"id": k + 1, "nom": f"zone {k + 1}", "profil": p, "geometrie": CARRE} for k, p in enumerate(profils)]


def test_zone_geometry_thresholds_severity_and_fingerprint():
    assert controle.dans_geometrie(CARRE, -6.0, 48.1) and not controle.dans_geometrie(CARRE, -4.0, 48.1)
    assert not controle.dans_geometrie(TROU, -6.0, 48.1) and controle.dans_geometrie(TROU, -6.4, 48.3)
    assert controle.dans_geometrie({"type": "MultiPolygon", "coordinates": [CARRE["coordinates"]]}, -6.0, 48.1)
    r = controle.appliquer_reglages(RULES, {}, {}, _zones("renforce", "inconnu"))
    assert [z["profil"] for z in r["_controle"]["zones"]] == ["renforce"], "profil absent du fichier : zone ignorée"
    assert controle.seuil_min(r, "rendezvous.min_duration_min", 120) == 60
    assert controle.seuil_zone(r, "rendezvous.min_duration_min", 120, -6.0, 48.1) == 60
    assert controle.seuil_zone(r, "rendezvous.min_duration_min", 120, -4.0, 48.1) == 120
    assert controle.seuil_zone(r, "rendezvous.min_duration_min", 45, -6.0, 48.1) == 45, "une zone n'élève jamais un seuil"
    sans = controle.appliquer_reglages(RULES, {}, {})
    assert controle.seuil_min(sans, "ais_gap.min_gap_min", 120) == 120
    d = {}
    assert controle.marquer_zone(r, "faible", d, -6.0, 48.1) == "moyenne"
    assert d["zone_vigilance"] == {"id": 1, "nom": "zone 1", "profil": "renforce", "profil_nom": "Renforcé", "autres": [],
                                   "gravite_avant": "faible"}
    assert controle.marquer_zone(r, "elevee", {}, -6.0, 48.1) == "elevee"
    assert controle.marquer_zone(r, "faible", {}, -4.0, 48.1) == "faible"
    assert controle.priorite_zone(r, -6.0, 48.1) == 2 and controle.priorite_zone(r, -6.0, 48.1, "priorite_sentinel") == 5
    assert r["_controle"]["empreinte"] != sans["_controle"]["empreinte"]
    assert sans["_controle"]["empreinte"] == controle.empreinte({"modes": {}}), "sans zone, empreinte de la phase C"
    from mars import tip_and_cue as tcm
    assert tcm.priorite([{"motif": "corridor_liste", "zone_vigilance": 1, "priorite": 5}]) == 5


def test_zone_routes_alert_marking_and_withdrawal(client, conn):
    import asyncio
    import json as js
    import asyncpg
    from mars.rules import save_alerts
    client.post("/api/controle/connexion", json={"operateur": "antoine", "mot_de_passe": "bon mot de passe"})
    base = {"nom": "Rail d'Ouessant", "motif": "navires des listes signalés", "profil": "renforce"}
    assert client.post("/api/controle/zones", json={**base, "profil": "inconnu", "geometrie": CARRE}).status_code == 422
    croise = {"type": "Polygon", "coordinates": [[[-6, 48], [-5, 49], [-5, 48], [-6, 49], [-6, 48]]]}
    assert client.post("/api/controle/zones", json={**base, "geometrie": croise}).status_code == 422
    assert client.post("/api/controle/zones", json=base).status_code == 422                 # ni polygone ni infrastructure
    zid = client.post("/api/controle/zones", json={**base, "geometrie": CARRE}).json()["id"]
    iid = conn.execute("INSERT INTO infrastructure (region_id, kind, name, geom) SELECT min(id), 'cable', 'essai', "
                       "ST_GeogFromText('LINESTRING(-4 47, -4 48)') FROM regions RETURNING id").fetchone()[0]
    try:
        z2 = client.post("/api/controle/zones", json={**base, "nom": "Câble", "profil": "attention",
                                                      "infrastructure_id": iid, "tampon_m": 3000}).json()["id"]
        g = client.get("/api/controle/zones").json()
        assert {f["properties"]["id"] for f in g["features"]} == {zid, z2} and "renforce" in g["profils"]
        r = controle.appliquer(conn, RULES)
        assert [z["id"] for z in r["_controle"]["zones"]] == [zid, z2] and controle.zones_de(r, -4.01, 47.5)
        conn.execute("DELETE FROM alerts WHERE rule_key LIKE 'essai_z:%'")

        async def go():
            c = await asyncpg.connect(URL)
            for t in ("json", "jsonb"):
                await c.set_type_codec(t, encoder=js.dumps, decoder=js.loads, schema="pg_catalog")
            try:
                return await save_alerts(c, "WATCHLIST", [{**_alerte_item("essai_z:1", "faible"), "lon": -6.0, "lat": 48.1}],
                                         rules=r)
            finally:
                await c.close()
        asyncio.run(go())
        sev, det, emp = conn.execute("SELECT severity, details, controle_empreinte FROM alerts WHERE rule_key = 'essai_z:1'"
                                     ).fetchone()
        assert sev == "moyenne" and det["zone_vigilance"]["id"] == zid and det["zone_vigilance"]["gravite_avant"] == "faible"
        assert conn.execute("SELECT etat FROM empreintes_controle WHERE empreinte = %s", (emp,)).fetchone()[0]["zones"] == \
            [[zid, "renforce"], [z2, "attention"]]
        f = client.get(f"/api/controle/zones/{zid}").json()
        assert [a["severity"] for a in f["alertes"]] == ["moyenne"] and f["km2"] > 1000
        assert client.post(f"/api/controle/zones/{zid}/retirer", json={"motif": "fin de l'opération"}).status_code == 200
        assert [z["id"] for z in controle.appliquer(conn, RULES)["_controle"]["zones"]] == [z2]
        assert conn.execute("SELECT details->'zone_vigilance'->>'id' FROM alerts WHERE rule_key = 'essai_z:1'"
                            ).fetchone()[0] == str(zid), "l'alerte garde la zone appliquée"
        actions = [x["action"] for x in client.get("/api/controle/journal").json()]
        assert actions.count("zone_creee") == 2 and "zone_retiree" in actions
    finally:
        conn.execute("DELETE FROM alerts WHERE rule_key LIKE 'essai_z:%'")
        conn.execute("DELETE FROM zones_vigilance")
        conn.execute("DELETE FROM infrastructure WHERE id = %s", (iid,))


def test_tip_priority_raised_in_zone_and_for_followed_vessels(conn):
    rules = controle.appliquer_reglages({**RULES, "tip_and_cue": {**RULES["tip_and_cue"], "actif": True}},
                                        {"suivis:prioritaires": True}, {}, _zones("renforce"))
    base = int(RULES["tip_and_cue"]["tips"]["viirs"]["priorite"])
    d1 = tc.creer(conn, rules, NOW, cle="essai_z:viirs", tip="viirs", lon=-6.0, lat=48.1, position_le=NOW)
    assert conn.execute("SELECT priorite FROM demandes_observation WHERE id = %s", (d1["demande"],)).fetchone()[0] == base + 2
    vid = conn.execute("SELECT id FROM vessels LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO followed_vessels (vessel_id) VALUES (%s) ON CONFLICT DO NOTHING", (vid,))
    try:
        d2 = tc.creer(conn, rules, NOW, cle="essai_z:suivi", tip="identite", lon=-4.0, lat=47.0, position_le=NOW,
                      vessel_id=vid)
        prio = int(RULES["tip_and_cue"]["tips"]["identite"]["priorite"]) + RULES["controle"]["suivis"]["priorite_plus"]
        assert conn.execute("SELECT priorite FROM demandes_observation WHERE id = %s", (d2["demande"],)).fetchone()[0] == prio
    finally:
        conn.execute("DELETE FROM followed_vessels WHERE vessel_id = %s", (vid,))


def test_rules_file_untouched():
    """Le panneau ne modifie jamais config/rules.yaml (une modification locale bloque git pull sur le serveur)."""
    src = Path(controle.__file__).read_text() + (ROOT / "backend" / "controle.py").read_text()
    assert "rules.yaml\", \"w" not in src and "write_text" not in src
