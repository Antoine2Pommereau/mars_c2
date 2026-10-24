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
# Facultatif, jamais par défaut (image publique d'AI2 de 5 Go, téléchargée au besoin) :
#   ai2         : le script du travailleur Sentinel importe la chaîne d'AI2 dans la vraie image épinglée
#                 (config/rules.yaml), en linux/amd64, sans GPU (une minute) ;
#   ai2-complet : analyse complète sur CPU d'une scène synthétique, quatre cibles à retrouver (environ 7 minutes).
#   Ajouté après le second essai réel du 14/10/2026 (« No module named 'src' »).
#   fenetres    : lecture par fenêtres (lot C3) dans l'image d'AI2 : faux S3 qui vérifie la signature AWS version 4 de
#                 GDAL et compte les octets, scène synthétique géolocalisée par points d'appui ; part lue, points
#                 d'appui recalés, signature fausse refusée (environ une minute) ;
#   ai2-fenetres: chaîne d'AI2 sur une scène entière puis sur une fenêtre : mêmes détections au même endroit
#                 (environ 15 minutes sur CPU).
#   traces      : une trace complète de bout en bout arrive dans Tempo (étape D2) : base, Tempo et collecteur réels,
#                 API et nginx des images construites ; lancement d'un travailleur simulé côté taches, étapes du script
#                 de démarrage et vrai script Sentinel sous Python 3.8 (comme l'image d'AI2), envoyés par nginx avec le
#                 jeton (un jeton faux est refusé), résultats traités et instance « détruite » ; la trace doit réunir
#                 taches, travailleur et API. Vérifie aussi le filtre des données sensibles du collecteur (environ
#                 3 minutes ; images publiques du collecteur et de Tempo téléchargées au besoin).
#   s2          : travailleur Sentinel 2 (travailleurs/sentinel2.py) dans l'image rslearn d'AI2 (7,2 Go, poids publics
#                 de 2,2 Go gardés dans ~/.cache/mars_c2/sentinel2), sur une vraie fenêtre de 24 km de la Manche lue
#                 sans clé dans le miroir public de Google (13 bandes L1C et masque SCL du L2A), sur processeur :
#                 quatre navires, masque présent, vignettes de 2 et 10 km (environ 3 minutes en émulation).
#   s2-s3       : même essai par le chemin S3 de production (obligatoire avant publication) : adresses s3://eodata/…,
#                 paire de clés factice remise comme par le serveur, faux S3 local (travailleurs/faux_s3.py) qui
#                 vérifie la signature AWS version 4 de chaque requête et relaie les plages d'octets vers le miroir ;
#                 SCL, 13 bandes, inférence et vignettes ; puis voie sans boto3, signature fausse refusée, aucune clé
#                 dans le résultat, les spans, l'environnement ou la sortie. Ajouté après l'essai réel du 20/10/2026
#                 (EnvError de rasterio sur les clés AWS en options GDAL, que la cible s2, sans clé, ne voyait pas).
#   controle    : connexion d'un opérateur puis une écriture du panneau à travers le vrai nginx de l'image web, avec un
#                 en tête Origin de navigateur ; cookie sans Secure en http ; une autre origine refusée (lot A, environ
#                 une minute ; « Origine refusée » sur le serveur le 21/10/2026). Lot Z1 : écriture sans session (401)
#                 ou sans JSON (415) refusée, en têtes de sécurité, hôte inconnu fermé, tentatives de connexion
#                 comptées en base (toujours bloquées après un redémarrage de l'API).
#   sante       : collecte figée simulée dans l'image des scripts (processus vivant, aucun fichier écrit), lancée comme
#                 sur le serveur (init, contrôle de santé, restart) : elle doit être détectée et relancée, et une
#                 collecte qui écrit ne doit pas l'être (environ 2 minutes ; incident du 18/10/2026).
#
# Usage : scripts/verifier_images.sh [backend] [scripts] [web] [ai2|ai2-complet|fenetres|ai2-fenetres|geolocalisation] [s2] [s2-s3] [controle] [sante] [traces]   (sans argument : les trois premiers)
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
FIGEE=mars_verif_figee_$$
SAINE=mars_verif_saine_$$
TEMPO=mars_verif_tempo_$$
OTEL=mars_verif_otel_$$
OUVRIER=mars_verif_travailleur_$$

