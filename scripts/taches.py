"""Tâches planifiées du serveur, dans le conteneur « taches » (docker-compose.serveur.yml) : pas de cron sur
l'hôte, tout redémarre avec la plateforme.

Chaque nuit à TACHES_HEURE (UTC, 02:30 par défaut), dans cet ordre :
  1. archivage : fichiers Parquet des journées terminées compactés (un par zone, type et passage), envoyés sur R2,
     vérifiés, puis supprimés du serveur s'ils sont ingérés et confirmés ;
  2. purge : positions de plus de CONSERVATION_JOURS jours (30), seulement pour les journées archivées ;
  3. sauvegarde : pg_dump compressé vers R2, sans les données de positions, SAUVEGARDES_GARDEES (7) conservées.
Listes de surveillance, après les tâches de la nuit : OpenSanctions chaque jour, GUR chaque semaine (seulement si
Vessels1.db a changé dans son dépôt) ; téléchargement en mémoire, journal des navires ajoutés, retirés ou modifiés
dans task_runs, puis nouveau passage des règles des listes. En cas d'échec, la liste précédente reste en place et la
barre d'état de l'interface le signale.
Calendrier des passages Sentinel 1 et 2, chaque jour : catalogue public de Copernicus Data Space et plans
d'acquisition de l'ESA (métadonnées seulement), puis infrastructures et navires des listes couverts (mars/satellites.py).
Détections nocturnes VIIRS, chaque jour à VIIRS_HEURE (UTC, 06:30 par défaut, après la publication des données de
la nuit) : granules des dernières nuits pas encore traitées (rattrapage automatique), analysées par un travailleur
éphémère Scaleway (mars/travailleurs.py, mars/viirs.py). Chaque minute : surveillance des travailleurs (résultats
traités, instances finies ou trop vieilles détruites, orphelins détruits, exécutions journalisées dans task_runs).
Preuves images (vignettes des détections sur R2), chaque nuit : sans limite si liées à une alerte, 30 jours sinon.
Toutes les 10 minutes : espace disque, alerte sous ALERTE_DISQUE_PCT (15 %), et mesures historisées pour Grafana
(taille de la base, des positions, du dossier de la collecte, dernier fichier Parquet : mesures_disque).
Chaque minute : signes de vie (collecte, taches) et, si Grafana ne répond pas, alertes de secours sur le téléphone
par ntfy pour les trois plus graves (disque, collecte figée, ingestion en échec ou en retard), sans rien écrire en
base (mars/supervision.py). Chaque étape est isolée : une erreur (disque plein) n'arrête ni la boucle ni les alertes.
Toutes les 5 minutes (section continu de config/rules.yaml) : règles comportementales sur le flux en direct
(rendez vous, coupures AIS, navires des listes, changements d'identité), consignées dans task_runs (tâche regles),
puis statistiques du trafic pour la frise de l'interface (mars/frise.py).
Une tâche réussie ne rejoue pas le même jour ; une tâche en échec est retentée une heure plus tard.

Usage :
    python scripts/taches.py                          # boucle (service Docker « taches »)
    python scripts/taches.py tester-alerte            # message d'essai sur le téléphone (ntfy, NTFY_SUJET)
    python scripts/taches.py mesurer-tableaux         # durée de chaque requête des tableaux Grafana (lecture seule)
    python scripts/taches.py archiver | purger | sauvegarder | disque | regles | listes | passages | viirs
    python scripts/taches.py travailleurs             # état des travailleurs, une surveillance tout de suite
    python scripts/taches.py journal-travailleur [ID] # journal envoyé par un travailleur, état Scaleway s'il est muet
    python scripts/taches.py viirs-reevaluer          # détections VIIRS en base réévaluées (gravité, non évaluables)
    python scripts/taches.py sentinel                 # passages Sentinel 1 à analyser maintenant (sélection automatique)
    python scripts/taches.py satellites-pause on|off [sentinel1|sentinel2|viirs|tip_and_cue|tout] [--jusqua DATE]
                                              [--motif TEXTE]   # lot P : pause ou reprise ; sans argument : état
    Commandes viirs, sentinel-analyser, sentinel2-analyser : refusées si la source est en pause, sauf --malgre-pause
    python scripts/taches.py operateur-hacher [NOM]   # lot P : ligne MARS_OPERATEURS du .env (connexion au panneau)
    python scripts/taches.py controle-diagnostic      # lignes MARS_OPERATEURS lues par taches et par l'API (lecture seule)
    python scripts/taches.py sentinel-analyser ID      # analyse d'un passage choisi à la main (identifiant sar_passes)
    python scripts/taches.py sentinel-refusionner ID   # fusion refaite sur les détections en base (gratuit)
    python scripts/taches.py sentinel-refusionner ID --simulation   # ce que la refusion changerait (rien écrit)
    python scripts/taches.py viirs-refusionner --toutes --simulation   # appariement VIIRS refait (lecture seule)
    python scripts/taches.py viirs-expliquer ALERTE [ALERTE…]   # pourquoi une alerte VIIRS tient (lecture seule)
    python scripts/taches.py viirs-reevaluer [DEBUT] --simulation   # changements de la réévaluation (rien écrit)
    python scripts/taches.py tip-rejeu [JOURS]          # demandes qu'auraient données les jours passés (lecture seule)
    python scripts/taches.py tip-and-cue-bilan [--du AAAA-MM-JJ]   # suivi quotidien de tip and cue (lecture seule)
    python scripts/taches.py sentinel2-candidats [H]  # passages Sentinel 2 récents utilisables (lecture seule)
    python scripts/taches.py essai-reel               # lot Z6 : prêt pour un essai réel ? candidats (lecture seule)
    python scripts/taches.py essai-reel --lancer ID   # un essai réel sur le passage ID, types éprouvés seulement
    python scripts/taches.py essai-reel --lancer ID --refaire   # réanalyse d'un passage Sentinel 1 déjà analysé
    python scripts/taches.py viirs-bilan [--du AAAA-MM-JJ]   # nuits VIIRS depuis une date (lecture seule)
    python scripts/taches.py viirs --nuit 2026-10-22 --nuit 2026-10-23   # retraiter ces nuits (un travailleur DEV1-M)
    SENTINEL_PLAFOND_ESSAI=1 python scripts/taches.py sentinel2-analyser ID   # essai réel Sentinel 2 (coupé sinon)
    python scripts/taches.py sentinel2-poids [--nettoyer] # poids Sentinel 2 dans le seau des modèles (une fois)
    python scripts/taches.py sentinel2-diagnostic     # seau des modèles, variables des conteneurs, poids (lecture seule)
    python scripts/taches.py sentinel2-heures [J]     # heure propre des tuiles Sentinel 2 du calendrier
    python scripts/taches.py sentinel-diagnostic       # prêt pour Sentinel ? aucune instance, aucun coût ; crée au besoin le groupe de sécurité des travailleurs
    python scripts/taches.py sentinel-candidats [H]    # passages des H (36) dernières heures avec un navire des listes
    python scripts/taches.py coupures-injection [--jour 2026-10-09] [--essais 20]
    python scripts/taches.py coupures-injection --jour J --tranches 6-9,10- --min-prior 10 --min-prior 6
                       [--zone-du D --zone-au D] [--episodes-depuis T]   # diagnostic, transactions annulées
                                                      # test par injection de la règle des coupures (rien n'est écrit)
    python scripts/taches.py detruire-travailleurs    # arrêt d'urgence : détruit toute instance étiquetée
    python scripts/taches.py mesures [--jours 14]     # mesures de calibration (scripts/mesures_calibration.py)
    python scripts/taches.py sauvegardes              # liste des sauvegardes sur R2
    python scripts/taches.py telecharger CLE CHEMIN   # récupère un objet de R2 (restauration)
    python scripts/taches.py restaurer-positions --du 2026-10-01 --au 2026-10-30
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from mars import archive, controle, echeances, supervision, traces
from mars.config import ROOT, database_url, load_env, load_rules

LIVE = Path(os.environ.get("AIS_LIVE_DIR", ROOT / "data" / "ais_live"))
HOUR = os.environ.get("TACHES_HEURE", "02:30")
VIIRS_HOUR = os.environ.get("VIIRS_HEURE", "06:30")
KEEP_DAYS = int(os.environ.get("CONSERVATION_JOURS", 30))
KEEP_BACKUPS = int(os.environ.get("SAUVEGARDES_GARDEES", 7))
DISK_PCT = float(os.environ.get("ALERTE_DISQUE_PCT", 15))
DISK_PATHS = ["/", str(LIVE)]
DISK_EVERY_S = 600
RETRY_S = 3600


def log(msg: str):
    print(f"{datetime.now(timezone.utc):%d/%m/%Y %H:%M:%S}  {msg}", flush=True)


def connect() -> psycopg.Connection:
    for k in range(30):
        try:
            return psycopg.connect(database_url(), autocommit=True)
        except psycopg.OperationalError:
            if k == 29:
                raise
            time.sleep(2)


def r2():
    from mars.r2 import R2
    return R2()


def run(conn, task: str, fn, quiet: bool = False, parent: str | None = None) -> dict | None:
    """Exécute une tâche et la consigne dans task_runs (traçabilité, et pas de double exécution le même jour).

    Chaque tâche, présente ou future, est tracée par construction : cette enveloppe ouvre le span « tache <nom> »,
    racine d'une trace propre (ou rattaché à `parent`, traceparent W3C), dont l'identifiant est écrit dans task_runs
    et donné à la session PostgreSQL (les alertes créées ou mises à jour le portent dans leurs détails)."""
    day = datetime.now(timezone.utc).date()
    with traces.span(f"tache {task}", parent=parent, nouvelle_trace=parent is None, **{"tache.nom": task}) as sp:
        rid = conn.execute("INSERT INTO task_runs (task, run_day, status, trace_id) VALUES (%s, %s, 'en_cours', %s) "
                           "RETURNING id", (task, day, traces.trace_id())).fetchone()[0]
        sp.set_attribute("tache.execution", rid)
        t = time.time()
        try:
            with traces.en_base(conn):
                details = fn()
            details["duree_s"] = round(time.time() - t, 1)
            sp.set_attributes(traces.propres({f"tache.{k}": v for k, v in details.items()
                                              if isinstance(v, (int, float, bool, str)) and len(str(v)) <= 200}))
            conn.execute("UPDATE task_runs SET status = 'ok', finished_at = now(), details = %s WHERE id = %s",
                         (json.dumps(details, default=str), rid))
            if not quiet or _changed(details):
                log(f"{task} : {json.dumps(details, default=str, ensure_ascii=False)}")
            return details
        except Exception as e:
            # refus : attendu (plafond, budget, clés absentes), ignoré par l'alerte « deux échecs de suite » ; le span
            # n'est pas en erreur, le motif est un attribut
            refus = bool(getattr(e, "motif_seul", False))
            if refus:
                sp.set_attribute("tache.refus", str(e)[:300])
            else:
                sp.record_exception(e)
                traces.erreur(f"{type(e).__name__}: {e}")
            conn.execute("UPDATE task_runs SET status = 'echec', finished_at = now(), details = %s WHERE id = %s",
                         (json.dumps({"erreur": str(e)[:1000], **({"refus": True} if refus else {})}), rid))
            log(f"{task} : ÉCHEC, {e}")
            if not refus:                         # refus attendu (plafond, clés absentes) : le motif suffit
                traceback.print_exc()
            return None


def _changed(details) -> bool:
    """Un cycle des règles n'est journalisé que s'il crée ou retire des alertes (journaux sobres)."""
    if isinstance(details, dict):
        return bool(details.get("nouvelles") or details.get("retirees")) or any(_changed(v) for v in details.values())
    return False


def due(conn, task: str, now: datetime, every_days: int = 1, hour: str = HOUR) -> bool:
    """Tâche nocturne à lancer : heure passée, pas de réussite depuis `every_days` jours (aujourd'hui compris), pas
    d'échec depuis moins d'une heure."""
    h, m = (int(x) for x in hour.split(":"))
    if now < now.replace(hour=h, minute=m, second=0, microsecond=0):
        return False
    r = conn.execute("SELECT bool_or(status = 'ok' AND NOT details ? 'en_pause') FILTER (WHERE run_day > %s), "
                     "max(started_at) FILTER (WHERE status = 'echec'), "
                     "bool_or(details ? 'en_pause') FILTER (WHERE run_day = %s) FROM task_runs WHERE task = %s",
                     (now.date() - timedelta(days=every_days), now.date(), task)).fetchone()
    if r[0]:
        return False
    # Lot P : une source en pause est consignée une fois par jour ; à sa reprise, la tâche redevient due le jour même
    source = {"viirs": "viirs"}.get(task)
    if r[2] and source and conn.execute("SELECT source_en_pause(%s)", (source,)).fetchone()[0]:
        return False
    return r[1] is None or (now - r[1]).total_seconds() >= RETRY_S


def tasks(conn):
    """Tâches quotidiennes, dans l'ordre : fonction, période en jours, heure de déclenchement (UTC)."""
    return {
        "archivage": (lambda: archive.run_archive(conn, r2(), LIVE, log=log), 1, HOUR),
        "purge": (lambda: archive.run_purge(conn, LIVE, KEEP_DAYS), 1, HOUR),
        "sauvegarde": (lambda: archive.run_backup(r2(), database_url(), KEEP_BACKUPS), 1, HOUR),
        "listes_opensanctions": (lambda: update_list(conn, "opensanctions"), 1, HOUR),
        "listes_gur": (lambda: update_list(conn, "gur"), 7, HOUR),
        "passages": (lambda: update_passes(conn), 1, HOUR),
        "viirs": (lambda: launch_viirs(conn), 1, VIIRS_HOUR),
        "preuves": (lambda: purge_proofs(conn), 1, HOUR),
        "echeances": (lambda: echeances.relever(conn, load_rules()), 1, HOUR),   # lot Z1 : secrets qui expirent
        "essai_restauration": (lambda: essai_restauration(conn), 30, HOUR),        # lot Z2 : une fois par mois
    }


def essai_restauration(conn, cible: str | None = None) -> dict:
    """Dernière sauvegarde restaurée dans une base jetable et comparée à la base en service (mars/restauration.py).
    Échec : notification ntfy, une par jour au plus (la tâche est retentée chaque heure)."""
    from mars import restauration
    try:
        return restauration.essai(conn, r2(), database_url(), cible)
    except Exception as e:
        deja = conn.execute("SELECT count(*) FROM task_runs WHERE task = 'essai_restauration' AND status = 'echec' "
                            "AND started_at > now() - interval '1 day'").fetchone()[0]
        if not deja:
            controle.notifier("pannes", "elevee", "MARS C2 : essai de restauration en échec",
                              f"La dernière sauvegarde ne se restaure pas correctement : {str(e)[:300]}", conn=conn)
        raise


def regles(conn) -> dict:
    """Règles du fichier, avec l'état du panneau de contrôle qui prime (plafonds, pauses ; mars/controle.py)."""
    return controle.appliquer(conn, load_rules())


def garde_pause(conn, rules: dict, source: str, manuel: bool, malgre_pause: bool, motif: str | None = None) -> dict | None:
    """Source en pause (lot P) : la boucle consigne la décision sans bruit ({"en_pause": motif}) ; une commande manuelle
    est refusée, sauf --malgre-pause, consigné dans journal_controle."""
    m = controle.en_pause(rules, source)
    if not m:
        return None
    if manuel and malgre_pause:
        controle.journaliser(conn, os.environ.get("MARS_OPERATEUR") or "terminal", "malgre_pause", source,
                             motif=motif or "commande manuelle avec --malgre-pause")
        log(f"{m} : lancement manuel malgré la pause (consigné)")
        return None
    if manuel:
        from mars import travailleurs
        raise travailleurs.RefusLancement(f"{m} : commande refusée, --malgre-pause pour passer outre (consigné)")
    return {"en_pause": m}


def purge_proofs(conn) -> dict:
    """Preuves images : sans limite si liées à une alerte, CONSERVATION_JOURS (30) sinon (mars/preuves.py)."""
    from mars import preuves
    return preuves.purge(conn, r2(), KEEP_DAYS)


def launch_viirs(conn, manuel: bool = False, malgre_pause: bool = False, motif: str | None = None,
                 nuits: list | None = None) -> dict:
    """Granules des dernières nuits pas encore traitées, confiées à un travailleur éphémère (un seul à la fois) ; avec
    `nuits`, toutes les granules de ces nuits, même déjà traitées (retraitement)."""
    import requests
    from mars import travailleurs, viirs
    rules = regles(conn)
    pause = garde_pause(conn, rules, "viirs", manuel, malgre_pause, motif)
    if pause:
        return pause
    scw = travailleurs.Scaleway.from_env()
    if scw is None:
        raise travailleurs.RefusLancement("clés absentes du .env : SCW_SECRET_KEY, SCW_PROJECT_ID")
    busy = conn.execute("SELECT id FROM travailleurs WHERE tache = 'viirs' AND detruit_le IS NULL").fetchone()
    if busy:                     # travailleur actif : ses nuits sont couvertes, rien à lancer (le verrou en base le garantit)
        return {"en_cours": busy[0]}
    p = viirs.plan(conn, requests.Session(), rules, datetime.now(timezone.utc), nuits)
    traces.attributs(**{"viirs.retraitement": bool(nuits), "viirs.granules_prevues": len(p["granules"]),
                        "viirs.granules_avec_masque": sum(1 for g in p["granules"] if g.get("masque_urls"))})
    if not p["granules"]:
        return {"nuits": p["nuits"], "granules": 0}
    # Le jeton Earthdata n'est plus dans le cloud-init (lot Z1) : l'API le remet au travailleur avec sa tâche
    r = travailleurs.launch(conn, scw, rules, "viirs", {"granules": p["granules"], "regions": viirs.regions_boxes()},
                            {}, log=log)
    if "occupe" in r:            # un autre processus vient de lancer (verrou) : pas un échec
        return {"en_cours": r["occupe"]}
    if "refus" in r:
        raise travailleurs.RefusLancement(r["refus"])
    return {**r, "nuits": p["nuits"], "granules": len(p["granules"])}


def sentinel_suspended(conn) -> str | None:
    """Motif du dernier échec de lancement Sentinel du jour (UTC), s'il y en a un : la sélection automatique attend
    alors le lendemain au lieu de recommencer toutes les 30 minutes (même motif, mêmes passages). Le motif reste dans
    task_runs, donc dans l'indicateur « Satellites »."""
    r = conn.execute("SELECT details->>'erreur' FROM task_runs WHERE task = 'sentinel' AND status = 'echec' "
                     "AND run_day = %s ORDER BY started_at DESC LIMIT 1", (datetime.now(timezone.utc).date(),)).fetchone()
    return r and (r[0] or "échec sans motif")


def launch_sentinel(conn, pass_id: int | None = None, auto: bool = False, malgre_pause: bool = False,
                    motif_pause: str | None = None, refaire: bool = False) -> dict:
    """Passages Sentinel 1 à analyser (corridor avec un navire des listes, alerte VIIRS), ou un passage choisi à la
    main : une session sur processeur, dans les limites du plafond de scènes et du budget (mars/sentinel.py). `auto` : appel de
    la boucle, suspendu jusqu'au lendemain après un échec du jour."""
    import requests
    from mars import sentinel, travailleurs
    rules = regles(conn)
    pause = garde_pause(conn, rules, "sentinel1", not auto, malgre_pause, motif_pause)
    if pause:
        return pause
    if auto:
        motif = sentinel_suspended(conn)
        if motif:
            return {"passages": 0, "suspendu_jusqu_au_lendemain": motif}
    scw = travailleurs.Scaleway.from_env()
    # Paire de clés Copernicus dédiée aux travailleurs (remise par le réseau privé) ; celle du serveur n'y sert pas
    missing = [k for k in ("CDSE_TRAVAILLEUR_ACCESS_KEY", "CDSE_TRAVAILLEUR_SECRET_KEY") if not os.environ.get(k)]
    if scw is None or missing:
        raise travailleurs.RefusLancement("clés absentes du .env : " + ", ".join(
            (["SCW_SECRET_KEY, SCW_PROJECT_ID"] if scw is None else []) + missing))
    busy = conn.execute("SELECT id FROM travailleurs WHERE tache = 'sentinel' AND detruit_le IS NULL").fetchone()
    if busy:
        return {"en_cours": busy[0]}
    r = sentinel.launch(conn, scw, rules, datetime.now(timezone.utc), requests.Session(), pass_id, log=log,
                        refaire=refaire)
    if "refus" in r:
        raise travailleurs.RefusLancement(r["refus"])
    return r


def launch_sentinel2(conn, pass_id: int | None = None, malgre_pause: bool = False,
                     motif_pause: str | None = None) -> dict:
    """Passages Sentinel 2 à analyser (mêmes points de déclenchement que Sentinel 1), ou un passage choisi à la main :
    une session sur processeur, plafond propre et budget partagé (mars/sentinel2.py). Coupé tant que
    sentinel2.scenes_max_par_jour vaut 0 (essai : SENTINEL_PLAFOND_ESSAI=1 sur la commande manuelle)."""
    import requests
    from mars import sentinel2, travailleurs
    rules = regles(conn)
    pause = garde_pause(conn, rules, "sentinel2", pass_id is not None, malgre_pause, motif_pause)
    if pause:
        return pause
    scw = travailleurs.Scaleway.from_env()
    missing = [k for k in ("CDSE_TRAVAILLEUR_ACCESS_KEY", "CDSE_TRAVAILLEUR_SECRET_KEY") if not os.environ.get(k)]
    if scw is None or missing:
        raise travailleurs.RefusLancement("clés absentes du .env : " + ", ".join(
            (["SCW_SECRET_KEY, SCW_PROJECT_ID"] if scw is None else []) + missing))
    busy = conn.execute("SELECT id FROM travailleurs WHERE tache = 'sentinel2' AND detruit_le IS NULL").fetchone()
    if busy:
        return {"en_cours": busy[0]}
    r = sentinel2.launch(conn, scw, rules, datetime.now(timezone.utc), requests.Session(), pass_id, log=log)
    if "refus" in r:
        raise travailleurs.RefusLancement(r["refus"])
    return r


def supervise_workers(conn, quiet: bool = True) -> dict | None:
    """Une minute de surveillance des travailleurs ; une erreur (API Scaleway injoignable) n'arrête pas la boucle."""
    from mars import sentinel, sentinel2, travailleurs, viirs
    rules = regles(conn)

    def compte(fn):
        # Épisodes des règles en observation au panneau (lot P) : comptés dans le résumé de l'exécution, donc dans
        # task_runs (travailleur_<tâche>)
        def h(c, w):
            rules["_controle"]["observees"] = {}
            out = fn(c, w, rules)
            if rules["_controle"]["observees"]:
                out["controle_observees"] = dict(rules["_controle"]["observees"])
            return out
        return h
    try:
        out = travailleurs.supervise(conn, travailleurs.Scaleway.from_env(), rules,
                                     {"viirs": compte(viirs.ingest), "sentinel": compte(sentinel.ingest),
                                      "sentinel2": compte(sentinel2.ingest)}, log=log)
    except Exception as e:
        log(f"surveillance des travailleurs : ÉCHEC, {e}")
        return None
    if not quiet or any(out.values()):
        log(f"travailleurs : {json.dumps(out, ensure_ascii=False)}")
    return out


def update_passes(conn) -> dict:
    """Calendrier des passages, puis heure propre des tuiles Sentinel 2 des trois derniers jours (lot S2)."""
    import requests
    from mars import satellites, sentinel2
    out = satellites.update(conn)
    try:
        out["heures_sentinel2"] = sentinel2.corriger_heures(conn, requests.Session(), datetime.now(timezone.utc))
    except Exception as e:                             # le calendrier reste à jour même si Copernicus refuse la lecture
        traces.erreur(f"heures Sentinel 2 : {e}")
        out["heures_sentinel2"] = {"erreur": str(e)[:300]}
    return out


def update_list(conn, source: str) -> dict:
    """Mise à jour d'une liste de surveillance (mars/watchlist.py), puis, si elle a changé, passage immédiat des
    règles des listes pour que les alertes WATCHLIST suivent sans attendre le prochain cycle."""
    from mars import watchlist
    if source == "gur":
        known = conn.execute("SELECT details->>'version' FROM task_runs WHERE task = 'listes_gur' AND status = 'ok' "
                             "AND details ? 'version' ORDER BY started_at DESC LIMIT 1").fetchone()
        out = watchlist.update_gur(conn, known[0] if known else None)
    else:
        out = watchlist.update_opensanctions(conn)
    if out.get("ajoutes") or out.get("retires") or out.get("modifies"):
        log(f"liste {source} : {out['ajoutes']} ajoutés, {out['retires']} retirés, {out['modifies']} modifiés")
        out["regles"] = rules_cycle(lists_only=True, rules=regles(conn))
    return out


def injection(day: date, essais: int, tranches=None, min_prior=None, zone=None, episodes_depuis=None) -> dict:
    """Test par injection de la règle des coupures AIS (mars/rules.py, ais_gap_injection) : coupures de 3 h à 2, 6,
    10, 14 et 18 h UTC, sur une connexion asyncpg dédiée ; chaque essai est annulé, la base n'est pas modifiée.
    Diagnostic (27/10/2026) : `tranches` [(6, 9), (10, None)] mesure le taux par tranche de messages dans l'heure ;
    `min_prior` [10, 6] rejoue avec chaque seuil ; `zone` (du, au) reconstruit la zone de réception fiable sur ces
    jours dans une transaction annulée, pour comparer à la zone en service ; `episodes_depuis` compte les coupures
    réellement observées depuis cet instant, par tranche et par gravité (lecture)."""
    import asyncio
    import copy
    import asyncpg
    from mars.rules import RECEPTION_SQL, ais_gap_injection, min_paires, tranche_de

    async def go():
        c = await asyncpg.connect(database_url())
        tr = c.transaction()
        await tr.start()                       # tout est annulé à la fin : zone de comparaison comprise
        try:
            rules = load_rules()
            out = {}
            if zone:
                z = rules["reception"]
                borne = "($7::timestamptz IS NULL OR p.ts >= $7::timestamptz)"
                assert borne in RECEPTION_SQL
                du, au = (datetime(d.year, d.month, d.day, tzinfo=timezone.utc) for d in zone)
                await c.execute("DELETE FROM reception_cells")
                await c.execute(RECEPTION_SQL.replace(borne, "(p.ts >= $7::timestamptz AND p.ts < $8::timestamptz)"),
                                z["cell_deg_lon"], z["cell_deg_lat"], z["max_interval_s"], z["min_coverage"],
                                min_paires(z, (au - du).days + 1), z["min_vessels"], du, au + timedelta(days=1))
                r = await c.fetchrow("SELECT count(*) AS n, round((sum(ST_Area(geom)) / 1e6)::numeric) AS km2 "
                                     "FROM reception_cells")
                out["zone_de_comparaison"] = {"du": str(zone[0]), "au": str(zone[1]), "cellules": r["n"],
                                              "min_pairs": min_paires(z, (au - du).days + 1),
                                              "km2": float(r["km2"] or 0), "annulee": True}
            if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM reception_cells)"):
                return {"refus": "zone de réception fiable absente : lancer scripts/build_masks.py"}
            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            for n in (min_prior or [None]):
                r = copy.deepcopy(rules)
                if n is not None:
                    r["ais_gap"]["min_prior_messages"] = int(n)
                res = await ais_gap_injection(c, start, r, essais=essais, heures=[2, 6, 10, 14, 18], tranches=tranches)
                out[f"min_prior_{r['ais_gap']['min_prior_messages']}"] = res
            if episodes_depuis:
                obs: dict = {}
                for cle, grav in await c.fetch("SELECT cle, gravite FROM episodes_observes WHERE regle = 'ais_gap' "
                                               "AND premiere_le >= $1", episodes_depuis):
                    vid, stamp = cle.split(":")
                    tl = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
                    m = await c.fetchval("SELECT count(*) FROM positions WHERE vessel_id = $1 AND ts > $2 "
                                         "AND ts < $3", int(vid), tl - timedelta(minutes=rules["ais_gap"]["prior_window_min"]),
                                         tl + timedelta(seconds=1))
                    lab = tranche_de(int(m), tranches or [(0, None)]) or f"moins de {tranches[0][0]}"
                    x = obs.setdefault(lab, {"episodes": 0, "gravites": {}})
                    x["episodes"] += 1
                    x["gravites"][grav] = x["gravites"].get(grav, 0) + 1
                out["episodes_observes_par_tranche"] = {"depuis": episodes_depuis.isoformat(), **obs}
            return out if len(out) > 1 or zone or episodes_depuis else next(iter(out.values()))
        finally:
            await tr.rollback()
            await c.close()
    return asyncio.run(go())


