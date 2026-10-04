"""Test par injection du score de risque : il doit rester une somme auditable et bornée."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from risk import band, score_vessel

CFG = {
    "type_weights": {"DARK_SHIP": 25, "AIS_GAP": 18, "RENDEZVOUS": 15, "INFRA_THREAT": 22,
                     "IDENTITY_MISMATCH": 20, "AIS_UNCONFIRMED": 10},
    "severity_factor": {"faible": 0.4, "moyenne": 0.7, "elevee": 1.0, "critique": 1.3},
    "status_factor": {"nouvelle": 1.0, "acquittee": 1.0, "confirmee": 1.25, "classee": 0.0},
    "half_life_days": 30, "recurrence_coef": 6, "identity_point": 4, "identity_cap": 12,
    "identity_flags": ["mid_incoherent", "mmsi_hors_format", "longueur_manquante",
                       "imo_manquant_classe_a", "pavillon_manquant"],
}
NO_ANOM = {k: False for k in CFG["identity_flags"]}


def test_single_fresh_alert():
    r = score_vessel([{"id": 1, "type": "DARK_SHIP", "severity": "elevee", "status": "nouvelle",
                       "age_jours": 0.0, "jour": "2024-06-05"}], NO_ANOM, CFG)
    assert r["contributions"][0]["points"] == 25.0
    assert r["jours_distincts"] == 1
    assert r["score"] == round(25 + 6 * math.log(2))   # alerte + bonus de récurrence
    assert r["bande"] == "a_surveiller"


def test_classee_counts_zero():
    r = score_vessel([{"id": 1, "type": "DARK_SHIP", "severity": "critique", "status": "classee",
                       "age_jours": 0.0, "jour": "2024-06-05"}], NO_ANOM, CFG)
    assert r["contributions"] == []
    assert r["jours_distincts"] == 0
    assert r["score"] == 0 and r["bande"] == "neutre"


def test_bounded_to_100():
    alerts = [{"id": i, "type": "DARK_SHIP", "severity": "critique", "status": "confirmee",
               "age_jours": 0.0, "jour": f"2024-06-{i:02d}"} for i in range(1, 12)]
    assert score_vessel(alerts, NO_ANOM, CFG)["score"] == 100


def test_identity_term():
    anom = dict(NO_ANOM); anom["mid_incoherent"] = True; anom["longueur_manquante"] = True
    r = score_vessel([], anom, CFG)
    assert r["terme_identite"] == 8.0   # 4 points par anomalie, deux anomalies
    assert r["score"] == 8


def test_decote_old_alert():
    r = score_vessel([{"id": 1, "type": "DARK_SHIP", "severity": "elevee", "status": "nouvelle",
                       "age_jours": 30.0, "jour": "2024-05-06"}], NO_ANOM, CFG)
    assert r["contributions"][0]["points"] == 12.5   # 25 x 0.5^(30/30)


def test_bands():
    assert band(0) == "neutre" and band(10) == "faible" and band(30) == "a_surveiller"
    assert band(60) == "eleve" and band(90) == "prioritaire"
