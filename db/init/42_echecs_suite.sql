-- MARS C2 : règle Grafana « Tâche en échec plusieurs fois de suite » (mars-taches) et tuile Tâches de la vue
-- d'ensemble, une seule définition (23/10/2026). Idempotent.
-- Fausse alerte permanente depuis le 20/10/2026 : les deux derniers travailleur_sentinel2 (essais en échec) restaient
-- les derniers tant que Sentinel 2 était en pause, relus pendant 7 jours avec un rappel chaque heure. Désormais :
-- * une série ne compte que si son dernier échec a moins de 24 h ;
-- * une tâche de travailleur (ou son lancement) dont la source est en pause (panneau de contrôle) est tue, comme la
--   règle VIIRS.
-- `n` : nombre d'échecs de suite (parametres_supervision.seuil_echecs_successifs, 2 par défaut). Refus attendus exclus.
CREATE OR REPLACE FUNCTION source_de_tache(t TEXT) RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE t WHEN 'travailleur_sentinel' THEN 'sentinel1' WHEN 'sentinel' THEN 'sentinel1'
                  WHEN 'travailleur_sentinel2' THEN 'sentinel2' WHEN 'sentinel2' THEN 'sentinel2'
                  WHEN 'travailleur_viirs' THEN 'viirs' WHEN 'viirs' THEN 'viirs'
                  WHEN 'tip_and_cue' THEN 'tip_and_cue' END
$$;

CREATE OR REPLACE FUNCTION taches_en_echec_suite(n INT DEFAULT 2)
RETURNS TABLE (tache TEXT, dernier_echec TIMESTAMPTZ) LANGUAGE sql STABLE AS $$
    SELECT task, dernier FROM (
        SELECT task, array_agg(status ORDER BY started_at DESC) AS s,
               max(started_at) FILTER (WHERE status = 'echec') AS dernier
        FROM task_runs
        WHERE started_at > now() - interval '7 days' AND status <> 'en_cours' AND NOT (details ? 'refus')
        GROUP BY task) x
    WHERE array_length(s, 1) >= n AND s[1:n] <@ ARRAY['echec']
      AND dernier > now() - interval '24 hours'
      AND NOT coalesce(source_en_pause(source_de_tache(task)), false)
$$;

GRANT EXECUTE ON FUNCTION source_de_tache(TEXT) TO grafana_lecture;
GRANT EXECUTE ON FUNCTION taches_en_echec_suite(INT) TO grafana_lecture;

INSERT INTO schema_migrations (nom) VALUES ('42_echecs_suite.sql') ON CONFLICT (nom) DO NOTHING;
