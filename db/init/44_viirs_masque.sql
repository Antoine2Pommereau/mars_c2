-- MARS C2 : masque des nuages des granules VIIRS (lot viirs-masque, 25/10/2026). Idempotent.
-- masque : « fourni » ou « absent » (NULL pour les granules traités avant ce lot, tous sans masque) ; masque_motif :
-- pourquoi il manque, ou d'où il vient ; ciel_clair_part : part des pixels de ciel clair (confiance au dessus de
-- 0,99) ; non_evaluable : motif quand le granule ne permet aucune conclusion (lune au dessus de 80 % sans masque :
-- le modèle d'allenai éteint alors toute la scène).
ALTER TABLE viirs_granules ADD COLUMN IF NOT EXISTS masque TEXT;
ALTER TABLE viirs_granules ADD COLUMN IF NOT EXISTS masque_motif TEXT;
ALTER TABLE viirs_granules ADD COLUMN IF NOT EXISTS ciel_clair_part REAL;
ALTER TABLE viirs_granules ADD COLUMN IF NOT EXISTS non_evaluable TEXT;

-- Granules déjà traités : aucun n'avait de masque ; sous une lune forte, ils ne prouvaient rien
UPDATE viirs_granules SET non_evaluable = 'masque des nuages absent, lune à ' || round(lune) || ' % : le modèle '
       'éteint toute la scène au dessus de 80 %, aucune conclusion sur ce granule'
 WHERE masque IS NULL AND non_evaluable IS NULL AND erreur IS NULL AND lune > 80;

INSERT INTO schema_migrations (nom) VALUES ('44_viirs_masque.sql') ON CONFLICT (nom) DO NOTHING;
