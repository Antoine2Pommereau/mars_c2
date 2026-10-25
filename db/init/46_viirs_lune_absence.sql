-- MARS C2 : seuil de lune des granules VIIRS non évaluables abaissé à 55 % (règles 2026.10.53, 25/10/2026). Idempotent.
-- Mesure sur la chaîne complète d'allenai : masque ou pas, son classifieur final écarte les navires faibles dès
-- 60 % (4 sur 4 jusqu'à 58 %, 3 à 60 %, 1 à 70 %, aucun à 75 %), d'où 55 % avec une marge ; sans masque au dessus de 80 %, la scène entière est éteinte.
-- Réapplique le marquage de la migration 44 avec ce seuil, mêmes motifs que mars/viirs.py (granule_non_evaluable).
UPDATE viirs_granules SET non_evaluable = CASE
    WHEN coalesce(masque, 'absent') <> 'fourni' AND lune > 80 THEN
        'masque des nuages absent, lune à ' || round(lune) || ' % : le modèle éteint toute la scène au dessus de '
        '80 %, aucune conclusion sur ce granule'
    ELSE 'lune à ' || round(lune) || ' % : au dessus de 55 %, le classifieur d''allenai écarte les navires faibles '
         '(mesuré : 3 sur 4 à 60 %, 1 à 70 %, aucun à 75 %), l''absence de détection ne prouve rien' END
 WHERE erreur IS NULL AND lune > 55;

INSERT INTO schema_migrations (nom) VALUES ('46_viirs_lune_absence.sql') ON CONFLICT (nom) DO NOTHING;
