# Sentinel 1 : analyse des passages par un travailleur éphémère (étape 3, lot C)

> **État au 24/10/2026**
> * **En pause** depuis le 21/10/2026 par le panneau de contrôle (état en base, table `interrupteurs_satellites` ;
>   le fichier garde `scenes_max_par_jour: 2`). Reprise : Coulisses, Sources et capteurs, ou `taches.py satellites-pause off sentinel1`.
> * Calcul : **processeur en premier** (GP1-S en fr-par-2, POP2-8C-32G en secours) pour 4 fenêtres au plus ; GPU
>   (L4 fr-par-2, L4 fr-par-1, L40S fr-par-2) seulement au delà de 4 fenêtres ou pour une scène entière ; RENDER-S retiré.
> * **Une seule analyse réelle aboutie** : analyse 97 du 19/10/2026 (passage 149, GP1-S, 1 scène, 122 s, 2,6 Go de
>   mémoire, 1,7 % de la scène lue, 2 détections : MSC MERAVIGLIA appariée, un écho écarté à terre). **Jamais d'analyse
>   aboutie sur GPU** (essai 20 du 14/10 : GPU vu, import corrigé depuis ; rupture du L4 le 16/10 ; RENDER-S incompatible
>   le 19/10). POP2-8C-32G jamais lancé.
> * Coût unitaire mesuré : **0,19 € HT par session GP1-S** (heure entamée) ; budget de 8 € par mois partagé avec
>   Sentinel 2 et tip and cue.
> * Ce qui manque : une série d'analyses réelles (100 analyses sur 101 en échec, dont 82 essais du 12/10), un essai
>   GPU abouti, la recalibration des tolérances d'IDENTITY_MISMATCH.
> * Travailleurs (création, types, destruction, sécurité, facturation, diagnostic) : `docs/travailleurs.md`. Les
>   sections datées plus bas sont l'historique des essais ; les sections qui supposaient un GPU unique sont signalées.

Lot C, première livraison (C1 et C2) : détection des navires sur les images radar Sentinel 1, déclenchée
automatiquement sur les passages utiles, fusionnée avec l'AIS, avec ses alertes et ses preuves. Même architecture que
VIIRS (`docs/travailleurs.md`) : le serveur décide et reçoit, un travailleur éphémère fait l'analyse. Rédigé le
11/10/2026 pour un travailleur GPU ; depuis le lot C5 (19/10/2026), le travailleur est d'abord un GP1-S sur processeur.

## Chaîne

Schéma de la conception du 11/10/2026 (L4) ; un GP1-S suit la même chaîne, sans volume bloc ni `--gpus`.

```
conteneur taches (serveur, fr-par-1)                    instance GPU éphémère L4-1-24G (fr-par-2)
  toutes les 30 min : passages Sentinel 1 récents ─API─▶ créée (image Ubuntu GPU OS, volume bloc), réseau privé
  avec un point d'intérêt (catalogue STAC, métadonnées)   cloud-init : script et jeton seulement (ni scène ni clé)
  plafond de scènes, budget du mois                       docker pull de l'image d'AI2 (5 Go), --gpus all
  lecture de 16 octets avec la paire des travailleurs     POST …/etat {"etat":"tache"} (jeton, réseau privé) :
  nginx :8090 (réseau privé) ──────────────────────────▶ tâche et clés dédiées, remises une seule fois
                                                          téléchargement VV et VH en S3, signature par en tête
                                                          (1 Go par scène), détection
                                                          vignettes radar, mesures, fichiers effacés
  nginx :8090 (réseau privé) ◀────────────────────────── POST /api/travailleurs/<id>/resultats (jeton)
  détections, vignettes sur R2, fusion AIS, alertes
  instance éteinte, supprimée, volume bloc supprimé
```

* **Le serveur ne télécharge jamais d'image** : il lit le catalogue STAC de Copernicus Data Space (métadonnées) et
  reçoit des détections et des vignettes (20 Ko chacune au plus). Le travailleur lit lui même les images en S3.
* **Deux paires de clés S3** (corrigé le 14/10/2026, voir « Copernicus refuse les adresses présignées ») :
  * celle du serveur (`CDSE_ACCESS_KEY`, `CDSE_SECRET_KEY`) ne quitte jamais le serveur ; elle ne sert qu'au diagnostic ;
  * celle des travailleurs (`CDSE_TRAVAILLEUR_ACCESS_KEY`, `CDSE_TRAVAILLEUR_SECRET_KEY`, expiration dans
    `CDSE_TRAVAILLEUR_EXPIRENT_LE`), créée à part pour être révoquée seule. Elle n'est **jamais** dans le cloud-init,
    ni en base, ni dans un journal : l'API la lit dans le `.env` et la remet au travailleur avec sa tâche, par le
    réseau privé, une seule fois et sur présentation de son jeton.
* Code : `mars/sentinel.py` (sélection, garde fous, résultats, fusion, alertes), `travailleurs/sentinel.py`
  (script du travailleur), `backend/sentinel.py` (API), `backend/travailleurs.py` (remise de la tâche),
  `mars/r2_signature.py` (signature AWS version 4), `mars/travailleurs.py` (orchestrateur commun), migration 25.

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
toutes les scènes du moment partent dans **une seule session de travailleur**. Scène pas encore au catalogue : nouvel essai au
cycle suivant, pendant 12 heures. Un passage n'est jamais analysé deux fois (index unique en base, même entre la
sélection automatique et la commande manuelle).

## Garde fous