nettoyer() {
  docker rm -f "$API" "$WEB" "$BASE" "$FIGEE" "$SAINE" "$TEMPO" "$OTEL" "$OUVRIER" >/dev/null 2>&1 || true
  docker network rm "$RESEAU" >/dev/null 2>&1 || true
}
trap nettoyer EXIT

ok()    { echo "OK      $*"; }
echec() { echo "ÉCHEC   $*"; exit 1; }

verifier_backend() {
  local image="$R/mars_c2-backend:$TAG"
  docker image inspect "$image" >/dev/null 2>&1 || echec "$image absente : la construire d'abord"
  docker network create "$RESEAU" >/dev/null
  docker run -d --name "$BASE" --platform "$PLATEFORME" --network "$RESEAU" -e POSTGRES_DB=mars \
    -e POSTGRES_USER=mars -e POSTGRES_PASSWORD=mars postgis/postgis:16-3.4 >/dev/null
  # Attente en TCP (-h 127.0.0.1) : pendant l'initialisation, l'image PostGIS démarre une base temporaire qui n'ouvre
  # que la socket locale, puis l'arrête. pg_isready sans -h la voyait prête, et l'API se connectait pendant cet arrêt
  # (ConnectionRefused, faux échec). La base définitive est la seule à écouter en TCP.
  local prete=""
  for _ in $(seq 1 90); do
    if docker exec "$BASE" pg_isready -h 127.0.0.1 -U mars -d mars >/dev/null 2>&1; then prete=1; break; fi
    sleep 2
  done
  [ -n "$prete" ] || { docker logs --tail 30 "$BASE"; echec "backend : la base de vérification ne répond pas en TCP après 3 minutes"; }
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

verifier_ai2() {
  local mode=$1 script=${2:-essai_ai2.py} image
  image=$(grep -o 'ghcr.io/allenai/vessel-detection-sentinels@sha256:[0-9a-f]*' config/rules.yaml | head -1)
  [ -n "$image" ] || echec "ai2 : image d'AI2 introuvable dans config/rules.yaml"
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "        téléchargement de l'image d'AI2 (5 Go) : $image"
    docker pull -q --platform "$PLATEFORME" "$image" >/dev/null || echec "ai2 : image $image non téléchargée"
  fi
  # Lancé comme sur le travailleur (travailleurs/demarrage.sh) : script monté en lecture seule dans /mars
  local sortie code=0
  sortie=$(mktemp)
  docker run --rm --platform "$PLATEFORME" --shm-size=2g -v "$PWD/travailleurs:/mars:ro" \
    -v "$PWD/tests/donnees:/donnees:ro" --entrypoint python3 "$image" "/mars/$script" $mode >"$sortie" 2>&1 || code=$?
  grep -E '^(OK|ÉCHEC|        )' "$sortie" || true
  if [ "$code" -ne 0 ]; then
    grep -vE 'Warning|warn\(' "$sortie" | tail -30
    rm -f "$sortie"
    echec "ai2 : essai du script du travailleur Sentinel en échec (code $code, trace ci dessus)"
  fi
  rm -f "$sortie"
}

# Lot S2 (docs/sentinel2.md) : image épinglée de config/rules.yaml (travailleurs.types.sentinel2)
POIDS_S2=https://storage.googleapis.com/ai2-rslearn-projects-data/projects
SATLAS_S2='https://huggingface.co/allenai/satlas-pretrain/resolve/main/sentinel2_swinb_si_ms.pth?download=true'
verifier_s2() {
  local mode=${1:-} cache=${MARS_CACHE_S2:-$HOME/.cache/mars_c2/sentinel2} p IMAGE_S2
  IMAGE_S2=$(grep -o 'ghcr.io/allenai/sentinel2-vessel-detection@sha256:[0-9a-f]*' config/rules.yaml | head -1)
  [ -n "$IMAGE_S2" ] || echec "s2 : image Sentinel 2 introuvable dans config/rules.yaml"
  for p in sentinel2_vessels/data_20250213_02_all_bands sentinel2_vessel_attribute/data_20250205_regress_00; do
    if [ ! -s "$cache/projects/$p/best.ckpt" ]; then
      echo "        téléchargement des poids $p (1,1 Go)"
      mkdir -p "$cache/projects/$p"
      curl -sf -o "$cache/projects/$p/best.ckpt" "$POIDS_S2/$p/checkpoints/best.ckpt" || echec "s2 : poids $p non téléchargés"
    fi
  done
  # Squelette SatlasPretrain (467 Mo) : gardé en cache, reprise du téléchargement si coupé, empreinte vérifiée
  local satlas="$cache/satlas/sentinel2_swinb_si_ms.pth" sha
  sha=$(grep -A3 'chemin: satlas/sentinel2_swinb_si_ms.pth' config/rules.yaml | grep -o 'sha256: [0-9a-f]*' | cut -d' ' -f2)
  if [ "$(shasum -a 256 "$satlas" 2>/dev/null | cut -d' ' -f1)" != "$sha" ]; then
    echo "        téléchargement du squelette SatlasPretrain (467 Mo)"
    mkdir -p "$cache/satlas"
    for _ in 1 2 3 4 5; do curl -sSL --retry 5 -C - -o "$satlas" "$SATLAS_S2" && break; done
    [ "$(shasum -a 256 "$satlas" | cut -d' ' -f1)" = "$sha" ] || echec "s2 : squelette SatlasPretrain, empreinte fausse"
  fi
  if ! docker image inspect "$IMAGE_S2" >/dev/null 2>&1; then
    echo "        téléchargement de l'image Sentinel 2 d'AI2 (7,2 Go) : $IMAGE_S2"
    docker pull -q --platform "$PLATEFORME" "$IMAGE_S2" >/dev/null || echec "s2 : image non téléchargée"
  fi
  local sortie code=0
  sortie=$(mktemp)
  # Hugging Face injoignable dans le conteneur : le modèle doit charger son squelette depuis le fichier remis
  docker run --rm --platform "$PLATEFORME" --shm-size=2g -e RSLP_PREFIX=/rslp -v "$cache:/rslp" \
    --add-host huggingface.co:127.0.0.1 --add-host us.aws.cdn.hf.co:127.0.0.1 --add-host cdn-lfs.huggingface.co:127.0.0.1 \
    -v "${MARS_TRAVAILLEURS:-$PWD/travailleurs}:/mars:ro" --entrypoint python "$IMAGE_S2" /mars/essai_sentinel2.py $mode \
    >"$sortie" 2>&1 || code=$?
  grep -E '^(OK|ÉCHEC|        )' "$sortie" || true
  if [ -n "$mode" ] && grep -q SECRETESSAIS3 "$sortie"; then
    rm -f "$sortie"
    echec "s2-s3 : la clé secrète de l'essai apparaît dans la sortie du travailleur"
  fi
  if [ "$code" -ne 0 ]; then
    grep -vE 'Warning|warn\(' "$sortie" | tail -30
    rm -f "$sortie"
    echec "s2${mode:+-s3} : travailleur Sentinel 2 en échec (code $code, trace ci dessus)"
  fi
  rm -f "$sortie"
}

verifier_sante() {
  local image="$R/mars_c2-scripts:$TAG"
  docker image inspect "$image" >/dev/null 2>&1 || echec "$image absente : la construire d'abord"
  # Mêmes réglages que docker-compose.yml (init, contrôle de santé, restart), seuil ramené à 1 minute pour l'essai.
  # Le processus porte « ais_live.py » dans sa ligne de commande, comme la vraie collecte.
  local opts=(--platform "$PLATEFORME" --init --restart unless-stopped -e SEUIL_COLLECTE_MIN=1 -e AIS_LIVE_DIR=/tmp/live
              --health-cmd "python scripts/sante.py collecte" --health-interval 10s --health-timeout 30s
              --health-retries 1 --health-start-period 0s)
  docker run -d --name "$FIGEE" "${opts[@]}" "$image" python -c "import time; time.sleep(10 ** 6)" ais_live.py >/dev/null
  docker run -d --name "$SAINE" "${opts[@]}" "$image" python -c "
import pathlib, time
p = pathlib.Path('/tmp/live/.signe_de_vie_collecte'); p.parent.mkdir(parents=True, exist_ok=True)
while True:
    p.touch(); time.sleep(20)" ais_live.py >/dev/null
  for _ in $(seq 1 30); do                   # 1 minute de seuil, puis un contrôle toutes les 10 s
    [ "$(docker inspect -f '{{.RestartCount}}' "$FIGEE")" -ge 1 ] && break
    sleep 6
  done
  local n m
  n=$(docker inspect -f '{{.RestartCount}}' "$FIGEE")
  m=$(docker inspect -f '{{.RestartCount}}' "$SAINE")
  [ "$n" -ge 1 ] || { docker inspect -f '{{json .State.Health}}' "$FIGEE"; echec "sante : collecte figée non relancée"; }
  docker logs "$FIGEE" 2>&1 | grep -q "REDÉMARRAGE AUTOMATIQUE de la collecte" \
    || echec "sante : redémarrage absent du journal du conteneur"
  [ "$m" -eq 0 ] || echec "sante : une collecte qui écrit a été relancée ($m fois)"
  ok "sante : collecte figée détectée et relancée ($n redémarrage(s)), collecte saine laissée en paix"
}

# Lot A (21/10/2026) : connexion d'un opérateur et écriture du panneau de contrôle à travers le vrai nginx de l'image
# web, comme le navigateur par le tunnel (en tête Origin). Sur le serveur, nginx remplaçait Host par backend:8000 et
# l'API refusait toute connexion (« Origine refusée »).
verifier_controle() {
  local api="$R/mars_c2-backend:$TAG" web="$R/mars_c2-web:$TAG" i
  for i in "$api" "$web"; do
    docker image inspect "$i" >/dev/null 2>&1 || echec "$i absente : la construire d'abord"
  done
  nettoyer
  docker network create "$RESEAU" >/dev/null
  docker run -d --name "$BASE" --platform "$PLATEFORME" --network "$RESEAU" --network-alias db -e POSTGRES_DB=mars \
    -e POSTGRES_USER=mars -e POSTGRES_PASSWORD=mars -v "$PWD/db/init:/docker-entrypoint-initdb.d:ro" \
    postgis/postgis:16-3.4 >/dev/null
  local prete=""
  for _ in $(seq 1 120); do
    if docker exec "$BASE" pg_isready -h 127.0.0.1 -U mars -d mars >/dev/null 2>&1; then prete=1; break; fi
    sleep 2
  done
  [ -n "$prete" ] || echec "controle : la base de vérification ne répond pas"
  # Opérateur d'essai : mot de passe aléatoire, empreinte au format de taches.py operateur-hacher
  local mdp ligne hote=localhost:18081 jar corps code
  mdp=$(openssl rand -hex 12)
  ligne=$(MDP="$mdp" python3 -c "import hashlib, os, secrets; s = secrets.token_bytes(16); h = hashlib.pbkdf2_hmac('sha256', os.environ['MDP'].encode(), s, 600000); print(f'essai:pbkdf2_sha256:600000:{s.hex()}:{h.hex()}')")
  docker run -d --name "$API" --platform "$PLATEFORME" --network "$RESEAU" --network-alias backend \
    -e DATABASE_URL=postgresql://mars:mars@db:5432/mars -e MARS_OPERATEURS="$ligne" -v "$PWD/config:/app/config:ro" \
    "$api" >/dev/null                                   # config monté comme sur le serveur (règles relues)
  docker run -d --name "$WEB" --platform "$PLATEFORME" --network "$RESEAU" --network-alias web -p "127.0.0.1:18081:80" \
    "$web" >/dev/null
  for _ in $(seq 1 90); do
    curl -sf -m 3 "http://$hote/api/health" >/dev/null 2>&1 && break
    sleep 2
  done
  jar=$(mktemp); corps=$(mktemp)
  code=$(curl -s -o "$corps" -w '%{http_code}' -c "$jar" -D "$corps.entetes" -H "Origin: http://$hote" \
    -H 'Content-Type: application/json' -d "{\"operateur\": \"essai\", \"mot_de_passe\": \"$mdp\"}" \
    "http://$hote/api/controle/connexion")
  if [ "$code" != 200 ]; then
    cat "$corps"; echo; docker logs --tail 15 "$API"
    rm -f "$jar" "$corps" "$corps.entetes"
    echec "controle : connexion refusée à travers nginx (HTTP $code)"
  fi
  if grep -qi '^set-cookie:.*secure' "$corps.entetes"; then
    rm -f "$jar" "$corps" "$corps.entetes"
    echec "controle : cookie Secure sur http (Safari ne le renverrait pas sur http://localhost:8080)"
  fi
  code=$(curl -s -o "$corps" -w '%{http_code}' -b "$jar" -H "Origin: http://$hote" -H 'Content-Type: application/json' \
    -d '{"valeur": 1, "motif": "essai de verifier_images"}' "http://$hote/api/controle/plafonds/sentinel1")
  [ "$code" = 200 ] || { cat "$corps"; echo; rm -f "$jar" "$corps" "$corps.entetes"; echec "controle : écriture du panneau refusée (HTTP $code)"; }
  code=$(curl -s -o "$corps" -w '%{http_code}' -b "$jar" -H "Origin: http://ailleurs.example" \
    -H 'Content-Type: application/json' -d '{"valeur": 2, "motif": "essai"}' "http://$hote/api/controle/plafonds/sentinel1")
  [ "$code" = 403 ] || { rm -f "$jar" "$corps" "$corps.entetes"; echec "controle : une autre origine n'est pas refusée (HTTP $code)"; }
  local motif
  motif=$(python3 -c "import json, sys; print(json.load(open(sys.argv[1]))['detail'])" "$corps")
  # Lot U : dossiers, récit introuvable (404 et non 500), mesures des décisions, à travers nginx, avec les règles montées
  local route attendu
  for route in "dossiers?end=2026-10-09T21:00:00Z 200" "dossiers/navire:1?end=2026-10-09T21:00:00Z 404" \
               "decisions/mesures?jours=30 200" "decisions/journal?limite=5 200"; do
    attendu=${route##* }; route=${route% *}
    code=$(curl -s -o "$corps" -w '%{http_code}' "http://$hote/api/$route")
    [ "$code" = "$attendu" ] || { cat "$corps"; echo; docker logs --tail 15 "$API"; rm -f "$jar" "$corps" "$corps.entetes"
      echec "controle : /api/$route répond $code au lieu de $attendu"; }
  done
  # Lot Z1 : toute écriture exige une session et du JSON ; en têtes de sécurité ; hôte inconnu fermé ; tentatives de
  # connexion comptées par adresse, en base (elles survivent au redémarrage de l'API)
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Origin: http://$hote" -H 'Content-Type: application/json' \
    -d '{"action": "classer", "motif": "doublon"}' "http://$hote/api/alerts/1/actions")
  [ "$code" = 401 ] || { rm -f "$jar" "$corps" "$corps.entetes"; echec "controle : décision sans session acceptée (HTTP $code)"; }
  code=$(curl -s -o /dev/null -w '%{http_code}' -b "$jar" -H "Origin: http://$hote" -H 'Content-Type: text/plain' \
    -d '{"note": "intersite"}' "http://$hote/api/vessels/1/notes")
  [ "$code" = 415 ] || { rm -f "$jar" "$corps" "$corps.entetes"; echec "controle : écriture sans JSON acceptée (HTTP $code)"; }
  curl -s -D "$corps.entetes" -o /dev/null "http://$hote/"
  for e in "content-security-policy: default-src 'self'" "x-frame-options: DENY" "x-content-type-options: nosniff" \
           "referrer-policy: no-referrer"; do
    grep -qi "^$e" "$corps.entetes" || { cat "$corps.entetes"; rm -f "$jar" "$corps" "$corps.entetes"; echec "controle : en tête absent ($e)"; }
  done
  if curl -s -m 5 -o /dev/null -H "Host: rebond.example" "http://$hote/"; then
    rm -f "$jar" "$corps" "$corps.entetes"; echec "controle : un hôte inconnu reçoit une réponse (rebond DNS)"
  fi
  for _ in 1 2 3 4 5; do
    curl -s -o /dev/null -H "Origin: http://$hote" -H 'Content-Type: application/json' \
      -d '{"operateur": "essai", "mot_de_passe": "faux"}' "http://$hote/api/controle/connexion"
  done
  docker restart "$API" >/dev/null
  for _ in $(seq 1 60); do curl -sf -m 3 "http://$hote/api/health" >/dev/null 2>&1 && break; sleep 2; done
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Origin: http://$hote" -H 'Content-Type: application/json' \
    -d "{\"operateur\": \"essai\", \"mot_de_passe\": \"$mdp\"}" "http://$hote/api/controle/connexion")
  [ "$code" = 429 ] || { rm -f "$jar" "$corps" "$corps.entetes"; echec "controle : 5 échecs puis redémarrage, connexion non bloquée (HTTP $code)"; }
  rm -f "$jar" "$corps" "$corps.entetes"
  ok "controle : connexion et écriture du panneau à travers nginx, cookie sans Secure en http, autre origine refusée ($motif) ; dossiers et mesures des décisions servis ; écriture sans session (401) ou sans JSON (415) refusée, en têtes de sécurité, hôte inconnu fermé, tentatives comptées en base"
}

