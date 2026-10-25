"""Essais réels des satellites (lot Z6) : `taches.py essai-reel` prépare, `taches.py essai-reel --lancer ID` lance.

La préparation ne lance rien et ne coûte rien : travailleurs encore en vie (aucun ne doit tourner avant un essai),
budget des essais (`essai_reel.budget_eur`, 8 € HT) et budget Sentinel du mois, pause de chaque source, clés et
réseau privé, types de calcul de chaque tâche avec leur état « éprouvé » (au moins un travailleur abouti sur ce type
et cette zone), passages candidats. Une ligne par point : PRÊT, MANQUE ou INFO.

Le lancement porte sur un seul passage (`sar_passes.id`, Sentinel 1 ou 2), par la commande manuelle existante, avec
trois gardes de plus :
* aucun travailleur en vie, de quelque tâche que ce soit ;
* budget des essais non épuisé (coût des travailleurs lancés par `essai-reel`, réel une fois détruits) ;
* aucun type jamais éprouvé : pendant un essai, `travailleurs.launch` refuse tout type qui n'a jamais abouti (le POP2
  de secours, jamais lancé, est donc refusé ; un GP1-S en rupture laisse l'analyse en échec, sans repli inconnu).
Le passage en pause n'est pas repris : le lancement passe « malgré la pause », consigné dans le journal du panneau ;
la boucle ne reprend rien d'elle même (une source en pause ne lance aucun lot suivant). Chaque essai est consigné
dans task_runs (tâche essai_reel), tracé de bout en bout comme toute analyse."""
import os
from datetime import datetime

from mars import controle, travailleurs

PRET, MANQUE, INFO = "PRÊT", "MANQUE", "INFO"
MISSIONS = {"S1": ("sentinel1", "sentinel"), "S2": ("sentinel2", "sentinel2")}       # source du panneau, tâche
VERIFICATION_SQL = ("SELECT id, tache, etat, commercial_type, zone, cree_le FROM travailleurs "
                    "WHERE detruit_le IS NULL ORDER BY id")


def actifs(conn) -> list[tuple]:
    """Travailleurs encore en vie (non détruits) : aucun ne doit tourner avant ni après un essai."""
    return conn.execute(VERIFICATION_SQL).fetchall()


def depense_essais(conn) -> tuple[float, list[int]]:
    """Coût des travailleurs lancés par essai-reel : réel s'il est connu, estimé sinon, prévu à défaut."""
    ids = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT (details->>'travailleur')::bigint FROM task_runs WHERE task = 'essai_reel' "
        "AND details ? 'travailleur' AND details->>'travailleur' ~ '^[0-9]+$'").fetchall()]
    if not ids:
        return 0.0, []
    cout = conn.execute(
        "SELECT coalesce(sum(coalesce(cout_reel_eur, (mesures->>'cout_estime_eur')::float8, cout_prevu_eur, 0)), 0) "
        "FROM travailleurs WHERE id = ANY(%s)", (ids,)).fetchone()[0]
    return round(float(cout), 2), ids


def types_essai(rules: dict, ok: set) -> list[dict]:
    """Types de calcul de Sentinel 1 et 2 dans l'ordre de la liste, avec leur prix, leur facturation et leur état."""
    out = []
    for source, tache in MISSIONS.values():
        for v in travailleurs.variantes(rules, tache):
            t = travailleurs.type_params(rules, tache, v)
            zone = t.get("zone", rules["travailleurs"]["zone"])
            out.append({"source": source, "type": t["commercial_type"], "zone": zone, "prix_heure": t["prix_heure"],
                        "facturation": t["facturation"], "eprouve": (t["commercial_type"], zone) in ok})
    return out


