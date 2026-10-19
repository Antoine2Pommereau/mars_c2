-- MARS C2 : supervision (étape D). Grafana branché sur PostgreSQL, signes de vie, historique du disque. Idempotent.
-- Après l'incident du 18/10/2026 : disque plein, collecte figée sans s'arrêter, ingestion en échec pendant 21 h, et
-- personne de prévenu.

-- Historique de la mesure du disque, toutes les 10 minutes (disk_status ne garde que la dernière valeur par chemin) :
-- croissance par jour et jours restants au rythme actuel
CREATE TABLE IF NOT EXISTS mesures_disque (
    mesure_le         TIMESTAMPTZ PRIMARY KEY DEFAULT now(),
    total_octets      BIGINT NOT NULL,
    libre_octets      BIGINT NOT NULL,
    libre_pct         REAL NOT NULL,
    base_octets       BIGINT,              -- pg_database_size
    positions_octets  BIGINT,              -- pg_total_relation_size('positions'), index compris
    ais_live_octets   BIGINT,              -- dossier data/ais_live (collecte pas encore archivée)
    ais_live_fichiers INTEGER,
    dernier_parquet   TIMESTAMPTZ          -- dernier fichier Parquet écrit par la collecte
);

-- Signes de vie, mis à jour en place : collecte (relevée chaque minute par taches, d'après le dernier fichier écrit),
-- ingestion (écrite par l'ingestion à chaque passage réussi), taches (chaque minute)
CREATE TABLE IF NOT EXISTS signes_de_vie (
    service    TEXT PRIMARY KEY,
    dernier_le TIMESTAMPTZ NOT NULL,       -- dernier signe utile : fichier écrit, passage d'ingestion réussi
    detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
    mesure_le  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Paramètres utiles aux tableaux (budget du mois, plafonds), recopiés par taches depuis config/rules.yaml : les
-- tableaux ne gardent aucune valeur en dur
CREATE TABLE IF NOT EXISTS parametres_supervision (
    cle           TEXT PRIMARY KEY,
    valeur        DOUBLE PRECISION,
    mis_a_jour_le TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Rôle de Grafana : lecture seule, tables et colonnes utiles aux tableaux seulement (ni jeton, ni paramètres des
-- travailleurs, ni position, ni nom de navire, ni note d'opérateur). Créé sans connexion : taches lui donne son mot de
-- passe (GRAFANA_PG_MOT_DE_PASSE du .env) et le droit de connexion au démarrage, rien n'est réglé à la main.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_lecture') THEN
        CREATE ROLE grafana_lecture NOLOGIN;
    END IF;
END $$;
ALTER ROLE grafana_lecture SET default_transaction_read_only = on;
ALTER ROLE grafana_lecture SET statement_timeout = '10s';
ALTER ROLE grafana_lecture CONNECTION LIMIT 5;
GRANT CONNECT ON DATABASE mars TO grafana_lecture;
GRANT USAGE ON SCHEMA public TO grafana_lecture;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM grafana_lecture;

GRANT SELECT ON mesures_disque, signes_de_vie, parametres_supervision, disk_status, stats_minute, stats_10min_region,
                ingested_files, task_runs, ais_days TO grafana_lecture;
GRANT SELECT (id, tache, etat, commercial_type, zone, mesures, erreur, cree_le, demarre_le, resultats_le, detruit_le,
              fini_le) ON travailleurs TO grafana_lecture;
GRANT SELECT (id, nuit, satellite, statut, erreur, detections, traite_le) ON viirs_granules TO grafana_lecture;
GRANT SELECT (id, granule_id, ts, matched_vessel_id, mask_reason, non_evaluable) ON viirs_detections TO grafana_lecture;
GRANT SELECT (id, pass_id, status, source, requested_at, completed_at, error, cout_estime_eur, travailleur_id)
    ON analyses TO grafana_lecture;
GRANT SELECT (id, mission, satellite, mode, acquired_at, statut, regions, infra_ids, watch_ids) ON sar_passes
    TO grafana_lecture;
GRANT SELECT (id, type, severity, status, event_time, detected_at) ON alerts TO grafana_lecture;
GRANT SELECT (id, alert_id, action, at) ON alert_actions TO grafana_lecture;

-- Requêtes des tableaux : alertes par jour de détection, tâches sur 7 jours toutes confondues
CREATE INDEX IF NOT EXISTS alerts_detected_idx ON alerts (detected_at);
CREATE INDEX IF NOT EXISTS task_runs_started_idx ON task_runs (started_at);
