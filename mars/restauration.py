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
            with traces.span("restauration.comparaison"):
                vivants = comptes(conn)
                with psycopg.connect(url, autocommit=True) as rc:
                    restaures = comptes(rc)
                    out["tables_restaurees"] = rc.execute(
                        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'").fetchone()[0]
            out["tables_en_service"] = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'").fetchone()[0]
            out["comptes"] = {t: [restaures.get(t), v] for t, v in vivants.items()}
            pb = ecarts(vivants, restaures)
            if out["tables_restaurees"] < out["tables_en_service"] - 1:      # tolérance : une table créée depuis
                pb.append(f"{out['tables_restaurees']} tables restaurées pour {out['tables_en_service']} en service")
            traces.attributs(**{"restauration.tables": out["tables_restaurees"], "restauration.ecarts": len(pb)})
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
