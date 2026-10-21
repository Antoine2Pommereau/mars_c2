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
    python scripts/taches.py sentinel-non-confirmees [--appliquer]   # AIS_UNCONFIRMED hors des fenêtres lues
    python scripts/taches.py sentinel2-candidats [H]  # passages Sentinel 2 récents utilisables (lecture seule)
    SENTINEL_PLAFOND_ESSAI=1 python scripts/taches.py sentinel2-analyser ID   # essai réel Sentinel 2 (coupé sinon)
    python scripts/taches.py sentinel2-poids [--nettoyer] # poids Sentinel 2 dans le seau des modèles (une fois)
    python scripts/taches.py sentinel2-diagnostic     # seau des modèles, variables des conteneurs, poids (lecture seule)
    python scripts/taches.py sentinel2-heures [J]     # heure propre des tuiles Sentinel 2 du calendrier
    python scripts/taches.py sentinel-diagnostic       # prêt pour Sentinel ? lecture seule, aucune création, aucun coût
    python scripts/taches.py sentinel-candidats [H]    # passages des H (36) dernières heures avec un navire des listes
    python scripts/taches.py sentinel-lever-incompatible TYPE ZONE   # lève le marquage d'un GPU inutilisable
    python scripts/taches.py coupures-injection [--jour 2026-10-09] [--essais 20]
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

from mars import archive, controle, supervision, traces
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
    }


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


