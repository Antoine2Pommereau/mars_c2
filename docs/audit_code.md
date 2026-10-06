# Audit du code, 06/10/2026

Audit du dépôt après le pivot du rejeu de journées danoises vers la surveillance en direct de la France
(infrastructures et flotte fantôme). **Rien n'a été supprimé ni déplacé** : ce document propose, Antoine décide.
Base auditée : `main` au commit `145ae68`.

## Résumé

* **Le pivot a laissé peu de code mort.** Sur environ 7 000 lignes suivies (Python, SQL, configuration, interface), environ 200 sont
  propres au Danemark (lecteur DMA et son script d'import), environ 130 sont mortes ou redondantes (un script
  supplanté, deux routes doublées par le flux SSE, une fonction de l'interface), et deux dossiers d'outils
  (`.idea/`, `mars_c2.egg-info/`) sont suivis par erreur. Tout le reste sert le direct ou les étapes 2 à 4.
* **La vraie dette est ailleurs : dans les valeurs calibrées et la documentation.** Les seuils des règles ont été
  réglés sur deux journées danoises **non allégées** (un message toutes les 2 à 10 secondes par navire, stations
  côtières denses), alors que la base reçoit maintenant un AIS AISStream clairsemé et allégé à un point par minute.
  Tous les seuils de densité sont à remesurer (section 3). CLAUDE.md fait 510 lignes, dont la moitié raconte le
  Danemark.
* **Trois défauts réels relevés, à corriger avant les règles en continu (étape 2)** :
  1. la coupure AIS juge le « bord des données » sur l'emprise de `ais_days`, qui pour la France est le rectangle
     englobant les quatre zones, terres comprises : la règle ne sait pas où les données s'arrêtent vraiment ;
  2. les régions `Manche` et `Gascogne`, attendues par `/api/infrastructure`, ne sont créées par aucune migration
     (seules `Bretagne` et `Mediterranee` le sont) : une base neuve ne les a pas ;
  3. le test par injection choisit des navires ayant au moins 100 messages en 5 heures et 8 nœuds de moyenne : avec
     l'allègement et la couverture AISStream (une dizaine de messages par heure et par navire en moyenne sur la
     collecte du 05/10), il risque de ne trouver aucun candidat.
* **Couplage à défaire** : la fusion radar et AIS (à garder) importe `positions_at` depuis `mars/ais/dma.py`, le
  module danois. Il faut sortir cette fonction avant d'archiver le lecteur DMA.
* Les exigences sont respectées par toutes les propositions : service d'inférence et CircleNet, fusion radar et
  AIS, persistance des échos fixes, moteur de règles et test par injection sont classés « garder ».

## Méthode

Tout est reproductible depuis la racine du dépôt :

```bash
python3 -m venv /tmp/outils && /tmp/outils/bin/pip install vulture ruff
/tmp/outils/bin/vulture backend mars scripts inference tests --min-confidence 60
/tmp/outils/bin/ruff check --select F,ARG backend mars scripts inference tests
cd web && npx knip@5 && npx ts-prune && npx tsc --noEmit
grep -rnE "^\s*(from|import) (mars|rules|inference)[. ]" backend mars scripts inference tests   # graphe des imports
grep -noE "(get|post)<[^>]*>\(\`?\"?/[a-z_/{}\$.?=&]*|EventSource" web/src/lib/*.ts             # appels de l'interface
python -m pytest tests                                                                             # 33 réussis, 1 ignoré
```

Versions : vulture 2.16, ruff 0.16.10, knip 5, `ts-prune` 0.10. Légende des statuts :

| Statut | Sens |
|---|---|
| **Direct** | Utilisé par la plateforme en direct (serveur, interface, exploitation) |
| **Étape 2, 3 ou 4** | Pas encore branché sur le direct, utile pour l'étape indiquée du plan (2 : règles en continu, recherche, fiche navire, frise par période ; 3 : satellites ; 4 : anticipation) |
| **Danemark** | Historique danois, sans usage en France |
| **Mort** | Plus appelé, ou redondant avec autre chose |

Décisions : **garder**, **remanier** (garder en le modifiant), **archiver** (sortir du dépôt courant vers
`docs/historique_danemark.md` ou une branche `archive/danemark`), **supprimer**.

## 1. Inventaire, statut et décision

### `backend/` (API)

