"""Archivage quotidien de la collecte sur R2, purge des positions anciennes, sauvegarde de la base.

Règles de sûreté de l'archivage, appliquées fichier par fichier :
* seuls les dossiers de journées terminées (veille ou avant, plus une marge) sont traités ;
* un fichier pas encore inscrit au registre d'ingestion n'est ni archivé ni supprimé ;
* un fichier n'est supprimé que s'il figure dans une archive confirmée sur R2 (taille et empreinte MD5 relues) et
  enregistrée en base (table archives) ; la décision relit la base juste avant la suppression.

Les fonctions de sélection (eligible_folders, plan_folder, deletable) sont pures et couvertes par
tests/test_archive.py.
"""
import io
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ARCHIVE_PREFIX = "ais"
BACKUP_PREFIX = "sauvegardes/"
GRACE = timedelta(minutes=30)       # marge après minuit avant de considérer la veille comme terminée
ZSTD_LEVEL = 9                      # 27 octets par position, 21 par message statique (mesuré le 06/10/2026)


# Sélection (fonctions pures)

def folder_day(folder: str) -> date:
    return date.fromisoformat(folder.rsplit("date=", 1)[1])


def eligible_folders(folders: list[str], now: datetime) -> list[str]:
    """Dossiers dont la journée est terminée : la collecte date ses dossiers à l'heure UTC de l'écriture, rien ne
    s'y ajoute plus après minuit ; la marge couvre une écriture en cours au passage de minuit."""
    return [f for f in folders
            if datetime.combine(folder_day(f) + timedelta(days=1), datetime.min.time(), timezone.utc) + GRACE <= now]


@dataclass
class FolderPlan:
    to_archive: list[str] = field(default_factory=list)     # ingérés, pas encore dans une archive confirmée
    to_delete: list[str] = field(default_factory=list)      # déjà dans une archive confirmée (reprise après arrêt)
    blocked: list[str] = field(default_factory=list)        # pas encore ingérés : on n'y touche pas


def plan_folder(on_disk: set[str], ingested: set[str], archived: set[str]) -> FolderPlan:
    """Partage les fichiers d'un dossier. Un fichier déjà archivé n'est jamais renvoyé : pas de doublon sur R2."""
    p = FolderPlan()
    for f in sorted(on_disk):
        if f not in ingested:
            p.blocked.append(f)
        elif f in archived:
            p.to_delete.append(f)
        else:
            p.to_archive.append(f)
    return p


def deletable(candidates: list[str], ingested: set[str], archived: set[str]) -> list[str]:
    """Dernier contrôle avant suppression : ingéré en base et présent dans une archive confirmée."""
    return [f for f in candidates if f in ingested and f in archived]


def backups_to_drop(keys: list[str], keep: int) -> list[str]:
    """Sauvegardes à supprimer : toutes sauf les `keep` plus récentes (la clé porte l'horodatage)."""
    dumps = sorted(k for k in keys if k.startswith(BACKUP_PREFIX + "mars_") and k.endswith(".dump"))
    return dumps[:-keep] if keep > 0 else dumps


# Archivage

