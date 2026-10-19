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
#   traces      : une trace complète de bout en bout arrive dans Tempo (étape D2) : base, Tempo et collecteur réels,
#                 API et nginx des images construites ; lancement d'un travailleur simulé côté taches, étapes du script
#                 de démarrage et vrai script Sentinel sous Python 3.8 (comme l'image d'AI2), envoyés par nginx avec le
#                 jeton (un jeton faux est refusé), résultats traités et instance « détruite » ; la trace doit réunir
#                 taches, travailleur et API. Vérifie aussi le filtre des données sensibles du collecteur (environ
#                 3 minutes ; images publiques du collecteur et de Tempo téléchargées au besoin).
#   sante       : collecte figée simulée dans l'image des scripts (processus vivant, aucun fichier écrit), lancée comme
#                 sur le serveur (init, contrôle de santé, restart) : elle doit être détectée et relancée, et une
#                 collecte qui écrit ne doit pas l'être (environ 2 minutes ; incident du 18/10/2026).
#
# Usage : scripts/verifier_images.sh [backend] [scripts] [web] [ai2|ai2-complet] [sante] [traces]   (sans argument : les trois premiers)
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
  local mode=$1 image
  image=$(grep -o 'ghcr.io/allenai/vessel-detection-sentinels@sha256:[0-9a-f]*' config/rules.yaml | head -1)
  [ -n "$image" ] || echec "ai2 : image d'AI2 introuvable dans config/rules.yaml"
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "        téléchargement de l'image d'AI2 (5 Go) : $image"
    docker pull -q --platform "$PLATEFORME" "$image" >/dev/null || echec "ai2 : image $image non téléchargée"
  fi
  # Lancé comme sur le travailleur (travailleurs/demarrage.sh) : script monté en lecture seule dans /mars
  local sortie code=0
  sortie=$(mktemp)
  docker run --rm --platform "$PLATEFORME" --shm-size=2g -v "$PWD/travailleurs:/mars:ro" --entrypoint python3 \
    "$image" /mars/essai_ai2.py $mode >"$sortie" 2>&1 || code=$?
  grep -E '^(OK|ÉCHEC|        )' "$sortie" || true
  if [ "$code" -ne 0 ]; then
    grep -vE 'Warning|warn\(' "$sortie" | tail -30
    rm -f "$sortie"
    echec "ai2 : essai du script du travailleur Sentinel en échec (code $code, trace ci dessus)"
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
            "sentinel.preparation_gpu", "travailleur.renvoi_resultats", "travailleur.resultats",
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
    sante)   verifier_sante ;;
    traces)  verifier_traces ;;
    *)       echec "cible inconnue : $cible (backend, scripts, web, ai2, ai2-complet, sante ou traces)" ;;
  esac
done
echo "Images vérifiées : ${CIBLES[*]} ($TAG). Publication possible."
