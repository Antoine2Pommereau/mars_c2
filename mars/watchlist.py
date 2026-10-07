"""Listes de surveillance : catalogue GUR de la flotte fantôme (Vessels1.db, repris de shadow-fleet-tracker-light,
licence MIT) et jeu maritime d'OpenSanctions (maritime.csv, licence CC BY NC 4.0 : « Data from OpenSanctions.org »,
attribution reprise dans l'interface et la documentation). Lecture, normalisation, comparaison et import, partagés
par scripts/import_watchlist.py (fichiers locaux) et la mise à jour automatique du conteneur taches (téléchargement
en mémoire, aucun fichier écrit)."""
import io
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


OS_URL = "https://data.opensanctions.org/datasets/latest/maritime/maritime.csv"
GUR_REPO = "FormerLab/shadow-fleet-tracker-light"
GUR_API = f"https://api.github.com/repos/{GUR_REPO}/contents/Vessels1.db"
GUR_RAW = f"https://raw.githubusercontent.com/{GUR_REPO}/main/Vessels1.db"
HEADERS = {"User-Agent": "mars-c2 (surveillance maritime, projet personnel)"}


def load_gur(src: Path | bytes = GUR) -> pd.DataFrame:
    """Catalogue GUR depuis un fichier, ou depuis son contenu en mémoire (sqlite3 deserialize, rien sur le disque)."""
    if isinstance(src, bytes):
        # Vessels1.db est publié en mode WAL (octets 18 et 19 de l'en tête à 2), que deserialize refuse : on le
        # repasse en journal classique, en mémoire seulement
        data = bytearray(src)
        if len(data) > 19 and data[18] == 2:
            data[18] = data[19] = 1
        c = sqlite3.connect(":memory:")
        c.deserialize(bytes(data))
    elif src.exists():
        c = sqlite3.connect(src)
    else:
        return pd.DataFrame(columns=["mmsi", "imo", "gur_nom"])
    try:
        df = pd.read_sql("SELECT mmsi, imo, name AS gur_nom FROM vessels", c)
    finally:
        c.close()
    df["imo"], df["mmsi"] = df.imo.map(digits), df.mmsi.map(digits)
    # Certaines entrées n'ont pas de nom : chaîne vide plutôt que NaN, car la présence dans la liste se lit sur
    # cette colonne après les jointures (une entrée sans nom passait pour absente du catalogue)
    df["gur_nom"] = df.gur_nom.fillna("").astype(str).str.strip()
    return df


def load_os(src: Path | bytes = OS_CSV) -> pd.DataFrame:
    cols = ["mmsi", "imo", "os_nom", "os_risques", "os_sources", "os_url", "os_sanction", "os_id"]
    if isinstance(src, bytes):
        src = io.BytesIO(src)
    elif not src.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(src, dtype=str).fillna("")
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


def gur_rows(gur: pd.DataFrame) -> list[tuple]:
    return [("gur", r.mmsi if isinstance(r.mmsi, str) else None, valid_imo(r.imo), _mmsi(r.mmsi),
             r.gur_nom or None, [], [], None, False, False) for r in gur.itertuples(index=False)]


def os_rows(os_: pd.DataFrame) -> list[tuple]:
    rows = []
    for r in os_.itertuples(index=False):
        risks = _split(r.os_risques)
        rows.append(("opensanctions", r.os_id, valid_imo(r.imo), _mmsi(r.mmsi), r.os_nom or None, risks,
                     _split(r.os_sources), r.os_url or None, bool(r.os_sanction), "mare.shadow" in risks))
    return rows


def watchlist_rows(gur: pd.DataFrame, os_: pd.DataFrame) -> list[tuple]:
    """Lignes de la table watchlist : (source, ref, imo, mmsi, name, risks, datasets, url, sanctioned, shadow).
    L'OMI n'est retenu que si sa clé est juste ; sinon l'entrée reste rapprochable par son MMSI."""
    return gur_rows(gur) + os_rows(os_)


# Comparaison et import

FIELDS = ("source", "ref", "imo", "mmsi", "name", "risks", "datasets", "url", "sanctioned", "shadow")
COMPARED = ("imo", "mmsi", "name", "risks", "datasets", "sanctioned", "shadow")


