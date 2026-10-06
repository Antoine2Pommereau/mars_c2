-- MARS C2 : couverture réelle des données AIS d'une journée (union des zones collectées), pour que la règle des
-- coupures AIS mesure le bord des données sur les zones et non sur leur rectangle englobant. Idempotent.
-- Une journée sans couverture renseignée garde l'ancien comportement (rectangle lon_min, lat_min, lon_max, lat_max).

ALTER TABLE ais_days ADD COLUMN IF NOT EXISTS coverage GEOMETRY(MultiPolygon, 4326);

-- Journées déjà chargées par l'ingestion en direct (reconnaissables à l'emprise des quatre zones France) :
-- couverture rétablie à partir des zones de mars/ais/live.py
UPDATE ais_days SET coverage = ST_Multi(ST_Union(ARRAY[
        ST_MakeEnvelope(-6.8, 47.3, -3.0, 49.6, 4326),     -- bretagne
        ST_MakeEnvelope(3.0, 41.2, 9.8, 43.7, 4326),       -- mediterranee
        ST_MakeEnvelope(-5.0, 48.4, 2.6, 51.2, 4326),      -- manche
        ST_MakeEnvelope(-6.0, 43.3, -1.0, 47.4, 4326)]))   -- gascogne
WHERE coverage IS NULL AND lon_min = -6.8 AND lat_min = 41.2 AND lon_max = 9.8 AND lat_max = 51.2;
