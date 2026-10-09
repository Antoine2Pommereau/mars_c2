"""AIS en direct (AISStream) : zones collectées, nettoyage des messages et allègement des trajectoires.

Fonctions pures, sans base de données, partagées par la collecte (scripts/ais_live.py) et l'ingestion
(mars/ais/ingest.py), et couvertes par tests/test_live.py.
"""
import math
import re
from dataclasses import dataclass

import pandas as pd

# Zones : [lat_min, lon_min, lat_max, lon_max]
# Origine des données AIS, inscrite dans chaque message de l'archive et chaque position en base : prépare un
# changement de fournisseur (Spire, récepteur personnel…) et distingue les périodes dans les calibrations.
SOURCE = "aisstream"

ZONES = {
    "bretagne": [47.3, -6.8, 49.6, -3.0],
    "mediterranee": [41.2, 3.0, 43.7, 9.8],
    "manche": [48.4, -5.0, 51.2, 2.6],
    "gascogne": [43.3, -6.0, 47.4, -1.0],
}


def zones_extent() -> tuple[float, float, float, float]:
    """Emprise de l'ensemble des zones : lon_min, lat_min, lon_max, lat_max."""
    z = list(ZONES.values())
    return min(b[1] for b in z), min(b[0] for b in z), max(b[3] for b in z), max(b[2] for b in z)


def zones_wkt() -> str:
    """Zones collectées en multipolygone WKT (rectangles éventuellement superposés : l'union se fait en SQL)."""
    rings = [f"(({b} {a},{d} {a},{d} {c},{b} {c},{b} {a}))" for a, b, c, d in ZONES.values()]
    return "MULTIPOLYGON(" + ",".join(rings) + ")"


def parse_times(s: pd.Series) -> pd.Series:
    """Format AISStream : '2026-10-05 12:39:19.104049717 +0000 UTC'. La partie décimale a une longueur variable
    (les zéros finaux sont omis) : sans format ISO explicite, pandas déduit le format de la première ligne et rejette
    silencieusement les autres."""
    return pd.to_datetime(s.astype(str).str.split(" +", regex=False).str[0], utc=True, errors="coerce",
                          format="ISO8601").dt.floor("us")       # PostgreSQL s'arrête à la microseconde


# Libellés de type au format de la Danish Maritime Authority : les règles (navires de service exclus) et la carte
# (couleur par type) les utilisent déjà.
_TYPE_RANGES = [(20, 29, "WIG"), (40, 49, "HSC"), (60, 69, "Passenger"), (70, 79, "Cargo"), (80, 89, "Tanker"),
                (90, 99, "Other")]
_TYPE_CODES = {30: "Fishing", 31: "Towing", 32: "Towing long/wide", 33: "Dredging", 34: "Diving", 35: "Military",
               36: "Sailing", 37: "Pleasure", 50: "Pilot", 51: "SAR", 52: "Tug", 53: "Port tender",
               54: "Anti-pollution", 55: "Law enforcement", 56: "Other", 57: "Other", 58: "Medical", 59: "Other"}


def ship_type_label(code) -> str | None:
    if code is None or (isinstance(code, float) and math.isnan(code)):
        return None
    code = int(code)
    if code in _TYPE_CODES:
        return _TYPE_CODES[code]
    for lo, hi, label in _TYPE_RANGES:
        if lo <= code <= hi:
            return label
    return None          # 0 (non renseigné) et codes réservés


def valid_imo(v) -> int | None:
    """Numéro OMI à sept chiffres dont la clé est juste, sinon None (l'AIS transporte souvent 0 ou un nombre
    quelconque dans ce champ). Clé : somme des six premiers chiffres pondérés de 7 à 2, modulo 10."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        digits = re.sub(r"\D", "", str(v))
        if not digits:
            return None
        n = int(digits)
    if not 1_000_000 <= n <= 9_999_999:
        return None
    d = [int(c) for c in str(n)]
    return n if sum(x * w for x, w in zip(d[:6], range(7, 1, -1))) % 10 == d[6] else None


def clean_text(v) -> str | None:
    """Champ texte AIS : le caractère @ sert de remplissage ; chaîne vide devient None."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = re.sub(r"\s+", " ", str(v).replace("@", " ")).strip()
    return s or None


