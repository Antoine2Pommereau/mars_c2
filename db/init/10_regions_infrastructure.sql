-- MARS C2 : régions de couverture, manifeste de provisionnement et infrastructures sous marines. Idempotent.
-- Première brique de la piste infrastructures de la nouvelle direction (section 0) : câbles, pipelines et
-- parcs éoliens téléchargés d'EMODnet et découpés par région. Portée depuis la session de calibration danoise.

-- Une région de couverture : mer prédéfinie (OHI) ou emprise tracée à la main.
CREATE TABLE IF NOT EXISTS regions (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    origin      TEXT NOT NULL CHECK (origin IN ('iho', 'manuelle')),
    geom        GEOGRAPHY NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS regions_geom_idx ON regions USING GIST (geom);

-- Manifeste de provisionnement : une ligne par couche d'une région, avec sa provenance et son état.
CREATE TABLE IF NOT EXISTS region_layers (
    region_id       INTEGER NOT NULL REFERENCES regions (id) ON DELETE CASCADE,
    layer           TEXT NOT NULL,
    usage           TEXT NOT NULL CHECK (usage IN ('operationnelle', 'affichage')),
    source          TEXT,
    source_version  TEXT,
    source_url      TEXT,
    license         TEXT,
    status          TEXT NOT NULL DEFAULT 'absente' CHECK (status IN ('absente', 'en_cours', 'prete', 'echec')),
    fetched_at      TIMESTAMPTZ,
    size_bytes      BIGINT,
    feature_count   INTEGER,
    PRIMARY KEY (region_id, layer)
);

-- Infrastructures sous marines à risque ou d'intérêt (câbles, pipelines, parcs éoliens), découpées par région.
CREATE TABLE IF NOT EXISTS infrastructure (
    id          SERIAL PRIMARY KEY,
    region_id   INTEGER NOT NULL REFERENCES regions (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    name        TEXT,
    operator    TEXT,
    status      TEXT,
    source      TEXT,
    attrs       JSONB NOT NULL DEFAULT '{}'::jsonb,
    geom        GEOGRAPHY NOT NULL
);
CREATE INDEX IF NOT EXISTS infrastructure_geom_idx ON infrastructure USING GIST (geom);
CREATE INDEX IF NOT EXISTS infrastructure_region_idx ON infrastructure (region_id);
ALTER TABLE infrastructure ADD COLUMN IF NOT EXISTS attrs JSONB NOT NULL DEFAULT '{}'::jsonb;
-- Autorise les parcs éoliens en plus des câbles et pipelines (rejoue proprement si la contrainte existe déjà).
ALTER TABLE infrastructure DROP CONSTRAINT IF EXISTS infrastructure_kind_check;
ALTER TABLE infrastructure ADD CONSTRAINT infrastructure_kind_check
    CHECK (kind IN ('cable', 'pipeline', 'windfarm'));

-- Deux zones France collectées dès le départ (voir section 0 du CLAUDE.md).
INSERT INTO regions (name, origin, geom, active) VALUES
    ('Bretagne', 'manuelle', ST_MakeEnvelope(-6.8, 47.3, -3.0, 49.6, 4326)::geography, false),
    ('Mediterranee', 'manuelle', ST_MakeEnvelope(3.0, 41.2, 9.8, 43.7, 4326)::geography, false)
ON CONFLICT (name) DO NOTHING;

-- Couches attendues pour ces zones, en attente de provisionnement.
INSERT INTO region_layers (region_id, layer, usage)
SELECT r.id, c.layer, c.usage
FROM regions r
CROSS JOIN (VALUES
    ('coastline', 'operationnelle'),
    ('bathymetry', 'affichage'),
    ('infrastructure', 'operationnelle')
) AS c(layer, usage)
WHERE r.name IN ('Bretagne', 'Mediterranee')
ON CONFLICT (region_id, layer) DO NOTHING;