def launch_viirs(conn, manuel: bool = False, malgre_pause: bool = False, motif: str | None = None) -> dict:
    """Granules des dernières nuits pas encore traitées, confiées à un travailleur éphémère (un seul à la fois)."""
    import requests
    from mars import travailleurs, viirs
    rules = regles(conn)
    pause = garde_pause(conn, rules, "viirs", manuel, malgre_pause, motif)
    if pause:
        return pause
    scw, token = travailleurs.Scaleway.from_env(), os.environ.get("EARTHDATA_TOKEN")
    if scw is None or not token:
        raise travailleurs.RefusLancement("clés absentes du .env : SCW_SECRET_KEY, SCW_PROJECT_ID, EARTHDATA_TOKEN")
    busy = conn.execute("SELECT id FROM travailleurs WHERE tache = 'viirs' AND detruit_le IS NULL").fetchone()
    if busy:                     # travailleur actif : ses nuits sont couvertes, rien à lancer (le verrou en base le garantit)
        return {"en_cours": busy[0]}
    p = viirs.plan(conn, requests.Session(), rules, datetime.now(timezone.utc))
    if not p["granules"]:
        return {"nuits": p["nuits"], "granules": 0}
    r = travailleurs.launch(conn, scw, rules, "viirs", {"granules": p["granules"], "regions": viirs.regions_boxes()},
                            {"EARTHDATA_TOKEN": token}, log=log)
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
                    motif_pause: str | None = None) -> dict:
    """Passages Sentinel 1 à analyser (corridor avec un navire des listes, alerte VIIRS), ou un passage choisi à la
    main : une session GPU, dans les limites du plafond de scènes et du budget (mars/sentinel.py). `auto` : appel de
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
    r = sentinel.launch(conn, scw, rules, datetime.now(timezone.utc), requests.Session(), pass_id, log=log)
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


def injection(day: date, essais: int) -> dict:
    """Test par injection de la règle des coupures AIS (mars/rules.py, ais_gap_injection) : coupures de 3 h à 2, 6,
    10, 14 et 18 h UTC, sur une connexion asyncpg dédiée ; chaque essai est annulé, la base n'est pas modifiée."""
    import asyncio
    import asyncpg
    from mars.rules import ais_gap_injection

    async def go():
        c = await asyncpg.connect(database_url())
        try:
            if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM reception_cells)"):
                return {"refus": "zone de réception fiable absente : lancer scripts/build_masks.py"}
            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            return await ais_gap_injection(c, start, load_rules(), essais=essais, heures=[2, 6, 10, 14, 18])
        finally:
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
    if run(conn, "viirs", lambda: launch_viirs(conn, True, a.malgre_pause, a.motif),
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
    zones = sorted({p["zone"], *(t.get("zone", p["zone"]) for t in p["types"].values())})   # GPU : fr-par-2
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
    print(f"  {'tip':<12} {'tips':>5} {'regroupés':>10} {'passage':>8} {'côte':>5} {'incertitude':>12} {'délai':>6} "
          f"{'transit':>8} {'sans 36 h':>10}")
    for tip, d in sorted(r["par_tip"].items()):
        print(f"  {tip:<12} {d['tips']:>5} {d['regroupees']:>10} {d['passage']:>8} {d['cote']:>5} {d['incertitude']:>12} "
              f"{d['delai']:>6} {d['transit']:>8} {d['sans_passage_36h']:>10}")
    delais = sorted(x["delai_h"] for x in r["lignes"] if x["issue"] == "passage")
    if delais:
        print(f"  délai du tip au passage : médiane {delais[len(delais) // 2]} h, de {delais[0]} à {delais[-1]} h")
    print(f"  {r['sessions']} session(s) ({r['sessions_par_jour']} par jour), {r['fenetres']} fenêtre(s), "
          f"{r['cout_eur']:.2f} € sur la période, environ {r['cout_mois_eur']:.2f} € par mois".replace(".", ","))


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
    res = viirs.reevaluate(conn, load_rules(), debut)
    devenues = conn.execute("SELECT count(*) FROM viirs_detections WHERE id = ANY(%s) AND non_evaluable IS NULL",
                            (coupees,)).fetchone()[0]
    res |= {"periode_avant": avant, "periode_apres": compte(), "coupure_puis_evaluees": devenues}
    traces.attributs(**{"viirs.coupure_puis_evaluees": devenues, "viirs.coupure_avant": len(coupees)})
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


@commande("sentinel-flux")                           # drapeau flux_coupe d'une analyse recalculé (statistiques réparées)
def _sentinel_flux(conn, a):
    from mars import sentinel
    if not a.args:
        raise SystemExit("Préciser l'analyse : taches.py sentinel-flux 97")
    for aid in a.args:
        r = sentinel.recalculer_flux(conn, int(aid), load_rules())
        traces.attributs(**{"sentinel.analyse": int(aid), "sentinel.redevenues_evaluables": len(r["redevenues_evaluables"])})
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        if r["redevenues_evaluables"]:
            print(f"{len(r['redevenues_evaluables'])} détection(s) redevenue(s) évaluable(s), sans alerte : examiner "
                  "leur fiche, ou relancer l'analyse du passage")


@commande("sentinel-diagnostic")                     # lecture seule : Copernicus, puis Scaleway
def _sentinel_diagnostic(conn, _a):
    import requests
    from mars import diagnostic, travailleurs
    # Volumes bloc enregistrés des travailleurs Sentinel récents : chacun doit avoir été supprimé
    recorded = conn.execute("SELECT id, volumes_bloc FROM travailleurs WHERE tache = 'sentinel' AND "
                            "cardinality(volumes_bloc) > 0 AND cree_le > now() - interval '30 days' "
                            "ORDER BY id").fetchall()
    marques = conn.execute("SELECT commercial_type, zone, depuis, jusqu_au, message, travailleur_id FROM types_incompatibles "
                           "WHERE leve_le IS NULL AND jusqu_au > now() ORDER BY depuis").fetchall()
    lines = diagnostic.run(load_rules(), requests.Session(), travailleurs.Scaleway.from_env(), recorded=recorded,
                           marques=marques)
    print(diagnostic.report(lines))
    sys.exit(1 if any(s == diagnostic.MANQUE for s, _r, _d in lines) else 0)


def _sentinel(manual: bool):
    def go(conn, a):
        pid = int(a.args[0]) if manual and a.args else None
        if manual and pid is None:
            raise SystemExit("Préciser l'identifiant du passage : taches.py sentinel-analyser ID (table sar_passes)")
        if run(conn, "sentinel", lambda: launch_sentinel(conn, pid, malgre_pause=a.malgre_pause, motif_pause=a.motif),
               parent=traces.traceparent()) is None:
            sys.exit(1)
    return go


@commande("sentinel-lever-incompatible")              # GPU de nouveau utilisable (pilote corrigé) : marquage levé
def _sentinel_lever_incompatible(conn, a):
    if len(a.args) != 2:
        raise SystemExit("Préciser le type et la zone : taches.py sentinel-lever-incompatible L40S-1-48G fr-par-2")
    n = conn.execute("UPDATE types_incompatibles SET leve_le = now() WHERE commercial_type = %s AND zone = %s "
                     "AND leve_le IS NULL AND jusqu_au > now()", tuple(a.args)).rowcount
    traces.attributs(**{"marquage.type": a.args[0], "marquage.zone": a.args[1], "marquage.leves": n})
    print(f"{n} marquage(s) levé(s) pour {a.args[0]} en {a.args[1]}" if n else
          f"Aucun marquage actif pour {a.args[0]} en {a.args[1]}")


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


@commande("controle-diagnostic", base=False)       # lecture seule : lignes MARS_OPERATEURS (taches et backend)
def _controle_diagnostic(_conn, _a):
    """MARS_OPERATEURS lue dans ce conteneur et dans l'API (GET /api/controle/session : noms et raisons seulement) ;
    code de sortie 1 si une ligne est abîmée ou absente."""
    import requests
    from mars import diagnostic
    lignes = []
    ok, abimes = controle.operateurs(), controle.operateurs_abimes()
    for n in ok:
        lignes.append((diagnostic.PRET, f"Opérateur {n} (taches)", "empreinte de 64 caractères, bien formée"))
    for n, r in abimes.items():
        lignes.append((diagnostic.MANQUE, f"Opérateur {n} (taches)", controle.message_abime(n, r)))
    if not ok and not abimes:
        lignes.append((diagnostic.MANQUE, "MARS_OPERATEURS (taches)", "absente du .env : panneau en lecture seule"))
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


@commande("sentinel-non-confirmees")                 # alertes AIS_UNCONFIRMED hors des fenêtres lues (21/10/2026)
def _sentinel_non_confirmees(conn, a):
    """Lecture seule par défaut ; --appliquer : classe avec motif les alertes encore nouvelles, annote les autres."""
    from mars import sentinel
    r = sentinel.non_confirmees_hors_fenetres(conn, appliquer="--appliquer" in a.args)
    print(f"{r['examinees']} alerte(s) AIS_UNCONFIRMED de Sentinel 1 examinée(s), {len(r['hors_fenetres'])} hors des "
          f"fenêtres lues" + (f" : {r['classees']} classée(s) avec motif, {r['annotees']} annotée(s) (décision "
                               "d'opérateur conservée)" if "--appliquer" in a.args else " (lecture seule, --appliquer pour "
                                                                                       "corriger)"))
    for x in r["hors_fenetres"]:
        print(f"  alerte {x['alerte']} (analyse {x['analyse']}), statut {x['statut']}"
              + (", décision d'opérateur" if x["decisions"] else ""))


@commande("sentinel2-analyser")                      # essai réel : SENTINEL_PLAFOND_ESSAI=1 (Sentinel 2 coupé)
def _sentinel2_analyser(conn, a):
    if not a.args:
        raise SystemExit("Préciser l'identifiant du passage Sentinel 2 : taches.py sentinel2-analyser ID (sar_passes)")
    if run(conn, "sentinel2", lambda: launch_sentinel2(conn, int(a.args[0]), a.malgre_pause, a.motif),
           parent=traces.traceparent()) is None:
        sys.exit(1)


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
    print(json.dumps(injection(a.jour or (datetime.now(timezone.utc) - timedelta(days=1)).date(), a.essais),
                     ensure_ascii=False, indent=2, default=str))


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


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Tâches planifiées de MARS C2")
    ap.add_argument("commande", nargs="?", default="boucle", choices=list(COMMANDES))
    ap.add_argument("args", nargs="*")
    ap.add_argument("--du", type=date.fromisoformat)
    ap.add_argument("--au", type=date.fromisoformat)
    ap.add_argument("--jours", type=int, default=14, help="mesures : nombre de jours examinés")
    ap.add_argument("--jour", type=date.fromisoformat, help="coupures-injection : journée (par défaut, la veille)")
    ap.add_argument("--essais", type=int, default=20, help="coupures-injection : navires par heure de coupure")
    ap.add_argument("--malgre-pause", action="store_true",
                    help="viirs, sentinel-analyser, sentinel2-analyser : lancer malgré la pause (consigné)")
    ap.add_argument("--motif", help="satellites-pause, --malgre-pause : motif consigné dans journal_controle")
    ap.add_argument("--jusqua", help="satellites-pause on : reprise automatique (17/06/2024 08:00, heure de Paris)")
    return ap


def main():
    a = parser().parse_args()
    load_env()
    traces.init("mars-taches")
    executer(a.commande, a)


if __name__ == "__main__":
    main()
