# Plan de calibration de MARS C2

Plan vivant : ce qui est recalé en France, ce qui reste danois, ce qui est provisoire, la méthode et les mesures à
refaire sur le serveur. Il reprend intégralement la méthode de `docs/archives/audit_code.md`, section 3 (audit du 06/10/2026,
archivé), mise à jour au 24/10/2026 sur les règles **2026.10.47** (`config/rules.yaml`). La condition, le mode et la
gravité de chaque alerte sont dans `docs/regles_alertes.md`.

**À tenir à jour** : à chaque recalage, déplacer la ligne du seuil vers la section 3, avec la version des règles, la
date et la mesure ; à chaque mesure faite, cocher la ligne du calendrier (section 8) et noter le résultat.

Sources : `docs/archives/audit_code.md` (section 3), `docs/historique_danemark.md`, `docs/bilan.md` (sections 1 et 8, lecture
du serveur du 22/10/2026), l'ancien `docs/mesures_calibration.md` (base de test, retiré), `docs/travailleurs.md`,
`docs/sentinel.md`, `docs/sentinel2.md`, `docs/trajectoire.md`, `docs/tip_and_cue.md`, `docs/deploiement.md`, CLAUDE.md
(section 4), `docs/journal.md` (ancienne section 9 de CLAUDE.md), le code (`mars/rules.py`, `scripts/taches.py`,
`scripts/mesures_calibration.py`, `scripts/build_masks.py`) et le journal Git.

## 1. Pourquoi recalibrer

Contexte commun (`docs/archives/audit_code.md`, section 3) : la calibration danoise reposait sur deux journées de la Danish
Maritime Authority, avec un tiers de doublons et un message toutes les 2 à 10 secondes par navire, captées par un
réseau dense de stations côtières, à 57° N, en mer fermée (Skagerrak, Kattegat). La France combine un AIS AISStream
plus clairsemé (environ 10 messages par heure et par navire en moyenne sur la collecte du 05/10/2026), allégé en base
à un point par minute en route et un toutes les dix minutes à l'arrêt, des latitudes de 41 à 51° N et l'Atlantique,
plus agité.

Constats français depuis l'audit :
* les seuils danois de la zone de réception fiable (180 s, 95 %, 200 paires) laissaient la zone **vide** sur les quatre
  jours mesurés du 06 au 09/10/2026 (`config/rules.yaml`, section `reception`) ;
* 316 navires en route sur 988 (32 %) n'ont pas les 10 messages dans l'heure qu'exige `ais_gap.min_prior_messages`
  (docs/journal.md, 11/10/2026 ; repris par `docs/bilan.md`, section 1) ;
* les 30 coupures élevées du 11/10/2026 déclassées l'étaient toutes pour un silence long sans réapparition,
  signature d'une sortie de couverture (docs/journal.md, 13/10/2026) ;
* VIIRS sans AIS ne veut pas dire navire sombre : 68 des 87 détections sans AIS des trois premières nuits n'avaient
  aucun navire AIS reçu à moins de 20 km (`docs/travailleurs.md`, section 7).

## 2. Méthode

Ordre conseillé par l'audit, toujours valable : **mesurer d'abord** (une à deux semaines de collecte, distribution
des intervalles par navire et par cellule, puis `build_masks.py` sur la France), **recalibrer ensuite**, et **prouver
la coupure AIS par le test par injection avant de la mettre en continu**.

Règles de conduite, issues des principes du projet et des recalages déjà faits :
1. **Lecture seule d'abord.** Toute mesure sur le serveur se fait sans écrire en base (rapport de mesures, test par
   injection dans des transactions annulées, `trajectoire-mesures`, `tip-rejeu`).
2. **Tableau avant et après.** Une proposition de seuil se présente avec ses effets mesurés (alertes en plus, en moins,
   cas connus gardés), puis Antoine valide seuil par seuil. Exemple : préfiltre côtier des rendez vous, mêmes 4
   épisodes retenus avant et après (règles 2026.10.18).
3. **Une règle en recalibration reste en observation** (`ais_gap.mode`, ou mode « observation » du panneau) : les
   épisodes sont comptés dans `task_runs` et `episodes_observes` sans remplir le fil.
4. **Monter la version** de `config/rules.yaml` à chaque changement : chaque alerte l'enregistre. Le fichier est relu
   à chaque cycle ; sur le serveur, il arrive par le dépôt (`docs/deploiement.md`).
5. **Déclasser plutôt qu'exclure** : un cas douteux baisse de gravité, il ne disparaît pas (points de disparition
   collectifs, zones de mouillage, « à confirmer »).
6. **Calibrer au cas par cas** : chaque seuil se juge sur des épisodes réels relus un par un, pas sur un total.

### Outils

