"""Essai de restauration de la dernière sauvegarde (lot Z2) : une sauvegarde jamais restaurée n'est pas une sauvegarde.

La dernière sauvegarde de R2 (`sauvegardes/mars_*.dump`, mars/archive.py) est téléchargée, restaurée par pg_restore dans
une base jetable, puis les comptes des tables clés sont confrontés à la base en service ; la base jetable est détruite
dans tous les cas, le résultat consigné dans task_runs (tâche essai_restauration, tracée). Deux cibles :
* par défaut (tâche mensuelle de taches) : une base temporaire `essai_restauration` créée puis supprimée sur le
  PostgreSQL en service. Aucun conteneur : taches n'a pas (et ne doit pas avoir) le socket Docker ;
* `cible` (scripts/essai_restauration.sh, sur l'hôte) : un conteneur PostGIS jetable, 512 Mo, sans port publié, volume
  temporaire, créé et détruit par le script ; taches n'y reçoit qu'une adresse de connexion.
Garde : 3 Go libres au moins (dernière mesure du disque), sinon refus. Échec : notification ntfy (une par jour au plus,
la tâche étant retentée chaque heure)."""
import re
import subprocess
import tempfile
import time
from pathlib import Path

from mars import traces
from mars.archive import BACKUP_PREFIX, EXCLUDED_DATA
from mars.config import ROOT

DB_INIT = ROOT / "db" / "init"          # copié dans l'image des scripts (scripts/Dockerfile)

BASE_ESSAI = "essai_restauration"
LIBRE_MIN_OCTETS = 3_000_000_000
# Tables comparées : identité des navires, alertes et décisions, listes, carte, satellites, panneau, registre
TABLES = ("vessels", "vessel_identities", "watchlist", "alerts", "alert_evidence", "alert_actions", "infrastructure",
          "regions", "analyses", "detections", "viirs_detections", "zones_vigilance", "journal_controle",
          "schema_migrations")
TOLERANCE = 0.8          # la base vivante a pu grandir depuis la sauvegarde de la nuit (alertes, décisions)


def derniere_sauvegarde(r2) -> dict:
    dumps = [o for o in r2.list(BACKUP_PREFIX) if re.search(r"/mars_\d{8}T\d{6}\.dump$", o["key"])]
    if not dumps:
        raise RuntimeError("aucune sauvegarde sur R2")
    return max(dumps, key=lambda o: o["key"])


def _url_base(url: str, base: str) -> str:
    return re.sub(r"/[^/?]+(\?|$)", f"/{base}\\1", url, count=1) if "://" in url else url


def comptes(conn, tables=TABLES) -> dict[str, int | None]:
    out = {}
    for t in tables:
        if t in EXCLUDED_DATA:
            continue
        r = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{t}",)).fetchone()[0]
        out[t] = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] if r else None
    return out


def migrations_de_la_sauvegarde(rc) -> list[str]:
    """Migrations connues au moment de la sauvegarde : son registre schema_migrations, ou, pour une sauvegarde d'avant
    le registre (migration 39), les migrations 01 à 38."""
    if rc.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()[0]:
        return sorted(r[0] for r in rc.execute("SELECT nom FROM schema_migrations"))
    return sorted(p.name for p in DB_INIT.glob("*.sql") if int(p.name.split("_", 1)[0]) < 39)


def tables_des_migrations(noms) -> set[str]:
    """Tables laissées par ces fichiers de migration, dans l'ordre : créées (CREATE TABLE, hors tables temporaires),
    moins celles qu'une migration suivante retire (DROP TABLE, par exemple sim_clock, migration 43)."""
    out = set()
    for n in sorted(noms):
        p = DB_INIT / n
        if p.exists():
            texte = re.sub(r"--[^\n]*", "", p.read_text())
            out |= {m.lower() for m in re.findall(r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-z_][a-z0-9_]*)", texte, re.I)}
            out -= {m.lower() for m in re.findall(r"DROP\s+TABLE(?:\s+IF\s+EXISTS)?\s+([a-z_][a-z0-9_]*)", texte, re.I)}
    return out


