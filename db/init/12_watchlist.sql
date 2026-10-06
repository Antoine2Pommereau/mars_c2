-- MARS C2 : liste de surveillance (catalogue GUR de la flotte fantôme, OpenSanctions maritime). Idempotent.
-- Chargée par scripts/import_watchlist.py ; le rapprochement avec les navires vus est la vue vessel_watch.

CREATE TABLE IF NOT EXISTS watchlist (
    id           SERIAL PRIMARY KEY,
    source       TEXT NOT NULL CHECK (source IN ('gur', 'opensanctions')),
    ref          TEXT,                 -- identifiant dans la source (fiche OpenSanctions, MMSI pour le GUR)
    imo          INTEGER,
    mmsi         INTEGER,
    name         TEXT,
    risks        TEXT[] NOT NULL DEFAULT '{}',    -- thèmes OpenSanctions : sanction, mare.shadow, mare.detained…
    datasets     TEXT[] NOT NULL DEFAULT '{}',
    url          TEXT,
    sanctioned   BOOLEAN NOT NULL DEFAULT false,
    shadow       BOOLEAN NOT NULL DEFAULT false,  -- flotte fantôme selon OpenSanctions (thème mare.shadow)
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS watchlist_imo_idx ON watchlist (imo);
CREATE INDEX IF NOT EXISTS watchlist_mmsi_idx ON watchlist (mmsi);

-- Niveau de signal, du plus fort au plus faible (même logique que scripts/watchlist_check.py) :
--   fort            liste GUR et (sanctionné ou flotte fantôme selon OpenSanctions)
--   sanctionne      sanctionné selon OpenSanctions
--   flotte_fantome  flotte fantôme selon OpenSanctions, sans sanction
--   suspect_gur     liste GUR seulement
--   autre_risque    signalé par OpenSanctions pour un autre motif (immobilisation, avertissement)
CREATE OR REPLACE FUNCTION watch_level(gur BOOLEAN, sanctioned BOOLEAN, shadow BOOLEAN) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN gur AND (sanctioned OR shadow) THEN 'fort'
                WHEN sanctioned THEN 'sanctionne'
                WHEN shadow THEN 'flotte_fantome'
                WHEN gur THEN 'suspect_gur'
                ELSE 'autre_risque' END
$$;

CREATE OR REPLACE FUNCTION watch_rank(level TEXT) RETURNS SMALLINT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE level WHEN 'fort' THEN 0 WHEN 'sanctionne' THEN 1 WHEN 'flotte_fantome' THEN 2
                      WHEN 'suspect_gur' THEN 3 ELSE 4 END::smallint
$$;

-- Rapprochement : par OMI d'abord (stable), sinon par MMSI, source par source. Une correspondance par MMSI alors
-- que les deux OMI sont connus et différents est signalée (MMSI réattribué, ou identité usurpée).
-- Vue matérialisée, rafraîchie par l'ingestion quand des identités changent et après chaque import des listes.
DROP MATERIALIZED VIEW IF EXISTS vessel_watch;
CREATE MATERIALIZED VIEW vessel_watch AS
WITH m AS (
    SELECT v.id AS vessel_id, w.*, 'omi' AS par
    FROM vessels v JOIN watchlist w ON w.imo = v.imo
    UNION ALL
    SELECT v.id, w.*,
           CASE WHEN v.imo IS NOT NULL AND w.imo IS NOT NULL AND v.imo <> w.imo THEN 'mmsi_omi_different'
                ELSE 'mmsi' END
    FROM vessels v JOIN watchlist w ON w.mmsi = v.mmsi
    WHERE NOT EXISTS (SELECT 1 FROM watchlist w2 WHERE w2.source = w.source AND w2.imo = v.imo)
)
SELECT vessel_id,
       watch_level(bool_or(source = 'gur'), bool_or(sanctioned), bool_or(shadow)) AS level,
       watch_rank(watch_level(bool_or(source = 'gur'), bool_or(sanctioned), bool_or(shadow))) AS rank,
       CASE WHEN bool_or(par = 'omi') THEN 'omi'
            WHEN bool_or(par = 'mmsi') THEN 'mmsi' ELSE 'mmsi_omi_different' END AS matched_by,
       jsonb_agg(jsonb_build_object('source', source, 'name', name, 'imo', imo, 'mmsi', mmsi, 'risks', risks,
                                    'datasets', datasets, 'url', url, 'par', par) ORDER BY source) AS entries
FROM m
GROUP BY vessel_id;
CREATE UNIQUE INDEX IF NOT EXISTS vessel_watch_vessel_idx ON vessel_watch (vessel_id);
