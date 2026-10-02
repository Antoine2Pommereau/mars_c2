-- MARS C2, phase 4 : registre des échos fixes (éoliennes, plateformes, épaves émergées, bouées de grande taille).
-- Un écho sans AIS revu au même endroit à deux dates différentes est fixe. Idempotent.
CREATE TABLE IF NOT EXISTS fixed_echoes (
    id            SERIAL PRIMARY KEY,
    geom          GEOGRAPHY(Point, 4326) NOT NULL,
    first_seen    TIMESTAMPTZ NOT NULL,
    last_seen     TIMESTAMPTZ NOT NULL,
    observations  INTEGER NOT NULL DEFAULT 2,
    detection_ids BIGINT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS fixed_echoes_geom_idx ON fixed_echoes USING GIST (geom);