| Outil | Commande (serveur, `cd /opt/mars_c2`) | Ce qu'il mesure |
|---|---|---|
| Rapport de mesures | `docker compose exec -T taches python scripts/taches.py mesures --jours 14 > /tmp/mesures_calibration.md` | Section 1, intervalles entre messages par type, par navire, par zone ; section 2, surface de la zone de réception fiable pour plusieurs valeurs de `min_pairs`, `min_coverage`, `max_interval_s` ; section 3, épisodes et alertes par règle et par jour ; section 4, détections VIIRS (évaluables avant et après) ; section 5, portée de réception ; section 6, chaque seuil avec sa valeur, sa mesure et une proposition (`scripts/mesures_calibration.py`) |
| Test par injection des coupures | `docker compose exec taches python scripts/taches.py coupures-injection --jour 2026-10-28 --essais 20` | Coupures de 3 h simulées à 2, 6, 10, 14 et 18 h UTC sur des navires réels, chaque essai annulé : taux de détection, candidats écartés avant essai et pourquoi (`ais_gap_injection`) ; refusé sans zone de réception fiable |
| Masques | `docker compose exec taches python scripts/build_masks.py --skip-land --jours 10` | Zones de mouillage (`stationary_zones`) et zone de réception fiable (`reception_cells`) sur les N derniers jours ; sans `--skip-land`, le trait de côte est rechargé (ajouter `--sans-cache`) |
| Réévaluation VIIRS | `docker compose exec taches python scripts/taches.py viirs-reevaluer` | Détections VIIRS en base rejugées selon la zone du moment (à lancer après chaque reconstruction de la zone) |
| Trajectoire | `docker compose run --rm --no-deps taches python scripts/taches.py trajectoire-mesures` (options `--cercle-seul`, `--hypotheses`) | Cercle appris, contenance, routes fréquentes en plusieurs hypothèses ; rapport JSON dans `data/ais_live/.trajectoire` |
| Rejeu du tip and cue | `docker compose exec taches python scripts/taches.py tip-rejeu 7` | Tips, passages utiles, délai, coût, en lecture seule même coupé |
| Épisodes observés | `task_runs` (tâche `regles`, détail `ais_gap` : gravités hypothétiques, 30 plus longs épisodes) et `episodes_observes` | Coupures AIS en observation |

Le rapport `docs/mesures_calibration.md` qui était dans le dépôt (retiré le 24/10/2026) **ne valait pas mesure** : il vient de la base de test
locale (collecte du 05/10/2026 recopiée, ports densifiés), produit le 07/10/2026 sous les règles 2026.10.18, avec
quatre sections seulement ; il sera retiré du dépôt. Ses propositions (par exemple `min_prior_messages` à 3) sont à
refaire sur le serveur.

## 3. Déjà recalé en France

| Seuil | Valeur | Version, date | Mesure |
|---|---|---|---|
| `rendezvous.coast_prefilter_m` (optimisation) | 1 000 m | 2026.10.18, 07/10/2026 | Base de test avec ports densifiés : 419 s ramenées à 10,5 s, mêmes 4 épisodes retenus ; sur le serveur, 4 483 épisodes sur 4 485 étaient côtiers (docs/journal.md) |
| `identite.militaire.mmsi` | 232002833 | 2026.10.18, 07/10/2026 | Cas réel de HMS CATTISTOCK, qui émettait « 101 » |
| `viirs.tolerance_m`, `estime_max_min` | 1 500 m, 20 min | 2026.10.19 puis confirmés le 09/10/2026 (2026.10.20) | 152 détections réelles : distance bimodale, appariées 39 à 41 % de 1 000 à 5 000 m (`docs/travailleurs.md`, section 7) |
| `viirs.couverture_rayon_km`, `couverture_fenetre_min`, `couverture_min_navires` | 30 km, 30 min, 1 | 2026.10.20, 09/10/2026 | 68 des 87 détections sans AIS sans aucun navire AIS à moins de 20 km |
| `ais_gap.route_horizon_min`, `route_pas_min` | 60 et 10 min | 2026.10.24, 12/10/2026 | Copie réelle du 11/10 (1 752 navires en route, zone fiable de 1 395 cellules) : 22 sorties de couverture de plus, 63 coupures retenues au lieu de 85 |
| `ais_gap.disparition_min_navires`, `disparition_cellule_deg` | 3 navires, 0,2 × 0,1° | 2026.10.24, 12/10/2026 ; nature des points 2026.10.25, 13/10/2026 | Même copie : 13 points collectifs, 47 coupures déclassées ; une seule journée mesurée |
| `ais_gap.debut_incertain_min` | 10 min | 2026.10.25, 13/10/2026 | Arrêts simulés de 20 et 140 min sur la même copie : mêmes élevées |
| `sentinel.minutes_par_fenetre_cpu`, `minutes_fixes_cpu` (coût, pas alerte) | 3 et 5 min | 2026.10.29, 19/10/2026 | Analyse 97 : 119 s par fenêtre (`docs/sentinel.md`) |
| `sentinel.point_cote_min_km`, `point_mouillage_m`, `point_mouillage_cote_min_m` (sélection) | 2 km, 1 000 m, 800 m | lot C5, 19/10/2026 | VICTOR ANDRYUKHIN à quai à Marseille ; bassins à 92 m et port de Brest à 281 m écartés, rade de Brest à 1,8 km gardée |
| `sentinel2.nuages_annonce_max_pct`, `nuages_fenetre_max_pct` | 50 %, 20 % | 2026.10.34, 20/10/2026 | Du 20/09 au 20/10/2026 : au delà de 50 % annoncés, 3 % des fenêtres dégagées (`docs/sentinel2.md`) |
| `sentinel2.masque_part_max_pct`, `masque_voisinage_m` | 25 %, 500 m | 20/10/2026 | 1,1 à 7,5 % de pixels nuageux autour de quatre navires dégagés |
| `sentinel2.fusion_rayon_m` | 300 m | 17/10/2026 | Écarts de 8 à 130 m sur cinq navires (petit échantillon) |
| `tip_and_cue.tips.viirs.delai_max_h` | 12 h | 2026.10.32, 20/10/2026 | Rejeu sur le serveur : nuit VIIRS vers 01 à 03 UTC, passages 14 à 16 h plus tard (`docs/tip_and_cue.md`) |
| `watchlist.hors_port_m`, `dossiers.sejour_hors_port_part` | 2 000 m, 0,2 | 2026.10.43, 21/10/2026 | 18 séjours de 30 jours sur le serveur : 16 redescendent à « à noter » (commit `86a366d`) |
| `tip_and_cue.prediction.cercle_appris` (quantiles), `contenance` | table, 0,8 | 2026.10.44 (échantillon), 2026.10.45 (garde), 2026.10.46 (archive), 22/10/2026 | Archive du 06 au 21/10, hors pêche : 42 407 fenêtres d'apprentissage, 14 837 tenues à l'écart, étalonnage juste jusqu'à 8 h (`docs/trajectoire.md`, section 13). **Table de l'archive pas encore déployée** au 22/10 (`docs/bilan.md`, section 8.2) |
| `tip_and_cue.prediction.cercle_appris.contenance_min` | 0,4 | 2026.10.45, 22/10/2026 | Seuil choisi sur la pré mesure ; contenance réelle des retenus 60 % à 3 h, 38 % à 4 h (`docs/trajectoire.md`, section 12) |