def rules_cycle(lists_only: bool = False, rules: dict | None = None) -> dict:
    """Un cycle des règles sur le flux en direct (mars/rules.py), sur une connexion asyncpg dédiée. `lists_only` :
    seulement les règles qui dépendent des listes (navires des listes, gravité des changements d'identité). `rules` :
    règles appliquées par le panneau de contrôle (modes des règles), celles du fichier à défaut."""
    rules = rules or load_rules()
    import asyncio
    import asyncpg
    from mars.rules import run_continuous, run_identity, run_watchlist

    async def go():
        c = await asyncpg.connect(database_url())
        for t in ("json", "jsonb"):
            await c.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        try:
            async with traces.en_base_async(c):        # alertes écrites avec l'identifiant de la trace du cycle
                return await cycle(c)
        finally:
            await c.close()

    async def cycle(c):
        now = datetime.now(timezone.utc)
        if lists_only:
            p = rules["continu"]
            return {"watchlist": await run_watchlist(c, now - timedelta(hours=p["fenetre_h"]), now, rules),
                    "identite": await run_identity(c, now - timedelta(hours=p["identite_fenetre_h"]), now, rules)}
        return await run_continuous(c, now, rules)

    return asyncio.run(go())


def _asyncpg(fn):
    """Exécute `fn(c)` sur une connexion asyncpg dédiée (statistiques de la frise)."""
    import asyncio
    import asyncpg

    async def go():
        c = await asyncpg.connect(database_url())
        try:
            return await fn(c)
        finally:
            await c.close()
    return asyncio.run(go())


