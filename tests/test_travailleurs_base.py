"""Travailleurs sur une vraie base PostgreSQL (migrations de db/init appliquées) : verrou contre les lancements
concurrents, échec de lancement, délai de démarrage, résultats reçus deux fois. Ignorés sans MARS_TEST_DATABASE_URL,
par exemple : MARS_TEST_DATABASE_URL=postgresql://mars:mars@localhost:55432/mars (base de test, docs/deploiement.md)."""
import base64
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from mars import travailleurs as T
from mars.config import load_rules
from mars.viirs import ingest

URL = os.environ.get("MARS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="MARS_TEST_DATABASE_URL absente : base de test requise")
RULES = load_rules()


class FakeScw(T.Scaleway):
    """Faux client au niveau des méthodes : instances en mémoire, rattachement réussi ou en échec."""

    def __init__(self, nic_ok=True, delay=0.0):
        self.project, self.protected, self.servers, self.destroyed = "p", set(), {}, []
        self.nic_ok, self.delay, self.n = nic_ok, delay, 0

    def resolve_image(self, *_args):
        return {"id": "img", "name": "Ubuntu 24.04", "arch": "x86_64", "root_type": "l_ssd", "root_size": 10_000_000_000}

    def create(self, _zone, name, _ct, _image, _disque, tags):
        time.sleep(self.delay)
        self.n += 1
        sid = f"srv-{name}"
        self.servers[sid] = {"id": sid, "name": name, "tags": tags, "project": "p", "state": "stopped",
                             "image": {"id": "img"}, "boot_type": "local",
                             "volumes": {"0": {"volume_type": "l_ssd", "size": 20_000_000_000}}}
        return dict(self.servers[sid])

    def attach_private_network(self, *_args, **_kw):
        if not self.nic_ok:
            raise T.ScalewayError("rattachement au réseau privé refusé (403)")
        return {"id": "nic", "mac_address": "02:00:00:aa:bb:cc", "state": "available"}

    def set_cloud_init(self, _zone, sid, text):
        self.servers[sid]["cloud"] = text

    def start(self, _zone, sid, **_kw):
        self.servers[sid]["state"] = "running"

    def server(self, _zone, sid):
        return self.servers.get(sid)

    def tagged(self, _zone):
        return [s for s in self.servers.values() if T.refus(s, self.project, self.protected) is None]

    def destroy(self, _zone, sid):
        s = self.servers.get(sid)
        if s and T.refus(s, self.project, self.protected):
            raise T.RefusDestruction("pas un travailleur")
        self.servers.pop(sid, None)
        self.destroyed.append(sid)
        return True


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setenv("MARS_IP_PRIVEE", "172.16.8.2")
    monkeypatch.setenv("SCW_PRIVATE_NETWORK_ID", "pn")
    c = psycopg.connect(URL, autocommit=True)
    for t in ("viirs_detections", "viirs_granules", "travailleurs"):
        c.execute(f"DELETE FROM {t}")
    c.execute("DELETE FROM task_runs WHERE task LIKE 'travailleur%'")
    yield c
    c.close()


def test_concurrent_launches_create_a_single_worker(conn):
    """Rattrapage automatique et commande manuelle au même instant : un seul travailleur, l'autre voit « occupe »."""
    scw, results, barrier = FakeScw(delay=0.3), [], threading.Barrier(2)

    def go():
        c = psycopg.connect(URL, autocommit=True)
        barrier.wait()
        results.append(T.launch(c, scw, RULES, "viirs", {"granules": [{"nom": "SNPP.A2026280.0018"}]}, {},
                                sleep=lambda _s: None))
        c.close()
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(k for r in results for k in r if k in ("travailleur", "occupe")) == ["occupe", "travailleur"]
    assert scw.n == 1
    assert conn.execute("SELECT count(*) FROM travailleurs WHERE detruit_le IS NULL").fetchone()[0] == 1


def test_failed_launch_is_an_error_and_destroys_the_instance_at_once(conn):
    scw = FakeScw(nic_ok=False)
    with pytest.raises(T.ScalewayError, match="réseau privé"):
        T.launch(conn, scw, RULES, "viirs", {"granules": []}, {}, sleep=lambda _s: None)
    assert scw.destroyed and not scw.servers                   # détruite aussitôt, par le garde
    etat, erreur, detruit = conn.execute("SELECT etat, erreur, detruit_le IS NOT NULL FROM travailleurs").fetchone()
    assert etat == "echec" and "réseau privé" in erreur and detruit
    status, details = conn.execute("SELECT status, details FROM task_runs WHERE task = 'travailleur_viirs'").fetchone()
    assert status == "echec" and "réseau privé" in details["erreur"]
    # Le verrou est libéré : un nouveau lancement est possible
    assert "travailleur" in T.launch(conn, FakeScw(), RULES, "viirs", {"granules": []}, {}, sleep=lambda _s: None)


def test_silent_worker_is_destroyed_after_the_start_delay(conn):
    scw = FakeScw()
    wid = T.launch(conn, scw, RULES, "viirs", {"granules": []}, {}, sleep=lambda _s: None)["travailleur"]
    later = datetime.now(timezone.utc) + timedelta(minutes=RULES["travailleurs"]["delai_demarrage_min"] + 1)
    out = T.supervise(conn, scw, RULES, {}, log=lambda _m: None, now=later)
    assert out["expires"] == 1 and not scw.servers
    etat, erreur = conn.execute("SELECT etat, erreur FROM travailleurs WHERE id = %s", (wid,)).fetchone()
    assert etat == "echec" and "aucun signe de vie" in erreur


def test_worker_that_reached_the_server_is_not_destroyed_by_the_start_delay(conn):
    scw = FakeScw()
    wid = T.launch(conn, scw, RULES, "viirs", {"granules": []}, {}, sleep=lambda _s: None)["travailleur"]
    conn.execute("UPDATE travailleurs SET etat = 'demarre', demarre_le = now() WHERE id = %s", (wid,))
    later = datetime.now(timezone.utc) + timedelta(minutes=RULES["travailleurs"]["delai_demarrage_min"] + 1)
    assert T.supervise(conn, scw, RULES, {}, log=lambda _m: None, now=later)["expires"] == 0 and scw.servers


def _result(name, start):
    frame = [[-10, 52], [10, 52], [10, 40], [-10, 40], [-10, 52]]
    dets = [{"lon": -6.5 + k * 0.1, "lat": 48.5, "nanowatts": 50.0} for k in range(3)]
    return {"granules": [{"dnb": name, "debut": start.isoformat(), "fin": (start + timedelta(minutes=6)).isoformat(),
                          "emprise": frame, "statut": ["processed"], "detections": dets}], "mesures": {}}


def test_same_granule_received_twice_never_duplicates_detections(conn):
    start = datetime(2026, 10, 7, 0, 58, tzinfo=timezone.utc)
    g = {"nom": "NOAA20.A2026280.0058", "satellite": "NOAA20", "dnb": "VJ102DNB_NRT.A2026280.0058.nc",
         "debut": start.isoformat(), "nuit": "2026-10-06"}
    for _ in range(2):                                          # deux travailleurs, même granule
        wid = conn.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, etat, resultat) "
                           "VALUES ('viirs', 'DEV1-M', 'fr-par-1', 'x', %s, 'resultats', %s) RETURNING id",
                           (json.dumps({"granules": [g]}), json.dumps(_result(g["dnb"], start)))).fetchone()[0]
        T.supervise(conn, None, RULES, {"viirs": lambda c, w: ingest(c, w, RULES)}, log=lambda _m: None)
        assert conn.execute("SELECT etat FROM travailleurs WHERE id = %s", (wid,)).fetchone()[0] == "termine"
    assert conn.execute("SELECT count(*) FROM viirs_detections").fetchone()[0] == 3
    assert conn.execute("SELECT detections FROM viirs_granules").fetchone()[0] == 3
    with pytest.raises(psycopg.errors.UniqueViolation):          # l'index unique le garantit en base
        conn.execute("INSERT INTO viirs_detections (granule_id, ts, geom) SELECT granule_id, ts, geom FROM viirs_detections LIMIT 1")


