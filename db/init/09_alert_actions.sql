-- MARS C2, phase 5 : cycle de vie des alertes et journal des décisions des opérateurs. Idempotent.
ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_status_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_status_check
    CHECK (status IN ('nouvelle', 'acquittee', 'confirmee', 'classee'));

CREATE TABLE IF NOT EXISTS alert_actions (
    id        SERIAL PRIMARY KEY,
    alert_id  BIGINT NOT NULL REFERENCES alerts (id) ON DELETE CASCADE,
    action    TEXT NOT NULL CHECK (action IN ('acquitter', 'confirmer', 'classer', 'rouvrir')),
    note      TEXT,
    author    TEXT NOT NULL,
    at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alert_actions_alert_idx ON alert_actions (alert_id, at);
