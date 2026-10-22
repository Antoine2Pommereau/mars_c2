-- MARS C2 : lot Z2, statistiques des requêtes (bilan, section 3.1). Idempotent.
-- Exige shared_preload_libraries=pg_stat_statements au démarrage de PostgreSQL (commande du service db,
-- docker-compose.yml) : sans lui, l'extension se crée mais sa vue refuse d'être lue. Lecture :
--   SELECT calls, round(mean_exec_time::numeric, 1) ms, left(query, 80) FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

INSERT INTO schema_migrations (nom) VALUES ('41_pg_stat_statements.sql') ON CONFLICT (nom) DO NOTHING;