def test_worker_stopped_by_inaccessible_data_is_a_failure_with_its_reason(conn):
    """Essai réel du 08/10 : licence LANCE non acceptée. Le travailleur s'arrête au premier granule ; l'exécution est
    en échec avec ce motif (travailleurs, task_runs, barre d'état), la granule est consignée en échec."""
    start = datetime(2026, 10, 7, 0, 58, tzinfo=timezone.utc)
    g = {"nom": "NOAA20.A2026280.0058", "satellite": "NOAA20", "dnb": "VJ102DNB_NRT.A2026280.0058.nc",
         "debut": start.isoformat(), "nuit": "2026-10-06"}
    motif = "licence LANCE non acceptée sur le compte Earthdata (redirection vers /profiles/licenses)"
    res = {"granules": [{"dnb": g["dnb"], "erreur": motif}], "erreur": motif, "mesures": {"duree_s": 4.0}}
    wid = conn.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, etat, resultat) "
                       "VALUES ('viirs', 'DEV1-M', 'fr-par-1', 'x', %s, 'resultats', %s) RETURNING id",
                       (json.dumps({"granules": [g]}), json.dumps(res))).fetchone()[0]
    T.supervise(conn, None, RULES, {"viirs": lambda c, w: ingest(c, w, RULES)}, log=lambda _m: None)
    etat, erreur, detruit = conn.execute("SELECT etat, erreur, detruit_le IS NOT NULL FROM travailleurs WHERE id = %s",
                                         (wid,)).fetchone()
    assert etat == "echec" and erreur == f"travailleur : {motif}" and detruit
    status, details = conn.execute("SELECT status, details FROM task_runs WHERE task = 'travailleur_viirs'").fetchone()
    assert status == "echec" and "licence LANCE" in details["erreur"]
    assert conn.execute("SELECT erreur FROM viirs_granules").fetchone()[0] == motif


