"""Lot Z2 : registre des migrations (db/init/39_schema_migrations.sql, scripts/deployer.sh). Chaque migration est
détectable : 01 à 38 par leur dernier objet (jamais supposées), à partir de 39 par leur propre inscription."""
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "db" / "init"
URL = os.environ.get("MARS_TEST_DATABASE_URL")
FICHIERS = sorted(p.name for p in INIT.glob("*.sql"))


def _num(nom: str) -> int:
    return int(nom.split("_", 1)[0])


def test_numbering_is_unique_and_continuous():
    nums = [_num(f) for f in FICHIERS]
    assert nums == list(range(1, len(nums) + 1)), "numéros de migration manquants ou en double"


def test_registry_detects_every_migration_before_it():
    texte = (INIT / "39_schema_migrations.sql").read_text()
    detectees = re.findall(r"\('(\d{2}_[a-z0-9_]+\.sql)', ", texte)
    assert detectees == [f for f in FICHIERS if _num(f) < 39]


def test_each_new_migration_registers_itself():
    for f in FICHIERS:
        if _num(f) >= 39:
            t = (INIT / f).read_text()
            assert re.search(rf"INSERT INTO schema_migrations \(nom\) VALUES \('{re.escape(f)}'\) ON CONFLICT", t), \
                f"{f} : sa dernière instruction doit inscrire son nom dans schema_migrations"


def test_deployer_and_publisher_keep_their_guards():
    d = (ROOT / "scripts" / "deployer.sh").read_text()
    assert 'cd "${MARS_DEPLOYER_ESSAI:-/opt/mars_c2}"' in d and "PAS SUR LE SERVEUR" in d
    assert 'if [ "${BASH_SOURCE[0]}" != "$0" ]' in d and "return 1" in d, "un script sourcé ne doit jamais faire exit"
    assert "--single-transaction" in d and "3000000000" in d and ":latest" not in d
    p = (ROOT / "scripts" / "publier.sh").read_text()
    assert 'git status --porcelain' in p and 'origin/main' in p and "org.opencontainers.image.revision" in p
    assert p.index("verifier_images.sh backend scripts web controle sante") < p.index("docker push")
    assert p.index('[ "$OK" = 1 ]') < p.index("docker push") and p.rstrip().splitlines()[-2].startswith('echo "PUBLIÉ')


@pytest.mark.skipif(not URL, reason="MARS_TEST_DATABASE_URL absente : base de test requise")
def test_registry_marks_existing_migrations_from_their_objects():
    import psycopg
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS schema_migrations")
        c.execute((INIT / "39_schema_migrations.sql").read_text())
        noms = {r[0] for r in c.execute("SELECT nom FROM schema_migrations WHERE detectee")}
        assert noms == {f for f in FICHIERS if _num(f) < 39}
        assert c.execute("SELECT detectee FROM schema_migrations WHERE nom = '39_schema_migrations.sql'").fetchone() == (False,)
        for f in FICHIERS:
            if _num(f) > 39:
                c.execute((INIT / f).read_text())
        assert {r[0] for r in c.execute("SELECT nom FROM schema_migrations")} == set(FICHIERS)
