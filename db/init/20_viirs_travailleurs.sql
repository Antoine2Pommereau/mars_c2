-- MARS C2 : travailleurs éphémères et détections nocturnes VIIRS (étape 3, lot B). Idempotent.

-- Travailleurs : une instance Scaleway créée à la demande, qui analyse puis est détruite par le serveur
-- (mars/travailleurs.py). La ligne reste comme journal ; le résumé de chaque exécution va aussi dans task_runs.
CREATE TABLE IF NOT EXISTS travailleurs (
    id              BIGSERIAL PRIMARY KEY,
    tache           TEXT NOT NULL,                      -- viirs (lot B), sentinel (lot C)
    etat            TEXT NOT NULL DEFAULT 'demande' CHECK (etat IN
                    ('demande', 'cree', 'demarre', 'resultats', 'termine', 'echec')),
    commercial_type TEXT NOT NULL,                      -- DEV1-M, L4-1-24G…
    zone            TEXT NOT NULL,
    scw_server_id   TEXT,
    jeton_hash      TEXT NOT NULL,                      -- empreinte SHA 256 du jeton de retour (le jeton n'est pas gardé)
    parametres      JSONB NOT NULL DEFAULT '{}'::jsonb, -- granules à traiter…
    resultat        JSONB,                              -- réponse brute du travailleur, vidée après traitement
    mesures         JSONB NOT NULL DEFAULT '{}'::jsonb, -- durées, mémoire, volume, coût estimé
    erreur          TEXT,
    cree_le         TIMESTAMPTZ NOT NULL DEFAULT now(),
    demarre_le      TIMESTAMPTZ,
    resultats_le    TIMESTAMPTZ,
    detruit_le      TIMESTAMPTZ,                        -- instance détruite chez Scaleway (NULL : à surveiller)
    fini_le         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS travailleurs_actifs_idx ON travailleurs (cree_le) WHERE detruit_le IS NULL;

-- Granules VIIRS (bande Day/Night) traitées : une par fichier DNB, avec son emprise
CREATE TABLE IF NOT EXISTS viirs_granules (
    id             BIGSERIAL PRIMARY KEY,
    nom            TEXT NOT NULL UNIQUE,                -- VJ102DNB_NRT.A2026280.0036
    satellite      TEXT NOT NULL,                       -- SNPP, NOAA20, NOAA21
    nuit           DATE NOT NULL,                       -- nuit du 06 au 07/10 : 2026-10-06
    debut          TIMESTAMPTZ NOT NULL,
    fin            TIMESTAMPTZ NOT NULL,
    emprise        GEOGRAPHY,
    lune           REAL,                                -- éclairement lunaire moyen, en %
    statut         TEXT,                                -- processed, ou motif d'abandon du modèle
    erreur         TEXT,
    tentatives     INTEGER NOT NULL DEFAULT 1,          -- une granule en échec est retentée une fois
    detections     INTEGER NOT NULL DEFAULT 0,
    travailleur_id BIGINT REFERENCES travailleurs (id),
    traite_le      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS viirs_granules_nuit_idx ON viirs_granules (nuit);

-- Détections nocturnes, appariées à l'AIS ou non
CREATE TABLE IF NOT EXISTS viirs_detections (
    id                 BIGSERIAL PRIMARY KEY,
    granule_id         BIGINT NOT NULL REFERENCES viirs_granules (id) ON DELETE CASCADE,
    ts                 TIMESTAMPTZ NOT NULL,            -- instant estimé (position le long de la granule)
    geom               GEOGRAPHY(Point, 4326) NOT NULL,
    nanowatts          REAL,                            -- intensité maximale, nW/cm²/sr
    orientation        REAL,
    lune               REAL,
    ciel_clair         REAL,
    matched_vessel_id  BIGINT REFERENCES vessels (id),
    match_distance_m   REAL,
    mask_reason        TEXT,                            -- cote, lumiere_fixe ; NULL : retenue
    distance_cote_m    REAL
);
CREATE INDEX IF NOT EXISTS viirs_detections_ts_idx ON viirs_detections (ts);
CREATE INDEX IF NOT EXISTS viirs_detections_geom_idx ON viirs_detections USING GIST (geom);

-- Lumières fixes (plateformes, éoliennes, côtes, phares) : revues au même endroit plusieurs nuits
CREATE TABLE IF NOT EXISTS viirs_lumieres_fixes (
    id         BIGSERIAL PRIMARY KEY,
    geom       GEOGRAPHY(Point, 4326) NOT NULL,
    nuits      INTEGER NOT NULL,
    premiere   TIMESTAMPTZ NOT NULL,
    derniere   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS viirs_lumieres_fixes_geom_idx ON viirs_lumieres_fixes USING GIST (geom);

-- Preuve d'une alerte : une détection VIIRS
ALTER TABLE alert_evidence DROP CONSTRAINT IF EXISTS alert_evidence_evidence_type_check;
ALTER TABLE alert_evidence ADD CONSTRAINT alert_evidence_evidence_type_check
    CHECK (evidence_type IN ('analysis', 'detection', 'position', 'vessel', 'viirs'));

-- Préparation du lot C : couverture nuageuse annoncée par le catalogue pour les passages Sentinel 2 (en %)
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS nuages REAL;
