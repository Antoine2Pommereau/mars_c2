-- MARS C2, phase 2 : horloge de simulation et journées AIS disponibles.
-- Idempotent : peut être appliqué à une base existante.

-- Horloge simulée partagée par l'API, l'interface et les règles.
-- Instant simulé = sim_anchor + (instant réel - real_anchor) x speed, sauf en pause.
CREATE TABLE IF NOT EXISTS sim_clock (
    id           SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    sim_anchor   TIMESTAMPTZ NOT NULL,
    real_anchor  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    speed        DOUBLE PRECISION NOT NULL DEFAULT 10 CHECK (speed > 0 AND speed <= 3600),
    paused       BOOLEAN NOT NULL DEFAULT true
);
INSERT INTO sim_clock (id, sim_anchor) VALUES (1, '2024-06-05 16:30:00+00') ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION sim_now() RETURNS TIMESTAMPTZ
LANGUAGE sql VOLATILE AS $$
    SELECT CASE WHEN paused THEN sim_anchor
                ELSE sim_anchor + (clock_timestamp() - real_anchor) * speed END
    FROM sim_clock WHERE id = 1
$$;

-- Journées AIS chargées, pour ne proposer que des passages exploitables
CREATE TABLE IF NOT EXISTS ais_days (
    day          DATE PRIMARY KEY,
    messages     BIGINT NOT NULL,
    vessels      INTEGER NOT NULL,
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Le rejeu lit les positions par fenêtre de temps. Les positions étant importées navire par navire, l'index
-- BRIN sur ts est inefficace : un index B-tree sur ts est indispensable.
CREATE INDEX IF NOT EXISTS positions_ts_idx ON positions (ts);
ANALYZE positions;