def clean_positions(df: pd.DataFrame, now: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Nettoie un lot de positions AISStream. Renvoie les positions triées par navire et par instant, et le décompte
    des retraits, pour que chaque règle reste traçable."""
    stats = {"lus": len(df)}
    df = df.copy()
    if "source" not in df:                   # fichiers antérieurs au champ « source » : tous issus d'AISStream
        df["source"] = SOURCE
    df["source"] = df["source"].fillna(SOURCE)
    df["t"] = parse_times(df["ts"])
    df["mmsi"] = pd.to_numeric(df["mmsi"], errors="coerce")
    n = len(df)
    df = df[df.mmsi.between(100_000_000, 999_999_999) & df.t.notna()]
    stats["mmsi_ou_instant_invalide"] = n - len(df)
    n = len(df)
    df = df[(df.lat.abs() <= 90) & (df.lon.abs() <= 180) & (df.t <= now + pd.Timedelta(minutes=5))]
    stats["position_ou_instant_impossible"] = n - len(df)
    df.loc[df.sog >= 102.2, "sog"] = float("nan")        # 102,3 : vitesse non disponible
    df.loc[df.cog >= 360, "cog"] = float("nan")          # 360 : route non disponible
    df["heading"] = pd.to_numeric(df.heading, errors="coerce")
    df.loc[df.heading >= 360, "heading"] = float("nan")  # 511 : cap non disponible
    df["nav_status"] = pd.to_numeric(df.nav_status, errors="coerce")
    df.loc[df.nav_status >= 15, "nav_status"] = float("nan")   # 15 : non défini
    df["mmsi"] = df.mmsi.astype("int64")
    n = len(df)
    df = df.drop_duplicates(subset=["mmsi", "t"]).sort_values(["t", "mmsi"])
    stats["doublons"] = n - len(df)
    return df, stats


@dataclass
class _Last:
    t: pd.Timestamp
    stopped: bool
    nav_status: float | None


class Thinner:
    """Allègement des trajectoires : un point par minute en route, un point toutes les dix minutes à l'arrêt.

    Un point est aussi conservé à chaque passage de la route à l'arrêt (et inversement) et à chaque changement de
    statut de navigation, pour que le début d'un arrêt soit daté à la minute près. L'arrêt se décide avec une
    hystérésis (arrêté sous stopped_kn, en route au delà de moving_kn, état précédent entre les deux) : un navire au
    mouillage qui évite sur son ancre autour de 0,5 nœud ne bascule pas à chaque message. Un message plus ancien que
    le dernier point conservé du navire est écarté (arrivée tardive)."""

    def __init__(self, moving_s=60, stopped_s=600, stopped_kn=0.5, moving_kn=1.0):
        self.moving_s, self.stopped_s = moving_s, stopped_s
        self.stopped_kn, self.moving_kn = stopped_kn, moving_kn
        self.last: dict[int, _Last] = {}

    def _stopped(self, sog, nav_status, prev: _Last | None) -> bool:
        if sog is None or math.isnan(sog):
            if nav_status is not None and not math.isnan(nav_status):
                return int(nav_status) in (1, 5)       # au mouillage, amarré
            return prev.stopped if prev else False
        if sog < self.stopped_kn:
            return True
        if sog > self.moving_kn:
            return False
        return prev.stopped if prev else False

    def seed(self, mmsi: int, t: pd.Timestamp, sog, nav_status):
        """État initial depuis la base, au redémarrage."""
        self.last[mmsi] = _Last(t, self._stopped(sog, nav_status, None), nav_status)

    def keep(self, mmsi: int, t: pd.Timestamp, sog, nav_status) -> tuple[bool, str]:
        prev = self.last.get(mmsi)
        stopped = self._stopped(sog, nav_status, prev)
        if prev is None:
            reason = "premier"
        elif t <= prev.t:
            return False, "en_retard"
        elif stopped != prev.stopped:
            reason = "bascule"
        elif _status_changed(nav_status, prev.nav_status):
            reason = "statut"
        elif (t - prev.t).total_seconds() >= (self.stopped_s if stopped else self.moving_s):
            reason = "intervalle"
        else:
            return False, "allege"
        self.last[mmsi] = _Last(t, stopped, nav_status)
        return True, reason


def _status_changed(a, b) -> bool:
    na = a is None or (isinstance(a, float) and math.isnan(a))
    nb = b is None or (isinstance(b, float) and math.isnan(b))
    return not na and not nb and int(a) != int(b)
