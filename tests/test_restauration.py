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
                if cols is None:                    # table retirée depuis de la base de test (sim_clock) : vide
                    rc.execute(f"CREATE TABLE {t} (x int)")
                    continue
                rc.execute(f"CREATE TABLE {t} ({cols})")
                n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                if n:
                    rc.execute(f"INSERT INTO {t} SELECT FROM generate_series(1, {n})")
    return restaurer


def _avant(n: int) -> list[str]:
    return sorted(p.name for p in R.DB_INIT.glob("*.sql") if int(p.name.split("_", 1)[0]) < n)


def test_backup_from_before_the_registry_is_compared_to_its_own_tables(conn):
    """Cas réel du 24/10/2026 : sauvegarde d'avant les migrations 39 à 42 (ni schema_migrations, ni echeances, ni
    tentatives_connexion), 55 tables pour 60 en service : faux échec. Désormais comparée aux tables de ses migrations."""
    tables = sorted(R.tables_des_migrations(_avant(39)))
    r2 = FauxR2(["sauvegardes/mars_20261024T023000.dump"])
    out = R.essai(conn, r2, URL, libre_octets=10**10, restaurer=_copie(conn, tables))
    assert out["migrations_de_la_sauvegarde"] == 38 and out["derniere_migration"].startswith("38_")
    assert out["tables_attendues"] == len(tables) and out["tables_restaurees"] == len(tables)
    assert {"schema_migrations", "echeances", "tentatives_connexion"} <= set(out["tables_creees_depuis"])
    assert "schema_migrations" not in out["comptes"]
    assert conn.execute("SELECT count(*) FROM pg_database WHERE datname = %s", (R.BASE_ESSAI,)).fetchone()[0] == 0


def test_a_table_known_to_the_backup_but_missing_is_a_failure(conn):
    tables = sorted(R.tables_des_migrations(_avant(39)))
    r2 = FauxR2(["sauvegardes/mars_20261024T023000.dump"])
    with pytest.raises(RuntimeError, match="alert_actions absente"):
        R.essai(conn, r2, URL, libre_octets=10**10, restaurer=_copie(conn, [t for t in tables if t != "alert_actions"]))
    assert conn.execute("SELECT count(*) FROM pg_database WHERE datname = %s", (R.BASE_ESSAI,)).fetchone()[0] == 0


def test_backup_with_its_registry_expects_the_tables_of_its_migrations(conn):
    """Sauvegarde d'après le registre : ses migrations (01 à 42 ici) disent les tables attendues, registre compris."""
    tables = sorted(R.tables_des_migrations(_avant(99)))
    noms = _avant(99)

    def restaurer(f, url):
        _copie(conn, [t for t in tables if t != "schema_migrations"])(f, url)
        import psycopg
        with psycopg.connect(url, autocommit=True) as rc:
            rc.execute("CREATE TABLE schema_migrations (nom text)")
            rc.cursor().executemany("INSERT INTO schema_migrations VALUES (%s)", [(n,) for n in noms])
    out = R.essai(conn, FauxR2(["sauvegardes/mars_20261025T023000.dump"]), URL, libre_octets=10**10, restaurer=restaurer)
    assert out["migrations_de_la_sauvegarde"] == len(noms) and out["tables_creees_depuis"] == []
    assert out["comptes"]["schema_migrations"][0] == len(noms)


def test_a_table_dropped_by_a_later_migration_is_not_expected():
    """La migration 43 retire sim_clock, créée par la 02 : une sauvegarde postérieure ne la contient plus."""
    toutes = sorted(p.name for p in R.DB_INIT.glob("*.sql"))
    assert "sim_clock" in R.tables_des_migrations([n for n in toutes if int(n[:2]) < 43])
    assert "sim_clock" not in R.tables_des_migrations(toutes)
