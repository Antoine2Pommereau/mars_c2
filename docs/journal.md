# MARS C2 : journal du projet

Journal de chantier, une entrée par jalon, **dans l'ordre chronologique : le plus ancien en haut, le plus récent en
bas**. Chaque entrée donne la date, la version des règles et les migrations quand elles sont citées, ce qui a changé
et les mesures. Il garde l'histoire ; **l'état actuel du projet est dans `CLAUDE.md`**, l'installation du serveur dans
`docs/deploiement.md` et l'exploitation courante (publier, déployer, restaurer, échéances) dans `docs/exploitation.md`.

Origine : section « 9. État et mesures » et section « 10. Tests » de `CLAUDE.md`, sections datées de
`docs/deploiement.md` (lot Z3, 24/10/2026). Les blocs de commandes recopiés ici sont historiques : ils précèdent les
scripts `scripts/publier.sh` et `scripts/deployer.sh` (lot Z2, 22/10/2026).
Les chemins de documents cités sont ceux de l'époque : plusieurs sont depuis rangés dans `docs/archives/`
(`audit_code.md`, `vision_interface.md`, `travailleurs_viirs.md`, maquettes de `docs/ux/maquettes/`).

## Sommaire

* 06/10/2026 : étape 1 terminée ; nettoyage après audit ; règles en continu ; restauration éprouvée
* 07/10/2026 : charpente et contenu de l'interface ; recalibration préparée et listes automatiques ; région affichée
  et calendrier des passages ; premier déploiement par le registre ; étape 3, lot B
* 08/10/2026 : fiabilité des travailleurs après le premier essai réel
* 09/10/2026 : évaluation des détections VIIRS ; preuves images ; fiches, sources et séjours
* 11/10/2026 : zone de réception fiable provisoire ; étape 3, lot C, C1 et C2
* 11 au 13/10/2026 : corrections après les essais des 11 et 12/10
* 14/10/2026 : clés Copernicus dédiées aux travailleurs
* 18/10/2026 : volumes mesurés sur le serveur, disque plein
* 19/10/2026 : supervision (étape D) ; traces (étape D2) ; vue d'ensemble de Grafana ; réglages de la supervision ;
  lots C3, C4, C5 ; Sentinel 1 automatique
* 20/10/2026 : lot T1, tip and cue ; tip and cue activé ; navires muets en transit ; lot S2 (mesures, construction,
  premier essai réel, seau des modèles)
* 21/10/2026 : lot P, panneau de contrôle ; lot U, phases 1 et 2 ; lot A ; séjour hors port ; Coulisses
* 22/10/2026 : lot T (trajectoire, cercle appris, garde de probabilité, table de l'archive) ; lots Z1 et Z2
* 24/10/2026 : lot Z3 (documentation) ; lot Z4 (code mort) ; lot Z6 phase 1 ; enquête sur l'analyse 103 ; audit des modèles et lot historique (25/10)
* Points ouverts France ; décomptes de tests par lot

---

## 06/10/2026, étape 1 terminée

Collecte des quatre zones (environ 680 messages par minute), ingestion (34 000 positions conservées par heure), mode
direct, liste de surveillance (8 navires des listes dans la collecte du 05/10, dont 3 au signal fort : GELIOTROP,
VULKAN, PASIPHAE), archivage R2, purge à 30 jours, sauvegarde, surveillance du disque.

| Mesure | Valeur |
|---|---|
| Ingestion, collecte du 05/10 | 21 197 positions lues, 11 597 conservées, 9 s pour 460 fichiers |
| Taille d'une position en base | 282 octets index compris ; **75 Mo par jour mesurés sur le serveur** (18/10, base 982 Mo dont positions 893 Mo pour 12 jours) ; équilibre à 30 jours environ 2,2 Go de positions, 2,4 Go de base (l'estimation de 7,5 Go était fausse) |
| Archive R2 | 27,4 octets par position, 22,5 par message statique ; 26 Mo par jour ; 10 Go gratuits en 13 mois |
| CircleNet | F1 0,942 (ensemble, phase 0), 0,958 avec l'harmonisation Sentinel Hub ; 1,6 s par tuile sur le Mac |

**Restauration éprouvée** (06/10/2026, base de test) : le rechargement des positions depuis l'archive R2
(`taches.py restaurer-positions`) a rechargé 11 554 positions et 1 079 navires pour le 05/10, exactement le résultat
de l'ingestion d'origine (procédure : `docs/deploiement.md`, « Restauration complète »).

**Masques France, coût mesuré** (image linux/amd64 émulée sur le Mac, base de test, 06/10/2026) : 61 s, 300 Mo de
mémoire au plus, 142 Mo de disque au plus (détail : `docs/deploiement.md`, « Masques France »).

## 06/10/2026, nettoyage après audit

Migrations 14 et 15. Code danois retiré (lecteur DMA et son import, dans l'historique Git), code mort supprimé,
régions Manche et Gascogne en migration (14), bord des données de la coupure AIS mesuré sur les zones réelles, terres
exclues (15), test par injection adapté à un AIS allégé, carte et masques centrés sur la France.

## 06/10/2026, règles en continu

Dans le conteneur `taches`, toutes les 5 minutes, sur une fenêtre glissante de 24 heures bornée par la dernière
position reçue (`mars/rules.py`, `run_continuous` ; section `continu` de `config/rules.yaml`, seuils de calibration
inchangés) :

* rendez vous et coupures AIS, comme avant, mais enregistrés par clé stable ; les minutes de flux AIS coupé (moins
  de 20 % de la médiane par minute) ne comptent pas dans un silence ; **coupures AIS en mode observation** depuis le
  11/10/2026 (`ais_gap.mode`) : épisodes comptés et décrits dans `task_runs`, aucune alerte, tant que leurs seuils
  sont danois ;
* **WATCHLIST** : une alerte par passage dans nos eaux d'un navire des listes (fort, sanctionné, flotte fantôme,
  suspect GUR), gravité selon le niveau, un cran de moins si reconnu par le MMSI seul ;
* **IDENTITY_CHANGE** : nouveau nom confirmé 6 h sans retour à un nom antérieur (les alternances comme MUTIN ne
  sont jamais signalées), ou même OMI sous un autre MMSI (pavillon changé, usage simultané) ; MMSI génériques
  (code pays et six zéros, comme 227000000) écartés.

Éprouvé sur une base de test : chaque cas attendu, décisions conservées d'un cycle à l'autre, alerte vierge retirée
quand des données tardives comblent un silence, 41 fausses coupures évitées lors d'une coupure générale du flux de
140 min. Coût sur 867 000 positions (émulation, borne haute) : 1,4 s pour les coupures, 33 s pour les rendez vous
dans un cas extrême (5 700 épisodes), 181 Mo de mémoire.

## 07/10/2026, charpente de l'interface

Lot 1 de `docs/vision_interface.md`. Mesures sur la base de test (3 000 navires, 867 000 positions sur 24 h) : frise
sur 24 h en 0,08 s (13,6 Ko), sur 7 et 30 jours en 0,02 s ; coupure du flux de 40 min retrouvée à la minute près ;
trafic à un instant, 3 042 navires en 240 Ko (78 octets par navire) et 0,13 s ; flux en direct 274 Ko au premier envoi
puis 0,1 Ko par seconde (680 Ko par seconde avant, sur le serveur) ; infrastructures 2,6 Mo en pleine résolution,
simplifiées à 50 m par l'API.

## 07/10/2026, contenu de l'interface

