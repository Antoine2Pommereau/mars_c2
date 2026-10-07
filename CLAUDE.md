# MARS C2 : contexte du projet pour Claude Code

Ce fichier est la mémoire du projet. Lis le en entier avant toute tâche, et tiens le à jour à chaque jalon. Le
document de référence de la direction est la Claude Doc « MARS C2, mise à jour du projet » :
https://claude.ai/code/artifact/00f50730-d117-4ad4-94ac-0cb9b967f002 (sinon, demander à Antoine de la déposer dans
`docs/`). L'interface cible des étapes 2 à 4 est décrite dans `docs/vision_interface.md` (lot 1, la charpente, et
lot 2, le contenu, faits). L'historique de la première version (rejeu de journées danoises) et ses enseignements de calibration
sont dans `docs/historique_danemark.md` ; l'état du code est audité dans `docs/audit_code.md`.

## 1. Le projet

**Objectif.** Un outil opérationnel, en direct, qui protège les infrastructures maritimes françaises (câbles,
interconnexions, pipelines, parcs éoliens) et suit les navires de la flotte fantôme dans les eaux françaises, en
confrontant l'AIS déclaratif à des capteurs satellitaires indépendants. Client imaginé : une autorité française de
l'action de l'État en mer. Projet personnel d'Antoine, vitrine pour un poste de **Forward Deployed Engineer**.

**Zones collectées** (`mars/ais/live.py`) :