def stats_cycle() -> dict:
    """Statistiques de la frise (densité, coupures du flux), étape distincte des règles depuis le lot C5 : pendant
    l'incident du 18/10/2026, les règles échouaient (disque plein) et les statistiques, calculées à la fin de leur
    cycle, n'étaient plus tenues ; les positions chargées en retard à la reprise, plus vieilles que la fenêtre de 3 h,
    n'ont jamais été comptées (stats_minute vide du 17/10 22:46 au 19/10 06:00 UTC). Les fichiers chargés en retard
    sont désormais recomptés, quelle que soit leur heure (mars/frise.py, refresh_stats)."""
    from mars.frise import refresh_stats
    return _asyncpg(lambda c: refresh_stats(c, datetime.now(timezone.utc)))


def stats_reconstruire(debut: datetime, fin: datetime) -> dict:
    from mars.frise import reconstruire
    return _asyncpg(lambda c: reconstruire(c, debut, fin))


STATS_GARDE_H = 24
STATS_GARDE_SEUIL = 10                    # minutes manquantes au delà desquelles la reconstruction part seule


def stats_garde(conn, secours) -> dict:
    """Garde contre un trou des statistiques, toutes les 10 minutes : minutes des dernières 24 h présentes dans les
    positions mais absentes de stats_minute (la minute en cours, pas encore comptée, est exclue). Au delà de 10, ces
    minutes sont reconstruites (task_runs « stats_reconstruction ») ; un échec part sur le téléphone par l'alerte Grafana,
    ou par ntfy directement si Grafana ne répond pas. Le nombre est publié dans signes_de_vie (service statistiques) pour Grafana."""
    from mars.frise import minutes_manquantes, plages
    now = datetime.now(timezone.utc)
    fin = now.replace(second=0, microsecond=0) - timedelta(minutes=2)
    manque = _asyncpg(lambda c: minutes_manquantes(c, now - timedelta(hours=STATS_GARDE_H), fin))
    out = {"minutes_manquantes": len(manque), "plages": len(plages(manque))}
    traces.attributs(**{f"stats.{k}": v for k, v in out.items()})
    if len(manque) > STATS_GARDE_SEUIL:
        def reparer():
            r = {"minutes_manquantes": len(manque), "debut": manque[0].isoformat(), "fin": manque[-1].isoformat()}
            for a, b in plages(manque):
                for k, v in stats_reconstruire(a, b).items():
                    r[k] = r.get(k, 0) + v
            return r
        res = run(conn, "stats_reconstruction", reparer)
        out["reconstruction"] = res if res is not None else "echec"
        # Échec : Grafana prévient (règle « Statistiques de la frise non reconstruites ») ; s'il ne répond pas, taches
        # envoie lui même l'alerte sur le téléphone
        if res is None and secours.ntfy is not None and not supervision.grafana_repond():
            titre = "MARS C2 : statistiques de la frise"
            message = (f"{len(manque)} minutes absentes de stats_minute sur 24 h, reconstruction en échec "
                       "(task_runs stats_reconstruction)")
            try:                 # préférences du panneau (lot P) : catégorie pannes, retenue en heures calmes
                d = controle.decider(conn, "pannes", "info", titre, message) if conn is not None else "envoyer"
            except Exception:
                d = "envoyer"
            if d == "envoyer":
                secours.ntfy.envoyer(titre, message)
        if res is not None:                       # nombre publié après réparation
            out["minutes_manquantes"] = len(_asyncpg(lambda c: minutes_manquantes(
                c, now - timedelta(hours=STATS_GARDE_H), fin)))
    supervision.signe(conn, "statistiques", now, out)
    return out


def loop(conn):
    # Une tâche restée « en cours » a été interrompue (redémarrage du conteneur, ou base restaurée depuis une
    # sauvegarde prise pendant sa propre exécution) : elle est marquée en échec et sera retentée.
    n = conn.execute("UPDATE task_runs SET status = 'echec', finished_at = now(), "
                     "details = details || '{\"erreur\": \"interrompue\"}'::jsonb WHERE status = 'en_cours'").rowcount
    if n:
        log(f"{n} tâche(s) interrompue(s) marquée(s) en échec")
    log(f"Tâches planifiées : chaque nuit à {HOUR} UTC (archivage, purge au delà de {KEEP_DAYS} jours, sauvegarde, "
        f"{KEEP_BACKUPS} gardées) ; disque toutes les {DISK_EVERY_S // 60} min, alerte sous {DISK_PCT:.0f} % ; "
        f"version {traces.version()}")
    with traces.span("taches.demarrage", nouvelle_trace=True):
        step(lambda: supervision.role_grafana(conn), "rôle grafana_lecture")
        step(lambda: supervision.parametres(conn, load_rules()), "paramètres de supervision")
    secours = supervision.Secours(supervision.Ntfy.from_env())
    if secours.ntfy is None:
        log("NTFY_SUJET absent du .env : aucune alerte de secours ne partira sur le téléphone")
    last = {"disk": 0.0, "rules": 0.0, "sentinel": 0.0, "tip": 0.0}
    while True:
        conn = minute(conn, secours, last)
        time.sleep(60)


def minute(conn, secours, last: dict):
    """Un tour de la boucle, chaque minute : une trace « taches.minute » dont chaque étape est un span ; chaque tâche
    lancée ouvre sa propre trace (run)."""
    with traces.span("taches.minute", nouvelle_trace=True):
        if conn.closed:                             # base redémarrée : nouvelle connexion, sans arrêter les alertes
            conn = step(connect, "connexion à la base") or conn
        # Alertes de secours d'abord, sans écrire en base : elles doivent partir disque plein (incident du 18/10/2026)
        step(lambda: alert_if_grafana_down(conn, secours), "alertes de secours")
        step(lambda: signs_of_life(conn), "signes de vie")
        step(lambda: supervision.mesurer_otel(conn), "mesures du collecteur OpenTelemetry")
        step(lambda: supervise_workers(conn), "surveillance des travailleurs")   # chaque minute : aucun orphelin
        step(lambda: interrupteurs(conn), "interrupteurs des analyses satellites")   # reprises prévues (lot P)
        step(lambda: notifications(conn), "notifications")          # nouvelles alertes, résumé du réveil (lot P)
        if time.time() - last["disk"] >= DISK_EVERY_S:
            last["disk"] = time.time()
            step(lambda: archive.check_disk(conn, DISK_PATHS, DISK_PCT, log=log), "disque")
            step(lambda: supervision.mesurer(conn, LIVE), "mesures du disque")
            step(lambda: supervision.parametres(conn, load_rules()), "paramètres de supervision")
            step(lambda: stats_garde(conn, secours), "garde des statistiques")
        if time.time() - last["rules"] >= 60 * load_rules()["continu"]["intervalle_min"]:
            last["rules"] = time.time()
            # statistiques d'abord, indépendantes des règles : un cycle des règles en échec ne les arrête plus
            step(stats_cycle, "statistiques de la frise")
            step(lambda: run(conn, "regles", lambda: rules_cycle(rules=regles(conn)), quiet=True), "règles")
        # Tip and cue (lot T1) : tips relevés après les règles, demandes planifiées ou expirées
        tc = load_rules().get("tip_and_cue") or {}
        if tc.get("actif") and time.time() - last["tip"] >= 60 * tc["intervalle_min"]:
            last["tip"] = time.time()
            step(lambda: run(conn, "tip_and_cue", lambda: tip_and_cue_cycle(conn), quiet=True), "tip and cue")
        # Sentinel 1 : passages examinés toutes les 30 minutes (rien n'est lancé sans point d'intérêt ; un refus du
        # plafond ou du budget est consigné dans task_runs, sans trace d'erreur)
        if time.time() - last["sentinel"] >= 60 * load_rules()["sentinel"]["intervalle_min"]:
            last["sentinel"] = time.time()
            if os.environ.get("CDSE_TRAVAILLEUR_ACCESS_KEY") and os.environ.get("SCW_SECRET_KEY"):
                step(lambda: run(conn, "sentinel", lambda: launch_sentinel(conn, auto=True), quiet=True), "sentinel")
                # Sentinel 2 (lot S2) : même rythme, seulement en service (plafond quotidien non nul)
                from mars import sentinel2
                if sentinel2.actif(regles(conn)):
                    step(lambda: run(conn, "sentinel2", lambda: launch_sentinel2(conn), quiet=True), "sentinel2")
        for task, (fn, every, hour) in tasks(conn).items():
            if step(lambda: due(conn, task, datetime.now(timezone.utc), every, hour), f"{task} (échéance)"):
                step(lambda: run(conn, task, fn), task)
    return conn