## 4. Provisoire

| Seuil | Valeur | Depuis | Ce qui manque |
|---|---|---|---|
| `reception.max_interval_s`, `min_coverage`, `min_pairs` | 600 s, 90 %, 50 paires | 2026.10.22, 11/10/2026 | Recalibration sur dix jours de collecte, jamais faite (`docs/bilan.md`, section 8.2). Zone du 11/10 : 1 067 cellules, environ 44 000 km² |
| `ais_gap.mode` | observation | 11/10/2026 | Mesure d'une semaine (section 8, ligne 1), puis tableau avant et après |
| `continu.flux_min_ratio` | 0,2 | 06/10/2026 | Seuil jamais mesuré seul (base de test : 41 fausses coupures évitées pour une coupure du flux de 140 min) |
| `watchlist.passage_gap_h`, `sejour_prolonge_h`, `arret_kn` | 12 h, 24 h, 1 nœud | 06 et 09/10/2026 | Distribution des passages et des séjours sur le serveur |
| `identite.confirmation_min`, `militaire.prefixes_nom` | 360 min, 21 préfixes | 06 et 07/10/2026 | Relecture des 38 alertes réelles (vrais et faux positifs) |
| `viirs.cote_km`, `persistance_*`, `corridor_m`, `liste_rayon_km`, `tres_au_large_km`, `estime_marge_ratio`, `coupure_marge_min` | 5 km ; 1 000 m, 3 nuits, 30 jours ; 2 milles ; 10 km ; 22 km ; 0,5 ; 5 min | 07 et 09/10/2026 | « Valeurs de départ, à calibrer sur les premières nuits » : relecture des 29 alertes réelles et des nuits à venir (VIIRS en pause depuis le 21/10) |
| `sentinel.ecart_longueur_m`, `ecart_longueur_relatif`, `longueur_declaree_min_m`, `cap_*` | 40 m et 40 %, 15 m, 67,5 à 112,5°, 0,5, 3 nœuds | 11/10/2026 | Premiers passages réels (un seul cas : MSC MERAVIGLIA, 9 m de plus, 3 %) |
| `sentinel.eolien_m` | 150 m | 11/10/2026 | Premiers échos près des parcs éoliens français |

## 5. Ce qui reste danois

Tableau de l'audit (section 3) conservé ligne à ligne ; colonne ajoutée : état au 24/10/2026.

