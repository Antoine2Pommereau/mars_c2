-- MARS C2 : contenu de l'interface (lot 2 de docs/vision_interface.md). Idempotent. Deux petites tables.

-- Navires suivis : liste de l'opérateur ; un navire suivi reste visible et coloré à toutes les échelles, et ses
-- nouvelles alertes remontent en tête du fil
CREATE TABLE IF NOT EXISTS followed_vessels (
    vessel_id  BIGINT PRIMARY KEY REFERENCES vessels (id) ON DELETE CASCADE,
    author     TEXT NOT NULL DEFAULT 'Opérateur',
    since      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Notes de l'opérateur sur un navire, horodatées et signées
CREATE TABLE IF NOT EXISTS vessel_notes (
    id         SERIAL PRIMARY KEY,
    vessel_id  BIGINT NOT NULL REFERENCES vessels (id) ON DELETE CASCADE,
    note       TEXT NOT NULL,
    author     TEXT NOT NULL DEFAULT 'Opérateur',
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vessel_notes_vessel_idx ON vessel_notes (vessel_id, at DESC);

-- Fiche infrastructure : positions proches d'un tracé, cherchées par morceaux courts (l'index spatial d'un câble de
-- plusieurs centaines de kilomètres, pris d'un bloc, ne filtre presque rien)
CREATE INDEX IF NOT EXISTS infrastructure_kind_idx ON infrastructure (kind);
