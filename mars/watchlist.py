"""Listes de surveillance : catalogue GUR de la flotte fantôme (Vessels1.db, repris de shadow-fleet-tracker-light)
et jeu maritime d'OpenSanctions (maritime.csv, licence CC BY NC 4.0). Lecture et normalisation, partagées par
scripts/import_watchlist.py (chargement en base) et scripts/watchlist_check.py (croisement avec le Parquet)."""
import re
import sqlite3
from pathlib import Path

import pandas as pd

from mars.ais.live import valid_imo
from mars.config import ROOT

LISTS = ROOT / "data" / "listes"
GUR = LISTS / "Vessels1.db"
OS_CSV = LISTS / "maritime.csv"


def digits(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, float):
        v = int(v)                       # 9289518.0 devient 9289518, pas 92895180
    s = re.sub(r"\D", "", str(v))
    return s or None


def load_gur(path: Path = GUR) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["mmsi", "imo", "gur_nom"])
    with sqlite3.connect(path) as c:
        df = pd.read_sql("SELECT mmsi, imo, name AS gur_nom FROM vessels", c)
    df["imo"], df["mmsi"] = df.imo.map(digits), df.mmsi.map(digits)
    # Certaines entrées n'ont pas de nom : chaîne vide plutôt que NaN, car la présence dans la liste se lit sur
    # cette colonne après les jointures (une entrée sans nom passait pour absente du catalogue)
    df["gur_nom"] = df.gur_nom.fillna("").astype(str).str.strip()
    return df


def load_os(path: Path = OS_CSV) -> pd.DataFrame:
    cols = ["mmsi", "imo", "os_nom", "os_risques", "os_sources", "os_url", "os_sanction", "os_id"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype=str).fillna("")
    df = df[df["type"].str.upper() == "VESSEL"]
    out = pd.DataFrame({
        "imo": df.imo.map(digits), "mmsi": df.mmsi.map(digits), "os_nom": df.caption,
        "os_risques": df.risk, "os_sources": df.datasets, "os_url": df.url, "os_id": df["id"],
    })
    out["os_sanction"] = out.os_risques.str.contains("sanction", case=False)
    return out


def _mmsi(v):
    return int(v) if isinstance(v, str) and len(v) == 9 else None


def _split(v) -> list[str]:
    return [x for x in str(v or "").split(";") if x]


def watchlist_rows(gur: pd.DataFrame, os_: pd.DataFrame) -> list[tuple]:
    """Lignes de la table watchlist : (source, ref, imo, mmsi, name, risks, datasets, url, sanctioned, shadow).
    L'OMI n'est retenu que si sa clé est juste ; sinon l'entrée reste rapprochable par son MMSI."""
    rows = []
    for r in gur.itertuples(index=False):
        rows.append(("gur", r.mmsi if isinstance(r.mmsi, str) else None, valid_imo(r.imo), _mmsi(r.mmsi),
                     r.gur_nom or None, [], [], None, False, False))
    for r in os_.itertuples(index=False):
        risks = _split(r.os_risques)
        rows.append(("opensanctions", r.os_id, valid_imo(r.imo), _mmsi(r.mmsi), r.os_nom or None, risks,
                     _split(r.os_sources), r.os_url or None, bool(r.os_sanction), "mare.shadow" in risks))
    return rows