_step_errors: dict[str, str] = {}


def step(fn, name: str):
    """Une étape de la boucle, dans son span : son erreur est journalisée (une fois tant qu'elle se répète) et marque
    le span en erreur, sans arrêter la boucle."""
    with traces.span(f"etape {name}") as sp:
        try:
            out = fn()
            if _step_errors.pop(name, None):
                log(f"{name} : rétabli")
            return out
        except Exception as e:
            sp.record_exception(e)
            traces.erreur(f"{type(e).__name__}: {e}")
            msg = f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"
            if _step_errors.get(name) != msg:
                _step_errors[name] = msg
                log(f"{name} : ÉCHEC, {msg}")
            return None


def signs_of_life(conn):
    now = datetime.now(timezone.utc)
    t = supervision.signe_collecte(LIVE)
    if t is not None:
        supervision.signe(conn, "collecte", t, {"age_s": round((now - t).total_seconds())},
                          version=supervision.version_collecte(LIVE))
    supervision.signe(conn, "taches", now, memoire=True)


def alert_if_grafana_down(conn, secours) -> list[str]:
    """Si Grafana ne répond pas, taches envoie lui même les trois alertes les plus graves ; sinon, il n'annonce que le
    retour à la normale de celles qu'il avait envoyées (Grafana prend le relais pour les nouvelles)."""
    etat = supervision.conditions(conn, LIVE)
    try:                     # catégories coupées au panneau (lot P) ; une alerte déjà envoyée suit son cours
        prefs = controle.preferences(load_rules(), controle.preferences_en_base(conn))
        etat = {k: v for k, v in etat.items() if k in secours.actives or controle.decision_notification(
            prefs, {"disque": "disque"}.get(k, "collecte"), "grave", datetime.now(timezone.utc)) == "envoyer"}
    except Exception:
        pass                 # base indisponible : les alertes de secours partent quand même
    if supervision.grafana_repond():
        etat = {k: v for k, v in etat.items() if k in secours.actives}
    return secours.evaluer(etat, log=log)


# Commandes : chacune est enregistrée ici et exécutée par `executer`, qui ouvre son span (racine d'une trace) ; une
# commande ajoutée sans passer par ce registre n'est pas acceptée par l'analyse des arguments
# (tests/test_traces.py vérifie que chaque commande produit son span).

COMMANDES: dict[str, tuple] = {}


def commande(nom: str, base: bool = True):
    """Déclare une commande de taches.py ; `base` : une connexion à la base lui est ouverte."""
    def deco(fn):
        COMMANDES[nom] = (fn, base)
        return fn
    return deco


def executer(nom: str, a) -> None:
    fn, base = COMMANDES[nom]
    if nom == "boucle":                     # sans fin : chaque tour et chaque tâche ouvrent leur propre trace
        return fn(connect(), a)
    try:
        with traces.span(f"commande {nom}", nouvelle_trace=True, **{"commande.nom": nom}) as sp:
            conn = connect() if base else None
            if conn is None:
                return fn(conn, a)
            with traces.en_base(conn):
                try:
                    return fn(conn, a)
                except SystemExit as e:
                    if e.code not in (0, None):
                        traces.erreur(f"code de sortie {e.code}")
                        sp.set_attribute("commande.code_sortie", str(e.code))
                    raise
    finally:
        traces.fermer()


@commande("boucle")
def _boucle(conn, _a):
    loop(conn)


@commande("tester-alerte", base=False)           # avant la base : l'envoi doit marcher même base arrêtée
def _tester_alerte(_conn, _a):
    n = supervision.Ntfy.from_env()
    if n is None:
        print("NTFY_SUJET absent du .env : rien n'est envoyé")
        sys.exit(1)
    try:
        code = n.envoyer("MARS C2 : essai d'alerte", f"Message d'essai envoyé le "
                         f"{datetime.now(timezone.utc):%d/%m/%Y à %H:%M} UTC. La réception fonctionne.", "default",
                         "white_check_mark")
    except RuntimeError as e:
        print(f"Envoi en échec : {e}")
        sys.exit(1)
    print(f"Message d'essai envoyé (HTTP {code}) : il doit apparaître sur le téléphone dans les secondes qui suivent")


def _named(task):
    def go(conn, _a):
        if run(conn, task, tasks(conn)[task][0], parent=traces.traceparent()) is None:
            sys.exit(1)
    return go


commande("archiver")(_named("archivage"))
commande("purger")(_named("purge"))
commande("sauvegarder")(_named("sauvegarde"))


@commande("regles")
def _regles(conn, _a):
    if run(conn, "regles", lambda: rules_cycle(rules=regles(conn)), parent=traces.traceparent()) is None:
        sys.exit(1)


@commande("listes")                                  # les deux listes, sans attendre la nuit
def _listes(conn, _a):
    ok = [run(conn, f"listes_{s}", lambda s=s: update_list(conn, s), parent=traces.traceparent()) is not None
          for s in ("opensanctions", "gur")]
    if not all(ok):
        sys.exit(1)


@commande("passages")
def _passages(conn, _a):
    if run(conn, "passages", lambda: update_passes(conn), parent=traces.traceparent()) is None:
        sys.exit(1)


@commande("viirs")
def _viirs(conn, a):
    # --nuit 2026-10-22 --nuit 2026-10-23 : retraitement de ces nuits (granules déjà traitées comprises)
    if run(conn, "viirs", lambda: launch_viirs(conn, True, a.malgre_pause, a.motif, a.nuit),
           parent=traces.traceparent()) is None:
        sys.exit(1)


@commande("travailleurs")
def _travailleurs(conn, _a):
    supervise_workers(conn, quiet=False)
    for r in conn.execute("SELECT id, tache, etat, commercial_type, cree_le, detruit_le, erreur, trace_id "
                          "FROM travailleurs ORDER BY id DESC LIMIT 10").fetchall():
        print(*r)


@commande("journal-travailleur")                     # journal envoyé par un travailleur (le dernier par défaut)
def _journal_travailleur(conn, a):
    row = conn.execute("SELECT id, etat, erreur, journal_le, journal, diagnostic, trace_id FROM travailleurs "
                       "WHERE (%s::bigint IS NULL OR id = %s) ORDER BY id DESC LIMIT 1",
                       (a.args[0] if a.args else None,) * 2).fetchone()
    if row is None:
        raise SystemExit("aucun travailleur")
    print(f"travailleur {row[0]}, état {row[1]}, journal reçu le {row[3] or 'jamais'}, trace {row[6] or 'aucune'}\n"
          f"{row[2] or ''}")
    if row[5]:
        print("état Scaleway relevé avant la destruction :\n" + json.dumps(row[5], ensure_ascii=False, indent=2))
    print(row[4] or "(aucun journal : le travailleur n'a jamais joint le serveur ; lire la console série Scaleway)")


@commande("detruire-travailleurs")
def _detruire_travailleurs(conn, _a):
    from mars import travailleurs
    scw = travailleurs.Scaleway.from_env()
    if scw is None:
        raise SystemExit("clés Scaleway absentes")
    p = load_rules()["travailleurs"]
    zones = sorted({p["zone"], *(t.get("zone", p["zone"]) for t in p["types"].values())})   # Sentinel : fr-par-2
    block = {r[0]: r[1] or [] for r in conn.execute(
        "SELECT scw_server_id, volumes_bloc FROM travailleurs WHERE scw_server_id IS NOT NULL").fetchall()}
    for zone in zones:
        for s in scw.tagged(zone):                                     # travailleurs seulement (garde complète)
            try:
                gone = scw.destroy(zone, s["id"], block.get(s["id"], []))
                print(s["id"], s["name"], zone, "détruite" if gone else "destruction en cours (relancer dans une minute)")
            except travailleurs.RefusDestruction as e:
                print(s["id"], s.get("name"), f"REFUSÉE : {e}")
    conn.execute("UPDATE travailleurs SET etat = 'echec', fini_le = coalesce(fini_le, now()), "
                 "erreur = coalesce(erreur, 'arrêt d''urgence') WHERE detruit_le IS NULL AND etat NOT IN ('termine', 'echec')")


def tip_and_cue_cycle(conn) -> dict:
    from mars import tip_and_cue
    return tip_and_cue.cycle(conn, regles(conn))


def notifications(conn) -> dict:
    """Nouvelles alertes notifiées selon les préférences du panneau, puis résumé des notifications retenues pendant
    les heures calmes ; défauts du fichier recopiés pour Grafana."""
    rules, now = load_rules(), datetime.now(timezone.utc)
    controle.recopier_defauts(conn, rules)
    out = {**controle.notifier_alertes(conn, rules, now), **controle.resume_du_reveil(conn, rules, now)}
    if out.get("notifiees") or out.get("retenues"):
        log(f"notifications : {json.dumps(out, ensure_ascii=False)}")
    return out


def interrupteurs(conn) -> dict:
    """Reprises automatiques des pauses échues et notification la veille (lot P) ; journalisé seulement s'il se passe
    quelque chose."""
    out = controle.echeances(conn, datetime.now(timezone.utc))
    if out["reprises"] or out["veilles"]:
        log(f"interrupteurs : {json.dumps(out, ensure_ascii=False)}")
    return out


@commande("tip-rejeu")                               # lecture seule : demandes qu'auraient données les jours passés
def _tip_rejeu(conn, a):
    """Rejeu du tip and cue sur les a.args[0] derniers jours (7 par défaut), seuils de config/rules.yaml, même coupé
    (actif: false) : demandes, passages utiles dans les 36 h, fenêtres et coût. Rien n'est écrit."""
    from mars import tip_and_cue
    jours = float(a.args[0]) if a.args else 7.0
    fin = datetime.now(timezone.utc)
    rules = load_rules()
    r = tip_and_cue.rejeu_base(conn, rules, fin - timedelta(days=jours), fin)
    traces.attributs(**{"tip.rejeu_jours": jours, "tip.rejeu_sessions": r["sessions"], "tip.rejeu_fenetres": r["fenetres"]})
    print(f"Rejeu du tip and cue sur {r['jours']} jours ({r['passages_examines']} passages Sentinel 1 examinés) :")
    print(f"  {'tip':<12} {'tips':>5} {'regroupés':>10} {'passage':>8} {'côte':>5} {'incertitude':>12} {'contenance':>11} "
          f"{'délai':>6} {'transit':>8} {'sans 36 h':>10}")
    for tip, d in sorted(r["par_tip"].items()):
        print(f"  {tip:<12} {d['tips']:>5} {d['regroupees']:>10} {d['passage']:>8} {d['cote']:>5} {d['incertitude']:>12} "
              f"{d['contenance']:>11} {d['delai']:>6} {d['transit']:>8} {d['sans_passage_36h']:>10}")
    if r.get("contenance_moyenne") is not None:
        print(f"  contenance estimée des passages retenus en estime : moyenne {100 * r['contenance_moyenne']:.0f} % "
              f"sur {r['contenance_n']} passage(s)")
    traces.attributs(**{"tip.rejeu_contenance_moyenne": r.get("contenance_moyenne") or 0.0})
    delais = sorted(x["delai_h"] for x in r["lignes"] if x["issue"] == "passage")
    if delais:
        print(f"  délai du tip au passage : médiane {delais[len(delais) // 2]} h, de {delais[0]} à {delais[-1]} h")
    print(f"  {r['sessions']} session(s) ({r['sessions_par_jour']} par jour), {r['fenetres']} fenêtre(s), "
          f"{r['cout_eur']:.2f} € sur la période, environ {r['cout_mois_eur']:.2f} € par mois".replace(".", ","))
    plafond = load_rules()["sentinel"]["scenes_max_par_jour"]
    print(f"  sous le plafond de {plafond} scènes par jour : {r['cout_plafond_eur']:.2f} € sur la période, environ "
          f"{r['cout_plafond_mois_eur']:.2f} € par mois".replace(".", ","))
    print(f"\n  {'jour':<10} {'tips':>5} {'demandes':>9} {'retenues':>9} {'contenance':>11} {'sessions':>9} "
          f"{'plafonnées':>11} {'fenêtres':>9} {'coût':>7} {'plafonné':>9}")
    for j, d in r["par_jour"].items():
        print(f"  {j:<10} {d.get('tips', 0):>5} {d.get('demandes', 0):>9} {d.get('retenues', 0):>9} "
              f"{d.get('contenance', 0):>11} {d['sessions']:>9} {d['sessions_plafond']:>11} {d['fenetres']:>9} "
              f"{d['cout_eur']:>7.2f} {d['cout_plafond_eur']:>9.2f}".replace(".", ","))
    print(f"\n  Demandes retenues ({len(r['navires'])}) :")
    for n in r["navires"]:
        qui = f"{n['name'] or n['vessel_id']} (MMSI {n['mmsi']})" if n["vessel_id"] else f"point {n['lon']}, {n['lat']}"
        cont = f", contenance {100 * n['contenance']:.0f} %" if n.get("contenance") is not None else ""
        print(f"    {n['tip']:<12} {qui} : passage {n['passage']} le {n['heure_passage'][:16].replace('T', ' ')} UTC, "
              f"délai {n['delai_h']} h, {n['fenetres']} fenêtre(s){cont}")


