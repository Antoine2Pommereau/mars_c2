"""Zone de réception fiable provisoire : critère d'évaluation des détections satellites, coupures AIS en observation,
rapport de mesures. Les tests en base demandent MARS_TEST_DATABASE_URL."""
import sys
from pathlib import Path

from mars.config import load_rules
from mars.evaluation import CRITERE_RAYON, CRITERE_ZONE, critere, motif_non_evaluable
from mars.rules import OBSERVATION_MAX, observation_summary

RULES = load_rules()
V = RULES["viirs"]


def test_reception_settings_are_the_provisional_ones():
    z = RULES["reception"]
    assert (z["max_interval_s"], z["min_coverage"], z["min_pairs"], z["min_vessels"]) == (600, 0.90, 50, 3)
    assert RULES["ais_gap"]["mode"] == "observation"


def test_zone_replaces_the_radius_criterion_once_built():
    # Zone pas encore construite : critère provisoire des navires reçus dans le rayon
    assert motif_non_evaluable(False, False, False, 0, V).startswith("aucun navire AIS reçu")
    assert motif_non_evaluable(False, False, False, 2, V) is None
    # Zone construite : elle seule décide, quel que soit le nombre de navires alentour
    assert motif_non_evaluable(False, True, True, 0, V) is None
    assert motif_non_evaluable(False, True, False, 12, V) == "hors de la zone de réception AIS fiable"
    # Une coupure du flux passe avant tout
    assert motif_non_evaluable(True, True, True, 12, V) == "coupure du flux AIS à l'heure du passage"
    assert critere(True) == CRITERE_ZONE and critere(False) == CRITERE_RAYON


def _item(k, dur, sev):
    return {"severity": sev, "details": {"navire": {"vessel_id": k, "mmsi": 227000000 + k}, "dernier_message": "t",
                                         "duree_min": dur, "reapparition": None if k % 2 else "t2",
                                         "comportement": None, "messages_heure_precedente": 12,
                                         "derniere_position": [-5, 48]}}


def test_observation_summary_counts_without_alerts_and_stays_small():
    items = [_item(k, 120 + k, "moyenne" if k % 3 else "elevee") for k in range(OBSERVATION_MAX + 10)]
    out = observation_summary(items)
    assert sum(out["gravites_hypothetiques"].values()) == len(items)
    assert len(out["episodes"]) == OBSERVATION_MAX
    assert out["episodes"][0]["duree_min"] == 120 + OBSERVATION_MAX + 9          # les plus longs d'abord
    assert out["episodes"][1]["reapparition"] is True


def test_report_gives_evaluable_share_before_and_after():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from mesures_calibration import evaluability_rows

    ev = [("bretagne", "sans_ais", 0, False, True),          # personne alentour, mais dans la zone : évaluable après
          ("bretagne", "non_evaluable", 3, False, False),    # navires alentour, hors zone : évaluable avant seulement
          ("bretagne", "non_evaluable", 3, True, True),      # coupure du flux : jamais
          ("bretagne", "avec_ais", 5, False, True)]          # appariée : hors du décompte
    rows = evaluability_rows({"viirs": {"evaluation": ev, "zone": True}}, RULES)
    assert rows == [["Bretagne", "3", "33 %", "33 %"]]
    rows = evaluability_rows({"viirs": {"evaluation": ev, "zone": False}}, RULES)
    assert rows[0][3] == "zone non construite"
