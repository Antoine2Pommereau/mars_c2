# Déploiement sur le serveur Scaleway (DEV1-S)

Le serveur fait tourner en permanence, avec Docker Compose : la base (`db`), l'API (`backend`), l'interface
(`web`), la collecte AISStream (`collector`) et l'ingestion en base (`ingest`). Le service d'inférence radar reste
sur le Mac (GPU Apple) ; sur le serveur, l'API le signale « injoignable », ce qui est attendu à cette étape.

Rien n'est ouvert sur Internet hormis SSH : la base n'a aucun port publié, l'API et l'interface écoutent sur
127.0.0.1 et se consultent par un tunnel SSH. C'est le rôle de `docker-compose.serveur.yml`.

## Première installation

Sur le serveur (Ubuntu 24.04, utilisateur `root` par défaut chez Scaleway) :

```bash
# Docker et Docker Compose (2.24.4 au moins, pour les étiquettes !reset et !override)
curl -fsSL https://get.docker.com | sh
docker compose version

# 2 Go de mémoire d'échange : la DEV1-S n'a que 2 Go de mémoire, et la construction de l'interface en demande
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Dépôt, sur la branche du jalon (main après fusion)
git clone https://github.com/Antoine2Pommereau/mars_c2.git && cd mars_c2
git checkout direct-ingestion
mkdir -p data/ais_live data/listes
```

Fichier `.env` du serveur (jamais versionné) :

```bash
cat > .env <<'EOF'
AISSTREAM_API_KEY=ta_cle
COMPOSE_FILE=docker-compose.yml:docker-compose.serveur.yml
COMPOSE_PROFILES=direct
EOF
```

`COMPOSE_FILE` et `COMPOSE_PROFILES` font qu'un simple `docker compose up -d` applique la surcouche du serveur et
démarre la collecte et l'ingestion (profil `direct`, inactif sur le Mac pour ne pas ouvrir deux collectes avec la
même clé).

Depuis le Mac, copier les listes de surveillance (et, si on veut garder l'historique déjà collecté, l'archive) :

```bash
scp data/listes/Vessels1.db data/listes/maritime.csv root@IP_DU_SERVEUR:mars_c2/data/listes/
rsync -av data/ais_live/ root@IP_DU_SERVEUR:mars_c2/data/ais_live/      # facultatif : rattrapage au démarrage
```

Puis, sur le serveur :

```bash
docker compose up -d --build
docker compose run --rm ingest python scripts/import_watchlist.py
```

Arrêter ensuite la collecte sur le Mac si elle tourne : une seule collecte, celle du serveur.

## Mise à jour

```bash
cd mars_c2 && git pull
docker compose up -d --build
```

Une nouvelle migration de `db/init` ne s'applique pas toute seule à une base existante (les scripts d'initialisation
ne jouent qu'à la création du volume) : `docker compose exec -T db psql -U mars -d mars < db/init/1X_nom.sql`.

## Vérifier

```bash
docker compose ps                                   # cinq services « running »
docker compose logs --tail 5 collector              # une ligne par minute : messages, navires par zone
docker compose logs --tail 5 ingest                 # une ligne par cycle : positions lues, conservées, allégées
curl -s localhost:8000/api/ingestion                # dernier fichier chargé, retard, volume de la dernière heure
curl -s localhost:8000/api/clock                    # "live": true
```

Ce qu'on doit lire : dans `/api/ingestion`, `retard_s` de l'ordre de la minute à deux minutes (une écriture Parquet
par minute, une lecture toutes les 15 secondes) ; environ la moitié des positions conservées sur la collecte de
nuit du 05/10 (21 197 lues, 11 597 conservées), davantage allégées dans les zones denses.

Liste de surveillance en base :

```bash
docker compose exec db psql -U mars -d mars -c "SELECT v.name, v.mmsi, v.imo, v.flag, w.level, w.matched_by
  FROM vessel_watch w JOIN vessels v ON v.id = w.vessel_id ORDER BY w.rank"
```

## Consulter l'interface

Depuis le Mac :

```bash
ssh -N -L 8080:localhost:8080 root@IP_DU_SERVEUR
```

puis http://localhost:8080 (laisser le tunnel ouvert). L'horloge est en direct ; la frise permet de revenir sur une
période passée, et le bouton « Direct » d'y revenir.

## Volumes et limites

* Collecte de nuit du 05/10 (deux zones, deux heures) : 21 197 positions, 1 088 navires. En base, 272 octets par
  position index compris. À raison de 250 000 positions conservées par jour pour les quatre zones (estimation à
  confirmer de jour), compter environ 70 Mo par jour en base, soit 6 Go en trois mois sur les 20 Go du disque. Le
  passage à TimescaleDB (compression au delà de 30 jours) et l'archive Parquet sur stockage objet sont les étapes
  suivantes du socle.
* La collecte écrit un fichier Parquet par minute, par zone et par type de message : environ 11 000 petits fichiers
  par jour. Un compactage quotidien sera utile avant l'envoi sur stockage objet.