Lot 2 de la vision. Constats sur les données réelles : EMODnet ne nomme aucun câble électrique et seulement 74 câbles
télécoms sur 649 (les fiches se replient sur le type et le numéro) ; les tracés communs aux régions Bretagne et
Manche, qui se chevauchent, sont stockés deux fois (dédoublonnés à l'affichage). Le lot 1 n'avait pas été déployé côté
API (transferts SSH en échec) : d'où les 680 Ko par seconde encore mesurés sur le serveur, résolus au premier
déploiement par le registre (entrée « Premier déploiement par le registre GitHub » ci dessous).

## 07/10/2026, recalibration préparée et listes automatiques

Règles 2026.10.18, aucun seuil de calibration changé.

* **Rendez vous** : les positions à moins de 1 km de la terre (`rendezvous.coast_prefilter_m`, paramètre
  d'optimisation, 2 km sous `min_coast_km`) sont écartées avant de former les paires. Sur le serveur, 4 483 épisodes
  sur 4 485 étaient calculés puis écartés comme côtiers, et un cycle montait à 132 s. Mesure sur la base de test
  avec ports densifiés (165 000 positions, 7 000 navires sur 24 h, émulation amd64) : 419 s avant, 10,5 s après
  (40 fois moins) ; fichiers temporaires PostgreSQL 15,8 Go avant, 107 Mo après ; plus gros nœud en mémoire 665 Mo
  avant, 8 Mo après ; 18 044 épisodes dont 18 040 côtiers avant, 10 dont 6 côtiers après ; **mêmes 4 épisodes
  retenus, donc mêmes 4 alertes**, y compris une paire qui dérive vers la rade de Brest.
* **Changements d'identité** : bâtiment militaire (type AIS 35, MMSI de la liste `identite.militaire.mmsi`,
  préfixe HMS, FS, USS…, nom réduit à un numéro de coque comme « 101 ») en gravité faible, étiqueté « militaire »
  dans le fil et la fiche ; même OMI sous un MMSI du même pays, sans navire des listes en cause : faible (moyenne en
  cas d'usage simultané), cas de L'HORIZON 1 (OMI 8542248) ; tout navire des listes en cause garde la gravité élevée.
* **Listes** : OpenSanctions chaque jour, GUR chaque semaine si l'empreinte Git de `Vessels1.db` a changé ;
  lecture en mémoire, journal ajoutés, retirés, modifiés dans `task_runs`, règles des listes relancées aussitôt ;
  un échec garde la liste précédente et met l'indicateur « Listes » à l'orange. Essai : 20 725 navires
  OpenSanctions et 1 206 GUR importés en 2 s, second passage « inchangée », échec simulé sans perte.
* **Mesures de calibration** : `scripts/mesures_calibration.py` (ou `taches.py mesures`) écrit
  `docs/mesures_calibration.md` : intervalles par type, navire et zone, réception selon `min_pairs`, continuité et
  intervalle, épisodes et alertes par jour, puis une proposition par seuil. Le rapport versionné vient de la base
  de test : à régénérer sur le serveur après une à deux semaines de collecte.

## 07/10/2026, région affichée et calendrier des passages

Migration 19. Sélecteur de région dans la barre d'état (les filtres par zone du fil et des couches sont retirés) ;
réponses allégées par l'API, sur la base de test : trafic 179 Ko pour la France, 14 Ko pour la Bretagne ;
infrastructures 701 Ko contre 195 Ko ; alertes 26 Ko contre 6 Ko.

Étape 3, lot A : calendrier des passages Sentinel 1 et 2 (`docs/passages_satellites.md`) : catalogue STAC public de
Copernicus Data Space pour les passages acquis, plans d'acquisition de l'ESA pour les passages prévus, réunis par
satellite et orbite absolue ; 30 jours lus en 32 s et 137 Mo de mémoire (98 passages Sentinel 1, 80 Sentinel 2,
81 prévus) ; plans confrontés au catalogue du 20/09 au 07/10 : 90 passages retrouvés sur 91 ; heure prévue
Sentinel 1 interpolée sur nos régions, 0,2 min d'écart médian ; 3,5 Ko par passage en base. Pour chaque passage :
infrastructures et navires des listes dans l'emprise (base du déclenchement au lot C). Couche « Passages Sentinel 1
et 2 » (plein acquis, pointillé prévu), piste de la frise, fiche passage, indicateur « Satellites » (prochain passage
sur la région affichée).

**Déploiement de la migration 19** (ancien `docs/deploiement.md`, « Tâches planifiées ») : au premier cycle après la
migration 19, les statistiques par région (`stats_10min_region`) sont rattrapées sur toutes les positions en base :
environ 9 s par journée (mesuré en émulation sur 288 000 positions, 3,2 s), soit 4 à 5 minutes une seule fois pour
30 jours, pendant lesquelles ce cycle des règles est retardé.

## 07/10/2026, premier déploiement par le registre GitHub (procédure manuelle)

Les images sont désormais construites sur le Mac, publiées sur le registre GitHub et téléchargées par le serveur. Cette
procédure manuelle a servi du 07/10 au 22/10/2026 ; elle est remplacée par `scripts/publier.sh` et
`scripts/deployer.sh` (lot Z2).

| Image | Services | Taille (07/10/2026) |
|---|---|---|
| `ghcr.io/antoine2pommereau/mars_c2-backend` | `backend` | 160 Mo |
| `ghcr.io/antoine2pommereau/mars_c2-web` | `web` | 21 Mo |
| `ghcr.io/antoine2pommereau/mars_c2-scripts` | `collector`, `ingest`, `taches` | 190 Mo |

Chaque publication portait deux étiquettes : `latest`, que le serveur utilisait par défaut, et l'empreinte courte du
commit (par exemple `4238202`), pour revenir à une version précise (`MARS_TAG=4238202` dans le `.env` du serveur). Les
couches inchangées ne sont ni republiées ni retéléchargées.

> Historique, ne plus suivre : voir `docs/exploitation.md`.

**1. Sur le Mac : construire**, depuis le dépôt à jour (commandes compatibles zsh), avec `docker build` directement :
ni Compose, ni `.env`, ni les secrets du serveur ne sont nécessaires (depuis l'étape D2 ; la surcouche du serveur
exige `GRAFANA_ADMIN_MOT_DE_PASSE`, `GRAFANA_PG_MOT_DE_PASSE` et `NTFY_SUJET`, qui n'ont rien à faire sur le Mac).
Chaque image reçoit l'empreinte du commit (`MARS_VERSION`), montrée par l'indicateur « Version » de la barre d'état
et par Grafana :

```zsh
cd ~/Documents/"Projet Perso"/MarsC2/mars_c2 && git pull origin main
R=ghcr.io/antoine2pommereau; TAG=$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -f backend/Dockerfile --build-arg MARS_VERSION=$TAG -t ${R}/mars_c2-backend:latest . \
  && docker build --platform linux/amd64 -f scripts/Dockerfile --build-arg MARS_VERSION=$TAG -t ${R}/mars_c2-scripts:latest . \
  && docker build --platform linux/amd64 --build-arg MARS_VERSION=$TAG -t ${R}/mars_c2-web:latest web
for i in mars_c2-backend mars_c2-web mars_c2-scripts; do
  docker image inspect "${R}/${i}:latest" --format "${i} {{.Architecture}} {{index .Config.Labels \"org.opencontainers.image.revision\"}}"
done
```

Les trois devaient afficher `amd64` et l'empreinte du commit. Ces images remplacent sur le Mac celles du même nom :
pour retrouver des images natives en local, relancer ensuite `docker compose build backend web`.

**2. Sur le Mac : publier**, avec l'étiquette du commit en plus de `latest` :

```zsh
R=ghcr.io/antoine2pommereau
TAG=$(git rev-parse --short HEAD)
scripts/verifier_images.sh && for i in mars_c2-backend mars_c2-web mars_c2-scripts; do
  docker tag "${R}/${i}:latest" "${R}/${i}:${TAG}" && docker push "${R}/${i}:latest" && docker push "${R}/${i}:${TAG}" || break
done
```

La vérification et la publication sont enchaînées par `&&` : une ligne `ÉCHEC` empêche tout envoi, et un envoi en
échec arrête la boucle. Ne publier que les images qui changent était possible (retirer les autres de la liste), mais
pas nécessaire : une couche déjà sur le registre n'est pas renvoyée.

**3. Sur le serveur : télécharger et démarrer.** Code et migrations d'abord (une nouvelle migration de `db/init` ne
s'applique pas seule à une base existante, et la nouvelle API peut en dépendre) :

```bash
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/1X_nom.sql   # chaque migration nouvelle
docker compose pull backend web collector ingest taches
docker compose up -d --no-build
docker image prune -f                              # anciennes versions devenues sans étiquette
```

**Disque juste.** `docker compose pull` télécharge les couches compressées (environ 330 Mo pour les trois images
quand tout change) puis les décompresse (environ 370 Mo) pendant que les anciennes images sont encore présentes et
utilisées : prévoir environ 800 Mo libres. S'il n'y en avait pas assez, libérer d'abord, dans cet ordre (la base et
ses données ne sont pas touchées ; la collecte s'interrompt quelques minutes, sans conséquence sur les règles, les
minutes de flux coupé ne comptant pas comme des silences) :

```bash
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/1X_nom.sql   # migrations nouvelles
docker compose rm -sf backend web collector ingest taches   # arrêt et suppression des conteneurs, pas des volumes
docker image prune -af                                      # images sans conteneur ; la base, en service, garde la sienne
df -h /                                                     # vérifier la place libre avant le téléchargement
docker compose pull backend web collector ingest taches
docker compose up -d --no-build
```

`docker image prune -af` ne retire que les images qu'aucun conteneur n'utilise : celle de la base, en service, reste.
Au passage aux images du registre, les anciennes images locales (`mars_c2-backend`, `mars_c2-web`,
`mars_c2-scripts`, et plus anciennes `mars_c2-collector`, `mars_c2-ingest`, `mars_c2-taches`) sont parties avec elles.

**Revenir à une version précédente** (à l'époque) : `MARS_TAG=<empreinte> docker compose pull` puis
`MARS_TAG=<empreinte> docker compose up -d --no-build` (ou inscrire `MARS_TAG` dans le `.env` du serveur).

## 07/10/2026, étape 3, lot B

Migration 20, règles 2026.10.19, `docs/travailleurs_viirs.md`. Architecture « orchestrateur et travailleurs
éphémères » commune aux lots B et C (instances Scaleway créées par l'API, retour par réseau privé et jeton, durée de
vie de 45 min, plafonds de 4 travailleurs et 180 minutes par jour, orphelins détruits chaque minute, journal dans
`task_runs`). VIIRS : 12 granules par nuit sur nos régions (catalogue CMR, temps quasi réel), modèle allenai dans son
image publique (886 Mo), 1,45 Go de mémoire et 12,5 s par granule mesurés en émulation, DEV1-M ; appariement AIS
(1 500 m plus l'estime), côtes à 5 km, lumières fixes (3 nuits en 30 jours, alertes antérieures classées), DARK_SHIP
de gravité élevée près d'une infrastructure ou d'un navire des listes. Coût estimé : **environ 0,76 € HT par mois**.
Éprouvé sur la base de test et avec un faux client Scaleway ; **pas encore sur une vraie instance** (clés absentes) :
la première nuit réelle donnera durée, mémoire et coût dans `task_runs`. Aussi : nuages annoncés des passages
Sentinel 2 (lot C), vignettes des navires dans les alertes, nettoyage du registre GitHub, `/api/metrics`.

Ancien `docs/deploiement.md`, « Mesures pour Grafana » (texte de l'époque) : `curl -s localhost:8000/api/metrics`
(format texte de Prometheus : exécutions et durée de chaque tâche, travailleurs, minutes et coût, détections VIIRS,
passages, alertes). Aucun outil installé ; un agent Grafana pourra lire cette adresse plus tard (Grafana est installé
depuis l'étape D, 19/10/2026).

## 08/10/2026, fiabilité des travailleurs après le premier essai réel

Migration 21. Création dans l'ordre vérifié (éteinte, réseau privé attendu « available », cloud-init avec la MAC,
démarrage attendu « running ») ; script de démarrage qui configure l'interface privée en DHCP, contacte le serveur et
lui envoie son journal ; échec de lancement journalisé « echec » avec destruction immédiate ; destruction après
10 minutes sans signe de vie ; verrou en base (un seul travailleur VIIRS actif) ; une même détection jamais écrite deux
fois (index unique, traitement d'un résultat verrouillé). Script de démarrage éprouvé dans un conteneur Ubuntu contre
l'API locale.

## 09/10/2026, évaluation des détections VIIRS

Migration 22, règles 2026.10.20. AIS le plus proche et réception AIS mesurés pour chaque détection ; non évaluable
pendant une coupure du flux ou sans réception AIS autour ; gravité faible « à confirmer » sauf corridor, navire des
listes ou au delà de 12 milles ; section VIIRS du rapport de mesures (par nuit et par région, luminosité, distances,
tolérances) ; état Scaleway d'un travailleur muet relevé avant sa destruction. Détail et chiffres :
`docs/travailleurs_viirs.md`.

## 09/10/2026, preuves images

Migration 23. Vignette PNG de 30 km par détection VIIRS, tirée par le travailleur de l'image en mémoire, rangée sur R2
par taches, registre `preuves_images` générique (Sentinel 1 et 2 au lot C), relue par l'API sans boto3
(`mars/r2_signature.py`, signature vérifiée sur l'exemple d'AWS et contre boto3) ; superposition des positions AIS à
5 km dans la fiche détection et les preuves des alertes DARK_SHIP, agrandissable ; 4,7 Ko par vignette, environ
0,25 Mo par nuit ; conservation sans limite avec alerte, 30 jours sinon.

## 09/10/2026, fiches, sources et séjours

Migration 24, règles 2026.10.21. Photo du navire en tête de toute fiche d'alerte qui concerne un navire (section
`navire` du registre), motif de l'échec dans la fiche et dans les journaux de l'API (`mars.photos` : page refusée, pas
de photo, image refusée, source injoignable) ; vérifié sur le serveur, SMYRTOS (27,6 Ko) et GROSSULYAR (51,7 Ko)
servies par VesselFinder. Sources des listes regroupées par organisme (`web/src/lib/sources.ts` : GUR, Union
européenne, Royaume Uni, États Unis, puis les autres, d'après le préfixe du jeu OpenSanctions, motifs et liens
réunis) : pour SMYRTOS, les sept entrées OpenSanctions deviennent six autorités, les deux listes de l'UE fusionnées.
Champ `source` dans chaque position et dans le Parquet (`mars.ais.live.SOURCE`, `aisstream`). Séjour prolongé d'un
navire des listes (`watchlist.sejour_prolonge_h`, 24 h) : durée mesurée depuis le début du passage (la fenêtre des
règles de 24 h le tronquerait), part du temps à l'arrêt pondérée par la durée (un point toutes les dix minutes à
l'arrêt contre un par minute en route), lieu (mouillage connu ou non, distance à la côte), dans l'alerte WATCHLIST et
la fiche navire. Rapport de mesures : section portée de réception.

**Déploiement de la migration 24 (origine des positions)**, ancien `docs/deploiement.md` :
`db/init/24_sources_sejours.sql` ajoute la colonne `positions.source` (aujourd'hui toujours `aisstream`). Valeur par
défaut constante : l'ajout est immédiat, sans réécrire la table, quel que soit le nombre de positions. Elle devait
précéder le démarrage de la nouvelle image des scripts : la nouvelle ingestion écrit cette colonne. Les fichiers
Parquet de la collecte portent le même champ ; les fichiers et archives antérieurs, qui ne l'ont pas, sont lus comme
`aisstream` (ingestion, rechargement depuis R2 et compactage de l'archive le complètent). Contrôle :
`SELECT source, count(*) FROM positions WHERE ts > now() - interval '1 hour' GROUP BY 1`.

## 11/10/2026, zone de réception fiable provisoire

Règles 2026.10.22, aucune migration. Mesuré sur le serveur du 06 au 09/10, les seuils danois (intervalle normal 3 min,
continuité 95 %, 200 paires) donnent une zone vide (flux AISStream partiel, un point par minute en route) ; réglage
provisoire 10 min, 90 %, 50 paires, 3 navires (1 067 cellules, environ 44 000 km² sur le serveur), à recalibrer sur dix
jours. Dès que la zone existe, elle seule décide qu'une détection satellite sans AIS est évaluable
(`mars/evaluation.py`, commun à VIIRS et à Sentinel ; alerte notée `critere_evaluation`) ; `taches.py viirs-reevaluer`
après chaque reconstruction. Coupures AIS en observation.

Test par injection partagé (`ais_gap_injection`, API et `taches.py coupures-injection`), éprouvé sur 135 769 positions
réelles de 500 navires en route (48 h lues sur le serveur), zone de test de 351 cellules : **181 coupures de 3 h
retrouvées sur 253 injectées (72 %)** ; 54 manquées prises pour des sorties de couverture (route projetée hors d'une
zone de test plus petite que celle du serveur), 18 par d'autres filtres ; avant tout essai, 316 navires en route sur
988 (32 %) n'ont pas les 10 messages dans l'heure qu'exige `min_prior_messages` (valeur danoise) : premier seuil à
recalibrer.

Rapport de mesures : surface de la zone par région, part des détections VIIRS évaluables avant et après, portée de
réception (trait de côte subdivisé et indexé, 32 plus proches voisins : 14,8 s pour 12 000 positions au lieu d'environ
13 min, écart au calcul exact de 14 m au plus), progression affichée par section.

## 11/10/2026, étape 3, lot C, C1 et C2

Migration 25, règles 2026.10.23, `docs/sentinel.md`. Travailleur GPU L4-1-24G en fr-par-2 (réseau privé régional,
image Ubuntu GPU OS sur volume bloc, supprimé explicitement : jamais `terminate`), image publique d'AI2 épinglée
(`sha-fafc6c3`, 5 Go) avec un script maison qui appelle sa chaîne (son serveur intégré est incohérent à cette
version) ; scènes COG lues en S3 par le travailleur, signature par en tête, avec une paire de clés dédiée remise par le
réseau privé (corrigé le 14/10/2026, voir les pièges) ; sélection des passages sur corridor avec navire des listes ou
alerte VIIRS moyenne au moins, scènes utiles seulement, une session GPU ; plafond de 2 scènes par jour et budget de
6 € par mois ; fusion existante réutilisée sans scipy ni pyproj dans l'image des scripts (mêmes résultats, testé) ;
éoliennes, échos fixes, évaluation commune avec VIIRS ; alertes DARK_SHIP radar, AIS_UNCONFIRMED, IDENTITY_MISMATCH
(longueur au delà de 40 m et 40 %, orientation en travers seulement) ; vignette radar de 3 km, 19 Ko au plus (essai
GDAL sur image synthétique géolocalisée : cible au pixel près, contraste de 32,6 dB attendu) ; couche, fiche,
vérification satellite, frise, indicateur, `/api/metrics`. Éprouvé de bout en bout sur la base de test avec de faux
clients Copernicus et Scaleway ; **pas encore sur un vrai GPU** (CUDA 11.3 de l'image sur L4, mémoire, poids dans
l'image : à trancher au premier essai, `taches.py sentinel-analyser <id>`).

## 11 au 13/10/2026, corrections après les essais des 11 et 12/10

Règles 2026.10.25, aucune migration ; seule l'image `mars_c2-scripts` change.

* **Plafond des travailleurs** : seuls comptent ceux dont une instance a été créée (`scw_server_id`) ; les sept refus de
  Scaleway en 0,1 s (lancements Sentinel 10 à 16) avaient épuisé le plafond et privé la nuit VIIRS du 12/10 ; place
  réservée à VIIRS (1 travailleur, 60 min) tant que sa nuit n'a pas eu son travailleur ; échec de lancement Sentinel :
  sélection automatique suspendue jusqu'au lendemain (motif dans `task_runs` et l'indicateur), passage jamais bloqué.
* **Image GPU** : résolution sur volume bloc (`sbs_snapshot` accepté, taille lue dans l'API Block Storage si la fiche
  la donne nulle, confirmé par le diagnostic du 13/10 : `sbs_snapshot` de taille 0, 20 Go au stockage bloc), un message
  par cause ; DEV1-M inchangées. Clés Copernicus vérifiées par une lecture réelle avant tout appel à Scaleway.
  `taches.py sentinel-diagnostic` (lecture seule, fiche brute de l'image, quota, droits, réseau privé).
* **Garde fou des images** : `tests/test_images.py` (modules locaux copiés par chaque Dockerfile, bibliothèques
  chargées au démarrage déclarées) et `scripts/verifier_images.sh` (images construites démarrées sur le Mac, API
  jusqu'à `/api/health`), étape de `docs/deploiement.md` à l'époque (aujourd'hui dans `scripts/publier.sh`).
* **Coupures AIS** (observation) : route déclarée suivie 60 min (un point toutes les 10 min, recherche par clé de
  cellule) : sortie de couverture si elle quitte la zone fiable ; points de disparition collectifs (3 navires sans lien,
  maille de 0,2 × 0,1 degré, même jour) : coupures déclassées en faible ; comptés à part dans le rapport de mesures.
  Mesuré sur une copie réelle du 11/10 (Manche et Bretagne, 1 752 navires en route, 146 635 positions, vraie zone
  fiable de 1 395 cellules, lues sur le serveur) : avant, 85 coupures retenues dont 43 élevées (le serveur en voyait 88
  dont 45) ; route suivie, 22 sorties de plus, 63 retenues ; points collectifs, 13 points dont le premier, 19 navires
  en 50,35 N 0,9 O (en pleine Manche, 40 km au sud de l'île de Wight, voie ouest du dispositif de séparation du trafic :
  limite de couverture, silences étalés de 00:39 à 18:51, tous vers l'ouest), 47 coupures déclassées : 55 faibles,
  2 moyennes, 6 élevées. **Coût** : la projection ajoute 0,01 s ; le cycle lent venait d'ailleurs : PostgreSQL
  recalculait le bord des données en mer (union du trait de côte) pour chaque silence ; sous requêtes `MATERIALIZED`,
  `find_gaps` passe de 68 s à 0,7 s, mêmes silences. Cycle complet sur la copie, étape par étape (ancien, nouveau) :
  rendez vous 7,0 et 7,2 s ; coupures 92,4 et 0,9 s ; listes et identités 0,03 et 0,01 s ; frise 1,7 et 1,6 s ; total
  **101 s et 9,7 s** (serveur avant correctif : 113 s). Chaque cycle consigne désormais `durees_s` par étape dans
  `task_runs`.
* **Points collectifs, nature et garde** (13/10/2026) : la moitié au moins des silences d'un point en une demi heure,
  coupure de réception simultanée ; sinon limite de couverture (les 13 points du 11/10, dont celui de 50,35 N 0,9 O :
  19 navires vers l'ouest, cap médian 254°, de 00:39 à 18:51). Un indice propre au navire (partenaire au large,
  rendez vous lié, arrêt pendant le silence) garde sa gravité : seul le silence long sans réapparition est déclassé
  (les 30 élevées déclassées du 11/10 l'étaient toutes pour cette seule raison) ; `gravite_avant_declassement` gardée.
* **Trous de notre collecte** (déploiement, coupure d'AISStream : toutes les minutes vides à la fois, `feed_outage`) :
  minutes retirées des silences (déjà), silence dont le début tombe dans le trou ou 10 min avant écarté (début
  inconnu, `debut_incertain_min`), historique exigé réduit au prorata des minutes collectées, points collectifs sur les
  vrais silences seulement. Copie du 11/10 : arrêt simulé de 20 min, résultat identique à sans arrêt (63 retenues,
  6 élevées) ; 140 min, mêmes élevées, 7 coupures dont le début tombait dans le trou écartées (choix délibéré).

**Vérifier les images construites avant toute publication** (étape « 1 bis » de l'ancienne procédure de
`docs/deploiement.md`, ajoutée après le 11/10/2026). Le 11/10/2026, l'API a planté en boucle au démarrage sur le
serveur (`backend/sentinel.py` non copié par `backend/Dockerfile`), alors que les tests passaient depuis le dépôt. Le
script démarre chaque image comme le serveur le fera (`scripts/publier.sh` l'appelle désormais) :

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```zsh
scripts/verifier_images.sh                     # les trois ; ou seulement celles qui changent : scripts/verifier_images.sh scripts
scripts/verifier_images.sh ai2                 # facultatif : script du travailleur Sentinel dans l'image d'AI2 (5 Go)
scripts/verifier_images.sh ai2-complet         # facultatif : analyse complète sur CPU d'une scène synthétique (5 min)
scripts/verifier_images.sh traces              # trace complète de bout en bout dans Tempo, travailleur simulé compris (3 min)
scripts/verifier_images.sh s2 s2-s3            # travailleur Sentinel 2 dans l'image rslearn ; s2-s3 obligatoire dès que travailleurs/sentinel2.py change :
                                               # chemin S3 de production, faux S3 qui vérifie la signature (environ 9 min en émulation)
```

(Les modes `traces`, `s2` et `s2-s3` ont été ajoutés plus tard, aux étapes D2 et S2.) `traces` doit afficher `OK
traces : trace … de bout en bout dans Tempo (OK 22 spans, services mars-api, mars-taches, mars-travailleur) ; jeton
faux refusé ; données sensibles filtrées par le collecteur`. À lancer avant de publier dès que l'instrumentation, un
script de travailleur, nginx ou la configuration du collecteur change. À lancer avant tout essai réel quand
`travailleurs/sentinel.py` change : `OK chaîne d'AI2 importée`, et pour `ai2-complet`, `OK analyse complète sur CPU :
4 cibles sur 4 retrouvées`. Ce qu'on doit lire : une ligne `OK` par image (`backend : l'API démarre et /api/health
répond`, `scripts : points d'entrée chargés`, `web : nginx sert la page`), puis `Publication possible`. Une ligne
`ÉCHEC` arrête tout, avec le journal du conteneur. Le test `tests/test_images.py` fait la même vérification sans Docker
(modules copiés par chaque Dockerfile, bibliothèques déclarées), à chaque `pytest`.

**Une seule image qui change, disque très juste** (354 Mo libres le 12/10/2026 : le serveur ne supportait qu'une image
à la fois). Cas d'un lot qui ne touche que `mars/`, `scripts/`, `config/` : seule `mars_c2-scripts` (collecte,
ingestion, tâches) est republiée. L'API garde son image : elle embarque aussi `mars/`, mais n'en utilise pas les
parties modifiées, et la configuration (`config/rules.yaml`) est lue depuis le dépôt, montée dans les conteneurs.

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```zsh
# Sur le Mac
cd ~/Documents/"Projet Perso"/MarsC2/mars_c2 && git checkout main && git pull origin main
R=ghcr.io/antoine2pommereau; TAG=$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -f scripts/Dockerfile --build-arg MARS_VERSION=$TAG -t ${R}/mars_c2-scripts:latest .
scripts/verifier_images.sh scripts && docker tag "${R}/mars_c2-scripts:latest" "${R}/mars_c2-scripts:${TAG}" \
  && docker push "${R}/mars_c2-scripts:latest" && docker push "${R}/mars_c2-scripts:${TAG}"
```

```bash
# Sur le serveur : l'ancienne image des scripts libérée avant le téléchargement de la nouvelle
cd /opt/mars_c2 && git pull origin main
docker compose rm -sf collector ingest taches               # conteneurs des scripts seulement ; API, interface et base restent
docker image prune -af                                      # l'ancienne image des scripts part ; celles en service restent
df -h /                                                     # environ 200 Mo nécessaires
docker compose pull collector ingest taches
docker compose up -d --no-build collector ingest taches
```

La collecte s'interrompait quelques minutes (minutes de flux coupé, sans effet sur les règles).

## 14/10/2026, clés Copernicus dédiées aux travailleurs

Règles 2026.10.26, aucune migration ; images backend et scripts, web inchangée. Copernicus refuse les adresses
présignées (piège de `CLAUDE.md`) ; le travailleur Sentinel demande sa tâche au démarrage (`tache_par_le_reseau`) et
reçoit scènes et paire `CDSE_TRAVAILLEUR_*` une seule fois, sur jeton, par le réseau privé ; il lit les images COG en
S3 avec signature par en tête. `sentinel-diagnostic` lit 16 octets avec chacune des deux paires et surveille
l'expiration de la paire dédiée. `scenes_max_par_jour` reste à 0.

## 18/10/2026, volumes mesurés sur le serveur, disque plein

Ancien `docs/deploiement.md`, « Volumes à l'équilibre (mesure du serveur, 18/10/2026) » ; l'état actuel du
dimensionnement reste dans `docs/deploiement.md`.

**Base. Corrigé le 19/10/2026** : l'estimation de 7,5 Go (34 000 positions conservées par heure, 282 octets chacune,
soit 230 Mo par jour) était fausse. Mesure du serveur le 18/10/2026 : base de 982 Mo, dont 893 Mo de positions pour
12 jours, soit **environ 75 Mo par jour**, trois fois moins ; 75 Mo par jour font environ 11 000 positions conservées
par heure, pas 34 000 ; autres tables : 89 Mo le 18/10.

**Disque du serveur** : le disque de 8 Go s'est rempli le 18/10/2026 vers 01:00 (incident, `docs/supervision.md`) ;
disque porté à 15 Go le même jour (13 Go utilisables), 4,6 Go libres avec une base de 982 Mo. Bilan prévisible à
15 Go, tel qu'il était écrit :

* Grafana : environ 0,8 Go (image décompressée et données), soit **environ 3,7 Go libres après déploiement** ;
* la base gagne encore environ 1,35 Go jusqu'à l'équilibre (18 jours à 75 Mo) : **environ 2,4 Go libres à
  l'équilibre**, soit 16 % de 15 Go, juste au dessus de l'alerte des 15 % et sous les 3 Go souhaités. À surveiller
  dans le tableau Disque et base ; leviers, dans l'ordre : `docker image prune -af` et `docker builder prune -af`
  (images et caches sans conteneur), puis l'index spatial de `positions` (23 % de la table, utile aux seules analyses
  radar), puis un disque de 20 Go ;
* **traces (étape D2)** : images du collecteur (environ 420 Mo décompressés) et de Tempo (environ 137 Mo), plus
  40 Mo pour les images de l'API et des scripts : environ 0,6 Go ; traces gardées 72 h : moins de 30 Mo. Soit
  **environ 3,1 Go libres après déploiement, et environ 1,8 Go à l'équilibre**, 12 % de 15 Go : **sous l'alerte des
  15 %**. Il fallait agrandir le volume : 20 Go donneraient environ 6,8 Go libres à l'équilibre (34 %). Le serveur est
  depuis passé à 20 Go.

**Mémoire** : la DEV1-S (2 Go) ne suffisait pas aux traces ; serveur passé en DEV1-M (4 Go) le 19/10/2026, 3,5 Go
disponibles avant les traces (`free -m`), soit environ 0,5 Go utilisés par la base, l'API, l'interface, la collecte,
l'ingestion, taches et Grafana. Mesures du serveur qui ont fait revoir les limites Docker : collecteur 211 Mo pour une
limite de 200, Grafana 208 Mo pour 256 (limites actuelles dans `docs/deploiement.md`).

## 19/10/2026, supervision, étape D

Migration 26, `docs/supervision.md`, après l'incident du 18/10/2026 (pièges de `CLAUDE.md`) :

* plus de mort silencieuse : la collecte journalise une écriture en échec et garde les messages en mémoire (120 000 au
  plus), s'arrête si une tâche interne meurt ; l'ingestion ne s'arrête plus sur une erreur (DiskFull dit en clair,
  attente croissante jusqu'à 5 min, échec consigné dans `task_runs`) ;
* contrôle de santé qui arrête un processus figé, relancé par `restart` (`scripts/sante.py`, `init: true`), sans le
  socket Docker ; redémarrages dans `task_runs` (`redemarrage_collecte`, `redemarrage_ingestion`) ; éprouvé dans
  Docker (`verifier_images.sh sante` : collecte figée relancée en 75 s, collecte saine laissée en paix) ;
* alertes sur le téléphone par ntfy (sujet secret `NTFY_SUJET`) : Grafana (disque sous 15 %, collecte sans fichier
  depuis 5 min, ingestion en retard de plus de 10 min, nuit VIIRS absente à 08:00 UTC, tâche en échec deux fois de
  suite), au plus une fois par heure, retour à la normale annoncé ; secours par taches pour les trois plus graves si
  Grafana ne répond pas, sans écrire en base ; `taches.py tester-alerte` ; seuils dans `config/supervision.env` ;
* Grafana 12.4.2 (214 Mo à télécharger, 763 Mo décompressée, 187 Mo de mémoire mesurés sur le Mac), cinq tableaux
  (Direct, Disque et base, Tâches, Satellites, Alertes) ; mesures du disque historisées toutes les 10 min
  (`mesures_disque`), signes de vie chaque minute (`signes_de_vie`) ; `taches.py mesurer-tableaux` : 86 mesures sous
  0,1 s sur un historique synthétique de 30 jours, à refaire sur le serveur.

Déploiement (ancien `docs/deploiement.md`) : migration 26 (`db/init/26_supervision.sql` : tables de mesures et de
signes de vie, rôle `grafana_lecture`), puis les images ; les trois variables de la supervision posées dans le `.env`
avant tout `docker compose` (installation : `docs/deploiement.md`, « Supervision »).

## 19/10/2026, traces OpenTelemetry, étape D2

Migration 27, `docs/opentelemetry.md`. Tout le système envoie ses traces à un collecteur OpenTelemetry (contrib,
nécessaire pour le tail sampling, le filtre `transform` et la file d'attente sur disque : la distribution de base
refuse la configuration), gardées 72 h dans Tempo 3.1 (binaire unique, sans Kafka) et lues dans Grafana (source
Tempo, tableau « Traces », liens depuis les tâches et les travailleurs). API (FastAPI, asyncpg, httpx, requests, spans
métier ; SSE et santé exclus), collecte (par écriture et par connexion AISStream), ingestion (par cycle et par
fichier), taches (toute tâche par `run()`, toute commande par le registre `COMMANDES`, chaque minute, chaque étape des
règles, de l'archivage, des listes, des passages, du disque), appels externes nommés (`peer.service`), interface
(requêtes vers l'API, par nginx), travailleurs : une analyse est une trace de bout en bout (lancement et étapes
Scaleway, script de démarrage bash, script Python, téléchargements, granules ou scènes, inférence, renvoi, traitement
des résultats, alertes, destruction). Images d'AI2 et d'allenai en Python 3.8 et 3.10 avec protobuf 4, alors que tout
exportateur OTLP officiel exige protobuf 5 : exportateur OTLP JSON en bibliothèque standard
(`travailleurs/otel_mini.py`). Travailleurs authentifiés par nginx (port 8090, jeton de l'exécution vérifié par l'API,
refusé après la destruction). trace_id dans `task_runs`, `travailleurs`, `analyses` et les détails de chaque alerte
(déclencheur). Échantillonnage : tâches et travailleurs 100 %, API erreurs et plus de 1 s, 10 % du reste. Données
sensibles filtrées dans le code et dans le collecteur. Alertes ntfy : collecteur muet 15 min, ou spans refusés.
Empreinte du commit dans chaque image (indicateur « Version », Grafana). Mesures (émulation) : surcoût des règles dans
le bruit, ingestion +0,8 % ; environ 45 000 spans par jour, moins de 30 Mo de traces à 72 h ; collecteur 110 à
200 Mo, Tempo 90 à 185 Mo. Bilan serveur : **la mémoire de la DEV1-S ne suffit plus (passer en DEV1-M) et le disque
tombe sous l'alerte des 15 % à l'équilibre (agrandir à 20 Go)** ; fait depuis (DEV1-M, 20 Go).

Mise en place sur le serveur, une image à la fois (disque juste) :

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```bash
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/27_traces.sql
df -h /                                                   # environ 300 Mo nécessaires pour les deux images
docker compose pull tempo otel && docker compose up -d tempo otel
docker compose rm -sf collector ingest taches && docker image prune -af
docker compose pull collector ingest taches && docker compose up -d --no-build collector ingest taches
docker compose rm -sf backend web && docker image prune -af
docker compose pull backend web && docker compose up -d --no-build backend web
docker compose restart grafana                            # relit la source Tempo, le tableau Traces et les règles
```

## 19/10/2026, vue d'ensemble de Grafana

Provisionnement seulement, aucune image. Tableau « Vue d'ensemble », page d'accueil de Grafana : six tuiles (Collecte,
Ingestion, Disque, Tâches, Satellites, Traces) dont la requête calcule le niveau avec les seuils des alertes
(`parametres_supervision`) et donne une valeur courte, un clic vers le tableau détaillé ; « Dernières traces de bout
en bout » (analyses satellites, lien vers la vue Gantt de Tempo). Grafana est tracé lui même (OTLP gRPC vers
`otel:4317`, réseau Docker seulement). Trace de démonstration : `docs/supervision.md`.

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```bash
cd /opt/mars_c2 && git pull origin main
docker compose up -d grafana        # recrée Grafana : nouvelle page d'accueil et traces de Grafana (variables changées)
docker compose restart otel         # relit otel/collecteur.yaml : récepteur gRPC des traces de Grafana
```

`docker compose restart grafana` ne suffisait pas ici : il garde les anciennes variables d'environnement (page
d'accueil, envoi des traces). Un lot qui ne touche que `grafana/provisioning` se déploie, lui, par
`git pull && docker compose restart grafana`.

## 19/10/2026, réglages de la supervision

Grafana n'est plus tracé (variables retirées, port gRPC du collecteur fermé, filtre `filter/outils` qui écarte tout
span de Grafana, Tempo ou du collecteur, éprouvé) ; mémoire relevée d'après les mesures du serveur passé en DEV1-M
(collecteur 400 Mo et memory_limiter 250, Tempo 448, Grafana 384) ; appels externes nommés par leur opération (« GET
CMR granules VJ102DNB_NRT », « POST STAC sentinel-1-grd », « PUT R2 archive »), service distant dans `peer.service` ;
recherche des granules VIIRS en parallèle, quatre appels à la fois (sur de vraies recherches CMR : 5,5 et 15,3 s en
série, 1,5 et 3,6 s en parallèle, chaque appel reste un span de la tâche) ; indicateur « Disque » de la barre d'état
aligné sur `config/supervision.env` comme la tuile de Grafana (`SEUIL_DISQUE_ORANGE_PTS`).

**Les trois images changent** (API : seuils du disque dans l'état du direct ; scripts : noms des spans, recherche en
parallèle, seuils ; interface : indicateur Disque).

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```zsh
# Sur le Mac : construire, vérifier l'empreinte du commit, éprouver, publier
cd ~/Documents/"Projet Perso"/MarsC2/mars_c2 && git checkout main && git pull origin main
R=ghcr.io/antoine2pommereau; TAG=$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -f backend/Dockerfile --build-arg MARS_VERSION=$TAG -t ${R}/mars_c2-backend:latest . \
  && docker build --platform linux/amd64 -f scripts/Dockerfile --build-arg MARS_VERSION=$TAG -t ${R}/mars_c2-scripts:latest . \
  && docker build --platform linux/amd64 --build-arg MARS_VERSION=$TAG -t ${R}/mars_c2-web:latest web
for i in mars_c2-backend mars_c2-web mars_c2-scripts; do
  v=$(docker image inspect "${R}/${i}:latest" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  [ "$v" = "$TAG" ] && echo "$i : $v" || { echo "$i : empreinte $v, attendue $TAG"; break; }
done
scripts/verifier_images.sh backend scripts web traces && for i in mars_c2-backend mars_c2-web mars_c2-scripts; do
  docker tag "${R}/${i}:latest" "${R}/${i}:${TAG}" && docker push "${R}/${i}:latest" && docker push "${R}/${i}:${TAG}" || break
done
```

```bash
# Sur le serveur
cd /opt/mars_c2 && git pull origin main
docker compose up -d otel tempo grafana        # nouvelles limites, filtre des outils, plus de traces de Grafana
docker compose rm -sf collector ingest taches && docker image prune -af
docker compose pull collector ingest taches && docker compose up -d --no-build collector ingest taches
docker compose rm -sf backend web && docker image prune -af
docker compose pull backend web && docker compose up -d --no-build backend web
```

Vérifier (à l'époque) :

```bash
# Grafana absent de Tempo depuis le déploiement (attendu : 0 trace)
docker compose exec taches python -c "import json, time, urllib.parse as p, urllib.request as u; t = int(time.time()); print(len(json.load(u.urlopen('http://tempo:3200/api/search?' + p.urlencode({'q': '{ resource.service.name = \"grafana\" }', 'start': t - 3600, 'end': t, 'limit': 100})))['traces']), 'trace(s) de Grafana dans la dernière heure')"
# Spans reçus par jour (sans Grafana à partir du jour du déploiement ; environ 45 000 prévus)
docker compose exec db psql -U mars -d mars -c "SELECT date_trunc('day', mesure_le)::date AS jour, sum(greatest(recus - prec, 0)) AS spans FROM (SELECT mesure_le, recus, lag(recus) OVER (ORDER BY mesure_le) AS prec FROM mesures_otel) x GROUP BY 1 ORDER BY 1"
docker stats --no-stream otel tempo grafana    # sous 400, 448 et 384 Mo
```

L'indicateur « Disque » de la barre d'état devait avoir la couleur de la tuile Disque de Grafana (vert à 39 % libres :
rouge sous 15 %, orange jusqu'à 25 %), et l'indicateur « Version » être vert avec l'empreinte publiée. La première
journée complète sans Grafana donne le nombre de spans reçus par jour ; celle du déploiement mélange les deux.

## 19/10/2026, lot C3, Sentinel 1 robuste

Migration 28, règles 2026.10.27, `docs/sentinel.md`. Disponibilité des GPU lue avant toute création (API Instances,
publique) ; rupture de stock à la création ou au démarrage : instance et volume supprimés, travailleur hors plafond et
sans coût, analyse en « attente_gpu » reprise à chaque cycle pendant 24 h après l'acquisition, puis abandon et alerte
ntfy ; types de secours dans l'ordre (L4 fr-par-2, L4 fr-par-1, L40S, RENDER-S P100) et repli sur processeur GP1-S
après 3 h ; **analyse limitée aux fenêtres utiles** (carrés de 24 km autour des points d'intérêt, réunis s'ils se
chevauchent) lues par GDAL en `/vsis3/` (requêtes partielles signées, points d'appui recalés) ; choix du calcul et sa
raison dans l'analyse et la trace ; coût selon le type et les fenêtres ; Grafana : analyses par mode, octets lus,
attente ; `taches.py sentinel-candidats` et plafond d'essai `SENTINEL_PLAFOND_ESSAI` pour la seule commande manuelle.
Essais dans la vraie image d'AI2 : faux S3 qui vérifie la signature AWS version 4 de GDAL, 11,6 % du fichier lu pour
une fenêtre de 24 km sur une scène synthétique de 113 Mo ; chaîne d'AI2 sur une fenêtre et sur la scène entière :
mêmes détections à 6,3 m près, 470 s contre 855 s sur processeur émulé. Pas encore éprouvé contre Copernicus ni sur
une instance réelle ; `scenes_max_par_jour` reste à 0.

Migration 28 (`db/init/28_sentinel_robuste.sql` : état « attente_gpu », mode et motif du calcul, fenêtres, octets lus,
rupture de stock des travailleurs ; reclasse en échec l'analyse 94 de l'essai du 14/10). **Deux images changent :
scripts (taches, mais aussi le script du travailleur, déposé par cloud-init depuis cette image) et API (état de
l'attente pour la barre d'état) ; l'interface aussi (indicateur Satellites).**

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```zsh
# Sur le Mac
cd ~/Documents/"Projet Perso"/MarsC2/mars_c2 && git checkout main && git pull origin main
R=ghcr.io/antoine2pommereau; TAG=$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -f backend/Dockerfile --build-arg MARS_VERSION=$TAG -t ${R}/mars_c2-backend:latest . \
  && docker build --platform linux/amd64 -f scripts/Dockerfile --build-arg MARS_VERSION=$TAG -t ${R}/mars_c2-scripts:latest . \
  && docker build --platform linux/amd64 --build-arg MARS_VERSION=$TAG -t ${R}/mars_c2-web:latest web
for i in mars_c2-backend mars_c2-web mars_c2-scripts; do
  v=$(docker image inspect "${R}/${i}:latest" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  [ "$v" = "$TAG" ] && echo "$i : $v" || { echo "$i : empreinte $v, attendue $TAG"; break; }
done
scripts/verifier_images.sh backend scripts web traces fenetres ai2 && for i in mars_c2-backend mars_c2-web mars_c2-scripts; do
  docker tag "${R}/${i}:latest" "${R}/${i}:${TAG}" && docker push "${R}/${i}:latest" && docker push "${R}/${i}:${TAG}" || break
done
```

```bash
# Sur le serveur
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/28_sentinel_robuste.sql
docker compose rm -sf collector ingest taches && docker image prune -af
docker compose pull collector ingest taches && docker compose up -d --no-build collector ingest taches
docker compose rm -sf backend web && docker image prune -af
docker compose pull backend web && docker compose up -d --no-build backend web
docker compose restart grafana                         # panneaux Sentinel 1, tuile, règle d'abandon
docker compose exec taches python scripts/taches.py sentinel-diagnostic   # disponibilité et prix de chaque type
```

L'essai réel se faisait ensuite pas à pas, plafond relevé pour la seule commande manuelle (`docs/sentinel.md`,
« Essai réel du lot C3 »).

## 19/10/2026, lot C4, GPU inutilisable

Migration 29, règles 2026.10.28, `docs/sentinel.md`. Essai réel du lot C3 (passage 149, analyse 96, travailleur 26)
sur un RENDER-S, `docker run --gpus` en échec, « failed to initialize NVML: Driver Not Loaded » (modules noyau ouverts
de NVIDIA, sans Pascal) : RENDER-S retiré de la liste. Contrôle du GPU par `nvidia-smi` avant l'analyse (étape
`controle_gpu`, attributs du GPU) ; état « gpu_inutilisable » : instance détruite, analyse remise en attente, type
marqué incompatible 7 jours (`types_incompatibles`, une alerte ntfy par marquage, levée par
`taches.py sentinel-lever-incompatible`) ; toute sortie en échec du script de démarrage signale « echec » avec l'étape
et le motif (destruction à la minute suivante, attente si la machine est en cause, échec si c'est l'analyse), filet sur
une instance arrêtée d'elle même ; budget sur le coût réel de chaque travailleur ; `SENTINEL_CALCUL_ESSAI=cpu` pour la
seule commande manuelle (repli GP1-S immédiat) ; sondages de Scaleway en un seul span d'attente. Analyse 96 remise en
échec (passage 149 relançable à la main).

Migration 29 (`db/init/29_gpu_inutilisable.sql` : état « gpu_inutilisable », table `types_incompatibles`, analyse 96
remise en échec, cause NVML du travailleur 26). Images qui changent : scripts (script de démarrage et surveillance des
travailleurs, déposés par cloud-init depuis cette image) et API (états « echec » et « gpu_inutilisable » reçus des
travailleurs) ; l'interface est inchangée. Grafana : nouveau panneau et nouvelle règle (redémarrage).

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```bash
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/29_gpu_inutilisable.sql
docker compose rm -sf collector ingest taches && docker image prune -af
docker compose pull collector ingest taches && docker compose up -d --no-build collector ingest taches
docker compose rm -sf backend && docker image prune -af
docker compose pull backend && docker compose up -d --no-build backend
docker compose restart grafana
docker compose exec taches python scripts/taches.py sentinel-diagnostic
```

## 19/10/2026, lot C5, statistiques sans trou et processeur en premier

Migration 30, règles 2026.10.29, `docs/supervision.md` et `docs/sentinel.md`. Trou de `stats_minute` du 17/10 22:46 au
19/10 06:00 (31 h), positions intactes : les statistiques n'étaient tenues qu'à la fin d'un cycle des règles (en échec
pendant le disque plein) et sur 3 h, les positions chargées en retard jamais comptées. Désormais étape à part avant les
règles, fichiers chargés en retard recomptés (`stats_suivi`), garde toutes les 10 min (plus de 10 minutes absentes sur
24 h : reconstruction, `task_runs` `stats_reconstruction`, alerte Grafana), commandes `stats-reconstruire DEBUT FIN`
(idempotente), `stats-trous`, `sentinel-flux ID`, `viirs-reevaluer DEBUT FIN`. Premier essai Sentinel 1 abouti
(analyse 97, GP1-S, 1,7 % de la scène lue, 119 s d'inférence par fenêtre, 6 min 15 s, 0,19 €). Point de déclenchement
d'un navire des listes en mer seulement (2 km de la côte, ou mouillage hors port ; « à quai, invisible au radar »).
Processeur en premier : 4 fenêtres au plus sur GP1-S (POP2-8C-32G en secours), au delà GPU, en rupture de GPU
processeur par lots ; durée de vie calculée (5 + 3 par fenêtre + 15 min) ; `SENTINEL_CALCUL_ESSAI=gpu`. Facturation par
type (minute pour L4 et L40S, heure entamée sinon : essai 26 facturé 1,23 € pour 0,28 € estimés), coût réel à la
destruction, garde des types horaires de plus de 0,50 €/h jamais éprouvés.

Migration 30 (`db/init/30_stats_processeur.sql` : `stats_suivi`, `analyses.fenetres_faites`, durée de vie, mode de
facturation, coûts prévu et réel des travailleurs, 1,23 € facturés reportés sur le travailleur 26). Images qui
changent : scripts (taches : statistiques, garde, commandes, stratégie de calcul ; script du travailleur Sentinel
déposé par cloud-init) et API (modules partagés `mars/`). Interface inchangée. Grafana : deux tuiles dans Direct,
colonnes de coût dans Satellites, une règle d'alerte (redémarrage).

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```bash
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/30_stats_processeur.sql
docker compose rm -sf collector ingest taches && docker image prune -af
docker compose pull collector ingest taches && docker compose up -d --no-build collector ingest taches
docker compose rm -sf backend && docker image prune -af
docker compose pull backend && docker compose up -d --no-build backend
docker compose restart grafana
docker compose exec taches python scripts/taches.py stats-reconstruire 2026-10-17T22:00 2026-10-19T07:00
docker compose exec taches python scripts/taches.py sentinel-flux 97
docker compose exec taches python scripts/taches.py viirs-reevaluer 2026-10-17T22:00 2026-10-19T07:00
docker compose exec taches python scripts/taches.py stats-trous 2026-10-06
docker compose exec taches python scripts/taches.py sentinel-candidats 72
docker compose exec taches python scripts/taches.py sentinel-diagnostic
```

## 19/10/2026, Sentinel 1 automatique

Règles 2026.10.30. Preuves du lot C5 faites sur le serveur, `scenes_max_par_jour` à 2, budget de 6 € par mois ;
`stats-trous` qualifie de vrai arrêt du flux un trou sous 5 % du flux habituel.

## 20/10/2026, lot T1, tip and cue

Migration 31, règles 2026.10.31, `docs/tip_and_cue.md`. Un signal suspect (VIIRS moyenne au moins, coupure élevée près
d'une infrastructure même en observation, rendez vous au large, navire des listes près d'une infrastructure,
changement d'identité moyen, demande manuelle) crée une demande d'observation (`demandes_observation`, origines
regroupées par navire ou par zone, expiration 36 h) ; position prédite à l'heure de chaque passage Sentinel 1
(dernière position reçue, sinon estime ; cercle de 2 km plus 30 % du parcours, 30 km au plus ; 1,5 km à l'arrêt, 3 km
sans navire), fenêtres de 24 km (4 au plus), point à moins de 2 km de la côte écarté ; passage acquis dans les 6 h,
sinon le prochain prévu ; points « tip_and_cue » dans la sélection Sentinel (même session, plafonds et budget ;
demandes d'abord dans le plafond) ; résultat (présent apparié, présent sans AIS, absent, non évaluable) en preuve de
l'alerte, gravité ajustée par table, jamais de retrait, ntfy si relevée à élevée ; vérification gardée par les mises à
jour en place des règles (`GARDE_VERIFICATION`). Liens de spans entre alerte, demande et analyse
(`traces.span(liens=…)`). Livré coupé. Rejeu sur le serveur (7 jours, API) : 7 demandes avec passage sur 43 tips,
6 sessions, environ 4,90 € par mois ; navires des listes en transit presque jamais observables.

Migration 31 (`db/init/31_tip_and_cue.sql` : tables `demandes_observation` et `demandes_origines`, preuve « demande »
des alertes) ; `tip_and_cue.actif: false`. Images qui changent : API (routes des demandes, passages), scripts (cycle,
sélection Sentinel, commande `tip-rejeu`) et interface (section, bouton, clic droit, frise, indicateur). Grafana :
section « Tip and cue » du tableau Satellites (redémarrage). La migration passait avant l'API (ses routes lisent les
nouvelles tables).

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```bash
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/31_tip_and_cue.sql
docker compose rm -sf collector ingest taches && docker image prune -af
docker compose pull collector ingest taches && docker compose up -d --no-build collector ingest taches
docker compose rm -sf backend && docker image prune -af
docker compose pull backend && docker compose up -d --no-build backend
docker compose rm -sf web && docker image prune -af
docker compose pull web && docker compose up -d --no-build web
docker compose restart grafana
docker compose exec taches python scripts/taches.py tip-rejeu 7
```

## 20/10/2026, tip and cue activé, panneau Observations

Règles 2026.10.32, aucune migration. Origines actives : coupure_ais, viirs (12 h au plus entre la lumière et le
passage), rendezvous, identite et demande manuelle ; watchlist coupé ; budget Sentinel 8 € par mois. Panneau
« Observations » du rail (`/api/observations` : ordre planifiées, en attente, analysées, closes repliées ; région et
plage ; cercle d'incertitude dessiné au clic ; compteur des actives).

`config/rules.yaml` est relu à chaque tour de taches : l'activation prenait effet au `git pull`, avant même les
nouvelles images. Images qui changent : API (route `/api/observations`), scripts (délai VIIRS, rejeu) et interface
(panneau Observations).

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```bash
cd /opt/mars_c2 && git pull origin main
docker compose rm -sf collector ingest taches && docker image prune -af
docker compose pull collector ingest taches && docker compose up -d --no-build collector ingest taches
docker compose rm -sf backend && docker image prune -af
docker compose pull backend && docker compose up -d --no-build backend
docker compose rm -sf web && docker image prune -af
docker compose pull web && docker compose up -d --no-build web
docker compose restart grafana
docker compose exec taches python scripts/taches.py tip-rejeu 7
```

## 20/10/2026, navires muets en transit

Règles 2026.10.33, aucune migration. Une demande n'attend jamais sans motif ; estime d'un navire muet bornée à 6 h et
à nos zones (au delà, « navire en transit, hors de portée au prochain passage »), muet lent sous 3 nœuds à position
fixe ; pêcheurs et coupures déclassées écartés du tip coupure_ais ; motif au survol dans le panneau Observations.

Seule l'image des scripts changeait (planification, filtres, rejeu) et celle de l'interface (motif au survol). Les
demandes en attente étaient replanifiées au premier cycle de la nouvelle image (5 minutes au plus).

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```bash
cd /opt/mars_c2 && git pull origin main
docker compose rm -sf collector ingest taches && docker image prune -af
docker compose pull collector ingest taches && docker compose up -d --no-build collector ingest taches
docker compose rm -sf web && docker image prune -af
docker compose pull web && docker compose up -d --no-build web
```

## 20/10/2026, lot S2, mesures

`docs/sentinel2.md`, aucun code de production. Toute notre mer acquise par Sentinel 2, large compris (un passage tous
les 1,7 jours en Atlantique, 3,3 en Méditerranée, entre 10:00 et 11:30 UTC) ; 0,8 passage dégagé par semaine et par
point au large en Atlantique et Manche, 1,15 en Méditerranée (SCL de la fenêtre) ; annonce du catalogue utile comme
premier tri seulement ; JP2 sur eodata, 47 Mo lus par fenêtre (15 %). Détecteur Sentinel 2 absent de l'image d'AI2
épinglée et de toutes ses étiquettes ; modèle rslearn d'AI2 essayé (L1C, 13 bandes, longueurs à 4 % près sur quatre
navires de l'AIS sur cinq), `verifier_images.sh s2`.

## 20/10/2026, lot S2, Sentinel 2 construit et livré coupé

Migration 32, règles 2026.10.34, `docs/sentinel2.md`. Modèle rslearn d'AI2 épinglé par empreinte, poids sur R2
(licence Apache 2.0, 22 % du quota gratuit, dépassement vers le 28/07/2027) ; travailleur GP1-S
(`travailleurs/sentinel2.py`) : SCL du L2A par fenêtre (close « nuages » au delà de 20 %), 13 bandes du L1C,
inférence, vignettes en couleurs réelles de 2 et 10 km ; heure propre des tuiles au calendrier et dans la fusion (sans
Doppler) ; garde des nuages annoncés (50 %), masque attendu 3 h puis « masque absent » ; navire sous un nuage écarté,
aucune absence déclarée sous les nuages ; alertes de source `sentinel2` ; plafond propre, budget partagé avec
Sentinel 1 ; tip and cue sur les passages optiques ; couche, fiche optique, vérification satellite, frise,
Observations, Grafana. Essai réel pas à pas : `docs/sentinel2.md`, section 11.

Livré coupé (`sentinel2.scenes_max_par_jour: 0`) : rien n'était lancé automatiquement. La migration passait **avant**
l'API (colonne `role` des preuves lue par toutes les fiches de détection). Les trois images changeaient : API (fiche
optique, remise des poids, passages), scripts (calendrier, sélection, fusion, travailleur) et interface.

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```bash
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -U mars -d mars < db/init/32_sentinel2.sql
docker compose rm -sf collector ingest taches && docker image prune -af
docker compose pull collector ingest taches && docker compose up -d --no-build collector ingest taches
docker compose rm -sf backend && docker image prune -af
docker compose pull backend && docker compose up -d --no-build backend
docker compose rm -sf web && docker image prune -af
docker compose pull web && docker compose up -d --no-build web
docker compose restart grafana
docker compose exec taches python scripts/taches.py sentinel2-poids      # une fois : 2,24 Go en flux, seau des modèles
docker compose exec taches python scripts/taches.py sentinel2-heures 3   # heure propre des tuiles des trois derniers jours
```

Le travailleur Sentinel 2 utilise la paire Copernicus des travailleurs et le seau des modèles (adresses présignées des
poids, 3 h). L'image d'analyse (7,2 Go) n'est téléchargée que par le travailleur, jamais par le serveur. Essai réel :
`docs/sentinel2.md`, section 11.

## 20/10/2026, premier essai réel Sentinel 2 et seau des modèles

Règles 2026.10.35, aucune migration. Passage 325, analyse 98, travailleur 29 : réseau privé, Docker et image rslearn
(7,2 Go en 4 min 23 s) fonctionnent, échec 403 AccessDenied au téléchargement des poids (jeton R2 principal filtré sur
l'IP du serveur). Poids dans un seau dédié `mars-c2-modeles` à jeton sans filtre IP (`R2_MODELES_*`), seul à présigner
pour un travailleur ; `sentinel2-poids` copie et `--nettoyer` retire l'ancienne copie ; contrôle d'un octet de chaque
adresse R2 dès le réseau privé joint, avant Docker (étape `controle_r2`, état « acces ») ; `sentinel2-diagnostic`
(variables dans taches et dans backend, seau, poids, adresse présignée). Mise en place du seau :
`docs/deploiement.md`, « Seau des modèles ». À l'époque, tout le `.env` arrivait dans les conteneurs backend et taches
par `env_file` ; depuis le lot Z1 (22/10), chaque service reçoit ses seules variables.

## 21/10/2026, lot P, panneau de contrôle

Migrations 33 à 37, règles 2026.10.36 à 2026.10.39, `docs/controle.md`. Icône « Contrôle » du rail ; état en base qui
prime sur `config/rules.yaml` (`mars/controle.appliquer`), seuils jamais édités dans l'interface ; chaque action dans
`journal_controle` ; écriture réservée aux opérateurs connectés (`MARS_OPERATEURS`, mot de passe haché, cookie HttpOnly
SameSite strict, 12 h).

* Phase A (migration 33, règles 2026.10.36) : interrupteurs Sentinel 1, Sentinel 2, VIIRS et tip and cue (aucun
  travailleur en pause, commande manuelle refusée sauf `--malgre-pause`, reprise prévue et notification la veille,
  `taches.py satellites-pause`), plafond quotidien réglable de 0 à 4, budget du mois, demande d'observation d'un
  point, Grafana muette en pause.
* Phase B : section Système (services, version, signe de vie, mémoire du conteneur lue dans son cgroup, retard,
  disque, échecs avec lien vers la trace, lien vers Grafana).
* Phase C (migration 34, règles 2026.10.37) : mode actif, observation ou coupé par règle et sous type, en base, prime
  sur le fichier (`ais_gap.mode` compris), aucune alerte supprimée, épisodes observés comptés, empreinte de l'état du
  panneau dans chaque alerte (`alerts.controle_empreinte`).
* Phase D (migration 35, règles 2026.10.38) : préférences de notification par gravité et type d'alerte, tip and cue,
  catégories système, heures calmes de Paris avec résumé au réveil, appliquées à taches, à l'API et à Grafana
  (`notification_permise`), notification de test.
* Phase E (migration 36, règles 2026.10.39) : zones de vigilance (polygone dessiné ou tampon d'infrastructure, profils
  du fichier : gravité minimale, seuils abaissés dans la zone, priorité tip and cue et Sentinel), zone et profil
  inscrits dans l'alerte, couche et fiche, priorité aux navires suivis.
* Phase F (migration 37) : rejeu tel qu'il était (alertes et demandes à leur état de l'instant, lecture seule),
  scénarios enregistrés, masquage des navires par l'API sous un cookie de session (pseudonymes stables, photos et GPX
  refusés, bandeau « démonstration »).

Déploiement : l'état du panneau est en base et prime sur `config/rules.yaml` : on ne modifie plus ce fichier sur le
serveur. Le dossier `config` est monté dans taches : dès que le fichier revenait à sa version du dépôt, l'ancien
taches relisait le plafond de Sentinel 1 (2 scènes par jour). D'où l'ordre : taches arrêté avec le retour du fichier,
pauses posées par un conteneur jetable de la nouvelle image avant le démarrage de la boucle.

> Historique, ne plus suivre : voir `docs/exploitation.md`.

```bash
cd /opt/mars_c2 && git checkout config/rules.yaml && docker compose stop taches
git pull origin main
for f in 33_controle 34_modes_regles 35_notifications 36_zones_vigilance 37_scenarios; do
  docker compose exec -T db psql -U mars -d mars -v ON_ERROR_STOP=1 < db/init/$f.sql || break; done
docker compose rm -sf collector ingest taches && docker image prune -af
docker compose pull collector ingest taches
docker compose run --rm --no-deps taches python scripts/taches.py satellites-pause on sentinel1 --motif "maîtrise des coûts"
docker compose run --rm --no-deps taches python scripts/taches.py satellites-pause on sentinel2 --motif "essai du passage 325 en attente"
docker compose up -d --no-build collector ingest taches
docker compose run --rm --no-deps taches python scripts/taches.py operateur-hacher antoine   # saisie masquée
nano .env                                    # coller la ligne MARS_OPERATEURS=antoine:pbkdf2_sha256:… à la fin
docker compose rm -sf backend && docker image prune -af
docker compose pull backend && docker compose up -d --no-build backend
docker compose rm -sf web && docker image prune -af
docker compose pull web && docker compose up -d --no-build web
docker compose restart grafana                 # règles (notification_permise) et tableaux Satellites
```

Une pause posée par le fichier (plafond à 0 modifié à la main) était remplacée par `satellites-pause` ; VIIRS restait
en service (environ 0,76 € par mois). La première connexion se faisait dans l'interface, icône « Contrôle » du rail.

## 21/10/2026, lot U, refonte UX, phase 1

`docs/ux_refonte.md`, maquettes `docs/ux/maquettes/`. Audit mesuré sur la base de test (captures sans affichage,
navires masqués) : 231 mots et 10 couleurs vives sur l'écran par défaut, navire le plus suspect éclaté en trois lignes
du fil ; heuristiques de Nielsen ; trois tâches de référence (20 s, 41 s, 6 s par alerte) ; cible : dossier par
navire, trois niveaux d'urgence, phrase d'explication, Veille, Enquête, Coulisses, tri au clavier ; prototype du modèle
des dossiers (`docs/ux/maquettes/dossiers.py`).

## 21/10/2026, lot A, correctifs

Aucune migration, aucune règle changée. Trafic visible en mode plage et rejeu (interface), origine vérifiée derrière
nginx (hôte du navigateur transmis par nginx, connexion des opérateurs), `operateur-hacher` et lignes de
`MARS_OPERATEURS` contrôlées (ligne imprimée sans suffixe de trace et contrôlée à la lecture), libellé de la couche
« Zones de vigilance », bandeau de démonstration sous les détails des voyants.

Le correctif à chaud de nginx sur le serveur (« proxy_set_header Host $http_host; ») était remplacé par l'image web :
rien à garder à la main. Une ligne MARS_OPERATEURS copiée avec « trace_id=… » se refait : `taches.py operateur-hacher
<nom>` (la nouvelle ligne n'a plus de suffixe), puis `taches.py controle-diagnostic` doit dire PRÊT pour taches et
backend.

## 21/10/2026, lot U, phase 2, refonte de l'interface

Règles 2026.10.42, aucune migration ; `docs/ux_refonte.md`, section 11. Dossiers calculés à la lecture
(`mars/dossiers.py`, `backend/dossiers.py` : liste, récit, décision journalisée alerte par alerte, devis de
vérification, `/api/decisions/mesures`), Veille, Enquête, clavier, Coulisses (section 7). Mesures sur le jeu d'essai :
écran par défaut de 231 à 149 mots et de 10 à 5 couleurs vives, fiche du navire suspect de 621 à 297 mots ; navire le
plus suspect trouvé sans geste (environ 7 s au lieu de 20, réponse juste), compris par la phrase de sa ligne, classé
en deux touches pour tout le dossier (au lieu de 6 s par alerte).

Déploiement : section `dossiers` de `config/rules.yaml`, lue par l'API à chaque requête, arrivée par `git pull`. Images
qui changeaient : backend (`backend/dossiers.py`, `mars/dossiers.py`) et web ; scripts seulement si le lot A n'avait
pas encore été publié (`mars/controle.py`, `mars/traces.py`, `operateur-hacher`).

Vérifier après déploiement, par le tunnel, en lecture seule :

```bash
curl -s "localhost:8000/api/dossiers?end=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | python3 -c "import json, sys; d = json.load(sys.stdin); print(d['compteurs'], len(d['dossiers']))"
curl -s "localhost:8000/api/decisions/mesures?jours=30"
```

Ce qu'on devait lire : des compteurs `urgent`, `a_examiner`, `a_noter` (les alertes ouvertes des 72 dernières heures,
regroupées par navire) ; les mesures par type d'alerte (vide tant qu'aucune décision n'a été prise). Dans
l'interface : la Veille s'ouvre par défaut, la barre d'état n'a plus que le voyant Système, les anciens panneaux sont
dans les Coulisses ; une adresse avec `vue=trafic` rouvre la vue d'avant la refonte.

## 21/10/2026, séjour hors port et cadrage de la Veille

Règles 2026.10.43, aucune migration. Le séjour prolongé d'un navire des listes mesure aussi sa part à l'arrêt hors port
(`arret_hors_port_part`, à plus de `watchlist.hors_port_m`, 2 km, de la côte ; `stay_offshore` dans `mars/rules.py`) ;
il ne rend le dossier « à examiner » qu'à partir de `dossiers.sejour_hors_port_part` (0,2). Mesuré sur le serveur
(lecture seule) : sur 30 jours, 18 séjours prolongés, 15 à 0 (transits de 26 à 37 h, escales à quai), NOSTOS 0,09,
SAFEEN BARONESS 0,33, SMYRTOS 1 ; 16 redescendent à « à noter ». Sur les 72 h du 21/10, 17 dossiers, un seul change
(VICTOR ANDRYUKHIN, à quai à Marseille) : 0 à examiner au lieu de 1. Une alerte écrite avant cette version n'a pas la
mesure : séjour noté « non mesuré », sans relever le niveau. La Veille cadre la carte sur les dossiers à examiner à son
ouverture (`lib/cadre.ts`).

Déploiement : règles arrivées par `git pull` (`watchlist.hors_port_m`, `dossiers.sejour_hors_port_part`). Images qui
changeaient : scripts (mesure dans le moteur, conteneur taches), backend (niveau des dossiers), web (cadrage). La
mesure apparaît dans les alertes WATCHLIST au premier cycle des règles (5 minutes) pour les passages en cours ; les
plus anciens restent « séjour non mesuré » et sortent de la fenêtre de 72 h en trois jours.

```bash
docker compose exec -T db psql -U mars -d mars -c "SELECT id, details->'navire'->>'name' AS navire,
  details->'sejour'->>'duree_h' AS h, details->'sejour'->>'arret_hors_port_part' AS hors_port, rule_version
  FROM alerts WHERE type = 'WATCHLIST' AND details->'sejour' IS NOT NULL AND detected_at > now() - interval '3 days'"
```

Ce qu'on devait lire : `rule_version` à 2026.10.43 et `hors_port` renseigné pour les passages en cours (0 pour une
escale à quai comme VICTOR ANDRYUKHIN à Marseille).

## 21/10/2026, Coulisses en rubriques et panneaux redimensionnables

Interface seule (image web) : aucune route, aucune migration, aucune règle. Les écritures des rubriques passent par les
routes du contrôle d'avant (connexion de l'opérateur, journal). Vérifier par le tunnel : ouvrir
`http://localhost:8080/?espace=coulisses&rubrique=notifications`, la rubrique Notifications seule s'affiche ; le retour
arrière du navigateur revient à la rubrique précédente ; la poignée du panneau de gauche l'élargit, la carte suit.

## 21/10/2026, Coulisses en quatre groupes, sans accueil

Images qui changeaient : backend (route en lecture `/api/decisions/journal`) et web. Aucune migration, aucune règle.
Vérifier par le tunnel : `curl -s "localhost:8000/api/decisions/journal?limite=3"` renvoie les dernières décisions des
opérateurs ; `http://localhost:8080/?espace=coulisses&rubrique=capteurs` est redirigé vers Sources et capteurs.

## 22/10/2026, lot T, prévision de trajectoire, phase 1

`docs/trajectoire.md`, aucune construction de modèle avant validation. Préparation et références
(`mars/trajectoire.py` : découpage au silence de 2 h, à l'arrêt, au changement de destination et au saut durable, pas
de 10 minutes, côte à un mille ; estime, estime mesurée, routes fréquentes), mesure de l'archive R2 par
`taches.py trajectoire-mesures` (lecture seule, conteneur à part). Pré mesure sur 340 navires lus en GET : cargos et
pétroliers, routes fréquentes à 1,1, 3,8 et 10,4 km d'erreur médiane à 1, 2 et 3 h ; cercle actuel de la vérification
satellite à 52 % de couverture à 3 h ; 13 des 20 demandes de vérification satellite sans passage parce que le passage
tombait 13 à 31 h après la dernière position.

Image qui changeait : scripts (commande `trajectoire-mesures`, modules `mars/trajectoire*.py`). Aucune migration,
aucune règle. La mesure lit l'archive R2 et la base sans rien écrire (rapport JSON dans `data/ais_live/.trajectoire`) ;
elle tourne dans un conteneur à part, pour ne jamais priver la boucle des tâches de ses 768 Mo :

```bash
cd /opt/mars_c2 || { echo "PAS SUR LE SERVEUR : arrêt"; exit 1; }
docker compose run --rm --no-deps taches python scripts/taches.py trajectoire-mesures
ls -la data/ais_live/.trajectoire/
```

Ce qu'on doit lire : une ligne par zone (positions réduites, trajets, fenêtres, mémoire), puis « Mesures écrites dans
… », mémoire maximale sous 600 Mo. En cas de manque de mémoire, une zone à la fois : `trajectoire-mesures manche`.

## 22/10/2026, cercle appris de la vérification satellite

Règles 2026.10.44, `docs/trajectoire.md` section 11. Le cercle de la vérification satellite n'est plus 2 km plus 30 %
du parcours (gardé en repli) mais le rayon qui a contenu 80 % des positions réelles autour de l'estime, par tranche de
vitesse et par horizon (`tip_and_cue.rayon_appris`), plafonné au rayon que couvrent les 4 fenêtres permises (20 km,
`fenetres_demande`). Pré mesure du 17 au 22/10 : navire trouvé 78, 74, 63, 40, 29 et 22 % du temps à 1 à 6 h, contre
63, 44, 19, 9, 4 et 2 %. Table provisoire, à remplacer par la mesure complète.

Déploiement : table `tip_and_cue.prediction.cercle_appris` par `git pull` ; image des scripts (le conteneur taches
planifie les demandes). Aucune migration. La commande `trajectoire-mesures` calcule maintenant la table sur toute
l'archive (section `cercle_appris` du rapport) : ses valeurs remplaceront la table provisoire. Vérifier après un cycle
de la vérification satellite (5 minutes) :

```bash
docker compose exec -T db psql -U mars -d mars -c "SELECT id, etat, round(rayon_m) AS rayon_m, fenetres IS NOT NULL AS fenetres,
  left(motif, 90) AS motif FROM demandes_observation WHERE mis_a_jour_le > now() - interval '1 hour' ORDER BY id DESC LIMIT 10"
```

Ce qu'on devait lire : des rayons de 20 000 m au plus pour les demandes planifiées en estime, et plus de motif
« incertitude de … km : N fenêtres, au delà de 4 ».

## 22/10/2026, garde de probabilité et table de l'archive

Règles 2026.10.45, migration 38, `docs/trajectoire.md` section 12. Table des quantiles de l'erreur de l'estime de 1 à
30 h (vérité : la position où le navire réapparaît), rayon au quantile 80 % plafonné aux fenêtres, contenance estimée
écrite dans la demande, la trace et l'interface ; un passage n'est retenu qu'à 40 % de contenance au moins (le plus
proche au dessus du seuil) ; estime bornée à 30 h. Avec l'estime seule, plus aucun passage retenu au delà d'environ
4 h.

Déploiement de la garde : migration 38 (colonne `contenance` de `demandes_observation`), règles 2026.10.45 (table des
quantiles, seuil de 40 %, estime bornée à 30 h). Images : scripts (planification), backend (colonne renvoyée), web
(liste et fiche). Les demandes planifiées portent leur contenance ; une demande dont aucun passage n'atteint le seuil
passe « sans passage » avec le motif « contenance estimée de N % au passage du …, sous le seuil de 40 % ».

**Table de l'archive** en 2026.10.46 (étalonnage juste jusqu'à 8 h) : table du cercle appris tirée de l'archive,
arrivée par `git pull`, relue par la vérification satellite au cycle suivant. Image des scripts : option
`--hypotheses` de `trajectoire-mesures`, rapports nommés par type et par lancement
(`trajectoire_<type>_<du>_<au>_<instant>.json`). Aucune migration.

Étape 2 mesurée hors production (`docs/trajectoire.md` section 13) : routes fréquentes en plusieurs hypothèses
(analogues, sans le même navire, garde sur la borne basse de Wilson) ; pré mesure : utiles de 12 à 30 h pour les
navires aux routes régulières, pas encore pour les cargos et pétroliers (`trajectoire-mesures --hypotheses` sur
l'archive pour trancher).

Données externes retenues (DMA, GeoTrackNet Bretagne, École navale) : usage personnel et non commercial, aucune
redistribution, ni des données ni d'un jeu dérivé ni des poids (section 10).

## 22/10/2026, lots Z1 et Z2, sécurité et exploitation

Migrations 39 à 42, règles 2026.10.47 ; guide : `docs/exploitation.md`. Toute écriture de l'API exige une session
d'opérateur, du JSON et la même origine, auteur tiré de la session (liste blanche `ECRITURES_LIBRES`, test qui parcourt
toutes les routes) ; l'interface ouvre la connexion sur un refus 401 puis reprend l'action ; démonstration en lecture
seule. Un environnement par service (plus aucun `env_file: .env`), confronté au code par
`tests/test_environnement.py`. FastAPI 0.159.1 et Starlette 1.9.0 ; verrous `requirements.lock` avec empreintes ;
`npm ci` ; nginx 1.28, noms du tunnel seulement, en têtes de sécurité, `X-Real-IP` ; tentatives de connexion en base.
Jeton Earthdata remis par le réseau privé ; groupe de sécurité `mars-travailleurs` ; résultats des travailleurs
validés contre leur tâche (`mars/validation.py`). Échéances des secrets (`mars/echeances.py`, Coulisses, Système).
Base et API du Mac sur 127.0.0.1. Exploitation : `scripts/publier.sh`, `scripts/deployer.sh`, registre
`schema_migrations`, essai de restauration mensuel (`mars/restauration.py`, `scripts/essai_restauration.sh`),
`pg_stat_statements`, `pytest-timeout`. Règle Grafana « Tâche en échec plusieurs fois de suite » (fausse alerte
permanente du 20/10, travailleur_sentinel2 avec Sentinel 2 en pause) : fonction SQL `taches_en_echec_suite`
(migration 42), série dont le dernier échec a moins de 24 h, source en pause tue. Défaut corrigé au passage : le filtre
des journaux (`mars/traces.py`) vidait `record.args`, le journal d'accès d'uvicorn levait une erreur à chaque requête
tracée (déjà présent en production).

---

## Points ouverts France

Tels que notés dans la section 9 de `CLAUDE.md` (rédigés vers le 19/10/2026) : masques France à construire sur le
serveur (`docker compose exec taches python scripts/build_masks.py --sans-cache --jours 7` : 61 s, 300 Mo de mémoire,
142 Mo de disque au plus, mesurés) ; recalibration des seuils après une à deux semaines de mesures (liste et méthode :
`docs/audit_code.md`, section 3). Mesure déjà faite sur données synthétiques : à un point toutes les 3 minutes,
`reception.min_pairs` (200, valeur danoise) écarte un tiers des cellules d'un rail de 30 navires, et le test par
injection n'a plus de candidat ; à un point toutes les 2 minutes, 5 sur 5. Le test par injection indique désormais
combien de candidats il écarte, et pourquoi.

## Décomptes de tests par lot

Anciens décomptes, repris de la section « 10. Tests » de `CLAUDE.md` ; le décompte courant est dans `CLAUDE.md`.

**22/10/2026, lots Z1 et Z2** : 480 réussis, 1 ignoré avec la base de test ; sans base, 358 réussis, 123 ignorés ;
chaque test borné à 60 s.

**Lot U et séjour hors port (21/10/2026)** (non daté dans `CLAUDE.md`) : 406 réussis, 1 ignoré avec la base de test ; 293 réussis, 114 ignorés
sans.

**Lot T1 (20/10/2026)** : 244 réussis, 65 ignorés sans base ; 308 réussis, 1 ignoré avec la base de test.

**Décompte antérieur, lot C4 (19/10/2026, date déduite du lot le plus récent cité)** : `python -m pytest tests` : 227 réussis, 46 ignorés : 1 sans
`MARS_TEST_MODEL=1` (contrat du modèle), 45 sans `MARS_TEST_DATABASE_URL` (travailleurs sur une vraie base : verrou
contre les lancements concurrents, échec de lancement, délai de démarrage, résultats reçus deux fois, travailleur
arrêté par des données inaccessibles, refus sans trace ; origine des positions écrite par l'ingestion ; séjour prolongé
au delà de la fenêtre des règles). Avec la base de test des migrations
(`MARS_TEST_DATABASE_URL=postgresql://mars:mars@localhost:55432/mars`) : 272 réussis, 1 ignoré (lot C4 : GPU
inutilisable, échec signalé, instance arrêtée, essai forcé sur processeur ; script de démarrage exécuté avec de fausses
commandes, `tests/test_demarrage.py`, avec `gdate` ; lot C3 : rupture de stock, attente, reprise, repli sur
processeur, abandon, plafond de l'essai manuel, reclassement des analyses sans scène aboutie ; identifiant de trace des
alertes par le déclencheur ; rôle grafana_lecture et requêtes des tableaux sous la seconde, redémarrage consigné,
mesures du disque ; volumes bloc nommés au journal de destruction ; remise unique de la tâche et des clés dédiées sur
jeton, cloud-init Sentinel sans scène ni clé ; trous de collecte sans effet sur les coupures, essai Sentinel à la main
avant la nuit VIIRS ; plafond et place réservée de VIIRS, report d'un échec de lancement Sentinel, diagnostic en
lecture seule, route des coupures et points collectifs ; zone de réception fiable qui décide de l'évaluation des
détections ; chaîne Sentinel 1 complète avec de faux clients Copernicus et Scaleway).

Ce que couvrent les tests (texte de la section 10 à cette date) : aussi fusion, direct, archivage, règles en continu,
vérification R2 avec un faux client S3, frise, garde de destruction, ordre de création d'un travailleur, résolution de
l'image et vérification du volume de démarrage. `tests/test_images.py` : modules copiés par chaque Dockerfile, sans
Docker. `tests/test_traces.py` : chaque commande de `taches.py` et chaque type de travailleur tracés (échoue sinon),
filtre des données sensibles, script de démarrage bash (avec `gdate` sur le Mac). `npx knip` et `npm run typecheck`
pour l'interface ; `npm test` (node --test, `web/tests/` : trafic, preuve, clavier, état dégradé, cadrage, largeur,
recherche des réglages ; 22 réussis) ; en développement, `MARS_API=http://localhost:8765 npm run dev` relaie une autre
API que le port 8000.

## 24/10/2026, lot Z4, étape 1 : retraits sûrs

Migration 43 (`sim_clock` et `sim_now()` retirées, aucune autre table touchée). Retirés : horloge partagée
(`GET` et `POST /api/clock`, champ `clock` du flux SSE, que l'interface ne lisait plus), routes sans client
(`/api/ais/days`, `/api/watchlist`, `GET /api/demandes/{id}`, `/api/metrics` : Grafana lit la base), commandes
ponctuelles finies (`sentinel-flux`, `sentinel-non-confirmees`), six objets morts et quatre servant seulement à leur
test (`docs/bilan.md`, section 2.1), 25 libellés orphelins, types exportés pour rien (`Clock`, `Legende`, `Objet`).
Le flux SSE prend toujours l'heure réelle ; son paramètre `direct` reste accepté pour les anciens onglets.
L'essai de restauration soustrait désormais les tables retirées par une migration (DROP TABLE) de celles qu'il attend.

## 24/10/2026, lot Z4, étape 2 : chaîne radar du Mac retirée

Règles 2026.10.48. **Pour la retrouver : commit `331298b97ea87d0236fcf40b6d2747557af424e2`**, le dernier qui la contient
(`git show 331298b:inference/app.py`, `git checkout 331298b -- inference mars/sar scripts/analyze_zone.py`). Retirés :
service d'inférence (`inference/`, profil Compose `conteneur`), extraction Sentinel Hub et CircleNet (`mars/sar/`),
`models/`, scripts `analyze_zone`, `sweep_threshold`, `run_rules`, `list_passes`, `tests/test_contract.py`, le paquet
`torch` ; dans l'API, la recherche des passages, le lancement et le suivi d'une analyse, la vignette `/api/chip`, l'état
de l'inférence et les règles à la demande (`/api/rules/...`, remplacées par `taches.py regles` et `coupures-injection`) ;
le champ `analyses` du flux SSE ; dans l'interface, l'outil de tracé, le lancement, la vignette Sentinel Hub et leurs
libellés ; les clés `model` et `contrast` propres à CircleNet (restent `model.thresholds.vessel` et
`contrast.min_vv_db`, lus par la fusion). Variables `INFERENCE_URL`, `SH_CLIENT_ID`, `SH_CLIENT_SECRET` retirées de
Compose et de `.env.example`. Les analyses passées restent lisibles (`/api/analyses`, détections, alertes).

## 24/10/2026, lot Z4, étape 3 : voie GPU de Sentinel 1 retirée

Règles 2026.10.49, aucune migration. Sentinel 1 tourne sur processeur seulement : GP1-S en fr-par-2 (type de base),
POP2-8C-32G en secours, quatre fenêtres par session et lots de quatre au delà ; un passage choisi à la main sans aucun
point d'intérêt (scène entière) est refusé avec son motif, sans appel à Scaleway. Retirés : types L4-1-24G et
L40S-1-48G, image GPU OS, `--gpus`, contrôle du GPU au démarrage (`controle_gpu`, état « gpu_inutilisable »),
marquage des types incompatibles et sa levée (`sentinel-lever-incompatible`), garde de dépense des types non éprouvés
(`prix_max_essai_eur`, qui ne pouvait rien refuser), choix GPU ou processeur (`strategie`, `repli_cpu_apres_h`),
essai forcé `SENTINEL_CALCUL_ESSAI`, coût GPU (`minutes_fixes`, `minutes_par_fenetre_gpu`, `minutes_par_scene`), quota
GPU du diagnostic, règle Grafana « GPU marqué incompatible » et sa tuile. Gardés : la gestion des volumes bloc (le POP2
en a un), la table `types_incompatibles` (historique), l'état d'analyse « attente_gpu » (nom historique, aussi pour la
rupture du processeur et le lot suivant), la lecture des analyses passées faites sur GPU. Le motif d'abandon devient
« processeur indisponible pendant toute la fenêtre utile » ; la règle d'alerte de Grafana reconnaît l'ancien et le
nouveau. La variable `GPU` du script de démarrage devient `DOCKER_OPTS`.

## 24/10/2026, lot Z4, étape 4 : mise en commun, ce qui est fait et ce qui attend

Fait : **une seule distance** côté serveur, `mars.geo.distance_m` (haversine, 6 371 km), à la place de cinq copies
identiques au caractère près (`viirs.haversine_m`, `rules._hav`, `sentinel._hav_m`, `tip_and_cue.distance_m`,
`dossiers._dist_m`, gardés comme alias là où d'autres modules les appellent). Restent à part, volontairement : la
version numpy de la trajectoire (`mars/trajectoire.py`, latitude en premier, sur des tableaux) et celles des scripts
des travailleurs (envoyés seuls par cloud-init, Python 3.8, bibliothèque standard).

Listé pour plus tard (réécriture risquée sans essai réel des travailleurs, aucun gain de comportement) :
* module commun des travailleurs (`travailleurs/viirs.py`, `sentinel.py`, `sentinel2.py` : récupération de la tâche,
  envoi des résultats, signature S3, journal), qui suppose de livrer deux fichiers par cloud-init ;
* une seule évaluation (« évaluable ou non ») : `mars/evaluation.py` est déjà commun, mais VIIRS, Sentinel 1 et
  Sentinel 2 gardent chacun leur appel et leur gravité ;
* un seul squelette Sentinel 1 et Sentinel 2 (`mars/sentinel.py`, `mars/sentinel2.py` : sélection, plafond, budget,
  attente, lancement, ingestion) ;
* renommer l'état `attente_gpu` (migration et requêtes de Grafana).

## 24/10/2026, lot Z6, phase 1 : préparation des essais réels

Règles 2026.10.50 (section `essai_reel`, budget des essais de 8 € HT), aucune migration, aucun essai payant.
* Coût par résultat dans Coulisses, Budget (`cout_par_resultat` de `/api/controle/etat`) : par source, sur le mois ou
  depuis le début, dépense des travailleurs détruits, échecs compris, divisée par les nuits VIIRS traitées, analyses
  Sentinel terminées, demandes d'observation analysées.
* `taches.py essai-reel` (préparation, lecture seule) et `essai-reel --lancer ID` (un essai, malgré la pause,
  consigné, plafond du jour et au moins une scène, aucun travailleur en vie, budget des essais, types jamais éprouvés
  refusés par `travailleurs.ESSAI_EPROUVES_SEULS`).
* `taches.py viirs-bilan` : nuits VIIRS depuis une date, nuits manquées comprises.
* Revue des échecs Sentinel 2 du 20/10 : tests sur le vrai chemin (`tests/test_echecs_sentinel2.py`), cible
  `s2-s3` rejouée et réussie ; ce qui reste à prouver en réel : `docs/sentinel2.md`, section 13.
* Bilan VIIRS depuis le 24/10 : non lu, le tunnel vers le serveur était fermé pendant la préparation ;
  `viirs-bilan --du 2026-10-24` le donne.


## 24/10/2026, lot Z6, phase 2 : enquête sur l'analyse Sentinel 1 réelle 103

Aucune migration, aucune règle changée, aucun essai payant : la scène du 23/10/2026 (S1D, 17:57 UTC) est publique chez
Planetary Computer et l'analyse a été reproduite sur le Mac au chiffre près (détection 3 à 0,909, 101 m).
* Géolocalisation : pas d'erreur de 5 km (côte GSHHG sur la côte du radar, SMYRTOS à sa position AIS) ; en revanche le
  polynôme d'ordre 2 de GDAL sur les points d'appui s'écartait de la grille de l'ESA de 54 m en médiane, 544 m au plus,
  80 m à Weymouth. Travailleur passé à la spline en plaque mince (`GCP_TPS`, `warp_tps`) : 20 m au plus. Tests :
  `tests/test_geolocalisation_s1.py`, `verifier_images.sh geolocalisation`.
* Détection manquée : SMYRTOS à 0,879 sous le seuil de 0,9, le brise lames de Portland à 0,909 ; le modèle d'AI2 est
  appelé sans les deux passages antérieurs qu'il attend. Avec eux : SMYRTOS 0,966, brise lames 0,282, 32 détections
  brutes au lieu de 185. Remettre l'historique au travailleur : proposé, non fait.
* Outil : `travailleurs/essai_diagnostic_s1.py` (fenêtre, détections brutes, image avec côte et AIS, gratuit).
* Tests : 483 réussis avec la base.

## 25/10/2026 : analyse 97 retraitée, audit des contrats des modèles, lot historique Sentinel 1

Règles 2026.10.51 (section `sentinel.historique`), aucune migration, aucun essai payant.
* Analyse 97 retraitée gratuitement (copie publique, vrai travailleur) : sans historique, MSC MERAVIGLIA à 0,879 et
  des rochers à 0,925 ; avec, 0,962 et 0,541 ; navires à quai sous 0,5 dans les deux cas.
* Audit des contrats d'entrée (`docs/contrats_modeles.md`) : Sentinel 1, historique manquant (corrigé) ;
  Sentinel 2 conforme ; VIIRS, masque des nuages jamais donné, d'où 0 détection sous une lune au dessus de 80 %
  (nuits du 22 et du 23/10), prouvé par `travailleurs/essai_viirs_lune.py`.
* Lot historique : deux passages antérieurs de la même orbite relative dans les 24 jours, repli à 0,95 sans eux,
  signalé dans la fiche, tracé ; 2,8 fois plus d'octets lus par fenêtre. Branche `historique-s1`, fusionnée le
  25/10/2026 (343cd97).

## 25/10/2026 : lot viirs-masque, masque des nuages donné au modèle VIIRS

Règles 2026.10.52 (section `viirs.masque_nuages`), migration 44, aucun essai payant.
* Le masque des nuages CLDMSK_L2 de la même minute est cherché au catalogue CMR (temps quasi réel de SIPS, puis
  produit standard de LAADS ; aucun en temps quasi réel pour NOAA 21), lu par le travailleur avec le jeton
  Earthdata, vérifié, passé au modèle (`phys_path`) ; tracé (span de lecture, octets, lune, état du masque).
* Sans masque au dessus de 80 % de lune : granule « non évaluable », nuit « non évaluable » si tous le sont
  (`viirs-bilan`, frise, indicateur) ; la migration marque ainsi les granules des nuits du 22 et du 23/10.
* `taches.py viirs --nuit …` : retraitement de nuits déjà traitées ; les granules du 22 et du 23/10 sont encore
  servis par LANCE (environ 7 jours de conservation).
* Essais : `verifier_images.sh viirs-lune` et `viirs-masque` dans l'image d'allenai ; tests : 502 réussis avec la base.

## 25/10/2026 : réanalyse d'un passage Sentinel 1, seuil de lune VIIRS à 55 %

Règles 2026.10.53 (`viirs.lune_max_absence`), migrations 45 et 46, aucun essai payant.
* `essai-reel --lancer 779` avait affiché « Essai lancé : travailleur None » après un refus, avec un motif faux
  (« lancement concurrent » alors que l'analyse 103 était terminée). Tout refus s'affiche désormais « REFUS : motif »,
  code de sortie non nul ; la garde distingue « analyse en cours » et « passage déjà analysé ». Option `--refaire` :
  nouvelle analyse liée à la précédente (`analyses.refait`, index unique revu), l'ancienne gardée, les deux dans la
  fiche du passage, refusée pendant une analyse en cours, tracée (span `sentinel.garde_passage`).
* VIIRS : la nuit du 20/10 (72 % de lune, 0 détection) n'est pas expliquée par l'extinction du modèle (au dessus de
  80 % seulement) mais par son classifieur final, qui perd les navires faibles dès 60 %, masque ou pas. Seuil
  d'absence non concluante à 55 % ; la migration 46 réapplique le marquage aux granules déjà traités.
* Tests : 521 réussis avec la base ; `verifier_images.sh viirs-lune viirs-masque` verts.

## 25/10/2026 : fusion d'un navire à l'arrêt, règle de prudence, refusion

Règles 2026.10.54 (section `fusion` : `arret_kn`, `arret_max_min`, `arret_derive_m_par_min`, `arret_contredit_m`,
`doute_ratio`), aucune migration, aucun essai payant.
* Essai réel A (analyse 105, réanalyse de 103) : détection réussie (SMYRTOS 0,966, historique S1C du 17/10 et S1D du
  11/10), fusion ratée : la seule position AIS de SMYRTOS avait 13 min, écartée par `max_gap_min` de 10 min, d'où
  l'alerte DARK_SHIP élevée 75182, fausse.
* Mesure en lecture seule sur 300 navires à l'arrêt : intervalle médian 12 min, 99e centile 60 min, 68 % au delà de
  10 min ; position encore à moins de 300 m dans 96,6 % des cas après 90 à 120 min.
* Maintien de la dernière position d'un navire à l'arrêt 120 min, tolérance de 2,5 m par minute d'âge ; prudence :
  DARK_SHIP faible à confirmer si un navire connu dans les 120 min peut expliquer l'écho (radar, optique, VIIRS) ;
  `taches.py sentinel-refusionner ID`.
* Tests : 537 réussis avec la base.

## 26/10/2026 : refusion des alertes VIIRS passées

Aucune règle changée (2026.10.54), aucune migration, aucun essai payant.
* `taches.py viirs-refusionner` (une nuit, une plage ou toutes, `--simulation` en lecture seule) : appariement VIIRS
  refait avec le maintien à l'arrêt et la prudence ; alertes mises à jour en place, faux positifs classés par MARS C2
  avec le navire qui les explique, décisions des opérateurs gardées, demandes d'observation actives annulées.
* Alerte 50275 (élevée, au large de Hyères, à l'origine de l'analyse Sentinel 2 102) vérifiée en lecture seule : aucun
  navire AIS à moins de 15 km entre 00:20 et 02:20 ; le seul proche (CIRTA, 13 nœuds) était à 5,8 km 80 min avant. La
  refusion ne l'explique pas : ce n'est pas un défaut de fusion (petit navire sans AIS possible).
* Tests : 544 réussis avec la base.

## 26/10/2026 : préparation d'une semaine réelle de tip and cue (Sentinel 1)

Règles 2026.10.55 (`travailleurs.types_eprouves_seuls`), aucune migration, aucun essai payant.
* Défaut trouvé : hors essai réel, une rupture de GP1-S aurait lancé POP2-8C-32G, jamais éprouvé ; refusé désormais
  pour Sentinel 1 et 2, l'analyse attend.
* Rejeu enrichi (par jour, coût brut et sous le plafond, demandes retenues et navires) ; alertes classées écartées
  du rejeu comme de la collecte en direct ; `taches.py tip-and-cue-bilan --du`.
* Tests : 547 réussis avec la base.

## 26/10/2026 : lumières voisines d'un même navire, explication d'une alerte VIIRS

Règles 2026.10.56 (`viirs.lumiere_voisine_m`), aucune migration, aucun essai payant.
* Simulation de la refusion VIIRS sur le serveur : élevées ouvertes de 16 à 8, 1 classée (MISTRAL), 8 abaissées,
  19 ouvertes. Parmi elles, 8 avaient un AIS à 196 à 479 m, par paires d'identifiants consécutifs : un grand navire
  allume deux pixels voisins et l'appariement un pour un laissait le second « sans AIS ». Une détection à moins de
  2 km d'une détection appariée au navire V, et dans la tolérance de V, est désormais expliquée par V.
* Alerte 50275 : ma vérification par photographies de `/api/traffic` (navires vus dans les 30 min avant chaque
  instant, de 00:20 à 02:20, rayon de 15 km) ne pouvait pas voir COSTA TOSCANA (position à 55 min, à environ 25 km,
  estime à 385 m de la lumière). Nouvelle méthode : `taches.py viirs-expliquer ALERTE` (lecture seule, code de la
  fusion). Gardée faible à confirmer : une estime de 55 min d'un navire en route n'est pas un appariement.
* Tests : 551 réussis avec la base.

## 26/10/2026 : décisions du lot Z7 (calibration)

Règles 2026.10.57, aucune migration, aucun essai payant. Mesures sur le serveur du 06 au 24/10 (18 jours, 18/10
exclu), proposition chiffrée seuil par seuil ; décisions d'Antoine :
* Rendez vous : durée minimale de 120 à 60 min, distance de 500 m gardée (1 000 m refusé : 39 épisodes, dont 21 au
  mouillage, aucun bord à bord). Aux seuils danois, NORTHERN OSPREY bord à bord de TORM ALMENA puis de PHILOTIMOS
  (8 à 37 m, 25 km au large, 70 à 90 min) n'était jamais vu ; il forme désormais un dossier « À examiner »,
  « Rendez vous 3 fois ».
* Balises de pêche (B1, M2, BUOY3 COEUR DE MARIN) reconnues par le MMSI, le nom, ou un nom réduit à un code sans
  longueur de navire : rendez vous écarté, compté à part (`exclus_balises`).
* Travaux offshore : rendez vous à moins de 1 km d'un parc éolien avec un navire du chantier (type « Other », ou
  présent dans le parc 3 jours sur 7) : gardé, étiqueté « travaux offshore », gravité faible ; jamais pour un navire
  des listes. EEMS DOVER et BOLD TERN (6 des 7 épisodes danois) sont dans le parc de Courseulles, ISLAND DILIGENCE
  et TSM WINDCAT 59 dans celui de Dieppe et du Tréport.
* Dossiers : un rendez vous figure dans le dossier de chacun des deux navires (auparavant, du plus petit identifiant
  seulement).
* Coupures AIS : `min_prior_messages` de 10 à 6, toujours en observation. Injection de contrôle sur la base de test
  (09/10) : navires écartés faute d'historique de 188 à 128, détection inchangée (80 sur 100). Sur le serveur, avant
  le changement : 239 coupures retrouvées sur 299 (80 %).
* Zone de réception fiable et masques des mouillages reconstruits sur 14 jours, seuils inchangés (mesure : 78 228 km²
  contre 57 867, 621 mouillages contre 364).

* Tests : 557 réussis avec la base.

## 27/10/2026 : réévaluation VIIRS corrigée, feux des parcs éoliens, diagnostic de l'injection

Règles 2026.10.58, aucune migration, aucun essai payant. Après le déploiement de c251223 et la reconstruction de la
zone de réception (1 626 cellules, 68 625 km²) :
* `viirs-reevaluer` avait relevé 50277 (abaissée par la refusion, ATHENA SEAWAYS compatible) en élevée : chaque
  alerte était réécrite avec une gravité recalculée, et la prudence ne lisait les navires que dans 0,3 × 0,2 degré
  autour de la lumière (un navire en route vu 60 min avant à 37 km en sortait). Désormais : refusion appliquée sur
  les mêmes nuits avant toute nouvelle alerte, abaissement du système jamais remonté, portée de 100 km
  (`viirs.doute_portee_km`), option `--simulation` qui liste les changements dans une transaction annulée.
* 9 alertes élevées sur les feux du parc d'Yeu et Noirmoutier : lumières à moins de 1 km d'un parc en service
  (`viirs.parcs_en_service`, l'état n'est pas dans EMODnet) écartées (`eolienne`), alertes classées par MARS C2 avec
  le motif ; lumière fixe dès 2 nuits évaluables sans AIS ; un parc éolien ne compte plus comme infrastructure
  proche. 80241 et 80242 sont dans le parc en projet de Fécamp Grand Large, à 16 et 18 km du parc construit : pas
  masquées. 80238 est à 10,6 km du parc de Saint Nazaire : pas masquée.
* Injection : options `--tranches`, `--min-prior` (répétable), `--zone-du` et `--zone-au` (zone de comparaison
  reconstruite dans une transaction annulée), `--episodes-depuis`. Base de test, même zone, navires pris sans
  préférence : 6 à 9 messages dans l'heure, 3 sur 11 retrouvées ; 10 et plus, 32 sur 50. L'injection d'avant
  prenait les navires les mieux reçus d'abord : le seuil ne change presque pas qui est essayé.
* Tests : 563 réussis avec la base.

## 27/10/2026 : enquête sur l'analyse Sentinel 1 106 (pas de Calais)

Règles 2026.10.59 (`sentinel.seuil_absence`), aucune migration, aucun essai payant. Scène S1D du 25/10 17:40 (passage
782), deux fenêtres de 24 km (NOSTOS, ALTAI), 3 détections, une alerte DARK_SHIP critique (76467) et 5 AIS_UNCONFIRMED.
Reproduite sur le Mac par la copie publique (Planetary Computer), avec et sans les passages du 19/10 (S1C) et du 13/10
(S1D) : `travailleurs/essai_diagnostic_s1.py`.
* L'analyse a tourné **sans historique** : NOSTOS à 0,963 et 130 m, l'écho 6 à 0,971 et 50 m, l'écho 7 à 36 m sont les
  valeurs exactes du passage sans historique, et le seuil prudent de 0,95 laisse exactement ces 3 détections. Avec
  l'historique : 11 détections au dessus de 0,9 dans les cœurs (7 et 4), dont quatre grands échos de 112 à 256 m dans la
  fenêtre NOSTOS restés entre 0,76 et 0,95 sans historique. Cause de l'absence d'historique : à lire dans les mesures de
  l'analyse (`historique_erreurs`).
* L'absence d'un navire AIS se jugeait contre les seules détections gardées : un navire vu à 0,87 passait pour absent.
  Le modèle tourne désormais au seuil d'absence (0,5) ; les échos entre ce seuil et celui de détection, et ceux hors de
  nos zones (le Doppler pousse au delà de 51,2 degrés nord l'écho d'un navire du bord de la Manche), sont transmis comme
  échos faibles, ne font ni détection ni alerte, mais confirment un navire. Sans eux, l'absence n'est jamais concluante
  (refusion de 106 : les 5 alertes classées avec ce motif). `sentinel-refusionner ID --simulation`.
* Doppler : décalage le long de la trace de 150 s fois la vitesse vers le radar, dans l'appariement comme dans
  l'absence (élargi de moitié) ; la physique de Sentinel 1 donne plutôt 80 à 90 s : tolérance généreuse.
* Tests : 566 réussis avec la base.

## 27/10/2026 : socle A, A03 (historique Sentinel 1 manquant)

Règles 2026.10.60 (`sentinel.historique.couverture_min`), aucune migration, aucun essai payant.
* Cause, rejouée sur le catalogue de Copernicus : le choix des passages antérieurs exigeait les quatre coins de chaque
  fenêtre dans leur emprise. Le coin sud ouest de la fenêtre NOSTOS est hors de l'image du jour, donc hors de tout
  passage de la même trace : les quatre passages de l'orbite 59 (19/10 S1C, 13/10 S1D, 07/10 S1C, 01/10 S1D) étaient
  écartés, avec le motif faux « aucun passage antérieur », et l'analyse partait au seuil prudent de 0,95.
* Désormais : un passage convient à une fenêtre s'il couvre 98 % de la part de la fenêtre dans l'image du jour (grille
  de 9 × 9 points) ; s'il n'en est aucun pour toutes les fenêtres, chaque fenêtre reçoit les siens ; le motif dit
  quelle fenêtre n'en a pas. Testé sur les géométries réelles de 106 (19/10 et 13/10 retrouvés) et de 103 (inchangée).
* Garde : une fenêtre analysée sans passage antérieur est comptée dans la trace, son motif s'affiche dans la fiche de
  la détection, le résumé de l'analyse la porte (`sans_historique`) et une notification part, une fois par analyse.
* Bande sans données : l'emprise du catalogue couvrait presque toute la fenêtre NOSTOS (3 points sur 81 dehors), l'image
  réelle n'en a pas sur 17 % (mesure sur la fenêtre découpée). Le travailleur relève les cases d'environ 1 km sans
  données ; un navire AIS qui s'y trouve n'est plus déclaré absent.
* Tests : 573 réussis avec la base.

## 27/10/2026 : mission socle A, étapes 1 et 2 (diagnostics, correctifs)

Règles 2026.10.61, aucune migration, aucune dépense.
* Étape 1 (serveur, conteneurs à part, transactions annulées). Injection du 24/10, navires pris sans préférence : zone
  en service (1 626 cellules) et zone du 06 au 10/10 (1 304) donnent le même taux à 10 messages (66 %) ; la baisse de
  80 à 64 % venait de l'ancien tirage (navires les mieux reçus d'abord), pas de la zone. Zone du 06 au 24/10 : 1 863
  cellules, l'écart de surface vient de la durée (`min_pairs` est un compte sur la période). Par tranche, zone en
  service, 20, 22 et 24/10, 358 essais : 6 à 7 messages 43,8 %, 8 à 9 messages 54,8 %, 10 et plus 62,3 %.
  Simulation `viirs-reevaluer` : critères remplis (50277 faible avec ATHENA SEAWAYS, 10 alertes du parc d'Yeu et
  Noirmoutier classées, aucune relevée, décisions gardées), élevées ouvertes de 13 à 3.
* Étape 2, branche `socle-a` : historique Sentinel 1 sur la part de la fenêtre dans l'image (A03), garde sans
  historique, cases sans données ; analyse 106 navire par navire (les cinq « absents » vus entre 0,76 et 0,945 sans
  historique, écho 6 sans AIS à moins de 5 km) ; `min_prior_messages` à 8 (critère : 8 à 9 à moins de 10 points de
  10 et plus) ; `min_pairs` proportionnel au nombre de jours ; place gardée pour une demande planifiée sous le plafond
  (B02) ; motif clair des candidats Sentinel 2 (C02). Tests : 578 réussis avec la base, typecheck, knip, 25 tests de
  l'interface.

## 27/10/2026 : mission socle A, étapes 3 et 4 (déploiement, opérations sur les données)

* État trouvé au départ : Sentinel 1 et tip and cue en service (repris le 25/10 à 21:38) et analyse automatique 107
  à 00:16 (avec historique : 2 passages, seuil 0,9, 3 détections, aucune alerte, 0,19 €). Mis en pause par claude-code
  à 13:40 UTC, motif écrit, jusqu'au déploiement du correctif.
* Étape 3 : `02b660e` publiée (toutes les vérifications OK) et déployée en 59 s, VERDICT à OK partout, règles
  2026.10.61.
* 4a : refusion de 106, simulation conforme puis réelle : 76468 à 76472 classées « absence non concluante », 76467
  (écho 6, critique) inchangée.
* 4b : `viirs-reevaluer`, simulation conforme puis réelle : 50277 faible (ATHENA SEAWAYS compatible), 10 alertes du
  parc d'Yeu et Noirmoutier classées, aucune relevée, décisions gardées ; élevées ouvertes de 13 à 3.
* 4c : prévision de la zone sur 14 jours avec 39 paires, 1 679 cellules ; reconstruite à 1 649 (69 676 km², à 1,8 %
  de la prévision), mouillages 512. La surface de 1 863 cellules sur 06 au 24/10 tenait à la période (réception plus
  étendue début octobre), pas au seul nombre de paires. Réévaluation VIIRS : 2 alertes créées (92405 moyenne, 92408
  faible), rien relevé. Injection de contrôle du 24/10 : 65 sur 100.
* 4d : verdicts des 9 alertes VIIRS élevées ou moyennes ouvertes, aucune expliquée par un navire AIS : 15198, 45569
  et 80242 (élevées) à surveiller ; 80241 et 80242, même granule dans le parc en projet de Fécamp Grand Large,
  balisage de bouée de mesure possible ; 80229, 80230 et 92405, golfe du Lion la même nuit, flottille sans AIS reçu
  probable ; 10478 et 80244, petite pêche probable. Décisions laissées à Antoine.

## 27/10/2026 : mission socle A, étape 5 (B01, l'historique en production)

Essai payant : `essai-reel --lancer 782 --refaire` (plafond porté à 2 pour la seule commande, la scène du jour ayant été
prise par l'analyse automatique 107), analyse 108, travailleur 44 (GP1-S), 13 min, 0,19 €, détruit. Dépense de la
mission : 0,19 €.
* Critères tenus : historique choisi « 2 passages antérieurs (19/10/2026 S1C, 13/10/2026 S1D) », lu par les deux
  fenêtres, seuil 0,9 ; 11 détections, exactement le rejeu gratuit ; échos faibles transmis (7 et 11) ; 98 cases sans
  données relevées sur NOSTOS (17 %) ; aucune AIS_UNCONFIRMED, les cinq « absents » de 106 appariés à leur écho.
* Défaut trouvé : VICTORINE rend deux échos à 106 m (150 m chacun) ; l'appariement un pour un laissait le second sans
  AIS, alerte DARK_SHIP critique 92604 fausse. Corrigé (règles 2026.10.62, `fusion.echo_voisin_m`), test sur les 11
  détections réelles ; refusion de 108 après déploiement.
* Alertes réelles à décider : 76467 (écho 6, 52 m, aucun AIS à 5 km), 92603 (écho de 30 m à 0,969, aucun AIS à 3,4 km),
  92602 (KATRE, longueur mesurée 34 m contre 119 m : sous estimation ou autre petit navire, ambigu).


## 29/10/2026 : mission socle A, reprise (correctif 2026.10.62 déployé, refusion de 108, étape 6 lancée, E01)

* État trouvé : étapes 1 à 5 faites le 27/10, correctif 2026.10.62 (écho voisin, analyse 108) fusionné mais pas
  publié ; serveur en 02b660e ; aucun travailleur en vie ; dépense de la mission 0,19 € (travailleur 44).
* Commande `taches.py plafond sentinel1 N --motif TEXTE` : le plafond ne se réglait que par le panneau (session
  d'opérateur) ; même borne, même journal, test en base. Publiée et déployée avec le correctif : `59a526c`, VERDICT
  à OK partout, règles 2026.10.62. Tests : 580 réussis avec la base, typecheck, knip, 25 tests de l'interface.
* Refusion de 108, critères écrits avant (92604 expliquée par VICTORINE, 76467, 92602 et 92603 inchangées, aucune
  nouvelle alerte) : simulation conforme, puis réelle : 92604 classée avec le motif « second écho du même navire,
  VICTORINE (MMSI 256274000), à 107 m », 7 échos appariés au lieu de 6.
* Étape 6 lancée le 29/10 à 11:28 UTC par claude-code (motif « mission socle A, boucle tip and cue ») : plafond
  Sentinel 1 à 2, Sentinel 1 et tip and cue repris. Garde fou sur le serveur (`/tmp/z7/garde_tip.sh`, journal
  `/tmp/z7/garde_tip.txt`), qui ne fait que remettre en pause : dépense de la mission à 2,81 € (0,19 € gardés pour
  l'étape 7), travailleur en vie depuis plus d'une heure (détruit par le code), analyse sans historique, alerte
  élevée ou critique d'une analyse postérieure à 108, ou 72 h écoulées (01/11 à 11:28 UTC).
* E01, règles 2026.10.63 : silence sans réapparition à moins de 10 km du bord en mer de la zone fiable déclassé en
  faible comme un point collectif, sauf indice propre au navire. Mesure sur les épisodes du 27 au 29/10 (les résumés des cycles ne remontent qu'au 27/10 à 02:32 UTC ; résumés des
  cycles, 30 plus longs par cycle) : 36 silences élevés sans réapparition, médiane à 6,8 km du bord de la zone ;
  15 à moins de 10 km d'un bord en mer (déclassés), 21 restent élevés ; témoin, 7 réapparus moyens ou élevés sur 31
  aussi près. Un groupe revient chaque jour vers 0° E 50,5° N (face à Newhaven) sans former de point collectif (à
  cheval sur la maille du méridien 0, jours différents). Bord le long de la côte écarté (baie de Seine, abords
  d'Ouessant gardés élevés). Défaut trouvé en chemin : l'union des géométries des cellules laisse des frontières
  internes ; cellules refaites depuis leurs indices. Les coupures injectées réapparaissent : taux inchangé.

## 29/10/2026 : mission socle A, étape 8 (chantiers de code sans dépense)

Règles 2026.10.64, aucune migration.
* E01 : voir l'entrée précédente (bord en mer de la zone fiable, 15 silences élevés sur 36 déclassés).
* E02 : proposition chiffrée pour `high_duration_min` (720 min) dans `docs/calibration.md`, **valeur inchangée** :
  66 silences passés en élevé pendant qu'ils étaient ouverts du 27 au 29/10, dont 26 réapparus ensuite ; à 720 min,
  44 et 14. Le critère de la mission ne tranche pas : une coupure injectée réapparaît toujours, ce seuil ne la touche
  pas. À refaire après une semaine avec E01, avec une injection de silences sans réapparition.
* D02 : NOAA 21 n'a jamais de masque des nuages à 06:30 (21 granules du 24 au 28/10, aucune masquée ; sous la pleine
  lune, le modèle éteint alors toute la scène). Tâche `viirs_masque` à 18:30 UTC : granules traitées sans masque
  refaites une fois si le masque standard est publié ; `viirs --remasquer` à la main. Un travailleur DEV1-M de plus
  par jour quand il y a lieu (environ 0,02 €), dans le plafond de 4 travailleurs par jour.
* D03 : erreur de l'estime à rebours mesurée (médiane 1,6 à 2,4 km, 80 % sous 7 à 10 km à 60 min) ; aucune règle,
  l'estime reste bornée à 20 min ; COSTA TOSCANA (385 m à 55 min) est un cas favorable.
* E06 : CLAUDE.md (état au 29/10), pièges (union des cellules, `docker compose exec` qui avale un bloc SSH, chemin du
  dépôt avec une espace, profondeur des résumés d'épisodes), calibration.