@commande("tip-and-cue-bilan")                       # lecture seule : suivi quotidien de tip and cue
def _tip_and_cue_bilan(conn, a):
    """Une ligne par jour depuis --du (7 jours par défaut) : demandes créées, planifiées, analysées, résultats, fins
    sans analyse, travailleurs et coût ; puis travailleurs en vie, scènes du jour, budget du mois, interrupteurs."""
    from mars import tip_and_cue
    du = a.du or (datetime.now(timezone.utc).date() - timedelta(days=7))
    b = tip_and_cue.bilan(conn, du, regles(conn))
    traces.attributs(**{"tip.bilan_jours": len(b["jours"]), "tip.bilan_vivants": len(b["vivants"])})
    etat = lambda src: b["pauses"][src] or "en service"
    print(f"Tip and cue depuis le {du:%d/%m/%Y} ({'actif' if b['actif'] else 'coupé dans les règles'}) ; "
          f"tip and cue : {etat('tip_and_cue')} ; Sentinel 1 : {etat('sentinel1')} ; Sentinel 2 : {etat('sentinel2')}")
    print(f"  {'jour':<10} {'créées':>6} {'planif.':>7} {'analys.':>7} {'apparié':>8} {'sans AIS':>9} {'absent':>7} "
          f"{'non éval.':>9} {'sans pass.':>10} {'expirées':>8} {'refusées':>8} {'annulées':>8} {'actives':>7} "
          f"{'trav.':>5} {'coût':>6}")
    for j in b["jours"]:
        print(f"  {j['jour']:%d/%m/%Y} {j['creees']:>6} {j['planifiees']:>7} {j['analysees']:>7} {j['present_apparie']:>8} "
              f"{j['present_sans_ais']:>9} {j['absent']:>7} {j['non_evaluable']:>9} {j['sans_passage']:>10} "
              f"{j['expirees']:>8} {j['refusees']:>8} {j['annulees']:>8} {j['actives']:>7} {j['travailleurs']:>5} "
              f"{float(j['cout_eur']):>6.2f}".replace(".", ","))
    tot = sum(float(j["cout_eur"]) for j in b["jours"])
    print(f"Total : {sum(j['creees'] for j in b['jours'])} demandes, {sum(j['analysees'] for j in b['jours'])} "
          f"analysées, {sum(j['present_sans_ais'] for j in b['jours'])} présentes sans AIS, {tot:.2f} € HT ; "
          f"scènes aujourd'hui {b['scenes_aujourd_hui']} sur {b['plafond_scenes']} ; dépense Sentinel du mois "
          f"{b['depense_mois_eur']:.2f} € sur {b['budget_mois_eur']:.2f} €".replace(".", ","))
    if b["vivants"]:
        for v in b["vivants"]:
            print(f"  TRAVAILLEUR EN VIE : {v['id']} {v['tache']} {v['type']} en {v['zone']}, {v['etat']}, créé le "
                  f"{v['cree_le']:%d/%m %H:%M} UTC")
    else:
        print("  Aucun travailleur en vie.")


def _instant(texte: str) -> datetime:
    t = datetime.fromisoformat(texte)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


@commande("stats-reconstruire")                      # statistiques de la frise recalculées depuis les positions
def _stats_reconstruire(conn, a):
    if len(a.args) != 2:
        raise SystemExit("Préciser le début et la fin (UTC) : taches.py stats-reconstruire 2026-10-17T22:00 "
                         "2026-10-19T07:00")
    debut, fin = (_instant(x) for x in a.args)
    res = run(conn, "stats_reconstruction", lambda: {"debut": debut.isoformat(), "fin": fin.isoformat(),
                                                     **stats_reconstruire(debut, fin)}, parent=traces.traceparent())
    if res is None:
        sys.exit(1)
    print(json.dumps(res, ensure_ascii=False, indent=2))


STATS_ARRET_PART = 0.05      # sous 5 % du flux habituel, un trou est un vrai arrêt du flux, pas un trou des statistiques


def nature_trou(positions: int, minutes: float, mediane: float | None) -> str:
    """Qualifie un trou de stats_minute : « vrai arrêt du flux » si ses positions font moins de 5 % du flux habituel
    (médiane des positions par minute) sur sa durée, sinon trou des statistiques à reconstruire. Cas du 14/10/2026,
    10:17 à 12:59 : 154 positions en 162 min, environ 190 par minute d'habitude, soit 0,5 % : arrêt."""
    if not mediane or positions < STATS_ARRET_PART * mediane * minutes:
        return "vrai arrêt du flux"
    return "trou des statistiques, à reconstruire (stats-reconstruire)"


@commande("stats-trous")                             # lecture seule : trous de stats_minute
def _stats_trous(conn, a):
    depuis = _instant(a.args[0]) if a.args else datetime.now(timezone.utc) - timedelta(days=30)
    rows = conn.execute(
        """SELECT minute AS a, nxt AS b, nxt - minute AS duree FROM (
               SELECT minute, lead(minute) OVER (ORDER BY minute) AS nxt FROM stats_minute WHERE minute >= %s) s
           WHERE nxt - minute > interval '5 minutes' ORDER BY duree DESC""", (depuis,)).fetchall()
    mediane = conn.execute("SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY positions) FROM stats_minute "
                           "WHERE minute >= %s", (depuis,)).fetchone()[0]
    print(f"{len(rows)} trou(s) de plus de 5 min dans stats_minute depuis le {depuis:%d/%m/%Y %H:%M} UTC ; flux "
          f"habituel {mediane or 0:.0f} positions par minute, vrai arrêt sous {STATS_ARRET_PART:.0%}".replace(".", ","))
    a_reparer = 0
    for a_, b, d in rows[:30]:
        # Minutes du trou seulement : la minute `a` a sa ligne, ses positions ne sont pas dans le trou
        debut = a_ + timedelta(minutes=1)
        n = conn.execute("SELECT count(*) FROM positions WHERE ts >= %s AND ts < %s", (debut, b)).fetchone()[0]
        minutes = (b - debut).total_seconds() / 60
        nature = nature_trou(n, minutes, mediane)
        a_reparer += not nature.startswith("vrai")
        print(f"  {debut:%d/%m/%Y %H:%M} à {b:%d/%m/%Y %H:%M}  {minutes:.0f} min  {n} positions : {nature}")
    traces.attributs(**{"stats.trous": len(rows), "stats.trous_a_reparer": a_reparer})
    print(f"{a_reparer} trou(s) des statistiques à reconstruire" if a_reparer else
          "Aucun trou des statistiques : les trous restants sont de vrais arrêts du flux")


@commande("viirs-reevaluer")                         # règles VIIRS en vigueur appliquées aux détections en base
def _viirs_reevaluer(conn, a):
    """Sans argument : toutes les détections ; avec DEBUT [FIN] (UTC), celles de la période, et le décompte des
    détections non évaluables pour coupure du flux avant et après (après une reconstruction des statistiques)."""
    from mars import viirs
    from mars.evaluation import MOTIF_COUPURE
    debut = _instant(a.args[0]) if a.args else None
    fin = _instant(a.args[1]) if len(a.args) > 1 else datetime.now(timezone.utc)

    def compte():
        return dict(conn.execute(
            "SELECT CASE WHEN non_evaluable = %s THEN 'coupure_du_flux' WHEN non_evaluable IS NOT NULL THEN 'autre_motif' "
            "ELSE 'evaluable_ou_ecartee' END, count(*) FROM viirs_detections WHERE ts >= %s AND ts < %s GROUP BY 1",
            (MOTIF_COUPURE, debut or datetime(2000, 1, 1, tzinfo=timezone.utc), fin)).fetchall())
    avant = compte()
    coupees = [r[0] for r in conn.execute("SELECT id FROM viirs_detections WHERE non_evaluable = %s AND ts >= %s "
                                          "AND ts < %s", (MOTIF_COUPURE, debut or datetime(2000, 1, 1, tzinfo=timezone.utc),
                                                           fin)).fetchall()]
    res = viirs.reevaluate(conn, load_rules(), debut, simulation=a.simulation)
    devenues = conn.execute("SELECT count(*) FROM viirs_detections WHERE id = ANY(%s) AND non_evaluable IS NULL",
                            (coupees,)).fetchone()[0]
    res |= {"periode_avant": avant, "periode_apres": compte(), "coupure_puis_evaluees": devenues}
    traces.attributs(**{"viirs.coupure_puis_evaluees": devenues, "viirs.coupure_avant": len(coupees),
                        "viirs.simulation": bool(a.simulation), "viirs.changements": len(res.get("changements", []))})
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


@commande("sentinel-diagnostic")                     # Copernicus, puis Scaleway ; seule écriture : groupe de sécurité
def _sentinel_diagnostic(conn, _a):
    import requests
    from mars import diagnostic, travailleurs
    # Volumes bloc enregistrés des travailleurs Sentinel récents : chacun doit avoir été supprimé
    recorded = conn.execute("SELECT id, volumes_bloc FROM travailleurs WHERE tache = 'sentinel' AND "
                            "cardinality(volumes_bloc) > 0 AND cree_le > now() - interval '30 days' "
                            "ORDER BY id").fetchall()
    lines = diagnostic.run(load_rules(), requests.Session(), travailleurs.Scaleway.from_env(), recorded=recorded)
    print(diagnostic.report(lines))
    sys.exit(1 if any(s == diagnostic.MANQUE for s, _r, _d in lines) else 0)


def _sentinel(manual: bool):
    def go(conn, a):
        pid = int(a.args[0]) if manual and a.args else None
        if manual and pid is None:
            raise SystemExit("Préciser l'identifiant du passage : taches.py sentinel-analyser ID (table sar_passes)")
        if run(conn, "sentinel", lambda: launch_sentinel(conn, pid, malgre_pause=a.malgre_pause, motif_pause=a.motif,
                                                         refaire=a.refaire),
               parent=traces.traceparent()) is None:
            sys.exit(1)
    return go


@commande("sentinel-candidats")                      # essai réel : passages récents avec un navire des listes
def _sentinel_candidats(conn, a):
    import requests
    from mars import sentinel
    rows = sentinel.candidats_essai(conn, requests.Session(), load_rules(), datetime.now(timezone.utc),
                                    int(a.args[0]) if a.args else 36)
    if not rows:
        print("Aucun passage Sentinel 1 acquis dans les 36 dernières heures ne couvre un navire des listes")
    for r in rows:
        cout = f", environ {r['cout_estime_eur']:.2f} €".replace(".", ",") if r["cout_estime_eur"] is not None else ""
        print(f"passage {r['passage']:>6}  {r['acquis_le']:%d/%m/%Y %H:%M} UTC  {r['satellite']}  "
              f"{r['navires_des_listes']} navire(s) des listes, {r['points']} point(s) en mer, {r['scenes']} scène(s), "
              f"{r['fenetres']} fenêtre(s) ({r['km2']} km²), environ {r['octets_estimes'] / 1e6:.0f} Mo lus sur "
              f"{r['octets_scenes'] / 1e6:.0f} Mo ; calcul prévu : {r['calcul_prevu']}{cout}")
        for e in r["points_ecartes"]:
            cote = f"{e['distance_cote_m']} m de la côte" if e.get("distance_cote_m") is not None else "côte inconnue"
            print(f"         écarté : {e.get('navire') or e.get('vessel_id')} ({e['lat']:.4f}, {e['lon']:.4f}, {cote}) : "
                  f"{e['ecarte']}")


