"""AIS en direct : allègement des trajectoires, vérification de l'OMI, pavillon, nettoyage, listes."""
import pandas as pd

from mars.ais.live import Thinner, clean_positions, ship_type_label, valid_imo
from mars.ais.mid import flag_of
from mars.watchlist import watchlist_rows

T0 = pd.Timestamp("2026-10-06 12:00:00", tz="UTC")


def at(s):
    return T0 + pd.Timedelta(seconds=s)


def test_thinning_moving_one_per_minute():
    th = Thinner()
    kept = [th.keep(1, at(s), 12.0, 0)[0] for s in range(0, 600, 10)]   # un message toutes les 10 s pendant 10 min
    assert sum(kept) == 10


def test_thinning_stopped_one_per_ten_minutes():
    th = Thinner()
    kept = [th.keep(1, at(s), 0.1, 1)[0] for s in range(0, 3600, 10)]
    assert sum(kept) == 6


def test_thinning_keeps_transitions_and_status_changes():
    th = Thinner()
    assert th.keep(1, at(0), 12.0, 0) == (True, "premier")
    assert th.keep(1, at(20), 0.2, 0) == (True, "bascule")       # arrêt : daté à la minute près
    assert th.keep(1, at(40), 0.2, 1) == (True, "statut")        # au mouillage
    assert th.keep(1, at(50), 0.2, 1) == (False, "allege")


def test_thinning_hysteresis_at_anchor():
    """Un navire qui évite sur son ancre entre 0,4 et 0,9 nœud ne bascule pas à chaque message."""
    th = Thinner()
    th.keep(1, at(0), 0.1, 1)
    kept = [th.keep(1, at(s), 0.4 if (s // 10) % 2 else 0.9, 1)[0] for s in range(10, 590, 10)]
    assert sum(kept) == 0


def test_thinning_drops_late_messages():
    th = Thinner()
    th.keep(1, at(100), 12.0, 0)
    assert th.keep(1, at(50), 12.0, 0) == (False, "en_retard")


def test_valid_imo():
    assert valid_imo(9074729) == 9074729            # exemple de l'OMI
    assert valid_imo(9289518.0) == 9289518          # lu en flottant depuis le Parquet
    assert valid_imo("IMO9427366") == 9427366
    assert valid_imo(9074728) is None               # clé fausse
    assert valid_imo(0) is None and valid_imo(None) is None and valid_imo(884717900) is None


def test_flag_and_ship_type():
    assert flag_of(227932820) == "FR" and flag_of(273123456) == "RU" and flag_of(352001234) == "PA"
    assert flag_of(992271234) is None and flag_of(2275000) is None          # aide à la navigation, MMSI tronqué
    assert ship_type_label(70) == "Cargo" and ship_type_label(84) == "Tanker" and ship_type_label(52) == "Tug"
    assert ship_type_label(0) is None and ship_type_label(float("nan")) is None


def test_clean_positions_sentinels():
    df = pd.DataFrame({
        "mmsi": [227932820, 227932820, 12345, 227000001], "ts": ["2026-10-06 12:00:00.5 +0000 UTC"] * 2
        + ["2026-10-06 12:00:00 +0000 UTC", "2026-10-06 12:00:01 +0000 UTC"],
        "lat": [48.0, 48.0, 48.0, 91.0], "lon": [-5.0, -5.0, -5.0, -5.0], "sog": [102.3, 102.3, 1.0, 1.0],
        "cog": [360.0, 360.0, 1.0, 1.0], "heading": [511, 511, 1, 1], "nav_status": [15.0, 15.0, 0.0, 0.0],
    })
    out, stats = clean_positions(df, T0)
    assert len(out) == 1 and stats["doublons"] == 1
    assert stats["mmsi_ou_instant_invalide"] == 1 and stats["position_ou_instant_impossible"] == 1
    r = out.iloc[0]
    assert pd.isna(r.sog) and pd.isna(r.cog) and pd.isna(r.heading) and pd.isna(r.nav_status)


def test_watchlist_rows():
    gur = pd.DataFrame({"mmsi": ["273123456"], "imo": ["9289518"], "gur_nom": ["PASIPHAE"]})
    os_ = pd.DataFrame({"mmsi": [""], "imo": ["9074728"], "os_nom": ["X"], "os_risques": ["sanction;mare.shadow"],
                        "os_sources": ["eu_fsf"], "os_url": ["u"], "os_sanction": [True], "os_id": ["id1"]})
    g, o = watchlist_rows(gur, os_)
    assert g[:4] == ("gur", "273123456", 9289518, 273123456)
    assert o[2] is None and o[3] is None             # OMI à clé fausse écarté, pas de MMSI
    assert o[8] is True and o[9] is True             # sanctionné, flotte fantôme