def test_quota_refusal_shows_only_its_reason(conn, capfd):
    import sys
    sys.path.insert(0, "scripts")
    import taches

    def refused():
        raise T.RefusLancement("plafond de 4 travailleurs par jour atteint")
    assert taches.run(conn, "viirs_essai", refused) is None
    out, err = capfd.readouterr()
    assert "ÉCHEC, plafond de 4 travailleurs par jour atteint" in out and "Traceback" not in out + err
    status, details = conn.execute("SELECT status, details FROM task_runs WHERE task = 'viirs_essai'").fetchone()
    assert status == "echec" and details == {"erreur": "plafond de 4 travailleurs par jour atteint"}
    conn.execute("DELETE FROM task_runs WHERE task = 'viirs_essai'")


def test_silent_worker_state_is_recorded_before_destruction(conn):
    scw = FakeScw()
    scw.diagnose = lambda _zone, _sid: {"etat": "running", "image": "Ubuntu 24.04", "cartes_privees": [{"adresses": ["172.16.8.6/22"]}]}
    wid = T.launch(conn, scw, RULES, "viirs", {"granules": []}, {}, sleep=lambda _s: None)["travailleur"]
    later = datetime.now(timezone.utc) + timedelta(minutes=RULES["travailleurs"]["delai_demarrage_min"] + 1)
    T.supervise(conn, scw, RULES, {}, log=lambda _m: None, now=later)
    diag = conn.execute("SELECT diagnostic FROM travailleurs WHERE id = %s", (wid,)).fetchone()[0]
    assert diag["etat"] == "running" and diag["cartes_privees"][0]["adresses"] == ["172.16.8.6/22"] and not scw.servers


