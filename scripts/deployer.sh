#!/usr/bin/env bash
# Déploiement d'une version publiée (scripts/publier.sh) sur le serveur, depuis /opt/mars_c2 (lot Z2,
# docs/exploitation.md). Toutes les gardes apprises depuis le 06/10/2026, dans l'ordre :
#   1. refus hors de /opt/mars_c2, ou si le script est sourcé (il ne fait jamais « exit » d'un shell interactif) ;
#   2. dépôt du serveur à la même empreinte que la version (fichiers Compose, migrations, règles) ;
#   3. au moins 3 Go libres ; images téléchargées à l'empreinte demandée (jamais latest), une à une, étiquette vérifiée ;
#   4. migrations non appliquées (table schema_migrations) listées : arrêt, sauf --migrations, qui fait une sauvegarde
#      puis les applique dans l'ordre, chacune dans une transaction, et inscrit l'empreinte SHA 256 de chaque fichier ;
#   5. docker compose up -d --no-build pour tous les services, attente de leur santé ;
#   6. tableau des versions : image de chaque conteneur et signe de vie (signes_de_vie), ÉCART en clair ;
#   7. déploiement consigné dans task_runs (tâche deploiement, tracée), anciennes images libérées.
#
# Usage : bash scripts/deployer.sh <empreinte> [--migrations]
#         bash scripts/deployer.sh --verifier      (tableau des versions seulement, rien n'est changé)
# Code de sortie : 0 si tout est à la version, 1 sur un échec ou un écart, 2 si des migrations attendent --migrations.

if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  echo "deployer.sh s'exécute (bash scripts/deployer.sh …), il ne se source pas"
  return 1 2>/dev/null
fi
set -euo pipefail
# MARS_DEPLOYER_ESSAI : racine d'une répétition sur le Mac (copie du dépôt, faux docker) ; jamais sur le serveur
cd "${MARS_DEPLOYER_ESSAI:-/opt/mars_c2}" 2>/dev/null || { echo "PAS SUR LE SERVEUR : arrêt"; exit 1; }
[ "$(cd "$(dirname "$0")/.." && pwd -P)" = "$(pwd -P)" ] || { echo "deployer.sh doit être celui de /opt/mars_c2"; exit 1; }

R=ghcr.io/antoine2pommereau
IMAGES=(backend scripts web)
echec() { echo "ÉCHEC   $*"; exit 1; }
psql_mars() { docker compose exec -T db psql -U mars -d mars -v ON_ERROR_STOP=1 -tA "$@"; }

TAG="" MIGRATIONS=0 VERIFIER=0
for a in "$@"; do
  case "$a" in
    --migrations) MIGRATIONS=1 ;;
    --verifier) VERIFIER=1 ;;
    *) TAG=$a ;;
  esac
done

# Tableau des versions : image de chaque conteneur (étiquette du commit) et signe de vie du service
# Service Compose, image MARS C2 qu'il exécute, nom de son signe de vie (vide : aucun)
declare -A SIGNE=([backend]=api [collector]=collecte [ingest]=ingestion [taches]=taches [web]="")
tableau() {
  local attendu=$1 ecarts=0 s cid v signe vsigne age
  declare -A SV=() SA=()
  while IFS='|' read -r s v age; do [ -n "$s" ] && SV[$s]=$v && SA[$s]=$age; done < <(psql_mars -c \
    "SELECT service, coalesce(detail->>'version', ''), round(extract(epoch FROM now() - dernier_le))::int FROM signes_de_vie" 2>/dev/null || true)
  printf '%-10s %-12s %-12s %-16s %s\n' SERVICE IMAGE SIGNE "SIGNE DE VIE" VERDICT
  for s in "${!SIGNE[@]}"; do
    cid=$(docker compose ps -q "$s" 2>/dev/null | head -1)
    if [ -z "$cid" ]; then printf '%-10s %-12s %-12s %-16s %s\n' "$s" "·" "·" "·" "ARRÊTÉ"; ecarts=$((ecarts + 1)); continue; fi
    v=$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$(docker inspect -f '{{.Image}}' "$cid")")
    signe=${SIGNE[$s]}; vsigne="(aucun)"; age=""
    if [ -n "$signe" ]; then vsigne=${SV[$signe]:-·}; age=${SA[$signe]:-}; fi
    if [ "${v:0:7}" = "${attendu:0:7}" ] && { [ -z "$signe" ] || [ "${vsigne:0:7}" = "${attendu:0:7}" ]; }; then
      verdict=OK
    else
      verdict="ÉCART : attendue ${attendu:0:7}"; ecarts=$((ecarts + 1))
    fi
    printf '%-10s %-12s %-12s %-16s %s\n' "$s" "${v:0:7}" "${vsigne:0:7}" "${age:+il y a ${age} s}" "$verdict"
  done
  return $ecarts
}

if [ "$VERIFIER" = 1 ]; then
  attendu=${TAG:-$(git rev-parse --short HEAD)}
  echo "Versions en service, attendue $attendu :"
  tableau "$attendu" && exit 0 || exit 1
fi

[[ "$TAG" =~ ^[0-9a-f]{7,40}$ ]] || echec "empreinte attendue (7 caractères hexadécimaux au moins) : bash scripts/deployer.sh <empreinte> [--migrations]"
TAG=${TAG:0:7}
DEBUT=$(date -u +%s)

# 2. Dépôt du serveur à la même version (Compose, migrations, règles)
[ "$(git rev-parse --short=7 HEAD)" = "$TAG" ] \
  || echec "dépôt du serveur à $(git rev-parse --short=7 HEAD), version demandée $TAG : git pull d'abord"
