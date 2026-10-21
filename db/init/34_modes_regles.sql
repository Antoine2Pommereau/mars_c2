-- MARS C2 : lot P, phase C, modes des règles d'alerte (docs/controle.md). Idempotent.
-- Mode de chaque règle et sous type (actif, observation, coupé) en base, dans reglages_controle (clé mode:<règle> ou
-- mode:<règle>.<sous type>) : il prime sur le mode du fichier (ais_gap.mode compris). Un changement de mode ne supprime
-- aucune alerte existante.

-- Empreinte de l'état du panneau qui a influencé une alerte (modes des règles, puis zones de vigilance), en plus de
-- la version des règles ; l'état complet est gardé une fois par empreinte
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS controle_empreinte TEXT;
CREATE TABLE IF NOT EXISTS empreintes_controle (
    empreinte TEXT PRIMARY KEY,
    etat      JSONB NOT NULL,
    cree_le   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Épisodes d'une règle en observation : comptés à chaque cycle dans task_runs, et gardés une fois par épisode (clé de
-- l'alerte qu'il aurait donnée) pour le décompte des 7 derniers jours du panneau
CREATE TABLE IF NOT EXISTS episodes_observes (
    regle              TEXT NOT NULL,
    sous_type          TEXT,
    cle                TEXT NOT NULL,
    gravite            TEXT,
    premiere_le        TIMESTAMPTZ NOT NULL DEFAULT now(),
    derniere_le        TIMESTAMPTZ NOT NULL DEFAULT now(),
    controle_empreinte TEXT,
    PRIMARY KEY (regle, cle)
);
CREATE INDEX IF NOT EXISTS episodes_observes_derniere_idx ON episodes_observes (derniere_le);

GRANT SELECT ON empreintes_controle, episodes_observes TO grafana_lecture;
GRANT SELECT (controle_empreinte) ON alerts TO grafana_lecture;
