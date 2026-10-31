-- MARS C2 : restes de la voie GPU retirés (audit final du 31/10/2026, voie retirée au lot Z4). Idempotent.
-- * état d'attente des analyses renommé : « attente_gpu » (nom historique) devient « attente_calcul » (rupture de
--   stock des processeurs Scaleway, nouvel essai au cycle suivant) ;
-- * état « gpu_inutilisable » des travailleurs retiré (aucune ligne : plus aucun contrôle de GPU depuis le lot Z4) ;
-- * table types_incompatibles supprimée (une ligne, le RENDER-S du 19/10/2026, travailleur 26, gardée dans le journal).
-- Aucune autre donnée touchée.

ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_status_check;
UPDATE analyses SET status = 'attente_calcul' WHERE status = 'attente_gpu';
ALTER TABLE analyses ADD CONSTRAINT analyses_status_check
    CHECK (status IN ('pending', 'running', 'done', 'failed', 'attente_calcul'));

ALTER TABLE travailleurs DROP CONSTRAINT IF EXISTS travailleurs_etat_check;
ALTER TABLE travailleurs ADD CONSTRAINT travailleurs_etat_check
    CHECK (etat IN ('demande', 'cree', 'demarre', 'resultats', 'termine', 'echec'));

DROP TABLE IF EXISTS types_incompatibles;

INSERT INTO schema_migrations (nom) VALUES ('47_voie_gpu_retiree.sql') ON CONFLICT (nom) DO NOTHING;
