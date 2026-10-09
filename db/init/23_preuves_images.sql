-- MARS C2 : registre des preuves images (vignettes des détections satellites, rangées sur R2). Idempotent.
-- Une ligne par image : source (viirs ; sentinel1 et sentinel2 au lot C), objet prouvé (viirs_detection, ou detection
-- pour le radar), objet R2, taille, géoréférence (pixels en fonction de lon, lat) pour superposer l'AIS.
-- Conservation : sans limite pour une image liée à une alerte, 30 jours sinon (tâche « preuves » de taches.py).
CREATE TABLE IF NOT EXISTS preuves_images (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL CHECK (source IN ('viirs', 'sentinel1', 'sentinel2')),
    objet       TEXT NOT NULL CHECK (objet IN ('viirs_detection', 'detection')),
    objet_id    BIGINT NOT NULL,
    cle         TEXT NOT NULL UNIQUE,
    octets      INTEGER NOT NULL,
    largeur     INTEGER,
    hauteur     INTEGER,
    geo         JSONB,
    prise_le    TIMESTAMPTZ NOT NULL,
    cree_le     TIMESTAMPTZ NOT NULL DEFAULT now(),
    supprime_le TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS preuves_images_objet_idx ON preuves_images (objet, objet_id) WHERE supprime_le IS NULL;
