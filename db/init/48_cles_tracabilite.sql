-- MARS C2 : clés étrangères des liens de traçabilité (audit final du 31/10/2026, 5.1). Idempotent.
-- Une analyse remonte à son travailleur, une demande d'observation à son analyse, une origine de demande à son alerte.
-- Données vérifiées avant (aucun orphelin sur le serveur). ON DELETE SET NULL : la suppression d'une ligne cible
-- (jamais faite par le code) ne supprime pas la trace qui la cite. NOT VALID puis VALIDATE : pas de verrou long.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'analyses_travailleur_fk') THEN
    ALTER TABLE analyses ADD CONSTRAINT analyses_travailleur_fk FOREIGN KEY (travailleur_id)
      REFERENCES travailleurs (id) ON DELETE SET NULL NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'demandes_observation_analyse_fk') THEN
    ALTER TABLE demandes_observation ADD CONSTRAINT demandes_observation_analyse_fk FOREIGN KEY (analysis_id)
      REFERENCES analyses (id) ON DELETE SET NULL NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'demandes_origines_alerte_fk') THEN
    ALTER TABLE demandes_origines ADD CONSTRAINT demandes_origines_alerte_fk FOREIGN KEY (alerte_id)
      REFERENCES alerts (id) ON DELETE SET NULL NOT VALID;
  END IF;
END $$;
ALTER TABLE analyses VALIDATE CONSTRAINT analyses_travailleur_fk;
ALTER TABLE demandes_observation VALIDATE CONSTRAINT demandes_observation_analyse_fk;
ALTER TABLE demandes_origines VALIDATE CONSTRAINT demandes_origines_alerte_fk;

INSERT INTO schema_migrations (nom) VALUES ('48_cles_tracabilite.sql') ON CONFLICT (nom) DO NOTHING;
