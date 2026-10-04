-- MARS C2 : nouveau type d'alerte, incohérence d'identité AIS contre radar. Idempotent.
ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_type_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_type_check
    CHECK (type IN ('DARK_SHIP', 'RENDEZVOUS', 'AIS_GAP', 'AIS_UNCONFIRMED', 'INFRA_THREAT', 'IDENTITY_MISMATCH'));
