# Déploiement sur le serveur Scaleway (DEV1-S)

Le serveur (`/opt/mars_c2`) fait tourner en permanence, avec Docker Compose, six services : la base (`db`),
l'API (`backend`), l'interface (`web`), la collecte AISStream (`collector`), l'ingestion en base (`ingest`) et les
tâches planifiées (`taches` : règles en continu, archivage sur R2, purge, sauvegarde, surveillance du disque). Pas de
cron sur l'hôte : tout redémarre avec la plateforme (`restart: unless-stopped`). Les images sont construites sur le
Mac en linux/amd64 et chargées sur le serveur (section « Construire sur le Mac et déployer ») : le serveur n'a ni la
place ni la mémoire pour les construire. Le service d'inférence radar reste sur le Mac (GPU
Apple) ; sur le serveur, l'API le signale « injoignable », ce qui est attendu.

Rien n'est ouvert sur Internet hormis SSH : la base n'a aucun port publié, l'API et l'interface écoutent sur
127.0.0.1 et se consultent par un tunnel SSH. C'est le rôle de `docker-compose.serveur.yml`.

## Première installation

Sur le serveur (Ubuntu 24.04, `root`) :

```bash
# Docker et Docker Compose (2.24.4 au moins, pour les étiquettes !reset et !override)
curl -fsSL https://get.docker.com | sh
docker compose version

# Mémoire d'échange : la DEV1-S n'a que 2 Go de mémoire
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

git clone https://github.com/Antoine2Pommereau/mars_c2.git /opt/mars_c2 && cd /opt/mars_c2
mkdir -p data/ais_live data/listes
```

Fichier `/opt/mars_c2/.env` (jamais versionné, `chmod 600`) :

```bash
AISSTREAM_API_KEY=ta_cle
COMPOSE_FILE=docker-compose.yml:docker-compose.serveur.yml
COMPOSE_PROFILES=direct
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
# Seau en juridiction européenne : https://<identifiant_du_compte>.eu.r2.cloudflarestorage.com
R2_ENDPOINT=https://<identifiant_du_compte>.r2.cloudflarestorage.com
R2_BUCKET=mars-c2
```

`COMPOSE_FILE` et `COMPOSE_PROFILES` font qu'un simple `docker compose up -d` applique la surcouche du serveur et
démarre collecte, ingestion et tâches (profil `direct` inactif sur le Mac : une seule collecte par clé).

Premier démarrage : charger les images depuis le Mac (section « Construire sur le Mac et déployer », étapes 1 et 2),
copier les listes de surveillance, démarrer, importer les listes, puis construire les masques France :

```bash
# Sur le Mac
scp data/listes/Vessels1.db data/listes/maritime.csv root@IP_DU_SERVEUR:/opt/mars_c2/data/listes/
# Sur le serveur
cd /opt/mars_c2 && docker compose up -d --no-build
docker compose run --rm ingest python scripts/import_watchlist.py
docker compose exec taches python scripts/build_masks.py --sans-cache --jours 7
```

## Configurer Cloudflare R2

1. Tableau de bord Cloudflare, R2, « Create bucket » : nom `mars-c2`, emplacement automatique (Europe de
   préférence). Ne pas activer d'accès public.
2. R2, « Manage R2 API Tokens », « Create API token » : permission **Object Read & Write**, limitée au seau
   `mars-c2`. Noter l'identifiant de clé, le secret et le point d'accès S3
   (`https://<identifiant_du_compte>.r2.cloudflarestorage.com`) ; les reporter dans `.env`.
3. Ne pas mettre de règle de cycle de vie sur le seau : la suppression des anciennes sauvegardes est faite par la
   tâche elle même, après vérification de la nouvelle.

**Seau en juridiction européenne.** Un seau créé avec la juridiction « European Union » n'a pas la même adresse :
`R2_ENDPOINT=https://<identifiant_du_compte>.eu.r2.cloudflarestorage.com`. Avec l'adresse sans `.eu`, tout accès est
refusé (`AccessDenied` ou `NoSuchBucket`, alors que la clé et le nom du seau sont justes) : cas rencontré au premier
déploiement. Le tableau de bord du seau, onglet « Settings », donne l'adresse S3 exacte à reporter.

Contrôle d'accès, depuis le serveur :

```bash
docker compose exec taches python scripts/taches.py sauvegardes   # liste vide ou sauvegardes, sans erreur
```

Organisation du seau :

