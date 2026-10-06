-- MARS C2 : régions Manche et Gascogne, attendues par /api/infrastructure (FRANCE_REGIONS) mais absentes de la
-- migration 10. Idempotent : sur le serveur, elles ont été insérées à la main ; une région existante (même nom, à la
-- casse près) n'est ni dupliquée ni modifiée. Emprises identiques aux zones de collecte (mars/ais/live.py).

INSERT INTO regions (name, origin, geom, active)
SELECT v.name, 'manuelle', ST_MakeEnvelope(v.lon0, v.lat0, v.lon1, v.lat1, 4326)::geography, false
FROM (VALUES ('Manche', -5.0, 48.4, 2.6, 51.2),
             ('Gascogne', -6.0, 43.3, -1.0, 47.4)) AS v(name, lon0, lat0, lon1, lat1)
WHERE NOT EXISTS (SELECT 1 FROM regions r WHERE lower(r.name) = lower(v.name))
ON CONFLICT (name) DO NOTHING;

-- Couches attendues, comme pour les deux premières zones (sans toucher à celles déjà provisionnées)
INSERT INTO region_layers (region_id, layer, usage)
SELECT r.id, c.layer, c.usage
FROM regions r
CROSS JOIN (VALUES
    ('coastline', 'operationnelle'),
    ('bathymetry', 'affichage'),
    ('infrastructure', 'operationnelle')
) AS c(layer, usage)
WHERE lower(r.name) IN ('manche', 'gascogne')
ON CONFLICT (region_id, layer) DO NOTHING;