| Paramètre | Valeur | Calé sur | Pourquoi recalibrer, et comment | État au 24/10/2026 |
|---|---|---|---|---|
| `model.thresholds.objectness` | 0,20 | Balayage au mouillage de Skagen | Mer plus formée en Atlantique : plus de faux échos possibles. Rejouer `sweep_threshold.py` sur des passages au large d'Ouessant et du golfe du Lion. | **Retiré au lot Z4** avec la chaîne radar du Mac (CircleNet) et `sweep_threshold.py`. La chaîne du serveur utilise le seuil d'AI2 (`sentinel.confiance`, 0,9) |
| `contrast.min_vv_db` | 10 dB | Grain de mer du Kattegat (phase 0) | Même raison ; mesurer le contraste des faux échos sur des passages par mer forte. | Gardé, lu par la fusion ; côté serveur, contraste mesuré et gardé comme preuve, pas comme filtre (`contraste_obligatoire: false`) |
| `fusion.flight_heading_deg` | 348 et 192 | 57° N | La direction de vol de Sentinel 1 dépend de la latitude ; à recalculer pour 41 à 51° N (de l'ordre de quelques degrés d'écart), au mieux à lire dans les métadonnées du produit. | **Corrigé côté serveur** : `track_heading` la calcule par scène depuis l'inclinaison de l'orbite ; les valeurs du fichier valent vers 47° N (`mars/sentinel.py`) |
| `fusion.doppler_s` | 150 s | Décalages mesurés au Danemark (410 à 494 m) | Physique du capteur, a priori transposable ; à vérifier sur quelques appariements français. | Un seul appariement réel (analyse 97) ; décalage non relevé |
| `fusion.max_gap_min`, `ais_window_min` | 10 et 15 min | AIS dense | Avec l'allègement (un point toutes les dix minutes à l'arrêt) et les trous d'AISStream, vérifier la part des navires interpolés plutôt qu'estimés. | Non mesuré |
| `rendezvous.min_coast_km` | 3 km | Mouillage de Skagen, de 4 à 8 km au large | Mouillages français différents (rade de Brest, golfe de Fos, Cherbourg, Hyères). À revoir après le calcul des zones de mouillage françaises. | En attente des zones de mouillage reconstruites (section 8) |
| `rendezvous.slot_min`, `max_gap_min` | 10 et 20 min | AIS dense | Un navire arrêté n'a plus qu'un point toutes les dix minutes en base : une tranche sur deux peut être vide. Vérifier que les épisodes ne se fragmentent pas, ou passer à 15 et 30 min. | Non mesuré |
| `rendezvous.high_coast_km`, `high_duration_min` | 20 km, 240 min | Kattegat | Sévérités à réexaminer cas par cas en France. | 7 alertes réelles en 30 jours à relire |
| `rendezvous.max_distance_m`, `max_speed_kn`, `min_duration_min` | 500 m, 2 nœuds, 120 min | Journées danoises | (ligne ajoutée) « Seuils danois jamais recalés » (`docs/bilan.md`, section 1) | À juger sur les 7 alertes et les épisodes du rapport régénéré |
| `stationary_zones.cell_deg_lon`, `cell_deg_lat` | 0,04 et 0,02° | « environ 2,4 km à ces latitudes » (57° N) | À 43° N, 0,04° de longitude valent 3,3 km : cellules plus larges. Passer à des cellules en mètres, ou adapter par zone. | Inchangé |
| `stationary_zones.min_slow_positions` | 30 | Positions brutes DMA | Avec un point toutes les dix minutes à l'arrêt, 30 positions lentes demandent cinq heures d'un même navire : seuil environ dix fois trop exigeant. | Inchangé ; zones construites une fois, à reconstruire sur dix jours |
| `reception.cell_deg_lon`, `cell_deg_lat` | 0,10 et 0,05° | 57° N | Même remarque qu'au dessus. | Inchangé |
| `reception.max_interval_s`, `min_coverage` | 180 s, 95 % | Continuité des stations danoises | Avec AISStream, peu de cellules atteindront 95 % d'intervalles sous 3 minutes. Mesurer la distribution réelle (`ais_live.py report` donne déjà une continuité par cellule) avant de fixer le seuil. | **Provisoire** depuis le 11/10/2026 : 600 s, 90 % (section 4) |
| `reception.min_pairs` | 200 | Densité DMA | Les paires sont divisées par l'allègement : seuil à abaisser. | **Provisoire** depuis le 11/10/2026 : 50 (section 4) |
| `reception.min_vessels` | 3 | Journées danoises (02/10/2026, journal Git) | (ligne ajoutée) Protège d'un seul navire bien reçu | Gardé au réglage provisoire |
| `ais_gap.min_prior_messages` | 10 dans l'heure | Écarter le MMSI fantôme danois (code 506) | Une dizaine de messages par heure en moyenne avec AISStream : la règle écarterait une grande partie des navires. Mesurer la distribution, abaisser (5 ?) en gardant l'exclusion des MMSI à message unique. | **Premier seuil à recalibrer** : 32 % des navires en route exclus (11/10/2026) |
| `ais_gap.edge_margin_deg` | 0,25° | Emprise rectangulaire de l'import danois | **Défaut** : en France, l'emprise de `ais_days` est le rectangle des quatre zones, terres comprises. Mesurer la distance au bord des polygones de zone (`regions`). | Bord des données désormais mesuré sur l'union des zones collectées (`ais_days.coverage`) ; la marge elle même reste danoise |
| `ais_gap.projection_min` | 120 min | Ferries vers Oslo (PEARL SEAWAYS, BERGENSFJORD) | Logique transposable (ferries Roscoff, Cherbourg, Corse), à vérifier sur les premières coupures françaises. | Complété par la route déclarée suivie 60 min (section 3) ; à juger dans la mesure du 29/10 |
| `ais_gap.excluded_ship_types` | navires de service | Libellés DMA | Libellés repris tels quels par l'ingestion : compatible. Mais la pêche n'est pas exclue, et la flotte bretonne est nombreuse : prévoir le contexte « pêche » déjà identifié au Danemark. | Pêche toujours incluse dans la règle ; exclue du tip `coupure_ais` (type 30) |
| `ais_gap.partner_*` | 500 m, 30 min, 2 nœuds, 5 positions | Zones de pêche danoises | `partner_min_positions` à revoir avec l'allègement. | Inchangé |
| `ais_gap.min_gap_min`, `min_speed_kn`, `min_coast_km`, `stop_ratio`, `stop_min_speed_kn`, `continue_ratio`, `high_duration_min`, `projection_open_min` | 120 min, 1 nœud, 3 km, 0,3, 5 nœuds, 0,5, 360 min, 120 min | Journées danoises (02 et 03/10/2026, journal Git) | (ligne ajoutée) À juger sur les coupures observées, une par une | Mesure du 29/10 |
| `unconfirmed`, `dark_ship`, `persistence`, `masks` | | Génériques | Liés au capteur, pas à la mer : à garder, à vérifier sur les premiers cas français. | Aucun cas français : 0 alerte radar (`docs/bilan.md`) ; `dark_ship` ne sert qu'à la chaîne du Mac |
| `ingestion` | | France | Déjà réglé pour le direct. | Inchangé |