def test_reevaluation_of_existing_viirs_alerts(conn):
    """Les règles nouvelles appliquées aux détections en base : très au large avec AIS alentour, gravité moyenne ;
    près des côtes, faible et à confirmer ; sans aucune réception AIS ou pendant une coupure du flux, non évaluable
    (alerte vierge retirée, alerte portant une décision gardée en faible avec le motif)."""
    from mars.viirs import reevaluate
    for sql in ("DELETE FROM alert_actions", "DELETE FROM alert_evidence WHERE evidence_type IN ('viirs', 'vessel')",
                "DELETE FROM alerts WHERE type = 'DARK_SHIP'", "DELETE FROM land WHERE source = 'essai'",
                "DELETE FROM positions WHERE vessel_id >= 990000", "DELETE FROM vessels WHERE id >= 990000",
                "DELETE FROM stats_minute", "DELETE FROM reception_cells"):
        conn.execute(sql)
    conn.execute("INSERT INTO land (source, geom) VALUES ('essai', ST_GeomFromText('POLYGON((-4.75 48.0,-4.2 48.65,"
                 "-3.0 48.85,-1.5 48.7,-1.5 47.4,-2.5 47.3,-4.4 47.8,-4.75 48.0))', 4326)::geography)")
    t = datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc)
    for vid, lon, lat in ((990001, -6.2, 48.0), (990002, -5.0, 48.2)):          # deux navires AIS immobiles
        conn.execute("INSERT INTO vessels (id, mmsi, name) VALUES (%s, %s, 'AIS')", (vid, vid))
        for k in range(-60, 61, 2):
            conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn) VALUES (%s, %s, "
                         "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 0)", (vid, t + timedelta(minutes=k), lon, lat))
    t_cut = t + timedelta(minutes=20)
    for k in range(-240, 120):                                                    # flux normal, coupé vers t + 20 min
        m = t + timedelta(minutes=k)
        if not t_cut - timedelta(minutes=2) <= m <= t_cut + timedelta(minutes=2):
            conn.execute("INSERT INTO stats_minute (minute, positions) VALUES (%s, 600)", (m,))
    gid = conn.execute("INSERT INTO viirs_granules (nom, satellite, nuit, debut, fin) VALUES ('NOAA20.A2026281.0100', "
                       "'NOAA20', '2026-10-07', %s, %s) RETURNING id", (t, t + timedelta(minutes=30))).fetchone()[0]
    cases = {"large": (-6.0, 48.0, t), "cote": (-4.85, 48.05, t), "sans_reception": (-6.5, 47.5, t),
             "coupure": (-6.05, 48.05, t_cut), "decision": (-6.6, 47.4, t)}
    ids, alerts = {}, {}
    for name, (lon, lat, ts) in cases.items():
        ids[name] = conn.execute("INSERT INTO viirs_detections (granule_id, ts, geom, nanowatts) VALUES (%s, %s, "
                                 "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 20) RETURNING id", (gid, ts, lon, lat)).fetchone()[0]
        conn.execute("UPDATE viirs_detections SET distance_cote_m = (SELECT min(ST_Distance(l.geom, d.geom)) FROM land l) "
                     "FROM viirs_detections d WHERE viirs_detections.id = d.id AND d.id = %s", (ids[name],))
        # Alerte de l'ancienne règle (gravité moyenne), comme les 87 alertes du serveur
        alerts[name] = conn.execute(
            "INSERT INTO alerts (type, severity, event_time, geom, details, rule_version, rule_key) VALUES "
            "('DARK_SHIP', 'moyenne', %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, '2026.10.19', %s) RETURNING id",
            (ts, lon, lat, json.dumps({"source": "viirs"}), f"viirs:NOAA20.A2026281.0100:{lon:.4f}:{lat:.4f}")).fetchone()[0]
        conn.execute("INSERT INTO alert_evidence VALUES (%s, 'viirs', %s)", (alerts[name], ids[name]))
    conn.execute("INSERT INTO alert_actions (alert_id, action, note, author) VALUES (%s, 'acquitter', 'vu', 'essai')",
                 (alerts["decision"],))
    out = reevaluate(conn, RULES)
    assert out["non_evaluables"] == 3 and out["alertes_retirees"] == 2 and out["alertes_gardees_avec_decision"] == 1
    det = {n: conn.execute("SELECT mask_reason, non_evaluable, ais_navires_rayon FROM viirs_detections WHERE id = %s",
                           (i,)).fetchone() for n, i in ids.items()}
    assert det["sans_reception"][0] == "non_evaluable" and "réception AIS absente" in det["sans_reception"][1]
    assert det["coupure"][0] == "non_evaluable" and "coupure du flux" in det["coupure"][1]
    assert det["large"][0] is None and det["large"][2] >= 1
    sev = {n: conn.execute("SELECT severity, details->>'a_confirmer', status FROM alerts WHERE id = %s", (a,)).fetchone()
           for n, a in alerts.items()}
    assert sev["large"][:2] == ("moyenne", "false") and sev["cote"][:2] == ("faible", "true")
    assert sev["sans_reception"] is None and sev["coupure"] is None                 # alertes vierges retirées
    assert sev["decision"][0] == "faible"
    assert conn.execute("SELECT details->>'non_evaluable' FROM alerts WHERE id = %s", (alerts["decision"],)).fetchone()[0]


