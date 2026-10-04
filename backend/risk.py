"""Score de risque par navire : somme pondérée et auditable des alertes (voir le dossier navire).

Chaque alerte contribue poids_type x facteur_severite x facteur_statut x decote_anciennete. Une alerte
classée (écartée par l'opérateur) compte pour zéro. S'ajoutent un bonus de récurrence sur les jours
distincts et un petit terme d'anomalie d'identité. Le tout est borné à [0, 100]. Rien n'est opaque :
chaque point remonte à une alerte précise, renvoyée ligne par ligne dans contributions.
"""
import math


def band(score: int) -> str:
    if score <= 0:
        return "neutre"
    if score < 25:
        return "faible"
    if score < 50:
        return "a_surveiller"
    if score < 75:
        return "eleve"
    return "prioritaire"


def score_vessel(alerts: list[dict], anomalies: dict, cfg: dict) -> dict:
    """alerts : dicts avec id, type, severity, status, age_jours, jour (texte ou date).
    anomalies : dict de drapeaux booléens d'identité. cfg : section risk_score de rules.yaml."""
    w, sev, st = cfg["type_weights"], cfg["severity_factor"], cfg["status_factor"]
    contributions, jours = [], set()
    for a in alerts:
        s_fac = st.get(a["status"], 1.0)
        if s_fac == 0.0:            # alerte classée : listée ailleurs, mais ne pèse pas
            continue
        decote = min(1.0, max(0.15, 0.5 ** (a["age_jours"] / cfg["half_life_days"])))
        pts = w.get(a["type"], 0) * sev.get(a["severity"], 0.4) * s_fac * decote
        contributions.append({"alert_id": a["id"], "type": a["type"], "severity": a["severity"],
                              "status": a["status"], "jour": a["jour"], "points": round(pts, 1)})
        jours.add(a["jour"])
    bonus = cfg["recurrence_coef"] * math.log(1 + len(jours))
    n_anom = sum(1 for k in cfg["identity_flags"] if anomalies.get(k))
    terme_id = min(cfg["identity_cap"], cfg["identity_point"] * n_anom)
    total = min(100, round(sum(c["points"] for c in contributions) + bonus + terme_id))
    return {"score": total, "bande": band(total), "contributions": contributions,
            "bonus_recurrence": round(bonus, 1), "jours_distincts": len(jours),
            "terme_identite": round(terme_id, 1)}
