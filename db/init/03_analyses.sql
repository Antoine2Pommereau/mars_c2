-- MARS C2, phase 3 : suivi des analyses à la demande. Idempotent.
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS progress JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS timings JSONB;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS summary JSONB;