def _key(r: dict):
    return r["ref"] or (f"omi:{r['imo']}" if r["imo"] else f"nom:{r['name']}")


def diff(old: list[dict], new: list[dict], sample: int = 20) -> dict:
    """Navires ajoutés, retirés et modifiés entre deux états d'une même source (lignes au format FIELDS).
    Clé : identifiant dans la source (fiche OpenSanctions, MMSI pour le GUR), sinon OMI, sinon nom. Le journal
    garde les comptes complets et au plus `sample` exemples par catégorie."""
    a = {_key(r): r for r in old}
    b = {_key(r): r for r in new}

    def brief(r):
        return {"ref": r["ref"], "name": r["name"], "imo": r["imo"], "mmsi": r["mmsi"]}

    added = [brief(b[k]) for k in sorted(b.keys() - a.keys(), key=str)]
    removed = [brief(a[k]) for k in sorted(a.keys() - b.keys(), key=str)]
    modified = []
    for k in sorted(a.keys() & b.keys(), key=str):
        ch = {f: [a[k][f], b[k][f]] for f in COMPARED
              if (sorted(a[k][f] or []) if f in ("risks", "datasets") else a[k][f])
              != (sorted(b[k][f] or []) if f in ("risks", "datasets") else b[k][f])}
        if ch:
            modified.append({**brief(b[k]), "changements": ch})
    return {"ajoutes": len(added), "retires": len(removed), "modifies": len(modified),
            "exemples": {"ajoutes": added[:sample], "retires": removed[:sample], "modifies": modified[:sample]}}


def current_rows(cur, source: str) -> list[dict]:
    rows = cur.execute(f"SELECT {', '.join(FIELDS)} FROM watchlist WHERE source = %s", (source,)).fetchall()
    return [dict(zip(FIELDS, r)) for r in rows]


def replace_source(cur, source: str, rows: list[tuple]):
    """Remplace une source dans la table watchlist (dans la transaction de l'appelant) : une liste absente ou en
    échec ne vide pas l'autre."""
    cur.execute("DELETE FROM watchlist WHERE source = %s", (source,))
    with cur.copy(f"COPY watchlist ({', '.join(FIELDS)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)


def import_rows(conn, source: str, rows: list[tuple]) -> dict:
    """Compare à la liste en base puis, s'il y a du changement, remplace la source et rafraîchit vessel_watch, en
    une transaction : en cas d'erreur, la liste précédente reste en place."""
    new = [dict(zip(FIELDS, r)) for r in rows]
    if not new:
        raise ValueError(f"liste {source} téléchargée vide : liste précédente conservée")
    with conn.transaction(), conn.cursor() as cur:
        d = diff(current_rows(cur, source), new)
        if d["ajoutes"] or d["retires"] or d["modifies"]:
            replace_source(cur, source, rows)
            cur.execute("REFRESH MATERIALIZED VIEW vessel_watch")
    return {"navires": len(new), **d}


def fetch(url: str, timeout: float = 120) -> bytes:
    import requests
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.content


def update_opensanctions(conn) -> dict:
    """Téléchargement du jeu maritime d'OpenSanctions (environ 5 Mo, en mémoire) et import s'il a changé."""
    data = fetch(OS_URL)
    os_ = load_os(data)
    if os_.empty:
        raise ValueError("jeu OpenSanctions sans navire : liste précédente conservée")
    rows = os_rows(os_)
    return {"source": "opensanctions", "octets": len(data), **import_rows(conn, "opensanctions", rows)}


def update_gur(conn, known_sha: str | None) -> dict:
    """Version de Vessels1.db (empreinte Git, API GitHub) ; téléchargement et import seulement si elle a changé."""
    import requests
    r = requests.get(GUR_API, headers={**HEADERS, "Accept": "application/vnd.github+json"}, timeout=30)
    r.raise_for_status()
    sha = r.json()["sha"]
    if sha == known_sha:
        return {"source": "gur", "version": sha, "inchangee": True}
    data = fetch(GUR_RAW)
    gur = load_gur(data)
    if gur.empty:
        raise ValueError("catalogue GUR vide : liste précédente conservée")
    rows = gur_rows(gur)
    return {"source": "gur", "version": sha, "octets": len(data), **import_rows(conn, "gur", rows)}