| Élément | Lignes | Statut | Décision | Raison |
|---|---|---|---|---|
| `app.py` | 903 | Direct | **Remanier** | Cœur de l'API, 31 routes dont 29 appelées (section 2). Le fichier mêle horloge, trafic, navires, radar, règles et exploitation : le découper en modules (`routes/horloge.py`, `navires.py`, `radar.py`, `regles.py`, `exploitation.py`) avant l'étape 2, qui va l'alourdir. Supprimer `/api/traffic` et `/api/alerts/live`, doublés par le flux SSE. |
| `rules.py` | 374 | Étape 2 | **Garder, remanier** | Moteur de règles (rendez vous, coupure AIS, mouillages, réception), à garder absolument. Lancé à la main, journée par journée : à passer en continu (étape 2). Remplacer l'emprise de `ais_days` par les polygones des zones (`regions`) pour le bord des données. |
| `Dockerfile`, `requirements.txt` | 18 | Direct | Garder | |

### `mars/` (code partagé)

| Élément | Lignes | Statut | Décision | Raison |
|---|---|---|---|---|
| `config.py`, `db.py`, `geo.py` | 49 | Direct | Garder | Importés par l'API, l'ingestion, les tâches et l'inférence. |
| `ais/live.py`, `ais/ingest.py`, `ais/mid.py` | 508 | Direct | Garder | Collecte, ingestion, allègement, identités, pavillon. Couverts par `tests/test_live.py`. |
| `ais/dma.py`, fonction `positions_at` | 26 | Étape 3 | **Remanier** | Position AIS à l'instant du passage radar (interpolation, estime), utilisée par `fusion/pipeline.py`. N'a rien de danois : la déplacer dans `mars/fusion/positions.py`. |
| `ais/dma.py`, le reste (`read_dma_csv`, `nav_status_code`, `_drop_jumps`) | 89 | Danemark | **Archiver** | Lecteur des CSV de la Danish Maritime Authority ; seul appelant : `scripts/import_ais.py`. À archiver après le déplacement de `positions_at`. Le filtre de sauts impossibles (`_drop_jumps`) est une bonne idée à reprendre dans `ais/live.py` (absent de l'ingestion en direct). |
| `archive.py`, `r2.py` | 352 | Direct | Garder | Archivage R2, purge, sauvegarde, restauration, disque. Petit nettoyage : argument `log` inutilisé dans `run_purge` (ruff). |
| `watchlist.py` | 72 | Direct | Garder | Listes GUR et OpenSanctions. |
| `fusion/match.py`, `fusion/pipeline.py` | 247 | Étape 3 | **Garder** | Fusion radar et AIS, navire sombre, position non confirmée, persistance des échos fixes. Exigé. Couverts par `tests/test_fusion.py`. |
| `sar/catalog.py`, `sar/sentinelhub.py`, `sar/inference.py` | 321 | Étape 3 | **Garder** | Recherche des passages, extraction Sentinel Hub, CircleNet. Exigé. `catalog.py` sert déjà `/api/passes`. Couverts par `tests/test_contract.py`. |
| `regions/provision.py` | 155 | Direct | Garder, remanier | Infrastructures EMODnet. Argument `refresh` inutilisé (vulture, confiance 100 %). |

### `inference/` (service radar, Mac)

| Élément | Lignes | Statut | Décision | Raison |
|---|---|---|---|---|
| `app.py`, `Dockerfile`, `__init__.py` | 189 | Étape 3 | **Garder** | Service d'inférence, exigé. Ses trois routes (`/v1/health`, `/v1/analyze`, `/v1/chip`) sont appelées par l'API ; vulture les signale à tort (routes déclarées par décorateur). |

### `scripts/`

| Élément | Lignes | Statut | Décision | Raison |
|---|---|---|---|---|
| `ais_live.py` (`collect`) | 220 | Direct | Garder, remanier | Collecte du service `collector`. La docstring parle encore de « deux zones » (il y en a quatre). `report` ne lit que le Parquet local, qui ne garde plus que la journée en cours et la veille depuis l'archivage : le faire lire dans la base ou sur R2. |
| `ais_ingest.py`, `taches.py`, `import_watchlist.py` | 292 | Direct | Garder | Services `ingest` et `taches`, import des listes. |
| `provision_region.py` | 59 | Direct | Garder | Provisionnement EMODnet. Variable `n` inutilisée (ruff). |
| `Dockerfile`, `requirements.txt` | 25 | Direct | Garder | Image des services `collector`, `ingest`, `taches`. |
| `watchlist_check.py` | 113 | Mort | **Supprimer** | Supplanté par la table `watchlist`, la vue `vessel_watch` et `/api/watchlist`. Il lit le Parquet local, qui ne contient plus que deux journées au plus. |
| `import_ais.py` | 112 | Danemark | **Archiver** | Import des CSV DMA. |
| `build_masks.py` | 136 | Étape 2 | **Remanier** | Trait de côte GSHHG, zones de mouillage et de réception, indispensables aux règles. Emprise par défaut `4 53 17 60` (Danemark) : passer aux quatre zones France. Les masques déduits de l'AIS sont à recalculer régulièrement (tâche nocturne). |
| `run_rules.py` | 34 | Étape 2 | Garder | Lancement manuel des règles et du test par injection. Exemple danois dans la docstring. Deviendra un outil de contrôle une fois les règles en continu. |
| `analyze_zone.py`, `list_passes.py` | 89 | Étape 3 | Garder, remanier | Outils radar. Exemples danois (Skagen, Anholt) à remplacer par des zones françaises. |
| `sweep_threshold.py` | 96 | Danemark, utile étape 3 | **Remanier** | Balayage du seuil de présence : zones Skagen codées en dur. La méthode servira à recalibrer le seuil en France ; passer les zones et l'instant en arguments. |
| `start.sh` | 21 | Mac | Garder, remanier | Démarrage local. Lance `web` deux fois (`up -d db backend web web`). |

### `web/src/` (interface)

knip et `ts-prune` ne trouvent qu'une fonction morte ; toutes les méthodes de `api.ts` sont appelées.

| Élément | Statut | Décision | Raison |
|---|---|---|---|
| `App.tsx`, `Rail.tsx`, `DetailPanel.tsx`, `AlertActions.tsx`, `AlertsPanel.tsx`, `lib/api.ts`, `types.ts`, `useStream.ts`, `geo.ts`, `styles.css` | Direct | Garder | |
| `MapView.tsx` | Direct | **Remanier** | Carte centrée sur Skagen au chargement (`center: [10.6, 57.6], zoom: 7.5`) : centrer sur la France. |
| `Timeline.tsx` | Direct | Remanier (étape 2) | Frise d'une journée : passer à la frise par période prévue au plan. |
| `LayersPanel.tsx` | Direct | Garder | Les couches mouillages et réception seront vides tant que `build_masks.py` n'a pas tourné pour la France. |
| `AnalysesPanel.tsx`, `NewAnalysis.tsx`, `Chip.tsx` | Étape 3 | Garder | Analyses radar. |
| `lib/format.ts`, fonction `toInputValue` | Mort | **Supprimer** | Jamais importée (knip, `ts-prune`). |
| `lib/api.ts`, types `VesselIdentity`, `VesselCard`, `AlertActionRow` | Direct | Remanier | Exportés sans être importés ailleurs (knip) : retirer `export`, détail cosmétique. |
| Fichiers de configuration (`package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, `nginx.conf`, `Dockerfile`) | Direct | Garder | |

### `db/init/` (schéma)

| Élément | Statut | Décision | Raison |
|---|---|---|---|
| `01` à `13` | Direct | **Garder tous** | Chaîne de création d'une base neuve : aucun fichier ne peut sortir sans casser l'initialisation. `02` place l'horloge au 05/06/2024, sans effet puisque le direct est actif par défaut. |
| `10_regions_infrastructure.sql` | Direct | Remanier (nouvelle migration `14`) | Ne crée que `Bretagne` et `Mediterranee`, alors que l'API attend aussi `Manche` et `Gascogne`. |

### `config/`, `tests/`, `docs/`, racine

| Élément | Statut | Décision | Raison |
|---|---|---|---|
| `config/rules.yaml` | Direct | **Remanier** | Structure à garder ; valeurs danoises à recalibrer (section 3). |
| `tests/test_contract.py`, `tests/test_fusion.py` | Étape 3 | Garder | Contrat de CircleNet et moteur de fusion. Les coordonnées de `test_fusion.py` sont à Skagen mais purement géométriques : sans conséquence. |
| `tests/test_live.py`, `tests/test_archive.py` | Direct | Garder | |
| Tests absents | | À créer (étape 2) | Rien ne teste `backend/rules.py` ni l'API hors du test par injection, qui s'exécute à la demande. |
| `docs/deploiement.md` | Direct | Garder | |
| `docs/inventaire_worktree.md` | Étapes 2 à 4 | Garder, remanier | Liste des briques du worktree de calibration encore à porter (alerte menace infrastructure, Tip and Cue, recherche, score de risque). La ligne « liste de la flotte fantôme, à créer » est faite. |
| `CLAUDE.md` | Direct | **Remanier** | Section 5. Contient aussi une affirmation fausse : « l'horloge est maintenue dans les journées chargées (saut à la journée suivante, pause à la fin de la dernière) » ; ce comportement n'existe plus dans le code. |
| `README.md` | Danemark | **Remanier** | Décrit la « phase 4 en cours » au Danemark. Le réduire à une présentation courte qui renvoie à CLAUDE.md et `docs/`. |
| `.env.example` | Direct | Remanier | Ne liste que Sentinel Hub et la base : ajouter `AISSTREAM_API_KEY`, `R2_*`, `COMPOSE_FILE`, `COMPOSE_PROFILES`. |
| `.gitignore` | Direct | Remanier | Ajouter `.idea/` et `*.egg-info/`. Une ligne en double (`logs/`). |
| `.idea/` (7 fichiers), `mars_c2.egg-info/` (5 fichiers) | Mort | **Supprimer du suivi git** | Réglages de l'éditeur et fichiers générés par `pip install -e` (`git rm --cached`, sans les effacer du disque). |
| `docker-compose.yml`, `docker-compose.serveur.yml`, `.dockerignore`, `pyproject.toml` | Direct | Garder | Le profil `conteneur` (inférence sur CPU) relève de l'étape 3. |

## 2. Preuves

### Routes de l'API et leurs appelants réels

| Route | Appelant | Verdict |
|---|---|---|
| `GET /api/stream` | `useStream.ts` (EventSource) | Direct |
| `POST /api/clock` | `App.tsx` (`api.clock`) | Direct |
| `GET /api/clock` | aucun dans le code ; commandes de vérification de `docs/deploiement.md` | Garder (contrôle) |
| `GET /api/traffic` | **aucun** ; l'horloge et le trafic arrivent par le flux | **Supprimer** |
| `GET /api/traffic/trails` | `App.tsx` (`api.trails`) | Direct |
| `GET /api/vessels/{id}` | `DetailPanel.tsx` (`api.vessel`) | Direct |
| `GET /api/vessels/{id}/track` | `App.tsx` (`api.track`) | Direct |
| `GET /api/watchlist` | aucun dans l'interface ; documentation | Garder (recherche et fiche, étape 2) |
| `GET /api/ingestion` | aucun dans l'interface ; exploitation | Garder (indicateur d'état des couches, étape 3) |
| `GET /api/ais/days` | `App.tsx` (`api.days`) | Direct |
| `GET /api/passes` | `NewAnalysis.tsx`, `analyze_zone.py`, `list_passes.py`, `sweep_threshold.py` | Étape 3 |
| `POST /api/analyses` | `NewAnalysis.tsx`, `analyze_zone.py`, `sweep_threshold.py` | Étape 3 |
| `GET /api/analyses`, `GET /api/analyses/{id}/detections` | `App.tsx` | Étape 3 |
| `GET /api/analyses/{id}` | `analyze_zone.py`, `sweep_threshold.py` | Étape 3 |
| `GET /api/inference/health` | aucun | Garder (état de la couche radar, étape 3) |
| `GET /api/chip` | `Chip.tsx` (`chipUrl`) | Étape 3 |
| `POST /api/masks/stationary`, `POST /api/masks/reception` | `build_masks.py` | Étape 2 |
| `GET /api/masks/stationary`, `GET /api/masks/reception`, `GET /api/infrastructure` | `App.tsx` | Direct |
| `POST /api/rules/rendezvous/run`, `POST /api/rules/ais_gap/run`, `POST /api/rules/ais_gap/selftest` | `run_rules.py` | Étape 2 (à garder) |
| `GET /api/alerts`, `GET /api/alerts/day` | `App.tsx` | Direct |
| `POST, GET /api/alerts/{id}/actions` | `AlertActions.tsx` | Direct |
| `GET /api/alerts/live` | **aucun** ; les alertes récentes arrivent par le flux (`live_alerts`) | **Supprimer** |
| `GET /api/health` | aucun | Garder (sonde de santé) |

### Graphe des imports Python (extrait utile)

* `backend/app.py` importe `mars.config`, `mars.fusion.pipeline`, `mars.geo`, `mars.sar.catalog` et `rules`.
* `mars/fusion/pipeline.py` importe **`mars.ais.dma.positions_at`** : c'est le seul lien entre le code vivant et le
  module danois.
* `mars.ais.dma.read_dma_csv` n'a qu'un appelant, `scripts/import_ais.py` ; `nav_status_code` et `_drop_jumps` ne
  servent qu'à l'intérieur de `dma.py`.
* `backend/rules.py` : ses cinq fonctions publiques sont appelées par `app.py`, et par lui seul.
* `mars/regions/provision.py` n'est appelé que par `scripts/provision_region.py`.
* `inference/app.py` importe `mars.sar.inference`, `mars.sar.sentinelhub`, `mars.config`.

### Résultats des outils

**vulture** (confiance 60 % et plus) : 31 signalements. 30 sont des faux positifs, les fonctions de routes de
FastAPI (27 dans `backend/app.py`, 3 dans `inference/app.py`), appelées par le cadre via leur décorateur ; le
tableau ci dessus vérifie leurs appelants un par un. 1 vrai : `mars/regions/provision.py:150`, argument `refresh`
inutilisé (confiance 100 %).

**ruff** (`F`, `ARG`) : 4 signalements. `inference/app.py:49` argument `app` du cycle de vie (imposé par FastAPI,
faux positif) ; `mars/archive.py:217` argument `log` inutilisé ; `mars/regions/provision.py:150` argument
`refresh` ; `scripts/provision_region.py:53` variable `n` inutilisée. Aucun import inutile, aucun nom indéfini.

**knip et `ts-prune`** : une fonction exportée jamais importée, `toInputValue` (`web/src/lib/format.ts:57`) ; trois
types exportés sans import extérieur (`VesselIdentity`, `VesselCard`, `AlertActionRow`). Aucun fichier, aucune
dépendance npm inutilisée. `tsc --noEmit` : aucune erreur.

**Tests** : 33 réussis, 1 ignoré (chargement du vrai modèle, `MARS_TEST_MODEL=1`).

**Références au Danemark dans le code** (hors documentation) : `mars/ais/dma.py`, `scripts/import_ais.py`,
`scripts/sweep_threshold.py` (zones et instant), exemples des docstrings de `analyze_zone.py`, `list_passes.py`,
`run_rules.py`, `build_masks.py`, emprise par défaut de `build_masks.py`, centre de la carte dans `MapView.tsx`,
date d'ancrage de l'horloge dans `02_simulation.sql`, commentaires de `01_schema.sql`, `10` et `11`, et
`backend/app.py:632`.

## 3. Paramètres de `config/rules.yaml` calibrés sur le Danemark

Contexte commun : la calibration danoise reposait sur deux journées de la Danish Maritime Authority, avec un tiers
de doublons et un message toutes les 2 à 10 secondes par navire, captées par un réseau dense de stations
côtières, à 57° N, en mer fermée (Skagerrak, Kattegat). La France combine un AIS AISStream plus clairsemé (environ
10 messages par heure et par navire en moyenne sur la collecte du 05/10), allégé en base à un point par minute en
route et un toutes les dix minutes à l'arrêt, des latitudes de 41 à 51° N et l'Atlantique, plus agité.

| Paramètre | Valeur | Calé sur | Pourquoi recalibrer, et comment |
|---|---|---|---|
| `model.thresholds.objectness` | 0,20 | Balayage au mouillage de Skagen | Mer plus formée en Atlantique : plus de faux échos possibles. Rejouer `sweep_threshold.py` sur des passages au large d'Ouessant et du golfe du Lion. |
| `contrast.min_vv_db` | 10 dB | Grain de mer du Kattegat (phase 0) | Même raison ; mesurer le contraste des faux échos sur des passages par mer forte. |
| `fusion.flight_heading_deg` | 348 et 192 | 57° N | La direction de vol de Sentinel 1 dépend de la latitude ; à recalculer pour 41 à 51° N (de l'ordre de quelques degrés d'écart), au mieux à lire dans les métadonnées du produit. |
| `fusion.doppler_s` | 150 s | Décalages mesurés au Danemark (410 à 494 m) | Physique du capteur, a priori transposable ; à vérifier sur quelques appariements français. |
| `fusion.max_gap_min`, `ais_window_min` | 10 et 15 min | AIS dense | Avec l'allègement (un point toutes les dix minutes à l'arrêt) et les trous d'AISStream, vérifier la part des navires interpolés plutôt qu'estimés. |
| `rendezvous.min_coast_km` | 3 km | Mouillage de Skagen, de 4 à 8 km au large | Mouillages français différents (rade de Brest, golfe de Fos, Cherbourg, Hyères). À revoir après le calcul des zones de mouillage françaises. |
| `rendezvous.slot_min`, `max_gap_min` | 10 et 20 min | AIS dense | Un navire arrêté n'a plus qu'un point toutes les dix minutes en base : une tranche sur deux peut être vide. Vérifier que les épisodes ne se fragmentent pas, ou passer à 15 et 30 min. |
| `rendezvous.high_coast_km`, `high_duration_min` | 20 km, 240 min | Kattegat | Sévérités à réexaminer cas par cas en France. |
| `stationary_zones.cell_deg_lon`, `cell_deg_lat` | 0,04 et 0,02° | « environ 2,4 km à ces latitudes » (57° N) | À 43° N, 0,04° de longitude valent 3,3 km : cellules plus larges. Passer à des cellules en mètres, ou adapter par zone. |
| `stationary_zones.min_slow_positions` | 30 | Positions brutes DMA | Avec un point toutes les dix minutes à l'arrêt, 30 positions lentes demandent cinq heures d'un même navire : seuil environ dix fois trop exigeant. |
| `reception.cell_deg_lon`, `cell_deg_lat` | 0,10 et 0,05° | 57° N | Même remarque qu'au dessus. |
| `reception.max_interval_s`, `min_coverage` | 180 s, 95 % | Continuité des stations danoises | Avec AISStream, peu de cellules atteindront 95 % d'intervalles sous 3 minutes. Mesurer la distribution réelle (`ais_live.py report` donne déjà une continuité par cellule) avant de fixer le seuil. |
| `reception.min_pairs` | 200 | Densité DMA | Les paires sont divisées par l'allègement : seuil à abaisser. |
| `ais_gap.min_prior_messages` | 10 dans l'heure | Écarter le MMSI fantôme danois (code 506) | Une dizaine de messages par heure en moyenne avec AISStream : la règle écarterait une grande partie des navires. Mesurer la distribution, abaisser (5 ?) en gardant l'exclusion des MMSI à message unique. |
| `ais_gap.edge_margin_deg` | 0,25° | Emprise rectangulaire de l'import danois | **Défaut** : en France, l'emprise de `ais_days` est le rectangle des quatre zones, terres comprises. Mesurer la distance au bord des polygones de zone (`regions`). |
| `ais_gap.projection_min` | 120 min | Ferries vers Oslo (PEARL SEAWAYS, BERGENSFJORD) | Logique transposable (ferries Roscoff, Cherbourg, Corse), à vérifier sur les premières coupures françaises. |
| `ais_gap.excluded_ship_types` | navires de service | Libellés DMA | Libellés repris tels quels par l'ingestion : compatible. Mais la pêche n'est pas exclue, et la flotte bretonne est nombreuse : prévoir le contexte « pêche » déjà identifié au Danemark. |
| `ais_gap.partner_*` | 500 m, 30 min, 2 nœuds, 5 positions | Zones de pêche danoises | `partner_min_positions` à revoir avec l'allègement. |
| `unconfirmed`, `dark_ship`, `persistence`, `masks` | | Génériques | Liés au capteur, pas à la mer : à garder, à vérifier sur les premiers cas français. |
| `ingestion` | | France | Déjà réglé pour le direct. |

Constantes codées en dur, même problème :

| Où | Valeur | Remarque |
|---|---|---|
| `backend/app.py`, test par injection | coupure à 11:00 UTC pendant 3 h, candidats à 100 messages au moins sur 5 h et 8 nœuds de moyenne, 40 candidats, 10 km des côtes | 100 messages en 5 heures n'est atteint qu'avec un AIS dense et non allégé : à abaisser (30 ?) et à choisir dans les zones de réception françaises. |
| `scripts/build_masks.py` | emprise `4 53 17 60` | Danemark. |
| `web/src/components/MapView.tsx` | centre `10.6, 57.6` | Skagen. |

Ordre conseillé : mesurer d'abord (une à deux semaines de collecte, distribution des intervalles par navire et
par cellule, puis `build_masks.py` sur la France), recalibrer ensuite, et prouver la coupure AIS par le test par
injection avant de la mettre en continu.

## 4. Plan d'action par décision

**Garder sans changement** : services du direct (`mars/ais/live.py`, `ingest.py`, `mid.py`, `archive.py`, `r2.py`,
`watchlist.py`, scripts des services), service d'inférence, CircleNet et `mars/sar/`, fusion et persistance
(`mars/fusion/`), moteur de règles et test par injection, toutes les migrations, tests, interface hors points ci
dessous, Docker.

**Remanier, par ordre d'intérêt** :
1. `backend/rules.py` : bord des données mesuré sur les polygones de zone, pas sur `ais_days` (défaut réel).
2. Nouvelle migration `14` : créer les régions `Manche` et `Gascogne`.
3. Test par injection : critères de sélection adaptés à un AIS allégé.
4. `config/rules.yaml` : recalibration après mesure (section 3), avec montée de version.
5. `positions_at` déplacée de `mars/ais/dma.py` vers `mars/fusion/positions.py`.
6. `build_masks.py` : emprise France par défaut.
7. `backend/app.py` : découpage en modules, retrait de `/api/traffic` et `/api/alerts/live`.
8. `MapView.tsx` : centre sur la France ; `Timeline.tsx` : frise par période (étape 2).
9. `ais_live.py report` : lecture en base ou sur R2.
10. `sweep_threshold.py`, `analyze_zone.py`, `list_passes.py`, `run_rules.py` : zones françaises en exemple, zones
    en arguments.
11. `.env.example`, `.gitignore`, `README.md`, `start.sh`, `docs/inventaire_worktree.md`, petits restes signalés
    par ruff et vulture.
12. CLAUDE.md : section 5.

**Archiver** (branche `archive/danemark` créée depuis `main`, puis retrait du dépôt courant ; la branche garde le
code exécutable) : `mars/ais/dma.py` (après l'étape 5 ci dessus), `scripts/import_ais.py`. Le récit et les
enseignements vont dans `docs/historique_danemark.md`.

**Supprimer** : `scripts/watchlist_check.py`, `GET /api/traffic`, `GET /api/alerts/live`, `toInputValue`, et du
suivi git seulement : `.idea/`, `mars_c2.egg-info/`.

## 5. CLAUDE.md allégé

Deux brouillons accompagnent cet audit, **sans rien changer à CLAUDE.md** tant qu'Antoine n'a pas validé :

* `docs/proposition_CLAUDE.md` : le CLAUDE.md proposé, centré sur la version actuelle (environ 210 lignes contre
  510). Il garde la direction, les règles de travail avec Antoine, l'architecture du direct et du serveur, la base,
  l'API, l'interface, le contrat du modèle, la configuration, les tests et les pièges encore d'actualité, et
  renvoie aux deux documents ci dessous.
* `docs/historique_danemark.md` : tout ce qui relève du Danemark, sans perte : données chargées, zones de test,
  enseignements de calibration cas par cas (SILVER KENNA, SSI GLORIOUS, PEARL SEAWAYS, Anholt…), résultats mesurés,
  points ouverts danois, historique des phases 0 à 5.

Répartition des sections actuelles :

| Section actuelle | Devient |
|---|---|
| 0. Nouvelle direction | Section 1 de la proposition, condensée (le document de référence reste la Claude Doc) |
| 1. Le projet (version initiale) | Principes directeurs gardés ; récit initial vers l'historique |
| 2. Travailler avec Antoine | Gardée telle quelle |
| 3. Démarrer et arrêter | Gardée, complétée par le serveur |
| 4 à 8. Architecture, arborescence, base, API, interface | Gardées, mises à jour |
| 9. Données | Sources françaises gardées ; DMA, Skagen, Anholt vers l'historique |
| 10. Contrat du modèle | Gardée (CircleNet) |
| 11. Configuration | Gardée, avec le renvoi à la recalibration (section 3 de l'audit) |
| 12. Enseignements de la calibration | Principes des règles et masques gardés en court ; récit cas par cas vers l'historique |
| 13. Résultats mesurés | Mesures du capteur et du direct gardées ; résultats des journées danoises vers l'historique |
| 14, 15. Tests, pièges | Gardées (pièges danois purement historiques déplacés) |
| 16. Points ouverts | Remplacés par les points ouverts France (cet audit) ; points danois vers l'historique |
| 17, 18. Suite, historique des phases | Vers l'historique ; le plan reste en section 1 |