| Garde fou | Valeur (`config/rules.yaml`, section `sentinel`) |
|---|---|
| Plafond quotidien | 2 scènes par jour (les plus riches en points d'intérêt d'abord) ; au delà, refus clair dans `task_runs`. Historique : 0 versionné du 12 au 19/10/2026, 2 depuis (règles 2026.10.30) ; depuis le 21/10/2026, la pause et le plafond se règlent dans le panneau de contrôle, dont l'état en base prime sur le fichier |
| Budget mensuel | 6 € à l'origine, **8 € depuis le 20/10/2026**, partagés avec Sentinel 2 et tip and cue : coût estimé avant lancement (à l'origine 12 min fixes et 6 min par scène au tarif du L4 ; depuis le lot C3, selon le type lancé et le nombre de fenêtres) ajouté au coût engagé du mois ; au delà, refus |
| Travailleur | un seul travailleur Sentinel actif (verrou), plafonds communs (4 travailleurs et 180 min par jour), durée de vie 45 min (calculée au lancement sur processeur), délai de démarrage 10 min, destruction gardée (`docs/travailleurs.md`) |
| Plafond commun | seuls comptent les travailleurs dont une instance a été créée chez Scaleway : un lancement refusé avant (image, quota, clés) ne coûte rien et ne compte pas (les 11 et 12/10/2026, sept refus de Scaleway en 0,1 s avaient épuisé le plafond et privé la nuit VIIRS de son travailleur) |
| Place réservée à VIIRS | tant que la nuit VIIRS du jour n'a pas eu son travailleur, Sentinel laisse 1 travailleur et 60 minutes (`travailleurs.reserve_viirs`) |
| Échec de lancement | même jour, mêmes passages : pas de nouvel essai toutes les 30 minutes ; la sélection automatique attend le lendemain (`task_runs`, tâche `sentinel`, motif affiché dans l'indicateur « Satellites ») ; l'analyse en échec ne bloque pas son passage (l'index unique ignore les analyses en échec) |
| Clés Copernicus | paire des travailleurs vérifiée (expiration, puis lecture réelle de 16 octets d'une image, signature par en tête) avant toute analyse et avant tout appel à Scaleway |
| Volume bloc | enregistré en base dès la création ; instance éteinte puis supprimée (jamais `terminate`, qui laisserait le volume, facturé) ; volume supprimé ensuite, retenté chaque minute jusqu'au bout |
| Remise de la tâche | scènes et paire dédiée remises par l'API, sur le réseau privé, à la demande du travailleur (`POST /api/travailleurs/<id>/etat` avec `{"etat": "tache"}`, jeton) ; une seule fois (`mesures.tache_remise_le`, vérifiée et posée dans la même requête : 409 ensuite) ; 503 sans marquer la remise si la paire manque au `.env` ; la remise est journalisée sans les clés |

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
* Indicateur « Satellites » : dernier passage analysé, dernier travailleur (processeur ou GPU), coût du mois, dernier refus.
* `/api/metrics` : `mars_sentinel_analyses_total`, `mars_sentinel_scenes_total`, durées de téléchargement et d'analyse
  et mémoire GPU de la dernière scène, `mars_sentinel_cout_mois_euros`, `mars_sentinel_detections_total` par statut.

## Coûts (hors taxes)

Estimation du 11/10/2026, sur L4 (historique) ; coûts mesurés et budget attendu sur GP1-S : lot C5 plus bas et
`docs/travailleurs.md`, section 5.

| Poste | Estimation | Par mois |
|---|---|---|
| L4-1-24G à la minute | une session de 2 scènes : environ 24 min (démarrage, image de 5 Go, 2 × 6 min) | 0,32 € la session |
| Volume bloc de 60 Go | quelques minutes par session | négligeable |
| IPv4, réseau privé, catalogue, données Copernicus | gratuits ou quasi | 0 € |
| **Scénario sobre** | 10 à 20 sessions par mois selon les points d'intérêt | **3 à 6 €**, budget plafonné à 6 € |

La durée réelle sur GPU (démarrage d'une image GPU, téléchargement de 5 Go) n'est toujours pas mesurée, faute
d'analyse aboutie sur GPU ; sur GP1-S, 6 min 15 s de la création à la destruction (analyse 97, lot C5).

## Mise en place (une fois)

1. **Scaleway, quota GPU** : console, Organization, Quotas : L4-1-24G vaut 1 une fois l'identité du compte validée
   (Account, Verification) ; sinon 0, et le lancement échoue avec le refus de Scaleway dans `task_runs`.
2. **Scaleway, droits** : à la politique de l'application `mars-c2-travailleurs` (portée Project), ajouter
   **BlockStorageFullAccess** (suppression des volumes bloc des instances GPU) aux deux ensembles existants.
3. **Réseau privé** : rien à faire, il est régional ; le même `SCW_PRIVATE_NETWORK_ID` sert en fr-par-2.
4. **Copernicus Data Space** : compte gratuit sur https://dataspace.copernicus.eu, puis clés S3 sur
   https://eodata-s3keysmanager.dataspace.copernicus.eu (Add Credentials ; date d'expiration au choix, la clé secrète
   n'est montrée qu'une fois). **Deux paires** : l'une pour le serveur (`CDSE_ACCESS_KEY`, `CDSE_SECRET_KEY`), l'autre
   dédiée aux travailleurs (`CDSE_TRAVAILLEUR_ACCESS_KEY`, `CDSE_TRAVAILLEUR_SECRET_KEY`), avec sa date d'expiration
   dans `CDSE_TRAVAILLEUR_EXPIRENT_LE=AAAA-MM-JJ`. Le `.env` est lu par l'API (qui remet la paire dédiée) et par
   `taches` : redémarrer les deux après un changement de clés.
5. Migration 25, images, puis le premier essai réel ci dessous.

## Image GPU sur volume bloc (corrigé le 12/10/2026, confirmé le 13/10/2026)

Les lancements 10 à 16 ont échoué avant toute création : « image e6d1b7f4 (ubuntu_noble_gpu_os_13_nvidia) introuvable
ou sans volume racine ». Le catalogue public trouve bien l'image `e6d1b7f4` (type `instance_sbs`, compatible
L4-1-24G, fr-par-2). L'échec venait de la lecture de sa fiche dans l'API Instances, que la vérification écrite pour les
DEV1-M (disque local) juge selon quatre conditions confondues dans un seul message. Désormais :
* un message par cause (introuvable, architecture, pas de volume racine, taille inconnue) ;
* pour le stockage bloc : volume racine de type `sbs_snapshot` accepté, et si la fiche n'en donne pas la taille
  (taille nulle : **confirmé par le diagnostic du 13/10/2026**, fiche brute avec `sbs_snapshot` de taille 0), elle est
  lue dans l'API Block Storage (20 Go pour l'image GPU)
  (`/block/v1alpha1/zones/<zone>/snapshots/<id>`), comme le fait la ligne de commande Scaleway ;
* la vérification des DEV1-M est inchangée (disque local, taille dans la fiche), testée.
Le chemin Block Storage `v1alpha1` (celui de la ligne de commande ; une `v1` existe aussi) répond. Reste à voir au
premier essai : le nom du quota GPU dans l'API IAM (lisible ou non avec la clé du serveur).

**Premier essai réel, 14/10/2026, passage 110 (travailleur 19)** : instance L4 créée, puis refusée à la relecture
(« volume de démarrage de 0 Go, plus petit que l'image (20 Go) »). Même cause que pour l'image : pour un volume bloc
(`sbs_volume`), l'API Instances renvoie `server.volumes["0"].size` à 0, la taille étant gérée par l'API Block Storage.
Désormais, la vérification d'après création (`boot_problem`) lit la taille du volume bloc dans
`/block/v1alpha1/zones/<zone>/volumes/<id>` ; si elle reste illisible, elle ne refuse pas sur la taille seule (image,
démarrage local, présence et type du volume restent contrôlés). Les volumes locaux des DEV1-M gardent le contrôle
d'origine. Testé avec la forme réelle des réponses (`tests/test_sentinel.py`). Coût de l'essai : 0,013 € (minimum
facturé). Destruction : travailleur marqué détruit 0,4 s après l'échec, ce qui n'arrive que si Scaleway a accepté la
suppression de l'instance et de chaque volume bloc (ou les dit déjà absents) ; le journal de l'essai ne nommait pas
les volumes : il les nomme désormais (`volumes_bloc_supprimes`), et le diagnostic relit chaque volume enregistré dans
l'API Block Storage pour le confirmer.

## Copernicus refuse les adresses présignées (constat du 14/10/2026)

Conception initiale : le serveur signait des adresses de lecture valables 3 heures (signature dans la requête,
`X-Amz-Signature`), et le travailleur n'avait aucune clé. Test sur le serveur, le 14/10/2026, même objet de la scène
`S1D_..._005015_0097A4_7746_COG`, mêmes clés :

```python
s3 = boto3.client("s3", endpoint_url="https://eodata.dataspace.copernicus.eu", region_name="default",
                  aws_access_key_id=CLE, aws_secret_access_key=SECRET)
s3.get_object(Bucket="eodata", Key=CLE_OBJET, Range="bytes=0-15")            # 1. en tête : lecture OK
requests.get(s3.generate_presigned_url("get_object", Params={"Bucket": "eodata", "Key": CLE_OBJET}))  # 2. 403 InvalidAccessKeyId
requests.get(signed_urls(...)[0])                                            # 3. MARS C2 : 403 InvalidAccessKeyId
```

Les clés et notre signature sont bonnes (la signature de MARS C2 est identique à celle de botocore, testé) :
`eodata.dataspace.copernicus.eu` n'accepte que l'authentification par en tête. Le principe « adresses signées par le
serveur » est abandonné : le travailleur reçoit une paire de clés dédiée et signe lui même chaque lecture par en tête
(bibliothèque standard, Python 3.8 de l'image d'AI2 ; GDAL ou rasterio y parviendraient aussi avec `AWS_S3_ENDPOINT`,
`AWS_VIRTUAL_HOSTING=FALSE`, `AWS_HTTPS=YES`, `AWS_REGION=default`). Garde fous :
* paire distincte de celle du serveur, révocable seule, expiration surveillée par le diagnostic ;
* jamais dans le cloud-init (le fichier de tâche n'y contient que l'adresse de retour, le jeton, le script et
  `tache_par_le_reseau`), ni en base (`travailleurs.parametres` garde les scènes, sans clé), ni dans un journal ;
* remise par l'API, seul service joignable par le réseau privé, une fois et au seul travailleur qui présente son
  jeton ; nginx (port 8090) relaie déjà la route `etat`, l'image web ne change donc pas ;
* les clés restent en mémoire dans le script du travailleur, qui est détruit à la fin.
Couvert par `tests/test_cles_travailleur.py`.

## Diagnostic avant tout essai (lecture seule)

```bash
docker compose exec taches python scripts/taches.py sentinel-diagnostic
```

Aucune création, aucun coût : des lectures sur les API Scaleway et 16 octets d'une image Copernicus. Une ligne par
point, dans l'ordre du lancement : les deux paires de clés Copernicus (présentes, valides par une vraie lecture de
16 octets signée par en tête, expiration), clés Scaleway et
réseau privé dans le `.env`, API Instances en fr-par-2, **image GPU résolue avec sa fiche brute affichée**, disponibilité
annoncée du L4-1-24G en fr-par-2, quota du L4 (API IAM ; « à vérifier » si la clé limitée au projet ne peut pas le lire),
stockage bloc (lecture), ensembles de droits de la politique de la clé (si lisibles), réseau privé (régional, donc
rattachable depuis fr-par-2), puis lecture d'un volume bloc par l'API Block Storage (taille) et relecture des volumes
bloc enregistrés pour les travailleurs Sentinel des 30 derniers jours (absents : supprimés ; encore présents : facturés,
MANQUE). Chaque ligne dit PRÊT, MANQUE (à corriger) ou À VÉRIFIER (illisible avec cette clé : à
contrôler dans la console) ; le plafond de scènes est donné pour information. La disponibilité du type d'instance est
une réponse paginée (100 types par page, les GPU au delà de la première) : toutes les pages sont lues, et la réponse
brute s'affiche si le type reste introuvable. Expiration des clés Copernicus : illisible avec les seules clés S3 (le
gestionnaire de clés demande une connexion au compte) ; la reporter dans le `.env` à la création
(`CDSE_TRAVAILLEUR_EXPIRENT_LE`, et `CDSE_CLES_EXPIRENT_LE` pour la paire du serveur) pour que le diagnostic la compare
à la date du jour (moins de 14 jours : à vérifier ; dépassée : manque ; non renseignée : à vérifier pour la paire des
travailleurs, qui est surveillée, simple information pour celle du serveur). Une paire des travailleurs expirée
arrête aussi tout lancement, avant Scaleway. Un refus de Copernicus donne son code S3 (`InvalidAccessKeyId`, `SignatureDoesNotMatch`…). Code de sortie non nul s'il manque quelque
chose.

