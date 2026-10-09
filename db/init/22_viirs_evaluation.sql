-- MARS C2 : évaluation des détections VIIRS et diagnostic des travailleurs (après trois nuits réelles). Idempotent.

-- Navire AIS le plus proche à l'heure de la détection (interpolé, ou estimé sur quelques minutes) et réception AIS
-- autour d'elle : mesures pour la calibration, et motif d'une détection « non évaluable »
ALTER TABLE viirs_detections ADD COLUMN IF NOT EXISTS ais_proche_m REAL;
ALTER TABLE viirs_detections ADD COLUMN IF NOT EXISTS ais_proche_vessel_id BIGINT;
ALTER TABLE viirs_detections ADD COLUMN IF NOT EXISTS ais_proche_ecart_s REAL;       -- 0 : interpolé ; sinon durée d'estime
ALTER TABLE viirs_detections ADD COLUMN IF NOT EXISTS ais_navires_rayon INTEGER;     -- navires AIS reçus dans le rayon
ALTER TABLE viirs_detections ADD COLUMN IF NOT EXISTS non_evaluable TEXT;            -- coupure du flux, pas de réception

-- État Scaleway d'un travailleur relevé avant sa destruction pour absence de signe de vie (ou durée dépassée)
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS diagnostic JSONB;
