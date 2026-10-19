-- MARS C2 : lot C5. Statistiques de la frise sans trou après un incident, analyses Sentinel par lots sur processeur,
-- durée de vie propre à chaque travailleur. Idempotent.

-- Suivi des statistiques : dernier passage au registre d'ingestion (ingested_at). Les fichiers chargés depuis, même
-- anciens (rattrapage après un incident), font recalculer leurs minutes : le 18/10/2026, les positions de la période
-- de l'incident, chargées en retard, n'avaient jamais été comptées (stats_minute vide du 17/10 22:46 au 19/10 06:00)
CREATE TABLE IF NOT EXISTS stats_suivi (
    cle    TEXT PRIMARY KEY,
    valeur TIMESTAMPTZ NOT NULL
);

-- Analyse Sentinel 1 traitée par lots de fenêtres sur processeur (GPU en rupture) : fenêtres déjà analysées, par scène
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS fenetres_faites JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Durée de vie calculée au lancement (processeur : d'après le nombre de fenêtres et les mesures de l'essai du
-- 19/10/2026) ; sinon celle du type
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS duree_max_min INTEGER;

-- Coût réel (lot C5) : essai 26, RENDER-S facturé 1,23 € pour 0,28 € estimés (heure entamée). Mode de facturation
-- du type lancé (minute, heure_entamee), coût prévu au lancement, coût réel calculé à la destruction
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS facturation TEXT;
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS cout_prevu_eur DOUBLE PRECISION;
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS cout_reel_eur DOUBLE PRECISION;
-- Travailleurs déjà détruits : coût repris du journal (mode heure entamée pour les types CPU et le RENDER-S)
UPDATE travailleurs SET cout_reel_eur = (mesures->>'cout_estime_eur')::float8
WHERE cout_reel_eur IS NULL AND detruit_le IS NOT NULL AND mesures ? 'cout_estime_eur'
  AND commercial_type <> 'RENDER-S';
-- Essai 26 : facture de Scaleway, une heure entamée de RENDER-S
UPDATE travailleurs SET cout_reel_eur = 1.23, facturation = 'heure_entamee',
       mesures = mesures || '{"cout_estime_eur": 1.23, "cout_reel_eur": 1.23, "facturation": "heure_entamee"}'::jsonb
WHERE id = 26 AND commercial_type = 'RENDER-S';

GRANT SELECT (fenetres_faites) ON analyses TO grafana_lecture;
GRANT SELECT (duree_max_min, facturation, cout_prevu_eur, cout_reel_eur) ON travailleurs TO grafana_lecture;
