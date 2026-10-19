-- MARS C2 : Sentinel 1 robuste (lot C3). Rupture de stock des GPU, types de secours, repli sur processeur, analyse
-- limitée aux fenêtres utiles. Idempotent.

-- Analyse en attente d'un GPU (rupture de stock chez Scaleway) : ni en échec, ni lancée ; reprise à chaque cycle tant
-- que la scène reste utile (sentinel.fenetre_utile_h), puis abandonnée avec son motif
ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_status_check;
ALTER TABLE analyses ADD CONSTRAINT analyses_status_check
    CHECK (status IN ('pending', 'running', 'done', 'failed', 'attente_gpu'));

ALTER TABLE analyses ADD COLUMN IF NOT EXISTS calcul TEXT;            -- gpu, cpu
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS calcul_type TEXT;       -- type d'instance lancé (L4-1-24G, GP1-S…) et zone
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS calcul_motif TEXT;      -- pourquoi ce choix, ou pourquoi l'attente
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS fenetres JSONB;         -- fenêtres à analyser, par scène (lon, lat)
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS octets_lus BIGINT;      -- octets lus dans les images (fenêtres seules)
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS attente_depuis TIMESTAMPTZ;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS essais_calcul INTEGER NOT NULL DEFAULT 0;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS prochain_essai TIMESTAMPTZ;

-- Travailleur créé puis détruit pour rupture de stock (412 out_of_stock à la création ou au démarrage) : il ne compte
-- pas dans le plafond quotidien
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS rupture_stock BOOLEAN NOT NULL DEFAULT false;

-- Essai 20 du 14/10/2026 : l'analyse 94 a été comptée « terminée » alors qu'aucune scène n'avait abouti. Toute analyse
-- Sentinel 1 « terminée » sans détection dont aucune scène n'a abouti (ou sans aucune scène relevée) repasse en échec.
UPDATE analyses a SET status = 'failed',
       error = coalesce(a.error, 'aucune scène analysée (reclassée le 19/10/2026, lot C3)')
WHERE a.source = 'sentinel1' AND a.status = 'done'
  AND NOT EXISTS (SELECT 1 FROM detections d WHERE d.analysis_id = a.id)
  AND (a.timings IS NULL OR jsonb_typeof(a.timings) <> 'object'
       OR NOT EXISTS (SELECT 1 FROM jsonb_each(a.timings) e WHERE coalesce(e.value->>'erreur', '') = ''));

GRANT SELECT (calcul, calcul_type, calcul_motif, octets_lus, attente_depuis, essais_calcul, prochain_essai, scenes,
              fenetres) ON analyses TO grafana_lecture;
GRANT SELECT (rupture_stock) ON travailleurs TO grafana_lecture;