| Préfixe | Contenu |
|---|---|
| `ais/positions/zone=…/date=AAAA-MM-JJ/` | Positions brutes d'une journée et d'une zone, un fichier Parquet par passage d'archivage (zstd, trié par navire puis par instant, colonnes identiques à la collecte) |
| `ais/statiques/zone=…/date=AAAA-MM-JJ/` | Messages statiques, même organisation |
| `sauvegardes/mars_AAAAMMJJTHHMMSS.dump` | Sauvegardes de la base (`pg_dump` au format personnalisé), les 7 plus récentes |

## Tâches planifiées (service `taches`)

Chaque nuit à 02:30 UTC (`TACHES_HEURE`), dans cet ordre, chacune consignée dans la table `task_runs` :

1. **Archivage** des journées terminées (veille ou avant, 30 minutes après minuit) : par dossier zone et type,
   les petits fichiers Parquet **déjà ingérés** sont regroupés en un fichier, envoyé sur R2 en une seule requête avec
   son MD5 (R2 refuse un contenu altéré), puis vérifié (voir « Confirmation des envois » ci dessous). L'archive est inscrite dans la table `archives` avec la
   liste des fichiers d'origine ; seuls ces fichiers, ingérés et confirmés, sont supprimés du serveur. Un fichier pas
   encore ingéré reste en place (signalé dans le journal) et sera archivé plus tard dans une seconde partie. Après un
   arrêt entre l'envoi et la suppression, la tâche supprime sans renvoyer : pas de doublon sur R2.
2. **Purge** des positions de plus de 30 jours (`CONSERVATION_JOURS`), journée par journée, **seulement si la
   journée est archivée** ; retrait de la journée de `ais_days` et du registre d'ingestion des dossiers disparus.
3. **Sauvegarde** : `pg_dump` compressé envoyé en flux sur R2, sans fichier local, **sans les données de
   `positions`** (dans l'archive) ni du registre d'ingestion. Vérifiée par le code de sortie de `pg_dump`, puis par
   la taille et le MD5 de l'objet stocké (relecture) ; une sauvegarde invalide est supprimée. Les 7 dernières sont gardées (`SAUVEGARDES_GARDEES`), les plus
   anciennes supprimées seulement après la vérification de la nouvelle.

**Confirmation des envois.** L'ETag d'un objet envoyé en une seule requête est son MD5 : il est comparé au MD5
calculé avant l'envoi, avec la taille. L'ETag d'un objet envoyé en plusieurs morceaux (au delà de 16 Mo, cas des
sauvegardes en flux) n'est pas le MD5 du fichier : il se termine par « tiret, nombre de morceaux ». L'objet est
alors relu depuis R2 et son MD5 recalculé, comparé à celui calculé pendant l'envoi. Les archives quotidiennes
(environ 6 Mo par zone et par type) partent toujours en une requête ; la relecture ne concerne que les envois en
morceaux. Dans tous les cas, un fichier n'est supprimé du serveur que s'il est ingéré et confirmé.

**Règles en continu** (toutes les 5 minutes, section `continu` de `config/rules.yaml`), sur une fenêtre glissante
de 24 heures bornée par la dernière position reçue :

| Règle | Alerte | Prérequis |
|---|---|---|
| Rendez vous | `RENDEZVOUS` | trait de côte (`land`) |
| Coupure AIS | `AIS_GAP` | zone de réception fiable (`reception_cells`) |
| Navire d'une liste dans nos eaux | `WATCHLIST`, une par passage (12 h sans position ouvrent un passage nouveau), niveaux fort, sanctionné, flotte fantôme, suspect GUR | listes importées |
| Changement d'identité | `IDENTITY_CHANGE` : nouveau nom confirmé 6 h, ou même OMI sous un autre MMSI | aucun |

Une règle dont le prérequis manque est sautée (le motif apparaît dans `task_runs`) : lancer d'abord la construction
des masques. Les alertes sont mises à jour en place (clé `rule_key`) : statut et décisions des opérateurs sont
conservés ; une alerte encore vierge qui n'est plus détectée est retirée. Les minutes où le flux AIS est coupé
(moins de 20 % de la médiane des positions par minute) ne comptent pas dans la durée d'un silence : une interruption
d'AISStream ne fait pas apparaître une coupure sur chaque navire. Un cycle n'est journalisé que s'il crée ou retire
des alertes ; chaque cycle est consigné dans `task_runs` (tâche `regles`, deux jours gardés).

