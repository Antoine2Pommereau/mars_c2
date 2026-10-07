-- MARS C2 : fiabilité des travailleurs éphémères après le premier essai réel (lot B). Idempotent.

-- Journal de démarrage envoyé par le travailleur dès que le réseau privé fonctionne (travailleurs/demarrage.sh)
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS journal TEXT;
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS journal_le TIMESTAMPTZ;

-- Verrou : un seul travailleur actif (non détruit) par verrou ; pour VIIRS, le verrou est « viirs ». L'index unique
-- rend l'insertion atomique : le rattrapage automatique et la commande manuelle ne peuvent pas lancer les mêmes nuits.
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS verrou TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS travailleurs_verrou_idx ON travailleurs (verrou)
    WHERE verrou IS NOT NULL AND detruit_le IS NULL;

-- Une même détection (même granule, même instant, même point) ne peut exister qu'une fois, même si deux résultats
-- arrivent pour la même granule : doublons éventuels retirés, puis index unique
DELETE FROM viirs_detections a USING viirs_detections b
WHERE a.id > b.id AND a.granule_id = b.granule_id AND a.ts = b.ts
  AND ST_AsText(a.geom::geometry) = ST_AsText(b.geom::geometry);
CREATE UNIQUE INDEX IF NOT EXISTS viirs_detections_unique_idx
    ON viirs_detections (granule_id, ts, ST_AsText(geom::geometry));
