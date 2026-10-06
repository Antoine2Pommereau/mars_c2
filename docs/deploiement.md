# Déploiement sur le serveur Scaleway (DEV1-S)

Le serveur (`/opt/mars_c2`) fait tourner en permanence, avec Docker Compose, six services : la base (`db`),
l'API (`backend`), l'interface (`web`), la collecte AISStream (`collector`), l'ingestion en base (`ingest`) et les
tâches planifiées (`taches` : archivage sur R2, purge, sauvegarde, surveillance du disque). Pas de cron sur l'hôte :
tout redémarre avec la plateforme (`restart: unless-stopped`). Le service d'inférence radar reste sur le Mac (GPU
Apple) ; sur le serveur, l'API le signale « injoignable », ce qui est attendu.

Rien n'est ouvert sur Internet hormis SSH : la base n'a aucun port publié, l'API et l'interface écoutent sur
127.0.0.1 et se consultent par un tunnel SSH. C'est le rôle de `docker-compose.serveur.yml`.

## Première installation

Sur le serveur (Ubuntu 24.04, `root`) :

```bash
# Docker et Docker Compose (2.24.4 au moins, pour les étiquettes !reset et !override)
curl -fsSL https://get.docker.com | sh
docker compose version

# Mémoire d'échange : la DEV1-S n'a que 2 Go de mémoire, et la construction de l'interface en demande
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
R2_ENDPOINT=https://<identifiant_du_compte>.r2.cloudflarestorage.com
R2_BUCKET=mars-c2
```

`COMPOSE_FILE` et `COMPOSE_PROFILES` font qu'un simple `docker compose up -d` applique la surcouche du serveur et
démarre collecte, ingestion et tâches (profil `direct` inactif sur le Mac : une seule collecte par clé).

## Configurer Cloudflare R2

1. Tableau de bord Cloudflare, R2, « Create bucket » : nom `mars-c2`, emplacement automatique (Europe de
   préférence). Ne pas activer d'accès public.
2. R2, « Manage R2 API Tokens », « Create API token » : permission **Object Read & Write**, limitée au seau
   `mars-c2`. Noter l'identifiant de clé, le secret et le point d'accès S3
   (`https://<identifiant_du_compte>.r2.cloudflarestorage.com`) ; les reporter dans `.env`.
3. Ne pas mettre de règle de cycle de vie sur le seau : la suppression des anciennes sauvegardes est faite par la
   tâche elle même, après vérification de la nouvelle.

Organisation du seau :

| Préfixe | Contenu |
|---|---|
| `ais/positions/zone=…/date=AAAA-MM-JJ/` | Positions brutes d'une journée et d'une zone, un fichier Parquet par passage d'archivage (zstd, trié par navire puis par instant, colonnes identiques à la collecte) |
| `ais/statiques/zone=…/date=AAAA-MM-JJ/` | Messages statiques, même organisation |
| `sauvegardes/mars_AAAAMMJJTHHMMSS.dump` | Sauvegardes de la base (`pg_dump` au format personnalisé), les 7 plus récentes |

## Tâches planifiées (service `taches`)

Chaque nuit à 02:30 UTC (`TACHES_HEURE`), dans cet ordre, chacune consignée dans la table `task_runs` :

1. **Archivage** des journées terminées (veille ou avant, 30 minutes après minuit) : par dossier zone et type,
   les petits fichiers Parquet **déjà ingérés** sont regroupés en un fichier, envoyé sur R2 avec son MD5 (R2 refuse
   un contenu altéré), puis relu (taille et empreinte). L'archive est inscrite dans la table `archives` avec la
   liste des fichiers d'origine ; seuls ces fichiers, ingérés et confirmés, sont supprimés du serveur. Un fichier pas
   encore ingéré reste en place (signalé dans le journal) et sera archivé plus tard dans une seconde partie. Après un
   arrêt entre l'envoi et la suppression, la tâche supprime sans renvoyer : pas de doublon sur R2.
2. **Purge** des positions de plus de 30 jours (`CONSERVATION_JOURS`), journée par journée, **seulement si la
   journée est archivée** ; retrait de la journée de `ais_days` et du registre d'ingestion des dossiers disparus.
3. **Sauvegarde** : `pg_dump` compressé envoyé en flux sur R2, sans fichier local, **sans les données de
   `positions`** (dans l'archive) ni du registre d'ingestion. Vérifiée par le code de sortie et la taille relue sur
   R2 ; une sauvegarde invalide est supprimée. Les 7 dernières sont gardées (`SAUVEGARDES_GARDEES`), les plus
   anciennes supprimées seulement après la vérification de la nouvelle.

Toutes les 10 minutes : espace libre du disque. Sous 15 % (`ALERTE_DISQUE_PCT`), une ligne `ALERTE DISQUE` dans
le journal du service, et `"alerte_disque": true` dans `/api/ingestion`.

Une tâche réussie ne rejoue pas le même jour ; une tâche en échec est retentée une heure plus tard. Au premier
démarrage dans la journée, après 02:30, les trois tâches s'exécutent tout de suite. Commandes manuelles :

```bash
docker compose exec taches python scripts/taches.py archiver      # ou purger, sauvegarder, disque
docker compose exec taches python scripts/taches.py sauvegardes   # liste des sauvegardes sur R2
```

## Mise à jour

```bash
cd /opt/mars_c2 && git pull origin main
docker compose up -d --build
```

Une nouvelle migration de `db/init` ne s'applique pas toute seule à une base existante (les scripts d'initialisation
ne jouent qu'à la création du volume) : `docker compose exec -T db psql -U mars -d mars < db/init/1X_nom.sql`.

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
  moins de 30 minutes (`perimee` à `false`) ; dans `taches`, `archivage`, `purge` et `sauvegarde` à `ok`.
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

**Compression TimescaleDB** : pas utile à cette échelle. Avec 30 jours en base, 7,5 Go tiennent largement sur
40 Go ; la compression (de l'ordre de 10 fois) demanderait de changer l'image de la base, de refaire la clé de
`positions` et de transformer la purge, pour un gain d'espace dont on n'a pas besoin. Elle deviendrait intéressante
si l'on voulait garder 6 à 12 mois en base. Levier plus simple si l'espace manquait : l'index spatial de
`positions` (23 % de la taille) n'est utile qu'aux analyses radar, qui filtrent d'abord sur un quart d'heure.
