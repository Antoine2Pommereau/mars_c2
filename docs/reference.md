# MARS C2 : référence technique détaillée

Anciennes sections 3 à 8 de CLAUDE.md (démarrage, architecture, arborescence, base et API, interface, modèle radar),
gardées le 24/10/2026 (lot Z3) avec leurs détails ; contradictions corrigées (migrations 01 à 42, travailleurs sur
processeur d'abord, disposition du lot U). L'état actuel et le résumé sont dans CLAUDE.md. Ce qui concerne la chaîne
radar du Mac (inférence, CircleNet, Sentinel Hub) est appelé à disparaître au lot Z4.
### 3. Démarrer

**Serveur** (Scaleway DEV1-M, `/opt/mars_c2`) : tout est décrit dans `docs/deploiement.md` (installation, R2,
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
| http://localhost:3000 | Grafana (serveur seulement, par tunnel : `ssh -N -L 8080:localhost:8080 -L 3000:localhost:3000 root@IP`) |
| localhost:5432 | PostgreSQL (`mars` / `mars`), fermé sur le serveur |

Après une modification : `backend/`, `docker compose up -d --build backend` (attendre quelques secondes) ;
`inference/` ou `mars/sar/`, redémarrer l'inférence ; `web/`, rien en développement, `--build web` pour le port 8080 ;
`config/rules.yaml`, rien (relu à chaque exécution). Une collecte par clé AISStream : le profil `direct` ne tourne
que sur le serveur.

### 4. Architecture

| Composant | Où | Rôle |
|---|---|---|
| `db` | Docker, PostgreSQL 16, PostGIS 3.4 | Positions, navires et identités, listes, alertes et preuves, radar, masques, archives, tâches |
| `backend` | Docker, FastAPI, asyncpg | Flux SSE, navires, radar, règles, décisions, état du direct |
| `web` | Docker (nginx) ; Vite en développement | Interface React |
| `collector` | Docker, profil `direct` | AISStream vers le Parquet `data/ais_live` (un fichier par minute, zone et type) ; contrôle de santé (`scripts/sante.py`) : figée 5 min sans fichier, arrêtée puis relancée (`init`, `restart`) |
| `ingest` | Docker, profil `direct` | Parquet vers la base, allègement, identités, journées, `vessel_watch` ; ne s'arrête plus sur une erreur (DiskFull journalisé), boucle figée relancée |
| `grafana` | Docker, serveur seulement | Grafana 12.4.2 sur PostgreSQL (rôle `grafana_lecture`), 127.0.0.1:3000, 256 Mo ; tableaux, règles et canal ntfy en code (`grafana/provisioning`) |
| `otel` | Docker, serveur seulement | Collecteur OpenTelemetry contrib 0.163.1 (`otel/collecteur.yaml`) : 127.0.0.1:4318 et réseau Docker, filtre des données sensibles, tail sampling, file d'attente sur disque, 200 Mo |
| `tempo` | Docker, serveur seulement | Grafana Tempo 3.1.0, binaire unique, stockage local, traces gardées 72 h, 320 Mo ; lu par Grafana (source « Tempo ») |
| `taches` | Docker, serveur seulement | Règles toutes les 5 min ; chaque nuit : archivage R2, purge à 30 jours, sauvegarde, liste OpenSanctions (le GUR chaque semaine), calendrier des passages satellites ; 06:30 : nuits VIIRS confiées à un travailleur éphémère ; chaque minute : surveillance des travailleurs ; disque toutes les 10 min ; à la demande : masques (`build_masks.py`), mesures de calibration (`taches.py mesures`) |
| Travailleurs | Instances Scaleway éphémères (API Scaleway, `mars/travailleurs.py`) | Analyse satellite hors du serveur : créées à la demande, script et tâche par cloud-init, résultat renvoyé par le réseau privé (nginx, port 8090, jeton), détruites par le serveur ; DEV1-M pour VIIRS (fr-par-1) ; Sentinel 1 sur GP1-S puis POP2-8C-32G (fr-par-2), GPU L4 ou L40S seulement au delà de 4 fenêtres ; Sentinel 2 sur GP1-S ; tâche et secrets remis par le réseau privé (`docs/travailleurs.md`) |
| Inférence | Mac, hors Docker (GPU Apple) | Extraction Sentinel Hub, CircleNet, vignettes ; repli conteneur sur CPU (profil `conteneur`) |

**Chaîne du direct** : AISStream, Parquet local, ingestion toutes les 15 s (un point par minute en route, un toutes
les dix minutes à l'arrêt), base, flux SSE une fois par seconde. Chaque nuit, les journées terminées partent sur
Cloudflare R2 (archive brute complète, servant aussi à l'entraînement) et sont effacées du serveur une fois
confirmées.

**Analyse radar** (étape 3, sur le Mac) : `POST /api/analyses` avec une emprise et un passage Sentinel 1 ; le
service d'inférence extrait l'image et détecte les échos ; l'API lit l'AIS autour de l'instant du passage, applique
la fusion (`mars/fusion/pipeline.py`) et la persistance des échos fixes, écrit détections et alertes.

### 5. Arborescence

```
mars_c2/
  CLAUDE.md, start.sh, pyproject.toml, docker-compose.yml, docker-compose.serveur.yml
  config/rules.yaml       seuils versionnés
  db/init/01 à 42         schéma et migrations (idempotentes à partir de 02, registre schema_migrations depuis 39)
  mars/
    ais/live.py, ingest.py, mid.py   zones, nettoyage, allègement, ingestion, pavillon
    watchlist.py, archive.py, r2.py  listes, archivage et sauvegarde, client R2
    satellites.py                    calendrier des passages Sentinel 1 et 2 (catalogue, plans de l'ESA, couverture)
    travailleurs.py, viirs.py        orchestrateur des travailleurs éphémères ; granules VIIRS, appariement, alertes
  travailleurs/viirs.py              script du travailleur VIIRS (exécuté dans l'image publique d'allenai)
  travailleurs/otel_mini.py, traces.sh  traces des travailleurs (OTLP JSON en bibliothèque standard, et en bash)
  mars/traces.py                     traces OpenTelemetry : init, spans, filtre des données sensibles, journaux
  otel/collecteur.yaml, tempo.yaml   collecteur OpenTelemetry et Tempo (étape D2, docs/opentelemetry.md)
  mars/sentinel.py                   Sentinel 1 : sélection des passages, garde fous, clés de lecture, fusion, alertes
  mars/evaluation.py                 détection satellite évaluable ou non (zone de réception fiable), VIIRS et Sentinel
  travailleurs/sentinel.py           script du travailleur Sentinel 1, sur processeur d'abord (image publique d'AI2, Python 3.8)
  .github/workflows/                 nettoyage du registre GitHub (trois dernières versions par image)
    fusion/                          positions à l'instant du passage, tolérance Doppler, appariement, persistance
    sar/                             passages, extraction Sentinel Hub, CircleNet
    regions/provision.py             infrastructures EMODnet
  mars/rules.py                      moteur de règles, partagé par l'API et le conteneur taches
  mars/supervision.py                signes de vie, mesures du disque, ntfy, alertes de secours, durée des requêtes
  scripts/sante.py                   contrôle de santé de la collecte et de l'ingestion, redémarrage automatique
  grafana/generer.py, provisioning/  tableaux (produits en JSON), source, règles, canal ntfy ; config/supervision.env : seuils
  backend/app.py                     API ; backend/contenu.py : recherche, fiches, notes, navires suivis, photo ;
                                     backend/regions.py : région affichée ; backend/satellites.py : passages ;
                                     backend/travailleurs.py : retour des travailleurs, VIIRS ;
                                     backend/sentinel.py : détections Sentinel 1, vérification satellite
  inference/app.py                   service d'inférence
  scripts/                           collecte, ingestion, tâches, listes, régions, masques, règles, radar,
                                     mesures de calibration (mesures_calibration.py)
  tests/                             pytest (base de test facultative) ; web/tests (node --test)
  web/                               interface React
  docs/                              index : docs/README.md
```

### 6. Base et API

Tables principales : `positions` (30 jours), `vessels`, `vessel_identities` (vue `imo_history`), `watchlist` (vue
`vessel_watch`), `alerts`, `alert_evidence`, `alert_actions`, `sar_passes` (passages des analyses radar et calendrier Sentinel 1 et 2, colonne `mission`), `stats_10min_region`, `analyses`, `detections`,
`fixed_echoes`, `land`, `stationary_zones`, `reception_cells`, `regions`, `infrastructure`, `ais_days`,
`ingested_files`, `archives`, `task_runs`, `disk_status` (la table `sim_clock` de l'horloge partagée est retirée au lot Z4, migration 43). `ais_days.coverage`
(union des zones collectées) situe le bord des données pour la coupure AIS ; les régions `Bretagne`, `Manche`,
`Gascogne`, `Mediterranee` portent les infrastructures EMODnet. `alerts.rule_key` : clé stable d'une alerte
comportementale (mise à jour en place, décisions des opérateurs conservées). Une migration ne
s'applique pas seule à une base existante : `bash scripts/deployer.sh <empreinte> --migrations` (registre
`schema_migrations`, sauvegarde avant ; `docs/exploitation.md`).

API : flux (`/api/stream?direct=1` : horloge chaque seconde, trafic en colonnes seulement quand l'ingestion a
chargé du nouveau, au plus tard toutes les 30 s), trafic à un instant (`/api/traffic?at=`), traînées
(`/api/traffic/trails?at=`), navires (`/api/vessels/{id}` avec identités, listes et alertes ; trajectoire allégée
à `max_points`), état du direct (`/api/ingestion` : retard, disque, tâches), frise
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
`POST /api/travailleurs/{id}/etat|resultats` (jeton, réseau privé seulement). **Mesures** : `/api/metrics` retirée au lot Z4 (Grafana lit la base). Tables du lot B : `travailleurs`, `viirs_granules`, `viirs_detections`,
`viirs_lumieres_fixes` (migration 20). **Sentinel 1** : `/api/sentinel/detections?start&end&region`,
`/api/sentinel/detections/{id}` (fiche : mesures, vignette, AIS à 2 km, navire apparié), `/api/vessels/{id}/satellite`
(vérification satellite), `satellites.sentinel` dans `/api/ingestion` ; analyses `source = 'sentinel1'` dans `analyses`
et `detections` (migration 25), alerte IDENTITY_MISMATCH. L'horloge partagée (`/api/clock`, `sim_clock`), qui n'était plus utilisée, est retirée au lot Z4
par l'interface : l'instant est tenu par chaque navigateur et inscrit dans l'adresse de la page.

### 7. Interface

Poste de commandement épuré et sombre ; **la couleur est réservée à ce qui demande l'attention**. Jetons
(`web/src/styles.css`) : fond `#0e1419`, panneaux `#141c23`, surélevé `#1b252e`, filets `#26323d`, texte `#e6ecf0`,
secondaire `#7c8b97`, signal système `#4fb6c8` ; navire sombre `#e85bc7`, rendez vous `#f0a84b`, coupure AIS
`#ef6461`, position non confirmée `#e8d45a`, navire sur liste et alerte WATCHLIST `#b48cf2`, changement d'identité
`#5fd3a5`. IBM Plex Sans et Sans Condensed, chiffres
tabulaires.

Disposition (lot U, 21/10/2026, `docs/ux_refonte.md`) : **barre d'état** en haut (**sélecteur de région**, puis un
seul **voyant Système**, vert quand tout fonctionne, sinon la cause ; les indicateurs flux AIS, ingestion, listes,
disque, archivage, satellites et version se déplient au clic ; **recherche** par Cmd + K), **rail** à deux espaces
(**Veille** et **Coulisses**), panneau contextuel, carte avec ses **vues** (Veille, Trafic complet, Satellites,
Couches), **fiche** à droite, **frise** en bas.

* **Échelle d'urgence** (`lib/urgence.ts`) : 3 `#ef6461`, 2 `#f0a84b`, 1 `#aab6bf` ; le type passe par l'icône, ses
  couleurs ne restent que dans la vue Trafic complet.
* **Veille** (`components/VeillePanel.tsx`) : dossiers par navire ou par point (`/api/dossiers`), « N à examiner »,
  « à noter » repliés, fil d'alertes complet en second onglet, état calme, état dégradé en tête (plus de 15 min sans
  AIS en direct, `lib/systeme.ts`). Carte calme : navires des dossiers nets et nommés, le reste en poussière,
  infrastructures à moins d'un mille d'un dossier seules nettes.
* **Enquête** (`components/Recit.tsx`) : tout clic sur un navire ouvre sa fiche, sections repliées ; s'il a un
  dossier, le récit vient en tête (niveau, qui, phrase, gestes, légende de la preuve, chronologie, détail technique
  replié). Preuve dessinée sur la carte (`lib/preuve.ts`, mêmes chiffres que la légende).
* **Clavier** (`lib/clavier.ts`) : J, K, Entrée dans la Veille ; F confirmer, C puis 1 2 3 classer, S suivre, V
  vérifier (devis, Entrée demande, Échap annule), ? aide avec les vrais et faux positifs.
* **Coulisses** (`components/Coulisses.tsx`, rubriques dans `components/Reglages.tsx`, groupes, recherche et
  redirections dans `registres/reglages.ts`) : critère de tri et inventaire dans `docs/ux_refonte.md`, section 13.
  Quatre groupes, dix rubriques : Mon espace (Compte, Notifications, Affichage et démonstration), Surveillance (Règles
  d'alerte, Zones de vigilance), Capteurs (Sources et capteurs, Activité satellite, Budget), Plateforme (Système,
  Journal). Pas d'accueil : dernière rubrique consultée, sinon Système ; point d'attention seulement si besoin ;
  adresse `espace=coulisses&rubrique=…`, anciennes rubriques redirigées ; Échap ramène à la Veille. Les outils de
  travail sont dans la Veille : onglets À examiner, Alertes, Suivis ; couches dans la vue « Couches » de la carte.
* **Panneaux redimensionnables** (`components/Poignee.tsx`, `lib/largeur.ts`) : panneau de gauche (Veille, Coulisses)
  et fiche de droite, de 280 (300 pour la fiche) à 640 px, jamais plus de la moitié de l'écran ; flèches par pas de
  16 px, double clic pour la largeur par défaut, largeur gardée par navigateur ; la carte suit (ResizeObserver).

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
* **Échos radar Sentinel 1** (lot C) : anneaux de la couche « Détections radar et optiques » (blanc avec AIS, couleur
  du navire sombre sans AIS, gris écarté), fiche « détection radar Sentinel 1 » (mesures, vignette avec AIS, navire
  apparié et longueur déclarée), section « Vérification satellite » de la fiche navire, passage analysé plus haut et
  cerclé sur la frise, alertes IDENTITY_MISMATCH et AIS_UNCONFIRMED actives.
* **Photos dans les alertes** : vignette (source VesselFinder indiquée, emplacement neutre sinon) devant chaque navire
  de « Navires concernés » ; dans le fil, très petite vignette facultative (bouton image de l'en tête du fil, réglage
  gardé dans le navigateur, désactivée par défaut).
* **Région affichée** (barre d'état) : Toute la France ou une région ; **un seul réglage** pour la carte et son
  compteur, les infrastructures, le fil, les suivis, la recherche, la frise (histogramme par région, coupures du flux
  toujours globales), les passages et le contour de la couche « couverture ». Changer de région recentre la carte.
* **Navires suivis** (table `followed_vessels`) : onglet Suivis de la Veille (panneau du rail avant le lot U) ; visibles et colorés à toutes les échelles ; leurs
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
* **Fil** (`lib/fil.ts`, onglet Alertes de la Veille depuis le lot U) : filtres par type, gravité et statut (la région vient de la barre d'état) ; tri par gravité
  puis date ; alertes d'un même
  navire regroupées ; le pavillon en tête de ligne, le signe distinctif du type ensuite.
* **Carte** : infrastructures par type (par défaut câbles électriques et parcs éoliens), tracés
  simplifiés et estompés aux échelles larges, noms à partir du zoom 9, mode « concernées seulement » (alertes
  ouvertes, ou à moins de 2 milles de la sélection) ; navires ordinaires estompés aux échelles larges, ceux des
  listes et en alerte nets et colorés.

### 8. Modèle radar et configuration

**CircleNet V2S** (xView3, membre 4, TorchScript `models/xview3_membre4_v2s.jit`, couvert par
`tests/test_contract.py`) : canaux **VH puis VV**, sigma0 en dB, normalisation sigmoïde de ((x + 20) × 0,18) ;
tuiles de 2048 pixels au pas de 1536 ; Sentinel Hub **SIGMA0_ELLIPSOID en BILINEAR** (le plus proche voisin fait
tomber le F1 de 0,96 à 0,89) ; précision mixte coupée sur le GPU Apple ; seuils présence 0,20, navire 0,338,
pêche 0,35.

`config/rules.yaml` : toutes les règles et l'ingestion. **Monter la version à chaque changement**, chaque alerte
l'enregistre. Les valeurs des règles viennent de la calibration danoise et sont **à recalibrer pour la France**
(liste et méthode : `docs/audit_code.md`, section 3).
