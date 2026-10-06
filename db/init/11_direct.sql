-- MARS C2, étape 1 de la nouvelle direction : AIS en direct (AISStream) chargé en base. Idempotent.

-- Horloge : en direct par défaut, elle suit l'heure réelle ; le rejeu d'une période passée la détache.
ALTER TABLE sim_clock ADD COLUMN IF NOT EXISTS live BOOLEAN NOT NULL DEFAULT true;

CREATE OR REPLACE FUNCTION sim_now() RETURNS TIMESTAMPTZ
LANGUAGE sql VOLATILE AS $$
    SELECT CASE WHEN live THEN clock_timestamp()
                WHEN paused THEN sim_anchor
                ELSE sim_anchor + (clock_timestamp() - real_anchor) * speed END
    FROM sim_clock WHERE id = 1
$$;

-- Identité déclarée : indicatif, code de type AIS (le libellé reste dans ship_type, au format de la DMA pour
-- que les règles existantes s'appliquent sans changement), source de la ligne.
ALTER TABLE vessels ADD COLUMN IF NOT EXISTS callsign TEXT;
ALTER TABLE vessels ADD COLUMN IF NOT EXISTS ship_type_code SMALLINT;
ALTER TABLE vessels ADD COLUMN IF NOT EXISTS destination TEXT;
CREATE INDEX IF NOT EXISTS vessels_imo_idx ON vessels (imo) WHERE imo IS NOT NULL;

-- Historique des identités déclarées par un navire (un MMSI) : une ligne par combinaison nom, OMI, indicatif,
-- type et pavillon, avec sa période d'observation. Un changement de pavillon change en général le MMSI : on le
-- retrouve en reliant les MMSI par l'OMI (vue imo_history).
CREATE TABLE IF NOT EXISTS vessel_identities (
    id              BIGSERIAL PRIMARY KEY,
    vessel_id       BIGINT NOT NULL REFERENCES vessels (id) ON DELETE CASCADE,
    name            TEXT,
    imo             INTEGER,
    callsign        TEXT,
    ship_type_code  SMALLINT,
    flag            CHAR(2),
    first_seen      TIMESTAMPTZ NOT NULL,
    last_seen       TIMESTAMPTZ NOT NULL,
    messages        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS vessel_identities_vessel_idx ON vessel_identities (vessel_id, last_seen);
CREATE INDEX IF NOT EXISTS vessel_identities_imo_idx ON vessel_identities (imo) WHERE imo IS NOT NULL;

CREATE OR REPLACE VIEW imo_history AS
SELECT i.imo, v.mmsi, i.vessel_id, i.name, i.flag, i.callsign, i.first_seen, i.last_seen, i.messages
FROM vessel_identities i JOIN vessels v ON v.id = i.vessel_id
WHERE i.imo IS NOT NULL;

-- Registre des fichiers Parquet chargés : chaque fichier de la collecte est chargé une fois et une seule.
CREATE TABLE IF NOT EXISTS ingested_files (
    folder       TEXT NOT NULL,        -- par exemple positions/zone=bretagne/date=2026-10-06
    name         TEXT NOT NULL,        -- 143012.parquet
    rows_read    INTEGER NOT NULL,
    rows_kept    INTEGER NOT NULL,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (folder, name)
);
