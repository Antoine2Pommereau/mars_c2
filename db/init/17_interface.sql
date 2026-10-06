-- MARS C2 : charpente de l'interface (lot 1 de docs/vision_interface.md). Idempotent.

-- Décisions des opérateurs : commentaires horodatés, et motif obligatoire au classement
ALTER TABLE alert_actions DROP CONSTRAINT IF EXISTS alert_actions_action_check;
ALTER TABLE alert_actions ADD CONSTRAINT alert_actions_action_check
    CHECK (action IN ('acquitter', 'confirmer', 'classer', 'rouvrir', 'commenter'));
ALTER TABLE alert_actions ADD COLUMN IF NOT EXISTS motif TEXT;
ALTER TABLE alert_actions DROP CONSTRAINT IF EXISTS alert_actions_motif_check;
ALTER TABLE alert_actions ADD CONSTRAINT alert_actions_motif_check
    CHECK (motif IS NULL OR motif IN ('faux_positif', 'activite_legitime', 'doublon'));

-- Statistiques du trafic pour la frise, tenues par le conteneur taches (mars/frise.py) : quelques dizaines de
-- milliers de lignes par mois, lues à la place des millions de positions pour les plages de 7 et 30 jours.
-- Positions par minute : repère les coupures du flux AIS (zones hachurées de la frise).
CREATE TABLE IF NOT EXISTS stats_minute (
    minute     TIMESTAMPTZ PRIMARY KEY,
    positions  INTEGER NOT NULL
);
-- Navires distincts par tranche de 10 minutes : histogramme de densité (un navire arrêté n'émet qu'un point
-- toutes les 10 minutes en base, d'où la tranche).
CREATE TABLE IF NOT EXISTS stats_10min (
    tranche    TIMESTAMPTZ PRIMARY KEY,
    navires    INTEGER NOT NULL,
    positions  INTEGER NOT NULL
);

-- Le flux temps réel n'envoie le trafic que lorsque l'ingestion a chargé du nouveau
CREATE INDEX IF NOT EXISTS ingested_files_at_idx ON ingested_files (ingested_at);
-- Alertes d'une plage de temps
CREATE INDEX IF NOT EXISTS alerts_type_event_idx ON alerts (type, event_time);
