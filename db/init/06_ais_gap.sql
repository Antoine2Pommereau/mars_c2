-- MARS C2, phase 4 : coupures AIS. Idempotent. Réimporter l'AIS pour renseigner la classe et l'emprise.

-- Classe AIS du navire (A : navires de commerce, émission fréquente obligatoire ; B : petites unités)
ALTER TABLE vessels ADD COLUMN IF NOT EXISTS ais_class TEXT;

-- Emprise des données chargées : un navire qui en sort cesse d'être vu sans avoir rien coupé
ALTER TABLE ais_days ADD COLUMN IF NOT EXISTS lon_min DOUBLE PRECISION;
ALTER TABLE ais_days ADD COLUMN IF NOT EXISTS lat_min DOUBLE PRECISION;
ALTER TABLE ais_days ADD COLUMN IF NOT EXISTS lon_max DOUBLE PRECISION;
ALTER TABLE ais_days ADD COLUMN IF NOT EXISTS lat_max DOUBLE PRECISION;

-- Zone de réception fiable : cellules où l'on reçoit des messages de nombreux navires, presque à toute heure
CREATE TABLE IF NOT EXISTS reception_cells (
    cx        INTEGER NOT NULL,
    cy        INTEGER NOT NULL,
    messages  INTEGER NOT NULL,
    vessels   INTEGER NOT NULL,
    hours     INTEGER NOT NULL,
    geom      GEOGRAPHY(Polygon, 4326) NOT NULL,
    PRIMARY KEY (cx, cy)
);
