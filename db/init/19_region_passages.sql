-- MARS C2 : région affichée et calendrier des passages satellites (étape 3, lot A). Idempotent.

-- Passages Sentinel 1 et 2 : la table des passages radar sert aussi au calendrier. Les lignes déjà présentes
-- (produits Sentinel Hub des analyses à la demande) gardent ces colonnes vides ; le calendrier se reconnaît à
-- « mission ». Clé (product_name) : satellite et orbite absolue (S1D_OR4898, S2C_OR10818).
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS mission TEXT;          -- S1, S2
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS satellite TEXT;        -- S1C, S1D, S2A, S2B, S2C
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS mode TEXT;             -- IW, EW, MSI, NOBS…
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS relative_orbit INTEGER;
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS absolute_orbit INTEGER;
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS datatake TEXT;
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS statut TEXT;           -- prevu (plan de l'ESA), acquis (catalogue)
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS source TEXT;           -- plan, catalogue
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS produits INTEGER;      -- produits du catalogue réunis
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS regions TEXT[];        -- régions couvertes (clés en minuscules)
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS infra_ids INTEGER[];   -- infrastructures dans l'emprise
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS watch_ids BIGINT[];    -- navires des listes dans l'emprise
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS couverture_le TIMESTAMPTZ;
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS mis_a_jour_le TIMESTAMPTZ;
ALTER TABLE sar_passes DROP CONSTRAINT IF EXISTS sar_passes_statut_check;
ALTER TABLE sar_passes ADD CONSTRAINT sar_passes_statut_check CHECK (statut IS NULL OR statut IN ('prevu', 'acquis'));
CREATE INDEX IF NOT EXISTS sar_passes_acquired_idx ON sar_passes (acquired_at) WHERE mission IS NOT NULL;

-- Histogramme de la frise par région : navires distincts par tranche de 10 minutes dans chaque région (une position
-- dans le recouvrement de Bretagne et Manche compte dans les deux). Les coupures du flux restent globales.
CREATE TABLE IF NOT EXISTS stats_10min_region (
    region     TEXT NOT NULL,
    tranche    TIMESTAMPTZ NOT NULL,
    navires    INTEGER NOT NULL,
    positions  INTEGER NOT NULL,
    PRIMARY KEY (region, tranche)
);
