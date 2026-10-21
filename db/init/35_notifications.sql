-- MARS C2 : lot P, phase D, préférences de notification (docs/controle.md). Idempotent.
-- Préférences en base (reglages_controle, clé notif:preferences) par dessus les valeurs par défaut du fichier
-- (config/rules.yaml, controle.notifications, recopiées par taches dans notifications_etat) : par gravité et type
-- d'alerte, résultat de tip and cue, catégories système (pannes, disque, collecte, travailleurs), heures calmes (heure
-- de Paris) qui retiennent tout sauf les pannes graves, avec un résumé au réveil. Appliqué aux envois de taches et
-- aux règles de Grafana (notification_permise).

CREATE TABLE IF NOT EXISTS notifications_etat (
    cle           TEXT PRIMARY KEY,            -- defauts (recopiés du fichier), derniere_alerte (dernière alerte vue)
    valeur        JSONB NOT NULL,
    mis_a_jour_le TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Notifications retenues pendant les heures calmes : envoyées en un résumé au réveil
CREATE TABLE IF NOT EXISTS notifications_retenues (
    id          BIGSERIAL PRIMARY KEY,
    le          TIMESTAMPTZ NOT NULL DEFAULT now(),
    categorie   TEXT NOT NULL,
    gravite     TEXT,
    titre       TEXT NOT NULL,
    message     TEXT,
    envoyee_le  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS notifications_retenues_attente_idx ON notifications_retenues (le) WHERE envoyee_le IS NULL;

CREATE OR REPLACE FUNCTION preferences_notification() RETURNS JSONB LANGUAGE sql STABLE AS $$
    SELECT coalesce((SELECT valeur FROM notifications_etat WHERE cle = 'defauts'), '{}'::jsonb)
        || coalesce((SELECT valeur FROM reglages_controle WHERE cle = 'notif:preferences'), '{}'::jsonb)
$$;

-- Heures calmes à l'instant t (heure de Paris), plage qui peut passer minuit
CREATE OR REPLACE FUNCTION heures_calmes(t TIMESTAMPTZ DEFAULT now()) RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT CASE WHEN NOT coalesce((p->'heures_calmes'->>'actif')::boolean, true) THEN false
                WHEN d = f THEN false
                WHEN d < f THEN h >= d AND h < f
                ELSE h >= d OR h < f END
    FROM (SELECT preferences_notification() AS p) x,
         LATERAL (SELECT coalesce((p->'heures_calmes'->>'debut')::time, '23:00') AS d,
                         coalesce((p->'heures_calmes'->>'fin')::time, '07:00') AS f,
                         (t AT TIME ZONE 'Europe/Paris')::time AS h) y
$$;

-- Une règle de Grafana peut elle notifier ? Catégorie active, et hors des heures calmes sauf panne grave
CREATE OR REPLACE FUNCTION notification_permise(categorie TEXT, grave BOOLEAN) RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT coalesce((preferences_notification()->'categories'->>categorie)::boolean, true)
           AND (grave OR NOT heures_calmes())
$$;

GRANT SELECT ON notifications_etat, notifications_retenues TO grafana_lecture;
GRANT EXECUTE ON FUNCTION preferences_notification(), heures_calmes(TIMESTAMPTZ), notification_permise(TEXT, BOOLEAN)
    TO grafana_lecture;
