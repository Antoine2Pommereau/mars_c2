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
| **Masque des nuages** | CLDMSK_L2_VIIRS (`phys_path` de `run_pipeline`), couche `/geophysical_data/Clear_Sky_Confidence` ; absent ou illisible, allenai le remplit de zéros en silence, « équivalent d'une couverture nuageuse de 100 % » | jamais fourni jusqu'au 25/10/2026 ; depuis le lot viirs-masque, le CLDMSK de la même minute, vérifié par le travailleur (la couche est relue : allenai ne dirait rien d'un fichier illisible) | **corrigé** (lot viirs-masque) |
| Lune | au dessus de `MOONLIGHT_ILLUMINATION_PERCENT` (80 %, moyenne du granule) : seuls les pixels de ciel clair (masque au dessus de 0,99) restent | avec masque : ciel clair analysé ; sans masque, la scène entière est éteinte : le granule est « non évaluable » | **corrigé** ; sans masque sous la lune, aucune conclusion |
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

**Correction (lot viirs-masque, 25/10/2026, règles 2026.10.52, migration 44)** :
* Le serveur cherche au catalogue CMR, pour chaque granule, le masque de la même minute : CLDMSK_L2_VIIRS_SNPP_NRT et
  CLDMSK_L2_VIIRS_NOAA20_NRT d'abord (temps quasi réel), puis le produit standard CLDMSK_L2_VIIRS_SNPP, NOAA20 ou
  NOAA21 (`viirs.masque_nuages`). Constat du 25/10/2026 : le masque en temps quasi réel **n'est pas servi par
  LANCE** mais par SIPS (`sips-data.ssec.wisc.edu`, université du Wisconsin), le produit standard par LAADS
  (`data.laadsdaac.earthdatacloud.nasa.gov`, publié environ 12 h après) ; **aucun masque en temps quasi réel pour
  NOAA 21**. Les deux exigent la connexion Earthdata ; le travailleur leur présente le même jeton que pour LANCE.
* Le travailleur essaie les adresses dans l'ordre (span « GET masque des nuages », hôte, octets, part de ciel
  clair), relit la couche de ciel clair, puis passe le fichier en `phys_path`. Un échec n'arrête jamais le
  travailleur : le granule part avec « masque des nuages absent » et le motif de chaque essai.
* Le serveur écrit l'état du masque dans le granule (`masque`, `masque_motif`, `ciel_clair_part`) et, sans masque
  au dessus de 80 % de lune, `non_evaluable` : aucune conclusion sur ce granule ; une nuit dont tous les granules
  sont dans ce cas est « non évaluable » dans `viirs-bilan`, la frise et l'indicateur, jamais « 0 détection ». La
  migration 44 marque ainsi les granules déjà traités sous une lune forte (nuits du 22 et du 23/10/2026).
* Essais dans l'image d'allenai : `verifier_images.sh viirs-lune` (modèle seul, 4 sur 4 à 90 % avec masque, 0 sans)
  et `viirs-masque` (vrai script du travailleur, vrai chemin de téléchargement : serveur local qui joue LANCE, SIPS
  refusant le jeton et LAADS renvoyant vers un stockage sans jeton ; 4 sur 4 avec masque, 0 sans, masque tronqué
  nommé sans arrêt, 4 sur 4 à 50 % sans masque). Limite : le classifieur final d'allenai (`feedback_cnn`) écarte sous
  90 % de lune des bateaux synthétiques de 40 à 70 nW (2 sur 4) et garde ceux de 80 à 100 nW ; l'acceptation du
  jeton par SIPS et LAADS ne se prouve qu'avec le vrai jeton, au premier travailleur (motif écrit dans le granule).
* Granules en temps quasi réel conservés par LANCE environ 7 jours (constat du 25/10/2026 : jour 291 en cours de
  purge, 292 complet) : les nuits du 22 et du 23/10 se retraitent jusqu'au 29/10 environ
  (`taches.py viirs --nuit 2026-10-22 --nuit 2026-10-23`).

**Lune sous 80 % (25/10/2026, règles 2026.10.53, migration 46)** : la nuit du 20/10, à 72 % de lune, a donné
14 granules et 0 détection. Rejoué gratuitement dans l'image d'allenai (`verifier_images.sh viirs-lune`) :

| Lune | Modèle seul, sans masque | Chaîne complète, bateaux de 20 à 40 nW, sans masque |
|---|---|---|
| 55 % | 4 sur 4 | 4 sur 4 |
| 58 % | 4 sur 4 | 4 sur 4 |
| 60 % | 4 sur 4 | 3 sur 4 |
| 65 % | 4 sur 4 | 2 sur 4 |
| 70 % | 4 sur 4 | 1 sur 4 |
| 75 % | 4 sur 4 | 0 sur 4 |
| 80 % | 4 sur 4 | 0 sur 4 |
| 80,5 % | 0 sur 4 | |

Le modèle seul n'éteint la scène qu'au dessus de 80 %, d'un coup. La perte progressive vient du classifieur final
(`feedback_cnn`, qui reçoit la lune en canal), **masque ou pas** (mesuré à 60, 70, 75 et 80 % : le masque ne change
presque rien) : les navires de 40 à 70 nW tiennent jusqu'à 70 %, les plus faibles tombent dès 60 %. Nuits réelles
cohérentes : 52 détections à 53 %, 11 à 63 %, 0 à 72 %. D'où `viirs.lune_max_absence: 55` : au dessus, l'absence de
détection d'un granule ne prouve rien (« non évaluable »), ses détections restent valables. Coût : environ dix nuits
par mois deviennent non concluantes pour l'absence ; c'est la limite du modèle, pas de notre chaîne.
