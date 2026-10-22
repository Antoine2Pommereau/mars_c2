"""Lot Z2 : essai de restauration de la dernière sauvegarde (mars/restauration.py). Comparaison des comptes, choix de
la dernière sauvegarde, garde du disque, base temporaire toujours supprimée (base de test, MARS_TEST_DATABASE_URL)."""
import os

import pytest

from mars import restauration as R

URL = os.environ.get("MARS_TEST_DATABASE_URL")


class FauxR2:
    def __init__(self, cles):
        self.cles, self.lus = cles, []

    def list(self, prefix):
        return [{"key": k, "bytes": 5_600_000} for k in self.cles if k.startswith(prefix)]

    def download(self, key, path):
        self.lus.append(key)
        path.write_bytes(b"PGDMP")


def test_latest_backup_and_plausible_counts():
    r2 = FauxR2(["sauvegardes/mars_20261020T023000.dump", "sauvegardes/mars_20261022T023000.dump",
                 "sauvegardes/autre.txt", "archives/x.parquet"])
    assert R.derniere_sauvegarde(r2)["key"] == "sauvegardes/mars_20261022T023000.dump"
    with pytest.raises(RuntimeError, match="aucune sauvegarde"):
        R.derniere_sauvegarde(FauxR2([]))
    vivants = {"alerts": 100, "vessels": 50, "regions": 4, "zones_vigilance": 0}
    assert R.ecarts(vivants, {"alerts": 97, "vessels": 50, "regions": 4, "zones_vigilance": 0}) == []
    pb = R.ecarts(vivants, {"alerts": 10, "vessels": 60, "zones_vigilance": 0})
    assert any("alerts : 10" in p for p in pb) and any("plus que" in p for p in pb) and any("regions absente" in p for p in pb)
    assert "positions" not in R.TABLES or "positions" in R.EXCLUDED_DATA


def test_refused_when_disk_is_short():
    with pytest.raises(RuntimeError, match="3 Go"):
        R.essai(None, FauxR2(["sauvegardes/mars_20261022T023000.dump"]), "postgresql://x/mars", libre_octets=1_000_000_000)


@pytest.fixture
def conn():
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL, autocommit=True)
    yield c
    c.execute(f"DROP DATABASE IF EXISTS {R.BASE_ESSAI} WITH (FORCE)")
    c.close()


def _copie(conn, tables):
    """Fausse restauration : recopie le schéma et les lignes des tables clés de la base de test (sans pg_restore)."""
    import psycopg

    def restaurer(_fichier, url):
        with psycopg.connect(url, autocommit=True) as rc:
            for t in tables:
                cols = conn.execute("SELECT string_agg(quote_ident(column_name) || ' text', ', ' ORDER BY ordinal_position) "
                                    "FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
                                    (t,)).fetchone()[0]
                rc.execute(f"CREATE TABLE {t} ({cols})")
                n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                if n:
                    rc.execute(f"INSERT INTO {t} SELECT FROM generate_series(1, {n})")
    return restaurer


def test_full_run_on_a_temporary_database_then_dropped(conn):
    tables = [t for t in R.TABLES if conn.execute("SELECT to_regclass(%s)", (f"public.{t}",)).fetchone()[0]]
    nb = conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'").fetchone()[0]
    r2 = FauxR2(["sauvegardes/mars_20261022T023000.dump"])
    with pytest.raises(RuntimeError, match="tables restaurées"):        # seulement les tables clés : schéma incomplet
        R.essai(conn, r2, URL, libre_octets=10**10, restaurer=_copie(conn, tables))
    assert conn.execute("SELECT count(*) FROM pg_database WHERE datname = %s", (R.BASE_ESSAI,)).fetchone()[0] == 0

    def complet(fichier, url):
        _copie(conn, tables)(fichier, url)
        import psycopg
        with psycopg.connect(url, autocommit=True) as rc:
            for i in range(nb - len(tables)):
                rc.execute(f"CREATE TABLE autre_{i} (x int)")
    out = R.essai(conn, r2, URL, libre_octets=10**10, restaurer=complet)
    assert out["sauvegarde"] == "sauvegardes/mars_20261022T023000.dump" and out["cible"] == "base temporaire"
    assert out["tables_restaurees"] == nb and all(a == b for a, b in out["comptes"].values())
    assert conn.execute("SELECT count(*) FROM pg_database WHERE datname = %s", (R.BASE_ESSAI,)).fetchone()[0] == 0
