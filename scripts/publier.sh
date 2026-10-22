#!/usr/bin/env bash
# Publication des trois images de MARS C2, sur le Mac (lot Z2, docs/exploitation.md). Remplace les blocs collés à la
# main : toutes les gardes apprises depuis le 06/10/2026 sont ici, dans l'ordre.
#   1. refus si l'arbre Git n'est pas propre, ou pas sur main égale à origin/main ;
#   2. construction des trois images en linux/amd64, avec MARS_VERSION = empreinte courte du commit ;
#   3. vérification de l'étiquette org.opencontainers.image.revision de chaque image : arrêt à la moindre différence ;
#   4. scripts/verifier_images.sh backend scripts web controle sante ;
#   5. envoi de latest et de l'empreinte de chaque image ; « PUBLIÉ <empreinte> » seulement si tout a réussi.
# Rien n'est poussé avant que tout soit vérifié. Code de sortie non nul au premier échec.
#
# Usage : bash scripts/publier.sh      (depuis la racine du dépôt, après git pull)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

R=ghcr.io/antoine2pommereau
IMAGES=(backend scripts web)
echec() { echo "ÉCHEC   $*"; exit 1; }

# 1. Arbre propre, sur main, à jour avec origin/main
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || echec "pas sur main (branche $(git rev-parse --abbrev-ref HEAD))"
[ -z "$(git status --porcelain)" ] || { git status --short; echec "arbre Git non propre : valider ou ranger d'abord"; }
git fetch -q origin main || echec "origin injoignable"
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
  || echec "main ($(git rev-parse --short HEAD)) différente d'origin/main ($(git rev-parse --short origin/main)) : git pull ou git push d'abord"
TAG=$(git rev-parse --short HEAD)
echo "Publication de $TAG : $(git log --oneline -1 | cut -c1-100)"

# 2. Construction
construire() {
  case "$1" in
    backend) docker build --platform linux/amd64 -f backend/Dockerfile --build-arg MARS_VERSION="$TAG" -t "$R/mars_c2-backend:latest" . ;;
    scripts) docker build --platform linux/amd64 -f scripts/Dockerfile --build-arg MARS_VERSION="$TAG" -t "$R/mars_c2-scripts:latest" . ;;
    web)     docker build --platform linux/amd64 --build-arg MARS_VERSION="$TAG" -t "$R/mars_c2-web:latest" web ;;
  esac
}
for i in "${IMAGES[@]}"; do
  construire "$i" || echec "construction de $i"
done

# 3. Empreinte de chaque image : la moindre différence arrête tout, rien n'est poussé
OK=1
for i in "${IMAGES[@]}"; do
  v=$(docker image inspect "$R/mars_c2-${i}:latest" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  if [ "$v" = "$TAG" ]; then echo "OK      $i : $v"; else echo "ÉCART   $i : $v, attendue $TAG"; OK=0; fi
done
[ "$OK" = 1 ] || echec "empreinte d'image différente du commit : rien n'est publié"

# 4. Vérification des images construites
MARS_TAG=latest scripts/verifier_images.sh backend scripts web controle sante || echec "verifier_images.sh : rien n'est publié"

# 5. Envoi : latest et l'empreinte, image par image
for i in "${IMAGES[@]}"; do
  docker tag "$R/mars_c2-${i}:latest" "$R/mars_c2-${i}:$TAG"
  docker push -q "$R/mars_c2-${i}:$TAG" >/dev/null || echec "envoi de $i:$TAG"
  docker push -q "$R/mars_c2-${i}:latest" >/dev/null || echec "envoi de $i:latest"
  echo "OK      $i : $TAG et latest envoyées"
done
echo "PUBLIÉ $TAG"
echo "Sur le serveur : ( cd /opt/mars_c2 || { echo \"PAS SUR LE SERVEUR : arrêt\"; exit 1; } ; git pull -q && bash scripts/deployer.sh $TAG --migrations )"
