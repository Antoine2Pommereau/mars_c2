# MARS C2 : contexte complet du projet pour Claude Code

Ce fichier est la mémoire du projet. Lis le en entier avant toute tâche, et tiens le à jour à chaque jalon
(nouvelle règle, calibration, phase terminée, piège découvert). La spécification de référence est dans `docs/`
(version 2.4) si Antoine l'y a déposée.

## 0. Nouvelle direction (octobre 2026) : à lire en premier

Le projet a pivoté. La version décrite dans les sections 3 à 15 (rejeu de journées AIS danoises, analyse radar à la
demande) reste le socle technique, mais **l'objectif et le périmètre ont changé**. Le document de référence complet
est la Claude Doc « MARS C2, mise à jour du projet » :
https://claude.ai/code/artifact/00f50730-d117-4ad4-94ac-0cb9b967f002 (si elle n'est pas accessible, demander à
Antoine de la déposer dans `docs/`).

**Objectif.** Un outil opérationnel, en direct, qui protège les infrastructures maritimes françaises (câbles,
interconnexions, pipelines, parcs éoliens) et suit les navires de la flotte fantôme dans les eaux françaises, en
confrontant l'AIS déclaratif à des capteurs satellitaires indépendants. Client imaginé : une autorité française
de l'action de l'État en mer.

**Périmètre.** France uniquement, deux zones collectées dès le départ :

