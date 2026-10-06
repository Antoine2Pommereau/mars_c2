-- MARS C2 : règles en continu et deux nouveaux types d'alerte. Idempotent.

-- Nouveaux types : navire d'une liste de surveillance dans nos eaux, changement d'identité
ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_type_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_type_check CHECK (type IN
    ('DARK_SHIP', 'RENDEZVOUS', 'AIS_GAP', 'AIS_UNCONFIRMED', 'WATCHLIST', 'IDENTITY_CHANGE'));

-- Clé stable d'une alerte comportementale : une nouvelle évaluation met à jour l'alerte au lieu de la recréer, et le
-- statut comme le journal des décisions des opérateurs sont conservés (mars/rules.py, save_alerts)
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS rule_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS alerts_rule_key_idx ON alerts (type, rule_key) WHERE rule_key IS NOT NULL;

-- Clés des alertes déjà en base, au format de mars/rules.py
UPDATE alerts SET rule_key = (details->'navire'->>'vessel_id') || ':' ||
       to_char((details->>'dernier_message')::timestamptz AT TIME ZONE 'UTC', 'YYYYMMDD"T"HH24MISS')
WHERE type = 'AIS_GAP' AND rule_key IS NULL AND details->'navire'->>'vessel_id' IS NOT NULL;
UPDATE alerts SET rule_key = (details->'navires'->0->>'vessel_id') || ':' || (details->'navires'->1->>'vessel_id') || ':' ||
       to_char((details->>'debut')::timestamptz AT TIME ZONE 'UTC', 'YYYYMMDD"T"HH24MISS')
WHERE type = 'RENDEZVOUS' AND rule_key IS NULL AND details->'navires'->1->>'vessel_id' IS NOT NULL;

-- Fiche navire : alertes dont un navire est la preuve
CREATE INDEX IF NOT EXISTS alert_evidence_vessel_idx ON alert_evidence (evidence_type, evidence_id);