Constantes codées en dur, même problème :

| Où | Valeur à l'audit | Remarque de l'audit | État au 24/10/2026 |
|---|---|---|---|
| Test par injection (alors `backend/app.py`, aujourd'hui `mars/rules.py`, `ais_gap_injection`) | coupure à 11:00 UTC pendant 3 h, candidats à 100 messages au moins sur 5 h et 8 nœuds de moyenne, 40 candidats, 10 km des côtes | 100 messages en 5 heures n'est atteint qu'avec un AIS dense et non allégé : à abaisser (30 ?) et à choisir dans les zones de réception françaises. | Candidats : `min_prior_messages` plus un dans l'heure, réapparition dans l'heure qui suit, 8 nœuds de moyenne, à plus de 10 km des côtes, dans la zone fiable ; heures 2, 6, 10, 14 et 18 UTC par `taches.py`, 11 h par l'API ; 50 essais au plus par heure. Restent en dur : 8 nœuds et 10 km |
| `scripts/build_masks.py` | emprise `4 53 17 60` | Danemark. | **Corrigé** : quatre zones françaises avec 0,5° de marge |
| `web/src/components/MapView.tsx` | centre `10.6, 57.6` | Skagen. | **Corrigé** : centre `1.5, 46.4` |

## 6. Ce que la calibration doit trancher

* **Coupures AIS** (plan validé avec Antoine le 13/10/2026, `docs/bilan.md`, sections 1 et 8.2) : trois notions, à
  mesurer **sur une semaine de données du serveur avant d'écrire du code** :
  1. **visibilité attendue** : durée pendant laquelle la route déclarée (dernier cap, dernière vitesse) reste dans la
     zone fiable ; un silence sans réapparition est d'autant plus grave que le navire aurait dû rester visible
     longtemps ;
  2. **temps inexpliqué** : durée du silence moins le trajet à la vitesse déclarée jusqu'à la réapparition ; une
     réapparition n'efface pas un temps inexpliqué important ;
  3. **bords de réception** : cellules de la zone fiable où des navires sans lien cessent régulièrement d'être reçus,
     à retirer de la zone.
  Puis `min_prior_messages`, et la sortie de l'observation seulement après un test par injection satisfaisant.
* **Gravité d'un silence sans réapparition** (`ais_gap.high_duration_min`, 360 min, E02, mesure du 29/10/2026) :
  épisodes du 27 au 29/10 (résumés des cycles, 30 plus longs par cycle, 117 épisodes ; flux coupé négligeable,
  15 minutes en tout, durées corrigées égales aux brutes). 66 sont passés en élevé pendant qu'ils étaient ouverts, dont
  26 ont réapparu ensuite (39 %, 8 avec la route poursuivie) :

  | Seuil | Élevés ouverts | dont réapparus ensuite | dont toujours ouverts |
  |---|---|---|---|
  | 360 min (en service) | 66 | 26 | 40 |
  | 480 min | 57 | 21 | 36 |
  | 600 min | 48 | 17 | 31 |
  | 720 min | 44 | 14 | 30 |
  | 960 min | 34 | 6 | 28 |

  **Proposition : 720 min**, qui retire un tiers des élévations et près de la moitié de celles qui se démentent ; les
  silences toujours ouverts sous 720 min sont surtout ceux que la fin de la fenêtre a tronqués (élévation retardée,
  pas perdue). **Valeur inchangée** : le critère « sans perdre de cas retrouvé par injection » ne juge rien ici (une
  coupure injectée réapparaît toujours, ce seuil ne la touche pas), la mesure couvre 2,4 jours et précède E01
  (15 des 36 silences élevés sans réapparition déclassés au bord de la zone). À refaire après une semaine avec E01 en
  service, et avec une injection de silences sans réapparition (messages effacés jusqu'à la fin de la fenêtre) pour
  que le critère puisse trancher.
* **Estime à rebours VIIRS** (`viirs.estime_max_min`, 20 min, D03, mesure du 29/10/2026) : cas de COSTA TOSCANA
  (alerte 50275), vue 55 min après la lumière, estime à 385 m. Mesure sur le serveur (positions de navires en route,
  5 nœuds et plus, de 0 à 5 h UTC, tirées au hasard, environ 580 par horizon) : position remontée avec le cap et la
  vitesse déclarés, comparée à la vraie position du navire.

  | Remontée | Erreur médiane | 80 % sous | 90 % sous | Sous 1 500 m |
  |---|---|---|---|---|
  | 20 min | 0,5 à 0,6 km | 1,1 à 1,5 km | 1,9 à 3,3 km | 80 à 83 % |
  | 30 min | 0,7 à 1,0 km | 2,1 à 3,9 km | 3,8 à 6,7 km | 62 à 74 % |
  | 60 min | 1,6 à 2,4 km | 7,0 à 10,0 km | 13,5 à 16,8 km | 38 à 48 % |
  | 90 min | 3,4 à 5,1 km | 12 à 17,5 km | 21 à 27 km | 16 à 30 % |

  (fourchettes : tranches de 5 à 10, 10 à 15 et plus de 15 nœuds). **Aucune règle** : contenir 80 % des navires à
  60 min demanderait un rayon de 7 à 10 km, qui la nuit expliquerait n'importe quelle lumière, celle d'un navire
  sombre comprise ; les 385 m de COSTA TOSCANA sont un cas favorable (route droite d'un paquebot). L'estime reste
  bornée à 20 min (tolérance actuelle, 1 500 m plus la moitié du chemin : 93 à 100 % des navires) ; un navire vu plus
  loin dans le temps rend l'alerte faible « à confirmer » par la règle de prudence, sans l'expliquer.
* **Zone de réception fiable** : seuils définitifs sur dix jours, qui décident aussi de l'évaluation des détections
  VIIRS et Sentinel.
* **Rendez vous** : seuils danois (500 m, 2 nœuds, 120 min, tranches de 10 et 20 min) contre les épisodes réels.
* **Rendez vous d'une flottille** (proposition du 31/10/2026, **non appliquée**, en attente de l'accord d'Antoine) :
  cas 125730, deux unités sans nom aux MMSI 227632082 et 227632085, bord à bord (3 m) au large du golfe de Fos.
  Mesure sur le serveur (toutes les alertes RENDEZVOUS, du 06 au 31/10/2026) : 10 alertes, dont 2 entre MMSI à moins
  de 10 d'écart et de même pays, aucune décision d'opérateur sur ces deux là :

  | Alerte | MMSI | Unités | Gravité |
  |---|---|---|---|
  | 125730 | 227632082 et 227632085 | série de 5 MMSI 22763208x, toutes en classe B, sans type ni longueur, apparues le 29/10 ensemble devant Fos, 0,9 à 2,6 nœuds en moyenne (une seule nommée, MIMOSETTE II) | élevée |
  | 56150 | 241100324 et 241100325 | série de 4 MMSI grecs 24110032x nommés B1, B2, M1 et M2, classe B, sans type ni longueur, 1,2 à 1,4 nœud en moyenne pendant trois semaines au large de Santander : balises d'engins de pêche probables | moyenne |

  Règle proposée (`rendezvous.flottille`) : paire dont les deux MMSI ont le même code pays et moins de 10 d'écart,
  **les deux en classe B, sans type ni longueur déclarés**, et aucun navire des listes : gravité **faible**, étiquette
  « flottille ou balises d'un même armement » dans le fil et la fiche (déclasser plutôt qu'exclure : l'alerte reste
  visible). Une paire en classe A, un type ou une longueur déclarés, ou un navire des listes : règle inchangée, car un
  transbordement réel met en jeu de grands navires en classe A. Effet mesuré : 125730 de élevée à faible, 56150 de
  moyenne à faible, les 8 autres inchangées. Limites : deux cas seulement en 25 jours ; un écart de 10 est un choix
  (les deux séries observées tiennent dans 6). Mise en service proposée en observation une semaine (compte des paires
  concernées dans `task_runs`, sans changer la gravité), puis appliquée si rien d'autre n'y tombe.
* **Zones de mouillage** : cellules et `min_slow_positions` adaptés à l'allègement.
* **Trajectoire** : table du cercle appris sur une archive plus longue, et décision sur l'étape 2.

## 7. Prérequis

* **Déployer `29bcfff`** (lots Z1 et Z2) : prochaine étape annoncée par CLAUDE.md (section 4) ; il porte les règles
  2026.10.46 (table de l'archive) et 2026.10.47. Sans lui, le serveur tourne avec la table provisoire 2026.10.45
  (`docs/bilan.md`, section 1).
* **Dix jours de collecte sans incident** : la collecte a été figée 21,1 h le 18/10/2026 (`docs/bilan.md`, section 1) ;
  une fenêtre de dix jours lancée le 29/10 (19 au 28/10) l'évite.
* **Lecture seule** pour toute mesure ; une mesure longue (`trajectoire-mesures`) tourne dans un conteneur à part
  (`docker compose run --rm`), jamais dans la boucle des tâches.

## 8. Mesures à refaire sur le serveur, calendrier

Dates proposées au 24/10/2026, à confirmer avec Antoine. Chaque commande se lance depuis `/opt/mars_c2`, avec la garde
`cd /opt/mars_c2 || exit` (`docs/deploiement.md`).

| N° | Date | Mesure | Commande | Durée | Ce qu'on lira | Seuils concernés |
|---|---|---|---|---|---|---|
| 1 | **29/10/2026** | Coupures AIS sur une semaine (plan du 13/10) | Lecture de `task_runs` et `episodes_observes` (requête ci dessous) ; aucune commande dédiée n'existe encore, à écrire en lecture seule si la requête ne suffit pas | Non mesuré (lecture seule de 7 jours de journal) | Épisodes par jour et par gravité hypothétique, comportement (arrêt, route poursuivie), réapparition ou non, messages dans l'heure précédente, points collectifs ; pour chaque épisode relu : visibilité attendue, temps inexpliqué, cellule en bord de réception. Repère : 84 épisodes en 7 jours au 22/10 | `ais_gap.min_prior_messages`, `high_duration_min`, `projection_min`, `projection_open_min`, `disparition_*`, `stop_ratio`, `continue_ratio`, `ais_gap.mode` |
| 2 | 29/10/2026 | Rapport de mesures régénéré sur le serveur | `docker compose exec -T taches python scripts/taches.py mesures --jours 14 > /tmp/mesures_calibration.md`, puis rapatrier le fichier | Non mesuré sur le serveur ; 140 Mo de mémoire (limite du conteneur 768 Mo) ; portée de réception 14,8 s pour 12 000 positions | Les six sections ; en priorité la section 2 (surface de la zone selon les seuils), la section 1 (heures navire sous 10 messages), la section 3 (épisodes et alertes par jour), la section 6 (propositions) | `reception.*`, `ais_gap.min_prior_messages`, `ais_gap.min_gap_min`, `rendezvous.*`, `identite.confirmation_min`, `watchlist.passage_gap_h` |
| 3 | 30/10/2026, après décision sur les seuils de la ligne 2 | Zone de réception fiable sur dix jours | Seuils validés reportés dans `config/rules.yaml` (version montée), puis `docker compose exec taches python scripts/build_masks.py --skip-land --jours 10` et `docker compose exec taches python scripts/taches.py viirs-reevaluer` | Non mesuré à 10 jours ; 61 s pour tout le script à 7 jours (base de test, émulation, 06/10/2026), 300 Mo de mémoire et 142 Mo de disque au plus | `SELECT count(*), round(sum(ST_Area(geom)) / 1e6) AS km2 FROM reception_cells` comparé à 1 067 cellules et environ 44 000 km² (11/10) ; détections VIIRS évaluables avant et après (sortie de `viirs-reevaluer`) | `reception.max_interval_s`, `min_coverage`, `min_pairs`, `min_vessels` |
| 4 | 30/10/2026 (même lancement) | Masques France : zones d'arrêt (mouillages) | Construits par la même commande que la ligne 3 | Idem | Nombre et emplacement des zones de mouillage (rade de Brest, golfe de Fos, Cherbourg, Hyères) ; alertes de rendez vous qui passent en faible au mouillage | `stationary_zones.*`, `rendezvous.stationary_zone_buffer_m`, `rendezvous.min_coast_km` |
| 5 | 31/10/2026, après la ligne 3 | Test par injection des coupures | `docker compose exec taches python scripts/taches.py coupures-injection --jour 2026-10-30 --essais 20` | Non mesuré | `taux_de_detection` comparé à 72 % (181 sur 253, 11/10/2026), `ecartes_avant_essai` (dont `historique_insuffisant`, effet de `min_prior_messages`), `raison_probable` de chaque coupure manquée | `ais_gap.min_prior_messages`, `route_horizon_min`, zone fiable |
| 6 | Semaine du 02/11/2026 | Tableau avant et après des coupures, puis décision | Rejouer la ligne 1 avec les seuils proposés (en observation), puis la ligne 5 | Non mesuré | Coupures retenues par gravité avant et après, cas connus du 11/10 (voie ouest en Manche, sorties vers Ouessant) ; aucun code avant validation | `ais_gap.*` ; sortie de l'observation |
| 7 | Semaine du 02/11/2026 | Rendez vous | Relecture des alertes réelles (7 au 22/10) et des épisodes de la section 3 du rapport | Non mesuré | Épisodes fragmentés (tranches vides à l'arrêt), rencontres légitimes au mouillage, durée et distance des vraies rencontres | `rendezvous.slot_min`, `max_gap_min`, `min_duration_min`, `max_distance_m`, `high_duration_min`, `high_coast_km` |
| 8 | Après le déploiement de `29bcfff`, puis vers le 06/11/2026 (30 jours d'archive) | Cercle appris, mesure complète | `TACHES_MEM_LIMIT=1200m docker compose run --rm --no-deps taches python scripts/taches.py trajectoire-mesures --cercle-seul`, puis `docker compose exec taches python scripts/taches.py tip-rejeu 7` | 146 s et 654 Mo au plus sur l'archive du 06 au 21/10 ; mesure complète 315 s et 661 Mo | Étalonnage sur les fenêtres tenues à l'écart (contenance estimée contre réelle, juste jusqu'à 8 h au 22/10) ; dans le rejeu, passages retenus et écartés pour contenance, délai médian, coût par mois | `tip_and_cue.prediction.cercle_appris.quantiles_m`, `contenance`, `contenance_min` |
| 9 | Après la ligne 8 | Trajectoire, étape 2 (routes fréquentes en plusieurs hypothèses) | `TACHES_MEM_LIMIT=1200m docker compose run --rm --no-deps taches python scripts/taches.py trajectoire-mesures --hypotheses` | Non mesuré (environ 57 000 départs attendus) | Par catégorie (cargos et pétroliers, autres), avec 20 et 50 exemples au moins : départs retenus par la garde et réussite des retenus de 3 à 30 h ; décision d'intégration à la vérification satellite (`docs/trajectoire.md`, section 13) | Aucun seuil actuel ; garde de 40 % sur la borne basse de Wilson |
| 10 | À la reprise des capteurs (lot Z6) | Premiers cas satellites français | Relecture des alertes VIIRS des nuits reprises ; analyses Sentinel 1 réelles en voie processeur | Non mesuré | VIIRS : lumières fixes, côtes, gravité « à confirmer » ; Sentinel 1 : écart de longueur et de cap des navires appariés, décalage Doppler, échos près des parcs éoliens | `viirs.cote_km`, `persistance_*`, `sentinel.ecart_longueur_*`, `cap_*`, `eolien_m`, `fusion.doppler_s`, `unconfirmed.*` |

Requête de la ligne 1 (lecture seule) :

```bash
cd /opt/mars_c2 || exit
docker compose exec -T db psql -U mars -d mars -P pager=off -c "
SELECT started_at::date AS jour, count(*) AS cycles,
       max((details->'ais_gap'->>'coupures_retenues')::int) AS retenues_max,
       max((details->'ais_gap'->>'coupures_declassees_disparition_collective')::int) AS declassees_max
FROM task_runs WHERE task = 'regles' AND started_at > now() - interval '7 days'
GROUP BY 1 ORDER BY 1"
docker compose exec -T db psql -U mars -d mars -P pager=off -c "
SELECT gravite, count(*) FROM episodes_observes
WHERE regle = 'ais_gap' AND derniere_le > now() - interval '7 days' GROUP BY 1 ORDER BY 2 DESC"
```

Les épisodes décrits un par un (30 plus longs par cycle) sont dans `details->'ais_gap'->'episodes'` des mêmes lignes de
`task_runs`.

## 9. Journal des recalages

| Date | Version | Recalage |
|---|---|---|
| 07/10/2026 | 2026.10.18 | Préfiltre côtier des rendez vous ; bâtiments militaires en gravité faible |
| 09/10/2026 | 2026.10.20 | VIIRS : non évaluable sans réception AIS, gravité faible « à confirmer » par défaut |
| 11/10/2026 | 2026.10.22 | Zone de réception fiable provisoire ; coupures AIS en observation |
| 12 et 13/10/2026 | 2026.10.24 et 2026.10.25 | Coupures : route déclarée suivie, points de disparition collectifs, début incertain |
| 19/10/2026 | 2026.10.29 | Coût d'une fenêtre Sentinel 1 sur processeur |
| 20/10/2026 | 2026.10.32 et 2026.10.34 | Délai de 12 h du tip VIIRS ; garde des nuages Sentinel 2 |
| 21/10/2026 | 2026.10.43 | Séjour hors port des navires des listes |
| 22/10/2026 | 2026.10.44 à 2026.10.46 | Cercle appris, garde de contenance, table de l'archive |
| 26/10/2026 | 2026.10.57 | Lot Z7 : rendez vous à 60 min (500 m gardés), balises de pêche écartées, travaux offshore d'un parc éolien marqués et abaissés ; coupures : 6 messages dans l'heure au lieu de 10, toujours en observation ; zone de réception et masques reconstruits sur 14 jours, seuils inchangés |
| 27/10/2026 | 2026.10.58 | VIIRS : lumières d'un parc éolien en service écartées (1 km), lumière fixe dès 2 nuits évaluables sans AIS, parcs éoliens hors du corridor, prudence cherchée à 100 km ; réévaluation qui applique la fusion actuelle et garde les abaissements |
| 27/10/2026 | 2026.10.59 | Sentinel 1 : modèle lancé au seuil d'absence 0,5, échos faibles (et hors zones) transmis pour juger l'absence d'un navire AIS ; sans eux, absence non concluante |
| 27/10/2026 | 2026.10.60 | Sentinel 1 : historique choisi sur la part de chaque fenêtre dans l'image du jour (`historique.couverture_min` 0,98), par fenêtre si besoin, motif exact ; garde des analyses retombées sans historique ; absence jamais déclarée là où l'image n'a pas de données |
| 27/10/2026 | 2026.10.61 | Coupures : `min_prior_messages` à 8 (injection, 358 essais : 8 à 9 messages à 54,8 % contre 62,3 % à 10 et plus) ; zone fiable : `min_pairs` proportionnel au nombre de jours (50 pour 18) ; tip and cue : place gardée sous le plafond pour une demande planifiée |
| 27/10/2026 | 2026.10.62 | Fusion radar : écho voisin du même navire (300 m, longueur à 40 % près) expliqué par lui |
| 29/10/2026 | 2026.10.63 | Coupures (E01) : silence sans réapparition à moins de 10 km du bord en mer de la zone fiable déclassé en faible, comme un point collectif |
