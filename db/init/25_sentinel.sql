-- MARS C2 : analyse Sentinel 1 par un travailleur GPU éphémère (étape 3, lot C). Idempotent.

-- Volumes bloc d'un travailleur GPU (les L4 n'ont pas de disque local) : enregistrés dès la création, car Scaleway les
-- garde après la suppression de l'instance ; supprimés explicitement par la surveillance, jusqu'au bout
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS volumes_bloc TEXT[];

-- Une analyse par passage Sentinel 1 traité par un travailleur (les analyses à la demande du Mac restent « mac »)
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'mac';      -- mac, sentinel1
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS travailleur_id BIGINT;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS scenes JSONB;          -- scènes retenues : identifiant, emprise, heures
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS declenchement JSONB;   -- motifs : corridor et navire des listes, alerte VIIRS, demande
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS cout_estime_eur REAL;
-- Jamais deux analyses actives ou réussies du même passage (sélection automatique et commande manuelle concurrentes)
CREATE UNIQUE INDEX IF NOT EXISTS analyses_sentinel_passage_idx ON analyses (pass_id)
    WHERE source = 'sentinel1' AND status <> 'failed';
CREATE INDEX IF NOT EXISTS analyses_source_idx ON analyses (source, requested_at);

-- Détections du modèle d'AI2 : attributs mesurés (longueur, largeur, cap, vitesse), instant de la scène, évaluation
ALTER TABLE detections ADD COLUMN IF NOT EXISTS scene TEXT;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS ts TIMESTAMPTZ;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS score REAL;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS width_m REAL;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS heading_deg REAL;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS heading_prob REAL;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS speed_kn REAL;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS distance_cote_m REAL;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS non_evaluable TEXT;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS ais_navires_rayon INTEGER;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS cle TEXT;            -- scène et position : une détection n'est jamais écrite deux fois
CREATE UNIQUE INDEX IF NOT EXISTS detections_cle_idx ON detections (analysis_id, cle) WHERE cle IS NOT NULL;
CREATE INDEX IF NOT EXISTS detections_matched_idx ON detections (matched_vessel_id) WHERE matched_vessel_id IS NOT NULL;

-- Incohérence entre l'écho radar et la déclaration AIS (longueur, cap)
ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_type_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_type_check CHECK (type IN
    ('DARK_SHIP', 'RENDEZVOUS', 'AIS_GAP', 'AIS_UNCONFIRMED', 'WATCHLIST', 'IDENTITY_CHANGE', 'IDENTITY_MISMATCH'));
