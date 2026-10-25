# Contrats d'entrée des trois modèles (audit du 25/10/2026)

Pour chaque modèle : ce qu'il attend d'après son propre code et sa configuration, ce que nos travailleurs lui donnent
réellement, et un verdict par entrée. Tout ce qui pouvait être rejoué sans coût l'a été (données publiques, images
épinglées, sur le Mac). Origine : enquête sur l'analyse Sentinel 1 réelle 103 (`docs/sentinel.md`).

## Sentinel 1 : AI2 `vessel-detection-sentinels` (sha-fafc6c3)

Code lu au commit `fafc6c3` : `src/data/image.py` (prepare_scenes), `src/data/warp.py`, `src/inference/pipeline.py`,
`src/models/frcnn_cmp2.py`, configurations `model_artifacts/frcnn_cmp2/3dff445/cfg.json` et `attr/c34aa37/cfg.json`.

| Entrée | Ce que le modèle attend | Ce que le travailleur donne | Verdict |
|---|---|---|---|
| Produit | GRD IW double polarisation, un dossier `measurement` par scène | COG GRD IW de Copernicus, VV et VH, fenêtre recopiée en GeoTIFF avec ses points d'appui | conforme |
| Canaux et ordre | six : `vh`, `vv`, puis `vh_overlap0`, `vv_overlap0`, `vh_overlap1`, `vv_overlap1`, par groupes de deux (`GroupChannels: 2`, agrégation `max`) | **deux seulement jusqu'au 25/10/2026** : le modèle remplace l'historique par des zéros ; six avec le lot historique | **non conforme**, corrigé par le lot historique |
| Historique | deux passages antérieurs du même lieu, alignés par la géoréférence puis recalés par produit scalaire (32 pixels au plus) | aucun ; puis les deux plus récents de la même orbite relative dans les 24 jours, même emprise | corrigé ; recalage mesuré nul (0, 0) sur 103 |
| Nom des fichiers | polarisation dans le quatrième champ du nom séparé par « - » | nom de Copernicus (`s1d-iw-grd-vv-…`), désormais fixé par le travailleur (`nom_local`) | conforme |
| Valeurs, unités | nombres numériques bruts (uint16), écrêtés à 0..255 puis en uint8 par `prepare_scenes`, divisés par 255 dans le modèle | nombres bruts du COG, sans calibration | conforme : la production de 103 est reproduite au chiffre près sur la copie publique |
| Géométrie et résolution | reprojection Web Mercator par `gdalwarp`, pas du zoom 13 (9,55 m), bilinéaire, par les points d'appui | même chose, mais polynôme d'ordre 2 de GDAL : 54 m d'écart médian, 544 m au plus, 80 m à Weymouth | corrigé le 24/10/2026 (spline en plaque mince, 20 m au plus) |
| Taille minimale | carreaux de 2 048 pixels Web Mercator, bord de 400 | fenêtres d'au moins 2 300 pixels GRD | conforme |
| Seuil | `conf` 0,9 dans la documentation d'AI2 (établie avec historique), NMS 10 pixels entier | 0,9 ; NMS 10 entier | conforme avec historique ; sans historique, 0,95 (repli prudent) |
| Attributs | second modèle, `vh` et `vv`, carrés de 120 pixels | fournis par la chaîne d'AI2 | conforme |