class FakeR2:
    """Faux seau R2 : objets en mémoire (ni disque, ni réseau)."""

    def __init__(self):
        self.objects = {}

    def put_verified(self, data, key):
        self.objects[key] = data
        return {"cle": key, "octets": len(data)}

    def delete(self, key):
        self.objects.pop(key, None)


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNgAAAAAgABc3UBGAAAAABJRU5ErkJggg==")


def _worker_with_images(conn, nom="NOAA20.A2026282.0100"):
    start = datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc)
    g = {"nom": nom, "satellite": "NOAA20", "dnb": f"VJ102DNB_NRT.{nom[7:]}.nc", "debut": start.isoformat(), "nuit": "2026-10-08"}
    geo = {"x": [100.0, 0.0, 700.0], "y": [0.0, -100.0, 4900.0], "largeur": 240, "hauteur": 240, "m_par_px": 124.0}
    dets = [{"lon": -6.5 + k * 0.1, "lat": 48.5, "nanowatts": 40.0, "vignette": base64.b64encode(PNG).decode(),
             "vignette_geo": geo} for k in range(2)]
    res = {"granules": [{"dnb": g["dnb"], "debut": start.isoformat(), "fin": (start + timedelta(minutes=6)).isoformat(),
                         "emprise": [[-10, 52], [10, 52], [10, 40], [-10, 40], [-10, 52]], "statut": ["processed"],
                         "detections": dets}], "mesures": {}}
    return conn.execute("INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, parametres, etat, resultat) "
                        "VALUES ('viirs', 'DEV1-M', 'fr-par-1', 'x', %s, 'resultats', %s) RETURNING id",
                        (json.dumps({"granules": [g]}), json.dumps(res))).fetchone()[0]


