"""Lecture et nettoyage des fichiers AIS journaliers de la Danish Maritime Authority."""
import numpy as np
import pandas as pd

USECOLS = ["# Timestamp", "Type of mobile", "MMSI", "Latitude", "Longitude", "Navigational status", "SOG", "COG",
           "Heading", "Name", "Ship type", "Length"]
COLUMNS = ["mmsi", "ts", "lat", "lon", "sog", "cog", "heading", "nav_status", "name", "ship_type", "length", "ais_class"]

# Codes normalisés du statut de navigation AIS, à partir des libellés de la Danish Maritime Authority
NAV_STATUS = [("under way using engine", 0), ("at anchor", 1), ("not under command", 2), ("restricted", 3),
              ("constrained", 4), ("moored", 5), ("aground", 6), ("engaged in fishing", 7), ("sailing", 8)]


def nav_status_code(label) -> float:
    if not isinstance(label, str):
        return np.nan
    low = label.lower()
    for key, code in NAV_STATUS:
        if key in low:
            return code
    return np.nan


def read_dma_csv(path, bbox, margin: float = 0.2, chunksize: int = 2_000_000):
    """Lit le fichier par blocs, garde la zone élargie et nettoie les messages.

    Renvoie (positions nettoyées, statistiques de nettoyage), pour que chaque règle soit traçable.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    stats = {"lus": 0, "dans_la_zone": 0}
    parts = []
    for chunk in pd.read_csv(path, usecols=USECOLS, chunksize=chunksize, low_memory=False):
        stats["lus"] += len(chunk)
        c = chunk[chunk.Latitude.between(lat_min - margin, lat_max + margin)
                  & chunk.Longitude.between(lon_min - margin, lon_max + margin)]
        if len(c):
            parts.append(c)
    if not parts:
        return pd.DataFrame(columns=COLUMNS), stats
    df = pd.concat(parts, ignore_index=True)
    stats["dans_la_zone"] = len(df)

    n = len(df)
    df = df[df["Type of mobile"].isin(["Class A", "Class B"])]
    stats["retires_hors_classes_A_B"] = n - len(df)

    n = len(df)
    df = df[df.MMSI.between(100_000_000, 999_999_999)]
    stats["retires_mmsi_invalide"] = n - len(df)

    n = len(df)
    df = df[(df.Latitude.abs() <= 90) & (df.Longitude.abs() <= 180)].copy()
    stats["retires_position_non_disponible"] = n - len(df)

    df["ts"] = pd.to_datetime(df["# Timestamp"], format="%d/%m/%Y %H:%M:%S", utc=True)
    stats["vitesse_non_disponible"] = int((df.SOG >= 102.2).sum())    # 102,3 nœuds
    stats["route_non_disponible"] = int((df.COG >= 360).sum())        # 360
    stats["cap_non_disponible"] = int((df.Heading >= 360).sum())      # 511
    df.loc[df.SOG >= 102.2, "SOG"] = np.nan
    df.loc[df.COG >= 360, "COG"] = np.nan
    df.loc[df.Heading >= 360, "Heading"] = np.nan

    df["nav_status"] = df["Navigational status"].map(nav_status_code)
    df["ais_class"] = df["Type of mobile"].str.replace("Class ", "", regex=False)
    df = df.rename(columns={"MMSI": "mmsi", "Latitude": "lat", "Longitude": "lon", "SOG": "sog", "COG": "cog",
                            "Heading": "heading", "Name": "name", "Ship type": "ship_type", "Length": "length"})
    n = len(df)
    df = df.drop_duplicates(subset=["mmsi", "ts", "lat", "lon"]).sort_values(["mmsi", "ts"])
    stats["retires_doublons"] = n - len(df)

    n = len(df)
    df = _drop_jumps(df)
    stats["retires_sauts_impossibles"] = n - len(df)
    stats["conserves"] = len(df)
    return df[COLUMNS], stats


def _drop_jumps(df: pd.DataFrame, max_speed_kn: float = 50.0) -> pd.DataFrame:
    """Supprime les positions impliquant une vitesse physiquement impossible depuis le message précédent."""
    prev = df.groupby("mmsi")[["lat", "lon", "ts"]].shift()
    dlat = np.radians(df.lat - prev.lat)
    dlon = np.radians(df.lon - prev.lon) * np.cos(np.radians(df.lat))
    dist_m = 6_371_000 * np.sqrt(dlat ** 2 + dlon ** 2)
    dt_s = (df.ts - prev.ts).dt.total_seconds()
    speed_kn = dist_m / dt_s.where(dt_s > 0) / 0.514444
    return df[~(speed_kn > max_speed_kn)]


def positions_at(pos: pd.DataFrame, t0: pd.Timestamp, max_gap: pd.Timedelta) -> pd.DataFrame:
    """Position estimée de chaque navire à t0 : interpolation entre deux messages, sinon estime courte."""
    out = []
    for vid, g in pos.sort_values("ts").groupby("vessel_id"):
        before, after = g[g.ts <= t0].tail(1), g[g.ts > t0].head(1)
        row = None
        if len(before) and len(after):
            b, a = before.iloc[0], after.iloc[0]
            if t0 - b.ts <= max_gap and a.ts - t0 <= max_gap:
                w = (t0 - b.ts) / (a.ts - b.ts)
                row = {"lat": b.lat + w * (a.lat - b.lat), "lon": b.lon + w * (a.lon - b.lon),
                       "sog": np.nanmean([b.sog, a.sog]), "cog": b.cog, "method": "interpolation"}
        if row is None:
            near = g.assign(gap=(g.ts - t0).abs()).sort_values("gap").iloc[0]
            if near["gap"] > max_gap:
                continue
            if np.isnan(near.sog) or np.isnan(near.cog):
                row = {"lat": near.lat, "lon": near.lon, "sog": near.sog, "cog": near.cog, "method": "plus proche"}
            else:
                d = near.sog * 0.514444 * (t0 - near.ts).total_seconds()
                row = {"lat": near.lat + d * np.cos(np.radians(near.cog)) / 111_320,
                       "lon": near.lon + d * np.sin(np.radians(near.cog)) / (111_320 * np.cos(np.radians(near.lat))),
                       "sog": near.sog, "cog": near.cog, "method": "estime"}
        row.update({"vessel_id": vid, "mmsi": g["mmsi"].iloc[0], "name": g["name"].iloc[0],
                    "length_m": g["length_m"].iloc[0]})
        out.append(row)
    return pd.DataFrame(out)
