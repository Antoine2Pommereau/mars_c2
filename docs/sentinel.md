# Sentinel 1 : analyse des passages par un travailleur GPU (étape 3, lot C)

Lot C, première livraison (C1 et C2) : détection des navires sur les images radar Sentinel 1, déclenchée
automatiquement sur les passages utiles, fusionnée avec l'AIS, avec ses alertes et ses preuves. Même architecture que
VIIRS (`docs/travailleurs_viirs.md`) : le serveur décide et reçoit, un travailleur éphémère fait l'analyse. Rédigé le
11/10/2026 ; **aucun essai sur une vraie instance GPU** à cette date (voir « Premier essai réel »).

## Chaîne

```
conteneur taches (serveur, fr-par-1)                    instance GPU éphémère L4-1-24G (fr-par-2)
  toutes les 30 min : passages Sentinel 1 récents ─API─▶ créée (image Ubuntu GPU OS, volume bloc), réseau privé
  avec un point d'intérêt (catalogue STAC, métadonnées)   cloud-init : script, tâche, adresses signées
  plafond de scènes, budget du mois                       docker pull de l'image d'AI2 (5 Go), --gpus all
  adresses de lecture signées (clés S3 du serveur)        téléchargement VV et VH (1 Go par scène), détection
                                                          vignettes radar, mesures, fichiers effacés
  nginx :8090 (réseau privé) ◀────────────────────────── POST /api/travailleurs/<id>/resultats (jeton)
  détections, vignettes sur R2, fusion AIS, alertes
  instance éteinte, supprimée, volume bloc supprimé
```

* **Le serveur ne télécharge jamais d'image** : il lit le catalogue STAC de Copernicus Data Space (métadonnées), signe
  des adresses de lecture valables 3 heures avec ses clés S3 (`CDSE_ACCESS_KEY`, `CDSE_SECRET_KEY`, jamais transmises
  au travailleur), et reçoit des détections et des vignettes (20 Ko chacune au plus).
* Code : `mars/sentinel.py` (sélection, garde fous, adresses, résultats, fusion, alertes), `travailleurs/sentinel.py`
  (script du travailleur), `backend/sentinel.py` (API), `mars/travailleurs.py` (orchestrateur commun), migration 25.

## Faits vérifiés (sources primaires, 11/10/2026)

| Sujet | Constat |
|---|---|
| Image d'AI2 | `ghcr.io/allenai/vessel-detection-sentinels`, étiquette `sha-fafc6c3` (commit du 04/01/2024), publique, amd64, 5,0 Go compressés en 18 couches ; base `nvidia/cuda:11.3.1-cudnn8`, torch 1.13 ; la couche du code pèse 291 Mo : les poids des modèles (Git LFS dans le dépôt) y sont très probablement. Épinglée par empreinte dans `config/rules.yaml`. |
| Serveur intégré de l'image | `main.py` appelle `detect_vessels` avec des arguments que la fonction n'accepte pas à cette version, et cherche sa configuration dans un dossier absent : inutilisable. Notre script appelle directement `prepare_scenes` puis `detect_vessels` avec leur signature réelle (lue dans le dépôt à ce commit). |
| Entrée du modèle | dossier `<scène>.SAFE/measurement/` avec les images VV et VH (polarisation lue dans le nom du fichier), reprojetées par GDAL ; le reste du produit n'est pas lu. |
| Sortie | `predictions.csv` : position, score, longueur, largeur, 16 classes de cap de 22,5°, vitesse, pêche. AI2 annonce 307 s par scène sur un T4 et demande un GPU d'au moins 8 Go. |
| Catalogue | collection STAC `sentinel-1-grd` : produits COG (`IW_GRDH_1S-COG`), liens `s3://eodata/...` vers `measurement/s1c-iw-grd-vv-…-cog.tiff` ; 0,42 Go (VH) et 0,57 Go (VV) pour une scène du 10/10/2026. |
| Accès S3 Copernicus | `https://eodata.dataspace.copernicus.eu`, région `default`, seau `eodata` ; 4 connexions simultanées, 20 Mo/s chacune, 12 To par 30 jours. |
| Scaleway L4-1-24G | fr-par-1, fr-par-2, pl-waw-2 ; 8 vCPU, 48 Go, GPU L4 24 Go ; 0,7875 € de l'heure, facturé à la minute ; **stockage bloc seulement** (pas de disque local) ; images « GPU OS » avec pilotes NVIDIA et Docker GPU (`ubuntu_noble_gpu_os_13_nvidia`, type `instance_sbs`). |
| Réseau privé | régional (« Private Networks let you connect resources across multiple AZs within the same region ») : un travailleur en fr-par-2 rejoint le serveur en fr-par-1 sur le même réseau privé, sans rien changer. Non encore éprouvé en réel. |
| Quota GPU | L4-1-24G : 1 instance, après validation de l'identité du compte ; au delà, demande au support. |

