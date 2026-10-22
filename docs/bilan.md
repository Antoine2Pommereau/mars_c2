# MARS C2 : bilan et consolidation (lot Z, phase 1)

Audit en lecture seule du 22/10/2026, sur `main` au commit `5e0cd73` (règles 2026.10.46). Aucun code de production
modifié. Sources :

* le serveur, lu en GET seulement par le tunnel (`/api/ingestion`, `/api/controle/*`, `/api/metrics`, alertes et
  analyses sur 30 jours, demandes d'observation, dossiers, `openapi.json`) le 22/10 vers 15:15 UTC ;
* le dépôt et la base de test locale (tests, couverture, construction de l'interface, temps de chargement de la Veille
  mesuré par un navigateur sans affichage sur l'interface du serveur) ;
* trois audits menés en parallèle : documentation, code mort et doublons, sécurité et dépendances (outils installés
  seulement dans l'environnement de travail : `vulture`, `pip-audit`).

Ce qui n'a pas pu être lu par l'API (tailles de la base, index, durées des tâches sur 7 jours, mémoire des conteneurs,
latences de Tempo) est demandé à l'annexe A : cinq blocs en lecture seule à lancer sur le serveur. Les chiffres qui en
dépendent sont marqués « bloc N ».

**En une phrase.** Le socle (collecte, ingestion, archive, règles comportementales, listes, interface) tourne en réel
et sans échec depuis des semaines ; toute la partie satellite est construite, mais n'a produit en réel qu'une nuit VIIRS
utile par jour pendant dix jours et **une seule analyse radar aboutie** (19/10), et elle est **en pause depuis le
21/10** ; aucune alerte radar, aucune observation de tip and cue n'a jamais abouti sur le serveur.

---

## 1. Matrice des fonctions

États : **éprouvée** (en service et éprouvée en réel), **jamais éprouvée** (en service, jamais éprouvée en réel),
**coupée** (construite et coupée ou en pause), **morte** (morte ou remplacée). Dates en UTC, lues sur le serveur le
22/10/2026.

### Socle

| Fonction | État | Dernier fonctionnement réel | Ce qui manque pour le niveau suivant |
|---|---|---|---|
| Collecte AIS (AISStream, 4 zones) | éprouvée | 22/10 15:12, en continu ; incident du 18/10 (disque plein, collecte figée 21,1 h, corrigée) | Plan B (récepteur ou AISHub) jamais construit ; version 73d0d91, en retard sur l'API |
| Ingestion | éprouvée | 22/10 15:14, retard 27 s ; 19 511 messages lus et 11 002 conservés dans l'heure | Même retard de version (73d0d91) |
| Archivage R2 | éprouvée | 22/10 02:30, 563 711 lignes, 31,7 s, 12 Mo | Relecture d'une archive (`restaurer-positions`) jamais faite sur le serveur (à confirmer, bloc 4) |
| Sauvegarde de la base | éprouvée en écriture | 22/10 02:30, 5,6 Mo, ETag vérifié ; 16 réussies, 1 échec | **Jamais restaurée** : aucune trace d'un `pg_restore` réel (section 4.3) |
| Purge à 30 jours | éprouvée | 22/10 | Rien |
| Listes OpenSanctions et GUR | éprouvée | OpenSanctions 22/10 (20 861 navires), GUR 21/10 (1 206, inchangée depuis le 06/10) | Rien |
| Calendrier des passages Sentinel 1 et 2 | éprouvée | 22/10 (20,8 s) | Plan d'acquisition Sentinel 2 de l'ESA en 404 : les passages Sentinel 2 prévus manquent |
| Statistiques (frise, garde) | éprouvée | 22/10 15:06 ; reconstruction 19/10 | Rien |
| Surveillance du disque, signes de vie | éprouvée | 22/10 ; disque 39 % libre (7,1 Go sur 18,3) | Mesure de `/app/data/ais_live` périmée depuis le 20/10 (indiquée « perimee » par l'API) |

### Règles

| Règle | État | Activité réelle | Ce qui manque |
|---|---|---|---|
| Cycle des règles (toutes les 5 min) | éprouvée | 688 cycles sans échec ; dernier 18,8 s dont rendez vous 16,1 s et coupures 2,35 s | Rien de bloquant ; rendez vous est 85 % du coût (section 3) |
| WATCHLIST et séjour prolongé | éprouvée | 94 alertes en 30 jours, dernière 22/10 ; 92 « nouvelles », 2 confirmées | Seuils de séjour à mesurer ; décisions presque jamais prises (section 8) |
| IDENTITY_CHANGE | éprouvée | 38 alertes, dernière 21/10 ; 15 sur 7 jours | Rien de bloquant |
| RENDEZVOUS | éprouvée | 7 alertes, dernière 20/10 ; 3 sur 7 jours | **Seuils danois** (500 m, 120 min, 2 nœuds) jamais recalés |
| AIS_GAP (coupures) | coupée (observation depuis le 11/10) | 84 épisodes observés en 7 jours, 0 alerte | Plan de recalibration validé le 13/10 (visibilité attendue, temps inexpliqué, bords de réception), mesure d'une semaine pas encore faite ; `min_prior_messages` danois exclut 32 % des navires en route |
| DARK_SHIP VIIRS | éprouvée, en pause | 29 alertes, dernière 17/10 | Reprise de VIIRS ; évaluation par la zone de réception provisoire |
| DARK_SHIP radar, AIS_UNCONFIRMED, IDENTITY_MISMATCH | jamais éprouvée | **0 alerte** depuis le début | Une analyse Sentinel 1 réelle sur un passage avec navire sans AIS ou incohérent |
| Zones de vigilance (profils) | jamais éprouvée (à confirmer, bloc 4) | Nombre de zones créées inconnu par l'API | Créer une zone réelle et vérifier une alerte qui porte son profil |
| Modes des règles (panneau) | éprouvée | Coupures en observation par le fichier ; aucun mode posé par le panneau | Rien |

### Satellites

| Fonction | État | Dernier fonctionnement réel | Ce qui manque |
|---|---|---|---|
| VIIRS (travailleur DEV1-M chaque nuit) | coupée (pause du 21/10, motif « too expensive ») | 21/10 06:30 : 12 granules, 0 détection, 0,02 € ; 12 travailleurs réussis, 5 échecs ; 19 tâches réussies, 18 échecs | Reprise ; le motif ne tient pas au coût (0,34 € en octobre, section 6) |
| Sentinel 1, voie processeur (GP1-S) | éprouvée **une fois** puis coupée (pause du 21/10) | Analyse 97 du 19/10 : 1 scène, 122 s, 2,6 Go de mémoire, 0,19 €, 2 détections (MSC MERAVIGLIA appariée, 1 écho écarté à terre) | Une série d'analyses réelles ; 100 analyses sur 101 en échec, dont 82 le 12/10 (essais), 10 travailleurs en échec |
| Sentinel 1, repli POP2 | jamais éprouvée | Jamais lancé en réel | Un lancement réel quand le GP1-S est en rupture |
| Sentinel 1, voie GPU (L4, L40S) | jamais éprouvée | Jamais aboutie : rupture de stock L4 en `fr-par-2` (16/10), Pascal incompatible avec l'image GPU OS | Décision : la garder ou la retirer (section 2.4) |
| Sentinel 2 (travailleur GP1-S, rslearn d'AI2) | coupée (pause du 21/10, « essai du passage 325 en attente ») | 3 échecs réels les 20/10 (403, puis EnvError de GDAL deux fois) ; correctif livré (acd1c0a), **jamais rejoué** ; 0,57 € dépensés | Rejouer le passage 325 ; plan d'acquisition de l'ESA en 404 |
| Tip and cue | coupée (pause du 21/10) | Boucle active (559 cycles sans échec), mais **aucune observation aboutie** : 20 demandes, 17 « sans passage », 2 expirées, 1 en attente, 0 résultat | Un passage réel utile ; le rejeu sur 14 jours promettait 5 passages et 80 % de contenance |
| Vérification satellite et cercle appris | jamais éprouvée | Serveur en 2026.10.45 (table provisoire) ; 2026.10.46 (table de l'archive) non déployée | Déployer 5e0cd73 ; une demande réelle aboutie |
| Trajectoire, étape 2 (analogues) | hors production | Mesurée sur la base de test seulement | Mesure `trajectoire-mesures --hypotheses` sur le serveur, puis décision d'intégration |
| Preuves images (vignettes R2) | éprouvée | 13 tâches réussies ; vignettes VIIRS et radar de l'analyse 97 | Rien |

### Supervision, interface, exploitation

| Fonction | État | Dernier fonctionnement réel | Ce qui manque |
|---|---|---|---|
| Notifications ntfy | jamais éprouvée en réel (à confirmer, bloc 4) | L'API n'expose pas la date du dernier envoi | Une notification réelle reçue et datée ; sujet ntfy sans jeton (section 5) |
| Grafana, Tempo, traces | éprouvée | En service ; tableau de bord et alertes Grafana depuis le 19/10 | Latences de l'API jamais relevées (bloc 5) |
| Panneau de contrôle et Coulisses | éprouvée | Journal : 12 entrées, dernière connexion 22/10 ; 4 pauses le 21/10 | Rien |
| Veille et dossiers | éprouvée | 17 dossiers « à noter », 0 « à examiner », 0 « urgent » sur 72 h | Décisions des opérateurs : 9 en tout depuis le 06/10, aucune depuis le 19/10 |
| Démonstration (masquage, scénarios) | éprouvée | 22/10 12:40 (masquage par un visiteur) | Scénarios rejoués : nombre à confirmer (bloc 4) |
| Recherche, fiches, frise, région | éprouvée | En service | Rien |
| Masques (zones d'arrêt, zone de réception fiable) | éprouvée une fois | Zone de réception provisoire du 11/10 | Reconstruction après dix jours de collecte (prévue, jamais faite) |
| Chaîne radar du Mac (`inference/`, `mars/sar/`, CircleNet, Sentinel Hub) | morte ou remplacée | Une analyse le 06/10 (modèle xview3), en échec | Décision de retrait (section 2.4) |

**Lecture.** Sur 35 fonctions, 22 sont éprouvées en réel, 7 n'ont jamais été éprouvées, 5 sont en pause ou coupées et
1 est remplacée. Le déséquilibre est net : tout ce qui touche à l'AIS est solide ; tout ce qui touche aux satellites est
construit, testé sur la base de test avec de faux clients, mais quasiment pas confronté au réel.

---

## 2. Code mort et doublons

Audit outillé (`vulture`, `knip`, service de langage TypeScript pour les libellés, recherche des accès aux clés, aux
tables et aux routes). **Très peu de code réellement mort** ; le gisement est ailleurs : deux chaînes en trop (radar du
Mac, voie GPU) et des doublons structurels.

### 2.1 Outils

* `vulture` : 127 signalements à 60 %, aucun à 80 %. 115 faux positifs (routes, commandes `@commande`, méthodes HTTP).
  **Six vrais morts** (25 lignes) : `controle.regler`, `controle.SEUILS_ZONE`, `tip_and_cue.TIPS`,
  `trajectoire_mesures.ARTICLE_JOURS`, `sentinel.passes_status`, `n_cur` dans `mesures_calibration`. **Six objets qui ne
  servent qu'à leur test**, dont `sentinel.heading_from_buckets` (le travailleur calcule le cap lui même) et
  `sentinel.cost_estimate` (remplacé par `cout_calcul`).
* `knip` : aucun fichier, export de valeur ni dépendance inutile ; quatre types exportés pour rien (`Clock`,
  `ProgressStep`, `Legende`, `Objet`). Tous les membres de l'objet `api` sont appelés.

### 2.2 Routes, commandes, clés, tables, libellés

* **Routes sans client** (90 routes au total) : horloge partagée (`GET` et `POST /api/clock`, et le champ `clock` du
  flux SSE envoyé chaque seconde que l'interface ne lit plus), `/api/ais/days`, `/api/watchlist`,
  `GET /api/demandes/{id}`, `/api/metrics` (prévu « pour Grafana plus tard », Grafana lit PostgreSQL depuis l'étape D),
  règles à la demande `/api/rules/ais_gap/run`, `selftest`, `/api/rules/rendezvous/run` (héritage danois, appelées
  seulement par `scripts/run_rules.py`, remplacées par `taches.py regles` et `coupures-injection`).
* **Commandes de `taches.py`** : 38, dont 11 que la boucle exécute aussi et 27 à la main seulement ; toutes documentées.
  Ponctuelles, à retirer une fois appliquées : `sentinel-non-confirmees`, `sentinel-flux`. Manque une commande pour
  rejouer à la main `preuves` et un cycle de tip and cue. Les commandes jamais lancées sur le serveur se liront au bloc 4.
* **Clés de `config/rules.yaml`** (682 lignes) : une seule jamais lue, `model.thresholds.fishing`. Huit clés ne servent
  qu'à la chaîne du Mac, huit qu'à la voie GPU. `travailleurs.prix_max_essai_eur` est **sans effet** : aucun type facturé
  à l'heure entamée ne dépasse 0,50 € de l'heure, la garde `garde_essai` ne peut jamais refuser.
* **Tables** (51, 471 colonnes) : `sim_clock` à retirer avec l'horloge partagée ; `region_layers` écrite, jamais lue ;
  `empreintes_controle` écrite, jamais lue, mais archive de traçabilité à rendre consultable. État
  `analyses.status = 'attente_gpu'` mal nommé (sert aussi au processeur et à Sentinel 2). Aucune migration remplacée à
  nettoyer ; un schéma consolidé pour une base neuve serait un gain de lisibilité seulement.
* **Libellés** (`libelles.ts`, 1 008 propriétés) : 11 orphelins certains, 15 probables, 17 lignes propres à la chaîne
  du Mac.
* **Documents périmés** : section 7.

### 2.3 Taille du code

21 877 lignes de Python et de shell de production, 7 184 dans `web/src`, 8 605 de tests (30 % de la production),
38 migrations (1 260 lignes). Plus gros fichiers : `mars/sentinel.py` 1 433, `scripts/taches.py` 1 245, `mars/rules.py`
1 183, `backend/app.py` 1 024, `mars/tip_and_cue.py` 980, `mars/travailleurs.py` 963.

### 2.4 Cas à instruire

**Chaîne radar du Mac** (`inference/`, `mars/sar/`, CircleNet, Sentinel Hub, `POST /api/analyses`, `models/`).

* Ce que c'est : une **seconde chaîne radar complète**, antérieure au lot C. L'opérateur trace une emprise, l'API cherche
  les passages dans Sentinel Hub, appelle l'inférence du Mac, puis fusionne avec l'AIS, gère les échos fixes et écrit
  les alertes dans `backend/app.py` lui même (`_run_analysis`, 195 lignes).
* Ce qui l'utilise encore : `NewAnalysis.tsx`, `Chip.tsx`, une partie d'`AnalysesPanel.tsx` et du tracé dans `App.tsx`
  et `MapView.tsx` ; le flux SSE (analyses actives) ; trois scripts (`analyze_zone`, `sweep_threshold`, `list_passes`) ;
  `tests/test_contract.py` (ignoré, `models/` vide dans le dépôt) ; `start.sh` et le profil Compose `conteneur`. Dernier
  usage réel : une analyse le 06/10, en échec. Sur le serveur, `/api/chip` répond toujours 503.
* **Défaut de fond** : `_run_analysis` est un doublon divergent de la chaîne Sentinel 1, **sans l'évaluation commune**
  (coupure du flux, zone de réception fiable) ; une alerte DARK_SHIP venue du Mac ne suit pas les règles des alertes
  satellites du serveur.
* Ce qu'on perdrait : l'analyse gratuite d'une emprise quelconque, hors plafond ; CircleNet comme second avis (F1 0,958) ;
  le balayage des seuils.
* Lignes concernées : **environ 1 370** (inférence 189, `mars/sar` 321, `backend/app.py` environ 330, scripts 185,
  interface environ 230, test 91, configuration et Compose environ 27). À garder dans tous les cas : `mars/fusion/`
  (partagé avec Sentinel 1 et 2) et les routes de lecture des analyses.
* Recommandation : ne pas garder deux chaînes radar. Soit retrait après une étiquette Git (`circlenet-mac`), soit
  CircleNet comme second détecteur dans le travailleur Sentinel 1. **Décision d'Antoine** : contredit « CircleNet gardé
  en repli » de CLAUDE.md.

**Voie GPU de Sentinel 1** (face à la stratégie « processeur en premier »).

* Stratégie en vigueur : jusqu'à 4 fenêtres sur `GP1-S` (POP2 en secours) ; au delà, ou pour une scène entière, L4 puis
  L40S ; en rupture de GPU depuis 3 h, processeur par lots de 4. Le tip and cue est limité à 4 fenêtres par demande : le
  GPU n'est atteint que si plusieurs demandes tombent sur le même passage, ou pour une scène entière demandée à la main.
* **Aucune analyse aboutie sur GPU.** Code concerné : environ 250 lignes de production (`mars/sentinel.py`,
  `mars/travailleurs.py`, `demarrage.sh`, `diagnostic.py`, Grafana, configuration), 290 de tests, une table
  (`types_incompatibles`) et deux états (`gpu_inutilisable`, `attente_gpu`).
* Ce qu'on perdrait : la scène entière (sur processeur il faudrait plusieurs dizaines de fenêtres, une dizaine de lots),
  et la latence des grands passages (un lot de 4 fenêtres par cycle de 30 min). Le coût ne bouge presque pas : 0,19 € un
  lot `GP1-S`, environ 0,26 € une session L4 de 4 fenêtres.
* Recommandation : « processeur seul, par lots de 4 » après une étiquette Git (`gpu-sentinel1`), sauf projet de
  couverture large. À trancher après le bloc 4 (analyses par voie de calcul depuis le 19/10).

### 2.5 Doublons de logique

| Doublon | Ampleur | Proposition | Gain | Risque |
|---|---|---|---|---|
| Scripts des trois travailleurs (`fetch_task`, `send`, `main`, `_hav_m`, `rss_mo`…) | similarité jusqu'à 0,95 | `travailleurs/commun.py` en Python 3.8, envoyé par `cloud-init` comme `otel_mini.py` | 120 lignes | faible |
| Sentinel 1 et Sentinel 2 (`plan`, `launch`, `_calculer`, `ingest`, `fuse_analysis`) | même schéma, similarité 0,3 à 0,6 | squelette commun paramétré par la mission, **après** la décision sur la voie GPU | 150 à 250 lignes | moyen, bien couvert par les tests |
| Rangement des preuves images (trois copies) | similarité 0,58 à 0,80 | `mars/preuves.ranger` | 40 lignes | faible |
| VIIRS et Sentinel : « flux coupé » écrit deux fois, `EXISTS reception_cells` six fois | | tout dans `mars/evaluation.py` | 25 lignes, une seule définition | faible |
| Neuf calculs de distance haversine ; celui de `trajectoire.py` prend (lat, lon) dans l'ordre inverse | | `mars/geo.distance_m` | 20 lignes, un piège en moins | faible |
| Tip and cue et sélection : « navire des listes près d'une infrastructure » existe comme tip `watchlist` (coupé) et comme `corridor_liste` (actif) | | un seul mécanisme (le tip, qui porte priorité, cercle et suivi) | 55 lignes | moyen : change ce qui déclenche une analyse |

### 2.6 Bilan chiffré

* **Retraits sûrs** (vrais morts, code gardé pour ses tests, horloge partagée et `sim_clock`, routes sans client, règles à
  la demande, `/api/metrics`, libellés, clé `fishing`, commandes ponctuelles) : **environ 400 lignes et une table**, sans
  perte de fonction.
* Avec les décisions sur la chaîne du Mac et la voie GPU et la mise en commun : **1 700 à 2 300 lignes** en tout.
* Ordre : retraits sûrs, puis décisions d'Antoine, puis mise en commun.

---

## 3. Performance et ressources

### 3.1 Base de données

Mesuré par l'API : base de 1,4 Go (1 402 Mo le 22/10), disque de 18,3 Go dont 7,1 Go libres. Le reste (tailles par
table et par index, index jamais lus, extension `pg_stat_statements`, croissance par jour) est au **bloc 1**.

Repères connus : 282 octets par position index compris, 34 000 positions conservées par heure en octobre (11 002 dans
l'heure lue), soit environ 260 000 positions par jour et 75 Mo par jour, équilibre à 30 jours vers 2,2 Go pour la seule
table des positions. `pg_stat_statements` n'est **pas activé** (aucune trace dans `db/init` ni dans Compose) : le
proposer au lot Z2, il coûte une ligne de configuration (`shared_preload_libraries`) et un redémarrage de la base.

### 3.2 Tâches

| Tâche | Dernière durée | Remarque |
|---|---|---|
| Cycle des règles (5 min) | 18,8 s | rendez vous 16,1 s, coupures 2,35 s ; 6 % du temps de `taches` |
| Archivage (nuit) | 31,7 s | 563 711 lignes |
| Passages | 20,8 s | |
| Listes OpenSanctions | 2,2 s | |
| Sauvegarde | 1,9 s | 5,6 Mo |
| Reconstruction des statistiques | 23,6 s | |
| Travailleur Sentinel 1 (19/10) | 122 s | GP1-S, 2,6 Go de mémoire |
| Mesure de trajectoire (à part) | 146 à 315 s | 654 à 661 Mo de mémoire, au delà de la limite de 768 Mo pour l'archive entière (d'où `TACHES_MEM_LIMIT=1200m`) |

Médianes et maximums sur 7 jours, et durée de chaque étape du cycle des règles : **bloc 2**.

### 3.3 API

Temps et tailles mesurés depuis le Mac à travers le tunnel (réseau compris) :

| Route | Taille | Temps |
|---|---|---|
| Trafic à l'instant | 185 Ko | 0,18 s |
| Dossiers | 11 Ko | 0,23 s |
| Alertes sur 24 h | 14 Ko | 0,05 s |
| Infrastructures | 210 Ko | 0,35 s |
| Frise | 7 Ko | 0,16 s |
| État du direct (`/api/ingestion`) | 18 Ko | 0,24 s |

Médiane et 95e centile par route sur 24 h, lus dans Tempo : **bloc 5**.

### 3.4 Mémoire et disque

| Service | Mémoire | Limite |
|---|---|---|
| taches | 149 Mo | 768 Mo |
| api | 114 Mo | aucune |
| ingestion | 93 Mo | aucune |
| grafana | 208 Mo mesurés le 19/10 | 384 Mo |
| otel | | 400 Mo |
| tempo | | 448 Mo |

Le serveur a 4 Go. Marge sur 24 h, part de Tempo (72 h de traces) et des journaux Docker sur le disque : **bloc 3**.

### 3.5 Interface

* Paquet JavaScript : **1 268 Ko en un seul fichier** (354 Ko compressés), dont MapLibre pour environ 790 Ko ; feuille
  de style 100 Ko (16 Ko compressés). Aucun découpage : la carte, les Coulisses et l'Enquête arrivent ensemble.
* Veille sur le serveur (3 chargements, navigateur sans affichage, par le tunnel) : page affichée en 0,26 à 0,42 s,
  Veille prête en 0,65 à 3,1 s, liste des dossiers en 0,69 à 3,2 s, réseau calme en 2,1 à 5,8 s ; 37 ou 38 requêtes
  et environ 3,35 Mo annoncés. L'écart vient surtout du premier chargement (paquet non en cache).

**Gisements.** Séparer MapLibre et les Coulisses en morceaux chargés à la demande (gain attendu d'environ 60 % sur le
premier affichage) ; rendez vous à 16 s par cycle à surveiller quand la collecte grossit.

---

## 4. Fiabilité et exploitation

### 4.1 Incidents de déploiement et d'exploitation depuis le 06/10

| Date | Incident | Cause | Ce qui l'aurait évité |
|---|---|---|---|
| 07/10 | API du lot 1 jamais déployée, 680 Ko par seconde mesurés sur le serveur | Transferts SSH en échec, image non mise à jour sans que personne ne le voie | Registre (fait) et vérification de la version en service |
| 07 et 08/10 | Premiers travailleurs injoignables puis non amorçables (UEFI) ; deux travailleurs lancés en même temps | Droits Scaleway, réseau privé asynchrone, image « docker » ; rattrapage et commande manuelle simultanés | Verrou en base (fait) |
| 12/10 | 82 analyses Sentinel en échec en une journée | Essais successifs sur le serveur réel | Essais sur une base de test ou plafond par jour (fait) |
| 16/10 | Rupture de stock du L4 en `fr-par-2` | Disponibilité Scaleway | Voie processeur d'abord (faite) |
| 18/10 | Disque plein, collecte figée 21,1 h sans être vue | Tâche d'écriture morte en silence, DEV1-S trop petite | Signes de vie et alerte disque (faits), DEV1-M |
| 19/10 | RENDER-S facturé 1,23 € pour 0,28 € estimés ; durée de vie dépassée | Facturation à l'heure entamée | Garde `prix_max_essai_eur` (faite) |
| 20/10 | Sentinel 2 : 403 puis EnvError de GDAL (deux fois) | Clés S3 passées en options GDAL | Correctif livré, jamais rejoué |
| Plusieurs fois | Bloc de publication sans vérification d'empreinte ; commande lancée sur la mauvaise machine ; `prune` à manier | Blocs copiés à la main, longs, variables d'un lot à l'autre | Garde du serveur (faite), puis scripts (ci dessous) |
| 22/10 | Fichier de mesure écrasé par `--cercle-seul` | Nom de sortie unique | Noms par type et par lancement (fait) |
| En cours | **Versions mélangées** : API et taches en 89033cb, collecte et ingestion en 73d0d91, `main` en 5e0cd73 | Images publiées seulement si elles changent, déploiement service par service | Un déploiement qui affiche et compare toutes les versions |
| En local | Base de test cassée par un `pg_restore` ; un bloc de tests bloqué plus de 10 minutes une fois | Restauration sur la base de test en service ; test non borné dans le temps | `pytest-timeout`, base jetable pour les restaurations |

### 4.2 Proposition : `scripts/publier.sh` (Mac) et `scripts/deployer.sh` (serveur)

Les blocs collés à la main sont la première source d'erreurs. Deux scripts versionnés les remplacent, avec toutes les
gardes déjà apprises :

`scripts/publier.sh [backend|web|scripts|tout]`, sur le Mac :

1. refuse si la copie n'est pas `main`, propre, et égale à `origin/main` ;
2. détermine les images qui changent (fichiers touchés depuis la version publiée, lue dans l'étiquette de l'image
   `latest` du registre), ou celles demandées ;
3. construit en `linux/amd64` avec `MARS_VERSION` ;
4. **boucle d'empreinte** : chaque image construite porte l'empreinte du commit, sinon arrêt sans rien pousser ;
5. `verifier_images.sh` sur les cibles concernées ;
6. pousse `latest` et l'empreinte, puis imprime la ligne à lancer sur le serveur.

`scripts/deployer.sh <empreinte>`, sur le serveur :

1. **garde** : refuse hors de `/opt/mars_c2` ou si le nom d'hôte n'est pas celui du serveur ;
2. vérifie le disque (au moins 2 Go libres), puis télécharge les images une à une en libérant l'ancienne avant la
   suivante (la règle « une image à la fois ») ;
3. applique les migrations nouvelles dans l'ordre, chacune une fois (table `migrations_appliquees`, à créer au lot Z2),
   après une sauvegarde ;
4. `docker compose up -d --no-build`, attend la santé de chaque service ;
5. compare la version de chaque service (`/api/ingestion`, `versions`) à l'empreinte demandée et l'affiche ;
6. journalise le déploiement dans `task_runs` (tâche `deploiement`, avec trace).

**Option GitHub Actions** (construction et publication seulement, le déploiement reste manuel) :

| Point | Détail |
|---|---|
| Coût | Dépôt privé, offre gratuite : 2 000 minutes par mois ; une construction des trois images en amd64 natif prend environ 10 à 15 minutes, soit plus de 100 publications par mois sans frais |
| Secrets | Aucun jeton personnel : `GITHUB_TOKEN` avec `packages: write` suffit pour ghcr ; le jeton d'écriture du trousseau du Mac peut alors être révoqué |
| Avantages | Construction native (pas d'émulation sur le Mac), étiquettes et empreinte posées par la machine, `verifier_images.sh` et les tests à chaque publication, journal public des versions publiées |
| Limites | `verifier_images.sh` a besoin de Docker et d'une base : faisable sur les machines de GitHub, à éprouver ; le quota de stockage des paquets privés reste le même |
| À ne pas faire | Déployer depuis GitHub : il faudrait ouvrir SSH au réseau de GitHub et confier une clé du serveur à GitHub |

Recommandation : les deux scripts d'abord (gain immédiat, aucun nouveau secret), GitHub Actions ensuite si Antoine
veut libérer le Mac.

### 4.3 Tests

* `python -m pytest tests` : **424 réussis, 1 ignoré en 80 s environ** sur la base de test ; test le plus lent 4,6 s
  (`test_coupures_route`). Aucun test instable constaté sur ce passage ; le blocage de plus de 10 minutes vu une fois
  n'est pas reproduit, cause probable un test en base en attente d'un verrou : ajouter `pytest-timeout` (60 s par test).
* Couverture : **64 %** (12 814 instructions, 4 613 non couvertes).
  * 0 % : `inference/app.py`, `mars/db.py`, `mars/regions/provision.py`, les scripts `analyze_zone`, `build_masks`,
    `import_watchlist`, `list_passes`, `provision_region`, `run_rules`, `sweep_threshold`, et les essais des
    travailleurs (`essai_ai2`, `essai_fenetres`, `essai_sentinel2`).
  * Faible : `mesures_calibration` 21 %, `backend/sentinel` 24 %, `backend/preuves` 27 %, `backend/satellites` 32 %,
    `mars/archive` 35 %, `mars/ais/ingest` 36 %, `backend/app` 36 %, `scripts/taches` 37 %, `travailleurs/sentinel`
    41 %.
  * Bonne : `controle` 93 %, `dossiers` 92 %, `viirs` 92 %, `tip_and_cue` 89 %, `travailleurs` 88 %, `sentinel` 87 %.
* **Ce qui n'est éprouvé que sur des données synthétiques ou de faux clients** : toute la chaîne Sentinel 1 sauf une
  scène, Sentinel 2 de bout en bout, la voie GPU, le repli POP2, tip and cue jusqu'à l'observation, la vérification
  satellite, les zones de vigilance, l'étape 2 de la trajectoire, la restauration complète.
* L'interface : `npm run typecheck`, `npx knip` et le test node de `decodeTraffic` ; aucun test de rendu.

### 4.4 Sauvegarde et restauration

La sauvegarde nocturne marche (5,6 Mo, ETag vérifié), mais **aucune restauration n'a jamais été faite** : la procédure de
`docs/deploiement.md` (« Restauration complète ») n'a pas d'essai consigné. Proposition d'essai sans risque, sur une base
jetable du serveur (port non publié, volume temporaire) :

```bash
( cd /opt/mars_c2 || { echo "PAS SUR LE SERVEUR : arrêt"; exit 1; }
  df -h / | tail -1                                                  # au moins 3 Go libres avant de commencer
  docker compose run --rm taches python scripts/taches.py sauvegardes          # noter le nom de la plus récente
  F=sauvegardes/mars_AAAAMMJJTHHMMSS.dump                                     # à remplacer par ce nom
  docker compose run --rm -v /tmp/essai_restauration:/r taches python scripts/taches.py telecharger "$F" /r/mars.dump
  docker run -d --name essai_restauration --memory 512m -e POSTGRES_PASSWORD=essai postgis/postgis:16-3.4
  sleep 20; docker cp /tmp/essai_restauration/mars.dump essai_restauration:/mars.dump
  docker exec essai_restauration pg_restore -U postgres -d postgres --create --no-owner --exit-on-error /mars.dump
  docker exec essai_restauration psql -U postgres -d mars -c "SELECT count(*) FROM alerts" -c "SELECT count(*) FROM vessels"
  docker rm -f essai_restauration; rm -r /tmp/essai_restauration )
```

(Ce bloc écrit sur le disque du serveur et démarre un conteneur jetable : il est hors du périmètre de ce lot et ne sera
lancé qu'après validation, au lot Z2.)

### 4.5 Reprise après sinistre (serveur perdu)

| Étape | Durée estimée |
|---|---|
| Créer une DEV1-M, Docker, `/opt/mars_c2`, `.env` recopié de la sauvegarde hors serveur (aujourd'hui : nulle part) | 30 min |
| `docker login ghcr.io`, `docker compose pull`, `up -d db` | 10 min |
| Télécharger et restaurer la dernière sauvegarde | 5 min (5,6 Mo) |
| `restaurer-positions` sur les 30 derniers jours depuis R2 | 30 à 60 min (à mesurer) |
| Réseau privé, IP privée, filtre IP du jeton R2 principal à refaire | 20 min |
| Total | **environ 2 h, si le `.env` existe ailleurs** |

**Point faible principal** : le `.env` du serveur (toutes les clés) n'a pas de copie hors serveur documentée. Le
conserver chiffré dans un gestionnaire de mots de passe.

---

## 5. Sécurité

Rapport complet de l'audit : 17 recommandations, résumées ici par priorité.

**Ce qui est bien fait** : mots de passe des opérateurs en PBKDF2 à 600 000 itérations, cookie HttpOnly et SameSite
strict, jetons stockés hachés, paire Copernicus des travailleurs remise une seule fois par le réseau privé, deux
barrières de filtrage des traces (code et collecteur), port 8090 limité à quatre routes, garde de destruction des
instances, aucun port public sur le serveur hormis SSH.

| # | Gravité | Constat | Proposition |
|---|---|---|---|
| 1 | **Élevée** | Routes d'écriture **sans session** : décisions sur les alertes et les dossiers, demandes d'observation (qui peuvent lancer un travailleur payant), notes, suivis, analyses, recalcul des masques, `/api/rules/*/run`. Auteur libre. Joignables par une requête intersite sans Content-Type tant que le tunnel est ouvert | Session opérateur exigée partout, auteur tiré de la session |
| 2 | **Élevée** | Tout le `.env` arrive dans `backend`, `collector`, `taches`, `inference` ; la clé Scaleway est dans l'API, qui n'en a pas besoin | Un fichier d'environnement par service |
| 3 | Moyenne | Starlette 0.41.3 (tiré par FastAPI 0.115.6) : 7 failles, dont la validation de l'en tête Host sur laquelle repose le contrôle d'origine | Monter FastAPI |
| 4 | Moyenne | Jeton Earthdata transmis par `cloud-init` (lisible par l'API Scaleway et tout processus de l'instance) | Remise par le réseau privé, comme Copernicus |
| 5 | Moyenne | Travailleurs avec IP publique, groupe de sécurité par défaut du projet ; résultats vérifiés sur leur forme seulement (un travailleur compromis peut fabriquer des alertes) | Groupe de sécurité dédié, validation stricte des résultats |
| 6 | Moyenne | Sur le Mac, base (`mars`/`mars`) et API publiées sur toutes les interfaces | Liaison `127.0.0.1` |
| 7 | Faible | nginx sans `server_name` (rebond DNS) ni en têtes de sécurité ; compteur de force brute global et en mémoire ; masque des traces limité aux clés AWS ; sujet ntfy seul secret ; sauvegardes non chiffrées ; dépendances Python non figées, `npm install` au lieu de `npm ci`, images de base sans empreinte ; maplibre-gl 4.7.1 (faille critique selon npm, non atteinte par le code) | Voir le rapport, recommandations 9 à 16 |

**Échéances** : paire Copernicus des travailleurs le **14/11/2026** (dans 23 jours) ; jeton Earthdata tous les 60 jours,
date de création non consignée ; jetons GitHub (lecture sur le serveur, écriture sur le Mac) et clé Scaleway sans date
documentée. Le jeton GitHub du serveur (`read:packages`, classique) lit tous les paquets du compte.

**Données dans les journaux et les traces** : aucune fuite de secret constatée ; restent possibles une clé citée en clair
dans le message d'une exception (hors format AWS), le journal brut des travailleurs (200 000 caractères en base sans
filtre) et les recherches de l'opérateur dans les journaux d'accès d'uvicorn (noms et MMSI, données publiques).

---

## 6. Coûts

| Poste | Coût mensuel | Source |
|---|---|---|
| Serveur DEV1-M (4 Go) | environ 14,7 € HT (0,0202 € de l'heure) | Prix de l'API Scaleway dans `config/rules.yaml` ; à confirmer sur la facture |
| IP publique, volume de 18 Go | à lire sur la facture Scaleway | Non consigné dans le dépôt |
| Travailleurs, octobre au 22/10 | **2,51 €** : Sentinel 1 1,60 € (12 travailleurs), Sentinel 2 0,57 € (3), VIIRS 0,34 € (17) | `/api/controle/etat` |
| Cloudflare R2 | 0 € : environ 26 Mo par jour, sous les 10 Go gratuits pendant 13 mois | `docs/deploiement.md` |
| Registre GitHub, AISStream, Copernicus, Earthdata, OpenSanctions, ntfy.sh | 0 € | Offres gratuites |
| **Total estimé** | **environ 20 € HT par mois**, dont les trois quarts pour le serveur | |

**Coût par résultat** (le chiffre qui compte) : 1,60 € pour **une** analyse Sentinel 1 aboutie, 0,57 € pour aucune
analyse Sentinel 2, 0,34 € pour dix nuits VIIRS dont la dernière sans détection. L'argent est parti dans les essais et
les échecs, pas dans le service.

**Réductions possibles** :

* VIIRS en pause pour coût alors qu'il coûte 0,02 € par nuit (0,60 € par mois) : la pause n'économise presque rien et
  coupe la seule source satellite éprouvée. À reprendre.
* Sentinel 1 et 2 : le budget de 8 € n'est pas le problème ; l'essentiel est de ne lancer un travailleur que quand un
  résultat est probable (garde de contenance, faite) et de ne plus essayer sur le serveur réel.
* Serveur : DEV1-M nécessaire depuis l'incident du 18/10 ; un engagement ou une instance d'une autre gamme ne gagnerait
  que quelques euros. Pas de levier sérieux.
* Tempo, Grafana et otel occupent environ 1,2 Go de limites de mémoire sur 4 Go : les garder, mais vérifier au bloc 3
  que la marge reste suffisante pour une mesure lancée à part.

---

## 7. Documentation

### 7.1 CLAUDE.md

**1 013 lignes, 100 Ko** (environ 25 000 jetons relus avant chaque tâche) : la section 9 « État et mesures » fait 412
lignes de journal daté, la section 11 « Pièges » 271 lignes dont la moitié résolus. Contradictions relevées : serveur
« DEV1-S » (l. 90) alors que c'est une DEV1-M depuis le 19/10 ; Sentinel « arrêté » (l. 62 à 65) ; L4 présenté en tête des
travailleurs (l. 133) ; migrations « 01 à 27 » (il y en a 38) ; deux dispositions de l'interface superposées ; quatre
décomptes de tests d'époques différentes ; accès à Documents perdu puis rétabli.

**Structure cible, environ 260 lignes** :

| # | Section | Lignes |
|---|---|---|
| 0 | En tête : usage du fichier, liens vers la Claude Doc, `docs/journal.md`, l'index de `docs/` | 6 |
| 1 | Le projet : objectif, pistes, sources, principes, zones | 30 |
| 2 | Travailler avec Antoine | 18 |
| 3 | Règles permanentes de développement (OpenTelemetry condensé, version des règles, état du panneau prioritaire, règle en continu jamais effacée puis recréée, libellés, test par injection, destruction des travailleurs) | 28 |
| 4 | État actuel : table des capteurs et fonctions avec date du dernier essai réel et pause (la matrice de la section 1, réduite) | 20 |
| 5 | Architecture : services, travailleurs et ordre de repli, chaîne du direct, chaîne satellite | 35 |
| 6 | Carte du code, migrations 01 à 38 | 35 |
| 7 | Base et API par domaine, renvoi à `openapi.json` | 20 |
| 8 | Interface : jetons, urgence, Veille, Enquête, Coulisses | 25 |
| 9 | Démarrer et tester | 20 |
| 10 | Déployer et exploiter (renvoi au guide d'exploitation) | 15 |
| 11 | Pièges vivants, une à trois lignes chacun, par domaine | 55 |

Le reste part dans **`docs/journal.md`** (section 9 entière, sections datées de `deploiement.md`, décomptes de tests) et
**`docs/pieges_archives.md`** (pièges résolus).

### 7.2 Verdict par document

| Document | Verdict |
|---|---|
| `README.md` (racine) | **Périmé de bout en bout** (version danoise) : à réécrire en vitrine courte, utile pour le poste visé |
| `docs/deploiement.md` (1 137 l.) | Périmé (DEV1-S, 2 Go) ; sections datées (l. 550 à 982) au journal ; à scinder en guide d'exploitation |
| `docs/sentinel.md` (601 l.) | Périmé en tête (« travailleur GPU », « aucun essai réel ») ; réécrire l'en tête, histoire au journal |
| `docs/sentinel2.md` | État à dire en tête : correctif livré, jamais rejoué |
| `docs/tip_and_cue.md` | Cercle d'incertitude ancien, panneau Observations du rail disparu, pause à signaler |
| `docs/travailleurs_viirs.md` | DEV1-S ; à fusionner avec la partie travailleurs de `sentinel.md` en `docs/travailleurs.md` |
| `docs/controle.md` | « Icône Contrôle du rail » devenue Coulisses ; sinon à jour |
| `docs/vision_interface.md` | À fusionner dans `ux_refonte.md`, puis à archiver |
| `docs/mesures_calibration.md` | Rapport de la base de test du 07/10, jamais régénéré sur le serveur : à régénérer ou sortir du dépôt |
| `docs/audit_code.md` | À archiver ; extraire sa section 3 (méthode de recalibration) dans un plan de calibration vivant |
| `docs/inventaire_worktree.md`, `docs/historique_danemark.md` | À archiver (`docs/archives/`) |
| `docs/ux/maquettes/` | Trace de conception, à archiver une fois le lot U stabilisé |
| `docs/opentelemetry.md`, `passages_satellites.md`, `supervision.md`, `trajectoire.md`, `ux_refonte.md` | À jour |

### 7.3 Documents qui manquent

1. **Guide d'exploitation** (`docs/exploitation.md`) : déployer, mettre en pause et reprendre un capteur, restaurer,
   **rotation des secrets avec leurs échéances**, que faire sur chaque notification, suivre la dépense.
2. `docs/journal.md` et `docs/pieges_archives.md`.
3. Index de `docs/` (`docs/README.md`).
4. **Table d'état des capteurs et des coûts** : la pause « too expensive » du 21/10 n'existe qu'en base.
5. **Document des règles d'alerte** : condition, mode, seuils et origine (danoise ou française) de chaque type.
6. `docs/travailleurs.md` (architecture commune).
7. **Plan de calibration vivant** : ce qui est recalé, ce qui reste danois, les mesures à refaire sur le serveur.

---

## 8. Ce qui manque

### 8.1 Écart entre la vision et la réalité

| Promesse de la vision | Réalité au 22/10 |
|---|---|
| Confronter l'AIS déclaratif à des capteurs satellitaires indépendants | Une seule scène radar analysée en réel ; VIIRS seul capteur régulier, en pause |
| Alertes radar (navire sombre, AIS non confirmé, identité incohérente) | Jamais émises |
| Tip and cue : un indice AIS déclenche une observation | 20 demandes, 0 observation : 17 sans passage utile |
| Protection des infrastructures : ancre traînante, arrêt ou flânerie dans un corridor | Pas de règle dédiée « flânerie dans un corridor » ; couvert indirectement par rendez vous, coupures (en observation) et séjour prolongé |
| Transbordement | Couvert par rendez vous (seuils danois) |
| Anticipation (TrAISformer) | Lot T phase 1 : cercle appris et garde ; étape 2 mesurée hors production ; TrAISformer non commencé |
| Cartes de chaleur (étape 3) | Non commencées |
| Plan B de la collecte (récepteur, AISHub) | Non construit : AISStream est un point de défaillance unique |

### 8.2 Calibrations en attente

* **Seuils danois** encore en service : rendez vous (500 m, 120 min), coupures (`min_prior_messages` 10, intervalle
  normal), rapport de mesures jamais régénéré sur le serveur.
* **Zone de réception provisoire** (11/10 : 10 min, 90 %, 50 paires, 3 navires) : à recalibrer sur dix jours, jamais
  refaite.
* **Coupures AIS en observation** depuis le 11/10 : plan validé le 13/10, mesure d'une semaine à faire avant tout code.
* **Masques France** (zones d'arrêt) : construits une fois ; à reconstruire sur dix jours de collecte réelle.
* **Table du cercle appris** de l'archive (2026.10.46) et rejeu final : publiés dans `main`, pas encore sur le serveur.

### 8.3 Ce qui empêche une démonstration convaincante

1. **La Veille n'a rien d'urgent** : 17 dossiers « à noter », 0 « à examiner », 0 « urgent ». Un visiteur voit des navires
   des listes à quai, pas une menace sur un câble.
2. **Aucune preuve satellite récente** : capteurs en pause, une seule vignette radar (19/10), pas d'alerte radar.
3. **La boucle complète n'a jamais tourné en réel** : indice AIS, demande, passage, analyse, réponse. C'est pourtant le
   cœur du récit pour un poste de Forward Deployed Engineer.
4. **Décisions des opérateurs quasi absentes** : 9 décisions depuis le 06/10, 92 alertes WATCHLIST « nouvelles » ; la
   traçabilité existe mais n'est pas montrée en usage.
5. Le mode démonstration et ses scénarios compensent en partie, mais ils montrent du rejoué, pas du direct.

---

## 9. Plan de consolidation

Classé par gain, effort et risque. Chaque lot est indépendant ; aucun ne commence avant validation.

| Lot | Contenu | Gain | Effort | Risque |
|---|---|---|---|---|
| **Z1 Sécurité** | Session sur toutes les écritures (auteur tiré de la session) ; un fichier d'environnement par service ; ports du Mac sur `127.0.0.1` ; FastAPI à jour ; Earthdata par le réseau privé ; calendrier des échéances (Copernicus le 14/11) | Élevé | 1 à 2 jours | Faible, couvert par `verifier_images.sh controle` |
| **Z2 Exploitation** | `publier.sh` et `deployer.sh` ; table des migrations appliquées ; déploiement de 5e0cd73 sur **tous** les services ; essai de restauration sur base jetable ; copie chiffrée du `.env` ; `pg_stat_statements` ; `pytest-timeout` | Élevé | 1 à 2 jours | Faible |
| **Z3 Documentation** | CLAUDE.md à 260 lignes, `journal.md`, `pieges_archives.md`, `exploitation.md`, `travailleurs.md`, table d'état des capteurs, README vitrine | Élevé (chaque tâche relit 100 Ko) | 1 jour | Nul |
| **Z4 Code mort** | Retraits sûrs de la section 2.6 (environ 400 lignes, table `sim_clock`) ; puis chaîne du Mac et voie GPU selon décision ; puis module commun des travailleurs, preuves, évaluation et distance uniques, squelette Sentinel 1 et 2 | Moyen (1 700 à 2 300 lignes en moins) | 0,5 jour, puis 1 à 2 jours | Faible pour les retraits sûrs, moyen ensuite |
| **Z5 Performance** | Découpage du paquet (MapLibre, Coulisses), index jamais lus (bloc 1), surveillance de rendez vous | Moyen | 0,5 jour | Faible |
| **Z6 Satellites en réel** | Reprendre VIIRS ; rejouer Sentinel 2 (passage 325) ; une série d'analyses Sentinel 1 en voie processeur ; tip and cue repris avec la garde ; coût par résultat affiché dans Budget | Très élevé pour la démonstration | 2 à 3 jours d'essais, budget 8 € | Moyen (coûts, ruptures de stock) |
| **Z7 Calibration** | Mesure d'une semaine des coupures (plan du 13/10), rapport de mesures régénéré sur le serveur, zone de réception et masques reconstruits, seuils de rendez vous | Élevé pour la crédibilité | 1 semaine de collecte, 1 à 2 jours | Faible |

**Ordre proposé** : Z1 et Z2 d'abord (risque réel, effort faible, ils sécurisent tout le reste), Z3 aussitôt après
(il rend chaque tâche suivante moins chère), puis Z6 et Z7 en parallèle (Z7 attend surtout du temps de collecte), Z4 et
Z5 au fil de l'eau.

### Questions pour Antoine

1. **Chaîne radar du Mac** (CircleNet, Sentinel Hub, `inference/`) : la retirer du dépôt (elle reste dans l'historique
   Git) ou la garder comme comparaison isolée dans un dossier `archives/` ?
2. **Voie GPU de Sentinel 1** (L4, L40S) : jamais aboutie, rarement disponible. La retirer et garder la voie processeur
   seule, ou la garder coupée pour une scène entière ?
3. **Pauses du 21/10** : VIIRS coûte 0,02 € par nuit ; d'accord pour le reprendre dès Z6 ? Et tip and cue avec la garde
   de contenance ?
4. **Accès à l'interface** : rester en tunnel SSH seul (Z1 suffit), ou viser un accès https pour une démonstration à
   distance (alors Z1 devient indispensable et il faut un domaine et un certificat) ?
5. **GitHub Actions** pour construire et publier : oui maintenant, plus tard, ou non ?
6. **Copie du `.env` hors serveur** : quel coffre (gestionnaire de mots de passe, fichier chiffré sur R2) ?
7. **README vitrine** : en français seul, ou aussi en anglais pour le poste visé ?

---

## Annexe A. Blocs à lancer sur le serveur (lecture seule)

Chaque bloc est entre parenthèses avec la garde ; aucun n'écrit en base ni sur le disque, aucun ne télécharge d'image.

* Bloc 1 : tailles des tables et des index, index jamais lus, `pg_stat_statements`, positions par jour.
* Bloc 2 : durées des tâches sur 7 jours (médiane, maximum), étapes du cycle des règles.
* Bloc 3 : mémoire des conteneurs, disque, part de Tempo et des journaux Docker, processus tués par manque de mémoire.
* Bloc 4 : dernier usage réel de chaque tâche depuis le début, notifications, zones de vigilance, scénarios, demandes,
  masques, décisions.
* Bloc 5 : latence de l'API par route sur 24 h, lue dans Tempo depuis le conteneur `taches`.

Les résultats compléteront les sections 1, 2 et 3 de ce bilan à la phase 2.

```bash
# Bloc 1 : base (tailles, index jamais lus, pg_stat_statements, croissance)
( cd /opt/mars_c2 || { echo "PAS SUR LE SERVEUR : arrêt"; exit 1; }
docker compose exec -T db psql -U mars -d mars -P pager=off <<'SQL'
\echo '--- tables (total, données, index)'
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) total, pg_size_pretty(pg_relation_size(relid)) donnees,
       pg_size_pretty(pg_indexes_size(relid)) index, n_live_tup lignes
FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 25;
\echo '--- index jamais lus depuis la dernière remise à zéro des statistiques'
SELECT s.relname, s.indexrelname, pg_size_pretty(pg_relation_size(s.indexrelid)) taille, s.idx_scan
FROM pg_stat_user_indexes s JOIN pg_index i USING (indexrelid)
WHERE s.idx_scan = 0 AND NOT i.indisunique ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT 25;
SELECT stats_reset FROM pg_stat_database WHERE datname = 'mars';
\echo '--- pg_stat_statements'
SELECT name, setting FROM pg_settings WHERE name IN ('shared_preload_libraries', 'shared_buffers', 'work_mem');
SELECT extname FROM pg_extension;
\echo '--- croissance : positions par jour sur 10 jours'
SELECT date_trunc('day', ts)::date jour, count(*) FROM positions WHERE ts > now() - interval '10 days' GROUP BY 1 ORDER BY 1;
SELECT pg_size_pretty(pg_database_size('mars')) base;
SQL
)

# Bloc 2 : durées des tâches sur 7 jours, étapes du cycle des règles
( cd /opt/mars_c2 || { echo "PAS SUR LE SERVEUR : arrêt"; exit 1; }
docker compose exec -T db psql -U mars -d mars -P pager=off <<'SQL'
SELECT task, status, count(*) n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at))::numeric, 1) mediane_s,
       round(max(extract(epoch FROM finished_at - started_at))::numeric, 1) max_s, max(started_at)::timestamp(0) dernier
FROM task_runs WHERE started_at > now() - interval '7 days' GROUP BY 1, 2 ORDER BY 1, 2;
\echo '--- étapes du cycle des règles (médiane et max, 7 jours)'
SELECT k etape, round(percentile_cont(0.5) WITHIN GROUP (ORDER BY v::numeric)::numeric, 2) mediane_s, max(v::numeric) max_s
FROM task_runs, jsonb_each_text(COALESCE(details->'durees_s', '{}'::jsonb)) AS e(k, v)
WHERE task = 'regles' AND started_at > now() - interval '7 days' AND v ~ '^[0-9.]+$' GROUP BY 1 ORDER BY 2 DESC;
SQL
)

# Bloc 3 : mémoire des conteneurs, disque, part de Tempo et des journaux Docker
( cd /opt/mars_c2 || { echo "PAS SUR LE SERVEUR : arrêt"; exit 1; }
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}'
free -m; df -h / | tail -1
docker system df
docker system df -v | sed -n "/VOLUME NAME/,/^$/p"     # taille de chaque volume, Tempo compris
sudo du -sh /var/lib/docker/containers 2>/dev/null; du -sh /opt/mars_c2/data/* 2>/dev/null
journalctl --disk-usage 2>/dev/null
dmesg 2>/dev/null | grep -i -c "killed process"
)

# Bloc 4 : dernier usage réel des fonctions (matrice du bilan, section 1)
( cd /opt/mars_c2 || { echo "PAS SUR LE SERVEUR : arrêt"; exit 1; }
docker compose exec -T db psql -U mars -d mars -P pager=off <<'SQL'
\echo '--- dernière exécution réussie et dernier échec par tâche, depuis le début'
SELECT task, max(started_at) FILTER (WHERE status = 'ok')::timestamp(0) dernier_ok,
       max(started_at) FILTER (WHERE status = 'echec')::timestamp(0) dernier_echec, count(*) n
FROM task_runs GROUP BY 1 ORDER BY 1;
\echo '--- notifications, zones de vigilance, scénarios, demandes, masques'
SELECT cle, mis_a_jour_le::timestamp(0) FROM notifications_etat;
SELECT count(*) retenues, max(envoyee_le)::timestamp(0) dernier_resume FROM notifications_retenues;
SELECT count(*) zones, max(cree_le)::timestamp(0) derniere FROM zones_vigilance;
SELECT count(*) scenarios FROM scenarios_demo;
SELECT etat, count(*), max(cree_le)::timestamp(0) FROM demandes_observation GROUP BY 1;
SELECT count(*) cellules_reception FROM reception_cells;
SELECT count(*) zones_arret FROM stationary_zones;
SELECT count(*) echos_fixes FROM fixed_echoes;
SELECT max(at)::timestamp(0) derniere_decision, count(*) FROM alert_actions;
\echo '--- analyses par source, voie de calcul et statut (décision sur la voie GPU)'
SELECT source, calcul, status, count(*), max(requested_at)::timestamp(0) FROM analyses GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
SQL
)

# Bloc 5 : latence de l'API sur 24 h, lue dans Tempo (médiane et 95e centile par route, durée de la racine des traces)
( cd /opt/mars_c2 || { echo "PAS SUR LE SERVEUR : arrêt"; exit 1; }
docker compose exec -T taches python - <<'PY'
import json, statistics, time, urllib.parse, urllib.request
fin = int(time.time()); par_route = {}
for h in range(24):
    q = urllib.parse.urlencode({"q": '{ resource.service.name =~ ".*api.*" && span.http.route != nil }', "limit": 2000,
                                "start": fin - (h + 1) * 3600, "end": fin - h * 3600, "spss": 1})
    try:
        r = json.load(urllib.request.urlopen(f"http://tempo:3200/api/search?{q}", timeout=60))
    except Exception as e:
        print("tempo injoignable :", type(e).__name__, e); break
    for t in r.get("traces", []):
        par_route.setdefault(t.get("rootTraceName", "?"), []).append(t.get("durationMs", 0))
print(f"{'route':60} {'n':>6} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8}")
for nom, d in sorted(par_route.items(), key=lambda x: -len(x[1]))[:30]:
    d.sort(); p = lambda q: d[min(len(d) - 1, int(q * len(d)))]
    print(f"{nom[:60]:60} {len(d):6} {p(.5):8} {p(.95):8} {d[-1]:8}")
PY
)
```
