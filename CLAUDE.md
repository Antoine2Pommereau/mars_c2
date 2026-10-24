# MARS C2 : contexte du projet pour Claude Code

Fichier de travail, lu en entier avant toute tâche et tenu à jour à chaque jalon ; l'historique va dans
`docs/journal.md`, pas ici. Direction : Claude Doc « MARS C2, mise à jour du projet »
(https://claude.ai/code/artifact/00f50730-d117-4ad4-94ac-0cb9b967f002). Index de la documentation : `docs/README.md` ;
détail technique (démarrage, API, interface) :
`docs/reference.md` ; pièges en détail : `docs/pieges.md`, résolus : `docs/pieges_archives.md` ; ancienne version complète :
`docs/archives/CLAUDE_avant_Z3.md`.

## 1. Le projet

**Objectif.** Outil opérationnel, en direct, qui protège les infrastructures maritimes françaises (câbles,
interconnexions, pipelines, parcs éoliens) et suit la flotte fantôme dans les eaux françaises, en confrontant l'AIS
déclaratif au comportement des navires et à des capteurs satellitaires indépendants. Client imaginé : une autorité
française de l'action de l'État en mer. Projet personnel d'Antoine, vitrine pour un poste de Forward Deployed Engineer.

**Pistes.** 1. Infrastructures sous marines : ancre traînante, arrêt ou flânerie dans un corridor, coupure AIS, rendez
vous, écho sans AIS. 2. Flotte fantôme : navire des listes reconnu par son **OMI**, changements de nom ou de pavillon,
transbordement, incohérence radar et AIS. Brouillage GNSS écarté.

**Sources.** AIS en direct par AISStream (plan B non construit : récepteur personnel, AISHub) ; Sentinel 1 et 2
(Copernicus) ; VIIRS (NASA LANCE) ; EMODnet (infrastructures) ; catalogue GUR (`Vessels1.db`) et OpenSanctions ; GSHHG
(trait de côte). Archives (DMA, GeoTrackNet Bretagne, École navale) : **entraînement seulement**, usage personnel et
non commercial, aucune redistribution (`docs/trajectoire.md`).

**Modèles.** AI2 `vessel-detection-sentinels` (Sentinel 1), rslearn `sentinel2-vessel-detection` (Sentinel 2),
`vessel-detection-viirs` ; cercle appris et routes en plusieurs hypothèses (lot T) ; TrAISformer à venir ; CircleNet
(xView3, chaîne du Mac) retiré au lot Z4, retrouvable dans l'historique Git (`docs/journal.md`).

**Zones collectées** (`mars/ais/live.py`, lat_min, lon_min, lat_max, lon_max) : `bretagne` (rail d'Ouessant) 47.3,
-6.8, 49.6, -3.0 ; `manche` 48.4, -5.0, 51.2, 2.6 ; `gascogne` 43.3, -6.0, 47.4, -1.0 ; `mediterranee` (golfe du Lion,
Côte d'Azur, Corse) 41.2, 3.0, 43.7, 9.8.

**Principes directeurs** : traçabilité (chaque alerte remonte à ses preuves, à la version des règles et à sa trace) ;
mesurer plutôt qu'affirmer (une règle silencieuse se prouve par un test par injection) ; respecter la physique du
capteur ; tranche verticale d'abord ; calibrer au cas par cas ; déclasser plutôt qu'exclure (un statut AIS est un
indice, jamais une excuse).

## 2. Travailler avec Antoine

* **Répondre et rédiger les rapports en français.**
* **Aucun tiret dans les textes rédigés**, sous aucune forme (trait d'union, tiret court, tiret long) : prose,
  documentation, commentaires lus, messages de commit, libellés de l'interface. Reformuler : « rendez vous »,
  « Sentinel 1 », « MARS C2 », « bord à bord ». Code, commandes, identifiants, noms de fichiers et URL exemptés.
  Dates au format 17/06/2024.
* Expliquer les choix et leur raison, chiffrer, dire les limites et les erreurs, finir par la prochaine étape et les
  commandes de vérification avec ce qu'on doit y lire.
* Design validé : poste de commandement épuré et sombre, très peu de texte ; Antoine trouve vite une interface
  « trop chargée ».
* Mac : MacBook Air (Apple Silicon), Python 3.12 dans `.venv`, Docker Desktop, Node par Homebrew ; dépôt dans
  `~/Documents/Projet Perso/MarsC2/mars_c2`. GitHub : https://github.com/Antoine2Pommereau/mars_c2.
* Travail en branche ; commit explicite sans tiret, terminé par la ligne Co-Authored-By demandée ; fusion dans `main`
  en avance rapide **seulement sur demande**, puis pousser `main` et la branche.
* Avant tout commit : aucun fichier de données (`data/`, `*.parquet`, `Vessels1.db`, `maritime.csv`, `.env`, `.gstack`,
  `node_modules`, PNG, dumps, `.ckpt`, `.tif`, mesures `.json`, `.pkl`, `.zip`, `.pdf`), sauf demande explicite.
* Ne jamais afficher ni valider un secret ; ne jamais écrire dans `docs/` ni ici l'adresse IP publique du serveur, un
  identifiant de compte, d'organisation ou de projet Scaleway, ni le sujet ntfy.

## 3. Règles permanentes de développement

* **OpenTelemetry** : toute addition est tracée, sans donnée sensible ; une fonctionnalité non tracée n'est pas finie.
  1. Tâche : par `run()` de `scripts/taches.py` (span « tache <nom> », trace_id dans `task_runs`) ; commande : par
     `@commande` (registre `COMMANDES`, span « commande <nom> »).
  2. Étape métier : `with traces.span("domaine.etape", **attributs)` ; identifiants internes seulement (jamais nom
     de navire, position, clé, jeton, mot de passe, adresse signée, sujet ntfy).
  3. Appel externe : requests et httpx tracés d'office ; sinon un span avec `peer.service`, nommé « MÉTHODE
     opération », hôte déclaré dans `EXTERNES` et `OPERATIONS` de `mars/traces.py` ; appels nombreux par
     `traces.en_parallele`.
  4. Route : tracée d'office ; route lourde : un span métier ; route appelée chaque seconde : `API_NON_TRACEES`.
  5. Travailleur : son script importe `otel_mini` et rattache ses spans à `TRACEPARENT` ; toute écriture d'alerte
     dans une trace (`traces.en_base`).
  6. **Jamais de traces pour Grafana, Tempo ou le collecteur** (le collecteur les écarte, `filter/outils`).
  7. `tests/test_traces.py` vert, puis `scripts/verifier_images.sh traces`.
  Détail : `docs/opentelemetry.md`. La sortie d'une commande manuelle de taches est lisible (sans suffixe trace_id) ;
  les journaux des services gardent le suffixe ; une ligne à copier dans un fichier passe par `traces.ligne_brute`.
* **Version des règles** : monter `version` de `config/rules.yaml` à chaque changement ; chaque alerte l'enregistre.
* **L'état du panneau de contrôle prime sur le fichier** (pauses, plafonds, modes des règles, zones) : il est en base
  (`interrupteurs_satellites`, `reglages_controle`). Ne jamais modifier `config/rules.yaml` sur le serveur pour
  suspendre quoi que ce soit ; une décision de lancement ou d'alerte lit `rules["_controle"]` des règles appliquées
  (`controle.appliquer`), jamais `load_rules()` seul ; une écriture d'alerte passe par `controle.decision`,
  `controle.marquer_zone` ou `save_alerts` et porte `controle_empreinte`.
* **Règle en continu** : jamais effacer puis recréer ses alertes (décisions des opérateurs perdues) : `save_alerts`.
* **Écritures de l'API** : toute route POST, PUT, PATCH ou DELETE prend `Depends(exiger_operateur)` (session, JSON,
  même origine, hors démonstration), auteur tiré de la session ; liste blanche `ECRITURES_LIBRES`
  (`tests/test_securite_api.py`). Côté interface, toute écriture passe par `ecrire` de `web/src/lib/api.ts`.
* **Variables par service** : aucun conteneur ne reçoit le `.env` entier ; une variable lue par le code est donnée au
  service dans Compose ou déclarée dans `FACULTATIVES` de `tests/test_environnement.py` avec sa raison.
* **Migrations** : idempotentes ; à partir de 39, la dernière instruction inscrit le nom du fichier dans
  `schema_migrations` (`tests/test_migrations.py`).
* **Libellés** : tous dans `web/src/lib/libelles.ts`, aucun texte visible en dur.
* **Travailleurs** : toute destruction passe par `Scaleway.destroy` (garde : jamais une autre instance) ; aucun chemin
  destructeur qui l'évite.
* **Publication et déploiement** : seulement par `bash scripts/publier.sh` (Mac) et `bash scripts/deployer.sh
  <empreinte> --migrations` (serveur) ; jamais de bloc collé qui contourne leurs gardes (arbre propre et à jour,
  empreinte de chaque image, `verifier_images.sh`, image à l'empreinte, sauvegarde avant migration, tableau des
  versions). Tout bloc serveur est entre parenthèses avec la garde :
  `( cd /opt/mars_c2 || { echo "PAS SUR LE SERVEUR : arrêt"; exit 1; } ; … )`, empreinte lue par
  `git rev-parse --short HEAD`. Serveur : jamais d'action sur les alertes depuis un essai, l'API par le tunnel en GET.

## 4. État actuel (24/10/2026)

`main` à 29bcfff (lots Z1 et Z2), règles 2026.10.47, migrations 01 à 42. État des sources : en base (panneau), pas
dans le fichier. Détail des alertes : `docs/regles_alertes.md` ; plan de calibration : `docs/calibration.md`.

| Capteur ou fonction | État | Dernier essai réel | Coût unitaire mesuré |
|---|---|---|---|
| Collecte AIS, ingestion | en service | continu ; environ 680 messages par minute (06/10) | inclus au serveur |
| Archive R2, purge, sauvegarde | en service | chaque nuit ; restauration essayée le 24/10 | R2 gratuit (moins de 10 Go) |
| Règles WATCHLIST, IDENTITY_CHANGE, RENDEZVOUS | en service | cycle de 5 min, 18,8 s (22/10) | inclus |
| Règle AIS_GAP | observation (fichier) | 84 épisodes observés en 7 jours (22/10), aucune alerte | inclus |
| VIIRS (travailleur DEV1-M) | en pause depuis le 21/10 ; reprise au déploiement de 29bcfff | 21/10, 12 granules | environ 0,02 € la nuit |
| Sentinel 1 sur processeur (GP1-S, POP2 en secours) | en pause depuis le 21/10 | 19/10, analyse 97, 1,7 % de la scène lue | 0,19 € l'analyse |
| Sentinel 1 sur GPU (L4, L40S) | jamais éprouvé (rupture de stock, Pascal incompatible) | aucun | L4 0,7875 € l'heure à la minute |
| Sentinel 2 (GP1-S, rslearn) | en pause depuis le 21/10 | 20/10 : 3 échecs, correctif livré non rejoué | 0,19 € la session |
| Tip and cue, vérification satellite | en pause depuis le 21/10 | 20 demandes réelles, aucune observation | selon le capteur |
| Alertes radar (DARK_SHIP radar, AIS_UNCONFIRMED, IDENTITY_MISMATCH) | jamais éprouvées | aucune alerte réelle | inclus |
| Trajectoire, étape 2 (analogues) | hors production | mesures sur l'archive à faire | inclus |
| Supervision (Grafana, ntfy, Tempo) | en service | continu | inclus |
| Échéances des secrets | en service après déploiement de 29bcfff | clés Copernicus des travailleurs : 14/11/2026 | inclus |
| Interface (Veille, Enquête, Coulisses, démonstration) | en service | lot U du 21/10 | inclus |

Coût mesuré (facture Scaleway du 1er au 23/10) : environ 19 € par mois hors taxes en régime permanent (serveur DEV1-M
0,0202 € l'heure, réseau et stockage environ 4 € par mois). Prochaines étapes : déployer 29bcfff, mesure des coupures
AIS le 29/10 (`docs/calibration.md`), lot Z4 (code mort), Z6 (satellites en réel), Z7 (calibration), trajectoire
étape 2 et TrAISformer.

## 5. Architecture

| Composant | Où | Rôle |
|---|---|---|
| `db` | PostgreSQL 16, PostGIS 3.4, `pg_stat_statements` | positions (30 jours), navires et identités, listes, alertes et preuves, satellites, tâches, panneau |
| `backend` | FastAPI, asyncpg | API, flux SSE, dossiers, panneau de contrôle, retour des travailleurs (port 8090, jeton) |
| `web` | nginx, React | interface ; noms acceptés : localhost, 127.0.0.1, web ; en têtes de sécurité |
| `collector` | profil `direct` | AISStream vers Parquet `data/ais_live` (un fichier par minute, zone et type) ; santé par `scripts/sante.py` |
| `ingest` | profil `direct` | Parquet vers la base toutes les 15 s, allègement (un point par minute en route, toutes les dix minutes à l'arrêt) |
| `taches` | serveur | règles toutes les 5 min ; chaque nuit : archive R2, purge, sauvegarde, listes, passages, échéances ; VIIRS à 06:30 ; Sentinel toutes les 30 min ; tip and cue ; surveillance des travailleurs chaque minute ; disque ; essai de restauration chaque mois |
| `grafana`, `otel`, `tempo` | serveur, 127.0.0.1 | tableaux et alertes ntfy (Grafana sur PostgreSQL, rôle `grafana_lecture`), collecteur OpenTelemetry, traces gardées 72 h |
| Travailleurs | instances Scaleway éphémères | VIIRS sur DEV1-M (fr-par-1) ; Sentinel 1 sur GP1-S puis POP2 (fr-par-2), GPU au delà de 4 fenêtres ; Sentinel 2 sur GP1-S ; tâche et secrets remis une fois par le réseau privé ; groupe de sécurité `mars-travailleurs` ; résultats validés (`mars/validation.py`) |

Serveur Scaleway DEV1-M (4 Go de mémoire, 20 Go de disque), `/opt/mars_c2`, aucun port public hormis SSH ; interface
et Grafana par tunnel SSH. Chaîne du direct : AISStream, Parquet, ingestion, base, flux SSE. Chaîne satellite :
sélection (navire des listes sur corridor, alerte VIIRS, tip and cue), travailleur, résultats validés, fusion AIS
(`mars/fusion/`), évaluation commune (`mars/evaluation.py`), alertes. Architecture des travailleurs :
`docs/travailleurs.md` ; Sentinel 1 et 2 : `docs/sentinel.md`, `docs/sentinel2.md` ; tip and cue et trajectoire :
`docs/tip_and_cue.md`, `docs/trajectoire.md` ; supervision : `docs/supervision.md`.

## 6. Carte du code

```
config/rules.yaml, config/supervision.env   seuils versionnés, seuils de supervision
db/init/01 à 42                              schéma et migrations (registre schema_migrations depuis 39)
mars/ais/          live.py (zones, collecte), ingest.py, mid.py (pavillon)
mars/rules.py      règles en continu ; watchlist.py, archive.py, r2.py, r2_signature.py
mars/satellites.py calendrier des passages ; viirs.py ; sentinel.py ; sentinel2.py ; evaluation.py ; fusion/
mars/tip_and_cue.py, trajectoire.py, trajectoire_mesures.py   vérification satellite, cercle appris, analogues
mars/travailleurs.py, validation.py, diagnostic.py            orchestrateur Scaleway, contrôle des résultats, diagnostics
mars/controle.py, dossiers.py, frise.py, supervision.py, echeances.py, restauration.py, traces.py, config.py
backend/           app.py, contenu.py, dossiers.py, controle.py, demandes.py, travailleurs.py, sentinel.py,
                   satellites.py, preuves.py, regions.py, demo.py
travailleurs/      viirs.py, sentinel.py (Python 3.8), sentinel2.py, demarrage.sh, otel_mini.py, traces.sh, faux_s3.py
scripts/           taches.py, ais_live.py, ais_ingest.py, sante.py, build_masks.py, mesures_calibration.py,
                   publier.sh, deployer.sh, essai_restauration.sh, verifier_images.sh, essai_traces/
web/src/           components/, lib/ (api, libelles, urgence, clavier, temps, url, session…), registres/
grafana/generer.py, grafana/provisioning/, otel/   supervision en code
tests/                                             pytest (base de test facultative), web/tests (node --test)
```

## 7. Base et API

Tables par domaine : direct (`positions`, `vessels`, `vessel_identities` et vue `imo_history`, `ais_days`,
`ingested_files`, `stats_minute`, `stats_10min`) ; listes (`watchlist`, vue `vessel_watch`) ; alertes (`alerts` avec
`rule_key` stable, `alert_evidence`, `alert_actions`) ; carte (`regions`, `infrastructure`, `land`, `stationary_zones`,
`reception_cells`, `fixed_echoes`) ; satellites (`sar_passes`, `analyses`, `detections`, `viirs_*`,
`preuves_images`, `travailleurs`, `demandes_observation`) ; panneau (`interrupteurs_satellites`, `reglages_controle`,
`journal_controle`, `sessions_operateur`, `zones_vigilance`, `scenarios_demo`, `notifications_*`,
`tentatives_connexion`) ; exploitation (`task_runs`, `archives`, `signes_de_vie`, `mesures_disque`, `echeances`,
`schema_migrations`). Une migration s'applique par `deployer.sh --migrations`.

API par familles (liste à jour : `curl -s localhost:8000/openapi.json`) : flux et trafic (`/api/stream`,
`/api/traffic?at=`), dossiers et décisions (`/api/dossiers`), alertes et fiches, recherche (`/api/search`), région
(`?region=`), satellites (`/api/satellites/passes`, `/api/viirs/*`, `/api/sentinel/*`, `/api/vessels/{id}/satellite`),
demandes d'observation (`/api/demandes`, `/api/observations`), panneau (`/api/controle/*`), état (`/api/ingestion`,
`/api/controle/systeme` avec les échéances), retour des travailleurs (`/api/travailleurs/{id}/etat|resultats|journal`).
L'instant est tenu par le navigateur (routes `?at=`) ; l'horloge partagée a été retirée (migration 43).

## 8. Interface (lot U, `docs/ux_refonte.md`)

* Jetons (`web/src/styles.css`) : fond `#0e1419`, panneaux `#141c23`, surélevé `#1b252e`, filets `#26323d`, texte
  `#e6ecf0`, secondaire `#7c8b97`, signal `#4fb6c8` ; navire sombre `#e85bc7` ; IBM Plex Sans, chiffres tabulaires.
  **La couleur est réservée à ce qui demande l'attention.** Échelle d'urgence (`lib/urgence.ts`) : 3 `#ef6461`,
  2 `#f0a84b`, 1 `#aab6bf` ; couleurs par type seulement dans la vue Trafic complet.
* Barre d'état : région affichée (un seul réglage pour tout l'écran), voyant Système unique, recherche (Cmd + K).
* Rail à deux espaces : **Veille** (dossiers par navire ou par point, « à examiner », « à noter » repliés ; onglets À
  examiner, Alertes, Suivis) et **Coulisses** (quatre groupes, dix rubriques : Compte, Notifications, Affichage et
  démonstration ; Règles d'alerte, Zones de vigilance ; Sources et capteurs, Activité satellite, Budget ; Système avec
  les échéances, Journal ; pas d'accueil, Échap ramène à la Veille).
* Carte et vues (Veille, Trafic complet, Satellites, Couches) ; **Enquête** (`components/Recit.tsx`) : récit du dossier
  et preuve dessinée (`lib/preuve.ts`) ; fiches par registre (`registres/sections.tsx`) ; frise en bas.
* Clavier : J, K, Entrée ; F confirmer, C puis 1 2 3 classer, S suivre, V vérifier, ? aide. Une écriture sans session
  ouvre la connexion puis reprend ; le mode démonstration (pseudonymes, photos retirées) est en lecture seule.
* Temps (`lib/temps.ts`) : direct, plage, rejeu ; adresse de la page (`lib/url.ts`) : un lien rouvre la même vue.
* Panneaux redimensionnables (`components/Poignee.tsx`) ; registres (`web/src/registres/`) : alertes, couches,
  sections, frise, réglages, état.

## 9. Démarrer et tester

Mac : `./start.sh` (base, API, interface, Vite) ; interface de développement http://localhost:5173
(`MARS_API=http://localhost:8765 npm run dev` pour une autre API) ; construite http://localhost:8080 ; API
http://localhost:8000/api ; base, API et interface du Mac sur 127.0.0.1 seulement. Après une modification : `backend/`,
`docker compose up -d --build backend` ; `web/`, rien en développement ; `config/rules.yaml`, rien (relu).

Tests : `python -m pytest tests` (chaque test borné à 60 s) ; avec la base de test
(`MARS_TEST_DATABASE_URL=postgresql://mars:mars@localhost:55432/mars`, migrations 01 à 42) : **484 réussis, 1 ignoré**
(24/10/2026) ; sans base, 360 réussis, 125 ignorés. Interface : `npm run typecheck`, `npx knip`, `npm test`. Images :
`scripts/verifier_images.sh backend scripts web controle sante traces` (et `ai2`, `fenetres`, `s2`, `s2-s3` selon le
travail). Décomptes passés : `docs/journal.md`.

## 10. Déployer et exploiter

Guide complet : `docs/exploitation.md` (publier, déployer, vérifier, migrations, sauvegarde et essai de restauration,
reprise après sinistre, échéances, sécurité, dépendances figées) ; installation initiale : `docs/deploiement.md`.
Images construites sur le Mac en `linux/amd64` (le serveur ne construit rien), publiées sur ghcr
(`mars_c2-backend`, `mars_c2-web`, `mars_c2-scripts`, `latest` et empreinte), une image à la fois sur un disque
serré. Mettre en pause ou reprendre un capteur : panneau (Coulisses, Sources et capteurs) ou `taches.py
satellites-pause on|off <source>`. Version en service : `bash scripts/deployer.sh --verifier`.

## 11. Pièges vivants

Résumé ; texte complet de chaque piège, avec ses identifiants et ses dates : `docs/pieges.md`.

**Données et base**
* Horodatages AISStream : `format="ISO8601"`, arrondir à la microseconde ; archive : lire les 19 premiers caractères
  avec Arrow (`trajectoire_mesures.secondes`), jamais une division fixe.
* Arrow : allocateur du système (`pa.set_memory_pool(pa.system_memory_pool())`, `read_table(use_threads=False)`) ;
  mesures longues en colonnes numpy ; mesure lourde par `TACHES_MEM_LIMIT=1200m docker compose run --rm --no-deps
  taches …`, jamais dans le conteneur en service.
* NaN en NULL avant JSON ou base ; paramètres asyncpg typés (`$1::timestamptz`) ; psycopg n'adapte pas les entiers numpy.
* `LIKE 'préfixe%'` sans index : charger en une requête. Géométrie lourde dans un `WITH` : `MATERIALIZED`. Distance à
  la côte : table subdivisée, plus proches voisins, jamais `ST_DWithin` large sur `land`. GSHHG lu en numpy.
* Identités qui alternent (227000000, « MUTIN » et « FS MUTIN ») ; entrées GUR sans nom (PASIPHAE, OMI 9289518) ;
  `Vessels1.db` en WAL (`load_gur` remet les octets 18 et 19 à 1).
* Règles en continu : fenêtre de 24 h (une durée plus longue se relit depuis l'alerte précédente) ; tout filtre avant
  un appariement garde une marge sous le seuil final ; un trou de collecte agit de trois façons sur les coupures.
* Recharger d'anciens jours : `taches.py restaurer-positions` ; la collecte écrit un fichier caché puis renomme.
* Les tests en base vident des tables de la base de test : sauvegarder d'abord un jeu chargé.
* Statuts AIS déclaratifs : indices seulement ; VIIRS sans AIS ne veut pas dire navire sombre (réception absente au large).

**Serveur, images, déploiement**
* `tests/test_images.py` et `verifier_images.sh` avant publication (un test passe depuis le dépôt même si l'image ne
  copie pas un module) ; `pg_isready -h 127.0.0.1` ; noms d'images fixes ; surcouche Compose : `!override`, `!reset`.
* YAML ne partage pas les ancres entre fichiers : la surcouche recopie les ancres `x-*` (test d'égalité).
* Pas de commentaire en fin de ligne dans `.env` ; une variable changée n'atteint un conteneur qu'à sa recréation.
* `pg_dump` 16 ; `init: true` ; Docker ne relance pas un conteneur « unhealthy » ; jamais le socket Docker dans un
  conteneur ; aucune tâche asyncio sans garde.
* nginx : `Host $http_host`, `X-Forwarded-Host`, `X-Real-IP` ; cookie Secure seulement en https. Les scripts visent le
  port 8000. FastAPI récent enveloppe les routeurs (`original_router`) : parcourir les routes à plat.
* zsh : `$i:latest` applique le modificateur `:l` (écrire `${i}:latest`) ; une variable de boucle `path` écrase `PATH`.
* Le dossier temporaire de Claude Code est purgé au bout de quelques jours : repartir d'un clone neuf.
* L'horloge de la machine Docker du Mac peut prendre du retard ou sauter après une veille.

**Stockage et sources externes**
* R2 : `request_checksum_calculation="when_required"`, MD5 fourni ; ETag d'un envoi en morceaux relu ; adresse
  `.eu.` ; deux jetons, deux seaux : jamais `r2_get_url` pour un travailleur (jeton principal filtré sur l'IP).
* Copernicus : adresses présignées refusées, en tête `Authorization` seulement ; expiration des clés dans le `.env` ;
  STAC refuse un multipolygone chevauchant ; COG seulement dans `sentinel-1-grd` ; identifiant de prise de vue
  réattribué (rapprocher par satellite et orbite absolue).
* Sentinel 2 : heure de la tuile dans `SENSING_TIME` ; SCL classe le navire en nuage (juger un voisinage) ; absence
  jugée dans le cœur des fenêtres lues seulement ; rasterio refuse les clés AWS en options GDAL (`AWSSession` ou
  environnement) ; satlaspretrain télécharge son squelette (servi depuis R2) ; GDAL ne compte pas le premier bloc lu.
* Photos VesselFinder : usage personnel, conditions à vérifier avant une démonstration publique.

**Travailleurs Scaleway** (détail : `docs/travailleurs.md`)
* Destruction : « stopped in place » (poweroff, attendre, supprimer) ; volume bloc supprimé à part ; taille d'un
  volume bloc dans l'API Block Storage ; disponibilités paginées ; « scarce » ne garantit rien ; Pascal incompatible.
* PrivateNetworksFullAccess, carte « available » avant démarrage, DHCP au démarrage, `SCW_PRIVATE_NETWORK_ID` du
  réseau et non du VPC, réseau privé régional ; pas de SSH : `taches.py journal-travailleur` ou console série.
* Plafond compté sur les instances créées ; facturation à l'heure entamée (CPU) ou à la minute (GPU).
* Image d'AI2 Sentinel 1 : Python 3.8, imports `src.*`, `nms_thresh` entier, fenêtre d'au moins 2 300 pixels ;
  bibliothèque standard seulement dans le script ; travailleur sans scène aboutie en « echec ».

**Supervision et traces**
* Grafana 12 : pas de variables d'environnement dans les règles provisionnées (seuils dans `parametres_supervision`),
  gabarit du webhook, tuiles colorées en SQL, `noValue` ; jamais lire `positions` ; alerte « une fois » sur 55 min ;
  une règle modifiée n'est relue qu'au redémarrage de Grafana.
* Traces : requests remplace `traceparent` posé à la main ; images du collecteur et de Tempo sans shell ; TraceQL `[.]` ;
  Tempo 3 ; filtre OTTL entre apostrophes ; commande nouvelle par `@commande`.

**Interface**
* Tailwind 4 : règle hors couche prioritaire ; MapLibre : pas de `line-dasharray` par donnée, `map.resize()` par
  ResizeObserver, position par style direct ; poignée hors de la zone qui défile ; `fixed` sous `backdrop-blur` par
  portail ; raccourcis en capture.
* Deux formes du trafic en colonnes (`decodeTraffic`) ; `retry` sur frise et trafic ; dossiers lus à l'instant
  affiché ; `noUnusedLocals`.
* Playwright : Chromium sans affichage avec swiftshader pour voir la carte (`chrome-headless-shell`).
* Un `Envois` vide est faux en Python : `envoi is not None`. Ne jamais remplacer un dossier par le Finder (`ditto`).
