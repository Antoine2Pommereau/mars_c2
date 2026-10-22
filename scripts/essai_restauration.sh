#!/usr/bin/env bash
# Essai de restauration dans un conteneur PostGIS jetable (lot Z2), sur le serveur, depuis /opt/mars_c2.
#
# Le conteneur jetable : image de la base en service (déjà présente, rien n'est téléchargé), 512 Mo de mémoire, aucun
# port publié, données sur un volume temporaire en mémoire (tmpfs), mot de passe aléatoire ; il rejoint seulement le
# réseau Docker de MARS C2. taches.py essai-restauration y restaure la dernière sauvegarde de R2 et compare les comptes
# des tables clés à la base en service (résultat dans task_runs, tâche essai_restauration, tracée). Le conteneur est
# détruit dans tous les cas. La tâche mensuelle de taches fait le même essai sans conteneur (base temporaire), car
# taches n'a pas le socket Docker.
#
# Usage : bash scripts/essai_restauration.sh      (code de sortie non nul si l'essai échoue)
set -euo pipefail
cd /opt/mars_c2 2>/dev/null || { echo "PAS SUR LE SERVEUR : arrêt"; exit 1; }

NOM=mars_essai_restauration_$$
nettoyer() { docker rm -f "$NOM" >/dev/null 2>&1 || true; }
trap nettoyer EXIT

libre=$(df --output=avail -B1 / | tail -1)
[ "$libre" -ge 3000000000 ] || { echo "ÉCHEC   moins de 3 Go libres ($((libre / 1000000)) Mo) : essai refusé"; exit 1; }

db=$(docker compose ps -q db)
[ -n "$db" ] || { echo "ÉCHEC   base en service introuvable (docker compose ps db)"; exit 1; }
image=$(docker inspect -f '{{.Config.Image}}' "$db")
reseau=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$db" | awk '{print $1}')
mdp=$(openssl rand -hex 16)

docker run -d --name "$NOM" --network "$reseau" --memory 512m --tmpfs /var/lib/postgresql/data:size=2g \
  -e POSTGRES_PASSWORD="$mdp" -e POSTGRES_DB=essai "$image" >/dev/null
for _ in $(seq 1 60); do
  docker exec "$NOM" pg_isready -h 127.0.0.1 -U postgres -d essai >/dev/null 2>&1 && break
  sleep 2
done
docker exec "$NOM" pg_isready -h 127.0.0.1 -U postgres -d essai >/dev/null || { echo "ÉCHEC   la base jetable ne démarre pas"; exit 1; }

docker compose exec -T -e ESSAI_CIBLE="postgresql://postgres:${mdp}@${NOM}:5432/essai" taches \
  python scripts/taches.py essai-restauration
echo "OK      essai de restauration réussi ; conteneur jetable détruit"
