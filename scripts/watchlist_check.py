"""Croise la collecte AIS en direct avec deux listes de surveillance.

1. Liste GUR (data/listes/Vessels1.db, reprise de shadow-fleet-tracker-light) : flotte fantôme identifiée par le
   renseignement ukrainien, y compris des navires pas encore sanctionnés.
2. OpenSanctions, jeu maritime (data/listes/maritime.csv) : navires sanctionnés (UE, Royaume Uni, États Unis…) mais
   aussi signalés pour d'autres risques (immobilisations au contrôle portuaire, avertissements). Licence CC BY NC 4.0.

Un navire est reconnu d'abord par son numéro OMI (stable), sinon par son MMSI (qui change avec le pavillon :
correspondance moins sûre, signalée comme telle).

Niveau de signal :
  fort         : dans la liste GUR et sanctionné selon OpenSanctions
  sanctionné   : sanctionné selon OpenSanctions seulement
  suspect GUR  : dans la liste GUR, pas (encore) sanctionné
  autre risque : signalé par OpenSanctions pour un autre motif (immobilisation, avertissement)

Usage : python scripts/watchlist_check.py
Sorties : tableau, data/listes/correspondances.csv, data/listes/traces_surveillance.geojson (pour geojson.io)
"""
import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "ais_live"
LISTS = ROOT / "data" / "listes"
GUR = LISTS / "Vessels1.db"
OS_CSV = LISTS / "maritime.csv"
RANK = {"fort": 0, "sanctionné": 1, "flotte fantôme": 2, "suspect GUR": 3, "autre risque": 4}


def digits(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, float):
        v = int(v)                       # 9289518.0 devient 9289518, pas 92895180
    s = re.sub(r"\D", "", str(v))
    return s or None


def load_gur():
    if not GUR.exists():
        return pd.DataFrame(columns=["mmsi", "imo", "gur_nom"])
    with sqlite3.connect(GUR) as c:
        df = pd.read_sql("SELECT mmsi, imo, name AS gur_nom FROM vessels", c)
    df["imo"], df["mmsi"] = df.imo.map(digits), df.mmsi.map(digits)
    return df


def load_os():
    if not OS_CSV.exists():
        return pd.DataFrame(columns=["mmsi", "imo", "os_nom", "os_risques", "os_sources", "os_url", "os_sanction"])
    df = pd.read_csv(OS_CSV, dtype=str).fillna("")
    df = df[df["type"].str.upper() == "VESSEL"]
    out = pd.DataFrame({
        "imo": df.imo.map(digits), "mmsi": df.mmsi.map(digits), "os_nom": df.caption,
        "os_risques": df.risk, "os_sources": df.datasets, "os_url": df.url,
    })
    out["os_sanction"] = out.os_risques.str.contains("sanction", case=False)
    return out


def match(seen, wl, cols):
    """Rattache chaque navire vu à une entrée de liste : par OMI, sinon par MMSI."""
    a = seen.dropna(subset=["imo"]).merge(wl.dropna(subset=["imo"])[["imo"] + cols], on="imo")
    a["par"] = "OMI"
    b = seen[~seen.mmsi.isin(a.mmsi)].merge(wl.dropna(subset=["mmsi"])[["mmsi"] + cols], on="mmsi")
    b["par"] = "MMSI"
    return pd.concat([a, b], ignore_index=True).drop_duplicates("mmsi")


