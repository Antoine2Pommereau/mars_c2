"""Tableaux Grafana de MARS C2, écrits en code : ce script produit les fichiers JSON de
grafana/provisioning/dashboards/mars (relus par Grafana au démarrage, jamais modifiés à la main).

    python grafana/generer.py          # régénère les sept tableaux ; tests/test_supervision.py vérifie qu'ils sont à jour

Sobres et sombres, aux couleurs de MARS C2 : séries en gris et en bleu du signal système, la couleur n'est donnée qu'à
ce qui demande l'attention (seuil franchi, échec, alerte). Requêtes sur des tables de statistiques et de mesures,
jamais sur la table des positions (elles doivent rester sous la seconde, docs/supervision.md). Heures en UTC.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

OUT = Path(__file__).resolve().parent / "provisioning" / "dashboards" / "mars"
DS = {"type": "grafana-postgresql-datasource", "uid": "mars"}
TEMPO = {"type": "tempo", "uid": "tempo"}


def lien_trace(colonne: str = "${__value.raw}") -> str:
    # Exploration Tempo sur l'identifiant de la trace : Grafana l'ouvre directement en vue Gantt (chronologie des spans)
    """Adresse de l'exploration Tempo pour un identifiant de trace (lien d'une cellule de tableau)."""
    pane = {"a": {"datasource": "tempo", "queries": [{"refId": "A", "datasource": TEMPO, "queryType": "traceql",
                                                         "query": "__TRACE__"}], "range": {"from": "now-3d", "to": "now"}}}
    return ("/explore?schemaVersion=1&orgId=1&panes=" + quote(json.dumps(pane, separators=(",", ":")), safe="")
            ).replace("__TRACE__", colonne)

NEUTRE, CLAIR, SIGNAL = "#7c8b97", "#c2ccd3", "#4fb6c8"
VERT, GRIS = "#3f8f62", "#4c5a66"                 # tuiles de la vue d'ensemble : vert éteint, gris sans mesure
ROUGE, ORANGE, SOMBRE = "#ef6461", "#f0a84b", "#e85bc7"
NUANCES = [SIGNAL, CLAIR, NEUTRE, "#5a6b78", "#9fb3c1", "#3f6b78"]
TYPES = {"WATCHLIST": "#b48cf2", "IDENTITY_CHANGE": "#5fd3a5", "RENDEZVOUS": "#f0a84b", "AIS_GAP": "#ef6461",
         "DARK_SHIP": "#e85bc7", "AIS_UNCONFIRMED": "#e8d45a", "IDENTITY_MISMATCH": "#c9a0ff"}
GRAVITES = {"elevee": ROUGE, "moyenne": ORANGE, "faible": NEUTRE}
STATUTS = {"echec": ("Échec", ROUGE), "ok": ("Réussie", NEUTRE), "en_cours": ("En cours", SIGNAL)}


class Tableau:
    def __init__(self, uid: str, titre: str, description: str, de: str = "now-48h"):
        self.uid, self.titre, self.description, self.de = uid, titre, description, de
        self.panels: list[dict] = []
        self.x = self.y = self.h_ligne = 0

    def _place(self, w: int, h: int) -> dict:
        if self.x + w > 24:
            self.x, self.y = 0, self.y + self.h_ligne
            self.h_ligne = 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.h_ligne = max(self.h_ligne, h)
        return pos

    def section(self, titre: str):
        if self.x:
            self.x, self.y, self.h_ligne = 0, self.y + self.h_ligne, 0
        self.panels.append({"type": "row", "title": titre, "collapsed": False, "id": len(self.panels) + 1,
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "panels": []})
        self.y += 1

    def _panel(self, kind: str, titre: str, sql: str, w: int, h: int, fmt: str, description: str, defaults: dict,
               options: dict, overrides: list | None = None, transformations: list | None = None) -> dict:
        p = {"id": len(self.panels) + 1, "type": kind, "title": titre, "description": description, "datasource": DS,
             "gridPos": self._place(w, h), "transparent": True,
             "targets": [{"refId": "A", "datasource": DS, "editorMode": "code", "rawQuery": True, "format": fmt,
                          "rawSql": " ".join(sql.split())}],
             "fieldConfig": {"defaults": defaults, "overrides": overrides or []}, "options": options}
        if transformations:
            p["transformations"] = transformations
        self.panels.append(p)
        return p

    def chiffre(self, titre: str, sql: str, unite: str = "none", seuils=((None, NEUTRE),), w: int = 4, h: int = 4,
                description: str = "", decimales: int | None = 0, vide: str = "aucune mesure"):
        d = {"unit": unite, "color": {"mode": "thresholds"}, "noValue": vide,
             "thresholds": {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in seuils]}}
        if decimales is not None:
            d["decimals"] = decimales
        self._panel("stat", titre, sql, w, h, "table", description, d,
                    {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value",
                     "graphMode": "none", "textMode": "value", "justifyMode": "center", "wideLayout": True,
                     "showPercentChange": False})

    def courbe(self, titre: str, sql: str, unite: str = "none", w: int = 12, h: int = 8, description: str = "",
               barres: bool = False, empile: bool = False, couleurs: dict | None = None, seuil_ligne=None,
               fmt: str = "time_series", couleur: str = SIGNAL):
        d = {"unit": unite, "color": {"mode": "fixed", "fixedColor": couleur},
             "custom": {"drawStyle": "bars" if barres else "line", "lineWidth": 1, "fillOpacity": 60 if barres else 8,
                        "pointSize": 3, "showPoints": "never", "spanNulls": False, "axisBorderShow": False,
                        "stacking": {"mode": "normal" if empile else "none", "group": "A"},
                        "thresholdsStyle": {"mode": "line" if seuil_ligne is not None else "off"}}}
        if seuil_ligne is not None:
            d["thresholds"] = {"mode": "absolute", "steps": [{"value": None, "color": "transparent"},
                                                             {"value": seuil_ligne, "color": ROUGE}]}
        overrides = []
        if couleurs is not None:
            d["color"] = {"mode": "fixed", "fixedColor": NEUTRE}
            for k, (nom, c) in enumerate(couleurs.items()):
                overrides.append({"matcher": {"id": "byName", "options": nom},
                                  "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]})
        self._panel("timeseries", titre, sql, w, h, fmt, description, d,
                    {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                     "tooltip": {"mode": "multi", "sort": "desc"}}, overrides)

    def table(self, titre: str, sql: str, w: int = 24, h: int = 8, description: str = "", etats: dict | None = None,
              colonne_etat: str | None = None, unites: dict | None = None, trace: str | None = None,
              vide: str | None = None):
        overrides = []
        if trace:                                   # colonne de l'identifiant de trace : lien vers Tempo
            overrides.append({"matcher": {"id": "byName", "options": trace}, "properties": [
                {"id": "links", "value": [{"title": "Ouvrir la trace dans Tempo (vue Gantt)", "url": lien_trace()}]},
                {"id": "custom.width", "value": 120}]})
        if etats and colonne_etat:
            overrides.append({"matcher": {"id": "byName", "options": colonne_etat}, "properties": [
                {"id": "mappings", "value": [{"type": "value", "options": {
                    k: {"text": t, "color": c, "index": i} for i, (k, (t, c)) in enumerate(etats.items())}}]},
                {"id": "custom.cellOptions", "value": {"type": "color-text"}}]})
        for col, unite in (unites or {}).items():
            overrides.append({"matcher": {"id": "byName", "options": col}, "properties": [{"id": "unit", "value": unite}]})
        self._panel("table", titre, sql, w, h, "table", description,
                    {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}, "color": {"mode": "fixed",
                                                                                           "fixedColor": CLAIR},
                     **({"noValue": vide} if vide else {})},
                    {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}, overrides)

    def tuile(self, titre: str, sql: str, tableau: str, description: str = ""):
        """Tuile d'état de la vue d'ensemble. La requête renvoie une ligne : time, metric (texte court affiché) et
        value (niveau calculé en SQL avec les seuils de parametres_supervision : 0 vert, 1 orange, 2 rouge, -1 sans
        mesure) ; la couleur suit le niveau, le texte est le nom de la série. Un clic ouvre le tableau détaillé."""
        d = {"color": {"mode": "thresholds"}, "noValue": "aucune mesure",
             "thresholds": {"mode": "absolute", "steps": [{"value": None, "color": GRIS}, {"value": 0, "color": VERT},
                                                          {"value": 1, "color": ORANGE}, {"value": 2, "color": ROUGE}]},
             "links": [{"title": f"Ouvrir le tableau {tableau}", "url": f"/d/{TABLEAUX_UID[tableau]}?${{__url_time_range}}"}]}
        self._panel("stat", titre, sql, 4, 5, "time_series", description, d,
                    {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                     "colorMode": "background_solid", "graphMode": "none", "textMode": "name",
                     "justifyMode": "center", "wideLayout": True, "showPercentChange": False,
                     "text": {"titleSize": 20, "valueSize": 20}})

    def tempo(self, titre: str, requete: str, w: int = 12, h: int = 9, description: str = "", spans: bool = False,
              metriques: bool = False, limite: int = 50):
        """Panneau sur Tempo : recherche TraceQL (tableau des traces ou des spans), ou TraceQL metrics (courbe)."""
        cible = {"refId": "A", "datasource": TEMPO, "queryType": "traceql", "query": requete, "limit": limite,
                 "tableType": "spans" if spans else "traces"}
        if metriques:                               # pas choisi par Grafana selon la période affichée
            cible.update({"metricsQueryType": "range"})
            cible.pop("tableType")
            cible.pop("limit")
        p = {"id": len(self.panels) + 1, "type": "timeseries" if metriques else "table", "title": titre,
             "description": description, "datasource": TEMPO, "gridPos": self._place(w, h), "transparent": True,
             "targets": [cible], "options": {"showHeader": True, "cellHeight": "sm"} if not metriques else
             {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
             "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"} if metriques else
                                          {"mode": "fixed", "fixedColor": CLAIR},
                                          **({"unit": "s"} if metriques else {})}, "overrides": []}}
        self.panels.append(p)

    def json(self) -> dict:
        return {"uid": self.uid, "title": self.titre, "description": self.description, "tags": ["mars-c2"],
                "timezone": "utc", "editable": False, "graphTooltip": 1, "schemaVersion": 41, "version": 1,
                "refresh": "1m", "time": {"from": self.de, "to": "now"},
                "timepicker": {"refresh_intervals": ["1m", "5m", "15m", "1h"]},
                "links": [{"title": "Tableaux MARS C2", "type": "dashboards", "tags": ["mars-c2"], "asDropdown": False,
                           "includeVars": False, "keepTime": True}],
                "panels": self.panels, "templating": {"list": []}, "annotations": {"list": []}}


# Requêtes partagées : le tableau Direct et les règles d'alerte disent la même chose

MINUTES_COLLECTE = ("SELECT extract(epoch FROM now() - dernier_le) / 60 AS minutes FROM signes_de_vie "
                    "WHERE service = 'collecte'")
MINUTES_INGESTION = ("SELECT greatest(extract(epoch FROM now() - dernier_le), coalesce((detail->>'retard_s')::float8, "
                     "0)) / 60 AS minutes FROM signes_de_vie WHERE service = 'ingestion'")


def direct() -> Tableau:
    t = Tableau("mars-direct", "Direct", "Collecte AISStream, ingestion, trafic par région, coupures du flux")
    t.chiffre("Dernier fichier de la collecte", MINUTES_COLLECTE, "m",
              ((None, NEUTRE), (5, ROUGE)), w=6, decimales=1,
              description="Minutes depuis le dernier fichier Parquet écrit (relevé chaque minute). Alerte et "
                          "redémarrage automatique au delà de 5 minutes.")
    t.chiffre("Retard de l'ingestion", MINUTES_INGESTION, "m", ((None, NEUTRE), (3, ORANGE), (10, ROUGE)),
              w=6, decimales=1, description="Minutes depuis le dernier passage réussi, ou attente du plus ancien "
                                            "fichier à charger. Alerte au delà de 10 minutes.")
    t.chiffre("Messages par minute, dernière heure",
              "SELECT sum(rows_read) / 60.0 AS messages FROM ingested_files WHERE ingested_at > now() - interval "
              "'1 hour' AND folder LIKE 'positions/%'", "none", ((None, ROUGE), (100, NEUTRE)), w=6,
              description="Positions lues dans les fichiers de la collecte, moyenne de la dernière heure.")
    t.chiffre("Signe de vie de taches",
              "SELECT extract(epoch FROM now() - dernier_le) / 60 AS minutes FROM signes_de_vie WHERE service = "
              "'taches'", "m", ((None, NEUTRE), (3, ROUGE)), w=6, decimales=1,
              description="Minutes depuis le dernier tour de la boucle des tâches (mesures, règles, alertes de "
                          "secours).")
    t.courbe("Messages par minute",
             """SELECT $__timeGroupAlias(t, '1m', 0), sum(n) AS "messages" FROM (
                  SELECT to_timestamp(split_part(folder, 'date=', 2) || ' ' || left(name, 6), 'YYYY-MM-DD HH24MISS')
                         ::timestamp AT TIME ZONE 'UTC' AS t, rows_read AS n
                  FROM ingested_files WHERE ingested_at >= $__timeFrom() AND folder LIKE 'positions/%') f
                WHERE $__timeFilter(t) GROUP BY 1 ORDER BY 1""", w=12,
             description="Positions lues par minute de collecte (registre d'ingestion : les deux derniers jours, les "
                         "journées archivées en sortent).")
    t.courbe("Positions conservées par minute",
             """SELECT $__timeGroupAlias(minute, '1m', 0), sum(positions) AS "positions" FROM stats_minute
                WHERE $__timeFilter(minute) GROUP BY 1 ORDER BY 1""", w=12,
             description="Après allègement (un point par minute en route, toutes les dix minutes à l'arrêt). Un "
                         "creux à zéro est une coupure du flux, comme le 18/10/2026.")
    t.courbe("Positions par heure et par région",
             """SELECT $__timeGroupAlias(tranche, '1h', 0), region AS metric, sum(positions) AS value
                FROM stats_10min_region WHERE $__timeFilter(tranche) GROUP BY 1, 2 ORDER BY 1""", w=12,
             couleurs={"bretagne": SIGNAL, "manche": CLAIR, "gascogne": NEUTRE, "mediterranee": "#3f6b78"})
    t.table("Coupures du flux",
            """WITH bornes AS (SELECT greatest($__timeFrom()::timestamptz, min(minute)) AS a,
                                      least($__timeTo()::timestamptz, max(minute)) AS b
                               FROM stats_minute WHERE minute <= $__timeTo()::timestamptz),
                    m AS (SELECT g AS minute, coalesce(s.positions, 0) AS n FROM bornes,
                          generate_series(date_trunc('minute', bornes.a), bornes.b, interval '1 minute') g
                          LEFT JOIN stats_minute s ON s.minute = g),
                    seuil AS (SELECT coalesce((SELECT valeur FROM parametres_supervision WHERE cle = 'ratio_coupure'),
                                              0.2) * percentile_cont(0.5) WITHIN GROUP (ORDER BY n) AS v
                              FROM m WHERE n > 0),
                    c AS (SELECT minute, minute - row_number() OVER (ORDER BY minute) * interval '1 minute' AS grp
                          FROM m, seuil WHERE n < seuil.v)
               SELECT min(minute) AS "Début", max(minute) + interval '1 minute' AS "Fin",
                      count(*) AS "Durée" FROM c GROUP BY grp HAVING count(*) >= 3 ORDER BY 1 DESC LIMIT 100""",
            w=12, description="Minutes sous 20 % de la médiane de la période (définition de la frise de l'interface), "
                              "trois minutes au moins.", unites={"Durée": "m"})
    return t


def disque() -> Tableau:
    t = Tableau("mars-disque", "Disque et base", "Espace libre, taille de la base et des positions, croissance",
                de="now-30d")
    last = "FROM mesures_disque ORDER BY mesure_le DESC LIMIT 1"
    t.chiffre("Espace libre", f"SELECT libre_pct AS libre {last}", "percent",
              ((None, ROUGE), (15, ORANGE), (25, NEUTRE)), decimales=1,
              description="Alerte sous 15 % (incident du 18/10/2026 : disque plein, collecte et ingestion arrêtées).")
    t.chiffre("Libre", f"SELECT libre_octets AS libre {last}", "decbytes", decimales=1)
    t.chiffre("Base", f"SELECT base_octets AS base {last}", "decbytes", decimales=1)
    t.chiffre("Positions", f"SELECT positions_octets AS positions {last}", "decbytes", decimales=1,
              description="Table des positions, index compris (30 jours gardés).")
    croissance = """WITH a AS (SELECT mesure_le, total_octets - libre_octets AS u FROM mesures_disque
                               WHERE mesure_le > now() - interval '7 days' ORDER BY mesure_le LIMIT 1),
                         b AS (SELECT mesure_le, total_octets - libre_octets AS u, libre_octets AS libre
                               FROM mesures_disque ORDER BY mesure_le DESC LIMIT 1)"""
    t.chiffre("Croissance par jour",
              croissance + """ SELECT (b.u - a.u) / (extract(epoch FROM b.mesure_le - a.mesure_le) / 86400) AS jour
                               FROM a, b WHERE b.mesure_le - a.mesure_le > interval '6 hours'""", "decbytes",
              decimales=1, description="Espace occupé gagné par jour, sur les sept derniers jours (purge et archivage "
                                       "compris).", vide="moins de 6 h de mesures")
    t.chiffre("Jours restants",
              croissance + """ SELECT CASE WHEN b.u > a.u THEN b.libre / ((b.u - a.u) /
                                   (extract(epoch FROM b.mesure_le - a.mesure_le) / 86400)) END AS jours
                               FROM a, b WHERE b.mesure_le - a.mesure_le > interval '6 hours'""", "none",
              ((None, ROUGE), (7, ORANGE), (30, NEUTRE)), decimales=0,
              description="Au rythme des sept derniers jours ; vide si l'occupation ne croît pas.", vide="stable")
    t.courbe("Espace libre", """SELECT mesure_le AS time, libre_pct AS "espace libre" FROM mesures_disque
                                WHERE $__timeFilter(mesure_le) ORDER BY 1""", "percent", w=12, seuil_ligne=15)
    t.courbe("Taille de la base, des positions et de la collecte",
             """SELECT mesure_le AS time, base_octets AS "base", positions_octets AS "positions",
                       ais_live_octets AS "collecte (data/ais_live)" FROM mesures_disque
                WHERE $__timeFilter(mesure_le) ORDER BY 1""", "decbytes", w=12, fmt="table",
             couleurs={"base": SIGNAL, "positions": CLAIR, "collecte (data/ais_live)": NEUTRE})
    t.courbe("Croissance par jour",
             """WITH d AS (SELECT DISTINCT ON (date_trunc('day', mesure_le)) date_trunc('day', mesure_le) AS jour,
                                  base_octets, positions_octets, total_octets - libre_octets AS occupe
                           FROM mesures_disque WHERE $__timeFilter(mesure_le)
                           ORDER BY date_trunc('day', mesure_le), mesure_le DESC)
                SELECT jour AS time, base_octets - lag(base_octets) OVER (ORDER BY jour) AS "base",
                       positions_octets - lag(positions_octets) OVER (ORDER BY jour) AS "positions",
                       occupe - lag(occupe) OVER (ORDER BY jour) AS "disque occupé" FROM d ORDER BY 1""",
             "decbytes", w=24, barres=True, fmt="table",
             couleurs={"base": SIGNAL, "positions": CLAIR, "disque occupé": NEUTRE})
    return t


def taches() -> Tableau:
    t = Tableau("mars-taches", "Tâches", "Tâches planifiées, cycle des règles, redémarrages automatiques, échecs")
    t.chiffre("Échecs sur 7 jours", "SELECT count(*) AS echecs FROM task_runs WHERE status = 'echec' AND started_at > "
                                    "now() - interval '7 days'", seuils=((None, NEUTRE), (1, ORANGE)), w=6)
    t.chiffre("Redémarrages automatiques sur 7 jours",
              "SELECT count(*) AS redemarrages FROM task_runs WHERE task LIKE 'redemarrage_%' AND started_at > now() - "
              "interval '7 days'", seuils=((None, NEUTRE), (1, ORANGE)), w=6)
    t.chiffre("Durée du dernier cycle des règles",
              "SELECT extract(epoch FROM finished_at - started_at) AS duree FROM task_runs WHERE task = 'regles' AND "
              "finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 1", "s", ((None, NEUTRE), (120, ORANGE),
                                                                                (240, ROUGE)), w=6, decimales=1,
              description="Le cycle revient toutes les 5 minutes.")
    t.chiffre("Tâches en échec deux fois de suite",
              """SELECT count(*) AS taches FROM (SELECT task, array_agg(status ORDER BY started_at DESC) AS s
                 FROM task_runs WHERE started_at > now() - interval '7 days' AND status <> 'en_cours'
                 GROUP BY task) x WHERE s[1] = 'echec' AND s[2] = 'echec'""",
              seuils=((None, NEUTRE), (1, ROUGE)), w=6)
    t.table("Dernière exécution de chaque tâche",
            """SELECT DISTINCT ON (task) task AS "Tâche", status AS "Statut", started_at AS "Début",
                      extract(epoch FROM finished_at - started_at) AS "Durée",
                      left(coalesce(details->>'erreur', details->>'motif', ''), 160) AS "Motif",
                      trace_id AS "Trace"
               FROM task_runs WHERE started_at > now() - interval '30 days' ORDER BY task, started_at DESC""",
            h=10, etats=STATUTS, colonne_etat="Statut", unites={"Durée": "s"}, trace="Trace")
    t.courbe("Cycle des règles par étape",
             """SELECT started_at AS time, e.k AS metric, e.v::float8 AS value
                FROM task_runs, jsonb_each_text(details->'durees_s') AS e(k, v)
                WHERE task = 'regles' AND $__timeFilter(started_at) ORDER BY 1""", "s", w=12, barres=True,
             empile=True, description="Durée de chaque étape du cycle (deux jours gardés).")
    t.courbe("Durée des tâches",
             """SELECT started_at AS time, task AS metric, extract(epoch FROM finished_at - started_at) AS value
                FROM task_runs WHERE $__timeFilter(started_at) AND finished_at IS NOT NULL
                AND task NOT IN ('regles', 'sentinel') AND task NOT LIKE 'redemarrage_%' ORDER BY 1""", "s", w=12)
    t.table("Redémarrages automatiques",
            """SELECT started_at AS "Le", replace(task, 'redemarrage_', '') AS "Service", details->>'motif' AS "Motif"
               FROM task_runs WHERE task LIKE 'redemarrage_%' AND $__timeFilter(started_at)
               ORDER BY 1 DESC LIMIT 100""", w=12)
    t.table("Échecs des 7 derniers jours",
            """SELECT started_at AS "Le", task AS "Tâche",
                      left(coalesce(details->>'erreur', details->>'motif', ''), 200) AS "Motif", trace_id AS "Trace"
               FROM task_runs WHERE status = 'echec' AND started_at > now() - interval '7 days'
               ORDER BY 1 DESC LIMIT 200""", w=12, trace="Trace")
    return t


def satellites() -> Tableau:
    t = Tableau("mars-satellites", "Satellites", "Nuits VIIRS, travailleurs éphémères, coût du mois, passages Sentinel",
                de="now-30d")
    t.chiffre("Coût Sentinel du mois",
              """SELECT coalesce(sum((mesures->>'cout_estime_eur')::float8), 0) AS cout FROM travailleurs
                 WHERE tache = 'sentinel' AND cree_le >= date_trunc('month', now())""", "currencyEUR", decimales=2)
    t.chiffre("Part du budget Sentinel",
              """SELECT 100 * coalesce(sum((mesures->>'cout_estime_eur')::float8), 0) /
                        nullif((SELECT valeur FROM parametres_supervision WHERE cle = 'budget_sentinel_eur'), 0)
                        AS part FROM travailleurs WHERE tache = 'sentinel' AND cree_le >= date_trunc('month', now())""",
              "percent", ((None, NEUTRE), (80, ORANGE), (100, ROUGE)), decimales=0,
              description="Coût estimé du mois rapporté au budget de config/rules.yaml (sentinel.budget_mensuel_eur).")
    t.chiffre("Coût VIIRS du mois",
              """SELECT coalesce(sum((mesures->>'cout_estime_eur')::float8), 0) AS cout FROM travailleurs
                 WHERE tache = 'viirs' AND cree_le >= date_trunc('month', now())""", "currencyEUR", decimales=2)
    t.chiffre("Dernière nuit VIIRS traitée",
              "SELECT extract(epoch FROM now() - max(traite_le)) / 3600 AS heures FROM viirs_granules", "h",
              ((None, NEUTRE), (30, ORANGE), (54, ROUGE)), decimales=0, description="Heures depuis le dernier traitement.")
    t.chiffre("Ruptures de stock GPU sur 30 jours",
              """SELECT count(*) AS ruptures FROM task_runs WHERE task IN ('sentinel', 'travailleur_sentinel')
                 AND started_at > now() - interval '30 days'
                 AND details->>'erreur' ~* '(out of stock|stock|insufficient|not enough|capacity|scarce|unavailable)'""",
              seuils=((None, NEUTRE), (1, ORANGE)),
              description="Lancements refusés par Scaleway faute de GPU L4 disponible.")
    t.chiffre("Travailleurs actifs", "SELECT count(*) AS actifs FROM travailleurs WHERE detruit_le IS NULL",
              seuils=((None, NEUTRE), (1, SIGNAL), (3, ROUGE)), description="Instances Scaleway facturées en ce moment.")
    t.courbe("Nuits VIIRS : détections par statut",
             """SELECT g.nuit::timestamp AT TIME ZONE 'UTC' AS time,
                       CASE WHEN d.mask_reason IS NOT NULL THEN 'écartées'
                            WHEN d.non_evaluable IS NOT NULL THEN 'non évaluables'
                            WHEN d.matched_vessel_id IS NOT NULL THEN 'avec AIS' ELSE 'sans AIS' END AS metric,
                       count(*) AS value
                FROM viirs_detections d JOIN viirs_granules g ON g.id = d.granule_id
                WHERE g.nuit >= $__timeFrom()::date - 1 AND g.nuit <= $__timeTo()::date
                GROUP BY 1, 2 ORDER BY 1""", w=12, barres=True, empile=True,
             couleurs={"sans AIS": SOMBRE, "avec AIS": NEUTRE, "non évaluables": "#5a6b78", "écartées": "#3a4752"})
    t.table("Nuits VIIRS",
            """SELECT nuit AS "Nuit", count(*) AS "Granules", count(*) FILTER (WHERE erreur IS NOT NULL) AS "En échec",
                      coalesce(sum(detections), 0) AS "Détections", max(traite_le) AS "Traitée le"
               FROM viirs_granules WHERE nuit >= $__timeFrom()::date - 1 GROUP BY 1 ORDER BY 1 DESC""", w=12)
    t.table("Travailleurs",
            """SELECT id AS "N°", tache AS "Tâche", etat AS "État", commercial_type AS "Type", cree_le AS "Créé le",
                      extract(epoch FROM coalesce(detruit_le, now()) - cree_le) AS "Durée",
                      coalesce(mesures->>'rss_max_mo', mesures->'mesures_travailleur'->>'rss_max_mo')::float8
                          AS "Mémoire (Mo)",
                      (mesures->>'cout_estime_eur')::float8 AS "Coût",
                      left(erreur, 160) AS "Motif d'échec", trace_id AS "Trace"
               FROM travailleurs WHERE $__timeFilter(cree_le) ORDER BY id DESC LIMIT 100""",
            h=9, etats={"echec": ("échec", ROUGE), "termine": ("terminé", NEUTRE)}, colonne_etat="État",
            unites={"Durée": "s", "Coût": "currencyEUR"}, trace="Trace")
    t.table("Passages Sentinel 1",
            """SELECT p.acquired_at AS "Acquis le", p.satellite AS "Satellite", p.statut AS "État",
                      array_to_string(p.regions, ', ') AS "Régions", cardinality(p.infra_ids) AS "Infrastructures",
                      cardinality(p.watch_ids) AS "Navires des listes", coalesce(a.status, 'non analysé') AS "Analyse"
               FROM sar_passes p LEFT JOIN LATERAL (SELECT status FROM analyses WHERE pass_id = p.id
                                                    AND source = 'sentinel1' ORDER BY id DESC LIMIT 1) a ON true
               WHERE p.mission = 'S1' AND $__timeFilter(p.acquired_at) ORDER BY 1 DESC LIMIT 200""",
            h=9, etats={"failed": ("échec", ROUGE), "done": ("analysé", SIGNAL)}, colonne_etat="Analyse")
    return t


def alertes() -> Tableau:
    t = Tableau("mars-alertes", "Alertes", "Alertes par type, gravité et jour ; part traitée par les opérateurs",
                de="now-30d")
    t.chiffre("Alertes de la période", "SELECT count(*) AS alertes FROM alerts WHERE $__timeFilter(detected_at)", w=6)
    t.chiffre("Élevées encore nouvelles",
              "SELECT count(*) AS alertes FROM alerts WHERE severity = 'elevee' AND status = 'nouvelle'",
              seuils=((None, NEUTRE), (1, ROUGE)), w=6, description="Toutes périodes confondues.")
    t.chiffre("Part traitée par les opérateurs",
              """SELECT 100.0 * count(*) FILTER (WHERE status <> 'nouvelle') / nullif(count(*), 0) AS part
                 FROM alerts WHERE $__timeFilter(detected_at)""", "percent", decimales=0, w=6,
              description="Alertes acquittées, confirmées ou classées, sur celles de la période.")
    t.chiffre("Délai médian de première décision",
              """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM x.premiere - a.detected_at))
                        AS delai
                 FROM alerts a JOIN LATERAL (SELECT min(at) AS premiere FROM alert_actions WHERE alert_id = a.id) x
                 ON x.premiere IS NOT NULL WHERE $__timeFilter(a.detected_at)""", "s", decimales=0, w=6,
              vide="aucune décision")
    t.courbe("Alertes par jour et par type",
             """SELECT date_trunc('day', detected_at) AS time, type AS metric, count(*) AS value FROM alerts
                WHERE $__timeFilter(detected_at) GROUP BY 1, 2 ORDER BY 1""", w=12, barres=True, empile=True,
             couleurs=TYPES)
    t.courbe("Alertes par jour et par gravité",
             """SELECT date_trunc('day', detected_at) AS time, severity AS metric, count(*) AS value FROM alerts
                WHERE $__timeFilter(detected_at) GROUP BY 1, 2 ORDER BY 1""", w=12, barres=True, empile=True,
             couleurs=GRAVITES)
    t.table("Par type",
            """SELECT type AS "Type", count(*) AS "Alertes", count(*) FILTER (WHERE severity = 'elevee') AS "Élevées",
                      count(*) FILTER (WHERE status <> 'nouvelle') AS "Traitées",
                      round(100.0 * count(*) FILTER (WHERE status <> 'nouvelle') / count(*)) AS "Part traitée"
               FROM alerts WHERE $__timeFilter(detected_at) GROUP BY 1 ORDER BY 2 DESC""",
            unites={"Part traitée": "percent"})
    return t


# Collecteur OpenTelemetry (mesures_otel, relevées chaque minute par taches) : spans reçus depuis le dernier relevé
RECUS_PAR_MINUTE = """SELECT mesure_le AS time, greatest(recus - lag(recus) OVER (ORDER BY mesure_le), 0) AS value
    FROM mesures_otel WHERE $__timeFilter(mesure_le) ORDER BY 1"""
MINUTES_SANS_SPAN = """SELECT extract(epoch FROM now() - max(mesure_le) FILTER (WHERE recus > prec)) / 60 AS minutes
    FROM (SELECT mesure_le, recus, lag(recus) OVER (ORDER BY mesure_le) AS prec FROM mesures_otel
          WHERE mesure_le > now() - interval '1 day') x
    WHERE EXISTS (SELECT 1 FROM mesures_otel WHERE mesure_le > now() - interval '30 minutes')"""
REFUS_15_MIN = """SELECT coalesce(sum(greatest(refuses - prec, 0)), 0) AS refus
    FROM (SELECT mesure_le, refuses, lag(refuses) OVER (ORDER BY mesure_le) AS prec FROM mesures_otel
          WHERE mesure_le > now() - interval '16 minutes') x WHERE mesure_le > now() - interval '15 minutes'"""
# Étapes métier, par préfixe du nom de span (TraceQL n'accepte pas l'échappement du point : [.])
ETAPES = ('name =~ "(regles|archivage|purge|sauvegarde|listes|passages|travailleur|viirs|sentinel|demarrage|scaleway)'
          '[.].*"')


def traces() -> Tableau:
    t = Tableau("mars-traces", "Traces", "Traces OpenTelemetry (Tempo, 72 h) : erreurs, lenteurs, durées par étape, "
                "collecteur, versions des images", de="now-24h")
    t.chiffre("Collecteur : dernier span reçu", MINUTES_SANS_SPAN, "m", ((None, NEUTRE), (15, ROUGE)), w=6,
              decimales=1, description="Minutes depuis le dernier relevé où le collecteur avait reçu de nouveaux "
                                       "spans. Alerte au delà de 15 minutes.")
    t.chiffre("Spans refusés, 15 min", REFUS_15_MIN, seuils=((None, NEUTRE), (1, ROUGE)), w=6,
              description="Spans refusés par le collecteur (mémoire saturée, file pleine). Alerte dès le premier.")
    t.chiffre("File d'attente vers Tempo",
              "SELECT file_attente AS lots FROM mesures_otel ORDER BY mesure_le DESC LIMIT 1",
              seuils=((None, NEUTRE), (100, ORANGE), (1000, ROUGE)), w=6,
              description="Lots en attente sur le disque du collecteur (Tempo redémarré ou lent).")
    t.chiffre("Mémoire du collecteur",
              "SELECT memoire_octets AS octets FROM mesures_otel ORDER BY mesure_le DESC LIMIT 1", "bytes",
              ((None, NEUTRE), (300e6, ORANGE), (360e6, ROUGE)), w=6, decimales=None,
              description="Limite Docker 400 Mo, refus au delà de 250 Mo (memory_limiter).")
    t.section("Recherche des traces")
    t.tempo("Traces en erreur", "{ status = error }", description="Toute trace dont un span est en erreur.")
    t.tempo("Traces lentes de l'API", '{ resource.service.name =~ "mars-api|mars-web" && duration > 1s }',
            description="Requêtes de plus d'une seconde (toutes gardées par l'échantillonnage).")
    t.tempo("Tâches", '{ name =~ "tache .*|commande .*" }', description="Une trace par exécution de tâche.")
    t.tempo("Analyses satellites", '{ name = "travailleur.lancement" }',
            description="Une analyse VIIRS ou Sentinel 1 de bout en bout : sélection, lancement, démarrage, "
                        "téléchargements, inférence, fusion, alertes, destruction.")
    t.section("Durées par étape")
    t.tempo("Durée par étape (90e centile)", "{ " + ETAPES + " } | quantile_over_time(duration, .9) by (name)",
            w=24, metriques=True, description="Étapes du cycle des règles, de l'archivage, des listes, des passages "
                                              "et des travailleurs (TraceQL metrics, calculé par Tempo).")
    t.tempo("Étapes des analyses satellites", '{ resource.service.name = "mars-travailleur" || ' + ETAPES + " }",
            w=24, spans=True, limite=100, description="Spans des travailleurs et du traitement de leurs résultats.")
    t.section("Collecteur et versions")
    t.courbe("Spans reçus par minute", RECUS_PAR_MINUTE, w=12, barres=True)
    t.table("Versions des images",
            """SELECT service AS "Service", detail->>'version' AS "Version", dernier_le AS "Dernier signe de vie"
               FROM signes_de_vie WHERE detail ? 'version' ORDER BY 1""", w=12,
            description="Empreinte du commit de chaque image : toutes égales après un déploiement complet.")
    return t


# Vue d'ensemble (page d'accueil de Grafana) : six tuiles d'état aux seuils des alertes, et les dernières analyses
# satellites tracées de bout en bout

TABLEAUX_UID = {"Direct": "mars-direct", "Disque et base": "mars-disque", "Tâches": "mars-taches",
                "Satellites": "mars-satellites", "Traces": "mars-traces"}


def _seuil(cle: str, defaut: float) -> str:
    """Seuil de config/supervision.env, recopié par taches dans parametres_supervision (repli : valeur du fichier)."""
    return f"coalesce((SELECT valeur FROM parametres_supervision WHERE cle = '{cle}'), {defaut})"


def _minutes(m: str) -> str:
    return f"greatest(round({m}), 0)::int || ' min'"


TUILE_COLLECTE = f"""SELECT now() AS time,
      CASE WHEN m IS NULL THEN 'aucun fichier relevé' ELSE 'Fichier il y a ' || {_minutes("m")} END AS metric,
      CASE WHEN m IS NULL OR m > s THEN 2 WHEN m > s / 2 THEN 1 ELSE 0 END AS value
    FROM (SELECT ({MINUTES_COLLECTE}) AS m, {_seuil("seuil_collecte_min", 5)} AS s) x"""
TUILE_INGESTION = f"""SELECT now() AS time,
      CASE WHEN m IS NULL THEN 'aucun passage relevé' ELSE 'Retard ' || {_minutes("m")} END AS metric,
      CASE WHEN m IS NULL OR m > s THEN 2 WHEN m > s / 2 THEN 1 ELSE 0 END AS value
    FROM (SELECT ({MINUTES_INGESTION}) AS m, {_seuil("seuil_ingestion_min", 10)} AS s) x"""
TUILE_DISQUE = f"""SELECT now() AS time,
      CASE WHEN pct IS NULL THEN 'aucune mesure'
           ELSE round(pct)::int || ' % libres, ' || replace(round(go::numeric, 1)::text, '.', ',') || ' Go' END AS metric,
      CASE WHEN pct IS NULL THEN -1 WHEN pct < s THEN 2 WHEN pct < s + marge THEN 1 ELSE 0 END AS value
    FROM (SELECT (SELECT libre_pct FROM mesures_disque ORDER BY mesure_le DESC LIMIT 1) AS pct,
                 (SELECT libre_octets / 1e9 FROM mesures_disque ORDER BY mesure_le DESC LIMIT 1) AS go,
                 {_seuil("seuil_disque_pct", 15)} AS s, {_seuil("seuil_disque_orange_pts", 10)} AS marge) x"""
TUILE_TACHES = f"""SELECT now() AS time,
      CASE n WHEN 0 THEN 'aucun échec sur 24 h' WHEN 1 THEN '1 échec sur 24 h' ELSE n || ' échecs sur 24 h' END AS metric,
      CASE WHEN suite > 0 THEN 2 WHEN n > 0 THEN 1 ELSE 0 END AS value
    FROM (SELECT (SELECT count(*) FROM task_runs WHERE status = 'echec' AND NOT (details ? 'refus')
                  AND started_at > now() - interval '24 hours') AS n,
                 (SELECT count(*) FROM (SELECT task, array_agg(status ORDER BY started_at DESC) AS st FROM task_runs
                   WHERE started_at > now() - interval '7 days' AND status <> 'en_cours' AND NOT (details ? 'refus')
                   GROUP BY task) t
                  WHERE array_length(st, 1) >= {_seuil("seuil_echecs_successifs", 2)}::int
                    AND st[1:{_seuil("seuil_echecs_successifs", 2)}::int] <@ ARRAY['echec']) AS suite) x"""
TUILE_SATELLITES = f"""SELECT now() AS time,
      CASE WHEN absente THEN 'Nuit du ' || to_char(veille, 'DD/MM') || ' absente'
           WHEN nuit IS NULL THEN 'aucune nuit VIIRS traitée'
           ELSE 'Nuit du ' || to_char(nuit, 'DD/MM') || ' traitée' END AS metric,
      CASE WHEN absente THEN 2 WHEN nuit IS NULL THEN -1 WHEN echec THEN 1 ELSE 0 END AS value
    FROM (SELECT (SELECT max(nuit) FROM viirs_granules WHERE traite_le IS NOT NULL) AS nuit,
                 (now() AT TIME ZONE 'UTC')::date - 1 AS veille,
                 extract(hour FROM now() AT TIME ZONE 'UTC') >= {_seuil("seuil_heure_nuit_viirs", 8)}
                   AND NOT EXISTS (SELECT 1 FROM viirs_granules WHERE nuit = (now() AT TIME ZONE 'UTC')::date - 1
                                   AND traite_le IS NOT NULL) AS absente,
                 EXISTS (SELECT 1 FROM travailleurs WHERE etat = 'echec' AND cree_le > now() - interval '24 hours')
                   AS echec) x"""
TUILE_TRACES = f"""SELECT now() AS time,
      CASE WHEN NOT mesure THEN 'aucune mesure du collecteur'
           ELSE replace(to_char(coalesce(recus, 0), 'FM999,999,990'), ',', ' ') || ' spans en 1 h' END AS metric,
      CASE WHEN NOT mesure THEN -1
           WHEN NOT joignable OR coalesce(muet, 1e9) > {_seuil("seuil_collecteur_muet_min", 15)} OR refus > 0 THEN 2
           WHEN coalesce(file, 0) > 100 THEN 1 ELSE 0 END AS value
    FROM (SELECT EXISTS (SELECT 1 FROM mesures_otel WHERE mesure_le > now() - interval '30 minutes') AS mesure,
                 coalesce((SELECT joignable FROM mesures_otel ORDER BY mesure_le DESC LIMIT 1), false) AS joignable,
                 (SELECT file_attente FROM mesures_otel ORDER BY mesure_le DESC LIMIT 1) AS file,
                 (SELECT sum(greatest(recus - prec, 0)) FROM (SELECT mesure_le, recus,
                         lag(recus) OVER (ORDER BY mesure_le) AS prec FROM mesures_otel
                         WHERE mesure_le > now() - interval '61 minutes') d
                   WHERE mesure_le > now() - interval '1 hour') AS recus,
                 ({MINUTES_SANS_SPAN}) AS muet,
                 ({REFUS_15_MIN}) AS refus) x"""
DERNIERES_ANALYSES = """SELECT cree_le AS "Lancée le", CASE tache WHEN 'viirs' THEN 'VIIRS' WHEN 'sentinel' THEN 'Sentinel 1'
           ELSE tache END AS "Analyse",
           round(extract(epoch FROM coalesce(detruit_le, now()) - cree_le) / 60)::int || ' min' AS "Durée totale",
           etat AS "Statut", left(coalesce(erreur, '·'), 140) AS "Motif", trace_id AS "Trace"
    FROM travailleurs WHERE trace_id IS NOT NULL ORDER BY cree_le DESC LIMIT 20"""


def vue_ensemble() -> Tableau:
    t = Tableau("mars-vue-ensemble", "Vue d'ensemble", "État de la plateforme en un coup d'œil, et les dernières "
                "analyses satellites tracées de bout en bout", de="now-24h")
    t.tuile("Collecte", TUILE_COLLECTE, "Direct",
            "Dernier fichier Parquet écrit. Rouge au seuil de l'alerte (SEUIL_COLLECTE_MIN), orange à sa moitié.")
    t.tuile("Ingestion", TUILE_INGESTION, "Direct",
            "Retard de l'ingestion. Rouge au seuil de l'alerte (SEUIL_INGESTION_MIN), orange à sa moitié.")
    t.tuile("Disque", TUILE_DISQUE, "Disque et base",
            "Espace libre. Rouge sous le seuil de l'alerte (SEUIL_DISQUE_LIBRE_PCT), orange jusqu'à SEUIL_DISQUE_ORANGE_PTS "
            "points au dessus ; mêmes couleurs que l'indicateur Disque de la barre d'état.")
    t.tuile("Tâches", TUILE_TACHES, "Tâches",
            "Échecs des tâches sur 24 h (refus attendus exclus). Rouge si une tâche échoue ECHECS_SUCCESSIFS fois de "
            "suite (alerte), orange dès un échec.")
    t.tuile("Satellites", TUILE_SATELLITES, "Satellites",
            "Dernière nuit VIIRS traitée. Rouge si la nuit précédente manque après HEURE_NUIT_VIIRS (alerte), orange si "
            "un travailleur a échoué en 24 h.")
    t.tuile("Traces", TUILE_TRACES, "Traces",
            "Spans reçus par le collecteur en 1 h. Rouge s'il est muet depuis SEUIL_COLLECTEUR_MUET_MIN, injoignable "
            "ou s'il refuse des spans (alertes), orange si sa file d'attente dépasse 100 lots.")
    t.table("Dernières traces de bout en bout", DERNIERES_ANALYSES, h=11,
            description="Analyses satellites (travailleurs VIIRS et Sentinel 1) : une trace chacune, du lancement à la "
                        "destruction. Le lien de la colonne Trace ouvre la vue Gantt dans Tempo (72 h gardées).",
            etats={"echec": ("échec", ROUGE), "termine": ("terminée", NEUTRE), "resultats": ("résultats reçus", SIGNAL),
                   "demarre": ("en cours", SIGNAL), "cree": ("créée", SIGNAL), "demande": ("demandée", SIGNAL)},
            colonne_etat="Statut", trace="Trace", vide="aucune analyse satellite tracée depuis le déploiement")
    return t


TABLEAUX = {"vue_ensemble": vue_ensemble, "direct": direct, "disque": disque, "taches": taches, "satellites": satellites, "alertes": alertes,
            "traces": traces}


def generer() -> dict[str, str]:
    return {f"{nom}.json": json.dumps(f().json(), ensure_ascii=False, indent=1) + "\n" for nom, f in TABLEAUX.items()}


def requetes() -> list[tuple[str, str, str]]:
    """(tableau, panneau, requête) : pour la mesure des durées (taches.py mesurer-tableaux) et les tests."""
    out = []
    for nom, f in TABLEAUX.items():
        for p in f().json()["panels"]:
            for tg in p.get("targets", []):
                if tg.get("rawSql"):                # les panneaux Tempo ne lisent pas la base
                    out.append((nom, p["title"], tg["rawSql"]))
    return out


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in generer().items():
        (OUT / name).write_text(text)
        print(f"{OUT / name} écrit")
