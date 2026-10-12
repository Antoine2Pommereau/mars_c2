#!/usr/bin/env bash
# Vérification des images construites, sur le Mac, avant chaque publication (docs/deploiement.md).
#
# Le 11/10/2026, l'API a planté en boucle au démarrage sur le serveur : backend/sentinel.py n'était pas copié par
# backend/Dockerfile, alors que les tests passaient depuis le dépôt. Ce script démarre chaque image construite, comme
# le serveur le fera, et vérifie qu'elle charge son point d'entrée :
#   backend : l'API démarre contre une base jetable et /api/health répond ;
#   scripts : chaque point d'entrée (taches, collecte, ingestion, listes, masques, mesures) s'importe, et
#             taches.py --help répond ;
#   web     : nginx démarre et sert la page.
#
# Usage : scripts/verifier_images.sh [backend] [scripts] [web]   (sans argument : les trois)
#         MARS_TAG=<empreinte> scripts/verifier_images.sh scripts   (étiquette vérifiée, latest par défaut)
# Code de sortie non nul au premier échec ; rien n'est publié par ce script.
set -euo pipefail

R=ghcr.io/antoine2pommereau
TAG=${MARS_TAG:-latest}
PLATEFORME=linux/amd64
CIBLES=("$@")
[ ${#CIBLES[@]} -eq 0 ] && CIBLES=(backend scripts web)
RESEAU=mars_verif_$$
BASE=mars_verif_db_$$
API=mars_verif_api_$$
WEB=mars_verif_web_$$

nettoyer() {
  docker rm -f "$API" "$WEB" "$BASE" >/dev/null 2>&1 || true
  docker network rm "$RESEAU" >/dev/null 2>&1 || true
}
trap nettoyer EXIT

ok()    { echo "OK      $*"; }
echec() { echo "ÉCHEC   $*"; exit 1; }

verifier_backend() {
  local image="$R/mars_c2-backend:$TAG"
  docker image inspect "$image" >/dev/null 2>&1 || echec "$image absente : la construire d'abord"
  docker network create "$RESEAU" >/dev/null
  docker run -d --name "$BASE" --network "$RESEAU" -e POSTGRES_DB=mars -e POSTGRES_USER=mars \
    -e POSTGRES_PASSWORD=mars postgis/postgis:16-3.4 >/dev/null
  for _ in $(seq 1 60); do
    docker exec "$BASE" pg_isready -U mars -d mars >/dev/null 2>&1 && break
    sleep 2
  done
  docker run -d --name "$API" --platform "$PLATEFORME" --network "$RESEAU" -p 18765:8000 \
    -e DATABASE_URL="postgresql://mars:mars@$BASE:5432/mars" "$image" >/dev/null
  for _ in $(seq 1 90); do                   # émulation amd64 sur le Mac : démarrage lent
    if curl -sf -m 3 http://localhost:18765/api/health >/dev/null 2>&1; then
      ok "backend : l'API démarre et /api/health répond ($(curl -s -m 3 http://localhost:18765/api/health))"
      return
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$API" 2>/dev/null)" != "true" ]; then
      docker logs --tail 30 "$API"
      echec "backend : l'API s'est arrêtée au démarrage (journal ci dessus)"
    fi
    sleep 2
  done
  docker logs --tail 30 "$API"
  echec "backend : /api/health ne répond pas après 3 minutes"
}

verifier_scripts() {
  local image="$R/mars_c2-scripts:$TAG"
  docker image inspect "$image" >/dev/null 2>&1 || echec "$image absente : la construire d'abord"
  docker run --rm --platform "$PLATEFORME" "$image" python -c "
import sys
sys.path.insert(0, 'scripts')
import taches, ais_live, ais_ingest, import_watchlist, build_masks, mesures_calibration
from mars import sentinel, diagnostic, travailleurs, viirs, rules, archive, preuves, evaluation
from mars.fusion import pipeline
print('modules chargés')
" || echec "scripts : un point d'entrée ne s'importe pas (trace ci dessus)"
  docker run --rm --platform "$PLATEFORME" "$image" python scripts/taches.py --help >/dev/null \
    || echec "scripts : taches.py --help en erreur"
  ok "scripts : points d'entrée chargés, taches.py répond"
}

verifier_web() {
  local image="$R/mars_c2-web:$TAG"
  docker image inspect "$image" >/dev/null 2>&1 || echec "$image absente : la construire d'abord"
  # nginx résout le nom « backend » au démarrage : il est fourni ici, sans API derrière
  docker run -d --name "$WEB" --platform "$PLATEFORME" --add-host backend:127.0.0.1 -p 18766:80 "$image" >/dev/null
  for _ in $(seq 1 30); do
    if curl -sf -m 3 http://localhost:18766/ | grep -q '<div id="root">'; then
      ok "web : nginx sert la page"
      return
    fi
    sleep 1
  done
  docker logs --tail 30 "$WEB"
  echec "web : la page n'est pas servie"
}

for cible in "${CIBLES[@]}"; do
  case "$cible" in
    backend) verifier_backend ;;
    scripts) verifier_scripts ;;
    web)     verifier_web ;;
    *)       echec "cible inconnue : $cible (backend, scripts ou web)" ;;
  esac
done
echo "Images vérifiées : ${CIBLES[*]} ($TAG). Publication possible."
