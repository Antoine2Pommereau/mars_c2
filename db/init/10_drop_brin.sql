-- MARS C2, migration 10 : suppression de l'index BRIN devenu inutile sur positions.ts
--
-- Le schéma initial (01_schema.sql) crée positions_ts_brin, un index BRIN sur positions.ts.
-- La migration 02 a ajouté positions_ts_idx, un index B-tree, qui sert réellement aux lectures
-- par fenêtre de temps. L'index BRIN n'est plus interrogé mais reste maintenu à chaque import,
-- ce qui alourdit inutilement l'insertion en masse des positions. On le supprime.
--
-- Migration idempotente : rejouable sans effet si l'index a déjà disparu.
DROP INDEX IF EXISTS positions_ts_brin;

-- Note sur deux compromis assumés, laissés en l'état faute de gain suffisant au regard du risque :
--   1. Plusieurs clés étrangères n'ont pas de clause ON DELETE (par exemple positions.vessel_id,
--      analyses.pass_id, detections.matched_vessel_id). Les ajouter demanderait de revoir la
--      stratégie de suppression de chaque table ; le nettoyage passe aujourd'hui par les scripts.
--   2. alert_evidence est volontairement polymorphe (evidence_type et evidence_id) et ne peut donc
--      pas porter de clé étrangère unique vers les tables sources. La traçabilité est garantie par
--      le code applicatif. Modifier ce modèle sortirait du cadre de ce correctif.
