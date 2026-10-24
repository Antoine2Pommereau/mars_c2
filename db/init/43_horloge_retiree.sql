-- MARS C2 : lot Z4, horloge partagée retirée (docs/bilan.md, section 2.6). Idempotent.
-- L'horloge partagée (table sim_clock, fonction sim_now, migrations 02 et 11) n'était plus lue par l'interface depuis
-- le 07/10/2026 : l'instant est tenu par chaque navigateur (routes ?at=). Une seule ligne de réglage, aucune donnée
-- historique. La migration 13 la modifie encore à l'initialisation d'une base neuve : elle est retirée ici, après.
DROP FUNCTION IF EXISTS sim_now();
DROP TABLE IF EXISTS sim_clock;

INSERT INTO schema_migrations (nom) VALUES ('43_horloge_retiree.sql') ON CONFLICT (nom) DO NOTHING;
