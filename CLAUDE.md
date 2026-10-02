# MARS C2 : contexte du projet pour Claude Code

## Le projet

Projet personnel d'Antoine : plateforme de surveillance maritime (Maritime Domain Awareness) qui fusionne l'AIS
(déclaratif) et l'imagerie radar Sentinel 1 (indépendante du navire), pensée comme vitrine pour un poste de
Forward Deployed Engineer. Question opérationnelle : quels navires sont en mer sans le déclarer, et que font ceux
qui se déclarent ?

Principes directeurs : traçabilité de chaque alerte jusqu'à ses preuves, mesurer plutôt qu'affirmer, respecter la
physique du capteur, tranche verticale d'abord, calibrer chaque règle au cas par cas sur des données réelles.

## Préférences de travail

* Répondre en français.
* **Aucun tiret dans les textes rédigés pour Antoine** (ni trait d'union, ni tiret court, ni tiret long) : prose,
  documentation, messages de commit, libellés d'interface. Reformuler (par exemple « rendez vous », « Sentinel 1 »,
  « MARS C2 »). Le code, les commandes et les URL ne sont pas concernés.
* Antoine travaille sur un MacBook Air (Apple Silicon), Python 3.12 dans `.venv`, Docker Desktop.
* Expliquer les choix, signaler honnêtement les erreurs et les limites, chiffrer les résultats.

## Architecture

| Composant | Où | Rôle |
|---|---|---|
| Base PostgreSQL 16 + PostGIS | Docker (`db`, image amd64 émulée) | Positions, navires, passages, analyses, détections, alertes, masques, horloge simulée |
| API FastAPI | Docker (`backend`, port 8000) | Horloge, trafic rejoué, passages, orchestration des analyses, fusion, règles, SSE |
| Interface | Docker (`frontend`, nginx, port 8080) | Page statique MapLibre (React prévu en phase 5) |
| Service d'inférence | **Hors Docker sur le Mac** (`uvicorn inference.app:app --port 8001`) | Docker n'a pas accès au GPU Apple (MPS) ; l'API le joint via `host.docker.internal:8001` |
| Code partagé | `mars/` | Sentinel Hub, inférence, AIS, fusion |
| Règles | `backend/rules.py`, `config/rules.yaml` | Rendez vous, coupures AIS, zones de mouillage, zone de réception ; `rules.yaml` est monté dans le conteneur et relu à chaque exécution |
| Migrations | `db/init/01` à `07` | Idempotentes à partir de 02 ; à appliquer avec `docker compose exec -T db psql -U mars -d mars < fichier` |

Commandes utiles : `docker compose up -d --build backend` puis attendre quelques secondes avant d'appeler l'API ;
`python -m pytest tests` ; `python scripts/import_ais.py` ; `python scripts/build_masks.py --skip-land` ;
`python scripts/run_rules.py --day 2024-06-05` (option `--selftest` pour le test par injection) ;
`python scripts/analyze_zone.py --bbox ... --time ...`. Les scripts d'administration visent l'API sur le port 8000
(sans nginx, qui coupe les requêtes longues).

## Données de référence

* AIS : Danish Maritime Authority, journée du 5 juin 2024, Skagerrak et Kattegat (bbox 8.5 56.0 13.0 58.6).
  17,2 M messages lus, 2,45 M doublons (stations côtières), 4,44 M positions conservées.
