-- MARS C2 : GPU inutilisable (lot C4). Idempotent.
-- Essai réel du 19/10/2026 (passage 149, analyse 96, travailleur 26) : le RENDER-S (P100, Pascal) a démarré, mais
-- docker run --gpus a échoué (« failed to initialize NVML: Driver Not Loaded ») : l'image GPU de Scaleway charge les
-- modules noyau ouverts de NVIDIA, sans Pascal. Un GPU inutilisable est un problème de type, pas d'analyse.

-- Nouvel état signalé par le script de démarrage du travailleur (contrôle du GPU avant l'analyse)
ALTER TABLE travailleurs DROP CONSTRAINT IF EXISTS travailleurs_etat_check;
ALTER TABLE travailleurs ADD CONSTRAINT travailleurs_etat_check
    CHECK (etat IN ('demande', 'cree', 'demarre', 'resultats', 'termine', 'echec', 'gpu_inutilisable'));

-- Types d'instance marqués incompatibles (GPU inutilisable) : jamais essayés jusqu'à `jusqu_au` (7 jours), sauf levée
-- à la main (taches.py sentinel-lever-incompatible TYPE ZONE). Un marquage déclenche une seule alerte ntfy (Grafana).
CREATE TABLE IF NOT EXISTS types_incompatibles (
    id              BIGSERIAL PRIMARY KEY,
    commercial_type TEXT NOT NULL,
    zone            TEXT NOT NULL,
    depuis          TIMESTAMPTZ NOT NULL DEFAULT now(),
    jusqu_au        TIMESTAMPTZ NOT NULL,
    message         TEXT,
    travailleur_id  BIGINT,
    leve_le         TIMESTAMPTZ,
    trace_id        TEXT
);
CREATE INDEX IF NOT EXISTS types_incompatibles_actifs_idx ON types_incompatibles (commercial_type, zone, jusqu_au)
    WHERE leve_le IS NULL;
GRANT SELECT ON types_incompatibles TO grafana_lecture;

-- Essai du 19/10/2026 : historique gardé. Le travailleur 26 reste en échec, avec la cause NVML ; le RENDER-S en fr-par-2
-- est marqué (daté de l'essai : pas d'alerte rétroactive), bien qu'il ait quitté la liste des types
UPDATE travailleurs SET erreur = coalesce(erreur || ' ; ', '')
       || 'cause : GPU inutilisable, docker run --gpus en échec (code 125), « failed to initialize NVML: Driver Not '
       || 'Loaded » : modules noyau ouverts de NVIDIA sans Pascal'
WHERE id = 26 AND commercial_type = 'RENDER-S' AND coalesce(erreur, '') NOT LIKE '%NVML%';

INSERT INTO types_incompatibles (commercial_type, zone, depuis, jusqu_au, message, travailleur_id)
SELECT 'RENDER-S', 'fr-par-2', t.cree_le, t.cree_le + interval '7 days',
       'failed to initialize NVML: Driver Not Loaded (Pascal, modules noyau ouverts de NVIDIA)', t.id
FROM travailleurs t
WHERE t.id = 26 AND t.commercial_type = 'RENDER-S'
  AND NOT EXISTS (SELECT 1 FROM types_incompatibles i WHERE i.travailleur_id = 26);

-- Analyse 96 (passage 149) : en échec avec sa cause, ce qui libère le passage pour une nouvelle analyse lancée à la
-- main (docs/sentinel.md, « Essai réel du lot C4 ») ; l'index unique n'autorise qu'une analyse non échouée par passage
UPDATE analyses SET status = 'failed', completed_at = coalesce(completed_at, now()),
       error = 'GPU inutilisable (RENDER-S, NVML Driver Not Loaded), aucune scène lue : relancer à la main sur le '
               || 'passage 149 (lot C4)'
WHERE id = 96 AND pass_id = 149 AND source = 'sentinel1' AND status <> 'failed';