| Zone | Emprise (lat_min, lon_min, lat_max, lon_max) | Rôle |
|---|---|---|
| `bretagne` (rail d'Ouessant, entrée de la Manche) | 47.3, -6.8, 49.6, -3.0 | Zone pilote : flotte fantôme, câbles, approches de Brest, GeoTrackNet préentraîné |
| `mediterranee` (golfe du Lion, Marseille, Toulon, Côte d'Azur, Corse) | 41.2, 3.0, 43.7, 9.8 | Collectée dès maintenant pour accumuler l'historique ; nœud de câbles de Marseille |

**Pistes retenues.** 1. Infrastructures sous marines (ancre traînante, arrêt ou flânerie dans un corridor, coupure
AIS, rendez vous, écho sans AIS). 2. Flotte fantôme (navire de la liste identifié par son **OMI**, changements de
nom ou de pavillon, transbordement, incohérence radar et AIS). La piste brouillage GNSS est écartée.

**Sources.** AIS en direct par AISStream (WebSocket gratuit, en bêta, débit limité ; plan B : récepteur personnel
et AISHub ; MarineTraffic écarté pour son coût) ; Sentinel 1 et 2 ; VIIRS ; EMODnet (infrastructures et densités de
trafic) ; liste de la flotte fantôme du catalogue GUR (reprise par `shadow-fleet-tracker-light`, fichier `Vessels1.db`) et
OpenSanctions. Les archives (jeu de l'École navale de Brest, jeu d'Ouessant, données de GeoTrackNet, routes de
Brest, DMA) servent **uniquement à entraîner** les modèles de comportement.

**Modèles.** `allenai/vessel-detection-sentinels` (radar et optique, cap et vitesse estimés ; GPU loué à chaque
passage) ; `allenai/vessel-detection-viirs` (nuit, CPU) ; **GeoTrackNet en pilote** (modèle préentraîné sur les
cargos et pétroliers d'Ouessant, aucun réentraînement pour la zone pilote) ; TrAISformer ensuite pour anticiper
(réentraînement obligatoire : sa grille est calée sur la zone d'entraînement) ; le modèle actuel CircleNet reste en
repli et en comparaison. Environ trois mois de données suffisent pour entraîner.

**Interface.** Une seule vue, opérationnelle (fil d'alertes au centre, design actuel conservé), enrichie de quatre
emprunts à Global Fishing Watch : couches par source avec indicateur d'état, cartes de chaleur sur une période,
frise avec plage de dates, recherche et fiche navire (reprenant les fonctionnalités de `shadow-fleet-tracker-light`
dans notre interface React, pas son interface Folium).

**Stack cible.** Ingestion Python asynchrone sur un petit serveur loué allumé en permanence ; PostgreSQL, PostGIS
et TimescaleDB (30 jours en clair, compression au delà) ; archive brute en Parquet sur stockage objet ; GPU loué à
la demande. Budget : 15 à 25 € par mois.

**Plan.** 1. Socle en direct (collecte, base, archive, EMODnet, listes, mesure de couverture) ; 2. règles en
continu, recherche, fiche navire, frise par période ; 3. satellites (VIIRS chaque nuit, Sentinel 1 déclenché au
passage sur les corridors, cartes de chaleur, couches) ; 4. anticipation (TrAISformer). Le pilote GeoTrackNet
avance en parallèle : reproduire l'article, intégrer au fil d'alertes, évaluer au cas par cas (et sur les routes de
Brest), réentraîner après trois mois de collecte.

**État au 05/10/2026.** `scripts/ais_live.py` existe : `collect` archive le flux AISStream des deux zones en
Parquet (`data/ais_live/{positions,statiques}/zone=…/date=…/`), `report` mesure volume, navires, cargos et
pétroliers, retard du flux et continuité des trajectoires, avec une carte de couverture par cellule de 0,1°.
Premier point de passage : **la couverture d'AISStream autour d'Ouessant est elle suffisante ?** Sinon, récepteur.

**État au 06/10/2026 (branche `direct-ingestion`).** La collecte est branchée sur la base, prête pour le serveur
Scaleway (DEV1-S) avec Docker Compose (guide : `docs/deploiement.md`) :
* **Ingestion** (`scripts/ais_ingest.py`, `mars/ais/ingest.py`, service `ingest`) : relit toutes les 15 s les
  fichiers Parquet de la collecte (service `collector`), les charge une fois et une seule (registre
  `ingested_files`), allège les trajectoires (un point par minute en route, un toutes les dix minutes à l'arrêt, plus
  les bascules route et arrêt et les changements de statut ; section `ingestion` de `config/rules.yaml`), tient
  `ais_days` à jour. Navire identifié par son MMSI ; OMI conservé seulement si sa clé est juste ; pavillon déduit
  du MMSI (`mars/ais/mid.py`) ; historique des identités déclarées dans `vessel_identities`, relié entre MMSI par
  l'OMI (vue `imo_history`). Sur la collecte de nuit du 05/10 : 21 197 positions lues, 11 597 conservées, 1 088
  navires, 9 s pour 460 fichiers.
* **Mode direct** : l'horloge suit l'heure réelle par défaut (`sim_clock.live`) ; pause ou saut dans le passé
  passent en rejeu, le bouton « Direct » ou un rejeu qui rattrape l'heure réelle y reviennent.
* **Liste de surveillance** (`scripts/import_watchlist.py`, table `watchlist`, vue matérialisée `vessel_watch`) :
  catalogue GUR et OpenSanctions, rapprochement par OMI puis par MMSI, niveau de signal (fort, sanctionné, flotte
  fantôme, suspect GUR, autre risque). Navires des listes en violet sur la carte, fiche navire avec listes et
  identités successives. 8 navires des listes dans la collecte du 05/10, dont 3 au signal fort (GELIOTROP, VULKAN,
  PASIPHAE).

**État au 06/10/2026, fin de l'étape 1 (serveur `/opt/mars_c2`, quatre zones, environ 680 messages par minute,
34 000 positions conservées par heure).** Exploitation dans le conteneur `taches` (`scripts/taches.py`,
`mars/archive.py`, `mars/r2.py`), sans cron sur l'hôte ; guide complet dans `docs/deploiement.md` :
* **Archivage** chaque nuit à 02:30 UTC : Parquet des journées terminées compactés (zstd 9, trié par navire puis
  instant, format brut inchangé) vers Cloudflare R2, vérifiés (MD5 à l'envoi, taille et empreinte relues), inscrits
  dans `archives`, puis supprimés du serveur s'ils sont ingérés et confirmés.
* **Purge** des positions au delà de 30 jours, seulement pour les journées archivées.
* **Sauvegarde** nocturne : `pg_dump` sans les données de `positions` ni de `ingested_files`, en flux vers R2,
  7 gardées. Restauration complète éprouvée : schéma et tables depuis la sauvegarde, positions depuis l'archive
  (`taches.py restaurer-positions`, allègement dédié), fichiers locaux par l'ingestion.
* **Disque** mesuré toutes les 10 minutes, alerte sous 15 % (journal et `/api/ingestion`).
* **Horloge** : vitesse 1 en direct ; la vitesse enregistrée est celle du rejeu, remise à 1 au retour au direct.
* **Volumes à l'équilibre** : base 7,5 Go (24,5 millions de positions à 282 octets index compris) ; R2 26 Mo par
  jour (27,4 octets par position, 22,5 par message statique), 10 Go gratuits atteints en 13 mois environ.
  TimescaleDB inutile à cette échelle.

## 1. Le projet (version initiale)

**MARS C2** est une plateforme de surveillance maritime (Maritime Domain Awareness) qui fusionne deux sources :
l'**AIS**, déclaratif et falsifiable (chaque navire annonce sa position), et l'**imagerie radar Sentinel 1**,
indépendante du navire (le radar voit tout ce qui flotte, de jour comme de nuit, à travers les nuages).

Question opérationnelle : **quels navires sont en mer sans le déclarer, et que font ceux qui se déclarent ?**

C'est un projet personnel d'Antoine, pensé comme vitrine pour un poste de **Forward Deployed Engineer** : il doit
montrer la capacité à prendre un problème opérationnel flou, à intégrer des données hétérogènes, à livrer un outil
utilisable de bout en bout, et surtout à **calibrer sur des données réelles** en examinant les cas un par un.

Principes directeurs, à respecter dans toute évolution :

* **Traçabilité** : chaque alerte remonte à ses preuves (détection, positions, navires, paramètres, version des règles).
* **Mesurer plutôt qu'affirmer** : chaque choix est chiffré ; une règle silencieuse est prouvée par un test par injection.
* **Respecter la physique du capteur** (décalage Doppler, résolution de 10 m, couverture des stations AIS).
* **Tranche verticale d'abord**, puis approfondissement.
* **Calibrer au cas par cas** : chaque faux positif examiné a donné lieu à une correction documentée (section 12).
* **Déclasser plutôt qu'exclure** ; un statut AIS déclaratif est un indice, jamais une excuse.

## 2. Travailler avec Antoine

* **Répondre en français.**
* **Aucun tiret dans les textes rédigés pour Antoine**, sous aucune forme (trait d'union, tiret court, tiret long) :
  prose, documentation, commentaires destinés à être lus, messages de commit, libellés et textes de l'interface.
  Reformuler : « rendez vous », « Sentinel 1 », « MARS C2 », « bord à bord », « multi dates » devient « à plusieurs
  dates ». Dates affichées au format 17/06/2024. Le code, les commandes, les identifiants, les noms de fichiers et
  les URL ne sont pas concernés.
* Antoine travaille sur un **MacBook Air (Apple Silicon)**, Python 3.12 dans `.venv` (à activer :
  `source .venv/bin/activate`), Docker Desktop, Node installé par Homebrew. Le dépôt est dans
  `~/Documents/Projet Perso/MarsC2/mars_c2` (attention : le dossier parent s'appelle `MarsC2`, le dépôt `mars_c2`).
* Expliquer les choix et leur raison, signaler honnêtement les erreurs et les limites, chiffrer les résultats,
  terminer par la prochaine étape proposée. Antoine aime comprendre le pourquoi d'un résultat.
* Après une modification, donner les commandes de vérification (tests, requête SQL, analyse de contrôle) et ce
  qu'on doit y lire.
* Antoine a validé une direction de design précise pour l'interface (section 8) : poste de commandement épuré et
  sombre, très peu de texte. Il trouve vite une interface « trop chargée » : privilégier l'épure.
* Dépôt GitHub : https://github.com/Antoine2Pommereau/mars_c2. Enregistrer les jalons avec un commit explicite
  (sans tiret dans le message).

## 3. Démarrer et arrêter

Démarrage complet en une commande, depuis `mars_c2` :

```bash
./start.sh
```

Le script démarre la base, l'API et l'interface (Docker), puis le service d'inférence et le serveur de
développement Vite en arrière plan (journaux dans `logs/`), attend que l'inférence réponde sur `/v1/health`, et
arrête proprement ces deux derniers à Ctrl + C. Ne pas le lancer si un service d'inférence tourne déjà (conflit
sur le port 8001).

Démarrage manuel, si besoin :

```bash
docker compose up -d db backend web
source .venv/bin/activate
uvicorn inference.app:app --port 8001          # dans un terminal dédié, à laisser ouvert
cd web && npm run dev                           # optionnel : interface de développement, rechargement à chaud
```

| Adresse | Rôle |
|---|---|
| http://localhost:8080 | Interface construite (image Docker `web`), celle de la démonstration |
| http://localhost:5173 | Interface de développement (Vite), se met à jour à chaque modification |
| http://localhost:8000/api | API FastAPI (les scripts s'adressent directement à elle) |
| http://localhost:8001/v1 | Service d'inférence (hors Docker, GPU Apple) |
| localhost:5432 | PostgreSQL (`mars` / `mars`, base `mars`) |

Collecte et ingestion en continu (profil `direct`, normalement sur le serveur seulement, une collecte par clé) :
`docker compose --profile direct up -d collector ingest`.

Après une modification de `backend/` : `docker compose up -d --build backend`, puis **attendre quelques secondes**
avant d'appeler l'API (sinon « Connection reset by peer »). Après une modification de `inference/` ou de
`mars/sar/` : **redémarrer le service d'inférence** (Python ne recharge pas le code). Après une modification de
`web/` : rien en développement ; `docker compose up -d --build web` pour mettre à jour le port 8080. Après une
modification de `config/rules.yaml` : rien, le fichier est relu à chaque analyse et à chaque exécution des règles.

## 4. Architecture

| Composant | Où | Technologie | Rôle |
|---|---|---|---|
| Base | Docker `db` | PostgreSQL 16 + PostGIS 3.4 (image amd64 émulée) | Positions, navires, passages, analyses, détections, alertes et preuves, décisions, masques, échos fixes, horloge simulée |
| API | Docker `backend`, port 8000 | FastAPI, asyncpg, httpx | Horloge, trafic rejoué (SSE), passages, orchestration des analyses, fusion, règles, vignettes, décisions |
| Interface | Docker `web`, port 8080 (nginx) ; Vite en développement | React 18, TypeScript, Vite 5, Tailwind 4, MapLibre 4, TanStack Query 5, icônes `lucide-react` | Carte, fil d'alertes, analyses, frise, fiches |
| Inférence | **Hors Docker**, port 8001 | FastAPI, PyTorch (MPS), rasterio | Extraction Sentinel Hub, détection, vignettes radar ; détient le cache des extraits |
| Code partagé | `mars/` | Python | Sentinel Hub, inférence, lecture AIS, appariement, moteur de fusion |
| Collecte | Docker `collector` (profil `direct`) | Python, websockets | AISStream vers l'archive Parquet `data/ais_live` |
| Ingestion | Docker `ingest` (profil `direct`) | Python, psycopg | Parquet vers `positions`, `vessels`, `vessel_identities`, `ais_days` ; rafraîchit `vessel_watch` |

Pourquoi l'inférence est hors Docker : Docker Desktop n'a pas accès au GPU Apple (MPS). L'API joint le service via
`host.docker.internal:8001`. Une variante conteneurisée sur CPU existe (`docker compose --profile conteneur up -d`).

**Déroulé d'une analyse** : l'interface (ou `analyze_zone.py`) appelle `POST /api/analyses` avec une emprise et un
produit Sentinel 1 ; l'API vérifie la taille (50 km de côté au plus) et la présence d'AIS pour cette journée, crée
l'analyse, puis appelle `POST /v1/analyze` du service d'inférence, qui renvoie sa progression ligne par ligne
(NDJSON) : extraction (cache disque `data/sar/` ou requêtes parallèles à Sentinel Hub), détection (tuiles, décodage,
fusion des fragments, contraste local). L'API lit ensuite les positions AIS autour de l'instant du passage, applique
le moteur de fusion (`mars/fusion/pipeline.py`), la persistance, écrit détections et alertes, et diffuse la
progression dans le flux SSE.

**Flux SSE** (`/api/stream`, une fois par seconde) : horloge simulée, trafic (dernière position de chaque navire
dans les 30 minutes simulées), analyses actives (et terminées depuis moins de 20 secondes), alertes comportementales
franchies dans les 12 dernières heures simulées. L'horloge est maintenue dans les journées chargées (saut à la
journée suivante, pause à la fin de la dernière).

## 5. Arborescence

```
mars_c2/
  CLAUDE.md               ce fichier
  start.sh                démarrage complet
  docker-compose.yml      db, backend, web (inference en profil conteneur ; collector et ingest en profil direct)
  docker-compose.serveur.yml  surcouche du serveur Scaleway (ports fermés, redémarrage, journaux bornés, service taches)
  pyproject.toml          paquet mars et dépendances (pip install -e ".[test]")
  config/rules.yaml       tous les seuils, versionnés (version courante 2026.10.16)
  db/init/01 à 13         schéma et migrations (idempotentes à partir de 02)
  mars/
    config.py, db.py, geo.py
    sar/sentinelhub.py    extraction SIGMA0 bilinéaire, découpage en requêtes de 2400 px, cache
    sar/catalog.py        recherche des passages Sentinel 1
    sar/inference.py      chargement du modèle, tuilage, décodage, fusion des fragments, contraste local
    ais/dma.py            lecture et nettoyage des CSV DMA, positions à l'instant t0 (interpolation, estime)
    ais/live.py           zones, nettoyage AISStream, OMI, types, allègement des trajectoires (Thinner)
    ais/ingest.py         chargement continu du Parquet en base, identités, journées
    ais/mid.py            pavillon d'après le MMSI
    watchlist.py          lecture des listes GUR et OpenSanctions
    archive.py            archivage R2 (sélection, compactage), purge, sauvegarde, rechargement, disque
    r2.py                 client Cloudflare R2 (envoi vérifié, envoi en flux, liste, suppression)
    fusion/match.py       tolérance Doppler orientée (ellipse), appariement hongrois
    fusion/pipeline.py    masques, appariement, persistance, navires sombres, positions non confirmées
  backend/app.py          API ; backend/rules.py : rendez vous, coupures AIS, mouillages, réception
  inference/app.py        service d'inférence (analyse, vignettes, santé)
  scripts/                import, masques, règles, analyses, passages, balayage (section 13)
  tests/                  contrat du modèle et moteur de fusion
  web/                    interface React (section 8)
  data/                   ais/ (CSV DMA), sar/ (extraits en cache), masks/ (GSHHG, Natural Earth)
  models/                 xview3_membre4_v2s.jit (124 Mo, non versionné)
  docs/                   spécification
  logs/                   journaux de start.sh (non versionné)
```

## 6. Base de données

| Table | Contenu |
|---|---|
| `vessels` | Navire par MMSI : nom, type, longueur, pavillon, `ais_class` (A ou B), première et dernière vue |
| `positions` | Positions AIS : navire, instant (index btree sur `ts`), géométrie, vitesse, route, cap, `nav_status` normalisé (0 en route, 1 au mouillage, 5 amarré, 7 en pêche, 8 à la voile) |
| `sar_passes` | Produits Sentinel 1 : nom, instant, plateforme, sens d'orbite, emprise |
| `analyses` | Emprise, passage, mode, statut (`pending`, `running`, `done`, `failed`), progression, résumé, durées, erreur |
| `detections` | Échos : position, scores (présence, navire, pêche), longueur, contraste VV, `mask_reason` (terre, contraste faible, non navire, écho fixe), navire AIS apparié, coût d'appariement |
| `alerts` | Type, sévérité, `status` (`nouvelle`, `acquittee`, `confirmee`, `classee`), instant, géométrie, détails JSON, version des règles |
| `alert_evidence` | Preuves d'une alerte : analyse, détection, position ou navire |
| `alert_actions` | Journal des décisions : action (acquitter, confirmer, classer, rouvrir), note, auteur, date |
| `sim_clock` | Horloge simulée (ancre simulée, ancre réelle, vitesse, pause) ; fonction `sim_now()` |
| `ais_days` | Journées AIS chargées, volumes, emprise de l'import |
| `land` | Trait de côte GSHHG pleine résolution, subdivisé |
| `stationary_zones` | Zones de mouillage déduites de l'AIS |
| `reception_cells` | Zone de réception fiable : cellules, messages, navires, heures, continuité |
| `fixed_echoes` | Registre des échos fixes : position, première et dernière observation, nombre, détections |
| `regions`, `region_layers`, `infrastructure` | Zones France, manifeste de provisionnement, câbles, pipelines et parcs éoliens EMODnet |
| `vessel_identities` | Identités déclarées par un navire (nom, OMI, indicatif, type, pavillon) et leur période ; vue `imo_history` |
| `ingested_files` | Registre des fichiers Parquet chargés (dossier, nom, lus, conservés) |
| `archives` | Fichiers compactés envoyés sur R2 : type, zone, journée, clé, fichiers d'origine, lignes, octets, MD5 |
| `task_runs`, `disk_status` | Journal des tâches planifiées ; dernière mesure du disque |
| `watchlist` | Listes de surveillance : source, OMI, MMSI, nom, thèmes, sanctionné, flotte fantôme ; vue matérialisée `vessel_watch` (niveau par navire) |

Migrations : 02 horloge simulée, 03 analyses, 04 masques, 05 statut de navigation, 06 coupures AIS (classe, emprise,
réception), 07 continuité de réception, 08 échos fixes, 09 décisions des opérateurs, 10 régions et infrastructures, 11 direct (horloge, identités, registre), 12 liste
de surveillance, 13 exploitation (archives, tâches, disque, vitesse 1 en direct). Les appliquer avec
`docker compose exec -T db psql -U mars -d mars < db/init/0X_nom.sql`.

## 7. API

| Route | Rôle |
|---|---|
| `GET /api/health`, `GET /api/inference/health` | Santé de l'API et du service d'inférence |
| `GET, POST /api/clock` | Horloge : `live` (direct, vitesse 1), `play`, `pause`, `speed` (rejeu seulement, 409 en direct), `seek` (un instant futur ramène au direct) |
| `GET /api/stream` | Flux SSE (section 4) |
| `GET /api/traffic`, `GET /api/traffic/trails` | Trafic à l'instant simulé, traînées de 30 minutes |
| `GET /api/vessels/{id}` | Fiche navire : identité, identités successives (même MMSI ou même OMI), listes de surveillance |
| `GET /api/vessels/{id}/track?start&end` | Trajectoire d'un navire |
| `GET /api/watchlist?hours` | Navires des listes vus dans les dernières heures, du signal le plus fort au plus faible |
| `GET /api/ingestion` | État du direct : dernier fichier chargé, retard, volume de la dernière heure, listes chargées, disque (`alerte_disque`), dernière exécution de chaque tâche |
| `GET /api/ais/days` | Journées chargées |
| `GET /api/passes?bbox&start&end` | Passages Sentinel 1 sur une zone, recouvrement, disponibilité de l'AIS (par défaut sur la période chargée) |
| `POST /api/analyses` (202), `GET /api/analyses`, `GET /api/analyses/{id}`, `GET /api/analyses/{id}/detections` | Analyses radar |
| `GET /api/alerts?analysis_id`, `GET /api/alerts/live`, `GET /api/alerts/day?day` | Alertes d'une analyse, comportementales récentes, d'une journée (frise) |
| `POST, GET /api/alerts/{id}/actions` | Décisions des opérateurs |
| `GET /api/chip?lon&lat&time&size_m` | Vignette radar PNG (relayée depuis l'inférence) |
| `POST, GET /api/masks/stationary`, `POST, GET /api/masks/reception` | Masques déduits de l'AIS |
| `POST /api/rules/rendezvous/run`, `POST /api/rules/ais_gap/run`, `POST /api/rules/ais_gap/selftest` | Règles comportementales et test par injection |

Service d'inférence : `GET /v1/health`, `POST /v1/analyze` (NDJSON), `GET /v1/chip` (vignette VV lue dans les
extraits en cache `data/sar/extrait_AAAAMMJJTHHMMSS_*.tif`, PNG encodé sans dépendance).

## 8. Interface (`web/`)

**Direction de design validée par Antoine** : poste de commandement épuré et sombre (registre Palantir, Anduril).
La **couleur est réservée à ce qui demande l'attention** (les alertes) ; tout le reste est neutre. Très peu de
texte : titres d'un mot, pas de phrases d'explication (les précisions vont en infobulle).

* Jetons (`web/src/styles.css`, `@theme`) : fond `#0e1419`, panneaux `#141c23`, surélevé `#1b252e`, filets
  `#26323d`, texte `#e6ecf0`, secondaire `#7c8b97`, signal système `#4fb6c8`. Couleurs d'alerte : navire sombre
  `#e85bc7`, rendez vous `#f0a84b`, coupure AIS `#ef6461`, position non confirmée `#e8d45a`.
* Typographie : IBM Plex Sans (interface), IBM Plex Sans Condensed (horloge, chiffres clés), chiffres tabulaires.
* Mise en page : **rail d'icônes** à gauche (Alertes, Analyses radar, Couches) qui ouvre **un seul panneau à la
  fois**, carte plein écran, **fiche de détail** à droite, **frise de la journée** en bas.

| Composant | Rôle |
|---|---|
| `App.tsx` | État global : flux, analyse affichée (épinglée, sinon la plus récente du jour rejoué, sinon la plus récente), sélection, mode focus, tracé, filtres |
| `MapView.tsx` | Carte MapLibre : navires en chevrons orientés (points s'ils sont immobiles, icônes SDF teintables), traînées, détections, alertes, zones, tracé en deux clics, mode focus |
| `Rail.tsx` | Navigation ; badge des alertes à traiter |
| `AlertsPanel.tsx` | Fil unique trié par gravité ; filtres par type, onglets À traiter, Confirmées, Toutes ; zone affichée ; période 1, 6 ou 12 h |
| `AnalysesPanel.tsx`, `NewAnalysis.tsx` | Progression, résumé, historique cliquable ; nouvelle analyse (tracé, passages, lancement) |
| `LayersPanel.tsx` | Couches (analyse, couleur par type, mouillages, réception) et légende |
| `Timeline.tsx` | Lecture, vitesse, horloge, journée, frise avec les alertes et les passages Sentinel 1 |
| `DetailPanel.tsx`, `Chip.tsx`, `AlertActions.tsx` | Fiche (« pourquoi cette alerte ? »), vignette radar, décisions et journal |
| `lib/` | `api.ts` (appels), `types.ts`, `format.ts` (libellés, couleurs, dates sans tiret), `geo.ts`, `useStream.ts` |

En développement, Vite relaie `/api` vers le port 8000 (flux SSE compris). En production, nginx (`web/nginx.conf`)
relaie `/api` vers `backend:8000` avec un résolveur dynamique et sans mise en mémoire tampon pour le flux.

## 9. Données

* **AIS** : Danish Maritime Authority (aisdata.ais.dk), Skagerrak et Kattegat (`--bbox 8.5 56.0 13.0 58.6`).
  Journées chargées : **5 juin 2024** (17,2 M messages lus, 2,45 M doublons, 4,44 M positions) et **17 juin 2024**
  (20,8 M lus, 2,95 M doublons, 535 MMSI invalides, 4,70 M positions). Un tiers de doublons, structurellement
  (plusieurs stations côtières). Décompresser les archives avec `ditto -x -k` (Safari les décompresse parfois seul).
* **Radar** : Sentinel 1A, passages ascendants à 17 h 02 UTC les 5 et 17 juin (même orbite, 12 jours d'écart), via
  Sentinel Hub (CDSE) ; identifiants dans `.env`. Autres passages de juin 2024 disponibles mais sans AIS chargé.
* **Zones de test** : large Skagen `10.30 57.80 10.80 58.07`, mouillage de Skagen `10.45 57.58 10.85 57.80`, parc
  éolien d'Anholt `11.05 56.52 11.35 56.70`.
* **Modèle** : `models/xview3_membre4_v2s.jit` (aussi sur Drive, `mars_c2/phase0/results/`).
* **Trait de côte** : GSHHG 2.3.7 pleine résolution (miroir SOEST, repli NOAA 2.3.6), dans `data/masks/`.

## 10. Contrat du modèle (couvert par `tests/test_contract.py`)

CircleNet V2S, membre 4 de l'ensemble gagnant du défi xView3 (licence MIT), TorchScript.

* Canaux **VH puis VV** (l'inverse fait chuter le F1 de 0,92 à 0,81), sigma0 en dB, normalisation
  sigmoïde de ((x + 20) × 0,18), pixels manquants à zéro.
* Tuiles de 2048 pixels exactement, au pas de 1536, fusion pyramidale ; sorties à demi résolution (présence, navire,
  pêche, longueur, décalage) ; maxima locaux sur 3 pixels ; longueur = exp(sortie) moins 1, en pixels de 10 m.
* Sentinel Hub : **SIGMA0_ELLIPSOID avec rééchantillonnage BILINEAR** (le plus proche voisin par défaut fait tomber
  le F1 de 0,96 à 0,89 ; corrélation avec xView3 de 0,983), 2500 pixels au plus par requête.
* Précision mixte : utile sur GPU NVIDIA, **coupée sur le GPU Apple** (16 s contre 6 s pour 4 tuiles, résultats
  identiques ; forçable par `INFERENCE_AMP=on`).
* Seuils : présence **0,20**, navire 0,338, pêche 0,35. Fusion des fragments : 150 m ou 60 % de la longueur.

## 11. Configuration (`config/rules.yaml`, version 2026.10.16)

| Section | Paramètres clés |
|---|---|
| `model` | fichier, seuils, fusion des fragments |
| `contrast` | 10 dB au moins entre le pic VV et l'anneau de mer environnant |
| `masks` | 500 m autour de la terre |
| `fusion` | fenêtre AIS 15 min, estime 10 min au plus, rayon 300 m, Doppler 150 s, direction de vol 348° ascendant et 192° descendant, marge de 3 km au delà du bord de zone pour les candidats AIS |
| `persistence` | 60 m, dates distantes d'au moins un jour |
| `unconfirmed` | 50 m de longueur au moins, tolérance élargie de moitié, sévérité moyenne au delà de 150 m |
| `dark_ship` | critique au delà de 50 m et 20 dB |
| `rendezvous` | 500 m, 2 nœuds, 120 min, tranches de 10 min, 3 km des côtes, bord à bord 50 m, élevée au delà de 4 h ou 20 km, navires de service exclus, aucun statut exclu |
| `stationary_zones` | cellules 0,04 × 0,02°, 4 navires immobiles |
| `reception` | cellules 0,10 × 0,05°, 95 % d'intervalles sous 180 s, 200 paires, 3 navires |
| `ingestion` | un point par minute en route, un toutes les 10 min à l'arrêt, arrêt sous 0,5 nœud, route au delà de 1 nœud (hystérésis) |
| `ais_gap` | 120 min, 1 nœud, 10 messages dans l'heure, 3 km des côtes, marge de bord 0,25°, classe A, projection 120 min, arrêt sous 30 % de la vitesse si au moins 5 nœuds, route poursuivie au delà de 50 %, partenaire à 500 m des extrémités et lent 30 min, élevée au delà de 6 h sans réapparition |

**Toujours monter la version** à chaque changement de règle, puis relancer les règles (et les analyses si besoin) :
chaque alerte enregistre la version qui l'a produite.

## 12. Règles, masques et enseignements de la calibration

**Masques** (dans l'ordre) : terre GSHHG à 500 m ; contraste VV sous 10 dB (en phase 0, le grain de mer sur mer
agitée obtenait des scores supérieurs aux vrais navires, avec un contraste de 4 à 5 dB contre 22 à 44 dB) ; score
navire sous 0,338 ; écho fixe (persistance).

**Appariement** : position AIS à t0 par interpolation, sinon estime sur 10 min ; tolérance en **ellipse** orientée
le long de la trace du satellite (300 m en travers ; 300 m plus 150 s × composante de la vitesse vers le radar le
long de la trace) ; algorithme hongrois, coût augmenté d'un terme qui favorise les détections sûres.

**Navire sombre** (`DARK_SHIP`) : détection non masquée sans AIS dans la tolérance.

**Rendez vous** (`RENDEZVOUS`) : voir la configuration. Enseignements : 9 736 épisodes bruts le 5 juin, presque tous
au port ; Natural Earth ratait les îles (faux rendez vous au port de Sejerø), d'où GSHHG ; exclure le statut
« amarré » effaçait les transbordements bord à bord de SILVER KENNA (navire avitailleur, deux rencontres de plus de
9 h) ; SSI GLORIOUS se déclarait « en route » pendant 9 h 30 bord à bord ; au mouillage de Skagen, déclasser en
sévérité faible plutôt qu'exclure ; seuil côtier abaissé de 5 à 3 km (le mouillage s'étend de 4 à 8 km au large).
Résultat : 9 alertes le 5 juin, 11 le 17 juin.

**Coupure AIS** (`AIS_GAP`) : enseignements successifs : un MMSI fantôme (un seul message, code pays 506, Myanmar),
d'où l'exigence de 10 messages dans l'heure ; test par injection à 0 sur 5 parce que la zone fiable mesurait la
densité du trafic (3 700 km²), corrigé par la continuité des trajectoires (34 800 km² sur deux journées) ; ferries
partis vers Oslo ou la Norvège puis revenus (PEARL SEAWAYS, BERGENSFJORD), d'où la projection à l'estime portée à
2 h ; un navire qui poursuit sa route n'est pas suspect, un navire qui s'arrête l'est ; partenaires de circonstance
sur les zones de pêche, d'où le rayon de 500 m autour des extrémités et 30 min de lenteur. Résultat : 3 alertes le
5 juin, 8 le 17 juin ; **test par injection 5 sur 5**.

**Position AIS non confirmée** (`AIS_UNCONFIRMED`) : grand navire AIS dans la zone sans aucun écho compatible (même
masqué, hors terre), hors abords de terre et dans l'emprise du passage. Elle a révélé que le seuil de 0,30 manquait
des navires géants pourtant nets sur l'image (ARCTIC AURORA, 288 m, score 0,29), d'où le balayage du seuil.

**Persistance** : écho sans AIS revu à moins de 60 m à une autre date : masqué en écho fixe, inscrit au registre, et
les alertes antérieures sur cet écho sont **reclassées automatiquement** avec une note. 107 des 111 éoliennes
d'Anholt reconnues à partir de deux passages, sans base externe ; l'alerte erronée du 5 juin (éolienne de 50 m,
22 dB) a été reclassée.

**Effet de bord** : les navires AIS jusqu'à 3 km au delà de la zone sont candidats à l'appariement (un écho voisin
peut tomber dans la zone) ; seuls ceux de l'intérieur comptent pour les positions non confirmées.

## 13. Résultats mesurés

| Mesure | Valeur |
|---|---|
| Ensemble officiel xView3 sur une scène (phase 0) | F1 0,942 |
| V2S seul en précision mixte sur T4 (phase 0) | F1 0,916, 0,45 s par tuile |
| Harmonisation Sentinel Hub (bilinéaire contre plus proche voisin) | F1 0,958 contre 0,887 |
| Latence sur le Mac | 1,6 s par tuile ; environ 8 s pour 30 × 30 km depuis le cache, 15 s de plus pour télécharger |
| Décalage Doppler, navires en route | 410 à 494 m le long de la trace, 42 à 117 m en travers |
| Décalage, navires au mouillage | 19 m le long, 40 à 63 m en travers |
| Balayage du seuil (mouillage de Skagen) | appariées 14, 16, 17, 17, 18 et non confirmées 5, 4, 2, 2, 0 pour 0,30, 0,25, 0,20, 0,15, 0,10 ; 0,10 explose la latence (572 candidats) |
| Test par injection des coupures | 5 sur 5 |
| Persistance à Anholt | 107 échos fixes sur 111 éoliennes |
| Taille en base d'une position (2 millions) | 282 octets : table 101, index spatial 66, navire et instant 54, instant 39, clé 22 |
| Archive Parquet compactée (zstd 9) | 27,4 octets par position, 22,5 par message statique (petits fichiers : 123 et 249) |

Scripts : `import_ais.py`, `build_masks.py [--skip-land] [--source naturalearth]`,
`run_rules.py --day AAAA-MM-JJ [--selftest]`, `analyze_zone.py --bbox ... --time ...`,
`list_passes.py --bbox ... --start ... --end ...`, `sweep_threshold.py [--thresholds ...] [--keep]`.

## 14. Tests

`python -m pytest tests` : 33 tests attendus, 1 ignoré sans `MARS_TEST_MODEL=1` (qui charge le vrai modèle).
Couvrent le contrat du modèle (ordre des canaux, normalisation, tuilage, décodage, fusion des fragments) et le
moteur de fusion (masques, appariement, navire sombre, tolérance orientée, position non confirmée, écho fixe).
L'interface n'a pas de tests ; `npm run typecheck` vérifie les types.

## 15. Pièges connus

* Ne jamais remplacer un dossier par le Finder (il supprime les fichiers absents de la copie) : utiliser `ditto`.
* **Tailwind 4 et les feuilles de style tierces** : Tailwind range ses classes dans des couches CSS, et une règle
  hors couche l'emporte toujours. MapLibre impose `position: relative` à son conteneur : la classe `absolute inset-0`
  était ignorée et la carte mesurait 0 pixel. Solution : style direct (`style={{ position: "absolute", inset: 0 }}`).
* **Tracé au trackpad** : le glissé est interprété comme un double clic (zoom). Le tracé se fait en **deux clics**,
  le zoom au double clic est coupé pendant le tracé, et le zoom par sélection de MapLibre (Maj + glisser) est
  désactivé pour éviter toute confusion.
* **Rafraîchissement des analyses** : le flux ne signale une analyse terminée que 20 secondes ; l'interface interroge
  donc l'API toutes les deux secondes tant qu'une analyse épinglée n'est pas terminée.
* Pas de style MapLibre dépendant des données pour `line-dasharray` : deux couches filtrées.
* NaN doit devenir NULL avant JSON ou base (`finite()` dans l'API ; jamais de texte « NaN » à l'import).
* Typer explicitement les paramètres SQL dans asyncpg (`$1::timestamptz`, `$2::int`, `$3::bigint[]`), y compris
  dans `least`, `greatest` et `make_interval`.
* Les scripts visent le port 8000 : nginx coupait les requêtes longues.
* Le rejeu avance même sans personne devant l'écran : il est désormais borné aux journées chargées.
* Les statuts AIS sont déclaratifs et souvent faux : indices seulement.
* Avant de conclure à un manque du détecteur, regarder l'image (vignette) et les scores bruts : ni les données ni le
  GPU n'étaient en cause pour les navires géants manqués, c'était le seuil.
* `ditto ~/Downloads/<dossier>/mars_c2 .` : vérifier le nom réel du dossier décompressé, il varie.
* **Horodatages AISStream** : partie décimale de longueur variable (zéros finaux omis). Toujours
  `pd.to_datetime(..., format="ISO8601")`, sinon pandas déduit le format de la première ligne et met les autres à
  NaT sans erreur. Arrondir à la microseconde avant la base.
* **Identités qui alternent** : le MMSI 227000000 (« FRENCH WARSHIP ») est partagé par plusieurs bâtiments de la
  Marine nationale ; MUTIN émet tour à tour « MUTIN » et « FS MUTIN ». Une identité déjà vue est reprise, pas
  dupliquée : deux lignes aux périodes entrelacées.
* **Entrées GUR sans nom** : l'appartenance au catalogue ne doit pas se lire sur la présence du nom (PASIPHAE,
  OMI 9289518, listé sous son ancien MMSI hondurien, passait pour « flotte fantôme » au lieu de « fort »).
* psycopg n'adapte pas les entiers numpy : convertir en `int` avant toute requête.
* Surcouche Compose : `ports` se cumule entre fichiers ; utiliser `!override` ou `!reset` (Compose 2.24.4 ou plus).
* **R2 et boto3** : les versions récentes de boto3 ajoutent des sommes de contrôle que R2 n'accepte pas toutes :
  `request_checksum_calculation="when_required"`, et MD5 fourni explicitement (`ContentMD5`). L'ETag d'un envoi en
  une requête est le MD5 : c'est ce qui est relu pour confirmer l'archive.
* `pg_dump` doit avoir la version majeure du serveur (16) : l'image des scripts l'installe depuis le dépôt PGDG.
* **Recharger d'anciens jours ne passe pas par l'ingestion en direct** : son allègement écarte tout message plus
  ancien que le dernier point gardé du navire. `restaurer-positions` utilise une instance neuve, jour par jour.
* Une sauvegarde capture sa propre tâche « en cours » : à la restauration (et après un redémarrage), la boucle des
  tâches marque ces lignes en échec.
* Tester R2 en local : l'image MinIO n'est plus publiée sur Docker Hub ni quay.io ; `moto_server` dans un
  conteneur Python fait office de S3 (créer le seau en région `us-east-1`).
* Typer `ts` en horodatage dans l'archive ne gagne que 10 % (nanosecondes incompressibles) : format brut conservé.
* La collecte écrit dans un fichier caché puis renomme : l'ingestion ne lit jamais un fichier incomplet (et ignore
  de toute façon les fichiers de moins de 5 s).

## 16. Points ouverts

* SEA HAWK (17 juin) a perdu son partenaire HG35 VENDELBO (4 m du trajet présumé, loin des extrémités) avec le
  critère resserré : compromis assumé, à réexaminer avec plus de journées.
* Beaucoup de coupures du 17 juin sont des navires de pêche (immatriculations HG, HM, S) sur leurs zones de pêche :
  pas le schéma du transbordement, mais pertinent (AIS obligatoire au delà de 15 m en Europe). Piste : un contexte
  « pêche » dédié.
* Les zones de mouillage s'étendent avec les journées chargées : des rendez vous du 5 juin sont passés en sévérité
  faible (dont XANTHIA et VINGAREN). Comportement voulu, à expliquer à l'opérateur.
* AIDANOVA (337 m, score 0,13) et MAERSK INVOLVER (138 m, 0,13) restent sous le seuil : le mode complet devrait les
  voir.
* 4 éoliennes d'Anholt non reconnues (une troisième date les rattraperait).
* Le seuil de 0,20 et le contraste de 10 dB n'ont pas été validés sur une vérité terrain : c'est l'objet de la phase 6.

## 17. Suite

Remplacée par le plan de la section 0. L'ancienne phase 6 (évaluation sur xView3) reste utile comme volet
d'évaluation du détecteur radar, mais n'est plus prioritaire.

## 18. Historique des phases

* **Phase 0** : validation des hypothèses (reproduction exacte du script officiel, mode rapide, latence sur le Mac,
  harmonisation Sentinel Hub, filtre de contraste, premier candidat navire sombre de 43 m au nord de Skagen).
* **Phase 1** : tranche verticale (une commande, le navire sombre sur la carte).
* **Phase 2** : rejeu AIS par horloge en base, 1 000 navires à 60 fois le temps réel.
* **Phase 3** : analyse à la demande, service d'inférence natif, progression en direct.
* **Phase 4** : masques, quatre types d'alertes calibrés sur deux journées, tolérance Doppler orientée, seuil
  abaissé avec fusion des fragments, persistance des échos fixes.
* **Phase 5** : interface React (design épuré, rail, fil d'alertes, frise, fiche avec vignette radar, décisions des
  opérateurs, lancement d'analyse depuis la carte, historique, filtres, mode focus), servie sur le port 8080.
