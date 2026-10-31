# Audit final de MARS C2 (31/10/2026)

Audit en lecture seule avant la clôture du projet (mission finale, phase 1). Code à la version `cec3b6b` (règles
2026.10.67), base du serveur lue le 31/10/2026 vers 10:45 UTC. Chaque constat porte une gravité (haute, moyenne,
basse), sa preuve, le correctif proposé et son risque, puis un classement : **à corriger**, **à documenter** ou **à
laisser**. Rien n'a été modifié pendant l'audit.

## Synthèse

| | Mesure |
|---|---|
| Tests | 602 réussis avec la base de test, trois passages complets identiques : aucun test instable |
| Couverture | 74 % (11 836 instructions) ; 0 % pour cinq scripts d'outillage manuels |
| ruff, règles élargies | 0 erreur F (noms, imports) ; 1 254 remarques de style, dont 565 imports locaux voulus |
| Code mort | 2 symboles réellement inutilisés, 1 paramètre d'API inutilisé |
| Complexité | 50 fonctions au dessus de C (radon), dont 22 en E ou F |
| Interface | typecheck et knip propres ; un seul paquet JavaScript de 1,29 Mo (363 Ko compressé) |
| Dépendances Python | pip-audit : aucune vulnérabilité connue (backend et scripts) |
| Dépendances web | npm audit : 3 alertes (MapLibre 4.7.1, Vite 5, esbuild), aucune atteignable en production |
| Images | backend 714 Mo, scripts 836 Mo, web 94,5 Mo ; images de base épinglées par étiquette, pas par empreinte |
| Base | 1,93 Go ; positions 1,77 Go (6,8 millions de lignes) ; données saines ; une table morte |
| Sauvegarde | essai de restauration complet réussi le 31/10 (6,9 Mo, 4,9 s, comptes identiques) |
| Branches | 79 branches distantes déjà fusionnées dans `main`, 4 non fusionnées |

## 1. Code Python

### 1.1 Lint élargi (ruff : F, E, W, I, B, UP, SIM, C4, RUF, PL ciblées)

* **Aucune erreur F** (nom indéfini, import inutilisé) : à laisser.
* **B023, 6 cas** (`scripts/taches.py:632`, `mars/sentinel2.py:789`) : faux positifs, les fonctions anonymes sont
  appelées dans la même itération. À laisser.
* **PLW1508, 6 cas** (`os.environ.get` avec un entier par défaut, `scripts/taches.py:96`, `ais_live.py:36`,
  `ais_ingest.py:55`) : fonctionne car la valeur est convertie ensuite. Gravité basse. **À corriger** (défaut en
  chaîne), risque nul.
* **SIM115, 13 cas** : fichiers ouverts sans `with` dans les scripts des travailleurs (processus courts). À laisser.
* **Style** : 225 `timezone.utc` au lieu de `UTC`, 80 imports non triés, 38 `zip` sans `strict`, 565 imports
  locaux (voulus : démarrage rapide des commandes, cycles évités). Récrire 1 254 lignes juste avant la clôture
  n'apporte rien et brouillerait l'historique. À laisser, documenté.
* **E902** : la configuration cite un dossier `inference/` qui n'existe plus (voie Mac retirée au lot Z4). **À
  corriger** dans la documentation (CLAUDE.md le décrit encore).

### 1.2 Code mort (vulture, confiance 60 %)

120 signalements, presque tous des faux positifs : routes FastAPI (décorateurs), commandes de `taches.py`
(enregistrées par `@commande`), méthodes de serveurs HTTP de test. Réellement inutilisés :

| Symbole | Preuve | Classement |
|---|---|---|
| `mars/geo.py` `bbox_size_km` | aucune référence | **à corriger** (retrait) |
| `mars/sentinel.py` `MOTIF_HORS_FENETRE` | aucune référence | **à corriger** (retrait) |
| paramètre `direct` de `/api/stream` | jamais lu (le flux est toujours en direct) | à laisser (l'interface l'envoie encore ; le retirer casserait les liens existants) |
| `trajectoire.categorie_libelle`, `watchlist.watchlist_rows` | utilisés par les seuls tests | à laisser |

### 1.3 Complexité (radon, au dessus de C)

50 fonctions, dont 22 en E ou F. Les plus lourdes : `run_ais_gap` (73), `mesurer` (68), `launch` Sentinel 1 (66),
`proposals` (62), `_screen_and_alert` (61), `choisir_passage` et `fuse` (50). Ce sont les cœurs des règles et de
la sélection, couverts par des tests sur cas réels ; les découper juste avant la clôture serait le changement le plus
risqué de la mission pour aucun gain visible. **À documenter** (liste ici, découpage à faire seulement si le projet
reprend).

### 1.4 Restes de la voie GPU (retirée au lot Z4)