Toutes les 10 minutes : espace libre du disque. Sous 15 % (`ALERTE_DISQUE_PCT`), une ligne `ALERTE DISQUE` dans
le journal du service, et `"alerte_disque": true` dans `/api/ingestion`.

Une tâche réussie ne rejoue pas le même jour ; une tâche en échec est retentée une heure plus tard. Au premier
démarrage dans la journée, après 02:30, les trois tâches s'exécutent tout de suite. Commandes manuelles :

```bash
docker compose exec taches python scripts/taches.py archiver      # ou purger, sauvegarder, disque, regles
docker compose exec taches python scripts/taches.py sauvegardes   # liste des sauvegardes sur R2
```

## Masques France (trait de côte, mouillages, réception)

Ponctuellement, dans le conteneur `taches` (dépendances incluses dans l'image des scripts) :

```bash
docker compose exec taches python scripts/build_masks.py --sans-cache --jours 7
```

* **Trait de côte** : GSHHG pleine résolution, découpé sur l'emprise des quatre zones avec 0,5° de marge
  (2 005 polygones après subdivision, 3,6 Mo en base). `--sans-cache` lit le shapefile directement dans l'archive
  téléchargée, sans l'extraire, dans un dossier temporaire du conteneur effacé à la fin.
* **Zones de mouillage et zone de réception fiable** : calculées par l'API sur les 7 derniers jours (`--jours 7`) ;
  sans cette option, sur toutes les positions en base, ce qui coûte plus de mémoire et de fichiers temporaires à
  PostgreSQL. Les seuils (`stationary_zones`, `reception`) sont ceux de la calibration danoise, à recalibrer.
* **Coût mesuré** (image linux/amd64 émulée sur le Mac, base de test, 06/10/2026) : 61 s au total dont le
  téléchargement de 149 Mo ; **300 Mo de mémoire au plus** pendant une minute (539 Mo avant lecture économe du
  shapefile : le polygone de l'Eurasie compte plus d'un million de points) ; **142 Mo de disque au plus**,
  l'archive GSHHG seule, effacée à la fin. Sur le serveur, compter le temps de téléchargement en plus ; prévoir au
  moins 300 Mo de disque libre.
* À relancer de temps en temps (chaque semaine par exemple) pour les zones de mouillage et la réception ; le trait
  de côte ne change pas (`--skip-land` pour ne recalculer que les masques déduits de l'AIS).

## Construire sur le Mac et déployer

Le serveur ne construit rien : ni la place ni la mémoire. Les trois images (`mars_c2-backend`, `mars_c2-web`,
`mars_c2-scripts`, cette dernière commune à `collector`, `ingest` et `taches`) sont construites sur le Mac pour
linux/amd64, envoyées par `docker save` et `docker load`, puis démarrées sans construction.

**1. Sur le Mac**, depuis le dépôt à jour :

```bash
git pull origin main
touch .env                                         # la surcouche lit .env ; un fichier vide suffit pour construire
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose -f docker-compose.yml -f docker-compose.serveur.yml \
  --profile direct build backend web collector
docker image inspect mars_c2-backend mars_c2-web mars_c2-scripts --format '{{.RepoTags}} {{.Architecture}}'
```

Les trois doivent afficher `amd64`. Ces images remplacent sur le Mac celles du même nom : pour retrouver des images
natives en local, relancer ensuite `docker compose build backend web`.

Tailles mesurées le 06/10/2026 : `mars_c2-backend` 160 Mo, `mars_c2-web` 21 Mo, `mars_c2-scripts` 189 Mo ; flux
compressé de l'envoi : 325 Mo. Prévoir environ 400 Mo libres sur le serveur pour charger les trois images.

**2. Envoi en flux**, sans fichier intermédiaire ni sur le Mac ni sur le serveur (les couches communes ne passent
qu'une fois) :

```bash
docker save mars_c2-backend mars_c2-web mars_c2-scripts | gzip | ssh root@IP_DU_SERVEUR 'gunzip | docker load'
```

**3. Sur le serveur**, code et migrations à jour, puis démarrage sans construction :

```bash
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/1X_nom.sql   # chaque migration nouvelle
docker compose up -d --no-build
docker image prune -f                              # anciennes images devenues sans nom
```

Une nouvelle migration de `db/init` ne s'applique pas toute seule à une base existante (les scripts d'initialisation
ne jouent qu'à la création du volume) ; l'appliquer avant `up`, la nouvelle API pouvant en dépendre.

**Disque trop juste pour garder les anciennes et les nouvelles images.** `docker load` écrit les nouvelles couches
avant que les anciennes ne soient libérables, et une image utilisée par un conteneur, même arrêté, ne peut pas être
supprimée. Dans ce cas, libérer d'abord, dans cet ordre (la base et ses données ne sont pas touchées, la collecte
s'interrompt quelques minutes, sans conséquence sur les règles : les minutes de flux coupé ne comptent pas comme
des silences) :

```bash
cd /opt/mars_c2 && git pull origin main
docker compose rm -sf backend web collector ingest taches   # arrêt et suppression des conteneurs, pas des volumes
docker image rm mars_c2-backend mars_c2-web mars_c2-scripts  # libère la place des anciennes images
# Une seule fois, au passage aux noms d'images fixes : anciennes images nommées d'après chaque service
docker image rm mars_c2-collector mars_c2-ingest mars_c2-taches 2>/dev/null
docker image prune -f && df -h /                            # vérifier la place libre avant le chargement
```

puis, depuis le Mac, l'envoi en flux (étape 2), et sur le serveur :

```bash
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/1X_nom.sql   # migrations nouvelles
docker compose up -d --no-build
```

Ne jamais lancer `docker system prune --volumes` : il effacerait le volume de la base.

## Vérifier

```bash
docker compose ps                                   # six services « running »
docker compose logs --tail 5 collector              # une ligne par minute : messages, navires par zone
docker compose logs --tail 5 ingest                 # une ligne par cycle : positions lues, conservées, allégées
docker compose logs --tail 20 taches                # tâches de la nuit, alertes disque
curl -s localhost:8000/api/ingestion                # retard, disque, dernière exécution de chaque tâche
curl -s localhost:8000/api/clock                    # "live": true, "speed": 1.0
```

Ce qu'on doit lire :
* `/api/ingestion` : `retard_s` de l'ordre de la minute ; `alerte_disque` à `false` ; `disque` avec une mesure de
  moins de 30 minutes (`perimee` à `false`) ; dans `taches`, `archivage`, `purge`, `sauvegarde` et `regles` à `ok`
  (`regles` daté de moins de 5 minutes).
* Les règles, cycle par cycle, et les alertes produites :

```bash
docker compose exec taches python scripts/taches.py regles      # un cycle à la demande, détail complet
docker compose exec db psql -U mars -d mars -c "SELECT type, severity, status, count(*) FROM alerts
  WHERE detected_at > now() - interval '1 day' GROUP BY 1, 2, 3 ORDER BY 1, 2"
```

  Ce qu'on doit lire : pour `rendezvous` et `ais_gap`, des compteurs (et non « trait de côte absent » ou « zone de
  réception absente », signe que les masques n'ont pas été construits) ; `exclus_coupure_du_flux` non nul seulement
  après une interruption d'AISStream ; pour `watchlist`, autant de passages que de navires des listes présents.
* Le journal de l'archivage, chaque matin : `fichiers_non_ingeres` à 0, `fichiers_supprimes` égal au nombre de
  fichiers de la veille (environ 11 500 pour quatre zones), `octets_envoyes` de l'ordre de 25 Mo.
* Contrôle croisé en base :

```bash
docker compose exec db psql -U mars -d mars -c "SELECT day, kind, count(*) AS parties, sum(rows) AS lignes,
  pg_size_pretty(sum(bytes)) AS taille FROM archives GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT 8"
docker compose exec db psql -U mars -d mars -c "SELECT task, status, started_at, details FROM task_runs
  ORDER BY id DESC LIMIT 6"
```

Liste de surveillance en base :

```bash
docker compose exec db psql -U mars -d mars -c "SELECT v.name, v.mmsi, v.imo, v.flag, w.level, w.matched_by
  FROM vessel_watch w JOIN vessels v ON v.id = w.vessel_id ORDER BY w.rank"
```

## Restauration complète

La sauvegarde contient le schéma et toutes les tables sauf les données de `positions` et du registre
d'ingestion. Les positions se rechargent depuis l'archive R2 ; celles des fichiers encore sur le serveur (journée
en cours et veille non archivée) sont rechargées par l'ingestion elle même, puisque le registre restauré est vide.

```bash
cd /opt/mars_c2
docker compose stop backend ingest taches          # la collecte peut continuer : elle n'écrit que du Parquet

# 1. Récupérer la sauvegarde voulue (la plus récente en bas de liste)
docker compose run --rm taches python scripts/taches.py sauvegardes
docker compose run --rm -v /opt/mars_c2/restauration:/restauration taches \
  python scripts/taches.py telecharger sauvegardes/mars_AAAAMMJJTHHMMSS.dump /restauration/mars.dump

# 2. Recréer la base vide et y restaurer schéma et tables
docker compose exec db dropdb -U mars --maintenance-db=postgres mars
docker compose exec db createdb -U mars mars
docker compose exec -T db pg_restore -U mars -d mars --no-owner --exit-on-error < restauration/mars.dump

# 3. Positions des journées archivées, depuis R2 (au plus les 30 derniers jours : la purge retirerait les autres)
docker compose run --rm taches python scripts/taches.py restaurer-positions --du AAAA-MM-JJ --au AAAA-MM-JJ

# 4. Relance : l'ingestion recharge les fichiers encore présents sur le disque
docker compose up -d backend ingest taches
rm -r restauration
```

Le rechargement depuis l'archive a son propre allègement, dans l'ordre chronologique et toutes zones réunies :
l'ingestion en direct écarterait ces messages comme arrivés en retard. Il refuse une journée qui a déjà des
positions en base. Éprouvé le 06/10/2026 sur une base de test : 11 554 positions et 1 079 navires rechargés pour
le 05/10, exactement le résultat de l'ingestion d'origine.

## Consulter l'interface

Depuis le Mac : `ssh -N -L 8080:localhost:8080 root@IP_DU_SERVEUR`, puis http://localhost:8080. L'horloge est en
direct (vitesse 1) ; la frise permet de revenir sur une période passée (sélecteur de vitesse du rejeu), le bouton
« Direct » d'y revenir.

## Volumes à l'équilibre (débit du 06/10/2026)

Débit : environ 680 messages par minute pour les quatre zones, soit 979 000 par jour (76 % de positions, 24 % de
messages statiques, proportions de la collecte du 05/10), et 34 000 positions conservées par heure en base.

**Base** (30 jours de positions) :
* 34 000 × 24 × 30 = 24,5 millions de positions ;
* 282 octets par position index compris, mesurés sur 2 millions de positions : table 101, index spatial 66, index
  navire et instant 54, index sur l'instant 39, clé primaire 22 ;
* soit 6,9 Go, environ **7,5 Go** avec l'espace libéré par la purge quotidienne et les autres tables (navires,
  identités, registre d'ingestion, listes).

**Disque du serveur** : base 7,5 Go, images Docker et système environ 6 Go, Parquet local de moins de deux jours
environ 0,3 Go, journaux bornés à 0,2 Go : environ 14 Go sur 40, 65 % libres. **Tant que le disque fait 8 Go, il
se remplit** : la base croît de 230 Mo par jour jusqu'à l'équilibre, et l'alerte des 15 % se déclenchera en une à
deux semaines. Agrandir le disque avant.

**R2** :
* archive : 27,4 octets par position et 22,5 par message statique après compactage (mesuré sur l'archivage de
  test), soit environ 26 Mo par jour, 0,77 Go par mois, 9,4 Go par an ;
* sauvegardes sans positions : moins de 10 Mo chacune, moins de 70 Mo pour les 7 ;
* les 10 Go gratuits sont atteints après **environ 13 mois** de collecte. Au delà, 0,015 dollar par Go et par mois.

**Règles en continu** : un cycle sur 867 000 positions (24 heures au débit actuel), mesuré sur le Mac en émulation
linux/amd64, donc une borne haute : 1,4 s pour les coupures AIS, presque rien pour les listes et les identités, et
33 s pour les rendez vous dans un cas volontairement extrême (600 navires immobiles serrés, 5 700 épisodes) ;
181 Mo de mémoire au plus pour le processus, environ 100 Mo de fichiers temporaires PostgreSQL. Si les rendez vous
devenaient trop lents en vrai, la piste est d'écarter les positions proches des côtes avant de former les paires
(changement de sémantique léger, à décider avec la recalibration).

**Compression TimescaleDB** : pas utile à cette échelle. Avec 30 jours en base, 7,5 Go tiennent largement sur
40 Go ; la compression (de l'ordre de 10 fois) demanderait de changer l'image de la base, de refaire la clé de
`positions` et de transformer la purge, pour un gain d'espace dont on n'a pas besoin. Elle deviendrait intéressante
si l'on voulait garder 6 à 12 mois en base. Levier plus simple si l'espace manquait : l'index spatial de
`positions` (23 % de la taille) n'est utile qu'aux analyses radar, qui filtrent d'abord sur un quart d'heure.