Non vérifié avant l'essai réel : CUDA 11.3 de l'image sur un L4 (architecture Ada), mémoire GPU réelle, présence
effective des poids (le script le vérifie et s'arrête avec la cause), référencement exact des fichiers COG par GDAL.

## Sélection des passages (C2)

Toutes les 30 minutes, un passage Sentinel 1 en mode IW acquis depuis moins de 36 heures, pas encore analysé, est
retenu s'il couvre au moins un **point d'intérêt** :
* un navire des listes (fort, sanctionné, flotte fantôme, suspect GUR) à moins de 2 milles d'une infrastructure, à
  30 minutes près de l'acquisition ;
* une alerte DARK_SHIP de source VIIRS, ouverte, de gravité moyenne au moins, de moins de 24 heures.

Seules les **scènes** (tranches de 25 s du passage) qui couvrent un point d'intérêt, à 10 km près, sont analysées, et
toutes les scènes du moment partent dans **une seule session GPU**. Scène pas encore au catalogue : nouvel essai au
cycle suivant, pendant 12 heures. Un passage n'est jamais analysé deux fois (index unique en base, même entre la
sélection automatique et la commande manuelle).

## Garde fous

| Garde fou | Valeur (`config/rules.yaml`, section `sentinel`) |
|---|---|
| Plafond quotidien | 2 scènes par jour (les plus riches en points d'intérêt d'abord) ; au delà, refus clair dans `task_runs`. **0 versionné depuis le 12/10/2026** : Sentinel arrêté tant que le diagnostic n'est pas au vert |
| Budget mensuel | 6 € : coût estimé avant lancement (12 min fixes et 6 min par scène, au tarif du L4) ajouté au coût engagé du mois ; au delà, refus |
| Travailleur | un seul travailleur Sentinel actif (verrou), plafonds communs (4 travailleurs et 180 min par jour), durée de vie 45 min, délai de démarrage 10 min, destruction gardée (`docs/travailleurs_viirs.md`) |
| Plafond commun | seuls comptent les travailleurs dont une instance a été créée chez Scaleway : un lancement refusé avant (image, quota, clés) ne coûte rien et ne compte pas (les 11 et 12/10/2026, sept refus de Scaleway en 0,1 s avaient épuisé le plafond et privé la nuit VIIRS de son travailleur) |
| Place réservée à VIIRS | tant que la nuit VIIRS du jour n'a pas eu son travailleur, Sentinel laisse 1 travailleur et 60 minutes (`travailleurs.reserve_viirs`) |
| Échec de lancement | même jour, mêmes passages : pas de nouvel essai toutes les 30 minutes ; la sélection automatique attend le lendemain (`task_runs`, tâche `sentinel`, motif affiché dans l'indicateur « Satellites ») ; l'analyse en échec ne bloque pas son passage (l'index unique ignore les analyses en échec) |
| Clés Copernicus | vérifiées par une lecture réelle de 16 octets d'une image avant toute analyse et avant tout appel à Scaleway |
| Volume bloc | enregistré en base dès la création ; instance éteinte puis supprimée (jamais `terminate`, qui laisserait le volume, facturé) ; volume supprimé ensuite, retenté chaque minute jusqu'au bout |
| Adresses de lecture | signées par le serveur, valables 3 heures ; aucune clé sur le travailleur |

## Fusion avec l'AIS et alertes

Pour chaque scène, à l'instant du milieu de la scène (25 s) :
1. **Positions AIS** à cet instant : interpolées entre deux messages, sinon estimées sur 10 minutes au plus.
2. **Tolérance orientée** : ellipse allongée le long de la trace du satellite (direction de vol calculée depuis
   l'inclinaison de l'orbite : 348° en ascendante, 192° en descendante vers 47° N), décalage Doppler selon la vitesse
   du navire vers le radar ; affectation un pour un (hongroise si scipy est là, gloutonne par coût croissant dans
   l'image des scripts, mêmes résultats sur les cas de test).
3. **Écartées** : sur la terre (moins de 500 m), écho fixe (revu à moins de 60 m un autre jour sans AIS : registre
   `fixed_echoes`, alertes antérieures classées), éolienne probable (moins de 150 m d'un parc éolien en mer).
   Contraste : mesuré par le travailleur sur l'image VV d'origine (pic contre anneau), gardé comme preuve ; il ne
   sert pas à écarter, le modèle d'AI2 ayant déjà filtré (`contraste_obligatoire: false`).
4. **Évaluation** (`mars/evaluation.py`, commune avec VIIRS) : coupure du flux AIS, ou hors de la zone de réception
   fiable, rendent une détection sans AIS non évaluable (aucune alerte).

| Alerte | Quand | Gravité |
|---|---|---|
| DARK_SHIP (source `sentinel1`) | écho sans navire AIS compatible, évaluable | comme VIIRS : critique dans un corridor et près d'un navire des listes, élevée pour l'un des deux, moyenne au delà de 12 milles, faible « à confirmer » sinon |
| AIS_UNCONFIRMED | navire AIS de 50 m au moins, dans l'emprise réelle de la scène, au large, dans la zone fiable, sans aucun écho compatible (tolérance élargie de 50 %) | moyenne au delà de 150 m, faible sinon |
| IDENTITY_MISMATCH | écho apparié dont la longueur ou l'orientation contredit la déclaration AIS | moyenne pour un navire des listes, faible sinon |

**Tolérances de IDENTITY_MISMATCH** :
* **Longueur** : un pixel GRD haute résolution fait 10 m et la résolution réelle environ 20 m, soit deux pixels
  d'incertitude à chaque bout, plus l'erreur du modèle. Une incohérence n'est retenue qu'au delà de 40 m **et** de
  40 % de la longueur déclarée, si cette longueur fait au moins 15 m.
* **Orientation** : 16 classes de 22,5°, et le radar confond souvent l'avant et l'arrière. Seul un écho en travers de
  la route déclarée (écart de 67,5° à 112,5°) compte, avec une classe de probabilité 0,5 au moins, pour un navire
  faisant au moins 3 nœuds.
* **À recalibrer** sur les premiers passages réels.

**Preuves** : vignette radar PNG d'environ 3 km de côté (VV en décibels, chatoiement adouci sur 2 × 2 pixels, 32
niveaux de gris, marqueur, échelle de 1 km, nord, satellite, date), avec sa géoréférence pour superposer l'AIS ;
rangée sur R2 (`vignettes/sentinel1/<jour>/<scène>/<détection>.png`, registre `preuves_images`, même conservation que
VIIRS). Essai sur une image synthétique géolocalisée par points d'appui (comme un GRD), dans l'image GDAL officielle :
cible retrouvée au pixel près, contraste de 32,6 dB (valeur attendue), 10,02 m par pixel, **19 Ko** par vignette
(fond de bruit pur, le pire cas).

## Interface et mesures

* Couche « Détections radar et optiques » : anneaux, blanc avec AIS, couleur du navire sombre sans AIS, gris écartée
  ou non évaluable ; un clic ouvre la fiche « Détection radar Sentinel 1 » (mesures, vignette avec les AIS à 2 km,
  navire apparié avec sa longueur déclarée, alertes liées).
* Fiche navire : section « Vérification satellite » (échos radar et lumières VIIRS appariés, longueur mesurée contre
  déclarée).
* Frise : un passage analysé est plus haut et cerclé ; son état d'analyse est dans l'infobulle.
* Indicateur « Satellites » : dernier passage analysé, dernier travailleur GPU, coût du mois, dernier refus.
* `/api/metrics` : `mars_sentinel_analyses_total`, `mars_sentinel_scenes_total`, durées de téléchargement et d'analyse
  et mémoire GPU de la dernière scène, `mars_sentinel_cout_mois_euros`, `mars_sentinel_detections_total` par statut.

## Coûts (hors taxes)

| Poste | Estimation | Par mois |
|---|---|---|
| L4-1-24G à la minute | une session de 2 scènes : environ 24 min (démarrage, image de 5 Go, 2 × 6 min) | 0,32 € la session |
| Volume bloc de 60 Go | quelques minutes par session | négligeable |
| IPv4, réseau privé, catalogue, données Copernicus | gratuits ou quasi | 0 € |
| **Scénario sobre** | 10 à 20 sessions par mois selon les points d'intérêt | **3 à 6 €**, budget plafonné à 6 € |

La durée réelle (démarrage d'une image GPU, téléchargement de 5 Go) n'est pas mesurée : le premier essai la donnera
(`task_runs`, tâche `travailleur_sentinel`).

## Mise en place (une fois)

1. **Scaleway, quota GPU** : console, Organization, Quotas : L4-1-24G vaut 1 une fois l'identité du compte validée
   (Account, Verification) ; sinon 0, et le lancement échoue avec le refus de Scaleway dans `task_runs`.
2. **Scaleway, droits** : à la politique de l'application `mars-c2-travailleurs` (portée Project), ajouter
   **BlockStorageFullAccess** (suppression des volumes bloc des instances GPU) aux deux ensembles existants.
3. **Réseau privé** : rien à faire, il est régional ; le même `SCW_PRIVATE_NETWORK_ID` sert en fr-par-2.
4. **Copernicus Data Space** : compte gratuit sur https://dataspace.copernicus.eu, puis clés S3 sur
   https://eodata-s3keysmanager.dataspace.copernicus.eu (Add Credentials ; date d'expiration au choix, la clé secrète
   n'est montrée qu'une fois). Dans le `.env` du serveur : `CDSE_ACCESS_KEY` et `CDSE_SECRET_KEY`.
5. Migration 25, images, puis le premier essai réel ci dessous.

## Image GPU sur volume bloc (corrigé le 12/10/2026, hypothèse à confirmer)

Les lancements 10 à 16 ont échoué avant toute création : « image e6d1b7f4 (ubuntu_noble_gpu_os_13_nvidia) introuvable
ou sans volume racine ». Le catalogue public trouve bien l'image `e6d1b7f4` (type `instance_sbs`, compatible
L4-1-24G, fr-par-2). L'échec venait de la lecture de sa fiche dans l'API Instances, que la vérification écrite pour les
DEV1-M (disque local) juge selon quatre conditions confondues dans un seul message. Désormais :
* un message par cause (introuvable, architecture, pas de volume racine, taille inconnue) ;
* pour le stockage bloc : volume racine de type `sbs_snapshot` accepté, et si la fiche n'en donne pas la taille
  (taille nulle, l'**hypothèse la plus probable**, non vérifiable sans clé), elle est lue dans l'API Block Storage
  (`/block/v1alpha1/zones/<zone>/snapshots/<id>`), comme le fait la ligne de commande Scaleway ;
* la vérification des DEV1-M est inchangée (disque local, taille dans la fiche), testée.
Ce qui reste une hypothèse : la forme exacte de la fiche d'une image sur volume bloc, le chemin Block Storage
`v1alpha1` (celui de la ligne de commande ; une `v1` existe aussi), le nom du quota GPU dans l'API IAM. Le diagnostic
affiche la fiche brute pour trancher.

## Diagnostic avant tout essai (lecture seule)

```bash
docker compose exec taches python scripts/taches.py sentinel-diagnostic
```

Aucune création, aucun coût : des lectures sur les API Scaleway et 16 octets d'une image Copernicus. Une ligne par
point, dans l'ordre du lancement : clés Copernicus (présentes, puis valides par une lecture réelle), clés Scaleway et
réseau privé dans le `.env`, API Instances en fr-par-2, **image GPU résolue avec sa fiche brute affichée**, disponibilité
annoncée du L4-1-24G en fr-par-2, quota du L4 (API IAM ; « à vérifier » si la clé limitée au projet ne peut pas le lire),
stockage bloc (lecture), ensembles de droits de la politique de la clé (si lisibles), réseau privé (régional, donc
rattachable depuis fr-par-2). Chaque ligne dit PRÊT, MANQUE (à corriger) ou À VÉRIFIER (illisible avec cette clé : à
contrôler dans la console) ; le plafond de scènes est donné pour information. Code de sortie non nul s'il manque quelque
chose.

## Premier essai réel (passage choisi à la main)

Seulement quand `sentinel-diagnostic` est entièrement au vert (aucune ligne MANQUE, et les lignes « à vérifier »
contrôlées dans la console). Le plafond versionné vaut 0 : le relever d'abord, sur le serveur seulement
(`sed -i 's/^  scenes_max_par_jour: 0$/  scenes_max_par_jour: 2/' config/rules.yaml`, relu au cycle suivant).
Pour valider la chaîne sur un seul passage :

1. Choisir un passage Sentinel 1 récent et acquis :
   `docker compose exec db psql -U mars -d mars -c "SELECT id, satellite, mode, acquired_at, cardinality(infra_ids) AS infra, cardinality(watch_ids) AS listes FROM sar_passes WHERE mission = 'S1' AND statut = 'acquis' ORDER BY acquired_at DESC LIMIT 10"`
2. Lancer : `docker compose exec taches python scripts/taches.py sentinel-analyser <id>` ; lire le résumé : scènes,
   coût estimé, travailleur. Un refus (plafond, budget, quota Scaleway) s'affiche avec son motif.
3. Suivre : `docker compose exec taches python scripts/taches.py travailleurs` (état chaque minute) et
   `docker compose exec taches python scripts/taches.py journal-travailleur <n>` (journal du démarrage : pilotes,
   image, préparation du GPU avec son nom, sa mémoire et la version de CUDA, puis une ligne par scène).
4. Attendu : état « termine » puis instance détruite et volume bloc supprimé ; dans `task_runs` (tâche
   `travailleur_sentinel`) la durée, le coût, la mémoire GPU ; dans l'interface, la couche, la fiche, la frise.
   Vérifier chez Scaleway qu'**aucun volume bloc** ne reste (console, Block Storage) : sinon le supprimer et le
   signaler.
5. En cas d'échec, la cause est dans `travailleurs.erreur` : GPU invisible (CUDA de l'image et pilote), poids
   absents, accès refusé par Copernicus (clés), image refusée par Scaleway (quota).

Arrêt d'urgence : `docker compose exec taches python scripts/taches.py detruire-travailleurs` (toutes zones des types).

## Limites

* Aucun essai sur une vraie instance GPU à ce jour ; les points « non vérifié » ci dessus seront tranchés au premier
  essai, avant tout automatisme (les clés CDSE absentes du `.env` le bloquent).
* Sentinel 2 (optique) n'est pas dans cette livraison ; le registre des preuves l'attend (`sentinel2`).
* Le modèle d'AI2 manque une partie des petits navires ; AIS_UNCONFIRMED ne concerne que les navires de 50 m et plus.
* Les seuils d'incohérence et de persistance sont des valeurs de départ, à calibrer sur les premiers passages.
* Un volume bloc dont l'instance aurait été supprimée hors de MARS C2 (console) n'est pas retrouvé automatiquement.
