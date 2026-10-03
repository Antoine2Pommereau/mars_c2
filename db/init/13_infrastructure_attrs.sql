-- MARS C2 : attributs normalisés d'une infrastructure (type, opérateur, tension, année, tracé). Idempotent.
ALTER TABLE infrastructure ADD COLUMN IF NOT EXISTS attrs JSONB NOT NULL DEFAULT '{}'::jsonb;
