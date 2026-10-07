# Mesures pour la recalibration des règles

> **Exemple produit sur la base de test locale** (collecte du 05/10/2026 recopiée sur 24 h, ports densifiés) : les
> chiffres ne valent pas mesure. À régénérer sur le serveur après une à deux semaines de collecte
> (`docs/deploiement.md`, section « Tâches planifiées »).

Rapport produit par `scripts/mesures_calibration.py` le 07/10/2026 à 17:36 UTC, règles version 2026.10.18. Données : 151 000 intervalles entre positions de 7 037 navires, du 05/10/2026 au 07/10/2026 (3 jours). Aucun seuil n'est modifié : les propositions sont à valider une par une, puis à reporter dans `config/rules.yaml` en montant la version.

Les intervalles sont mesurés sur les positions en base, après allègement (un point par minute en route, un toutes les dix minutes à l'arrêt) : ce sont les données que voient les règles. Un intervalle de 60 s en route est donc un message reçu à chaque minute, pas la cadence réelle de l'émetteur (2 à 10 s pour la classe A en route). Les intervalles qui chevauchent minuit ne sont pas comptés.

## 1. Intervalles entre messages

### Par type de navire (en route, au moins 1 nœud)

| Type | Classe | Intervalles | Médiane | 9e décile | 95e centile | Sous 3 min | Au delà de 1,5 h |
|---|---|---|---|---|---|---|---|
| Cargo | A | 6 002 | 2,5 min | 7 min | 10 min | 65 % | 1 % |
| Pétrolier | A | 2 300 | 2,5 min | 7 min | 10 min | 67 % | 1 % |
| Passagers | A | 804 | 2,5 min | 7 min | 15 min | 57 % | 2 % |
| Pêche | A | 5 231 | 2,5 min | 7 min | 10 min | 58 % | 1 % |
| Pêche | B | 906 | 2,5 min | 7 min | 10 min | 70 % | 1 % |
| Plaisance et voile | A | 454 | 2,5 min | 10 min | 20 min | 66 % | 1 % |
| Plaisance et voile | B | 1 510 | 4 min | 10 min | 15 min | 46 % | 1 % |
| Remorquage et service | A | 744 | 2,5 min | 7 min | 15 min | 66 % | 1 % |
| Autre | A | 1 361 | 2,5 min | 7 min | 10 min | 58 % | 1 % |
| Inconnu | A | 3 211 | 2,5 min | 10 min | 30 min | 55 % | 3 % |
| Inconnu | B | 2 189 | 3 min | 15 min | 20 min | 55 % | 2 % |

### Par navire : médiane de chaque navire en route (10 intervalles au moins)

| Type | Navires | 1er décile des médianes | Médiane des médianes | 9e décile des médianes |
|---|---|---|---|---|
| Cargo | 110 | 2 min | 2,5 min | 4 min |
| Pétrolier | 45 | 2,5 min | 2,5 min | 4 min |
| Passagers | 21 | 2 min | 4 min | 5 min |
| Pêche | 135 | 2 min | 2,5 min | 4 min |
| Plaisance et voile | 59 | 2,5 min | 4 min | 4 min |
| Remorquage et service | 18 | 2 min | 2,5 min | 4 min |
| Autre | 30 | 2 min | 4 min | 4 min |
| Inconnu | 141 | 2,5 min | 3 min | 4 min |

### Par zone

| Zone | État | Intervalles | Médiane | 9e décile | 99e centile | Sous 3 min |
|---|---|---|---|---|---|---|
| bretagne | en route | 19 992 | 2,5 min | 7 min | au delà de 2 h | 62 % |
| bretagne | à l'arrêt | 17 421 | 15 min | 30 min | au delà de 2 h | 5 % |
| mediterranee | en route | 4 674 | 3 min | 10 min | au delà de 2 h | 51 % |
| mediterranee | à l'arrêt | 108 792 | 15 min | 30 min | au delà de 2 h | 1 % |
| hors zone | en route | 46 | 4 min | 15 min | au delà de 2 h | 33 % |
| hors zone | à l'arrêt | 75 | 15 min | 30 min | au delà de 2 h | 12 % |

Distribution complète, classe A en route, tous types :

| Intervalle | Nombre | Part | Cumul |
|---|---|---|---|
| ≤ 90 s | 5 352 | 27 % | 27 % |
| ≤ 2 min | 2 343 | 12 % | 38 % |
| ≤ 2,5 min | 3 351 | 17 % | 55 % |
| ≤ 3 min | 1 221 | 6 % | 61 % |
| ≤ 4 min | 2 970 | 15 % | 76 % |
| ≤ 5 min | 1 593 | 8 % | 84 % |
| ≤ 7 min | 1 716 | 9 % | 92 % |
| ≤ 10 min | 672 | 3 % | 96 % |
| ≤ 15 min | 362 | 2 % | 97 % |
| ≤ 20 min | 97 | 0 % | 98 % |
| ≤ 30 min | 78 | 0 % | 98 % |
| ≤ 45 min | 62 | 0 % | 99 % |
| ≤ 60 min | 24 | 0 % | 99 % |
| ≤ 90 min | 8 | 0 % | 99 % |
| > 2 h | 258 | 1 % | 100 % |

## 2. Zone de réception fiable

Cellules de 0.1 × 0.05 degrés ; paires d'intervalles successifs d'un navire de classe A en route, sous 2 h ; 3 navires distincts au moins par cellule (sur toute la période). Zone actuellement en base : 0 cellules, 0 km².

573 cellules traversées ; paires par cellule : médiane 15, 1er quartile 6, 3e quartile 33, 9e décile 75. 3 % des cellules atteignent 200 paires.

Intervalle jugé normal : 2 min

| Paires au moins | Continuité 80 % | Continuité 90 % | Continuité 95 % |
|---|---|---|---|
| 25 | 0 cellules, 0 km² | 0 cellules, 0 km² | 0 cellules, 0 km² |
| 50 | 0 cellules, 0 km² | 0 cellules, 0 km² | 0 cellules, 0 km² |
| 100 | 0 cellules, 0 km² | 0 cellules, 0 km² | 0 cellules, 0 km² |
| 200 | 0 cellules, 0 km² | 0 cellules, 0 km² | 0 cellules, 0 km² |

Intervalle jugé normal : 3 min (actuel)

| Paires au moins | Continuité 80 % | Continuité 90 % | Continuité 95 % |
|---|---|---|---|
| 25 | 7 cellules, 295 km² | 1 cellules, 46 km² | 0 cellules, 0 km² |
| 50 | 2 cellules, 82 km² | 0 cellules, 0 km² | 0 cellules, 0 km² |
| 100 | 1 cellules, 42 km² | 0 cellules, 0 km² | 0 cellules, 0 km² |
| 200 | 1 cellules, 42 km² | 0 cellules, 0 km² | 0 cellules, 0 km² (actuel) |

Intervalle jugé normal : 5 min

| Paires au moins | Continuité 80 % | Continuité 90 % | Continuité 95 % |
|---|---|---|---|
| 25 | 87 cellules, 3 545 km² | 53 cellules, 2 173 km² | 17 cellules, 696 km² |
| 50 | 68 cellules, 2 757 km² | 38 cellules, 1 547 km² | 12 cellules, 488 km² |
| 100 | 26 cellules, 1 063 km² | 16 cellules, 659 km² | 6 cellules, 246 km² |
| 200 | 9 cellules, 378 km² | 7 cellules, 296 km² | 3 cellules, 125 km² |

Intervalle jugé normal : 10 min

| Paires au moins | Continuité 80 % | Continuité 90 % | Continuité 95 % |
|---|---|---|---|
| 25 | 114 cellules, 4 664 km² | 110 cellules, 4 500 km² | 96 cellules, 3 908 km² |
| 50 | 82 cellules, 3 340 km² | 81 cellules, 3 300 km² | 73 cellules, 2 961 km² |
| 100 | 34 cellules, 1 400 km² | 34 cellules, 1 400 km² | 31 cellules, 1 264 km² |
| 200 | 15 cellules, 634 km² | 15 cellules, 634 km² | 12 cellules, 498 km² |

## 3. Épisodes et alertes par règle et par jour

Épisodes : comptes du cycle des règles le plus chargé de la journée (fenêtre glissante de 24 h, donc une même rencontre est vue par plusieurs cycles) ; journal `task_runs`.

Aucun cycle des règles consigné sur la période.

Alertes par type et par jour de l'événement :

| Type | 06/10 | Total | Gravité et décisions |
|---|---|---|---|
| WATCHLIST | 3 | 3 | critique 2, faible 1 |

## 4. Seuils de config/rules.yaml : valeur, mesure, proposition

| Seuil | Valeur | Ce que montrent les mesures | Proposition |
|---|---|---|---|
| reception.max_interval_s | 180 s | Classe A en route : 9e décile des intervalles 7 min, 61 % sous 3 min. | Porter à 600 s : un intervalle normal doit couvrir 9 intervalles sur 10 d'un navire bien reçu, sinon la continuité mesure l'allègement plutôt que la réception |
| reception.min_pairs | 200 | Surface fiable selon le minimum de paires : 25 paires 0 km², 50 paires 0 km², 100 paires 0 km², 200 paires 0 km². | Garder pour l'instant : avec la continuité et l'intervalle actuels, aucune cellule ne passe, quel que soit le nombre de paires ; régler d'abord max_interval_s, puis revoir ce seuil (tableaux de la section 2) |
| reception.min_coverage | 0.95 | Continuité des cellules assez fréquentées : 1er quartile 0,514, médiane 0,606 (15 cellules). | Abaisser à 0,80, plancher retenu : même ainsi, moins de trois cellules fréquentées sur quatre passent ; l'écart vient de l'intervalle jugé normal, à régler d'abord (max_interval_s) |
| reception.min_vessels | 3 | 0 cellules ont assez de paires mais moins de navires. | Garder : protège contre un seul navire bien reçu qui ferait passer une cellule pour fiable |
| reception.cell_deg_lon, cell_deg_lat | 0.1 × 0.05 | 573 cellules traversées. | Garder : la taille fixe la finesse de la zone, à revoir seulement si la carte de réception paraît trop morcelée |
| ais_gap.min_prior_messages (sur prior_window_min) | 10 en 60 min | Positions par heure d'un navire de classe A en route toute l'heure : 1er décile 2, médiane 10 (1 636 heures navire). | Abaisser à 3 : 1 heure navire sur 10 n'atteint pas le seuil, et la coupure de ces navires ne serait jamais vue |
| ais_gap.min_gap_min | 120 | Silences par jour, classe A en route au dernier message, d'au moins : 30 min 117,3, 60 min 88,7, 2 h 86,0, 4 h 86,0. | Garder : deux heures restent bien au delà des intervalles normaux (section 1) ; le nombre de silences retenus se règle par les filtres (sortie de couverture, flux coupé), pas par la durée |
| ais_gap.min_speed_kn | 1.0 | Pas de mesure directe dans ce rapport. | Garder : à juger sur les coupures retenues, une par une |
| ais_gap.min_coast_km | 3 | Pas de mesure directe dans ce rapport. | Garder : à juger sur les coupures retenues, une par une |
| ais_gap.edge_margin_deg | 0.25 | Pas de mesure directe dans ce rapport. | Garder : à juger sur les coupures retenues, une par une |
| ais_gap.partner_radius_m | 500 | Pas de mesure directe dans ce rapport. | Garder : à juger sur les coupures retenues, une par une |
| ais_gap.partner_min_slow_min | 30 | Pas de mesure directe dans ce rapport. | Garder : à juger sur les coupures retenues, une par une |
| ais_gap.projection_min | 120 | Pas de mesure directe dans ce rapport. | Garder : à juger sur les coupures retenues, une par une |
| ais_gap.stop_ratio | 0.3 | Pas de mesure directe dans ce rapport. | Garder : à juger sur les coupures retenues, une par une |
| ais_gap.continue_ratio | 0.5 | Pas de mesure directe dans ce rapport. | Garder : à juger sur les coupures retenues, une par une |
| ais_gap.high_duration_min | 360 | Pas de mesure directe dans ce rapport. | Garder : à juger sur les coupures retenues, une par une |
| rendezvous.min_coast_km | 3 | Aucun cycle consigné. | Garder : presque tous les épisodes sont des navires à quai ou au mouillage près des côtes, écartés à juste titre ; les positions à moins de 1 km de la terre ne sont plus appariées (coast_prefilter_m) |
| rendezvous.max_distance_m | 500 | Voir les alertes par jour (section 3). | Garder tant que le nombre d'alertes reste examinable une par une |
| rendezvous.max_speed_kn | 2.0 | Voir les alertes par jour (section 3). | Garder tant que le nombre d'alertes reste examinable une par une |
| rendezvous.min_duration_min | 120 | Voir les alertes par jour (section 3). | Garder tant que le nombre d'alertes reste examinable une par une |
| rendezvous.slot_min | 10 | Voir les alertes par jour (section 3). | Garder tant que le nombre d'alertes reste examinable une par une |
| rendezvous.max_gap_min | 20 | Voir les alertes par jour (section 3). | Garder tant que le nombre d'alertes reste examinable une par une |
| rendezvous.alongside_m | 50 | Voir les alertes par jour (section 3). | Garder tant que le nombre d'alertes reste examinable une par une |
| rendezvous.high_duration_min | 240 | Voir les alertes par jour (section 3). | Garder tant que le nombre d'alertes reste examinable une par une |
| rendezvous.high_coast_km | 20 | Voir les alertes par jour (section 3). | Garder tant que le nombre d'alertes reste examinable une par une |
| rendezvous.stationary_zone_buffer_m | 1000 | Voir les alertes par jour (section 3). | Garder tant que le nombre d'alertes reste examinable une par une |
| stationary_zones (cellules, 4 navires) | 4 navires | 0 zones de mouillage en base. | Garder ; reconstruire les zones sur 7 jours au moins |
| identite.confirmation_min | 360 | Alertes de changement d'identité sur la période : 0 de nom, 0 d'OMI, dont 0 bâtiments militaires. | Garder |
| watchlist.passage_gap_h | 12 | Voir les alertes WATCHLIST par jour. | Garder |
| ingestion.moving_interval_s, stopped_interval_s | 60 s, 600 s | Médiane en route 2,5 min. | Garder : paramètres d'allègement, pas de calibration |
| model.thresholds | voir config | Aucune mesure AIS : seuils du radar. | Garder ; à évaluer à l'étape 3 sur des passages Sentinel 1 français |
| contrast.min_vv_db | voir config | Aucune mesure AIS : seuils du radar. | Garder ; à évaluer à l'étape 3 sur des passages Sentinel 1 français |
| masks.land_buffer_m | voir config | Aucune mesure AIS : seuils du radar. | Garder ; à évaluer à l'étape 3 sur des passages Sentinel 1 français |
| fusion | voir config | Aucune mesure AIS : seuils du radar. | Garder ; à évaluer à l'étape 3 sur des passages Sentinel 1 français |
| persistence | voir config | Aucune mesure AIS : seuils du radar. | Garder ; à évaluer à l'étape 3 sur des passages Sentinel 1 français |
| unconfirmed | voir config | Aucune mesure AIS : seuils du radar. | Garder ; à évaluer à l'étape 3 sur des passages Sentinel 1 français |
| dark_ship | voir config | Aucune mesure AIS : seuils du radar. | Garder ; à évaluer à l'étape 3 sur des passages Sentinel 1 français |