def compact(paths: list[Path]) -> tuple[bytes, int]:
    """Regroupe des petits fichiers Parquet en un seul, trié par navire puis par instant, compressé en zstd.
    Les types peuvent différer d'un fichier à l'autre (entier ou flottant selon la présence de valeurs nulles) :
    promotion permissive. Contrôle : le fichier produit relu a autant de lignes que les fichiers d'origine."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    tables = [pq.read_table(p) for p in paths]
    n = sum(t.num_rows for t in tables)
    table = pa.concat_tables(tables, promote_options="permissive")
    if "mmsi" in table.column_names:              # origine de la donnée : fichiers antérieurs au champ, AISStream
        import pyarrow.compute as pc
        from mars.ais.live import SOURCE
        if "source" not in table.column_names:
            table = table.append_column("source", pa.array([SOURCE] * table.num_rows, pa.string()))
        else:
            table = table.set_column(table.column_names.index("source"), "source",
                                     pc.fill_null(table["source"], pa.scalar(SOURCE)))
    if {"mmsi", "ts"} <= set(table.column_names):
        table = table.sort_by([("mmsi", "ascending"), ("ts", "ascending")])
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd", compression_level=ZSTD_LEVEL)
    data = buf.getvalue()
    if pq.read_metadata(io.BytesIO(data)).num_rows != n:
        raise RuntimeError("Compactage incohérent : nombre de lignes différent")
    return data, n


def archive_key(kind: str, zone: str, day: date, stamp: datetime) -> str:
    return f"{ARCHIVE_PREFIX}/{kind}/zone={zone}/date={day}/{kind}_{zone}_{day}_{stamp:%Y%m%dT%H%M%S}.parquet"


def run_archive(conn, r2, root: Path, now: datetime | None = None, log=print) -> dict:
    now = now or datetime.now(timezone.utc)
    stats = {"dossiers": 0, "archives": 0, "fichiers_supprimes": 0, "octets_envoyes": 0, "lignes": 0,
             "fichiers_non_ingeres": 0}
    folders = [str(d.relative_to(root)) for kind in ("statiques", "positions")
               for d in sorted((root / kind).glob("zone=*/date=*")) if d.is_dir()]
    for folder in eligible_folders(folders, now):
        kind, zone_part, _ = folder.split("/")
        zone, day = zone_part.split("=", 1)[1], folder_day(folder)
        path = root / folder
        on_disk = {f.name for f in path.glob("*.parquet") if not f.name.startswith(".")}
        if not on_disk:
            _remove_if_empty(path)
            continue
        stats["dossiers"] += 1
        ingested = _ingested(conn, folder)
        plan = plan_folder(on_disk, ingested, _archived(conn, kind, zone, day))
        if plan.blocked:
            stats["fichiers_non_ingeres"] += len(plan.blocked)
            log(f"{folder} : {len(plan.blocked)} fichier(s) pas encore ingéré(s), conservés "
                f"(par exemple {plan.blocked[0]})")
        if plan.to_archive:
            data, rows = compact([path / f for f in plan.to_archive])
            key = archive_key(kind, zone, day, now)
            sent = r2.put_verified(data, key)          # lève une exception si l'objet stocké ne correspond pas
            with conn.transaction():
                conn.execute("INSERT INTO archives (kind, zone, day, object_key, files, rows, bytes, md5) "
                             "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                             (kind, zone, day, key, plan.to_archive, rows, sent["bytes"], sent["md5"]))
            stats["archives"] += 1
            stats["octets_envoyes"] += sent["bytes"]
            stats["lignes"] += rows
        # Suppression : la base est relue, seule source de vérité sur ce qui est confirmé
        confirmed = _archived(conn, kind, zone, day)
        for f in deletable(plan.to_delete + plan.to_archive, _ingested(conn, folder), confirmed):
            (path / f).unlink(missing_ok=True)
            stats["fichiers_supprimes"] += 1
        _remove_if_empty(path)
    return stats


def _ingested(conn, folder: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM ingested_files WHERE folder = %s", (folder,))}


def _archived(conn, kind: str, zone: str, day: date) -> set[str]:
    return {f for r in conn.execute("SELECT files FROM archives WHERE kind = %s AND zone = %s AND day = %s",
                                    (kind, zone, day)) for f in r[0]}


def _remove_if_empty(path: Path):
    try:
        path.rmdir()                 # échoue sans rien faire s'il reste un fichier (y compris caché)
    except OSError:
        pass


# Conservation en base

def run_purge(conn, root: Path, keep_days: int, now: datetime | None = None) -> dict:
    """Supprime les positions des journées de plus de keep_days jours, seulement si cette journée est archivée
    sur R2 (au moins un fichier de positions confirmé) : une journée jamais archivée (rejeu danois, ou archivage en
    échec) reste en base. Retire aussi ces journées de ais_days et le registre d'ingestion des dossiers disparus."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=keep_days)).date()
    days = [r[0] for r in conn.execute(
        "SELECT d.day FROM ais_days d WHERE d.day < %s "
        "AND EXISTS (SELECT 1 FROM archives a WHERE a.kind = 'positions' AND a.day = d.day) ORDER BY d.day",
        (cutoff,))]
    stats = {"jours": [str(d) for d in days], "positions_supprimees": 0, "registre_supprime": 0}
    for d in days:
        with conn.transaction():
            n = conn.execute("DELETE FROM positions WHERE ts >= (%(d)s::date)::timestamp AT TIME ZONE 'UTC' "
                             "AND ts < (%(d)s::date + 1)::timestamp AT TIME ZONE 'UTC'", {"d": d}).rowcount
            conn.execute("DELETE FROM ais_days WHERE day = %s", (d,))
        stats["positions_supprimees"] += n
    # Registre d'ingestion : lignes des dossiers anciens qui n'existent plus sur le disque (archivés et supprimés)
    old = [r[0] for r in conn.execute(
        "SELECT DISTINCT folder FROM ingested_files WHERE split_part(folder, 'date=', 2) < %s", (str(cutoff),))]
    gone = [f for f in old if not (root / f).exists()]
    if gone:
        stats["registre_supprime"] = conn.execute("DELETE FROM ingested_files WHERE folder = ANY(%s)", (gone,)).rowcount
    # Journal des cycles de règles (un toutes les 5 minutes) : deux jours suffisent
    stats["cycles_de_regles_supprimes"] = conn.execute(
        "DELETE FROM task_runs WHERE task = 'regles' AND started_at < now() - interval '2 days'").rowcount
    return stats


# Sauvegarde

# Tables dont les données ne sont pas sauvegardées : positions (dans l'archive Parquet de R2, rechargées par
# scripts/taches.py restaurer-positions) et registre d'ingestion (vidé à la restauration pour que l'ingestion
# recharge les fichiers encore sur le disque).
EXCLUDED_DATA = ("positions", "ingested_files")