def main():
    if not (LIVE / "positions").exists():
        raise SystemExit("Aucune collecte : lancer d'abord scripts/ais_live.py collect")
    gur, os_ = load_gur(), load_os()
    print(f"Listes chargées : GUR {len(gur)} navires, OpenSanctions {len(os_)} navires "
          f"(dont {int(os_.os_sanction.sum())} sanctionnés)")

    pos = pd.read_parquet(LIVE / "positions")
    st = pd.read_parquet(LIVE / "statiques") if (LIVE / "statiques").exists() else pd.DataFrame(columns=["mmsi", "imo", "name"])
    pos["mmsi"], st["mmsi"], st["imo"] = pos.mmsi.map(digits), st.mmsi.map(digits), st.imo.map(digits)
    pos["t"] = pd.to_datetime(pos.ts.astype(str).str.split(" +", regex=False).str[0], utc=True, errors="coerce")

    ident = (st.dropna(subset=["imo"]).groupby("mmsi").agg(imo=("imo", "last"))
             .join(st.dropna(subset=["name"]).groupby("mmsi").agg(nom_declare=("name", "last")), how="outer"))
    seen = pd.DataFrame({"mmsi": pos.mmsi.dropna().unique()}).set_index("mmsi").join(ident).reset_index()

    g = match(seen, gur, ["gur_nom"]).rename(columns={"par": "gur_par"})
    o = match(seen, os_, ["os_nom", "os_risques", "os_sources", "os_url", "os_sanction"]).rename(columns={"par": "os_par"})
    hits = seen.merge(g[["mmsi", "gur_nom", "gur_par"]], on="mmsi", how="left") \
               .merge(o[["mmsi", "os_nom", "os_risques", "os_sources", "os_url", "os_sanction", "os_par"]], on="mmsi", how="left")
    hits = hits[hits.gur_nom.notna() | hits.os_nom.notna()].copy()
    if hits.empty:
        print(f"Aucun navire des listes parmi les {len(seen)} navires collectés.")
        return

    def signal(r):
        sanction = bool(r.os_sanction) if pd.notna(r.os_sanction) else False
        shadow = isinstance(r.os_risques, str) and "mare.shadow" in r.os_risques
        if pd.notna(r.gur_nom) and (sanction or shadow):
            return "fort"
        if sanction:
            return "sanctionné"
        if shadow:
            return "flotte fantôme"
        if pd.notna(r.gur_nom):
            return "suspect GUR"
        return "autre risque"

    rows, features = [], []
    for h in hits.itertuples():
        p = pos[pos.mmsi == h.mmsi].sort_values("t")
        last = p.iloc[-1]
        sig = signal(h)
        par = "OMI" if "OMI" in (h.gur_par, h.os_par) else "MMSI seul (moins sûr)"
        rows.append({
            "signal": sig, "nom": h.gur_nom if pd.notna(h.gur_nom) else h.os_nom, "nom_declare": h.nom_declare,
            "omi": h.imo, "mmsi": h.mmsi, "reconnu_par": par, "zone": str(last.zone),
            "liste_gur": "oui" if pd.notna(h.gur_nom) else "non",
            "risques_opensanctions": h.os_risques if pd.notna(h.os_risques) else "",
            "dernier_vu": p.t.max(), "positions": len(p),
            "vitesse_moyenne_noeuds": round(p.sog.mean(), 1) if p.sog.notna().any() else None,
            "derniere_lat": round(last.lat, 4), "derniere_lon": round(last.lon, 4),
            "sources_opensanctions": h.os_sources if pd.notna(h.os_sources) else "",
            "fiche_opensanctions": h.os_url if pd.notna(h.os_url) else "",
        })
        features.append({"type": "Feature", "properties": {"signal": sig, "nom": rows[-1]["nom"], "omi": h.imo, "mmsi": h.mmsi},
                         "geometry": {"type": "LineString", "coordinates": p[["lon", "lat"]].values.tolist()}})

    df = pd.DataFrame(rows)
    df = df.sort_values(["signal", "zone", "dernier_vu"], key=lambda s: s.map(RANK) if s.name == "signal" else s)
    df.to_csv(LISTS / "correspondances.csv", index=False)
    (LISTS / "traces_surveillance.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    print(f"{len(df)} navire(s) des listes parmi {len(seen)} navires collectés :\n")
    show = ["signal", "nom", "nom_declare", "omi", "reconnu_par", "zone", "liste_gur", "risques_opensanctions", "positions"]
    print(df[show].to_string(index=False))
    print(f"\nDétail : {LISTS / 'correspondances.csv'} ; trajectoires : {LISTS / 'traces_surveillance.geojson'}")


if __name__ == "__main__":
    main()
