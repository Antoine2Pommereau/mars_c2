"""Numéros OMI de remplissage (31/10/2026) : 1234567 a la clé juste mais sert à plusieurs navires sans lien (VIVA
BEYOND, US GOVT VESSEL, AL SHINDAGA, TEST, TEST1 sous trois MMSI) ; il ne déclenche plus de changement d'identité, et
les alertes passées qui en dépendent (47064 et 63928 sur le serveur) sont classées avec leur motif, décisions des
opérateurs gardées. En base : MARS_TEST_DATABASE_URL."""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from mars.config import load_rules
from mars.rules import classer_omi_factices, run_identity

RULES = load_rules()
URL = os.environ.get("MARS_TEST_DATABASE_URL")
NOW = datetime(2026, 10, 22, 13, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL)
    yield c
    c.rollback()
    c.close()


def _alerte(conn, omi, statut):
    return conn.execute(
        "INSERT INTO alerts (type, severity, status, event_time, detected_at, geom, details, rule_version) VALUES "
        "('IDENTITY_CHANGE', 'elevee', %s, %s, %s, ST_SetSRID(ST_MakePoint(-4, 48), 4326)::geography, "
        "jsonb_build_object('omi', %s::int, 'changement', 'omi_autre_mmsi'), 'essai') RETURNING id",
        (statut, NOW, NOW, omi)).fetchone()[0]


def test_past_alerts_on_a_placeholder_imo_are_classified_and_decisions_kept(conn):
    conn.execute("DELETE FROM alerts WHERE type = 'IDENTITY_CHANGE'")
    vierge, decidee, vraie = _alerte(conn, 1234567, "nouvelle"), _alerte(conn, 1234567, "acquittee"), \
        _alerte(conn, 9901556, "nouvelle")
    sim = classer_omi_factices(conn, simulation=True)
    assert sim["classees"] == [vierge] and sim["decisions_gardees"] == [decidee] and "simulation" in sim
    assert conn.execute("SELECT status FROM alerts WHERE id = %s", (vierge,)).fetchone()[0] == "nouvelle"   # rien écrit
    r = classer_omi_factices(conn)
    assert r["fondees_sur_un_omi_factice"] == [vierge, decidee] and r["classees"] == [vierge]
    st = dict(conn.execute("SELECT id, status FROM alerts WHERE id = ANY(%s)", ([vierge, decidee, vraie],)).fetchall())
    assert st == {vierge: "classee", decidee: "acquittee", vraie: "nouvelle"}
    motif, note, auteur = conn.execute("SELECT motif, note, author FROM alert_actions WHERE alert_id = %s",
                                       (vierge,)).fetchone()
    assert motif == "faux_positif" and "remplissage (1234567)" in note and auteur == "MARS C2"
    note = conn.execute("SELECT details->'omi_factice' FROM alerts WHERE id = %s", (decidee,)).fetchone()[0]
    assert note["decision_gardee"] == "acquittee"


def test_placeholder_imo_shared_by_three_mmsi_raises_no_identity_change(conn):
    """Cas du serveur : 1234567 sous trois MMSI ; un vrai changement de pavillon du même instant reste signalé."""
    conn.execute("DELETE FROM positions WHERE vessel_id IN (SELECT id FROM vessels WHERE mmsi BETWEEN 269057210 AND "
                 "269057219)")
    conn.execute("DELETE FROM vessels WHERE mmsi BETWEEN 269057210 AND 269057219")
    ids = []
    for k, (mmsi, nom, omi, debut) in enumerate([(269057211, "VIVA BEYOND", 1234567, NOW - timedelta(days=10)),
                                                  (269057212, "US GOVT VESSEL", 1234567, NOW - timedelta(hours=12)),
                                                  (269057213, "TEST", 1234567, NOW - timedelta(hours=11)),
                                                  (269057214, "MSC ESSAI", 9901556, NOW - timedelta(days=10)),
                                                  (269057215, "MSC ESSAI", 9901556, NOW - timedelta(hours=12))]):
        vid = conn.execute("INSERT INTO vessels (mmsi, name, imo, ais_class) VALUES (%s, %s, %s, 'A') RETURNING id",
                           (mmsi, nom, omi)).fetchone()[0]
        conn.execute("INSERT INTO vessel_identities (vessel_id, name, imo, first_seen, last_seen) VALUES "
                     "(%s, %s, %s, %s, %s)", (vid, nom, omi, debut, debut + timedelta(hours=1 if k in (0, 3) else 11)))
        conn.execute("INSERT INTO positions (vessel_id, ts, geom, sog_kn, cog_deg) VALUES (%s, %s, "
                     "ST_SetSRID(ST_MakePoint(-4, 48), 4326)::geography, 10, 90)", (vid, debut))
        ids.append(vid)
    conn.commit()
    import asyncpg

    async def go():
        c = await asyncpg.connect(URL)
        await c.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        try:
            await c.execute("DELETE FROM alerts WHERE type = 'IDENTITY_CHANGE'")
            await run_identity(c, NOW - timedelta(hours=24), NOW, RULES)
            return [dict(r) for r in await c.fetch("SELECT (details->>'omi')::int AS omi FROM alerts "
                                                    "WHERE type = 'IDENTITY_CHANGE'")]
        finally:
            await c.execute("DELETE FROM alerts WHERE type = 'IDENTITY_CHANGE'")
            await c.close()
    try:
        assert asyncio.run(go()) == [{"omi": 9901556}]
    finally:
        conn.execute("DELETE FROM positions WHERE vessel_id = ANY(%s)", (ids,))
        conn.execute("DELETE FROM vessels WHERE id = ANY(%s)", (ids,))
        conn.commit()