## Second essai réel (14/10/2026, travailleur 20, passage 110, analyse 94)

Tout a fonctionné jusqu'à l'analyse : instance L4 démarrée, réseau privé de fr-par-2 vers fr-par-1 joint, tâche et
clés remises, image d'AI2 téléchargée en 113 s, GPU vu (NVIDIA L4, 22 563 Mo, CUDA 11.7, torch 1.13.1+cu117),
préparation en 1,9 s, mémoire 1 437 Mo. Échec à l'import de la chaîne d'AI2 : « No module named 'src' ». Le code
d'AI2 importe tout en `src.*` (`src.data.retrieve`, `src.models`…) ; le script mettait `/home/vessel_detection/src`
sur `sys.path` et importait `data.image`. Corrigé : racine `/home/vessel_detection` sur `sys.path`, imports en
`src.*`, faits dès la vérification préalable (un import cassé arrête le travailleur avant tout téléchargement).

Le script est désormais éprouvé dans la vraie image épinglée, sur le Mac, en linux/amd64, sans GPU
(`travailleurs/essai_ai2.py`, par `scripts/verifier_images.sh ai2` ou `ai2-complet`). Le premier essai complet a
révélé un second défaut : `nms_thresh`, annoncé décimal, sert à une division entière dans la grille d'AI2 (« 'float'
object cannot be interpreted as an integer ») ; il est passé en entier. Analyse complète d'une scène synthétique de
2 560 pixels de côté (mer de l'Iroise, quatre navires injectés, images VV et VH en entiers 16 bits avec points
d'appui) : 4 cibles sur 4 retrouvées à 3, 8, 20 et 22 m, scores de 0,89 à 0,96, vignettes de 18 Ko, 296 s et
2,8 Go de mémoire sur CPU émulé. L'ancien script, dans le même essai, échoue avec l'erreur exacte du travailleur 20.

Le travailleur avait fini en « code 0 » et l'exécution était comptée « terminée » alors que son unique scène avait
échoué : quand aucune scène n'aboutit, le script déclare l'erreur (« aucune scène analysée sur N : motif »), et le
serveur classe de lui même un tel résultat en échec (`failure`, pour un script plus ancien) ; le motif apparaît dans
l'indicateur « Satellites ».

## Premier essai réel (passage choisi à la main)

Procédure du 14/10/2026, historique : le plafond n'est plus relevé dans le fichier (une modification locale bloque
`git pull`) ; un essai passe par `SENTINEL_PLAFOND_ESSAI=1` et, en pause, `--malgre-pause` (`docs/controle.md`).

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

