"""Position AIS de chaque navire à l'instant d'un passage radar (interpolation entre deux messages, sinon estime
courte, sinon dernière position d'un navire à l'arrêt). Indépendant de la source AIS ; utilisé par le moteur de fusion.

Navire à l'arrêt (vitesse déclarée sous `arret_kn`) : sa dernière position avant l'instant reste candidate pendant
`arret_max_min` minutes, si aucune position plus récente ne la contredit (navire reparti ou déplacé), avec une
tolérance accrue de `arret_derive_m_par_min` par minute d'âge. Mesure du 25/10/2026 sur les données du serveur
(300 navires à l'arrêt, 23 et 24/10, 30 525 paires de positions après un point à l'arrêt) : intervalle médian de
12 min (allègement à un point toutes les dix minutes, réception partielle), 99e centile 60 min ; position suivante
encore à moins de 300 m dans 99,5 % des cas jusqu'à 60 min, 96,6 % de 90 à 120 min, 93,6 % de 120 à 180 min ;
95e centile de la distance 144 m à 120 min. D'où 120 min et 2,5 m par minute (600 m à 2 h au delà des 300 m de base).
"""
import numpy as np
import pandas as pd

from mars.fusion.arret import KNOT, maintien_arret  # noqa: F401  (réexport)


def positions_at(pos: pd.DataFrame, t0: pd.Timestamp, max_gap: pd.Timedelta, arret: dict | None = None) -> pd.DataFrame:
    """Position estimée de chaque navire à t0 : interpolation entre deux messages, sinon estime courte, sinon (avec
    `arret`) dernière position d'un navire à l'arrêt. Colonnes ajoutées : method, age_min (âge de la position
    retenue), tolerance_extra_m (accroissement de la tolérance avec l'âge, navire à l'arrêt)."""
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
            if near["gap"] <= max_gap:
                age = abs((t0 - near.ts).total_seconds()) / 60
                if np.isnan(near.sog) or np.isnan(near.cog):
                    row = {"lat": near.lat, "lon": near.lon, "sog": near.sog, "cog": near.cog, "method": "plus proche"}
                else:
                    d = near.sog * KNOT * (t0 - near.ts).total_seconds()
                    row = {"lat": near.lat + d * np.cos(np.radians(near.cog)) / 111_320,
                           "lon": near.lon + d * np.sin(np.radians(near.cog)) / (111_320 * np.cos(np.radians(near.lat))),
                           "sog": near.sog, "cog": near.cog, "method": "estime"}
                row["age_min"] = round(age, 1)
            elif arret:
                pts = [{"ts": r.ts, "lon": r.lon, "lat": r.lat, "sog": None if pd.isna(r.sog) else float(r.sog)}
                       for r in g.itertuples()]
                m = maintien_arret(pts, t0, arret)
                if m is None:
                    continue
                b, age = m
                row = {"lat": b["lat"], "lon": b["lon"], "sog": b["sog"], "cog": np.nan, "method": "arret",
                       "age_min": round(age, 1), "tolerance_extra_m": round(arret["derive_m_par_min"] * age, 1)}
            else:
                continue
        row.update({"vessel_id": vid, "mmsi": g["mmsi"].iloc[0], "name": g["name"].iloc[0],
                    "length_m": g["length_m"].iloc[0]})
        out.append(row)
    df = pd.DataFrame(out)
    if len(df) and "tolerance_extra_m" not in df:
        df["tolerance_extra_m"] = 0.0
    if len(df):
        df["tolerance_extra_m"] = df["tolerance_extra_m"].fillna(0.0)
    return df
