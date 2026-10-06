"""Archivage : choix des dossiers, des fichiers à archiver et à supprimer, des sauvegardes à retirer, compactage."""
import io
from datetime import datetime, timezone

import pandas as pd
import pyarrow.parquet as pq

from mars.archive import backups_to_drop, compact, deletable, eligible_folders, plan_folder

UTC = timezone.utc
F = ["positions/zone=bretagne/date=2026-10-05", "positions/zone=bretagne/date=2026-10-06",
     "statiques/zone=manche/date=2026-10-06", "positions/zone=manche/date=2026-10-07"]


def test_only_finished_days_after_grace():
    # Le 07/10 à 00:10 : la journée du 06 n'est pas encore sûre (marge de 30 min), celle du 05 l'est
    assert eligible_folders(F, datetime(2026, 10, 7, 0, 10, tzinfo=UTC)) == [F[0]]
    # À 00:30, le 06 aussi ; jamais la journée en cours
    assert eligible_folders(F, datetime(2026, 10, 7, 0, 30, tzinfo=UTC)) == F[:3]


def test_plan_never_archives_or_deletes_uningested():
    p = plan_folder(on_disk={"a.parquet", "b.parquet", "c.parquet"}, ingested={"a.parquet", "b.parquet"},
                    archived=set())
    assert p.to_archive == ["a.parquet", "b.parquet"] and p.blocked == ["c.parquet"] and p.to_delete == []


def test_plan_resumes_after_crash_without_duplicate():
    # Arrêt après l'envoi confirmé, avant la suppression : on supprime sans renvoyer (pas de doublon sur R2)
    p = plan_folder(on_disk={"a.parquet", "b.parquet"}, ingested={"a.parquet", "b.parquet"},
                    archived={"a.parquet"})
    assert p.to_delete == ["a.parquet"] and p.to_archive == ["b.parquet"]


def test_deletion_requires_ingested_and_confirmed():
    cands = ["a.parquet", "b.parquet", "c.parquet"]
    # Envoi échoué : rien de confirmé, rien n'est supprimé
    assert deletable(cands, ingested=set(cands), archived=set()) == []
    # Confirmé mais pas ingéré (registre purgé ou incohérent) : conservé
    assert deletable(cands, ingested={"a.parquet"}, archived=set(cands)) == ["a.parquet"]
    assert deletable(cands, ingested=set(cands), archived={"b.parquet"}) == ["b.parquet"]


def test_backups_keep_seven_most_recent():
    keys = [f"sauvegardes/mars_202610{d:02d}T023000.dump" for d in range(1, 11)] + ["sauvegardes/autre.txt"]
    drop = backups_to_drop(keys, keep=7)
    assert drop == keys[:3]
    assert backups_to_drop(keys[:5], keep=7) == []


def test_compact_merges_heterogeneous_files(tmp_path):
    # nav_status entier dans un fichier, flottant (avec une valeur manquante) dans l'autre : promotion permissive
    a = pd.DataFrame({"mmsi": [2, 1], "ts": ["2026-10-06 12:00:01 +0000 UTC", "2026-10-06 12:00:00.5 +0000 UTC"],
                      "nav_status": [0, 5]})
    b = pd.DataFrame({"mmsi": [1], "ts": ["2026-10-06 12:01:00 +0000 UTC"], "nav_status": [None]})
    a.to_parquet(tmp_path / "a.parquet", index=False)
    b.to_parquet(tmp_path / "b.parquet", index=False)
    data, rows = compact([tmp_path / "a.parquet", tmp_path / "b.parquet"])
    out = pq.read_table(io.BytesIO(data)).to_pandas()
    assert rows == 3 and len(out) == 3
    assert out.mmsi.tolist() == [1, 1, 2]                       # trié par navire puis par instant
    assert out.ts.iloc[0].startswith("2026-10-06 12:00:00.5")   # horodatage brut conservé tel quel