def preparation(conn, rules: dict, now: datetime, env: dict | None = None) -> list[tuple[str, str, str]]:
    env = os.environ if env is None else env
    lignes = []
    vivants = actifs(conn)
    lignes.append((PRET, "Travailleurs en vie", "aucun") if not vivants else
                  (MANQUE, "Travailleurs en vie", " ; ".join(f"{w[0]} {w[1]} {w[3]} ({w[2]})" for w in vivants)
                   + " : attendre leur destruction (taches.py travailleurs)"))
    budget = float((rules.get("essai_reel") or {}).get("budget_eur", 8.0))
    dep, ids = depense_essais(conn)
    lignes.append((PRET if dep < budget else MANQUE, "Budget des essais",
                   f"{dep:.2f} € dépensés sur {budget:.2f} € HT ({len(ids)} travailleur(s) lancé(s) par essai-reel)"
                   .replace(".", ",")))
    b = controle.budget(conn, rules, now)["budget_sentinel"]
    lignes.append((PRET if b["restant"] > 0.3 else MANQUE, "Budget Sentinel du mois",
                   f"{b['engage']:.2f} € engagés sur {b['plafond']:.2f} €, {b['restant']:.2f} € restants"
                   .replace(".", ",")))
    for source in ("sentinel1", "sentinel2", "viirs", "tip_and_cue"):
        m = controle.en_pause(rules, source)
        lignes.append((INFO, f"{controle.NOMS[source]}", m or "en service"))
    manque = [k for k in ("SCW_SECRET_KEY", "SCW_PROJECT_ID", "SCW_PRIVATE_NETWORK_ID", "MARS_IP_PRIVEE",
                          "CDSE_TRAVAILLEUR_ACCESS_KEY", "CDSE_TRAVAILLEUR_SECRET_KEY") if not (env.get(k) or "").strip()]
    lignes.append((MANQUE, "Clés et réseau privé", "absentes du conteneur : " + ", ".join(manque)) if manque else
                  (PRET, "Clés et réseau privé", "présentes (valeurs jamais affichées)"))
    from mars import echeances
    for e in echeances.etat(dict(env), now.date()):
        if e["variable"].startswith("CDSE_TRAVAILLEUR"):
            if e["expire_le"] is None:
                lignes.append((INFO, e["libelle"], f"échéance non déclarée ({e['variable']})"))
            else:
                lignes.append((MANQUE if e["jours"] < 0 else PRET, e["libelle"], echeances.message(e)))
    for t in types_essai(rules, travailleurs.eprouves(conn)):
        mode = "à l'heure entamée" if t["facturation"] == "heure_entamee" else "à la minute"
        lignes.append((INFO, f"{controle.NOMS[t['source']]} : {t['type']} en {t['zone']}",
                       (f"{t['prix_heure']:.4f} €/h {mode}, ".replace(".", ",")
                        + ("éprouvé" if t["eprouve"] else "jamais éprouvé : refusé pendant un essai"))))
    return lignes


def verifier_lancement(conn, rules: dict, pass_id: int) -> tuple[str | None, str | None]:
    """(mission, motif du refus) avant tout appel à Scaleway."""
    r = conn.execute("SELECT mission FROM sar_passes WHERE id = %s", (pass_id,)).fetchone()
    if not r:
        return None, f"passage {pass_id} inconnu (table sar_passes)"
    if r[0] not in MISSIONS:
        return r[0], f"passage {pass_id} : mission {r[0]}, essai réel pour Sentinel 1 ou 2 seulement"
    vivants = actifs(conn)
    if vivants:
        return r[0], ("travailleur(s) encore en vie : " + ", ".join(f"{w[0]} ({w[1]}, {w[2]})" for w in vivants)
                      + " ; un essai à la fois, attendre leur destruction")
    budget = float((rules.get("essai_reel") or {}).get("budget_eur", 8.0))
    dep, _ids = depense_essais(conn)
    if dep >= budget:
        return r[0], f"budget des essais épuisé : {dep:.2f} € sur {budget:.2f} € HT".replace(".", ",")
    return r[0], None


def refus_lancement(r: dict) -> str | None:
    """Motif quand le lancement n'a créé aucun travailleur : refus, pause, travailleur ou passage occupé, rien à
    analyser. None quand un travailleur est lancé (ou mis en attente d'un processeur, sans coût)."""
    if r.get("travailleur") or r.get("attente_gpu"):
        return None
    if r.get("occupe"):
        return f"travailleur {r['occupe']} encore en vie, un seul à la fois"
    if r.get("refus"):
        return str(r["refus"])
    if r.get("en_pause"):
        return f"source en pause : {r['en_pause']}"
    if r.get("en_cours") is not None:
        e = r["en_cours"]
        return e if isinstance(e, str) else f"travailleur {e} encore en vie, un seul à la fois"
    return "aucune analyse lancée : " + (", ".join(f"{k} {v}" for k, v in r.items() if k not in ("passage", "mission"))
                                        or "rien à analyser sur ce passage")


def rapport(lignes: list[tuple[str, str, str]]) -> str:
    out = [f"{s:<7} {r} : {d}" for s, r, d in lignes]
    n = sum(1 for s, _r, _d in lignes if s == MANQUE)
    out.append("Prêt pour un essai : rien ne manque." if not n else f"{n} point(s) manquant(s) : aucun essai possible.")
    return "\n".join(out)
