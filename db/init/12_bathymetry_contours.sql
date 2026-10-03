-- MARS C2 : isobathes calculées à partir du raster bathymétrique provisionné. Idempotent.
-- Couche d'affichage vectorielle (légère, stylable), stockée par région ; le raster reste la source
-- pour l'échantillonnage des règles.
CREATE TABLE IF NOT EXISTS bathymetry_contours (
    id          SERIAL PRIMARY KEY,
    region_id   INTEGER NOT NULL REFERENCES regions (id) ON DELETE CASCADE,
    depth_m     REAL NOT NULL,
    geom        GEOGRAPHY NOT NULL
);
CREATE INDEX IF NOT EXISTS bathymetry_contours_geom_idx ON bathymetry_contours USING GIST (geom);
CREATE INDEX IF NOT EXISTS bathymetry_contours_region_idx ON bathymetry_contours (region_id);
