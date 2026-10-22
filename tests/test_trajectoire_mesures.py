"""Lot T, phase 1 : la commande de mesure lit l'archive R2 (faux client), sans rien écrire ni en base ni sur R2."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from mars import trajectoire as T
from mars import trajectoire_mesures as TM


def positions_jour(jour: date, mmsi: int, lat=47.6, lon=-5.0):
    """Une journée d'un navire en route à 4 nœuds, cap au nord 12 h puis au sud 12 h (il reste dans la zone), une
    position toutes les 30 s, au format de la collecte."""
    t0 = datetime.combine(jour, datetime.min.time(), timezone.utc)
    t = np.arange(0, 24 * 3600, 30)
    la, lo = T.avancer(lat, lon, 0.0, 4 * T.NOEUD * np.minimum(t, 24 * 3600 - t))
    ts = [(t0 + timedelta(seconds=int(s))).strftime("%Y-%m-%d %H:%M:%S.%f000 +0000 UTC") for s in t]
    return pd.DataFrame({"mmsi": mmsi, "ts": ts, "lat": la, "lon": lo, "sog": 4.0, "cog": np.where(t < 12 * 3600, 0.0, 180.0),
                         "heading": 0, "nav_status": 0, "zone": "bretagne"})


class FauxR2:
    def __init__(self, racine: Path):
        self.racine, self.objets = racine, {}

    def poser(self, key: str, df: pd.DataFrame):
        p = self.racine / key.replace("/", "_")
        df.to_parquet(p)
        self.objets[key] = p

    def list(self, prefix):
        return [{"key": k, "bytes": p.stat().st_size, "modified": None} for k, p in self.objets.items() if k.startswith(prefix)]

    def download(self, key, path):
        path.write_bytes(self.objets[key].read_bytes())


class FausseBase:
    def __init__(self):
        self.requetes = []

    def execute(self, sql, _params=()):
        self.requetes.append(sql)
        if "FROM archives" in sql:
            return [("positions", "bretagne", date(2026, 10, 6), 2880)]
        return []                                                  # pas de trait de côte : filtre côtier absent


def test_measures_inventory_trips_and_references_read_only(tmp_path):
    r2 = FauxR2(tmp_path)
    jours = [date(2026, 10, 6) + timedelta(days=k) for k in range(4)]
    for j in jours:
        if j == date(2026, 10, 8):
            continue                                               # journée manquante
        r2.poser(f"ais/positions/zone=bretagne/date={j}/positions_bretagne_{j}_x.parquet", positions_jour(j, 227000001))
    r2.poser("ais/statiques/zone=bretagne/date=2026-10-06/statiques_bretagne_x.parquet",
             pd.DataFrame({"mmsi": [227000001], "ts": ["2026-10-06 00:00:01 +0000 UTC"], "ship_type": [70], "destination": ["BREST"]}))
    base = FausseBase()
    from mars.config import load_rules
    out = TM.mesurer(base, r2, jours[0], jours[-1], ["bretagne"], 1, tmp_path / "travail", log=lambda _m: None,
                     regles=load_rules())
    inv = out["inventaire"]["zones"]["bretagne"]
    assert inv["journees_manquantes"] == ["2026-10-08"]
    z = out["zones"]["bretagne"]
    assert z["heures"]["heures_sans_position"] == 24 and z["heures"]["trous"][0]["heures"] == 24
    assert z["trajets"] >= 2 and z["par_categorie"] == {"cargo": z["trajets"]}
    assert z["fin"].get("silence", 0) >= 1, "la journée manquante coupe le trajet"
    assert z["references"] and out["references"]["cargo_petrolier"][0]["fenetres"] > 0
    assert z["projection"]["jours_pour_le_volume_de_l_article"] > 0
    c = out["cercle_appris"]
    assert len(c["table"]["rayons_m"]) == 4 and len(c["table"]["rayons_m"][0]) == 6 and c["fenetres_test"] > 0
    assert {"actuel", "appris", "appris_plafonne"} <= set(c["gain"][0])
    assert all(s.lstrip().upper().startswith("SELECT") for s in base.requetes), "lecture seule"
    assert not list((tmp_path / "travail").iterdir()), "chaque objet lu est effacé du dossier de travail"
    assert z["positions_reduites"] == 3 * 24 * 6, "une position par tranche de 10 minutes"


def test_reduce_keeps_one_fix_per_ten_minutes_and_clears_unavailable_codes():
    df = positions_jour(date(2026, 10, 6), 227000001).head(40)
    df.loc[0, "sog"], df.loc[1, "cog"] = 102.3, 360.0
    r = TM.reduire(df)
    assert len(r) == 2 and r.mmsi.dtype == "int32" and r.lat.dtype == "float32"
    assert TM.heures_creuses({}, date(2026, 10, 6), date(2026, 10, 6))["heures_sans_position"] == 24


def test_timestamps_with_any_number_of_decimals():
    """AISStream omet les zéros finaux : 9, 6 ou aucune décimale donnent la même seconde ; illisible : -1."""
    s = TM.secondes(["2026-10-06 12:00:01.104049717 +0000 UTC", "2026-10-06 12:00:01.104 +0000 UTC",
                     "2026-10-06 12:00:01 +0000 UTC", "n'importe quoi"])
    attendu = int(datetime(2026, 10, 6, 12, 0, 1, tzinfo=timezone.utc).timestamp())
    assert list(s) == [attendu, attendu, attendu, -1]