verifier_traces() {
  local scripts="$R/mars_c2-scripts:$TAG" api="$R/mars_c2-backend:$TAG" web="$R/mars_c2-web:$TAG" i
  for i in "$scripts" "$api" "$web"; do
    docker image inspect "$i" >/dev/null 2>&1 || echec "$i absente : la construire d'abord"
  done
  local otel_img tempo_img
  otel_img=$(grep -o 'otel/opentelemetry-collector-contrib:[^ ]*' docker-compose.serveur.yml | head -1)
  tempo_img=$(grep -o 'grafana/tempo:[^ ]*' docker-compose.serveur.yml | head -1)
  nettoyer                                   # conteneurs des cibles précédentes (mêmes noms), réseau neuf
  docker network create "$RESEAU" >/dev/null
  docker run -d --name "$BASE" --platform "$PLATEFORME" --network "$RESEAU" --network-alias db -e POSTGRES_DB=mars \
    -e POSTGRES_USER=mars -e POSTGRES_PASSWORD=mars -v "$PWD/db/init:/docker-entrypoint-initdb.d:ro" \
    postgis/postgis:16-3.4 >/dev/null
  docker run -d --name "$TEMPO" --platform "$PLATEFORME" --network "$RESEAU" --network-alias tempo -u 0:0 \
    -v "$PWD/otel/tempo.yaml:/etc/tempo.yaml:ro" -p 127.0.0.1:13209:3200 "$tempo_img" -config.file=/etc/tempo.yaml >/dev/null
  docker run -d --name "$OTEL" --platform "$PLATEFORME" --network "$RESEAU" --network-alias otel -u 0:0 \
    -v "$PWD/otel/collecteur.yaml:/etc/otelcol-contrib/config.yaml:ro" "$otel_img" >/dev/null
  local prete=""
  for _ in $(seq 1 120); do
    if docker exec "$BASE" pg_isready -h 127.0.0.1 -U mars -d mars >/dev/null 2>&1; then prete=1; break; fi
    sleep 2
  done
  [ -n "$prete" ] || echec "traces : la base de vérification ne répond pas"
  local env=(-e DATABASE_URL=postgresql://mars:mars@db:5432/mars -e OTEL_EXPORTER_OTLP_ENDPOINT=http://otel:4318)
  docker run -d --name "$API" --platform "$PLATEFORME" --network "$RESEAU" --network-alias backend "${env[@]}" \
    -v "$PWD/config:/app/config:ro" \
    -e CDSE_TRAVAILLEUR_ACCESS_KEY=essai -e CDSE_TRAVAILLEUR_SECRET_KEY=essai "$api" >/dev/null
  docker run -d --name "$WEB" --platform "$PLATEFORME" --network "$RESEAU" --network-alias web "$web" >/dev/null
  for _ in $(seq 1 90); do
    docker run --rm --network "$RESEAU" "$scripts" python -c "import urllib.request as u; u.urlopen('http://backend:8000/api/health', timeout=3)" \
      >/dev/null 2>&1 && break
    sleep 2
  done
  local jeton id tp trace
  jeton=$(openssl rand -hex 16)
  read -r id tp < <(docker run --rm --platform "$PLATEFORME" --network "$RESEAU" "${env[@]}" -e JETON="$jeton" \
    -v "$PWD/scripts/essai_traces:/essai:ro" "$scripts" python /essai/lancement.py | tail -1)
  trace=$(echo "$tp" | cut -d- -f2)
  [ ${#trace} -eq 32 ] || echec "traces : lancement simulé sans contexte de trace ($id $tp)"
  docker run --name "$OUVRIER" --platform "$PLATEFORME" --network "$RESEAU" -e TRACEPARENT="$tp" -e JETON="$jeton" \
    -e RETOUR="http://web:8090/api/travailleurs/$id" -v "$PWD/travailleurs:/t:ro" \
    -v "$PWD/scripts/essai_traces:/essai:ro" python:3.8-slim bash /essai/travailleur.sh \
    || { docker logs --tail 20 "$API"; echec "traces : travailleur simulé en échec (trace ci dessus)"; }
  docker run --rm --platform "$PLATEFORME" --network "$RESEAU" "${env[@]}" -v "$PWD/scripts/essai_traces:/essai:ro" -v "$PWD/config:/app/config:ro" \
    "$scripts" python /essai/surveillance.py | tail -1
  # Filtre du collecteur : un span envoyé sans le filtre du code, avec un jeton et une adresse signée
  local brut
  brut=$(openssl rand -hex 16)
  docker exec "$WEB" curl -s -o /dev/null -X POST -H "Content-Type: application/json" http://otel:4318/v1/traces \
    -d '{"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"mars-taches"}}]},"scopeSpans":[{"spans":[{"traceId":"'"$brut"'","spanId":"0102030405060708","name":"essai.filtre","kind":1,"startTimeUnixNano":"1","endTimeUnixNano":"2","attributes":[{"key":"jeton","value":{"stringValue":"SECRET1"}},{"key":"url.full","value":{"stringValue":"https://h/p?X-Amz-Signature=SECRET2"}},{"key":"detail","value":{"stringValue":"Bearer SECRET3"}}]}]}]}]}'
  # Les outils de supervision ne sont jamais tracés : un span qui se dit « grafana » doit être écarté par le collecteur
  local outil
  outil=$(openssl rand -hex 16)
  docker exec "$WEB" curl -s -o /dev/null -X POST -H "Content-Type: application/json" http://otel:4318/v1/traces \
    -d '{"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"grafana"}}]},"scopeSpans":[{"spans":[{"traceId":"'"$outil"'","spanId":"0102030405060708","name":"essai.outil","kind":1,"startTimeUnixNano":"'"$(date +%s)"'000000000","endTimeUnixNano":"'"$(date +%s)"'100000000"}]}]}]}'
  echo "        attente de la décision d'échantillonnage du collecteur (15 s) et de l'écriture dans Tempo"
  local verdict=""
  for _ in $(seq 1 20); do
    sleep 6
    verdict=$(curl -s "http://127.0.0.1:13209/api/traces/$trace" | python3 -c '
import json, sys
d = json.load(sys.stdin)
noms, services = set(), set()
for b in d.get("batches", d.get("resourceSpans", [])):
    svc = [a["value"]["stringValue"] for a in b["resource"]["attributes"] if a["key"] == "service.name"]
    for ss in b.get("scopeSpans", []):
        for sp in ss["spans"]:
            noms.add(sp["name"]); services.update(svc)
attendus = {"tache sentinel", "sentinel.selection", "travailleur.lancement", "demarrage.contact_serveur",
            "demarrage.analyse", "travailleur.demarrage", "travailleur.sentinel", "travailleur.remise_tache",
            "sentinel.preparation", "travailleur.renvoi_resultats", "travailleur.resultats",
            "travailleur.destruction"}
manque = sorted(attendus - noms) + sorted({"mars-taches", "mars-travailleur", "mars-api"} - services)
print("OK %d spans, services %s" % (len(noms), ", ".join(sorted(services))) if not manque else "MANQUE " + ", ".join(manque))
' 2>/dev/null || echo "MANQUE trace introuvable")
    [ "${verdict%% *}" = OK ] && curl -s "http://127.0.0.1:13209/api/traces/$brut" | grep -q essai.filtre && break
  done
  [ "${verdict%% *}" = OK ] || { docker logs --tail 20 "$OTEL"; echec "traces : trace $trace incomplète dans Tempo : $verdict"; }
  local filtre
  filtre=$(curl -s "http://127.0.0.1:13209/api/traces/$brut")
  echo "$filtre" | grep -q essai.filtre || echec "traces : span de l'essai du filtre absent de Tempo"
  echo "$filtre" | grep -q SECRET && echec "traces : une donnée sensible a traversé le collecteur"
  [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:13209/api/traces/$outil")" = 404 ] \
    || echec "traces : un span de Grafana est arrivé dans Tempo (les outils de supervision ne sont jamais tracés)"
  ok "traces : trace $trace de bout en bout dans Tempo ($verdict) ; jeton faux refusé ; données sensibles filtrées ; Grafana écarté"
}

for cible in "${CIBLES[@]}"; do
  case "$cible" in
    backend) verifier_backend ;;
    scripts) verifier_scripts ;;
    web)     verifier_web ;;
    ai2)     verifier_ai2 --imports ;;
    ai2-complet) verifier_ai2 "" ;;
    fenetres) verifier_ai2 --lecture essai_fenetres.py ;;
    ai2-fenetres) verifier_ai2 --complet essai_fenetres.py ;;
    geolocalisation) verifier_ai2 --geolocalisation essai_fenetres.py ;;
    s2)      verifier_s2 ;;
    s2-s3)   verifier_s2 --s3 ;;
    controle) verifier_controle ;;
    sante)   verifier_sante ;;
    traces)  verifier_traces ;;
    *)       echec "cible inconnue : $cible (backend, scripts, web, ai2, ai2-complet, fenetres, ai2-fenetres, geolocalisation, s2, s2-s3, controle, sante ou traces)" ;;
  esac
done
echo "Images vérifiées : ${CIBLES[*]} ($TAG). Publication possible."
