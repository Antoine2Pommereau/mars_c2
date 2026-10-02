-- MARS C2, phase 4 : statut de navigation AIS (code normalisé : 0 en route au moteur, 1 au mouillage,
-- 5 amarré, 7 en pêche, 8 en route à la voile…). Idempotent. Réimporter l'AIS pour le renseigner.
ALTER TABLE positions ADD COLUMN IF NOT EXISTS nav_status SMALLINT;
