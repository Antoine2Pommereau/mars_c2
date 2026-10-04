-- MARS C2 : aires marines protégées (Natura 2000 marin, MPA des conventions régionales), par région. Idempotent.
CREATE TABLE IF NOT EXISTS protected_areas (
    id          SERIAL PRIMARY KEY,
    region_id   INTEGER NOT NULL REFERENCES regions (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('natura2000', 'mpa')),
    name        TEXT,
    designation TEXT,
    country     TEXT,
    source      TEXT,
    attrs       JSONB NOT NULL DEFAULT '{}'::jsonb,
    geom        GEOGRAPHY NOT NULL
);
CREATE INDEX IF NOT EXISTS protected_areas_geom_idx ON protected_areas USING GIST (geom);
CREATE INDEX IF NOT EXISTS protected_areas_region_idx ON protected_areas (region_id);
