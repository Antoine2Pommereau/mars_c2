-- MARS C2 : lot Z1, sécurité (docs/exploitation.md). Idempotent.
-- Tentatives de connexion refusées, par adresse (X-Real-IP posé par nginx) : 5 en 10 minutes bloquent l'adresse
-- 10 minutes ; gardées 7 jours, elles survivent à un redémarrage de l'API (le compteur était en mémoire).
CREATE TABLE IF NOT EXISTS tentatives_connexion (
    id        BIGSERIAL PRIMARY KEY,
    ip        TEXT NOT NULL,
    operateur TEXT,
    le        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tentatives_connexion_ip_idx ON tentatives_connexion (ip, le DESC);

-- Échéances des secrets (variables *_EXPIRENT_LE du .env), relevées chaque jour par taches (tâche echeances) : la
-- date seule, jamais le secret. Lues par l'API pour Coulisses, Système ; notification 14 puis 3 jours avant.
CREATE TABLE IF NOT EXISTS echeances (
    cle           TEXT PRIMARY KEY,               -- cdse_serveur, cdse_travailleur, earthdata, github_lecture...
    libelle       TEXT NOT NULL,
    variable      TEXT NOT NULL,                  -- nom de la variable du .env qui porte la date
    expire_le     DATE,                           -- NULL : aucune date déclarée
    illisible     BOOLEAN NOT NULL DEFAULT false, -- valeur présente mais pas au format JJ/MM/AAAA ni AAAA-MM-JJ
    notifiee      TEXT,                           -- dernier palier notifié (j14, j3, expiree) pour cette date
    mis_a_jour_le TIMESTAMPTZ NOT NULL DEFAULT now()
);
GRANT SELECT ON echeances TO grafana_lecture;

INSERT INTO schema_migrations (nom) VALUES ('40_securite.sql') ON CONFLICT (nom) DO NOTHING;