Table `types_incompatibles` (1 ligne), état `attente_gpu` des analyses (nom historique, 0 ligne aujourd'hui, encore
écrit par le code quand Scaleway est en rupture de processeurs), état `gpu_inutilisable` des travailleurs (0 ligne),
mentions dans Grafana (`satellites.json`, `vue_ensemble.json`), `travailleurs/demarrage.sh` et la configuration.
**À corriger** : migration explicite (table supprimée, état renommé `attente_calcul`, contraintes CHECK mises à
jour), code, tableaux Grafana et tests. Risque moyen (état écrit par la boucle) : couvert par les tests de rupture de
stock.

### 1.5 Autres

* Aucun `TODO` ni `FIXME`. Aucun script orphelin.
* Mises en commun reportées du lot Z4 (fusion Sentinel 1 et 2) : non entreprises. La condition posée (rejeu des
  scènes 103, 105, 108 et 112 à l'identique) demande un harnais qui n'existe pas encore ; à documenter.

## 2. Interface

* `npm run typecheck` : propre. `knip` : propre. Pas d'eslint dans le projet (à laisser).
* **Paquet** : un seul fichier JavaScript de 1 291 Ko (363 Ko compressé), CSS 103 Ko (17 Ko compressé). MapLibre
  y est pour l'essentiel. **À corriger** (partie A du lot Z5) : MapLibre dans son propre fichier, Coulisses et
  Enquête chargés à la demande, compression et cache longs sur `/assets/` par nginx.
* **npm audit** :
  * MapLibre 4.7.1, critique (GHSA-jrc7-96c5-q579, contournement de `DOM.sanitize`) : seulement par les bulles
    HTML (`Popup.setHTML`), que l'interface n'utilise pas. **À documenter** ; passage à MapLibre 5 ou 6 seulement
    si le projet reprend (changement majeur).
  * Vite 5 et esbuild, élevée et moyenne : serveur de développement seulement, jamais en production. À documenter.

## 3. Dépendances et images

* pip-audit sur `backend/requirements.lock` et `scripts/requirements.lock` : aucune vulnérabilité connue. Versions
  figées par fichier de verrouillage, sans empreintes (`--require-hashes`) : à documenter.
* Images de base : `python:3.12-slim`, `node:22-alpine`, `nginx:1.28-alpine`, épinglées par étiquette seulement.
  **À corriger** : empreinte `@sha256` sur chaque `FROM` (reproductible, risque nul).
* Tailles : backend 714 Mo, scripts 836 Mo (pandas, numpy, GDAL pour les vignettes), web 94,5 Mo. À laisser.

## 4. Tests

### 4.1 Couverture par module (les plus faibles)

| Module | Couverture | Lecture |
|---|---|---|
| `scripts/build_masks.py`, `import_watchlist.py`, `provision_region.py`, `mars/regions/provision.py` | 0 % | outils lancés à la main, une fois |
| `mars/db.py` | 0 % | quatre lignes |
| `scripts/mesures_calibration.py` | 21 % | rapport de mesures, éprouvé sur le serveur |
| `backend/preuves.py`, `backend/satellites.py` | 27 et 32 % | routes de lecture |
| `mars/archive.py`, `mars/ais/ingest.py` | 35 et 36 % | chemins R2 réels, couverts par les essais de restauration |
| `scripts/taches.py` | 41 % | commandes manuelles |

Cœurs mieux couverts : `mars/rules.py` 72 %, fusion, Sentinel, tip and cue, travailleurs au delà de 80 %. À
documenter.

### 4.2 Tests lents

Le plus lent 3,9 s (`test_traces`, trace d'un travailleur), puis 3,3 s (coupure de collecte) ; aucun au delà de
5 s. À laisser.

### 4.3 Instabilité (trois passages complets)

Trois passages complets avec la base de test : 602 réussis à chaque fois (132 s avec la mesure de couverture,
112 s et 108 s sans). Aucun test instable. À laisser.

### 4.4 Tests qui ne testent plus rien

Les tests de la voie GPU (`test_sentinel_robuste.py`, `test_cles_travailleur.py` en partie) vérifient un état
qui ne peut plus survenir après le lot Z4 ; ils seront adaptés avec le correctif 1.4.

## 5. Base de données (serveur, lecture seule)

### 5.1 Schéma

* **Clés étrangères manquantes** : `demandes_observation` (`analysis_id`, `pass_id`, `vessel_id`, `detection_id`),
  `demandes_origines.alerte_id`, `analyses.travailleur_id`, `viirs_detections.ais_proche_vessel_id`,
  `zones_vigilance.infrastructure_id`. Données vérifiées cohérentes. **À corriger** pour `analyses.travailleur_id`,
  `demandes_observation.analysis_id` et `demandes_origines.alerte_id` (les liens qui portent la traçabilité), par
  migration idempotente `NOT VALID` puis `VALIDATE`, `ON DELETE SET NULL` ; les autres à laisser (pas de
  suppression possible des tables cibles par le code). Risque bas.
* **Contraintes CHECK périmées** : `analyses_status_check` et `travailleurs_etat_check` contiennent les états GPU
  (avec 1.4) ; `analyses_mode_check` garde `fast` (jamais écrit depuis la voie Mac) : à laisser.
* **Table morte** : `types_incompatibles` (1 ligne, voie GPU). **À corriger** (suppression explicite).
* **Tables vides mais vivantes** : `fixed_echoes`, `followed_vessels`, `vessel_notes`, `scenarios_demo`,
  `zones_vigilance`, `region_layers` : fonctions de l'interface jamais utilisées sur le serveur. À laisser.
* **Index jamais lus** (statistiques jamais remises à zéro depuis la création de la base, le 06/10) : six, tous de
  8 à 16 Ko, sur des tables minuscules ou vides. Gain nul ; à laisser.
* **Statistiques du planificateur** : `land` et `infrastructure` déclarées à 0 ligne (`n_live_tup`), jamais
  analysées depuis leur chargement. **À corriger** : `VACUUM ANALYZE` (risque nul).

### 5.2 Requêtes les plus coûteuses (pg_stat_statements)

| Requête | Appels | Moyenne | Total |
|---|---|---|---|
| Rendez vous (tranches de positions) | 2 052 | 15,0 s | 8,6 h |
| Coupures AIS (fenêtre de positions) | 2 573 | 0,74 s | 32 min |
| Navires autour d'un point (fiches) | 63 159 | 26 ms | 27 min |
| Statistiques par région | 1 998 | 0,61 s | 20 min |

La règle des rendez vous coûte à elle seule l'essentiel du calcul de la base (15 s par cycle de 5 min, soit 5 % du
temps). À documenter, avec la piste (tranches précalculées par l'ingestion) ; aucun risque de saturation mesuré.

### 5.3 Données

| Contrôle | Résultat |
|---|---|
| Navires en double (même MMSI) | 0 |
| MMSI hors format | 0 ; 5 génériques (code pays et six zéros), écartés par les règles |
| Alertes sans preuve, preuves sans alerte | 0 et 0 |
| Demandes bloquées | 0 |
| Analyses en échec sans motif | 0 (96 échecs Sentinel 1, tous motivés : essais GPU, clés, plafonds) |
| Travailleurs sans destruction | 1, le travailleur 55 en cours de calcul pendant l'audit |
| Versions des règles des alertes ouvertes | 25 versions, de 2026.10.17 à 2026.10.66 : normal (une alerte garde la version qui l'a créée ou mise à jour) |

### 5.4 Tailles, croissance, purge, archivage

* Base 1,93 Go ; disque de 18,3 Go, 35 % libres.
* Positions : 6,8 millions de lignes depuis le 06/10, environ 250 000 par jour pleine ; la purge à 30 jours
  commencera le 05/11, équilibre attendu vers 7,5 millions de lignes et 2 Go, comme prévu (CLAUDE.md, section 9).
* Fragmentation : aucune notable (0 % de lignes mortes sur `positions` ; 16 à 20 % sur de petites tables très
  modifiées, `vessels`, `task_runs`, `stats_minute`, que l'autovacuum traite).
* Archivage R2 : positions et messages statiques archivés du 06 au 30/10, 100 objets de chaque sorte (25 jours,
  quatre zones) ; archivage, purge et sauvegarde réussis chaque nuit depuis huit jours.

### 5.5 Sauvegarde

`scripts/essai_restauration.sh` le 31/10 à 10:45 UTC : dernière sauvegarde de R2 (6,9 Mo, positions exclues car
archivées à part) restaurée dans un conteneur jetable en 4,9 s, comptes des tables clés identiques à la base en
service, conteneur détruit. Réussi.

## 6. Branches Git

79 branches distantes déjà fusionnées dans `main` (liste dans le rapport final, avant suppression). Non fusionnées :
`readme-vitrine` (à reprendre en phase 4), `calibration-z7` (2 commits du 26/10, à examiner),
`xview3-section-17-eval-57d052b3` (évaluation xView3 du 04/10, hors du produit : à laisser),
`flottille-observation` (fusionnée pendant l'audit).

## 7. Classement

**À corriger** (phase 2, du plus sûr au plus risqué) :
1. `VACUUM ANALYZE` de la base.
2. Code mort (`bbox_size_km`, `MOTIF_HORS_FENETRE`), défauts d'environnement en chaîne (PLW1508).
3. Empreintes des images de base.
4. Clés étrangères de traçabilité (trois), par migration idempotente.
5. Restes de la voie GPU : table `types_incompatibles` supprimée, état `attente_gpu` renommé, contraintes,
   Grafana, tests.
6. Paquet de l'interface découpé, compression et cache nginx (mesures avant et après).
7. Branches distantes fusionnées supprimées.
8. Documentation : dossier `inference/` et voie Mac retirés de CLAUDE.md.

**À documenter** : complexité, couverture, MapLibre et Vite, coût de la règle des rendez vous, mises en commun du
lot Z4, empreintes des dépendances Python.

**À laisser** : style ruff, faux positifs de vulture et de B023, index minuscules, tables vides des fonctions de
l'interface, paramètre `direct`, tailles des images.