Preuves rejouées (Planetary Computer, image d'AI2, vrai code du travailleur) :

| Fenêtre | Écho | Sans historique | Avec historique |
|---|---|---|---|
| 103, Weymouth | SMYRTOS au mouillage | 0,879 (manqué) | **0,966** |
| 103 | brise lames de Portland (détection 3 de la production) | **0,909** (faux positif) | 0,282 |
| 97, Marseille | MSC MERAVIGLIA en route | 0,879 avec la spline (0,9 et plus en production) | **0,962** |
| 97 | rochers de la côte, aucun AIS | **0,925** (faux positif, masqué « terre » en production) | 0,541 |
| 97 | VICTOR ANDRYUKHIN et CHEMICAL HUNTER à quai | 0,665 et 0,645 | moins de 0,5 |

Commandes : `scripts/verifier_images.sh geolocalisation`, `ai2-historique` ; `travailleurs/essai_diagnostic_s1.py`.

## Sentinel 2 : rslearn `sentinel2_vessels` (image `sentinel2_vessels_v0.0.22`)

Code lu dans l'image épinglée : `rslp/sentinel2_vessels/predict_pipeline.py`, `rslp/utils/filter.py`, configurations
`data/sentinel2_vessels/config.yaml` et `config_predict_local_files.json`.

| Entrée | Ce que le modèle attend | Ce que le travailleur donne | Verdict |
|---|---|---|---|
| Produit | L1C (réflectance au sommet de l'atmosphère) | L1C, 13 bandes lues par fenêtre (`lire_bandes`) | conforme |
| Bandes et ordre | détecteur sur neuf bandes : B04, B03, B02, B05, B06, B07, B08, B11, B12, prises par rslearn dans les fichiers donnés par nom | 13 fichiers, un par bande, nommés (`image_files`) | conforme : l'ordre est fait par rslearn, pas par nous |
| Résolution | B02, B03, B04, B08 à 10 m ; B05 à B12 stockées à 20 m ; B01, B09, B10 à 60 m ; projection et bornes prises sur B02 | chaque bande à sa résolution native, UTM de la tuile, même fenêtre | conforme |
| Harmonisation | la configuration « fichiers locaux » retire 1 000 (remap linéaire 1 000..11 000 vers 0..10 000), valable pour les traitements 04.00 et suivants | nombres bruts du L1C, traitement 05.xx ; le décalage n'est retiré que dans nos vignettes | conforme ; ne jamais le retirer une seconde fois avant rslearn |
| Normalisation | dans le modèle : écart type 3 000 pour les trois bandes visibles, 8 160 pour les six autres, moyenne nulle | aucune de notre côté | conforme |
| Taille | carreaux de 512 pixels, fenêtre arrondie à un multiple de 4 par rslearn | 2 400 pixels (24 km) | conforme |
| Historique | aucun (contrairement au détecteur Sentinel 2 d'AI2, absent de l'image Sentinel 1) | aucun | conforme |
| Seuil | `score_threshold` 0,8, NMS par distance 10 pixels | filtre du travailleur à 0,8 | conforme |
| Filtre des infrastructures | écarte les détections à moins de 50 m d'une infrastructure du fichier GeoJSON de Satlas, téléchargé de Google au lancement | rien : l'image le télécharge par Internet | conforme ; dépendance réseau (une coupure arrêterait l'inférence) |
| Heure | sans objet pour le modèle | heure de la tuile (`SENSING_TIME`) pour la fusion | hors modèle (piège connu) |

Preuve rejouée : `scripts/verifier_images.sh s2` (fenêtre réelle de Beachy Head du 04/10/2026, miroir public de
Google, vrai script du travailleur) rejouée le 25/10/2026 : 4 navires sur 4, scores de 0,99 à 1,00, longueurs de 103
à 403 m, 13 bandes lues en 33,6 Mo, inférence de 303 s en émulation.

## VIIRS : allenai `vessel-detection-viirs` (empreinte `eb5ae0e1…`)

Code lu au commit `3ccd3d2` : `src/pipeline.py`, `src/preprocessor.py`, `src/model.py`, `src/postprocessor.py`,
`src/utils.py`, `src/config/config.yml`.

| Entrée | Ce que le modèle attend | Ce que le travailleur donne | Verdict |
|---|---|---|---|
| Radiance DNB | VNP02DNB ou VJ102DNB (radiance en W/(cm² sr), multipliée par 1e9, écrêtée à 100 nW, aberrants au dessus de 1 000 nW) | fichiers LANCE NRT d'origine, vérifiés (taille, signature HDF5) | conforme |
| Géolocalisation | VNP03DNB ou VJ103DNB : latitude, longitude, masque terre et mer (codes 0, 6, 7 gardés), éclairement lunaire en % | fichiers d'origine | conforme |
| **Masque des nuages** | CLDMSK_L2_VIIRS (`phys_path` de `run_pipeline`) ; absent, allenai le remplit de zéros, « équivalent d'une couverture nuageuse de 100 % » | **jamais fourni** (`run_pipeline(dnb, geo, tmp)`) | **non conforme** |
| Lune | au dessus de `MOONLIGHT_ILLUMINATION_PERCENT` (80 %) : seuls les pixels de ciel clair (masque au dessus de 0,99) restent | sans masque, aucun pixel clair : **la scène entière est éteinte** | **défaut**, visible seulement sous une lune forte |
| Seuils | noyau 3 × 3, seuil 10, constante adaptative 3, côte à 3 000 m, bord de 10 pixels, NMS 0,5 | configuration de l'image, non modifiée | conforme |
| Filtres | `config["filters"]` par défaut | par défaut | conforme |

Preuve rejouée (gratuite, `travailleurs/essai_viirs_lune.py` dans l'image d'allenai, vrai `vvd_cv_model`, scène
synthétique de mer profonde avec quatre bateaux de 20 à 60 nW) :

| Lune | Masque des nuages | Détections |
|---|---|---|
| 50 % | absent (notre cas) | 4 sur 4 |
| 50 % | fourni, ciel clair | 4 sur 4 |
| 90 % | absent (notre cas) | **0 sur 4** |
| 90 % | fourni, ciel clair | 4 sur 4 |

**Nuits du 22 et du 23/10/2026** (24 granules, aucune erreur, 0 détection) : éclairement lunaire calculé à 01:30 UTC
de 82 % et 90 % (pleine lune le 26/10), donc au dessus de 80 % : **c'est le défaut, déclenché par la lune**, pas la
lune seule. Avec le masque des nuages, les zones de ciel clair auraient été analysées. Les nuits jusqu'au 29/10
environ (lune au dessus de 80 %) donneront aussi 0 détection, et le cas reviendra chaque mois pendant environ six
nuits. Vérification en base : colonne `viirs_granules.lune` (au dessus de 80 % ces nuits là) et
`viirs_detections.ciel_clair` (toujours nul depuis le début, faute de masque). Les données VIIRS réelles ne se
rejouent pas sans jeton Earthdata : la preuve est faite sur le vrai code du modèle.

**Correction proposée** (non faite) : lire aussi le granule CLDMSK_L2_VIIRS_SNPP_NRT ou CLDMSK_L2_VIIRS_NOAA20_NRT
de la même minute (même source LANCE, même jeton) et le passer en `phys_path` ; faute de masque, marquer la nuit
« non analysable, lune au dessus de 80 % sans masque des nuages » au lieu de compter zéro navire.
