-- MARS C2 : origine des positions AIS. Idempotent.
-- Toutes les positions reçues jusqu'ici viennent d'AISStream ; un nouveau fournisseur (Spire, récepteur personnel…)
-- s'inscrira sous son propre nom. Colonne avec valeur par défaut constante : ajout immédiat, sans réécrire la table
-- (PostgreSQL 11 et plus), même avec des dizaines de millions de positions.
ALTER TABLE positions ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'aisstream';
