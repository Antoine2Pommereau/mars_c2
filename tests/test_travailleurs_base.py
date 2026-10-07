"""Travailleurs sur une vraie base PostgreSQL (migrations de db/init appliquées) : verrou contre les lancements
concurrents, échec de lancement, délai de démarrage, résultats reçus deux fois. Ignorés sans MARS_TEST_DATABASE_URL,
par exemple : MARS_TEST_DATABASE_URL=postgresql://mars:mars@localhost:55432/mars (base de test, docs/deploiement.md)."""
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

    def image_id(self, *_args):
        return "img"

    def create(self, _zone, name, _ct, _image, _disque, tags):
        time.sleep(self.delay)
        self.n += 1
        sid = f"srv-{name}"
        self.servers[sid] = {"id": sid, "name": name, "tags": tags, "project": "p", "state": "stopped"}
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
