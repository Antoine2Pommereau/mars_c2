-- MARS C2 : régions de couverture et manifeste de provisionnement. Idempotent.
-- Voir docs/spec_regions.md. La région active borne la carte, l'AIS et la recherche de passages SAR.

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

-- Manifeste de provisionnement : une ligne par couche d'une région, avec sa provenance.
-- usage distingue ce qui nourrit les règles (operationnelle) de ce qui est seulement montré (affichage).
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

-- Infrastructures sous marines à risque ou d'intérêt (câbles, gazoducs), découpées par région.
-- La marge des corridors n'est pas figée ici : elle s'applique à la requête, paramétrée dans rules.yaml.
CREATE TABLE IF NOT EXISTS infrastructure (
    id          SERIAL PRIMARY KEY,
    region_id   INTEGER NOT NULL REFERENCES regions (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('cable', 'pipeline')),
    name        TEXT,
    operator    TEXT,
    status      TEXT,
    source      TEXT,
    geom        GEOGRAPHY NOT NULL
);
CREATE INDEX IF NOT EXISTS infrastructure_geom_idx ON infrastructure USING GIST (geom);
CREATE INDEX IF NOT EXISTS infrastructure_region_idx ON infrastructure (region_id);

-- Région d'amorçage : l'emprise déjà chargée (Skagerrak et Kattegat), pour la tranche verticale.
INSERT INTO regions (name, origin, geom, active)
VALUES ('Skagerrak et Kattegat', 'manuelle',
        ST_MakeEnvelope(8.5, 56.0, 13.0, 58.6, 4326)::geography, true)
ON CONFLICT (name) DO NOTHING;

-- Couches attendues pour cette région, en attente de provisionnement.
INSERT INTO region_layers (region_id, layer, usage)
SELECT r.id, c.layer, c.usage
FROM regions r
CROSS JOIN (VALUES
    ('coastline', 'operationnelle'),
    ('bathymetry', 'affichage'),
    ('infrastructure', 'operationnelle')
) AS c(layer, usage)
WHERE r.name = 'Skagerrak et Kattegat'
ON CONFLICT (region_id, layer) DO NOTHING;