| Zone | Emprise (lat_min, lon_min, lat_max, lon_max) |
|---|---|
| `bretagne` (rail d'Ouessant, approches de Brest) | 47.3, -6.8, 49.6, -3.0 |
| `manche` | 48.4, -5.0, 51.2, 2.6 |
| `gascogne` | 43.3, -6.0, 47.4, -1.0 |
| `mediterranee` (golfe du Lion, Marseille, Toulon, Côte d'Azur, Corse) | 41.2, 3.0, 43.7, 9.8 |

**Pistes.** 1. Infrastructures sous marines (ancre traînante, arrêt ou flânerie dans un corridor, coupure AIS,
rendez vous, écho sans AIS). 2. Flotte fantôme (navire des listes identifié par son **OMI**, changements de nom ou de
pavillon, transbordement, incohérence radar et AIS). Brouillage GNSS écarté.

**Sources.** AIS en direct par AISStream (plan B : récepteur personnel, AISHub) ; Sentinel 1 et 2 ; VIIRS ;
EMODnet (infrastructures) ; catalogue GUR (`Vessels1.db`) et OpenSanctions. Les archives (École navale, Ouessant,
GeoTrackNet, routes de Brest, DMA) servent **uniquement à entraîner** les modèles de comportement.

**Modèles.** `allenai/vessel-detection-sentinels` et `vessel-detection-viirs` ; GeoTrackNet en pilote ;
TrAISformer ensuite ; **CircleNet** (xView3) gardé en repli et en comparaison.

**Plan.** 1. Socle en direct (**fait**, voir section 9) ; 2. règles en continu (**faites**, alertes WATCHLIST et
IDENTITY_CHANGE comprises), recherche, fiche navire, frise par période ; 3. satellites (lot A, calendrier des
passages Sentinel 1 et 2, **fait** ; lot B, VIIRS chaque nuit par travailleurs éphémères, **fait** ; lot C, analyse
Sentinel déclenchée sur les corridors, par la même architecture sur GPU ; cartes de chaleur) ; 4. anticipation (TrAISformer). Pilote GeoTrackNet en parallèle.

**Principes directeurs** : traçabilité (chaque alerte remonte à ses preuves et à la version des règles) ; mesurer
plutôt qu'affirmer (une règle silencieuse est prouvée par un test par injection) ; respecter la physique du
capteur ; tranche verticale d'abord ; calibrer au cas par cas ; déclasser plutôt qu'exclure (un statut AIS est un
indice, jamais une excuse).

## 2. Travailler avec Antoine

* **Répondre en français.**
* **Aucun tiret dans les textes rédigés pour Antoine**, sous aucune forme (trait d'union, tiret court, tiret long) :
  prose, documentation, commentaires destinés à être lus, messages de commit, libellés et textes de l'interface.
  Reformuler : « rendez vous », « Sentinel 1 », « MARS C2 », « bord à bord ». Dates au format 17/06/2024. Le code,
  les commandes, les identifiants, les noms de fichiers et les URL ne sont pas concernés.
* MacBook Air (Apple Silicon), Python 3.12 dans `.venv`, Docker Desktop, Node par Homebrew. Dépôt dans
  `~/Documents/Projet Perso/MarsC2/mars_c2` (dossier parent `MarsC2`, dépôt `mars_c2`).
* Expliquer les choix et leur raison, signaler honnêtement erreurs et limites, chiffrer, terminer par la prochaine
  étape proposée. Après une modification, donner les commandes de vérification et ce qu'on doit y lire.
* Design validé (section 7) : poste de commandement épuré et sombre, très peu de texte. Antoine trouve vite une
  interface « trop chargée ».
* GitHub : https://github.com/Antoine2Pommereau/mars_c2. Jalons enregistrés par un commit explicite sans tiret ;
  travail en branche, fusion dans `main` sur demande.

## 3. Démarrer

**Serveur** (Scaleway DEV1-S, `/opt/mars_c2`) : tout est décrit dans `docs/deploiement.md` (installation, R2,
vérification, restauration). Les images sont **construites sur le Mac en linux/amd64**, publiées sur le registre
GitHub (`ghcr.io/antoine2pommereau/mars_c2-backend`, `mars_c2-web`, `mars_c2-scripts`, paquets privés, étiquettes
`latest` et empreinte du commit), téléchargées par le serveur (jeton en lecture seule) et démarrées par
`docker compose up -d --no-build`. Le serveur ne construit rien (ni la place ni la mémoire) ; les gros transferts par
SSH échouent depuis le Mac. Le `.env` du serveur active la surcouche `docker-compose.serveur.yml` et le profil `direct`.

**Mac** (développement, radar) :

```bash
./start.sh                                     # base, API, interface (Docker), inférence et Vite en arrière plan
docker compose up -d db backend web            # ou à la main
uvicorn inference.app:app --port 8001          # inférence sur le GPU Apple, terminal dédié
cd web && npm run dev                          # interface de développement
```

| Adresse | Rôle |
|---|---|
| http://localhost:8080 | Interface construite (nginx) ; sur le serveur, par tunnel SSH |
| http://localhost:5173 | Interface de développement (Vite) |
| http://localhost:8000/api | API FastAPI |
| http://localhost:8001/v1 | Service d'inférence (Mac seulement) |
| localhost:5432 | PostgreSQL (`mars` / `mars`), fermé sur le serveur |

Après une modification : `backend/`, `docker compose up -d --build backend` (attendre quelques secondes) ;
`inference/` ou `mars/sar/`, redémarrer l'inférence ; `web/`, rien en développement, `--build web` pour le port 8080 ;
`config/rules.yaml`, rien (relu à chaque exécution). Une collecte par clé AISStream : le profil `direct` ne tourne
que sur le serveur.

## 4. Architecture

| Composant | Où | Rôle |
|---|---|---|
| `db` | Docker, PostgreSQL 16, PostGIS 3.4 | Positions, navires et identités, listes, alertes et preuves, radar, masques, archives, tâches |
| `backend` | Docker, FastAPI, asyncpg | Horloge (direct ou rejeu), flux SSE, navires, radar, règles, décisions, état du direct |
| `web` | Docker (nginx) ; Vite en développement | Interface React |
| `collector` | Docker, profil `direct` | AISStream vers le Parquet `data/ais_live` (un fichier par minute, zone et type) |
| `ingest` | Docker, profil `direct` | Parquet vers la base, allègement, identités, journées, `vessel_watch` |
| `taches` | Docker, serveur seulement | Règles toutes les 5 min ; chaque nuit : archivage R2, purge à 30 jours, sauvegarde, liste OpenSanctions (le GUR chaque semaine), calendrier des passages satellites ; 06:30 : nuits VIIRS confiées à un travailleur éphémère ; chaque minute : surveillance des travailleurs ; disque toutes les 10 min ; à la demande : masques (`build_masks.py`), mesures de calibration (`taches.py mesures`) |
| Travailleurs | Instances Scaleway éphémères (API Scaleway, `mars/travailleurs.py`) | Analyse satellite hors du serveur : créées à la demande, script et tâche par cloud-init, résultat renvoyé par le réseau privé (nginx, port 8090, jeton), détruites par le serveur ; DEV1-M pour VIIRS, GPU au lot C |
| Inférence | Mac, hors Docker (GPU Apple) | Extraction Sentinel Hub, CircleNet, vignettes ; repli conteneur sur CPU (profil `conteneur`) |

**Chaîne du direct** : AISStream, Parquet local, ingestion toutes les 15 s (un point par minute en route, un toutes
les dix minutes à l'arrêt), base, flux SSE une fois par seconde. Chaque nuit, les journées terminées partent sur
Cloudflare R2 (archive brute complète, servant aussi à l'entraînement) et sont effacées du serveur une fois
confirmées.

**Analyse radar** (étape 3, sur le Mac) : `POST /api/analyses` avec une emprise et un passage Sentinel 1 ; le
service d'inférence extrait l'image et détecte les échos ; l'API lit l'AIS autour de l'instant du passage, applique
la fusion (`mars/fusion/pipeline.py`) et la persistance des échos fixes, écrit détections et alertes.

## 5. Arborescence

```
mars_c2/
  CLAUDE.md, start.sh, pyproject.toml, docker-compose.yml, docker-compose.serveur.yml
  config/rules.yaml       seuils versionnés
  db/init/01 à 19         schéma et migrations (idempotentes à partir de 02)
  mars/
    ais/live.py, ingest.py, mid.py   zones, nettoyage, allègement, ingestion, pavillon
    watchlist.py, archive.py, r2.py  listes, archivage et sauvegarde, client R2
    satellites.py                    calendrier des passages Sentinel 1 et 2 (catalogue, plans de l'ESA, couverture)
    travailleurs.py, viirs.py        orchestrateur des travailleurs éphémères ; granules VIIRS, appariement, alertes
  travailleurs/viirs.py              script du travailleur VIIRS (exécuté dans l'image publique d'allenai)
  .github/workflows/                 nettoyage du registre GitHub (trois dernières versions par image)
    fusion/                          positions à l'instant du passage, tolérance Doppler, appariement, persistance
    sar/                             passages, extraction Sentinel Hub, CircleNet
    regions/provision.py             infrastructures EMODnet
  mars/rules.py                      moteur de règles, partagé par l'API et le conteneur taches
  backend/app.py                     API ; backend/contenu.py : recherche, fiches, notes, navires suivis, photo ;
                                     backend/regions.py : région affichée ; backend/satellites.py : passages ;
                                     backend/travailleurs.py : retour des travailleurs, VIIRS, /api/metrics
  inference/app.py                   service d'inférence
  scripts/                           collecte, ingestion, tâches, listes, régions, masques, règles, radar,
                                     mesures de calibration (mesures_calibration.py)
  tests/                             contrat du modèle, fusion, direct, archivage
  web/                               interface React
  docs/                              déploiement, audit, historique danois
```

## 6. Base et API

Tables principales : `positions` (30 jours), `vessels`, `vessel_identities` (vue `imo_history`), `watchlist` (vue
`vessel_watch`), `alerts`, `alert_evidence`, `alert_actions`, `sar_passes` (passages des analyses radar et calendrier Sentinel 1 et 2, colonne `mission`), `stats_10min_region`, `analyses`, `detections`,
`fixed_echoes`, `land`, `stationary_zones`, `reception_cells`, `regions`, `infrastructure`, `ais_days`,
`ingested_files`, `archives`, `task_runs`, `disk_status`, `sim_clock` (direct par défaut). `ais_days.coverage`
(union des zones collectées) situe le bord des données pour la coupure AIS ; les régions `Bretagne`, `Manche`,
`Gascogne`, `Mediterranee` portent les infrastructures EMODnet. `alerts.rule_key` : clé stable d'une alerte
comportementale (mise à jour en place, décisions des opérateurs conservées). Une migration ne
s'applique pas seule à une base existante : `docker compose exec -T db psql -U mars -d mars < db/init/XX_nom.sql`.

API : flux (`/api/stream?direct=1` : horloge chaque seconde, trafic en colonnes seulement quand l'ingestion a
chargé du nouveau, au plus tard toutes les 30 s), trafic à un instant (`/api/traffic?at=`), traînées
(`/api/traffic/trails?at=`), navires (`/api/vessels/{id}` avec identités, listes et alertes ; trajectoire allégée
à `max_points`), listes (`/api/watchlist`), état du direct (`/api/ingestion` : retard, disque, tâches), frise
(`/api/timeline?start&end&bins` : histogramme des navires et coupures du flux, lus dans `stats_minute` et
`stats_10min`), alertes d'une plage (`/api/alerts?start&end`, avec `vessel_ids`) et décisions (acquitter,
confirmer, classer avec motif obligatoire, rouvrir, commenter, champ auteur), radar (`/api/passes`,
`/api/analyses`, détections, `/api/chip`), masques (`?jours=N`), infrastructures (`?tolerance=` pour simplifier),
règles et test par injection (`/api/rules/...`). Contenu (`backend/contenu.py`) : recherche (`/api/search?q=` :
navires par nom actuel ou ancien, MMSI, OMI via `imo_history` ; infrastructures ; alertes par numéro), alerte par
numéro (`/api/alerts/{id}`), comportement d'un navire sur la plage (`/api/vessels/{id}/comportement` : silences,
arrêts au large, passages à moins de 2 milles d'une infrastructure), trajectoire GPX, notes, photo (relayée depuis
VesselFinder, sans écriture disque, `PHOTOS=aucune` la coupe), navires suivis (`/api/suivis`), fiches infrastructure
(`/api/infrastructure/{id}` : navires passés, alertes liées) et zone (`/api/zones/{clé}`). **Région affichée** :
`/api/regions`, et `?region=bretagne` (ou `manche`, `gascogne`, `mediterranee`) sur le trafic, le flux, les
traînées, les alertes, la frise, les infrastructures, les masques, les suivis, la recherche (région en premier) et
l'état du direct ; le filtre est dans la requête SQL (`dans_region`, géométrie de la table `regions`). **Passages
satellites** : `/api/satellites/passes?start&end&region` (emprises en GeoJSON), `/api/satellites/passes/{id}`
(fiche : infrastructures et navires des listes couverts, analyses, nuages annoncés pour Sentinel 2), prochain
passage dans `/api/ingestion`. **VIIRS** : `/api/viirs/detections?start&end&region`, `/api/viirs/detections/{id}`,
`/api/viirs/nuits`, état dans `/api/ingestion` (`satellites.viirs`) ; retour des travailleurs
`POST /api/travailleurs/{id}/etat|resultats` (jeton, réseau privé seulement). **Mesures** : `/api/metrics` (texte
Prometheus, pour Grafana plus tard). Tables du lot B : `travailleurs`, `viirs_granules`, `viirs_detections`,
`viirs_lumieres_fixes` (migration 20). L'horloge partagée (`/api/clock`, `sim_clock`) n'est plus utilisée
par l'interface : l'instant est tenu par chaque navigateur et inscrit dans l'adresse de la page.

## 7. Interface

Poste de commandement épuré et sombre ; **la couleur est réservée à ce qui demande l'attention**. Jetons
(`web/src/styles.css`) : fond `#0e1419`, panneaux `#141c23`, surélevé `#1b252e`, filets `#26323d`, texte `#e6ecf0`,
secondaire `#7c8b97`, signal système `#4fb6c8` ; navire sombre `#e85bc7`, rendez vous `#f0a84b`, coupure AIS
`#ef6461`, position non confirmée `#e8d45a`, navire sur liste et alerte WATCHLIST `#b48cf2`, changement d'identité
`#5fd3a5`. IBM Plex Sans et Sans Condensed, chiffres
tabulaires.

Disposition (vision, lots 1 et 2 faits) : **barre d'état** en haut (**sélecteur de région**, puis flux AIS,
ingestion, listes, disque, archivage, satellites, chacun vert, orange ou rouge avec son détail au clic ; **recherche** par Cmd + K dans les navires, infrastructures,
alertes et lieux, résultats groupés, clavier), **rail** (alertes, couches, analyses, navires suivis), panneau
contextuel, carte, **fiche** à droite, **frise** en bas.

* **Fiches** (registre `registres/sections.tsx`, composants `components/Fiches.tsx`) : navire (en tête avec photo et
  source, signal, état, suivre ; listes ; identités en frise compacte ; alertes ; comportement ; trajectoire avec
  rejeu et GPX ; notes ; sections futures déclarées), alerte (motif, preuves, navires concernés, décisions),
  infrastructure (identité, navires passés à moins de 2 milles sur la plage, alertes liées), zone (surface,
  réception, mouillages, trafic et alertes de la plage), passage satellite (heure, état acquis ou prévu, capteur,
  orbite, emprise, « non analysé », infrastructures et navires des listes couverts). Un clic sur une infrastructure,
  une zone ou une emprise de passage ouvre sa fiche.
* **Détections nocturnes VIIRS** : couche (avec AIS neutre, sans AIS couleur du navire sombre, écartée discrète),
  piste des nuits dans la frise, fiche « détection nocturne » (heure, intensité, lune, navire AIS apparié ou absence,
  alertes liées), alertes DARK_SHIP de source VIIRS dans le fil avec leurs preuves ; l'indicateur « Satellites » donne
  aussi la dernière nuit traitée et l'état du dernier travailleur.
* **Photos dans les alertes** : vignette (source VesselFinder indiquée, emplacement neutre sinon) devant chaque navire
  de « Navires concernés » ; dans le fil, très petite vignette facultative (bouton image de l'en tête du fil, réglage
  gardé dans le navigateur, désactivée par défaut).
* **Région affichée** (barre d'état) : Toute la France ou une région ; **un seul réglage** pour la carte et son
  compteur, les infrastructures, le fil, les suivis, la recherche, la frise (histogramme par région, coupures du flux
  toujours globales), les passages et le contour de la couche « couverture ». Changer de région recentre la carte.
* **Navires suivis** (table `followed_vessels`) : panneau du rail ; visibles et colorés à toutes les échelles ; leurs
  alertes à traiter en tête du fil.
* **Mode focus** : tout objet sélectionné est mis en valeur et le reste atténué (navire, alerte, infrastructure avec
  les navires passés, zone).

* **Temps** (`lib/temps.ts`) : modes direct (la plage glisse, 1 h à 30 jours), plage (période fixe, bornes
  déplaçables ou saisie libre, instant placé d'un clic) et rejeu (l'instant avance à × 1 à × 300). Tout l'écran
  s'aligne sur la plage : fil, frise, trajectoires ; les navires sont placés à l'instant.
* **Adresse de la page** (`lib/url.ts`) : mode, plage, instant, vitesse, objet sélectionné, couches, région (l'ancien
  paramètre `zone` est encore lu). Un lien
  rouvre la même vue.
* **Libellés** : tous dans `web/src/lib/libelles.ts` (français, prêt pour une traduction) ; aucun texte visible en
  dur dans les composants.
* **Registres** (`web/src/registres/`), les quatre points d'extension de la vision : `alertes.tsx` (dix types, actifs
  ou à venir : libellé, icône, couleur, navires, titre, signe distinctif, rendu des preuves), `couches.ts` (groupe,
  étape, défaut, calques MapLibre, légende ; les couches futures grisées « à venir »), `sections.tsx` (sections de
  fiche par objet, ordre, condition, étape), `frise.ts` (pistes de marqueurs) ; plus `etat.ts` pour la barre d'état.
* **Fil** (`lib/fil.ts`) : filtres par type, gravité et statut (la région vient de la barre d'état) ; tri par gravité
  puis date ; alertes d'un même
  navire regroupées ; le pavillon en tête de ligne, le signe distinctif du type ensuite.
* **Carte** : infrastructures par type (par défaut câbles électriques et parcs éoliens), tracés
  simplifiés et estompés aux échelles larges, noms à partir du zoom 9, mode « concernées seulement » (alertes
  ouvertes, ou à moins de 2 milles de la sélection) ; navires ordinaires estompés aux échelles larges, ceux des
  listes et en alerte nets et colorés.

## 8. Modèle radar et configuration

**CircleNet V2S** (xView3, membre 4, TorchScript `models/xview3_membre4_v2s.jit`, couvert par
`tests/test_contract.py`) : canaux **VH puis VV**, sigma0 en dB, normalisation sigmoïde de ((x + 20) × 0,18) ;
tuiles de 2048 pixels au pas de 1536 ; Sentinel Hub **SIGMA0_ELLIPSOID en BILINEAR** (le plus proche voisin fait
tomber le F1 de 0,96 à 0,89) ; précision mixte coupée sur le GPU Apple ; seuils présence 0,20, navire 0,338,
pêche 0,35.

`config/rules.yaml` : toutes les règles et l'ingestion. **Monter la version à chaque changement**, chaque alerte
l'enregistre. Les valeurs des règles viennent de la calibration danoise et sont **à recalibrer pour la France**
(liste et méthode : `docs/audit_code.md`, section 3).

## 9. État et mesures

**Étape 1 terminée (06/10/2026)** : collecte des quatre zones (environ 680 messages par minute), ingestion
(34 000 positions conservées par heure), mode direct, liste de surveillance (8 navires des listes dans la collecte
du 05/10, dont 3 au signal fort : GELIOTROP, VULKAN, PASIPHAE), archivage R2, purge à 30 jours, sauvegarde,
surveillance du disque.

| Mesure | Valeur |
|---|---|
| Ingestion, collecte du 05/10 | 21 197 positions lues, 11 597 conservées, 9 s pour 460 fichiers |
| Taille d'une position en base | 282 octets index compris ; équilibre à 30 jours environ 7,5 Go |
| Archive R2 | 27,4 octets par position, 22,5 par message statique ; 26 Mo par jour ; 10 Go gratuits en 13 mois |
| CircleNet | F1 0,942 (ensemble, phase 0), 0,958 avec l'harmonisation Sentinel Hub ; 1,6 s par tuile sur le Mac |

**Nettoyage après audit (06/10/2026)** : code danois retiré (lecteur DMA et son import, dans l'historique Git),
code mort supprimé, régions Manche et Gascogne en migration (14), bord des données de la coupure AIS mesuré sur
les zones réelles, terres exclues (15), test par injection adapté à un AIS allégé, carte et masques centrés sur la
France.

**Règles en continu (06/10/2026)** : dans le conteneur `taches`, toutes les 5 minutes, sur une fenêtre glissante de
24 heures bornée par la dernière position reçue (`mars/rules.py`, `run_continuous` ; section `continu` de
`config/rules.yaml`, seuils de calibration inchangés) :
* rendez vous et coupures AIS, comme avant, mais enregistrés par clé stable ; les minutes de flux AIS coupé (moins
  de 20 % de la médiane par minute) ne comptent pas dans un silence ;
* **WATCHLIST** : une alerte par passage dans nos eaux d'un navire des listes (fort, sanctionné, flotte fantôme,
  suspect GUR), gravité selon le niveau, un cran de moins si reconnu par le MMSI seul ;
* **IDENTITY_CHANGE** : nouveau nom confirmé 6 h sans retour à un nom antérieur (les alternances comme MUTIN ne
  sont jamais signalées), ou même OMI sous un autre MMSI (pavillon changé, usage simultané) ; MMSI génériques
  (code pays et six zéros, comme 227000000) écartés.
Éprouvé sur une base de test : chaque cas attendu, décisions conservées d'un cycle à l'autre, alerte vierge retirée
quand des données tardives comblent un silence, 41 fausses coupures évitées lors d'une coupure générale du flux de
140 min. Coût sur 867 000 positions (émulation, borne haute) : 1,4 s pour les coupures, 33 s pour les rendez vous
dans un cas extrême (5 700 épisodes), 181 Mo de mémoire.

**Charpente de l'interface (07/10/2026)** : lot 1 de `docs/vision_interface.md` (section 7). Mesures sur la base de
test (3 000 navires, 867 000 positions sur 24 h) : frise sur 24 h en 0,08 s (13,6 Ko), sur 7 et 30 jours en 0,02 s ;
coupure du flux de 40 min retrouvée à la minute près ; trafic à un instant, 3 042 navires en 240 Ko (78 octets par
navire) et 0,13 s ; flux en direct 274 Ko au premier envoi puis 0,1 Ko par seconde (680 Ko par seconde avant, sur
le serveur) ; infrastructures 2,6 Mo en pleine résolution, simplifiées à 50 m par l'API.

**Contenu de l'interface (07/10/2026)** : lot 2 de la vision (section 7). Constats sur les données réelles :
EMODnet ne nomme aucun câble électrique et seulement 74 câbles télécoms sur 649 (les fiches se replient sur le type
et le numéro) ; les tracés communs aux régions Bretagne et Manche, qui se chevauchent, sont stockés deux fois
(dédoublonnés à l'affichage). Le lot 1 n'avait pas été déployé côté API (transferts SSH en échec) : d'où les 680 Ko
par seconde encore mesurés sur le serveur, résolus au premier déploiement par le registre.

**Recalibration préparée et listes automatiques (07/10/2026)** (version des règles 2026.10.18, aucun seuil de
calibration changé) :
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

**Région affichée et calendrier des passages (07/10/2026)** (migration 19) : sélecteur de région dans la barre d'état
(les filtres par zone du fil et des couches sont retirés) ; réponses allégées par l'API, sur la base de test : trafic
179 Ko pour la France, 14 Ko pour la Bretagne ; infrastructures 701 Ko contre 195 Ko ; alertes 26 Ko contre 6 Ko.
Étape 3, lot A : calendrier des passages Sentinel 1 et 2 (`docs/passages_satellites.md`) : catalogue STAC public de
Copernicus Data Space pour les passages acquis, plans d'acquisition de l'ESA pour les passages prévus, réunis par
satellite et orbite absolue ; 30 jours lus en 32 s et 137 Mo de mémoire (98 passages Sentinel 1, 80 Sentinel 2,
81 prévus) ; plans confrontés au catalogue du 20/09 au 07/10 : 90 passages retrouvés sur 91 ; heure prévue
Sentinel 1 interpolée sur nos régions, 0,2 min d'écart médian ; 3,5 Ko par passage en base. Pour chaque passage :
infrastructures et navires des listes dans l'emprise (base du déclenchement au lot C). Couche « Passages Sentinel 1
et 2 » (plein acquis, pointillé prévu), piste de la frise, fiche passage, indicateur « Satellites » (prochain passage
sur la région affichée).

**Étape 3, lot B (07/10/2026)** (migration 20, règles 2026.10.19, `docs/travailleurs_viirs.md`) : architecture
« orchestrateur et travailleurs éphémères » commune aux lots B et C (instances Scaleway créées par l'API, retour par
réseau privé et jeton, durée de vie de 45 min, plafonds de 4 travailleurs et 180 minutes par jour, orphelins détruits
chaque minute, journal dans `task_runs`). VIIRS : 12 granules par nuit sur nos régions (catalogue CMR, temps quasi
réel), modèle allenai dans son image publique (886 Mo), 1,45 Go de mémoire et 12,5 s par granule mesurés en émulation,
DEV1-M ; appariement AIS (1 500 m plus l'estime), côtes à 5 km, lumières fixes (3 nuits en 30 jours, alertes
antérieures classées), DARK_SHIP de gravité élevée près d'une infrastructure ou d'un navire des listes. Coût estimé :
**environ 0,76 € HT par mois**. Éprouvé sur la base de test et avec un faux client Scaleway ; **pas encore sur une vraie
instance** (clés absentes) : la première nuit réelle donnera durée, mémoire et coût dans `task_runs`. Aussi :
nuages annoncés des passages Sentinel 2 (lot C), vignettes des navires dans les alertes, nettoyage du registre GitHub,
`/api/metrics`.

**Fiabilité des travailleurs après le premier essai réel (08/10/2026)** (migration 21) : création dans l'ordre
vérifié (éteinte, réseau privé attendu « available », cloud-init avec la MAC, démarrage attendu « running ») ; script
de démarrage qui configure l'interface privée en DHCP, contacte le serveur et lui envoie son journal ; échec de
lancement journalisé « echec » avec destruction immédiate ; destruction après 10 minutes sans signe de vie ; verrou en
base (un seul travailleur VIIRS actif) ; une même détection jamais écrite deux fois (index unique, traitement d'un
résultat verrouillé). Script de démarrage éprouvé dans un conteneur Ubuntu contre l'API locale.

**Points ouverts France** : masques France à construire sur le serveur (`docker compose exec taches python
scripts/build_masks.py --sans-cache --jours 7` : 61 s, 300 Mo de mémoire, 142 Mo de disque au plus, mesurés) ;
recalibration des seuils après une à deux semaines de mesures (liste et méthode : `docs/audit_code.md`, section 3). Mesure déjà faite sur
données synthétiques : à un point toutes les 3 minutes, `reception.min_pairs` (200, valeur danoise) écarte un tiers
des cellules d'un rail de 30 navires, et le test par injection n'a plus de candidat ; à un point toutes les
2 minutes, 5 sur 5. Le test par injection indique désormais combien de candidats il écarte, et pourquoi.

## 10. Tests

`python -m pytest tests` : 101 réussis, 6 ignorés : 1 sans `MARS_TEST_MODEL=1` (contrat du modèle), 5 sans
`MARS_TEST_DATABASE_URL` (travailleurs sur une vraie base : verrou contre les lancements concurrents, échec de
lancement, délai de démarrage, résultats reçus deux fois). Avec la base de test des migrations
(`MARS_TEST_DATABASE_URL=postgresql://mars:mars@localhost:55432/mars`) : 106 réussis, 1 ignoré. Couvrent aussi fusion,
direct, archivage, règles en continu, vérification R2 avec un faux client S3, frise, garde de destruction, ordre de
création d'un travailleur, résolution de l'image et vérification du volume de démarrage. `npx knip` et `npm run typecheck` pour
l'interface ; en développement, `MARS_API=http://localhost:8765 npm run dev` relaie une autre API que le port 8000. `npm run typecheck` pour l'interface.

## 11. Pièges connus

* Ne jamais remplacer un dossier par le Finder : utiliser `ditto`.
* **Tailwind 4** : une règle hors couche l'emporte ; la carte MapLibre se positionne par style direct.
* Tracé au trackpad en **deux clics** ; zoom par sélection désactivé.
* L'interface interroge l'API toutes les 2 s tant qu'une analyse épinglée n'est pas terminée.
* MapLibre : pas de `line-dasharray` dépendant des données (deux couches filtrées).
* NaN devient NULL avant JSON ou base ; typer les paramètres asyncpg (`$1::timestamptz`) ; psycopg n'adapte pas
  les entiers numpy.
* Les scripts visent le port 8000 (nginx coupait les requêtes longues).
* Statuts AIS déclaratifs : indices seulement. Avant de conclure à un manque du détecteur, regarder la vignette et
  les scores bruts.
* **Horodatages AISStream** : partie décimale de longueur variable, toujours `format="ISO8601"`, arrondir à la
  microseconde.
* **Identités qui alternent** (MMSI 227000000 partagé par la Marine nationale, « MUTIN » et « FS MUTIN ») : une
  identité déjà vue est reprise.
* **Entrées GUR sans nom** : l'appartenance au catalogue ne se lit pas sur le nom (cas PASIPHAE, OMI 9289518).
* Surcouche Compose : `ports` se cumule, utiliser `!override` ou `!reset`.
* **R2 et boto3** : `request_checksum_calculation="when_required"`, MD5 fourni ; l'ETag d'un envoi simple est le MD5,
  celui d'un envoi en plusieurs morceaux (sauvegardes en flux) ne l'est pas : `R2.verify` relit alors l'objet.
* **Seau R2 européen** : adresse en `https://<compte>.eu.r2.cloudflarestorage.com`, sinon tout accès est refusé.
* Pas de commentaire en fin de ligne dans `.env` : `mars/config.py` ne les retire pas.
* **Noms d'images fixes** (`mars_c2-backend`, `mars_c2-web`, `mars_c2-scripts`) : sans eux, le nom dépend du dossier
  du projet, et une image construite sur le Mac n'aurait pas le nom attendu sur le serveur.
* `LIKE 'préfixe%'` n'utilise pas un index btree avec la collation par défaut : charger les alertes en une requête
  plutôt qu'une recherche par épisode.
* Lire GSHHG avec pyshp coûte environ 120 octets par point (liste de tuples) : le polygone de l'Eurasie porte la
  mémoire au delà de 500 Mo. `build_masks.py` lit les enregistrements en numpy (16 octets par point), et découpe
  avant de réparer la géométrie.
* Une règle en continu ne doit jamais effacer puis recréer ses alertes : les décisions des opérateurs seraient
  perdues à chaque cycle (`save_alerts`).
* `pg_dump` en version 16 (dépôt PGDG dans l'image des scripts).
* Recharger d'anciens jours ne passe pas par l'ingestion en direct : `taches.py restaurer-positions`.
* La collecte écrit dans un fichier caché puis renomme ; l'ingestion ignore les fichiers de moins de 5 s.
* Tester R2 en local : `moto_server` dans un conteneur Python (MinIO n'est plus publié).
* Le navigateur sans affichage (gstack) n'a pas WebGL : la carte ne s'y dessine pas. Une limite d'erreur
  (`components/Garde.tsx`) garde le reste de l'écran ; pour voir la carte, l'ouvrir dans un vrai navigateur.
* L'horloge partagée (`sim_clock`) est commune à tous les utilisateurs : un rejeu chez l'un changeait l'écran de tous.
  L'instant est désormais tenu par l'interface (routes `?at=` sans état) ; ne plus faire dépendre l'écran de
  `/api/clock`.
* Une requête en mode plage a une clé fixe : sans nouvelle tentative, une erreur passagère (redémarrage de l'API) y
  reste affichée. Garder `retry` sur les requêtes de la frise et du trafic.
* Pour savoir quelle version d'API tourne sur le serveur : `curl -s localhost:8000/openapi.json` (liste des routes) ;
  un comportement inattendu venait d'une image non déployée, pas du code.
* `tsconfig.json` impose `noUnusedLocals` : les imports et variables inutilisés sont des erreurs de types.
* `Vessels1.db` est publié en mode WAL (octets 18 et 19 de l'en tête à 2) : `sqlite3.deserialize` le refuse
  (« unable to open database file ») ; `load_gur` les remet à 1 en mémoire.
* Les règles en continu font sur le serveur l'essentiel de leur coût dans les ports : tout filtre qui retire des
  positions avant un appariement deux à deux doit garder une marge sous le seuil final (cas du préfiltre côtier).
* **Catalogue STAC de Copernicus** : un multipolygone dont les parties se chevauchent (Bretagne et Manche) est
  invalide et renvoie une erreur 500 (« TopologyException ») ; chercher région par région et dédoublonner.
* **Identifiant de prise de vue Sentinel 1** : réattribué à l'exécution (le plan et le catalogue diffèrent de
  quelques unités) ; rapprocher plan et catalogue par satellite et orbite absolue.
* Sous zsh, une variable de boucle nommée `path` écrase le `PATH` (plus aucune commande trouvée).
* Depuis le 07/10/2026, l'application qui exécute Claude Code n'a plus accès au dossier Documents (protection de
  macOS, « Operation not permitted », même hors bac à sable) : ni le `.venv` du dépôt, ni `data/`, ni Git (le
  worktree pointe vers `.git` du dépôt principal). Contournement : environnement Python et clone du dépôt dans le
  dossier temporaire, `npm ci` dans le worktree. Rétablir : Réglages Système, Confidentialité et sécurité, Fichiers
  et dossiers (ou Accès complet au disque) pour l'application du terminal.
* Node 26 ne lit plus un `node_modules` relié par lien symbolique dans Documents : installer avec `npm ci` dans le
  worktree.
* Travailleurs : **jamais une autre instance détruite**. Toute destruction passe par `Scaleway.destroy`, qui relit
  l'instance et refuse le serveur principal (`SCW_SERVEUR_PRINCIPAL`, métadonnées), une instance protégée, un autre
  projet, un nom autre que `mars-travailleur-<n>` ou des étiquettes discordantes ; ne jamais ajouter de chemin
  destructeur qui l'évite (`action` n'accepte que le démarrage). Couvert par tests/test_travailleurs.py.
* **Premier essai réel des travailleurs (07 et 08/10/2026)** : PrivateNetworksReadOnly ne permet pas de rattacher
  une carte au réseau privé (il faut PrivateNetworksFullAccess) ; le rattachement est asynchrone, et l'image Scaleway
  « docker » ne configure pas d'elle même l'interface privée (travailleur rattaché en 172.16.8.3, injoignable) :
  attendre la carte « available » avant de démarrer, et configurer l'interface en DHCP dans le script de démarrage
  (`travailleurs/demarrage.sh`). Deux lancements simultanés (rattrapage au démarrage du conteneur et commande manuelle)
  avaient créé deux travailleurs : verrou par index unique en base. Les travailleurs n'ont pas de SSH : journal envoyé
  au serveur (`taches.py journal-travailleur`), sinon console série Scaleway.
* `SCW_PRIVATE_NETWORK_ID` est l'identifiant du réseau privé (onglet Overview du réseau), pas celui du VPC.
* **Cause réelle des premiers essais (08/10/2026)** : la console série des travailleurs affichait « UEFI Interactive
  Shell » puis `Shell>` : aucun système amorçable sur le volume de démarrage. La création imposait un volume racine
  décrit avec un nom, sans lien vérifié avec l'image (image « docker » du catalogue, ni architecture ni volume racine
  contrôlés). Désormais : image Ubuntu 24.04 résolue (zone, type, x86_64, disque local, fiche de l'image), volume
  racine décrit par sa seule taille et son type, instance relue après création (image, démarrage local, volume),
  Docker installé au démarrage.
* Travailleurs : le serveur n'expose aucun port sur Internet ; le retour passe par le réseau privé Scaleway (nginx,
  port 8090 sur `MARS_IP_PRIVEE`). Les instances CPU sont facturées à l'heure entamée, les GPU à la minute.
* Image d'un travailleur : une image publique (allenai) n'entame pas le quota de 500 Mo des paquets privés ; le
  script passe par cloud-init plutôt que par une image privée de près d'un Go.
* Photo des navires : source VesselFinder (fiche publique par MMSI), à usage personnel ; conditions d'utilisation à
  vérifier avant une démonstration publique.
