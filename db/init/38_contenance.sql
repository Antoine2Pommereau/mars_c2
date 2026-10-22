-- Garde de probabilité de la vérification satellite (règles 2026.10.45, docs/trajectoire.md) : contenance estimée
-- (part des navires réels attendue dans les fenêtres du passage retenu), d'après la table du cercle appris
ALTER TABLE demandes_observation ADD COLUMN IF NOT EXISTS contenance real;
