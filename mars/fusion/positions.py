"""Position AIS de chaque navire à l'instant d'un passage radar (interpolation entre deux messages, sinon estime
courte). Indépendant de la source AIS ; utilisé par le moteur de fusion."""
import numpy as np
import pandas as pd


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
