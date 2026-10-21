-- MARS C2 : lot P, panneau de contrôle (phase A : capteurs et dépenses ; socle commun aux phases suivantes).
-- L'état du panneau est en base et prime sur config/rules.yaml : on ne modifie plus le fichier sur le serveur pour
-- suspendre quoi que ce soit (une modification locale bloque git pull). Les seuils restent dans le fichier versionné.
-- Idempotent.

-- Journal de chaque action du panneau ou des commandes équivalentes : qui, quand, quoi, valeur avant et après, motif
CREATE TABLE IF NOT EXISTS journal_controle (
    id        BIGSERIAL PRIMARY KEY,
    le        TIMESTAMPTZ NOT NULL DEFAULT now(),
    auteur    TEXT NOT NULL,                 -- opérateur connecté, « terminal » (taches.py) ou « automatique »
    action    TEXT NOT NULL,                 -- pause, reprise, plafond, demande, mode_regle, connexion…
    cible     TEXT,                          -- sentinel1, sentinel2, viirs, tip_and_cue, nom de règle…
    avant     JSONB,
    apres     JSONB,
    motif     TEXT,
    trace_id  TEXT
);
CREATE INDEX IF NOT EXISTS journal_controle_le_idx ON journal_controle (le DESC);

-- Interrupteurs des analyses satellites : une ligne par source. Une source en pause ne crée aucun travailleur ;
-- reprise automatique à jusqu_a (facultatif), notification la veille
CREATE TABLE IF NOT EXISTS interrupteurs_satellites (
    source             TEXT PRIMARY KEY CHECK (source IN ('sentinel1', 'sentinel2', 'viirs', 'tip_and_cue')),
    en_pause           BOOLEAN NOT NULL DEFAULT false,
    depuis             TIMESTAMPTZ,
    jusqu_a            TIMESTAMPTZ,
    motif              TEXT,
    auteur             TEXT,
    veille_notifiee_le TIMESTAMPTZ           -- notification de la veille de la reprise, envoyée une fois
);
INSERT INTO interrupteurs_satellites (source) VALUES ('sentinel1'), ('sentinel2'), ('viirs'), ('tip_and_cue')
ON CONFLICT DO NOTHING;

-- Réglages du panneau, dans les plages bornées par config/rules.yaml (section controle) : plafonds quotidiens de
-- scènes, puis modes des règles, préférences de notification, priorité aux navires suivis (phases suivantes)
CREATE TABLE IF NOT EXISTS reglages_controle (
    cle           TEXT PRIMARY KEY,
    valeur        JSONB NOT NULL,
    mis_a_jour_le TIMESTAMPTZ NOT NULL DEFAULT now(),
    auteur        TEXT
);

-- Sessions des opérateurs : empreinte SHA 256 du jeton du cookie (le jeton lui même n'est jamais stocké), 12 h
CREATE TABLE IF NOT EXISTS sessions_operateur (
    empreinte   TEXT PRIMARY KEY,
    operateur   TEXT NOT NULL,
    cree_le     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expire_le   TIMESTAMPTZ NOT NULL,
    revoquee_le TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS sessions_operateur_expire_idx ON sessions_operateur (expire_le);

-- Source en pause : lu par les règles de Grafana (une alerte d'absence se tait pour une source en pause)
CREATE OR REPLACE FUNCTION source_en_pause(s TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT coalesce((SELECT en_pause AND (jusqu_a IS NULL OR jusqu_a > now()) FROM interrupteurs_satellites
                     WHERE source = s), false)
$$;

GRANT SELECT ON interrupteurs_satellites, reglages_controle TO grafana_lecture;
GRANT SELECT (id, le, auteur, action, cible, avant, apres, motif, trace_id) ON journal_controle TO grafana_lecture;
GRANT SELECT (cout_reel_eur) ON travailleurs TO grafana_lecture;
GRANT EXECUTE ON FUNCTION source_en_pause(TEXT) TO grafana_lecture;

-- Demande d'observation manuelle du panneau : point et rayon choisis par l'opérateur (borné par controle.rayon_demande_m)
ALTER TABLE demandes_observation ADD COLUMN IF NOT EXISTS rayon_demande_m REAL;