def date_reprise(texte: str) -> datetime:
    """« 17/06/2024 08:00 » ou « 17/06/2024 » (heure de Paris, 08:00 par défaut), ou ISO 8601 ; rendue en UTC."""
    from zoneinfo import ZoneInfo
    t = texte.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            d = datetime.strptime(t, fmt)
            if fmt == "%d/%m/%Y":
                d = d.replace(hour=8)
            return d.replace(tzinfo=ZoneInfo("Europe/Paris")).astimezone(timezone.utc)
        except ValueError:
            pass
    d = datetime.fromisoformat(t)
    return (d if d.tzinfo else d.replace(tzinfo=ZoneInfo("Europe/Paris"))).astimezone(timezone.utc)


@commande("satellites-pause")                        # lot P : interrupteurs des analyses satellites (sans argument : état)
def _satellites_pause(conn, a):
    """satellites-pause on|off [sentinel1|sentinel2|viirs|tip_and_cue|tout] [--jusqua DATE] [--motif TEXTE] ; « tout »
    exclut VIIRS, qui se nomme explicitement. Journalisé (auteur MARS_OPERATEUR, « terminal » par défaut) et notifié."""
    if not a.args:
        for r in conn.execute(f"SELECT {controle.COLS_INTERRUPTEUR} FROM interrupteurs_satellites ORDER BY source"):
            e = controle.interrupteur(r)
            if e["en_pause"]:
                fin = f", reprise le {controle._paris(e['jusqu_a']):%d/%m/%Y %H:%M}" if e["jusqu_a"] else ""
                print(f"{e['source']:<12} EN PAUSE depuis le {controle._paris(e['depuis']):%d/%m/%Y %H:%M}{fin} "
                      f"par {e['auteur']} : {e['motif']}")
            else:
                print(f"{e['source']:<12} en service")
        return
    sens = a.args[0].lower()
    if sens not in ("on", "off"):
        raise SystemExit("taches.py satellites-pause on|off [source|tout] [--jusqua DATE] [--motif TEXTE]")
    cible = a.args[1] if len(a.args) > 1 else "tout"
    sources = controle.TOUT if cible == "tout" else (cible,)
    if any(s not in controle.SOURCES for s in sources):
        raise SystemExit(f"source inconnue : {cible} ({', '.join(controle.SOURCES)} ou tout)")
    if sens == "on" and not a.motif:
        raise SystemExit("--motif obligatoire pour une pause")
    jusqua = date_reprise(a.jusqua) if a.jusqua and sens == "on" else None
    auteur = os.environ.get("MARS_OPERATEUR") or "terminal"
    traces.attributs(**{"controle.sources": ",".join(sources), "controle.pause": sens == "on"})
    for s in sources:
        try:
            with conn.transaction():
                e = controle.basculer(conn, s, sens == "on", auteur, a.motif, jusqua)
        except ValueError as err:
            raise SystemExit(str(err))
        print(f"{s:<12} {'EN PAUSE' if e['en_pause'] else 'en service'}" +
              (f" jusqu'au {controle._paris(jusqua):%d/%m/%Y %H:%M} (heure de Paris)" if jusqua else ""))


@commande("operateur-hacher", base=False)           # lot P : ligne MARS_OPERATEURS du .env (mot de passe saisi masqué)
def _operateur_hacher(_conn, a):
    """Empreinte PBKDF2 SHA 256 du mot de passe d'un opérateur, saisi deux fois sans écho ; rien n'est écrit ni
    journalisé. Plusieurs opérateurs : séparer les entrées par une virgule dans MARS_OPERATEURS."""
    import getpass
    nom = (a.args[0] if a.args else input("Nom de l'opérateur : ")).strip()
    if not nom or any(c in nom for c in ":, "):
        raise SystemExit("Nom d'opérateur sans espace, deux points ni virgule")
    mdp = getpass.getpass("Mot de passe (12 caractères au moins) : ")
    if len(mdp) < 12:
        raise SystemExit("Mot de passe trop court : 12 caractères au moins")
    if getpass.getpass("Encore une fois : ") != mdp:
        raise SystemExit("Les deux saisies diffèrent")
    traces.ligne_brute(f"MARS_OPERATEURS={nom}:{controle.hacher(mdp)}")      # copiée dans le .env : sans suffixe


@commande("controle-diagnostic", base=False)       # lecture seule : MARS_OPERATEURS, lue par l'API seulement
def _controle_diagnostic(_conn, _a):
    """MARS_OPERATEURS n'arrive qu'au conteneur backend (lot Z1, un environnement par service) : elle est lue par l'API
    (GET /api/controle/session : noms et raisons seulement, jamais d'empreinte) ; code de sortie 1 si une ligne est
    abîmée ou absente. Une ligne encore présente dans taches est signalée (elle ne devrait plus y être)."""
    import requests
    from mars import diagnostic
    lignes = []
    if controle.operateurs() or controle.operateurs_abimes():
        lignes.append((diagnostic.A_VERIFIER, "MARS_OPERATEURS (taches)",
                       "présente dans ce conteneur : elle ne devrait aller qu'à backend (docker-compose.yml)"))
    else:
        lignes.append((diagnostic.INFO, "MARS_OPERATEURS (taches)", "absente, comme prévu : lue par l'API seulement"))
    try:
        api = os.environ.get("MARS_API", "http://backend:8000/api")
        s = requests.get(f"{api}/controle/session", timeout=10).json()
        lignes.append((diagnostic.PRET if s.get("configure") and not s.get("abimes") else diagnostic.MANQUE,
                       "MARS_OPERATEURS (backend)", "; ".join(s.get("abimes") or []) or
                       ("lue et bien formée" if s.get("configure") else "absente du conteneur backend")))
    except Exception as e:
        lignes.append((diagnostic.A_VERIFIER, "MARS_OPERATEURS (backend)", f"API injoignable ({type(e).__name__})"))
    print(diagnostic.report(lignes))
    sys.exit(1 if any(s == diagnostic.MANQUE for s, _r, _d in lignes) else 0)


commande("sentinel")(_sentinel(False))
commande("sentinel-analyser")(_sentinel(True))


@commande("sentinel-refusionner")                    # gratuit : fusion refaite sur les détections en base
def _sentinel_refusionner(conn, a):
    """taches.py sentinel-refusionner ID : refait la fusion de l'analyse ID (Sentinel 1 ou 2, terminée) avec les règles
    en vigueur, sans travailleur ; alertes mises à jour en place, jamais effacées, décisions des opérateurs gardées.
    Tout ou rien (une transaction), consigné dans task_runs (sentinel_refusion) et tracé."""
    from mars import sentinel, travailleurs
    if not a.args or not a.args[0].isdigit():
        raise SystemExit("Préciser l'identifiant de l'analyse : taches.py sentinel-refusionner ID (table analyses)")
    aid = int(a.args[0])
    vu = {}
    if getattr(a, "simulation", False):               # rien n'est écrit : transaction annulée, résultat affiché
        import psycopg
        res = {}
        with conn.transaction():
            res = sentinel.refusionner(conn, aid, regles(conn))
            res["alertes"] = [dict(zip(("id", "type", "gravite", "statut", "motif"), r)) for r in conn.execute(
                """SELECT DISTINCT a.id, a.type, a.severity, a.status, a.details->'refusion'->>'motif' FROM alerts a
                   JOIN alert_evidence e ON e.alert_id = a.id AND e.evidence_type = 'analysis' AND e.evidence_id = %s
                   ORDER BY a.id""", (aid,)).fetchall()]
            raise psycopg.Rollback()
        traces.attributs(**{"sentinel.analyse": aid, "sentinel.simulation": True})
        print(json.dumps({**res, "simulation": "rien n'est écrit : transaction annulée"}, ensure_ascii=False, indent=2,
                         default=str))
        return

    def refaire():
        with conn.transaction():
            r = sentinel.refusionner(conn, aid, regles(conn))
        if "refus" in r:
            vu["refus"] = r["refus"]
            raise travailleurs.RefusLancement(r["refus"])      # tâche en échec avec son motif, sans trace d'erreur
        return r
    r = run(conn, "sentinel_refusion", refaire, parent=traces.traceparent())
    if r is None:
        raise SystemExit(f"REFUS : {vu['refus']}" if "refus" in vu else 1)
    print(f"Analyse {aid} refusionnée : {r['appariees_avant']} écho(s) apparié(s) avant, {r['appariees']} après ; "
          f"alertes classées automatiquement {r['alertes_classees'] or 'aucune'}, décisions d'opérateurs gardées "
          f"{r['decisions_gardees'] or 'aucune'}, alertes levées ou mises à jour {r['alertes_levees'] or 'aucune'} "
          f"(dont {r['alertes_douteuses']} à confirmer) ; trace {traces.trace_id()}")


@commande("sentinel2-analyser")                      # essai réel : SENTINEL_PLAFOND_ESSAI=1 (Sentinel 2 coupé)
def _sentinel2_analyser(conn, a):
    if not a.args:
        raise SystemExit("Préciser l'identifiant du passage Sentinel 2 : taches.py sentinel2-analyser ID (sar_passes)")
    if run(conn, "sentinel2", lambda: launch_sentinel2(conn, int(a.args[0]), a.malgre_pause, a.motif),
           parent=traces.traceparent()) is None:
        sys.exit(1)


@commande("essai-reel")                              # lot Z6 : préparation (lecture seule), --lancer ID : un essai
def _essai_reel(conn, a):
    """Sans option : état avant un essai réel et passages candidats, rien n'est lancé (code de sortie 1 s'il manque
    quelque chose). --lancer ID : un essai sur le passage ID (Sentinel 1 ou 2), malgré la pause, plafond du jour en
    vigueur et au moins une scène, types éprouvés seulement, consigné dans task_runs (essai_reel)."""
    import requests
    from mars import essai_reel, sentinel, sentinel2, travailleurs
    rules = regles(conn)
    now = datetime.now(timezone.utc)
    if a.lancer is None:
        lignes = essai_reel.preparation(conn, rules, now)
        print(essai_reel.rapport(lignes))
        session = requests.Session()
        print("\nPassages Sentinel 1 candidats (36 h) :")
        for r in sentinel.candidats_essai(conn, session, rules, now, 36):
            cout = f"{r['cout_estime_eur']:.2f} €".replace(".", ",") if r["cout_estime_eur"] is not None else "·"
            print(f"  passage {r['passage']:>6}  {r['acquis_le']:%d/%m/%Y %H:%M} UTC  {r['satellite']}  "
                  f"{r['points']} point(s), {r['scenes']} scène(s), {r['fenetres']} fenêtre(s), {r['calcul_prevu']}, "
                  f"environ {cout}")
        print("Passages Sentinel 2 candidats (72 h) :")
        for r in sentinel2.candidats_essai(conn, session, rules, now, 72):
            print(f"  passage {r['passage']:>6}  " + (f"rien à analyser : {r['rien']}" if "rien" in r else
                  f"{r['acquis_le']:%d/%m/%Y %H:%M} UTC, {r['points']} point(s), {r['fenetres']} fenêtre(s), "
                  f"environ {r['cout_estime_eur']:.2f} €".replace(".", ",")))
        print(f"\nVérification, avant et après un essai (aucune ligne attendue) :\n  {essai_reel.VERIFICATION_SQL}")
        sys.exit(1 if any(s == essai_reel.MANQUE for s, _r, _d in lignes) else 0)
    pid = a.lancer
    mission, refus = essai_reel.verifier_lancement(conn, rules, pid)
    traces.attributs(**{"essai.passage": pid, "essai.mission": mission or "", "essai.refaire": a.refaire})
    if not refus and a.refaire and mission != "S1":
        refus = f"passage {pid} : --refaire pour Sentinel 1 seulement"
    if refus:
        traces.attributs(**{"essai.refus": refus[:300]})
        raise SystemExit(f"REFUS : {refus}")
    # Plafond de scènes du jour : celui en vigueur (panneau, sinon fichier), au moins 1 (Sentinel 2 est coupé au
    # fichier) ; pour cette commande seulement
    section = essai_reel.MISSIONS[mission][1]
    os.environ.setdefault("SENTINEL_PLAFOND_ESSAI", str(max(1, int(rules[section]["scenes_max_par_jour"]))))
    travailleurs.ESSAI_EPROUVES_SEULS = True
    motif = a.motif or f"essai réel du lot Z6, passage {pid} (taches.py essai-reel --lancer)"

    def lancer():
        if mission == "S1":
            r = launch_sentinel(conn, pid, malgre_pause=True, motif_pause=motif, refaire=a.refaire)
        else:
            r = launch_sentinel2(conn, pid, malgre_pause=True, motif_pause=motif)
        return {"passage": pid, "mission": mission, **r}
    r = run(conn, "essai_reel", lancer, parent=traces.traceparent())
    if r is None:
        sys.exit(1)
    refus = essai_reel.refus_lancement(r)
    if refus:                     # pause, travailleur occupé, passage en cours ou déjà analysé, rien à analyser
        traces.attributs(**{"essai.refus": refus[:300]})
        raise SystemExit(f"REFUS : {refus}")
    if r.get("refait"):
        print(f"Réanalyse du passage {pid}, liée à l'analyse {r['refait']} (gardée telle quelle avec ses détections)")
    if r.get("attente_gpu"):
        print(f"Analyse(s) {r['attente_gpu']} en attente d'un processeur, aucun travailleur créé, aucun coût : "
              f"{r.get('motif')} ; nouvel essai par la boucle dans {r.get('prochain_essai_min')} min")
        return
    print(f"Essai lancé : travailleur {r.get('travailleur')}, {r.get('type')} en {r.get('zone') or 'fr-par-2'}, "
          f"environ {r.get('cout_estime_eur') or 0:.2f} € ; trace {traces.trace_id()}".replace(".", ","))
    print(f"Suivre : taches.py travailleurs ; à la fin, aucune ligne attendue :\n  {essai_reel.VERIFICATION_SQL}")


