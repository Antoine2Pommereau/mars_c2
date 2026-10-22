-- MARS C2 : lot Z2, registre des migrations appliquées (docs/exploitation.md). Idempotent.
-- scripts/deployer.sh compare db/init/*.sql à cette table : une migration absente est listée et n'est appliquée qu'avec
-- --migrations (une transaction par fichier), puis l'empreinte SHA 256 du fichier est inscrite. Les migrations 01 à
-- 38, appliquées à la main avant ce registre, sont marquées seulement si leur dernier objet existe en base (détectées,
-- jamais supposées) ; une migration non détectée reste à appliquer (toutes sont idempotentes à partir de 02).
-- Règle pour toute migration à partir de 39 : sa dernière instruction inscrit son propre nom (tests/test_migrations.py).

CREATE TABLE IF NOT EXISTS schema_migrations (
    nom           TEXT PRIMARY KEY,                -- nom du fichier, 39_schema_migrations.sql
    empreinte     TEXT,                            -- SHA 256 du fichier, inscrite par deployer.sh (NULL : pas encore)
    appliquee_le  TIMESTAMPTZ NOT NULL DEFAULT now(),
    detectee      BOOLEAN NOT NULL DEFAULT false   -- marquée d'après ses objets, appliquée avant le registre
);

INSERT INTO schema_migrations (nom, detectee)
SELECT nom, true FROM (VALUES
    ('01_schema.sql', to_regclass('public.alert_evidence') IS NOT NULL),
    ('02_simulation.sql', to_regclass('public.ais_days') IS NOT NULL),
    ('03_analyses.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'analyses' AND column_name = 'summary')),
    ('04_masks.sql', to_regclass('public.stationary_zones') IS NOT NULL),
    ('05_nav_status.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'positions' AND column_name = 'nav_status')),
    ('06_ais_gap.sql', to_regclass('public.reception_cells') IS NOT NULL),
    ('07_reception_coverage.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'reception_cells' AND column_name = 'coverage')),
    ('08_fixed_echoes.sql', to_regclass('public.fixed_echoes') IS NOT NULL),
    ('09_alert_actions.sql', to_regclass('public.alert_actions') IS NOT NULL),
    ('10_regions_infrastructure.sql', to_regclass('public.infrastructure') IS NOT NULL),
    ('11_direct.sql', to_regclass('public.ingested_files') IS NOT NULL),
    ('12_watchlist.sql', to_regclass('public.watchlist') IS NOT NULL),
    ('13_exploitation.sql', to_regclass('public.disk_status') IS NOT NULL),
    ('14_regions_manche_gascogne.sql', EXISTS (SELECT 1 FROM regions WHERE lower(name) = 'gascogne')),
    ('15_couverture_zones.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'ais_days' AND column_name = 'coverage')),
    ('16_regles_continues.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'alerts' AND column_name = 'rule_key')),
    ('17_interface.sql', to_regclass('public.stats_minute') IS NOT NULL),
    ('18_contenu.sql', to_regclass('public.vessel_notes') IS NOT NULL),
    ('19_region_passages.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'sar_passes' AND column_name = 'mission')),
    ('20_viirs_travailleurs.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'sar_passes' AND column_name = 'nuages')),
    ('21_travailleurs_fiabilite.sql', to_regclass('public.viirs_detections_unique_idx') IS NOT NULL),
    ('22_viirs_evaluation.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'travailleurs' AND column_name = 'diagnostic')),
    ('23_preuves_images.sql', to_regclass('public.preuves_images') IS NOT NULL),
    ('24_sources_sejours.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'positions' AND column_name = 'source')),
    ('25_sentinel.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'travailleurs' AND column_name = 'volumes_bloc')),
    ('26_supervision.sql', to_regclass('public.signes_de_vie') IS NOT NULL),
    ('27_traces.sql', to_regclass('public.mesures_otel') IS NOT NULL),
    ('28_sentinel_robuste.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'travailleurs' AND column_name = 'rupture_stock')),
    ('29_gpu_inutilisable.sql', to_regclass('public.types_incompatibles') IS NOT NULL),
    ('30_stats_processeur.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'travailleurs' AND column_name = 'cout_reel_eur')),
    ('31_tip_and_cue.sql', to_regclass('public.demandes_observation') IS NOT NULL),
    ('32_sentinel2.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'sar_passes' AND column_name = 'heure_tuiles')),
    ('33_controle.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'demandes_observation' AND column_name = 'rayon_demande_m')),
    ('34_modes_regles.sql', to_regclass('public.episodes_observes') IS NOT NULL),
    ('35_notifications.sql', EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'notification_permise')),
    ('36_zones_vigilance.sql', to_regclass('public.zones_vigilance') IS NOT NULL),
    ('37_scenarios.sql', to_regclass('public.scenarios_demo') IS NOT NULL),
    ('38_contenance.sql', EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'demandes_observation' AND column_name = 'contenance'))
) AS m(nom, presente)
WHERE presente
ON CONFLICT (nom) DO NOTHING;

INSERT INTO schema_migrations (nom) VALUES ('39_schema_migrations.sql') ON CONFLICT (nom) DO NOTHING;
