-- MARS C2 : réanalyse d'un passage Sentinel 1 déjà analysé (taches.py essai-reel --lancer ID --refaire, 25/10/2026).
-- Idempotent. refait : l'analyse précédente du même passage, gardée telle quelle avec ses détections.
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS refait BIGINT REFERENCES analyses (id);

-- L'index du lot C interdisait toute seconde analyse non échouée d'un passage. Il garantit désormais une seule
-- analyse d'origine par passage, et une seule réanalyse de chaque analyse : deux lancements simultanés restent
-- impossibles, une analyse en cours est refusée par la garde du lancement (mars/sentinel.py, garde_passage).
DROP INDEX IF EXISTS analyses_sentinel_passage_idx;
CREATE UNIQUE INDEX IF NOT EXISTS analyses_sentinel_passage_refait_idx ON analyses (pass_id, coalesce(refait, 0))
    WHERE source = 'sentinel1' AND status <> 'failed';
GRANT SELECT (refait) ON analyses TO grafana_lecture;

INSERT INTO schema_migrations (nom) VALUES ('45_analyses_refaites.sql') ON CONFLICT (nom) DO NOTHING;
