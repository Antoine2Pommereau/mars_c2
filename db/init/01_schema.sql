-- MARS C2, schéma initial (phase 1)
CREATE EXTENSION IF NOT EXISTS postgis;

-- Référentiel des identités AIS observées. Le MMSI n'est pas une identité fiable : clé technique.
CREATE TABLE vessels (
    id          BIGSERIAL PRIMARY KEY,
    mmsi        INTEGER NOT NULL,
    imo         INTEGER,
    name        TEXT,
    ship_type   TEXT,          -- libellé fourni par la Danish Maritime Authority
    flag        CHAR(2),
    length_m    REAL,
    first_seen  TIMESTAMPTZ,
    last_seen   TIMESTAMPTZ
);
CREATE INDEX vessels_mmsi_idx ON vessels (mmsi);

-- Positions AIS. Le partitionnement mensuel arrive en phase 2, avec le rejeu.
CREATE TABLE positions (
    id           BIGSERIAL PRIMARY KEY,
    vessel_id    BIGINT NOT NULL REFERENCES vessels (id),
    ts           TIMESTAMPTZ NOT NULL,
    geom         GEOGRAPHY(Point, 4326) NOT NULL,
    sog_kn       REAL,
    cog_deg      REAL,
    heading_deg  SMALLINT
);
CREATE INDEX positions_geom_idx ON positions USING GIST (geom);
CREATE INDEX positions_ts_brin ON positions USING BRIN (ts);
CREATE INDEX positions_vessel_ts_idx ON positions (vessel_id, ts);

-- Passages Sentinel 1 connus
CREATE TABLE sar_passes (
    id               BIGSERIAL PRIMARY KEY,
    product_name     TEXT NOT NULL UNIQUE,
    platform         TEXT,
    acquired_at      TIMESTAMPTZ NOT NULL,
    orbit_direction  TEXT,
    footprint        GEOGRAPHY          -- polygone ou multipolygone selon le produit
);

-- Une ligne par demande d'analyse
CREATE TABLE analyses (
    id             BIGSERIAL PRIMARY KEY,
    pass_id        BIGINT NOT NULL REFERENCES sar_passes (id),
    aoi            GEOGRAPHY(Polygon, 4326) NOT NULL,
    mode           TEXT NOT NULL CHECK (mode IN ('fast', 'full')),
    status         TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed')),
    model_version  TEXT,
    requested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at   TIMESTAMPTZ
);

CREATE TABLE detections (
    id                 BIGSERIAL PRIMARY KEY,
    analysis_id        BIGINT NOT NULL REFERENCES analyses (id) ON DELETE CASCADE,
    geom               GEOGRAPHY(Point, 4326) NOT NULL,
    objectness         REAL,
    vessel_score       REAL,
    fishing_score      REAL,
    length_m           REAL,
    contrast_vv_db     REAL,
    mask_reason        TEXT,
    matched_vessel_id  BIGINT REFERENCES vessels (id),
    match_cost         REAL
);
CREATE INDEX detections_analysis_idx ON detections (analysis_id);
CREATE INDEX detections_geom_idx ON detections USING GIST (geom);

CREATE TABLE alerts (
    id            BIGSERIAL PRIMARY KEY,
    type          TEXT NOT NULL CHECK (type IN ('DARK_SHIP', 'RENDEZVOUS', 'AIS_GAP', 'AIS_UNCONFIRMED')),
    severity      TEXT NOT NULL CHECK (severity IN ('faible', 'moyenne', 'elevee', 'critique')),
    status        TEXT NOT NULL DEFAULT 'nouvelle' CHECK (status IN ('nouvelle', 'acquittee', 'classee')),
    event_time    TIMESTAMPTZ NOT NULL,
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    geom          GEOGRAPHY(Point, 4326) NOT NULL,
    details       JSONB NOT NULL DEFAULT '{}'::jsonb,
    rule_version  TEXT NOT NULL
);
CREATE INDEX alerts_geom_idx ON alerts USING GIST (geom);
CREATE INDEX alerts_event_time_idx ON alerts (event_time);
CREATE INDEX alerts_status_severity_idx ON alerts (status, severity);

-- Traçabilité : chaque alerte est reliée à ses éléments sources
CREATE TABLE alert_evidence (
    alert_id       BIGINT NOT NULL REFERENCES alerts (id) ON DELETE CASCADE,
    evidence_type  TEXT NOT NULL CHECK (evidence_type IN ('analysis', 'detection', 'position', 'vessel')),
    evidence_id    BIGINT NOT NULL,
    PRIMARY KEY (alert_id, evidence_type, evidence_id)
);
