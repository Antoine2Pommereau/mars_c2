"""Règles en continu : décisions pures des alertes WATCHLIST et IDENTITY_CHANGE, coupures du flux, clés stables."""
from datetime import datetime, timedelta, timezone

import yaml

from mars.config import ROOT
from mars.rules import (imo_changes, imo_severity, military, name_changes, normalize_name, outage_minutes,
                        outage_overlap_min, passages, placeholder_mmsi, stamp, watch_severity, zone_names)

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
CONF = timedelta(hours=6)


def ident(i, name, first_h, last_h, vessel_id=1, mmsi=227806500, imo=None):
    return {"id": i, "vessel_id": vessel_id, "mmsi": mmsi, "name": name, "imo": imo,
            "first_seen": T0 + first_h * H, "last_seen": T0 + last_h * H}


# WATCHLIST

def test_passages_split_on_long_silence():
    times = [T0, T0 + H, T0 + 2 * H, T0 + 20 * H, T0 + 21 * H]
    assert passages(times, timedelta(hours=12)) == [(0, 2), (3, 4)]
    assert passages([], timedelta(hours=12)) == []


def test_watch_severity_and_mmsi_only_downgrade():
    assert watch_severity("fort", "omi") == "critique"
    assert watch_severity("sanctionne", "omi") == "elevee"
    assert watch_severity("suspect_gur", "omi") == "moyenne"
    assert watch_severity("fort", "mmsi") == "elevee"           # reconnu par le MMSI seul : un cran de moins
    assert watch_severity("suspect_gur", "mmsi_omi_different") == "faible"


def test_zone_names_follow_collection_zones():
    assert zone_names([(-5.5, 48.6), (5.3, 43.3)]) == ["bretagne", "mediterranee"]


# IDENTITY_CHANGE

def test_real_name_change_is_reported_after_confirmation():
    rows = [ident(1, "OCEAN STAR", -48, -10), ident(2, "SEA QUEEN", -9, 0)]
    out = name_changes(rows, T0, CONF)
    assert [c["identity_id"] for c in out] == [2] and out[0]["ancien"]["name"] == "OCEAN STAR"
    assert name_changes(rows, T0 - 4 * H, CONF) == []          # pas encore confirmé (moins de 6 h)


def test_alternation_like_mutin_is_never_reported():
    # MUTIN et FS MUTIN alternent : l'ancien nom est revu après l'apparition du nouveau
    rows = [ident(1, "MUTIN", -30, 0), ident(2, "FS MUTIN", -29, -1)]
    assert name_changes(rows, T0, CONF) == []


def test_name_normalisation_ignores_punctuation_and_case():
    assert normalize_name(" sea-queen. ") == normalize_name("SEA QUEEN") == "SEA QUEEN"
    rows = [ident(1, "SEA QUEEN", -48, -10), ident(2, "Sea-Queen.", -9, 0)]
    assert name_changes(rows, T0, CONF) == []


def test_placeholder_mmsi_of_the_navy():
    assert placeholder_mmsi(227000000) and not placeholder_mmsi(227806500)


def test_same_imo_under_another_mmsi_with_flag_change():
    rows = [{"vessel_id": 1, "mmsi": 334891000, "imo": 9289518, "first_seen": T0 - 100 * H, "last_seen": T0 - 50 * H},
            {"vessel_id": 2, "mmsi": 314001078, "imo": 9289518, "first_seen": T0 - 10 * H, "last_seen": T0}]
    (ch,) = imo_changes(rows, T0, CONF)
    assert ch["nouveau"]["mmsi"] == 314001078 and ch["pavillon_change"] and not ch["simultane"]


def test_same_imo_simultaneous_use_and_navy_placeholder_ignored():
    rows = [{"vessel_id": 1, "mmsi": 227100001, "imo": 9074729, "first_seen": T0 - 100 * H, "last_seen": T0},
            {"vessel_id": 2, "mmsi": 227100002, "imo": 9074729, "first_seen": T0 - 10 * H, "last_seen": T0},
            {"vessel_id": 3, "mmsi": 227000000, "imo": 9074729, "first_seen": T0 - 9 * H, "last_seen": T0}]
    (ch,) = imo_changes(rows, T0, CONF)                         # le MMSI générique 227000000 est écarté
    assert ch["nouveau"]["vessel_id"] == 2 and ch["simultane"] and not ch["pavillon_change"]


MIL = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text())["identite"]["militaire"]


def test_military_hints_from_name_type_and_hull_number():
    assert military(["101", "HMS CATTISTOCK"], None, None, 232000001, MIL) == "préfixe HMS"
    assert military(["101"], None, None, 232000001, MIL) == "numéro de coque"
    assert military(["FS MUTIN"], None, None, 227000001, MIL) == "préfixe FS"
    assert military(["MARINE"], "Military", None, 227000001, MIL) == "type militaire déclaré"
    assert military(["X"], None, None, 232002833, MIL) == "MMSI de la Marine"


def test_civilian_names_are_not_military():
    for name in ["F/V L'HORIZON 1", "LE MARIN", "HG35 VENDELBO", "FS", "MSC ANNA", "1001 NIGHTS"]:
        assert military([name], "Fishing", 30, 227806500, MIL) is None, name


def test_imo_change_severity():
    same, flag = {"pavillon_change": False, "simultane": False}, {"pavillon_change": True, "simultane": False}
    assert imo_severity(same, False, None) == "faible"                      # cas de L'HORIZON 1, OMI 8542248
    assert imo_severity({**same, "simultane": True}, False, None) == "moyenne"
    assert imo_severity(flag, False, None) == "elevee"
    assert imo_severity(flag, False, "préfixe HMS") == "faible"
    assert imo_severity(same, True, None) == "elevee"                       # navire des listes : inchangé
    assert imo_severity(flag, True, "préfixe HMS") == "elevee"


# Coupures du flux et clés

def test_feed_outage_minutes_do_not_count_as_silence():
    m = T0.replace(minute=0)
    counts = {m + timedelta(minutes=k): 500 for k in range(240)}
    for k in range(60, 150):                                     # flux coupé 90 minutes
        counts.pop(m + timedelta(minutes=k))
    out = outage_minutes(counts, 0.2)
    assert len(out) == 90
    assert outage_overlap_min(out, m + timedelta(minutes=30), m + timedelta(minutes=180)) == 90
    # Un silence de 150 min dont 90 de flux coupé n'a que 60 min effectives : sous le seuil de 120 min


def test_stamp_is_utc_and_stable():
    paris = timezone(timedelta(hours=2))
    assert stamp(datetime(2026, 10, 6, 14, 5, 7, 123456, tzinfo=paris)) == "20261006T120507"