def test_vignettes_go_to_r2_and_the_registry_never_to_disk(conn, monkeypatch):
    import mars.r2
    r2 = FakeR2()
    monkeypatch.setattr(mars.r2, "R2", lambda: r2)
    conn.execute("DELETE FROM preuves_images")
    wid = _worker_with_images(conn)
    T.supervise(conn, None, RULES, {"viirs": lambda c, w: ingest(c, w, RULES)}, log=lambda _m: None)
    mesures = conn.execute("SELECT mesures FROM travailleurs WHERE id = %s", (wid,)).fetchone()[0]
    assert mesures["vignettes"] == 2 and mesures["vignettes_octets"] == 2 * len(PNG)
    rows = conn.execute("SELECT p.cle, p.octets, p.geo, p.objet_id = d.id FROM preuves_images p "
                        "JOIN viirs_detections d ON d.id = p.objet_id ORDER BY p.id").fetchall()
    assert len(rows) == 2 and all(r[3] for r in rows) and rows[0][2]["largeur"] == 240
    assert rows[0][0].startswith("vignettes/viirs/2026-10-08/NOAA20.A2026282.0100/") and rows[0][0] in r2.objects
    assert conn.execute("SELECT resultat FROM travailleurs WHERE id = %s", (wid,)).fetchone()[0] is None  # base64 non gardé


def test_without_r2_detections_are_kept_without_images(conn, monkeypatch):
    import mars.r2

    def absent():
        raise RuntimeError("Identifiants R2 absents du .env : R2_BUCKET")
    monkeypatch.setattr(mars.r2, "R2", absent)
    conn.execute("DELETE FROM preuves_images")
    wid = _worker_with_images(conn, "NOAA20.A2026282.0106")
    T.supervise(conn, None, RULES, {"viirs": lambda c, w: ingest(c, w, RULES)}, log=lambda _m: None)
    m = conn.execute("SELECT etat, mesures FROM travailleurs WHERE id = %s", (wid,)).fetchone()
    assert m[0] == "termine" and m[1]["vignettes_non_rangees"] == 2 and "R2" in m[1]["vignettes_motif"]
    assert conn.execute("SELECT count(*) FROM viirs_detections").fetchone()[0] == 2


def test_proofs_are_kept_forever_with_an_alert_and_30_days_otherwise(conn):
    from mars import preuves
    r2 = FakeR2()
    conn.execute("DELETE FROM preuves_images")
    conn.execute("DELETE FROM alert_evidence WHERE evidence_type = 'viirs'")
    gid = conn.execute("INSERT INTO viirs_granules (nom, satellite, nuit, debut, fin) VALUES ('NOAA20.A2026200.0100', "
                       "'NOAA20', '2026-07-18', now(), now()) RETURNING id").fetchone()[0]
    old = datetime.now(timezone.utc) - timedelta(days=40)
    ids = {}
    for name, when in (("ancienne_alerte", old), ("ancienne", old), ("recente", datetime.now(timezone.utc))):
        did = conn.execute("INSERT INTO viirs_detections (granule_id, ts, geom) VALUES (%s, %s, "
                           "ST_SetSRID(ST_MakePoint(%s, 48), 4326)::geography) RETURNING id",
                           (gid, when, -6 - len(ids) * 0.1)).fetchone()[0]
        ids[name] = preuves.store(conn, r2, "viirs", did, PNG, None, when, preuves.key("viirs", "nuit", "granule", did))
        if name == "ancienne_alerte":
            aid = conn.execute("INSERT INTO alerts (type, severity, event_time, geom, rule_version) VALUES "
                               "('DARK_SHIP', 'faible', %s, ST_SetSRID(ST_MakePoint(-6, 48), 4326)::geography, 'x') "
                               "RETURNING id", (when,)).fetchone()[0]
            conn.execute("INSERT INTO alert_evidence VALUES (%s, 'viirs', %s)", (aid, did))
    # Une preuve dont la détection a disparu (granule retraitée) : retirée aussitôt
    orphan = preuves.store(conn, r2, "viirs", 999_999_999, PNG, None, datetime.now(timezone.utc), "vignettes/viirs/x/y/orphan.png")
    out = preuves.purge(conn, r2, 30)
    assert out["retirees"] == 2 and out["gardees"] == 2
    gone = {r[0] for r in conn.execute("SELECT id FROM preuves_images WHERE supprime_le IS NOT NULL").fetchall()}
    assert gone == {ids["ancienne"], orphan}
    assert len(r2.objects) == 2
