-- MARS C2, phase 4 : masques géographiques. Idempotent.

-- Terres émergées (Natural Earth 10 m, découpées sur la région d'intérêt)
CREATE TABLE IF NOT EXISTS land (
    id      SERIAL PRIMARY KEY,
    source  TEXT NOT NULL,
    geom    GEOGRAPHY NOT NULL
);
CREATE INDEX IF NOT EXISTS land_geom_idx ON land USING GIST (geom);

-- Zones de stationnement observées dans l'AIS : mouillages et abords de ports, où des navires
-- immobiles se côtoient légitimement pendant des heures
CREATE TABLE IF NOT EXISTS stationary_zones (
    id              SERIAL PRIMARY KEY,
    geom            GEOGRAPHY(Polygon, 4326) NOT NULL,
    vessels         INTEGER NOT NULL,
    slow_positions  INTEGER NOT NULL,
    day_from        DATE NOT NULL,
    day_to          DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS stationary_zones_geom_idx ON stationary_zones USING GIST (geom);
