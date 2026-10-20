-- MARS C2 : lot S2, détection optique Sentinel 2 (docs/sentinel2.md). Idempotent.
-- Analyses source = 'sentinel2' dans analyses et detections, comme Sentinel 1 ; mesures propres à l'optique sur les
-- détections ; heure propre à chaque tuile au calendrier ; vignette de contexte au registre des preuves.

-- Une analyse par passage et par source (Sentinel 1 et Sentinel 2 ne partagent jamais un passage, mais l'index unique
-- du lot C ne visait que sentinel1)
CREATE UNIQUE INDEX IF NOT EXISTS analyses_sentinel2_passage_idx ON analyses (pass_id)
    WHERE source = 'sentinel2' AND status <> 'failed';

-- Détections optiques : type de navire donné par le modèle, part nuageuse autour de l'objet (masque SCL du L2A),
-- fenêtre et son état de masque (present, absent)
ALTER TABLE detections ADD COLUMN IF NOT EXISTS type_navire TEXT;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS nuages_pct REAL;
ALTER TABLE detections ADD COLUMN IF NOT EXISTS masque_nuages TEXT;

-- Calendrier : heure propre à chaque tuile Sentinel 2 (SENSING_TIME de MTD_TL.xml). L'heure du catalogue est le début
-- de la prise de vue sur toute l'orbite, plusieurs minutes avant le passage sur nos régions (387 s pour T30UXA le
-- 17/10/2026) ; acquired_at et ended_at deviennent la première et la dernière tuile sur nos régions
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS heure_prise_de_vue TIMESTAMPTZ;
ALTER TABLE sar_passes ADD COLUMN IF NOT EXISTS heure_tuiles JSONB;     -- {"T30UXA": "2026-10-17T11:17:16Z", …}

-- Preuves : vignette de la détection (2 km) ou de contexte (10 km, détection liée à une alerte)
ALTER TABLE preuves_images ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'vignette';
ALTER TABLE preuves_images DROP CONSTRAINT IF EXISTS preuves_images_role_check;
ALTER TABLE preuves_images ADD CONSTRAINT preuves_images_role_check CHECK (role IN ('vignette', 'contexte'));

-- Grafana (tableau Satellites, section Sentinel 2) : nuages annoncés des passages, résultat des analyses optiques
GRANT SELECT (nuages) ON sar_passes TO grafana_lecture;
GRANT SELECT (summary, octets_lus, calcul_motif, trace_id) ON analyses TO grafana_lecture;
