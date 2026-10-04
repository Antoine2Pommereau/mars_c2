-- MARS C2 : collaboration, commenter et assigner une alerte, dans le journal des décisions. Idempotent.
ALTER TABLE alert_actions DROP CONSTRAINT IF EXISTS alert_actions_action_check;
ALTER TABLE alert_actions ADD CONSTRAINT alert_actions_action_check
    CHECK (action IN ('acquitter', 'confirmer', 'classer', 'rouvrir', 'commenter', 'assigner', 'tip_and_cue'));