## Lot C3 : Sentinel 1 robuste (19/10/2026)

État réel avant ce lot : essai 19 (14/10) refusé par notre contrôle du volume bloc, corrigé ; essai 20 (14/10) :
instance L4 démarrée, réseau privé de fr-par-2 vers fr-par-1 joint, tâche remise, GPU vu (NVIDIA L4, CUDA 11.7,
torch 1.13.1), échec à l'import de la chaîne d'AI2, corrigé et éprouvé sur CPU dans la vraie image ; l'analyse 94,
comptée « terminée » sans aucune scène aboutie, est reclassée en échec par la migration 28 (toute analyse « terminée »
sans détection dont aucune scène n'a abouti) ; essai du 16/10 (passage 143) : 412 out_of_stock au démarrage du
L4-1-24G, instance et volume créés pour rien.

### Rupture de stock des GPU

* **Disponibilité lue avant toute création** (API Instances, `products/servers/availability`, lecture seule) : un type
  « shortage » n'est jamais essayé, rien n'est créé. Le 19/10/2026 à midi, le L4-1-24G était « shortage » en fr-par-1
  et en fr-par-2 : le stock varie d'heure en heure.
* **out_of_stock à la création ou au démarrage** (« scarce » ne garantit rien) : instance et volume bloc supprimés
  aussitôt par la garde habituelle, travailleur marqué `rupture_stock` (hors du plafond quotidien, coût nul, journal
  marqué refus : pas d'alerte « deux échecs de suite »), type suivant essayé dans le même cycle.
* **Aucun type lançable** : l'analyse passe en **« attente_gpu »** (pas en échec), avec son motif (« GPU indisponible
  chez Scaleway (L4-1-24G fr-par-2 : épuisé ; …) ») et l'heure du prochain essai ; nouvel essai à chaque cycle
  (30 minutes) tant que la scène reste utile (`sentinel.fenetre_utile_h`, 24 h après l'acquisition). Au delà :
  analyse abandonnée, en échec, et **alerte ntfy** (règle « Analyse Sentinel 1 abandonnée faute de GPU », une fois).
  Une simple rupture n'alerte pas.
* Indicateur « Satellites » de la barre d'état et tuile Satellites de Grafana : orange, « GPU indisponible chez
  Scaleway, nouvel essai dans N min ».

### Types de secours (catalogue public de Scaleway, 19/10/2026)

Exigences du modèle d'AI2 : PyTorch 1.13.1 compilé pour CUDA 11.7 (architectures sm_37 à sm_86, plus le PTX pour les
plus récentes : le L4, sm_89, l'a confirmé à l'essai 20), 16 Go de mémoire GPU suffisent (réglage d'AI2 sur un T4 de
16 Go). Région fr-par seulement : le réseau privé qui ramène les résultats est régional.

| Ordre | Type | Zone | GPU | Prix | Disponibilité le 19/10 | Raison |
|---|---|---|---|---|---|---|
| 1 | L4-1-24G | fr-par-2 | L4 24 Go | 0,79 €/h | shortage | éprouvé (essai 20) |
| 2 | L4-1-24G | fr-par-1 | L4 24 Go | 0,79 €/h | shortage | même GPU, autre stock |
| 3 | L40S-1-48G | fr-par-2 | L40S 48 Go | 1,47 €/h | shortage | Ada comme le L4 (même chemin CUDA) |
| ~~4~~ | ~~RENDER-S~~ | fr-par-2 | P100 16 Go | 1,22 €/h | available | **retiré le 19/10/2026 (lot C4)** : voir ci dessous |
| repli | GP1-S | fr-par-2 | aucun (8 processeurs, 32 Go) | 0,19 €/h | available | voir ci dessous |

**RENDER-S retiré** après l'essai réel du 19/10/2026 (passage 149, analyse 96, travailleur 26, trace
8a5f1602947453bf4ee1fe037e80f836) : démarrage en 1 min 14 s, réseau privé, Docker et image d'AI2 réussis, puis
`docker run --gpus` en échec en 5 s, code 125, « failed to initialize NVML: Driver Not Loaded ». L'image « Ubuntu Noble
GPU OS 13 » charge les modules noyau ouverts de NVIDIA, qui ne gèrent que Turing et plus récent : aucun Pascal ne peut
servir. Non remplacé. Ordre restant : L4 fr-par-2, L4 fr-par-1, L40S fr-par-2, puis repli GP1-S.

Écartés : H100 (Hopper, sm_90, au delà de CUDA 11.7, 2,87 €/h), L4 et H100 de pl-waw-2 (hors de la région du réseau
privé), multi GPU (une scène à la fois), GPU-3070 (absent du catalogue). `taches.py sentinel-diagnostic` affiche la
disponibilité et le prix du catalogue de chaque type, et signale un prix de `config/rules.yaml` à mettre à jour.

### Analyse limitée aux fenêtres utiles

* **Serveur** (`mars/sentinel.py`, `fenetres`) : autour de chaque point d'intérêt couvert par la scène, un carré de
  2 × (10 km + 2 km de marge) de côté ; les carrés qui se chevauchent sont réunis. Enregistrées dans l'analyse
  (`analyses.fenetres`), remises au travailleur avec sa tâche. Un passage choisi à la main sans corridor prend pour
  points les navires des listes de son emprise (`points_liste`) ; sans aucun navire, scène entière (GPU seulement).