* Radar : passage Sentinel 1A ascendant du 5 juin 2024 à 17 h 02 UTC, via Sentinel Hub (CDSE).
* Modèle : CircleNet V2S (membre 4 de l'ensemble gagnant xView3), `models/xview3_membre4_v2s.jit`, TorchScript.
* Trait de côte : GSHHG pleine résolution (Natural Earth ratait les petites îles comme Sejerø).

## Contrat du modèle (validé en phase 0, couvert par `tests/test_contract.py`)

Canaux **VH puis VV** (l'inverse fait chuter le F1 de 0,92 à 0,81), sigma0 en dB, normalisation
sigmoïde de ((x + 20) × 0,18), pixels manquants à zéro, tuiles de 2048 exactement au pas de 1536, fusion pyramidale,
sorties à demi résolution (présence, navire, pêche, longueur, décalage), seuils 0,30 / 0,338 / 0,35, longueur en
pixels = exp(sortie) moins 1, puis × 10 m. Côté Sentinel Hub : **SIGMA0_ELLIPSOID avec rééchantillonnage
BILINEAR** (le plus proche voisin par défaut faisait tomber le F1 de 0,96 à 0,89), 2500 pixels maximum par
requête, d'où un découpage en requêtes parallèles de 2400 pixels.

## État d'avancement

* **Phase 0** (validation) : boucle d'inférence propre reproduisant exactement le script officiel ; mode rapide V2S
  en précision mixte (F1 0,92, 0,45 s par tuile sur T4) ; harmonisation Sentinel Hub mesurée.
* **Phase 1** (tranche verticale) : premier navire sombre sur la carte.
* **Phase 2** (rejeu) : horloge simulée en base (`sim_clock`, `sim_now()`), trafic diffusé en SSE, index btree sur
  `positions.ts` (le BRIN était inefficace : import non chronologique).
* **Phase 3** (analyse à la demande) : service d'inférence natif, progression en direct ; latence sur le Mac
  environ 4,3 s par tuile, 20 s pour 30 × 30 km depuis le cache, 34 s avec téléchargement.
* **Phase 4** (fusion et règles), en cours :
  * masques : terre GSHHG (500 m), zones de mouillage déduites de l'AIS (cellules 0,04 × 0,02°, 4 navires immobiles) ;
  * filtre de contraste local VV (seuil 10 dB) contre les fausses détections sur mer agitée ;
  * rendez vous : 500 m, 2 nœuds, 2 h, à plus de 3 km des côtes ; zone de mouillage : déclassé en faible plutôt
    qu'exclu ; statut « amarré » en mer = indice, jamais exclusion (exclure ce statut effaçait les transbordements
    bord à bord de SILVER KENNA) ; 9 alertes sur 9 736 épisodes ;
  * coupures AIS : classe A, 10 messages dans l'heure précédente (écarte les MMSI fantômes), zone de réception
    fiable mesurée par la continuité des trajectoires (95 % d'intervalles sous 3 min), projection à l'estime
    (30 min, 120 min sans réapparition) contre les sorties de couverture, sévérité selon le comportement (arrêt :
    élevée, route poursuivie : faible) ; 3 alertes sur 12 silences ; test par injection 4 sur 5 ;
  * tolérance Doppler orientée (ellipse le long de la trace, direction de vol 348° ascendant, 192° descendant) :
    vérifiée sur données réelles (navires en route : décalage médian 494 m le long de la trace contre 117 m en
    travers ; navires au mouillage : 19 m contre 52 m) ;
  * positions AIS non confirmées : grands navires (50 m et plus) sans écho compatible, hors abords de terre et
    hors emprise du passage. Règles en version 2026.10.11.

## En cours

L'analyse n° 4 (mouillage de Skagen, bbox 10.45 57.58 10.85 57.80) lève 5 positions non confirmées, dont des
navires de 276 à 337 m (ARCTIC AURORA, EAGLE BARENTS, AIDANOVA), sans voisin à moins de 150 m, écho le plus proche à
plus de 1,3 km. Hypothèse principale : une portion de l'extrait sans données (une des 4 requêtes parallèles, bord
de fauchée ou limite entre deux produits), regroupée à l'est de la zone. Second point : ALICE est sur le bord nord de
la zone, où la règle ne devrait pas s'appliquer. Prochaine étape : visualiser l'extrait en cache (`data/sar/`) avec
la part de pixels valides par quart d'image, les détections et les croix des navires non confirmés. Correction
probable : ne jamais conclure à l'absence d'écho là où l'extrait n'a pas de données valides (renvoyer le masque de
validité depuis le service d'inférence) et exclure les navires proches du bord de la zone.

## Reste à faire

1. Phase 4 : régler le cas ci dessus, puis la persistance des échos fixes entre analyses (éoliennes, plateformes).
2. Mettre la spécification à jour en version 2.4 avec les enseignements de la phase 4.
3. Phase 5 : interface tactique en React (tracé de zone, choix du passage, fiches d'alerte, filtres).
4. Phase 6 : mode complet sur GPU distant, évaluation sur le jeu public xView3, calibration finale, README et vidéo.

## Pièges connus

* Ne jamais remplacer un dossier par le Finder : il supprime les fichiers absents de la copie. Utiliser `ditto`.
* Après une reconstruction du backend, attendre quelques secondes avant d'appeler l'API.
* Toute valeur NaN doit devenir NULL avant JSON ou base (`finite()` dans l'API).
* Typer explicitement les paramètres SQL dans asyncpg (`$1::timestamptz`, `$2::int`).
* Les statuts AIS sont déclaratifs et souvent faux (SSI GLORIOUS se disait « en route » pendant 9 h bord à bord).
* Monter la version dans `config/rules.yaml` à chaque changement de règle, puis relancer les règles.
