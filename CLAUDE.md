# MARS C2 : contexte du projet pour Claude Code

Ce fichier est la mémoire du projet. Lis le en entier avant toute tâche, et tiens le à jour à chaque jalon. Le
document de référence de la direction est la Claude Doc « MARS C2, mise à jour du projet » :
https://claude.ai/code/artifact/00f50730-d117-4ad4-94ac-0cb9b967f002 (sinon, demander à Antoine de la déposer dans
`docs/`). L'historique de la première version (rejeu de journées danoises) et ses enseignements de calibration
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
IDENTITY_CHANGE comprises), recherche, fiche navire, frise par période ; 3. satellites (VIIRS chaque nuit, Sentinel 1 au passage sur les corridors, cartes de chaleur, couches par
source) ; 4. anticipation (TrAISformer). Pilote GeoTrackNet en parallèle.

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
vérification, restauration). Les images sont **construites sur le Mac en linux/amd64**, envoyées par `docker save`
et `docker load`, puis démarrées par `docker compose up -d --no-build` (le serveur ne construit rien : ni la place
ni la mémoire). Le `.env` du serveur active la surcouche `docker-compose.serveur.yml` et le profil `direct`.

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
| `taches` | Docker, serveur seulement | Règles toutes les 5 min ; chaque nuit : archivage R2, purge à 30 jours, sauvegarde ; disque toutes les 10 min ; masques à la demande (`build_masks.py`) |
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
  db/init/01 à 16         schéma et migrations (idempotentes à partir de 02)
  mars/
    ais/live.py, ingest.py, mid.py   zones, nettoyage, allègement, ingestion, pavillon
    watchlist.py, archive.py, r2.py  listes, archivage et sauvegarde, client R2
    fusion/                          positions à l'instant du passage, tolérance Doppler, appariement, persistance
    sar/                             passages, extraction Sentinel Hub, CircleNet
    regions/provision.py             infrastructures EMODnet
  mars/rules.py                      moteur de règles, partagé par l'API et le conteneur taches
  backend/app.py                     API
  inference/app.py                   service d'inférence
  scripts/                           collecte, ingestion, tâches, listes, régions, masques, règles, radar
  tests/                             contrat du modèle, fusion, direct, archivage
  web/                               interface React
  docs/                              déploiement, audit, historique danois
```

## 6. Base et API

Tables principales : `positions` (30 jours), `vessels`, `vessel_identities` (vue `imo_history`), `watchlist` (vue
`vessel_watch`), `alerts`, `alert_evidence`, `alert_actions`, `sar_passes`, `analyses`, `detections`,
`fixed_echoes`, `land`, `stationary_zones`, `reception_cells`, `regions`, `infrastructure`, `ais_days`,
`ingested_files`, `archives`, `task_runs`, `disk_status`, `sim_clock` (direct par défaut). `ais_days.coverage`
(union des zones collectées) situe le bord des données pour la coupure AIS ; les régions `Bretagne`, `Manche`,
`Gascogne`, `Mediterranee` portent les infrastructures EMODnet. `alerts.rule_key` : clé stable d'une alerte
comportementale (mise à jour en place, décisions des opérateurs conservées). Une migration ne
s'applique pas seule à une base existante : `docker compose exec -T db psql -U mars -d mars < db/init/XX_nom.sql`.

API : horloge (`/api/clock`, vitesse 1 en direct), flux (`/api/stream`), navires (`/api/vessels/{id}` avec
identités, listes et alertes du navire ; trajectoire),
listes (`/api/watchlist`), état du direct (`/api/ingestion` : retard, disque, tâches), journées (`/api/ais/days`),
radar (`/api/passes`, `/api/analyses`, détections, `/api/chip`), alertes (`/api/alerts`, journée, décisions),
masques (`?jours=N` pour borner le calcul) et infrastructures, règles et test par injection (`/api/rules/...`). Le détail des appelants est dans
`docs/audit_code.md`.

## 7. Interface

Poste de commandement épuré et sombre ; **la couleur est réservée à ce qui demande l'attention**. Jetons
(`web/src/styles.css`) : fond `#0e1419`, panneaux `#141c23`, surélevé `#1b252e`, filets `#26323d`, texte `#e6ecf0`,
secondaire `#7c8b97`, signal système `#4fb6c8` ; navire sombre `#e85bc7`, rendez vous `#f0a84b`, coupure AIS
`#ef6461`, position non confirmée `#e8d45a`, navire sur liste et alerte WATCHLIST `#b48cf2`, changement d'identité
`#5fd3a5`. IBM Plex Sans et Sans Condensed, chiffres
tabulaires. Rail d'icônes (Alertes, Analyses radar, Couches), un panneau à la fois, carte plein écran, fiche à
droite, frise en bas (bouton « Direct », sélecteur de vitesse en rejeu seulement).

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
`config/rules.yaml`, version 2026.10.17, seuils de calibration inchangés) :
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

**Points ouverts France** : masques France à construire sur le serveur (`docker compose exec taches python
scripts/build_masks.py --sans-cache --jours 7` : 61 s, 300 Mo de mémoire, 142 Mo de disque au plus, mesurés) ;
recalibration des seuils après une à deux semaines de mesures (liste et méthode : `docs/audit_code.md`, section 3). Mesure déjà faite sur
données synthétiques : à un point toutes les 3 minutes, `reception.min_pairs` (200, valeur danoise) écarte un tiers
des cellules d'un rail de 30 navires, et le test par injection n'a plus de candidat ; à un point toutes les
2 minutes, 5 sur 5. Le test par injection indique désormais combien de candidats il écarte, et pourquoi.

## 10. Tests

`python -m pytest tests` : 49 réussis, 1 ignoré sans `MARS_TEST_MODEL=1` (contrat du modèle, fusion, direct,
archivage, règles en continu, vérification R2 avec un faux client S3). `npm run typecheck` pour l'interface.

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