[ -z "$(git status --porcelain --untracked-files=no)" ] || { git status --short; echec "fichiers suivis modifiés sur le serveur"; }

# 3. Disque, puis images à l'empreinte, une à une
libre=$(df --output=avail -B1 / | tail -1)
[ "$libre" -ge 3000000000 ] || echec "moins de 3 Go libres ($((libre / 1000000)) Mo) : docker image prune -af d'abord"
for i in "${IMAGES[@]}"; do
  docker pull -q "$R/mars_c2-${i}:$TAG" >/dev/null || echec "image $i:$TAG introuvable dans le registre (publiée ?)"
  v=$(docker image inspect "$R/mars_c2-${i}:$TAG" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  [ "$v" = "$TAG" ] || echec "image $i:$TAG porte l'empreinte $v"
  echo "OK      image $i:$TAG téléchargée"
done

# 4. Migrations
empreinte() { sha256sum "$1" | cut -c1-64; }
en_attente() {
  if [ "$(psql_mars -c "SELECT to_regclass('public.schema_migrations') IS NOT NULL")" != t ]; then
    ls db/init/*.sql | xargs -n1 basename | awk -F_ '$1 >= 39'     # registre absent : 39 le crée et détecte 01 à 38
  else
    comm -23 <(ls db/init/*.sql | xargs -n1 basename | sort) <(psql_mars -c "SELECT nom FROM schema_migrations" | sort)
  fi
}
appliquees=()
attente=$(en_attente)
if [ -n "$attente" ]; then
  echo "Migrations non appliquées :"; echo "$attente" | sed 's/^/  /'
  [ "$MIGRATIONS" = 1 ] || { echo "ARRÊT   relancer avec --migrations pour les appliquer (sauvegarde d'abord)"; exit 2; }
  docker compose exec -T taches python scripts/taches.py sauvegarder >/dev/null || echec "sauvegarde avant migration"
  echo "OK      sauvegarde faite avant les migrations"
  while [ -n "$attente" ]; do
    f=$(echo "$attente" | head -1)
    psql_mars --single-transaction -q < "db/init/$f" >/dev/null || echec "migration $f (transaction annulée)"
    psql_mars -c "INSERT INTO schema_migrations (nom, empreinte) VALUES ('$f', '$(empreinte "db/init/$f")')
                  ON CONFLICT (nom) DO UPDATE SET empreinte = EXCLUDED.empreinte" >/dev/null
    echo "OK      migration $f appliquée"
    appliquees+=("$f")
    attente=$(en_attente)
    if echo "$attente" | grep -qx "$f"; then echec "migration $f toujours en attente après application"; fi
  done
fi
# Empreinte des migrations détectées (appliquées avant le registre) ; fichier modifié après application signalé
for p in db/init/*.sql; do
  f=$(basename "$p"); e=$(empreinte "$p")
  psql_mars -c "UPDATE schema_migrations SET empreinte = '$e' WHERE nom = '$f' AND empreinte IS NULL" >/dev/null
  enreg=$(psql_mars -c "SELECT empreinte FROM schema_migrations WHERE nom = '$f'")
  [ -z "$enreg" ] || [ "$enreg" = "$e" ] || echo "ATTENTION $f modifié depuis son application (empreinte différente)"
done

# 5. Tous les services à la version, santé attendue
export MARS_TAG=$TAG
docker compose up -d --no-build --remove-orphans || echec "docker compose up"
pret=0
for _ in $(seq 1 60); do
  # En marche, et sain pour ceux qui ont un contrôle de santé (colonne vide sinon)
  if docker compose ps -a --format '{{.State}} {{.Health}}' \
       | awk '$1 != "running" || ($2 != "" && $2 != "healthy") { mauvais = 1 } END { exit mauvais }'; then
    pret=1; break
  fi
  sleep 5
done
[ "$pret" = 1 ] || { docker compose ps; echec "services pas tous en marche et sains après 5 minutes"; }
echo "OK      tous les services en marche et sains"

# 6. Versions : on laisse aux services le temps d'écrire leur signe de vie (taches, collecte : une minute)
for _ in $(seq 1 18); do
  tableau "$TAG" >/dev/null 2>&1 && break
  sleep 10
done
ecarts=0
tableau "$TAG" || ecarts=$?

# 7. Consigné dans task_runs (tracé), anciennes images libérées
verdict=$([ "$ecarts" = 0 ] && echo ok || echo ecart)
liste=""
for f in "${appliquees[@]+"${appliquees[@]}"}"; do liste="$liste${liste:+, }\"$f\""; done
DEPLOIEMENT="{\"version\": \"$TAG\", \"verdict\": \"$verdict\", \"ecarts\": $ecarts, \"duree_s\": $(( $(date -u +%s) - DEBUT )), \"migrations\": [$liste]}"
docker compose exec -T -e DEPLOIEMENT="$DEPLOIEMENT" taches python scripts/taches.py deploiement >/dev/null \
  || echo "ATTENTION déploiement non consigné dans task_runs"
docker image prune -af >/dev/null 2>&1 || true
if [ "$ecarts" = 0 ]; then
  echo "DÉPLOYÉ $TAG ($(( $(date -u +%s) - DEBUT )) s ; à la prochaine commande docker compose à la main : MARS_TAG=$TAG)"
else
  echec "$ecarts service(s) pas à la version $TAG (tableau ci dessus)"
fi