def ecarts(vivants: dict, restaures: dict) -> list[str]:
    """Tables dont le compte restauré n'est pas plausible : absente, plus grande que la base vivante, ou sous 80 % de
    la base vivante (une sauvegarde de la nuit ne peut pas avoir perdu un cinquième d'une table)."""
    out = []
    for t, v in vivants.items():
        r = restaures.get(t)
        if v is None:
            continue
        if r is None:
            out.append(f"{t} absente de la restauration")
        elif r > v:
            out.append(f"{t} : {r} lignes restaurées, plus que les {v} de la base en service")
        elif r < TOLERANCE * v:
            out.append(f"{t} : {r} lignes restaurées pour {v} en service")
    return out


def essai(conn, r2, database_url: str, cible: str | None = None, libre_octets: int | None = None,
          restaurer=None) -> dict:
    """Essai complet (psycopg, autocommit) ; lève RuntimeError avec le motif en cas d'échec. `restaurer(fichier, url)` :
    pg_restore par défaut (remplaçable par les tests)."""
    import psycopg
    if libre_octets is None:
        r = conn.execute("SELECT libre_octets FROM mesures_disque ORDER BY mesure_le DESC LIMIT 1").fetchone()
        libre_octets = r[0] if r else None
    if libre_octets is not None and libre_octets < LIBRE_MIN_OCTETS:
        raise RuntimeError(f"essai refusé : {libre_octets / 1e9:.1f} Go libres, 3 Go au moins")
    restaurer = restaurer or _pg_restore
    s = derniere_sauvegarde(r2)
    out = {"sauvegarde": s["key"], "octets": s["bytes"], "cible": "conteneur jetable" if cible else "base temporaire"}
    with tempfile.TemporaryDirectory(prefix="restauration_") as tmp:
        f = Path(tmp) / "mars.dump"
        t = time.time()
        with traces.span("restauration.telechargement", **{"restauration.octets": s["bytes"]}):
            r2.download(s["key"], f)
        out["telechargement_s"] = round(time.time() - t, 1)
        url = cible or _url_base(database_url, BASE_ESSAI)
        if not cible:
            conn.execute(f"DROP DATABASE IF EXISTS {BASE_ESSAI} WITH (FORCE)")
            conn.execute(f"CREATE DATABASE {BASE_ESSAI}")
        try:
            t = time.time()
            with traces.span("restauration.pg_restore"):
                restaurer(f, url)
            out["restauration_s"] = round(time.time() - t, 1)
            # Comparaison aux tables connues de la sauvegarde (son registre des migrations), jamais à la base actuelle :
            # une table créée depuis la sauvegarde n'est pas un échec (24/10/2026 : sauvegarde d'avant les migrations 39
            # à 42, 55 tables pour 60, faux échec)
            with traces.span("restauration.comparaison"):
                with psycopg.connect(url, autocommit=True) as rc:
                    migrations = migrations_de_la_sauvegarde(rc)
                    presentes = {r[0] for r in rc.execute(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
                    attendues = tables_des_migrations(migrations)
                    cles = tuple(t for t in TABLES if t in attendues)
                    restaures = comptes(rc, cles)
                vivants = comptes(conn, cles)
            en_service = {r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
            out.update({"migrations_de_la_sauvegarde": len(migrations), "derniere_migration": migrations[-1] if migrations else None,
                        "tables_attendues": len(attendues), "tables_restaurees": len(presentes),
                        "tables_en_service": len(en_service),
                        # tables de nos migrations postérieures à la sauvegarde (ni vues ni tables de PostGIS)
                        "tables_creees_depuis": sorted((tables_des_migrations(p.name for p in DB_INIT.glob("*.sql"))
                                                        & en_service) - attendues)})
            out["comptes"] = {t: [restaures.get(t), v] for t, v in vivants.items()}
            pb = [f"{t} absente de la restauration" for t in sorted(attendues - presentes)]
            pb += ecarts(vivants, restaures)
            traces.attributs(**{"restauration.tables": len(presentes), "restauration.ecarts": len(pb),
                                "restauration.migrations": len(migrations)})
            if pb:
                raise RuntimeError("restauration incomplète : " + " ; ".join(pb[:5]))
        finally:
            if not cible:
                conn.execute(f"DROP DATABASE IF EXISTS {BASE_ESSAI} WITH (FORCE)")
    return out


def _pg_restore(fichier: Path, url: str):
    p = subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", f"--dbname={url}",
                        str(fichier)], capture_output=True, text=True, timeout=1800)
    if p.returncode:
        raise RuntimeError(f"pg_restore code {p.returncode} : {p.stderr[-500:]}")
