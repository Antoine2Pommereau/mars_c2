-- MARS C2 : lot P, phase F, scénarios de démonstration (docs/controle.md). Idempotent.
-- Un scénario est une vue enregistrée de l'interface (adresse de la page : mode, période, instant, région, objet
-- sélectionné, couches), rouverte d'un clic ; le rejeu lui même ne lit que la base, sans rien écrire.
CREATE TABLE IF NOT EXISTS scenarios_demo (
    id          BIGSERIAL PRIMARY KEY,
    nom         TEXT NOT NULL,
    adresse     TEXT NOT NULL,              -- chaîne de requête de la page (?mode=plage&debut=…)
    debut       TIMESTAMPTZ,
    fin         TIMESTAMPTZ,
    region      TEXT,
    selection   TEXT,
    cree_le     TIMESTAMPTZ NOT NULL DEFAULT now(),
    cree_par    TEXT NOT NULL,
    retire_le   TIMESTAMPTZ
);