def _rapport_refusion(plan: dict, applique: dict | None = None):
    """Rapport lisible d'une refusion VIIRS (simulation ou réelle)."""
    g = lambda d: ", ".join(f"{k} {d[k]}" for k in ("critique", "elevee", "moyenne", "faible") if d.get(k)) or "aucune"
    portee = (f"nuits du {plan['du']} au {plan['au']}" if plan["du"] or plan["au"] else "toutes les nuits en base")
    print(f"Refusion VIIRS, {portee}, règles {plan['version_regles']} : {plan['nuits']} nuit(s), {plan['detections']} "
          f"détection(s) ; appariées {plan['appariees_avant']} avant, {plan['appariees_apres']} après "
          f"({len(plan['appariements_changes'])} appariement(s) changé(s))")
    print(f"Alertes DARK_SHIP ouvertes (« nouvelle ») par gravité : avant {g(plan['ouvertes_avant'])} ; "
          f"après {g(plan['ouvertes_apres'])}")
    par = {}
    for a in plan["alertes"]:
        par.setdefault(a["action"], []).append(a)
    titres = {"classer": "Classées en faux positif (navire qui les explique)",
              "faible": "Abaissées en faible, à confirmer (navire possiblement compatible)",
              "garder": "Restent ouvertes, vrais candidats (aucun navire AIS compatible dans les 120 min)",
              "rien": "Sans changement (détection déjà écartée par un masque)"}
    for k in ("classer", "faible", "garder", "rien"):
        lst = par.get(k, [])
        print(f"\n{titres[k]} : {len(lst)}")
        for a in lst:
            decision = "" if a["statut"] == "nouvelle" else f" [décision gardée : {a['statut']}]"
            if k == "classer":
                n = a["navire"]
                print(f"  alerte {a['alerte']:>6}  nuit {a['nuit']}  {a['gravite']:<8} {n['name'] or n['vessel_id']} "
                      f"(MMSI {n['mmsi']}), {n['distance_m']} m, {n['methode']}"
                      + (f" de {n['age_min']:.0f} min" if n["methode"] == "arret" else "") + decision)
            elif k == "faible":
                c = a["navire_compatible"]
                print(f"  alerte {a['alerte']:>6}  nuit {a['nuit']}  {a['gravite']:<8} {c.get('name') or c['vessel_id']}, "
                      f"{c['distance_m']} m, {c['age_min']:.0f} min{decision}")
            elif k == "garder":
                infra = (a["infrastructure"] or {}).get("type")
                print(f"  alerte {a['alerte']:>6}  nuit {a['nuit']}  {a['gravite']:<8} {a['lon']}, {a['lat']}  "
                      f"{a['nanowatts']} nW, AIS le plus proche {a['ais_proche_m']} m"
                      + (f", {infra} à {a['infrastructure'].get('distance_m')} m" if infra else "") + decision)
    print(f"\nDemandes d'observation nées d'une alerte classée : {len(plan['demandes'])}")
    for d in plan["demandes"]:
        quoi = "annulée avec le motif" if d["fin"] == "annulee" else "déjà traitée ou à plusieurs origines : motif noté"
        print(f"  demande {d['demande']:>5}  {d['etat']:<12} analyse {d['analyse'] or '·'}  alertes {d['alertes']} : {quoi}")
    if applique:
        print(f"\nAppliqué : {len(applique['classees'])} classée(s), {len(applique['decisions_gardees'])} décision(s) "
              f"d'opérateur gardée(s), {len(applique['faibles'])} abaissée(s), {len(applique['demandes_annulees'])} "
              f"demande(s) annulée(s), {len(applique['demandes_notees'])} notée(s) ; trace {traces.trace_id()}")
    else:
        print(f"\nSIMULATION : rien n'a été écrit (transaction en lecture seule) ; trace {traces.trace_id()}")


