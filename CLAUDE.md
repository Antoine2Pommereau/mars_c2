# MARS C2 : contexte complet du projet pour Claude Code

Ce fichier est la mémoire du projet. Lis le en entier avant toute tâche, et tiens le à jour à chaque jalon
(nouvelle règle, calibration, phase terminée, piège découvert). La spécification de référence est dans `docs/`
(version 2.4) si Antoine l'y a déposée.

## 1. Le projet

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
  docker-compose.yml      db, backend, web (et inference en profil conteneur)
  pyproject.toml          paquet mars et dépendances (pip install -e ".[test]")
  config/rules.yaml       tous les seuils, versionnés (version courante 2026.10.15)
  db/init/01 à 09         schéma et migrations (idempotentes à partir de 02)
  mars/
    config.py, db.py, geo.py
    sar/sentinelhub.py    extraction SIGMA0 bilinéaire, découpage en requêtes de 2400 px, cache
    sar/catalog.py        recherche des passages Sentinel 1
    sar/inference.py      chargement du modèle, tuilage, décodage, fusion des fragments, contraste local
    ais/dma.py            lecture et nettoyage des CSV DMA, positions à l'instant t0 (interpolation, estime)
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

## 6. Base de données (treize tables)

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

Migrations : 02 horloge simulée, 03 analyses, 04 masques, 05 statut de navigation, 06 coupures AIS (classe, emprise,
réception), 07 continuité de réception, 08 échos fixes, 09 décisions des opérateurs. Les appliquer avec
`docker compose exec -T db psql -U mars -d mars < db/init/0X_nom.sql`.

## 7. API

| Route | Rôle |
|---|---|
| `GET /api/health`, `GET /api/inference/health` | Santé de l'API et du service d'inférence |
| `GET, POST /api/clock` | Horloge simulée : `play`, `pause`, `speed`, `seek` |
| `GET /api/stream` | Flux SSE (section 4) |
| `GET /api/traffic`, `GET /api/traffic/trails` | Trafic à l'instant simulé, traînées de 30 minutes |
| `GET /api/vessels/{id}/track?start&end` | Trajectoire d'un navire |
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

## 11. Configuration (`config/rules.yaml`, version 2026.10.15)

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

Scripts : `import_ais.py`, `build_masks.py [--skip-land] [--source naturalearth]`,
`run_rules.py --day AAAA-MM-JJ [--selftest]`, `analyze_zone.py --bbox ... --time ...`,
`list_passes.py --bbox ... --start ... --end ...`, `sweep_threshold.py [--thresholds ...] [--keep]`.

## 14. Tests

`python -m pytest tests` : 19 tests attendus, 1 ignoré sans `MARS_TEST_MODEL=1` (qui charge le vrai modèle).
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

## 17. Suite : phase 6

Ordre convenu avec Antoine : **évaluation**, puis **documentation**, puis le mode complet seulement s'il reste de
l'énergie. La partie « démonstration guidée » a été écartée.

1. **Évaluation chiffrée sur le jeu public xView3** (le volet le plus important). Faire tourner exactement notre
   chaîne (extraction Sentinel Hub avec SIGMA0 bilinéaire, détection, fusion des fragments, contraste) sur des
   scènes annotées du jeu de validation xView3, et mesurer précision, rappel et F1 selon la métrique xView3
   (appariement d'une détection à une annotation à moins de 200 m). Le fichier de correspondance
   `ESA_xView3_sceneName_mapping.csv` (sur Drive, utilisé en phase 0D) relie les noms de scènes xView3 aux produits
   ESA. Puis balayer le seuil de présence et le seuil de contraste sur ces scènes pour les fixer sur des faits, et
   comparer au balayage danois. Prévoir un script `scripts/evaluate_xview3.py` et un rapport chiffré.
2. **Documentation finale** : README (présentation, architecture, captures, résultats chiffrés, démarche de
   calibration avec les cas réels), spec mise à jour, ce fichier à jour.
3. **Mode complet** (optionnel) : l'ensemble des 12 modèles sur GPU distant (Colab), appelé par la plateforme comme
   un second service d'inférence ; vérifier qu'il rattrape AIDANOVA et MAERSK INVOLVER.

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