* **Travailleur** (`travailleurs/sentinel.py`, `analyse_fenetres`) : GDAL ouvre l'image COG par `/vsis3/` (clés en
  mémoire du processus, jamais écrites), calcule la fenêtre en pixels par les **points d'appui** de la scène (les GRD
  ne sont pas géoréférencés par une transformation affine), puis la découpe par `gdal.Translate` : seules les tuiles
  utiles sont lues (requêtes partielles par plages d'octets, signées par en tête, AWS version 4), et la copie garde
  ses points d'appui recalés sur la fenêtre. La chaîne d'AI2 (`prepare_scenes` reprojette par ces points d'appui)
  analyse chaque fenêtre ; les détections à moins de la marge du bord sont écartées.
* **Chaîne d'AI2 et images partielles** : elle découpe en carreaux de 2 048 pixels Web Mercator, et une image plus
  petite dans une dimension donne des décalages négatifs (découpe fausse). Une fenêtre fait donc au moins
  2 300 pixels de côté (agrandie dans l'image au besoin) ; près du bord d'une scène trop petite, motif clair.
* **Essais** (`scripts/verifier_images.sh fenetres` et `ai2-fenetres`, vraie image d'AI2, GDAL 3.4.3) :
  * faux S3 qui vérifie la signature AWS version 4 de chaque requête de GDAL (région « default », adressage par
    chemin, comme Copernicus) : 8 requêtes signées, aucune refusée ; signature fausse refusée ;
  * scène synthétique de 12 000 × 8 000 pixels (113 Mo par polarisation), fenêtre de 24 km : **13,1 Mo lus (11,6 %)**,
    l'index du TIFF en annonçait 12,9 ; points d'appui exacts (écart nul entre scène et fenêtre) ;
  * chaîne d'AI2 complète sur processeur (émulation) : scène entière de 3 200 × 3 200 pixels, 4 détections en 855 s,
    2,8 Go de mémoire ; fenêtre de 2 300 × 2 300 pixels, **les 2 détections de son cœur retrouvées à 6,3 m près**
    (moins d'un pixel), en 470 s.
* **Projection sur une vraie scène** (non mesurée faute de clés Copernicus sur le Mac) : un GRD fait environ
  25 000 × 16 700 pixels (0,4 à 0,9 Go par polarisation) ; une fenêtre de 24 km en couvre environ 1,4 %, soit de
  l'ordre de 15 à 25 Mo par polarisation au lieu de la scène entière. Mesuré depuis sur une vraie scène (analyse 97,
  19/10/2026) : 20,0 Mo lus sur 1 194 Mo (1,7 %).

### Repli sur processeur

Après `repli_cpu_apres_h` (3 h) sans GPU de la liste, les fenêtres seules sont analysées sur un GP1-S : 8 processeurs
et 32 Go (le modèle et une fenêtre tiennent en moins de 3 Go mesurés), disque local (pas de volume bloc à gérer),
0,19 €/h facturés à l'heure, « available » en fr-par-1 et fr-par-2 le 19/10/2026 quand le POP2-8C-32G (0,29 €/h,
volume bloc) était « scarce ». Une scène entière n'est jamais envoyée sur processeur. Le choix (GPU ou processeur),
le type et la raison sont enregistrés dans l'analyse (`calcul`, `calcul_type`, `calcul_motif`) et dans la trace
(`sentinel.choix_calcul`, `sentinel.essai_type`). Durée : 470 s par fenêtre de 2 300 pixels sur processeur en
émulation (borne haute) : estimation de 12 min par fenêtre (`minutes_par_fenetre_cpu`), 10 min fixes, durée de vie
du travailleur portée à 150 min pour ce type (45 pour les autres).

### Budget et garde fous

Coût estimé selon le type réellement lancé et le nombre de fenêtres : GPU, 12 min fixes et 2 min par fenêtre au tarif
du type (6 min par scène entière) ; processeur, une heure entamée de GP1-S (0,19 €) pour une à quatre fenêtres.
Plafond de scènes et budget mensuel inchangés. Grafana (tableau Satellites, section « Analyses Sentinel 1 ») :
analyses en attente d'un GPU, octets lus du mois, analyses sur processeur, abandons, analyses par mode (GPU,
processeur, attente) et table des analyses avec leur trace.

### Essai réel du lot C3

Procédure du 19/10/2026, historique : le plafond du fichier vaut 2 depuis (règles 2026.10.30), et la pause se règle
dans le panneau de contrôle ; une commande manuelle en pause demande `--malgre-pause`.

1. **Diagnostic**, lecture seule : `docker compose exec taches python scripts/taches.py sentinel-diagnostic`. Aucune
   ligne MANQUE ; lire la disponibilité de chaque type (une rupture n'est pas un manque).
2. **Choisir un passage** récent qui couvre un navire des listes :
   `docker compose exec taches python scripts/taches.py sentinel-candidats` (passages des 36 dernières heures : points,
   scènes, fenêtres, octets estimés, coût sur L4). Retenir un passage avec une ou deux fenêtres.
3. **Plafond relevé à 1 pour cette seule commande**, sur le serveur, sans toucher à `config/rules.yaml` (la boucle
   garde 0) :
   `docker compose exec -e SENTINEL_PLAFOND_ESSAI=1 taches python scripts/taches.py sentinel-analyser <passage>`.
   Réponse attendue : `calcul`, `type`, `motif`, `cout_estime_eur`, ou `attente_gpu` avec le motif (rupture : la
   boucle réessaiera chaque 30 minutes, avec le plafond versionné de 0 pour les passages nouveaux ; l'analyse déjà
   créée est reprise).
4. **Suivre par la trace** : Grafana, Vue d'ensemble, « Dernières traces de bout en bout », colonne Trace (ou
   `taches.py travailleurs`, qui affiche l'identifiant). La trace doit montrer, dans l'ordre :
   `commande sentinel-analyser` › `tache sentinel` › `sentinel.attente` › `sentinel.selection` › `sentinel.fenetres`
   (nombre et surface) › `sentinel.verification_cles` › `sentinel.choix_calcul` (une ligne `dispo.<type zone>` par
   type) › `sentinel.disponibilite` (une par zone) › `sentinel.essai_type` (un par type essayé ; rupture marquée) ›
   `travailleur.lancement` et ses `scaleway.*` ; puis sur le travailleur `travailleur.demarrage` (étapes bash) ›
   `travailleur.sentinel` › `travailleur.remise_tache` › `sentinel.preparation_gpu` › `sentinel.scene` ›
   `sentinel.fenetre` (une par fenêtre) › `GET S3 eodata VV fenêtre` et `VH` (octets) › `sentinel.preparation_scene` ›
   `sentinel.inference` › `sentinel.mesures_et_vignettes` › `travailleur.renvoi_resultats` ; enfin sur le serveur
   `travailleur.resultats` › `sentinel.fusion_et_alertes` › `sentinel.vignettes` › `travailleur.destruction`.
5. **Console Scaleway** : Instances (fr-par-1 et fr-par-2) et Block Storage : aucune instance `mars-travailleur-*` ni
   aucun volume restant ; `taches.py sentinel-diagnostic` le vérifie aussi (volumes bloc enregistrés).
6. **Plafond** : rien à remettre, il n'a été relevé que pour la commande. Vérifier `scenes_max_par_jour: 0` dans
   `config/rules.yaml` (`git status` propre).
7. Relever pour ajuster les estimations : `analyses.octets_lus`, `timings` (lecture et analyse par fenêtre, mémoire
   GPU), durée et coût dans `task_runs` (`travailleur_sentinel`).

## Lot C4 : GPU inutilisable, échec signalé, essai forcé sur processeur (19/10/2026)

* **Contrôle du GPU avant l'analyse** (`travailleurs/demarrage.sh`, étape `controle_gpu`, avant même le
  téléchargement de l'image de 5 Go) : `nvidia-smi` donne le nom du GPU, le pilote, la mémoire et la version de CUDA,
  dans le journal et en attributs du span (`gpu.nom`, `gpu.pilote`, `gpu.memoire`, `gpu.cuda`). Échec ou aucun GPU :
  journal envoyé, état **« gpu_inutilisable »** signalé au serveur avec le message (dernières lignes utiles, `dmesg`
  nvidia), span en erreur, arrêt sans `docker run`. Filet : `docker run` qui sort en 125 avec NVML ou CDI dans sa sortie
  est traité de même.
* **Serveur** : un GPU inutilisable est un problème de type, pas d'analyse. Instance et volume détruits à la minute
  même ; analyse remise en « attente_gpu », reprise au cycle suivant avec le type suivant (pas d'échec, pas de nouvelle
  analyse, plafond de scènes non entamé une seconde fois) ; coût réel du travailleur compté dans le budget du mois ;
  type et zone **marqués incompatibles 7 jours** (`types_incompatibles`, migration 29 : date, message, travailleur,
  trace), sautés par le choix du calcul (attribut `incompatible.<type zone>` de `sentinel.choix_calcul`) ; une alerte
  ntfy par marquage (règle « GPU marqué incompatible »). `sentinel-diagnostic` affiche les marquages actifs, leur
  cause et leur date de fin ; levée à la main :
  `docker compose exec taches python scripts/taches.py sentinel-lever-incompatible L40S-1-48G fr-par-2`.
* **Échec signalé tout de suite** (constat de l'essai 26 : rien n'était signalé, le serveur attendait 45 min, instance
  « stopped in place » facturée environ 30 min pour rien) : toute sortie en échec du script de démarrage envoie l'état
  **« echec »** avec l'étape et le motif (code de sortie, dernière ligne d'erreur utile) avant l'arrêt différé. Le
  serveur passe le travailleur en échec, le détruit à la minute suivante, ferme la trace par un span
  `travailleur.echec` (motif reçu), et : analyse remise en attente si la machine est en cause (Docker, pilote, image :
  étapes `installation_docker`, `telechargement_image`, `analyse_docker` pour les codes 125 à 127), analyse en échec si
  c'est l'analyse elle même (étape `analyse`). Filet : une instance « stopped in place » ou « stopped » d'un
  travailleur encore « en cours » est traitée comme un échec de la machine et détruite aussitôt.
* **Sondages regroupés** : l'attente du démarrage d'une instance et celle de la carte du réseau privé sont un seul
  span chacune (`scaleway.attente_demarrage`, `scaleway.attente_reseau_prive` : nombre de sondages, durée, état final,
  dernier état intermédiaire) ; création, action de démarrage et suppressions gardent leur span.
* **Analyse 96** : remise en échec avec sa cause par la migration 29, ce qui libère le passage 149 pour une nouvelle
  analyse lancée à la main ; le travailleur 26 reste dans l'historique, en échec, avec la cause NVML ; le RENDER-S y est
  marqué incompatible à la date de l'essai (sans alerte).

### Essai forcé sur processeur (passage 149)

`SENTINEL_CALCUL_ESSAI=cpu`, sur le modèle de `SENTINEL_PLAFOND_ESSAI` : pour la seule commande manuelle
`sentinel-analyser`, le repli GP1-S est lancé tout de suite (fenêtres seules, jamais une scène entière : refus clair si
le passage n'a aucune fenêtre), sans attendre `repli_cpu_apres_h`. La boucle automatique et `config/rules.yaml` ne sont
pas touchés. Motif enregistré dans l'analyse et dans la trace : « essai forcé sur processeur ».

1. `docker compose exec taches python scripts/taches.py sentinel-diagnostic` : aucune ligne MANQUE ; GP1-S en fr-par-2
   « available ».
2. `docker compose exec taches python scripts/taches.py sentinel-candidats 72` : le passage 149 et ses fenêtres
   (navires des listes de l'emprise). Le passage est acquis depuis plus de 24 h : la fenêtre utile ne concerne que les
   reprises automatiques, la commande manuelle analyse quand même.
3. Lancer :
   `docker compose exec -e SENTINEL_PLAFOND_ESSAI=1 -e SENTINEL_CALCUL_ESSAI=cpu taches python scripts/taches.py sentinel-analyser 149`
   Réponse attendue : `calcul: cpu`, `type: GP1-S`, `motif: essai forcé sur processeur …`, `cout_estime_eur` de 0,19
   (une heure entamée de GP1-S pour une à quatre fenêtres).
4. Durée attendue : création et démarrage 1 à 2 min (disque local, aucun volume bloc), installation de Docker 1 à
   2 min, image d'AI2 de 5 Go 3 à 6 min, puis environ 8 à 12 min par fenêtre sur 8 processeurs (470 s par fenêtre de
   2 300 pixels mesurés en émulation sur le Mac, borne haute) : **20 à 35 min pour une ou deux fenêtres**, sous la
   durée de vie de 150 min du type. Coût : **0,19 € HT** (facturé à l'heure entamée).
5. Spans attendus, dans l'ordre (Grafana, Vue d'ensemble, « Dernières traces de bout en bout », colonne Trace) :
   `commande sentinel-analyser` › `tache sentinel` (attribut `sentinel.calcul_force: cpu`) › `sentinel.attente` ›
   `sentinel.selection` › `sentinel.fenetres` › `sentinel.verification_cles` › `sentinel.choix_calcul`
   (`sentinel.calcul_force: cpu`, `dispo.GP1-S fr-par-2`, `sentinel.motif: essai forcé sur processeur…`) ›
   `sentinel.disponibilite` › `sentinel.essai_type` (GP1-S, cpu) › `travailleur.lancement` › `scaleway.image` ›
   `scaleway.creation` (POST) › `scaleway.verification_demarrage` › `scaleway.reseau_prive` (POST, puis
   `scaleway.attente_reseau_prive`) › `scaleway.cloud_init` › `scaleway.demarrage` (POST action, puis
   `scaleway.attente_demarrage`) ; sur le travailleur : `travailleur.demarrage` › `demarrage.interface_privee` ›
   `demarrage.contact_serveur` › `demarrage.installation_docker` › `demarrage.telechargement_image` ›
   `demarrage.analyse` › `travailleur.sentinel` › `travailleur.remise_tache` › `sentinel.preparation_gpu`
   (`sentinel.gpu` vide : processeur) › `sentinel.scene` › `sentinel.fenetre` › `GET S3 eodata VV fenêtre` et `VH` ›
   `sentinel.preparation_scene` › `sentinel.inference` › `sentinel.mesures_et_vignettes` ›
   `travailleur.renvoi_resultats` ; enfin sur le serveur `travailleur.resultats` › `sentinel.fusion_et_alertes` ›
   `sentinel.vignettes` › `travailleur.destruction`. Pas d'étape `demarrage.controle_gpu` (aucun GPU demandé).
6. Console Scaleway : aucune instance `mars-travailleur-*` restante (GP1-S : disque local, pas de volume bloc).
7. Relever : `analyses.octets_lus` et `timings` (durée par fenêtre sur processeur, pour ajuster
   `minutes_par_fenetre_cpu`), coût et durée dans `task_runs` (`travailleur_sentinel`).

## Lot C5 : premier essai réel abouti, sélection en mer, processeur en premier (19/10/2026)

### Mesures de l'essai (passage 149, analyse 97, travailleur 27, GP1-S forcé sur processeur)

Trace `83549be17f1b3ca51383ee94ab973fd6`.

| Mesure | Valeur |
|---|---|
| Octets lus | 20,0 Mo sur 1 194 Mo de la scène (1,7 %) |
| Lecture de la fenêtre | 1,1 s |
| Inférence | 119 s pour une fenêtre de 2 770 × 2 740 pixels, sur 8 processeurs |
| Démarrage | Docker 30 s, image d'analyse 1 min 39 s |
| Durée totale | 6 min 15 s de la création à la destruction |
| Coût | 0,19 € HT (une heure entamée de GP1-S) |
| Écart de longueur | MSC MERAVIGLIA (détection 9502) : 324 × 52 m au radar pour environ 315 × 43 m réels, soit 9 m (3 %) de plus en longueur |

La seconde détection était masquée « terre ». Le navire des listes qui avait déclenché le passage, VICTOR ANDRYUKHIN
(4058), était à quai dans les bassins nord du Grand Port de Marseille : invisible au radar, l'analyse ne pouvait rien
en dire (d'où la sélection en mer ci dessous). L'analyse portait `flux_coupe: true` à cause du trou de `stats_minute`
du 17/10 22:46 au 19/10 06:00 (`docs/supervision.md`, « Statistiques de la frise ») : après reconstruction,
`taches.py sentinel-flux 97` recalcule le drapeau.

**Recalibration** (règles 2026.10.29) : `minutes_par_fenetre_cpu` de 12 à 3 (119 s mesurés, plus marge),
`minutes_fixes_cpu` de 10 à 5. La durée de vie d'un travailleur sur processeur n'est plus fixe (150 min) : elle est
calculée au lancement, `minutes_fixes_cpu + 3 min par fenêtre + cpu_marge_min` (15), soit 23 min pour une fenêtre et
32 min pour quatre, enregistrée dans `travailleurs.duree_max_min` et appliquée par la surveillance.

### Sélection : point de déclenchement en mer seulement

Un point « navire des listes » (dans un corridor ou dans l'emprise) n'est retenu qu'à plus de `point_cote_min_km`
(2 km) de la côte, masque des terres existant, ou au mouillage hors port (zone de mouillage connue à
`point_mouillage_m` près, 1 km, et à `point_mouillage_cote_min_m`, 800 m, de la côte au moins). Sinon il est écarté
avec le motif « à quai, invisible au radar ». Un passage sans point retenu n'est pas analysé ; à la main, la commande
refuse avec le nom des navires écartés. `sentinel-candidats` affiche les points écartés, leur distance à la côte et
leur motif. Les points d'alerte VIIRS ne sont pas filtrés. Essais : bassins nord de Marseille à 92 m de la côte et
port de Brest à 281 m écartés ; point au large et mouillage de la rade de Brest à 1,8 km de la côte gardés.

### Choix du calcul : processeur en premier

| Passage | Calcul | Motif enregistré (analyse et trace) |
|---|---|---|
| 4 fenêtres au plus (`fenetres_max_cpu`), aucune scène entière | GP1-S d'emblée, POP2-8C-32G si le GP1-S est en rupture, sinon attente | « petit passage : processeur » |
| Au delà, ou scène entière | GPU dans l'ordre (L4 fr-par-2, L4 fr-par-1, L40S) | « grand passage : GPU » |
| Grand passage, GPU en rupture depuis `repli_cpu_apres_h` (3 h) | GPU d'abord, puis processeur par lots de 4 fenêtres | « GPU en rupture : processeur par lots » |
| `SENTINEL_CALCUL_ESSAI=cpu` ou `gpu` (commande manuelle) | ce calcul seulement | « essai forcé sur processeur » ou « essai forcé sur GPU » |

Lots : chaque fenêtre porte un identifiant ; le travailleur ne reçoit que les fenêtres pas encore faites
(`analyses.fenetres_faites`). Après un lot, l'analyse repart en « attente_gpu » et le lot suivant part au cycle
suivant (30 min) ; la fusion avec l'AIS et les alertes n'ont lieu qu'une fois toutes les fenêtres analysées (sinon
les échos fixes seraient comptés à chaque lot). `SENTINEL_CALCUL_ESSAI=gpu` permet d'éprouver le L4 quand il revient,
sans repli sur processeur.

### Facturation et garde de dépense (point 9)

L'essai 26 (RENDER-S) a été facturé 1,23 € par Scaleway pour 0,28 € estimés : le RENDER-S, comme les GP1 et les POP2,
est facturé à l'heure entamée ; le L4 et le L40S à la minute. `config/rules.yaml` porte désormais le mode de
facturation de chaque type (`facturation: minute` ou `heure_entamee`, défaut `heure_entamee`) ; l'estimation, le
budget du mois, le coût réel et Grafana l'utilisent.

| Type | Zone | Prix HT | Facturation | Coût d'un échec au démarrage (5 min) |
|---|---|---|---|---|
| L4-1-24G | fr-par-2, fr-par-1 | 0,7875 €/h | à la minute | 0,07 € |
| L40S-1-48G | fr-par-2 | 1,4699 €/h | à la minute | 0,12 € |
| GP1-S | fr-par-2 | 0,1907 €/h | heure entamée | 0,19 € |
| POP2-8C-32G | fr-par-2 | 0,29 €/h | heure entamée | 0,29 € |
| RENDER-S (retiré) | fr-par-2 | 1,221 €/h | heure entamée | 1,22 € (1,23 € facturés à l'essai 26) |
| DEV1-M (VIIRS) | fr-par-1 | 0,0202 €/h | heure entamée | 0,02 € |

Coût réel de chaque travailleur, calculé à sa destruction (création à destruction, selon le mode du type) :
`travailleurs.cout_reel_eur` et `task_runs` (`travailleur_sentinel`), à côté du coût prévu au lancement
(`travailleurs.cout_prevu_eur`) ; span `travailleur.cout` avec l'écart. Le travailleur 26 porte les 1,23 € facturés
(migration 30). Garde : un type facturé à l'heure entamée et plus cher que `prix_max_essai_eur` (0,50 €/h) n'est
jamais lancé tant qu'aucun travailleur n'a abouti sur lui ; il faut alors `SENTINEL_CALCUL_ESSAI` explicite. Aucun type
de la liste actuelle n'est concerné ; le RENDER-S l'aurait été.

### Sentinel 1 rallumé en automatique (19/10/2026, règles 2026.10.30)

Preuves du lot C5 faites sur le serveur (statistiques reconstruites, `flux_coupe` de l'analyse 97 à false, sélection
à quai active) : `scenes_max_par_jour` passe de 0 à 2, budget de 6 € par mois inchangé (porté à 8 € le 20/10/2026).
Remettre 0 arrête tout, boucle et commande manuelle (refus clair, aucun appel à Scaleway) ; `config/rules.yaml` est
relu à chaque cycle. **Depuis le 21/10/2026, Sentinel 1 est en pause par le panneau de contrôle** (état en base, qui
prime sur le fichier) : c'est là qu'il se reprend, sans toucher au fichier.

### Budget mensuel attendu

Hypothèse : 2 scènes par jour au plus (`scenes_max_par_jour: 2`), 1 à 2 fenêtres en moyenne, donc presque toujours
un petit passage sur GP1-S. Une session dure 9 à 11 min (5 min fixes et 3 min par fenêtre) mais coûte une heure
entamée, 0,19 €. Les deux scènes d'un même passage partent dans une seule session.

| Sessions | Coût par jour | Coût par mois (30 jours) |
|---|---|---|
| 1 par jour (les deux scènes dans le même passage) | 0,19 € | 5,7 € |
| 2 par jour (deux passages distincts) | 0,38 € | 11,4 € |
| Pour comparaison, 1 par jour sur L4 (12 + 2 × 1,5 min) | 0,20 € | 5,9 € |

Le processeur ne coûte pas moins que le L4 par session (l'heure entamée efface l'avantage de prix) : il est choisi
pour sa disponibilité (L4 et L40S en rupture toute la journée du 19/10, GP1-S toujours disponible). **6 € par mois
suffisent pour une session par jour en moyenne (31 sessions)** ; au delà, la garde du budget refuse proprement les
sessions de fin de mois. Pour deux passages par jour tout le mois, il faut 12 €.

## Limites

* Aucune analyse aboutie sur une vraie instance GPU au 24/10/2026 (essai 20 : GPU vu, import corrigé depuis) ; la
  lecture par fenêtres, éprouvée d'abord contre un faux S3, a fonctionné contre Copernicus sur le GP1-S le 19/10/2026
  (analyse 97, seule analyse aboutie) ; le POP2-8C-32G jamais encore essayé.
* Une analyse par lots encore inachevée au bout de `fenetre_utile_h` (24 h) est abandonnée sans fusion des fenêtres
  déjà faites (à 4 fenêtres toutes les 30 min, 12 fenêtres demandent 1 h 30 : cas improbable).
* Aucun GPU Pascal ne peut servir avec l'image GPU de Scaleway (modules noyau ouverts de NVIDIA, Turing et plus récent) :
  RENDER-S retiré le 19/10/2026 ; quand L4 et L40S sont en rupture, seul le repli sur processeur reste.
* Sentinel 2 (optique) n'était pas dans cette livraison : il a été construit au lot S2 (`docs/sentinel2.md`), avec le même registre des preuves (`sentinel2`).
* Le modèle d'AI2 manque une partie des petits navires ; AIS_UNCONFIRMED ne concerne que les navires de 50 m et plus.
* Les seuils d'incohérence et de persistance sont des valeurs de départ, à calibrer sur les premiers passages.
* Un volume bloc dont l'instance aurait été supprimée hors de MARS C2 (console) n'est pas retrouvé automatiquement.

## AIS_UNCONFIRMED hors des fenêtres lues (corrigé le 21/10/2026)

Depuis le lot C3, une scène n'est lue que dans ses fenêtres utiles ; la fusion confrontait pourtant au radar tous les
grands navires AIS de l'emprise de la scène entière. Un navire de plus de 50 m passant ailleurs dans la scène, jamais
regardé, recevait une alerte AIS_UNCONFIRMED (« aucun écho compatible »). Désormais, pour une scène analysée par
fenêtres, seul un navire dans le cœur d'une fenêtre lue sans erreur peut être déclaré sans écho (`dans_coeur`,
`fenetres_lues`, comme pour Sentinel 2) ; une scène entière garde son emprise. Test :
`test_selection_launch_and_results_end_to_end` (un navire de 220 m hors fenêtre n'est plus visé).

Alertes déjà émises : sur le serveur, au 21/10/2026, **aucune** (0 AIS_UNCONFIRMED parmi les 156 alertes ; l'analyse
97, seule analyse Sentinel 1 réussie avec des détections, n'en avait produit aucune). La commande
`taches.py sentinel-non-confirmees` les recherche (lecture seule) ; avec `--appliquer`, une alerte encore nouvelle est
classée « faux positif » avec le motif, par une action signée MARS C2, et une alerte déjà décidée par un opérateur garde
son statut et sa décision (motif ajouté à ses détails seulement). Rien n'est supprimé.
