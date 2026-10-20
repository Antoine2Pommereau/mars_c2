-- MARS C2 : tip and cue (lot T1). Un signal suspect (le tip) crée une demande d'observation (le cue) : position
-- prédite au prochain passage Sentinel 1 qui la couvre (ou à un passage acquis dans les dernières heures), fenêtres
-- analysées dans la session Sentinel du passage, résultat rattaché à l'alerte d'origine. Idempotent.

CREATE TABLE IF NOT EXISTS demandes_observation (
    id                BIGSERIAL PRIMARY KEY,
    cree_le           TIMESTAMPTZ NOT NULL DEFAULT now(),
    mis_a_jour_le     TIMESTAMPTZ NOT NULL DEFAULT now(),
    tip               TEXT NOT NULL,          -- viirs, coupure_ais, rendezvous, watchlist, identite, operateur
    priorite          INTEGER NOT NULL,       -- la plus haute des origines regroupées
    vessel_id         BIGINT,                 -- absent pour une détection VIIRS ou un point de la carte
    position          GEOGRAPHY(Point, 4326) NOT NULL,   -- dernière position connue (ou position du tip)
    position_le       TIMESTAMPTZ NOT NULL,
    sog_kn            REAL,
    cog_deg           REAL,
    etat              TEXT NOT NULL DEFAULT 'en_attente' CHECK (etat IN ('en_attente', 'planifiee', 'analysee',
                          'sans_passage', 'expiree', 'refusee_budget', 'annulee')),
    expire_le         TIMESTAMPTZ NOT NULL,
    pass_id           BIGINT,                 -- passage retenu (ligne mise à jour en place de prévu à acquis)
    heure_passage     TIMESTAMPTZ,
    passage_retro     BOOLEAN,                -- passage déjà acquis au moment du choix
    position_predite  GEOGRAPHY(Point, 4326),
    rayon_m           REAL,                   -- cercle d'incertitude à l'heure du passage
    prediction        TEXT,                   -- estime, arret, point
    fenetres          JSONB,                  -- centres des fenêtres de 24 km [[lon, lat], …]
    analysis_id       BIGINT,
    resultat          TEXT CHECK (resultat IS NULL OR resultat IN ('present_apparie', 'present_sans_ais', 'absent',
                          'non_evaluable')),
    resultat_details  JSONB,
    detection_id      BIGINT,                 -- écho retenu dans le cercle
    distance_m        REAL,                   -- de l'écho à la position prédite
    motif             TEXT,
    auteur            TEXT,                   -- demande manuelle
    trace_id          TEXT,
    traceparent       TEXT,
    fini_le           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS demandes_observation_actives_idx ON demandes_observation (etat)
    WHERE etat IN ('en_attente', 'planifiee');
CREATE INDEX IF NOT EXISTS demandes_observation_pass_idx ON demandes_observation (pass_id);
CREATE INDEX IF NOT EXISTS demandes_observation_analyse_idx ON demandes_observation (analysis_id);
CREATE INDEX IF NOT EXISTS demandes_observation_cree_idx ON demandes_observation (cree_le);

-- Origines d'une demande : plusieurs tips sur un même navire ou une même zone sont regroupés dans une demande ; la clé
-- empêche de créer deux fois la même (une alerte mise à jour en place, un épisode revu à chaque cycle des règles)
CREATE TABLE IF NOT EXISTS demandes_origines (
    cle          TEXT PRIMARY KEY,            -- alerte:123, viirs:456, coupure:789:2026-10-19T10:00, operateur:…
    demande_id   BIGINT NOT NULL REFERENCES demandes_observation(id) ON DELETE CASCADE,
    tip          TEXT NOT NULL,
    priorite     INTEGER NOT NULL,
    alerte_id    BIGINT,
    detection_viirs_id BIGINT,
    motif        TEXT,
    trace_id     TEXT,                        -- trace de l'alerte d'origine (lien de span)
    cree_le      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS demandes_origines_demande_idx ON demandes_origines (demande_id);
CREATE INDEX IF NOT EXISTS demandes_origines_alerte_idx ON demandes_origines (alerte_id);

-- Preuve d'une alerte : la demande d'observation et son résultat
ALTER TABLE alert_evidence DROP CONSTRAINT IF EXISTS alert_evidence_evidence_type_check;
ALTER TABLE alert_evidence ADD CONSTRAINT alert_evidence_evidence_type_check
    CHECK (evidence_type IN ('analysis', 'detection', 'position', 'vessel', 'viirs', 'demande'));

GRANT SELECT ON demandes_observation, demandes_origines TO grafana_lecture;