@commande("viirs-expliquer")                         # lecture seule : pourquoi une alerte VIIRS tient ou non
def _viirs_expliquer(conn, a):
    """taches.py viirs-expliquer ALERTE [ALERTE…] : navires AIS autour de la lumière à plus ou moins 120 min, positions
    brutes, et chaque règle de la fusion appliquée par son propre code. Lecture seule (transaction en lecture seule)."""
    from mars import viirs
    if not a.args or not all(x.isdigit() for x in a.args):
        raise SystemExit("Préciser les alertes : taches.py viirs-expliquer 50275 45567 …")
    rules = regles(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        out = [viirs.expliquer(conn, int(x), rules) for x in a.args]
    traces.attributs(**{"viirs.expliquer_alertes": len(out)})
    for e in out:
        if "refus" in e:
            print(f"REFUS : {e['refus']}")
            continue
        vd = e["verdict"] or {}
        print(f"\nAlerte {e['alerte']} ({e['gravite']}, {e['statut']}) : lumière {e['detection']} du "
              f"{e['heure'][:19].replace('T', ' ')} UTC, {e['lon']:.5f}, {e['lat']:.5f}, {e['nanowatts']} nW, granule "
              f"{e['granule']}{', masque ' + e['masque'] if e['masque'] else ''}")
        print(f"  Verdict des règles en vigueur : {vd.get('action')} ({vd.get('motif')})")
        for x in e["voisines"]:
            print(f"  Lumière voisine {x['detection']} à {x['distance_m']} m, appariée à {x['navire'] or 'aucun navire'}")
        if not e["navires"]:
            print("  Aucun navire AIS à moins de 10 km de la lumière dans les 120 min avant et après")
        for n in e["navires"]:
            pos = " ; ".join(f"{p['ecart_min']:+.0f} min à {p['distance_m']} m, {p['sog']} nœuds" for p in n["positions"])
            print(f"  {n['name'] or n['vessel_id']} (MMSI {n['mmsi']}, {n['longueur_m'] or '?'} m) : {pos}")
            pc = n["position_courte"]
            print("    interpolation ou estime de 20 min au plus : " + (
                f"à {pc['distance_m']} m, tolérance {pc['tolerance_m']} m, "
                f"{'dans' if pc['dans_tolerance'] else 'hors de'} la tolérance" if pc else "aucune position assez proche"))
            print(f"    maintien à l'arrêt : {n['maintien']}")
            if n["pris_par_detection"]:
                print(f"    déjà apparié à la lumière {n['pris_par_detection']} du même granule")
            c = n["prudence"]
            print("    prudence : " + (f"compatible, {c['distance_m']} m pour un rayon de {c['rayon_m']} m, position "
                                       f"{'avant' if c['position_avant'] else 'après'} de {c['age_min']:.0f} min, "
                                       f"{c['vitesse_kn']} nœuds" if c else "non compatible"))


@commande("viirs-refusionner")                       # gratuit : appariement VIIRS refait sur les détections en base
def _viirs_refusionner(conn, a):
    """taches.py viirs-refusionner (--nuit J | --du J [--au J] | --toutes) [--simulation] : appariement de chaque
    détection VIIRS refait avec les règles en vigueur (maintien à l'arrêt, prudence), sans travailleur. --simulation :
    transaction en lecture seule, rapport de ce qui changerait. Sinon : tout ou rien, consigné (viirs_refusion), tracé."""
    from mars import viirs
    if a.nuit:
        du, au = min(a.nuit), max(a.nuit)
    elif a.du or a.toutes:
        du, au = a.du, a.au
    else:
        raise SystemExit("Préciser la portée : --nuit AAAA-MM-JJ, --du AAAA-MM-JJ [--au AAAA-MM-JJ] ou --toutes")
    rules = regles(conn)
    if a.simulation:
        with traces.span("tache viirs_refusion_simulation",
                         **{"tache.nom": "viirs_refusion_simulation"}) as sp:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")            # garantie : aucune écriture possible
                with traces.span("viirs.refusion_plan"):
                    plan = viirs.refusion_plan(conn, rules, du, au)
            sp.set_attributes({"viirs.refusion_detections": plan["detections"],
                               "viirs.refusion_alertes": len(plan["alertes"]),
                               "viirs.refusion_a_classer": sum(x["action"] == "classer" for x in plan["alertes"])})
        _rapport_refusion(plan)
        return
    vu = {}

    def refaire():
        with conn.transaction():
            with traces.span("viirs.refusion_plan"):
                plan = viirs.refusion_plan(conn, rules, du, au)
            with traces.span("viirs.refusion_application") as sp:
                r = viirs.refusion_appliquer(conn, plan, rules)
                sp.set_attributes({"viirs.refusion_classees": len(r["classees"]),
                                   "viirs.refusion_faibles": len(r["faibles"]),
                                   "viirs.refusion_demandes_annulees": len(r["demandes_annulees"])})
        vu["plan"], vu["r"] = plan, r
        return {"du": plan["du"], "au": plan["au"], "detections": plan["detections"],
                "appariees_avant": plan["appariees_avant"], "appariees_apres": plan["appariees_apres"], **r}
    if run(conn, "viirs_refusion", refaire, parent=traces.traceparent()) is None:
        sys.exit(1)
    _rapport_refusion(vu["plan"], vu["r"])


@commande("viirs-bilan")                             # lot Z6 : nuits VIIRS depuis --du (lecture seule)
def _viirs_bilan(conn, a):
    from mars import viirs
    du = a.du or (datetime.now(timezone.utc).date() - timedelta(days=7))
    b = viirs.bilan(conn, du)
    traces.attributs(**{"viirs.bilan_nuits": b["total"]["nuits"], "viirs.bilan_traitees": b["total"]["nuits_traitees"],
                        "viirs.bilan_non_evaluables": b["total"]["nuits_non_evaluables"]})
    print(f"Nuits VIIRS depuis le {du:%d/%m/%Y} :")
    for n in b["nuits"]:
        if not n["granules"]:
            print(f"  {n['nuit']:%d/%m}  aucune granule traitée")
            continue
        lune = f"lune {n['lune']:.0f} %" if n["lune"] is not None else "lune inconnue"
        masque = f"masque des nuages {n['masques']}/{n['granules'] - n['en_erreur']}"
        resultat = (f"{n['detections']} détections : {n['avec_ais']} avec AIS, {n['sans_ais']} sans AIS, "
                    f"{n['non_evaluables']} non évaluables, {n['ecartees']} écartées")
        if n["etat"] == "non évaluable":
            resultat = ("NON ÉVALUABLE (lune forte : une absence ne prouve rien, pas « 0 navire ») ; " + resultat)
        elif n["granules_non_evaluables"]:
            resultat += f" ; {n['granules_non_evaluables']} granules non évaluables (lune forte)"
        print(f"  {n['nuit']:%d/%m}  {lune}, {masque} ; {n['granules']} granules ({n['en_erreur']} en erreur), "
              f"{resultat} ; {n['alertes']} alertes ({n['alertes_hautes']} élevées ou critiques) ; "
              f"travailleurs : {n['travailleurs'] or '·'}")
    t = b["total"]
    print(f"Total : {t['nuits_traitees']} nuit(s) traitée(s) sur {t['nuits']} dont {t['nuits_non_evaluables']} non "
          f"évaluable(s), {t['detections']} détections, "
          f"{t['alertes']} alertes, {t['cout_eur']:.2f} € HT".replace(".", ","))
    for j in b["journal"]:
        print(f"  journal {j['jour']} : {j['statut']} {j['motif']}")


@commande("sentinel2-candidats")                     # lecture seule : passages Sentinel 2 récents utilisables
def _sentinel2_candidats(conn, a):
    import requests
    from mars import sentinel2
    heures = int(a.args[0]) if a.args else 72
    rows = sentinel2.candidats_essai(conn, requests.Session(), load_rules(), datetime.now(timezone.utc), heures)
    if not rows:
        print(f"Aucun passage Sentinel 2 acquis dans les {heures} dernières heures ne couvre un navire des listes ou une "
              "demande d'observation")
    for r in rows:
        if "rien" in r:
            print(f"passage {r['passage']:>6}  rien à analyser : {r['rien']}")
            continue
        tuiles = ", ".join(f"{t['tuile']} {t['nuages_annonce'] or 0:.0f} % annoncés, masque {t['masque']}"
                           + ("" if t["heure_tuile"] else ", heure du catalogue") for t in r["tuiles"])
        print(f"passage {r['passage']:>6}  {r['acquis_le']:%d/%m/%Y %H:%M} UTC  {r['satellite']}  {r['points']} point(s), "
              f"{r['fenetres']} fenêtre(s), environ {r['cout_estime_eur']:.2f} € ; tuiles : {tuiles}".replace(".", ","))


@commande("sentinel2-heures")                        # heure propre des tuiles Sentinel 2 du calendrier (MTD_TL.xml)
def _sentinel2_heures(conn, a):
    import requests
    from mars import sentinel2
    r = sentinel2.corriger_heures(conn, requests.Session(), datetime.now(timezone.utc), int(a.args[0]) if a.args else 3)
    print(json.dumps(r, ensure_ascii=False))


@commande("sentinel2-poids", base=False)             # poids Sentinel 2 dans le seau des modèles (--nettoyer)
def _sentinel2_poids(_conn, a):
    """Poids du modèle Sentinel 2 copiés en flux, sans disque, dans le seau des modèles (jeton dédié sans filtre IP,
    seul à présigner pour un travailleur), depuis l'ancienne copie du seau principal ou le dépôt public d'AI2,
    empreinte SHA 256 vérifiée ; --nettoyer retire ensuite l'ancienne copie du seau principal (quota par compte)."""
    import requests
    from mars import sentinel2
    from mars.r2 import R2
    try:
        r = sentinel2.copier_poids(load_rules()["sentinel2"], R2(), R2(seau="modeles"), requests.Session(),
                                   nettoyer="--nettoyer" in a.args)
    except RuntimeError as e:
        raise SystemExit(str(e))
    print(f"{r['copies']} copié(s) ({r['octets'] / 1e9:.2f} Go), {r['deja']} déjà présent(s), {r['retires']} ancienne(s) "
          "copie(s) retirée(s) du seau principal".replace(".", ",", 1))


@commande("sentinel2-diagnostic", base=False)        # lecture seule : seau des modèles, variables, poids
def _sentinel2_diagnostic(_conn, _a):
    import requests
    from mars import diagnostic
    from mars.r2 import R2
    session = requests.Session()
    api = os.environ.get("MARS_API", "http://backend:8000/api")

    def variables():
        r = session.get(f"{api}/travailleurs/variables", timeout=10)
        r.raise_for_status()
        return r.json()
    try:
        principal = R2()
    except Exception:
        principal = None
    lines = diagnostic.sentinel2(load_rules(), session, variables, principal=principal)
    print(diagnostic.report(lines))
    manque = [r for st, r, _d in lines if st == diagnostic.MANQUE]
    traces.attributs(**{"diagnostic.manques": len(manque)})
    sys.exit(1 if manque else 0)


@commande("coupures-injection", base=False)          # coupures simulées dans des transactions annulées
def _coupures_injection(_conn, a):
    tranches = None
    if a.tranches:                                    # « 6-9,10- » : 6 à 9 messages dans l'heure, puis 10 et plus
        tranches = [(int(lo), int(hi) if hi else None) for lo, hi in (t.split("-") for t in a.tranches.split(","))]
    res = injection(a.jour or (datetime.now(timezone.utc) - timedelta(days=1)).date(), a.essais, tranches,
                    a.min_prior, (a.zone_du, a.zone_au) if a.zone_du else None,
                    _instant(a.episodes_depuis) if a.episodes_depuis else None)
    traces.attributs(**{"injection.tranches": a.tranches or "", "injection.zone_du": str(a.zone_du or "")})
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


@commande("mesures", base=False)
def _mesures(_conn, a):
    from mesures_calibration import main as mesures
    mesures(a.jours)


@commande("mesurer-tableaux")                        # lecture seule, sous le rôle de Grafana, transactions annulées
def _mesurer_tableaux(conn, _a):
    import importlib.util
    spec = importlib.util.spec_from_file_location("generer", ROOT / "grafana" / "generer.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    rows = supervision.mesurer_requetes(conn, gen.requetes())
    for r in sorted(rows, key=lambda r: -r["duree_s"]):
        print(f"{r['duree_s']:7.3f} s  {r['jours']:>2} j  {r['tableau']:<10} {r['panneau']:<48} "
              f"{r.get('erreur') or str(r['lignes']) + ' lignes'}")
    lentes = [r for r in rows if r["duree_s"] > 1 or r.get("erreur")]
    print(f"\n{len(rows)} mesures, la plus longue {max(r['duree_s'] for r in rows):.3f} s ; "
          f"{len(lentes)} au dessus de 1 s ou en erreur")
    sys.exit(1 if lentes else 0)


@commande("disque")
def _disque(conn, _a):
    print(archive.check_disk(conn, DISK_PATHS, DISK_PCT, log=log))


@commande("echeances")                              # lot Z1 : échéances des secrets, relevées et notifiées
def _echeances(conn, _a):
    print(json.dumps(run(conn, "echeances", lambda: echeances.relever(conn, load_rules())), ensure_ascii=False,
                     indent=2, default=str))


@commande("essai-restauration")                      # lot Z2 : dernière sauvegarde restaurée et comparée
def _essai_restauration(conn, _a):
    """Base temporaire sur le PostgreSQL en service, ou conteneur jetable si ESSAI_CIBLE (adresse de connexion) est
    donnée par scripts/essai_restauration.sh ; résultat consigné dans task_runs (essai_restauration)."""
    r = run(conn, "essai_restauration", lambda: essai_restauration(conn, os.environ.get("ESSAI_CIBLE") or None))
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if r is not None else 1)


@commande("deploiement")                             # lot Z2 : consigné par scripts/deployer.sh
def _deploiement(conn, _a):
    """Déploiement d'une version (DEPLOIEMENT : version, verdict, écarts, durée, migrations appliquées, en JSON),
    consigné dans task_runs, tracé ; écrit par scripts/deployer.sh sur le serveur, jamais lancé à la main."""
    d = json.loads(os.environ.get("DEPLOIEMENT") or "{}")
    if not d.get("version"):
        raise SystemExit("DEPLOIEMENT absente : commande réservée à scripts/deployer.sh")
    traces.attributs(**{"deploiement.version": d["version"], "deploiement.verdict": d.get("verdict", "?")})
    print(json.dumps(run(conn, "deploiement", lambda: d), ensure_ascii=False, default=str))


@commande("sauvegardes", base=False)
def _sauvegardes(_conn, _a):
    for o in sorted(r2().list(archive.BACKUP_PREFIX), key=lambda o: o["key"]):
        print(f"{o['key']}  {o['bytes'] / 1e6:.1f} Mo")


@commande("telecharger", base=False)
def _telecharger(_conn, a):
    key, dest = a.args
    r2().download(key, Path(dest))
    print(f"{key} enregistré dans {dest}")


@commande("restaurer-positions")
def _restaurer_positions(conn, a):
    from mars.ais.ingest import Ingestor
    if not a.du or not a.au:
        raise SystemExit("Préciser --du et --au (AAAA-MM-JJ)")
    client, ing = r2(), Ingestor(conn, LIVE, load_rules()["ingestion"])   # allègement neuf, sans reprise
    workdir = LIVE / ".restauration"
    workdir.mkdir(parents=True, exist_ok=True)
    d = a.du
    while d <= a.au:                          # dans l'ordre chronologique : continuité de l'allègement
        with traces.span("restauration.jour", jour=str(d)):
            log(json.dumps(archive.restore_positions(conn, client, ing, d, workdir), ensure_ascii=False))
        d += timedelta(days=1)
    ing.refresh_watch(force=True)


def nom_sortie(genre: str, du: date, au: date, quand: datetime) -> str:
    """Nom du rapport de trajectoire-mesures : type de mesure, période, instant du lancement (UTC)."""
    return f"trajectoire_{genre}_{du}_{au}_{quand:%Y%m%dT%H%M%SZ}.json"


@commande("trajectoire-mesures")       # lot T, phase 1 : lecture seule de R2 et de la base (docs/trajectoire.md)
def _trajectoire_mesures(conn, a):
    """Inventaire de l'archive, trajets, continuité et références de prévision (estime, routes fréquentes) ; zones en
    arguments (toutes par défaut), du 06/10/2026 à la veille par défaut ; rapport JSON dans data/ais_live/.trajectoire.
    À lancer dans un conteneur à part (docker compose run --rm), jamais dans celui de la boucle : mémoire de 768 Mo."""
    from mars import trajectoire_mesures as TM
    from mars.ais.live import ZONES
    du = a.du or date(2026, 10, 6)
    au = a.au or (datetime.now(timezone.utc).date() - timedelta(days=1))
    zones = [z for z in (a.args or list(ZONES)) if z in ZONES]
    travail = LIVE / ".trajectoire"
    out = TM.mesurer(conn, r2(), du, au, zones, a.test_jours, travail, log=log, regles=load_rules(),
                     cercle_seul=a.cercle_seul, hypotheses=a.hypotheses)
    # Un fichier par type de mesure et par lancement : une mesure n'en écrase jamais une autre (22/10/2026)
    genre = "hypotheses" if a.hypotheses else "cercle" if a.cercle_seul else "complete"
    sortie = travail / nom_sortie(genre, du, au, datetime.now(timezone.utc))
    sortie.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    log(f"Mesures écrites dans {sortie} ({sortie.stat().st_size} octets), {out['duree_s']} s, "
        f"mémoire maximale {out['memoire_max_mo']} Mo")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Tâches planifiées de MARS C2")
    ap.add_argument("commande", nargs="?", default="boucle", choices=list(COMMANDES))
    ap.add_argument("args", nargs="*")
    ap.add_argument("--du", type=date.fromisoformat)
    ap.add_argument("--au", type=date.fromisoformat)
    ap.add_argument("--simulation", action="store_true",
                    help="viirs-refusionner : lecture seule, liste ce qui changerait")
    ap.add_argument("--toutes", action="store_true", help="viirs-refusionner : toutes les nuits en base")
    ap.add_argument("--refaire", action="store_true",
                    help="essai-reel --lancer, sentinel-analyser : réanalyser un passage Sentinel 1 déjà analysé "
                         "(nouvelle analyse liée, l'ancienne gardée)")
    ap.add_argument("--nuit", type=date.fromisoformat, action="append",
                    help="viirs : nuit à retraiter (AAAA-MM-JJ, date du début de la nuit), répétable")
    ap.add_argument("--jours", type=int, default=14, help="mesures : nombre de jours examinés")
    ap.add_argument("--jour", type=date.fromisoformat, help="coupures-injection : journée (par défaut, la veille)")
    ap.add_argument("--essais", type=int, default=20, help="coupures-injection : navires par heure de coupure")
    ap.add_argument("--tranches", help="coupures-injection : tranches de messages dans l'heure, « 6-9,10- »")
    ap.add_argument("--min-prior", type=int, action="append", help="coupures-injection : seuil rejoué (répétable)")
    ap.add_argument("--zone-du", type=date.fromisoformat, help="coupures-injection : zone de comparaison, premier jour")
    ap.add_argument("--zone-au", type=date.fromisoformat, help="coupures-injection : zone de comparaison, dernier jour")
    ap.add_argument("--episodes-depuis", help="coupures-injection : coupures observées depuis (UTC), par tranche")
    ap.add_argument("--test-jours", type=int, default=5, help="trajectoire-mesures : derniers jours tenus à l'écart")
    ap.add_argument("--hypotheses", action="store_true",
                    help="trajectoire-mesures : routes fréquentes en plusieurs hypothèses (étape 2), de 1 à 30 h")
    ap.add_argument("--cercle-seul", action="store_true",
                    help="trajectoire-mesures : inventaire et cercle appris seulement (ni routes ni références)")
    ap.add_argument("--malgre-pause", action="store_true",
                    help="viirs, sentinel-analyser, sentinel2-analyser : lancer malgré la pause (consigné)")
    ap.add_argument("--motif", help="satellites-pause, --malgre-pause : motif consigné dans journal_controle")
    ap.add_argument("--lancer", type=int, metavar="ID",
                    help="essai-reel : lance l'essai réel sur le passage ID (sar_passes), types éprouvés seulement")
    ap.add_argument("--jusqua", help="satellites-pause on : reprise automatique (17/06/2024 08:00, heure de Paris)")
    return ap


def main():
    a = parser().parse_args()
    load_env()
    # Sortie d'une commande manuelle lisible telle quelle (diagnostics, tableaux) ; la boucle du service garde le suffixe
    traces.init("mars-taches", sortie_lisible=a.commande != "boucle")
    executer(a.commande, a)


if __name__ == "__main__":
    main()
