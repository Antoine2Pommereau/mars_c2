-- MARS C2 : lot P, phase E, zones de vigilance (docs/controle.md). Idempotent.
-- Zone dessinée sur la carte (polygone) ou tampon autour d'une infrastructure, avec un profil défini dans
-- config/rules.yaml (zones_vigilance.profils) : gravité minimale des alertes de la zone, seuils abaissés, priorité dans
-- tip and cue et la sélection Sentinel. Les valeurs restent dans le fichier versionné ; l'alerte enregistre la zone et
-- le profil appliqués (details.zone_vigilance, et l'empreinte de l'état du panneau).
CREATE TABLE IF NOT EXISTS zones_vigilance (
    id                BIGSERIAL PRIMARY KEY,
    nom               TEXT NOT NULL,
    motif             TEXT NOT NULL,
    profil            TEXT NOT NULL,
    geom              GEOGRAPHY(Geometry, 4326) NOT NULL,
    infrastructure_id BIGINT,                 -- tampon autour d'une infrastructure
    tampon_m          REAL,
    fin_le            TIMESTAMPTZ,            -- facultative : zone close d'elle même à cette date
    cree_le           TIMESTAMPTZ NOT NULL DEFAULT now(),
    cree_par          TEXT NOT NULL,
    retiree_le        TIMESTAMPTZ,
    retiree_par       TEXT
);
CREATE INDEX IF NOT EXISTS zones_vigilance_geom_idx ON zones_vigilance USING gist (geom);
CREATE INDEX IF NOT EXISTS alerts_zone_vigilance_idx ON alerts (((details->'zone_vigilance'->>'id')::bigint))
    WHERE details ? 'zone_vigilance';
GRANT SELECT (id, nom, profil, fin_le, cree_le, cree_par, retiree_le) ON zones_vigilance TO grafana_lecture;