def run_backup(r2, database_url: str, keep: int, now: datetime | None = None) -> dict:
    """pg_dump au format personnalisé (compressé), envoyé en flux sur R2 sans fichier local, puis vérifié : code de
    sortie nul, taille sur R2 égale aux octets produits. Les anciennes sauvegardes ne sont supprimées qu'ensuite."""
    now = now or datetime.now(timezone.utc)
    key = f"{BACKUP_PREFIX}mars_{now:%Y%m%dT%H%M%S}.dump"
    cmd = ["pg_dump", "--format=custom", "--compress=6", f"--dbname={database_url}"]
    cmd += [f"--exclude-table-data={t}" for t in EXCLUDED_DATA]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        try:
            sent = r2.put_stream(proc.stdout, key)
        finally:
            proc.stdout.close()
            code = proc.wait()
        err.seek(0)
        message = err.read().decode(errors="replace").strip()
    try:
        if code != 0 or sent["bytes"] == 0:
            raise RuntimeError(f"pg_dump code {code}, {sent['bytes']} octets : {message[-500:]}")
        method = r2.verify(key, sent["md5"], sent["bytes"])     # envoi en plusieurs morceaux : relecture
    except Exception as e:
        r2.delete(key)                    # une sauvegarde tronquée ou altérée ne doit pas passer pour valide
        raise RuntimeError(f"Sauvegarde invalide : {e}")
    dropped = backups_to_drop([o["key"] for o in r2.list(BACKUP_PREFIX)], keep)
    for k in dropped:
        r2.delete(k)
    return {"cle": key, "octets": sent["bytes"], "verification": method, "supprimees": dropped}


# Rechargement des positions depuis l'archive

def restore_positions(conn, r2, ingestor, day: date, workdir: Path) -> dict:
    """Recharge une journée de positions depuis R2, toutes zones réunies et dans l'ordre chronologique, avec
    l'allègement de l'ingestion (instance dédiée : l'ingestion en direct écarterait ces messages comme tardifs).
    Refuse une journée qui a déjà des positions en base, pour ne pas créer de doublons."""
    present = conn.execute(
        "SELECT count(*) FROM positions WHERE ts >= (%(d)s::date)::timestamp AT TIME ZONE 'UTC' "
        "AND ts < (%(d)s::date + 1)::timestamp AT TIME ZONE 'UTC'", {"d": day}).fetchone()[0]
    if present:
        return {"jour": str(day), "ignore": f"{present} positions déjà en base"}
    keys = [o["key"] for o in r2.list(f"{ARCHIVE_PREFIX}/positions/") if f"/date={day}/" in o["key"]]
    if not keys:
        return {"jour": str(day), "ignore": "aucune archive"}
    frames = []
    for k in keys:
        local = workdir / Path(k).name
        r2.download(k, local)
        frames.append(pd.read_parquet(local))
        local.unlink()
    raw = pd.concat(frames, ignore_index=True)
    raw["_t"] = pd.to_datetime(raw.ts.astype(str).str.split(" +", regex=False).str[0], utc=True,
                               errors="coerce", format="ISO8601")
    raw = raw.sort_values("_t").drop(columns="_t")
    totals: dict = {}
    for start in range(0, len(raw), 200_000):         # par tranches chronologiques : mémoire bornée
        s = ingestor.load_positions(raw.iloc[start:start + 200_000])
        for k, v in s.items():
            totals[k] = totals.get(k, 0) + v
    ingestor.update_days()
    return {"jour": str(day), "archives": len(keys), **totals}


# Surveillance du disque

def check_disk(conn, paths: list[str], threshold_pct: float, log=print) -> list[dict]:
    """Espace libre de chaque chemin (dans le conteneur, « / » est le disque de Docker, où vit la base, et
    /app/data/ais_live celui de la collecte). Écrit la mesure en base pour /api/ingestion ; une ligne d'alerte dans
    les journaux sous le seuil."""
    import shutil

    out, seen = [], set()
    for p in paths:
        if not Path(p).exists():
            continue
        u = shutil.disk_usage(p)
        if (u.total, u.free) in seen:          # même système de fichiers : une seule mesure utile
            continue
        seen.add((u.total, u.free))
        pct = 100.0 * u.free / u.total
        alert = pct < threshold_pct
        conn.execute(
            "INSERT INTO disk_status (path, total_bytes, free_bytes, free_pct, alert, checked_at) "
            "VALUES (%s, %s, %s, %s, %s, now()) ON CONFLICT (path) DO UPDATE SET total_bytes = EXCLUDED.total_bytes, "
            "free_bytes = EXCLUDED.free_bytes, free_pct = EXCLUDED.free_pct, alert = EXCLUDED.alert, checked_at = now()",
            (p, u.total, u.free, pct, alert))
        if alert:
            log(f"ALERTE DISQUE : {pct:.1f} % libre sur {p} ({u.free / 1e9:.1f} Go sur {u.total / 1e9:.1f} Go), "
                f"seuil {threshold_pct:.0f} %")
        out.append({"chemin": p, "libre_pct": round(pct, 1), "alerte": alert})
    return out
