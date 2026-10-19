-- MARS C2 : traces OpenTelemetry (étape D2). Chaque exécution, travailleur, analyse et alerte remonte à la trace qui
-- l'a produite (Grafana Tempo, conservation de 72 h). Idempotent.

ALTER TABLE task_runs   ADD COLUMN IF NOT EXISTS trace_id TEXT;
ALTER TABLE analyses    ADD COLUMN IF NOT EXISTS trace_id TEXT;
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS trace_id TEXT;
-- Contexte W3C du lancement (00-<trace>-<span>-01), remis au travailleur avec sa tâche : son démarrage, ses
-- téléchargements, son inférence, puis le traitement de ses résultats et sa destruction rejoignent la même trace
ALTER TABLE travailleurs ADD COLUMN IF NOT EXISTS traceparent TEXT;

-- Alertes : identifiant de la trace qui les a créées ou mises à jour, dans leurs détails. Le code donne la trace à
-- la session (set_config('mars.trace_id', …), mars/traces.py, en_base) ; ce déclencheur l'écrit, quel que soit le
-- chemin d'écriture (règles en continu, VIIRS, Sentinel 1, analyses radar) : traçable par construction.
-- trace_id : dernière trace qui a écrit l'alerte ; trace_id_creation : celle qui l'a créée. Une mise à jour sans
-- trace (décision d'un opérateur) garde les deux. Un pool de connexions partagé (API) écrit trace_id dans les
-- détails lui même, sans toucher à la session : il est gardé.
CREATE OR REPLACE FUNCTION alerts_trace() RETURNS trigger AS $$
DECLARE
    t TEXT := nullif(current_setting('mars.trace_id', true), '');
    avant JSONB := CASE WHEN TG_OP = 'UPDATE' THEN coalesce(OLD.details, '{}'::jsonb) ELSE '{}'::jsonb END;
BEGIN
    NEW.details := coalesce(NEW.details, '{}'::jsonb);
    NEW.details := NEW.details || jsonb_strip_nulls(jsonb_build_object(
        'trace_id', coalesce(t, NEW.details->>'trace_id', avant->>'trace_id'),
        'trace_id_creation', coalesce(avant->>'trace_id_creation', NEW.details->>'trace_id_creation', t,
                                     NEW.details->>'trace_id')));
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS alerts_trace ON alerts;
CREATE TRIGGER alerts_trace BEFORE INSERT OR UPDATE OF details ON alerts
    FOR EACH ROW EXECUTE FUNCTION alerts_trace();

-- Mesures du collecteur OpenTelemetry, relevées chaque minute par taches (port 8888 du collecteur, réseau Docker) :
-- spans reçus, refusés (mémoire saturée), envoyés à Tempo, en échec, file d'attente, mémoire. Compteurs cumulés depuis
-- le démarrage du collecteur (remis à zéro à chaque redémarrage). Lues par Grafana sans Prometheus ; 30 jours gardés.
CREATE TABLE IF NOT EXISTS mesures_otel (
    mesure_le       TIMESTAMPTZ PRIMARY KEY DEFAULT now(),
    joignable       BOOLEAN NOT NULL,
    recus           BIGINT,
    refuses         BIGINT,
    envoyes         BIGINT,
    echecs_envoi    BIGINT,
    file_attente    BIGINT,
    traces_gardees  BIGINT,
    memoire_octets  BIGINT,
    demarre_depuis_s DOUBLE PRECISION
);

GRANT SELECT ON mesures_otel TO grafana_lecture;
GRANT SELECT (trace_id) ON travailleurs TO grafana_lecture;
GRANT SELECT (trace_id) ON analyses TO grafana_lecture;
