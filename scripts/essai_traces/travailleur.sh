#!/bin/bash
# Essai des traces de bout en bout, côté travailleur (Python 3.8, comme l'image d'AI2) : étapes du script de
# démarrage (travailleurs/traces.sh), puis le vrai script Sentinel (travailleurs/sentinel.py), sans GPU ni modèle :
# il s'arrête à la préparation, renvoie son échec, et ses spans partent par nginx (port 8090, jeton vérifié).
set -euo pipefail
# Check-Date : horloge de la machine de Docker Desktop parfois en retard sur un index Debian tout juste publié (22/10/2026)
apt-get -qq -o Acquire::Check-Date=false update >/dev/null && apt-get -qq install -y curl >/dev/null
export SPANS=/tmp/spans.json
. /t/traces.sh
etape contact_serveur
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $JETON" \
  -H "Content-Type: application/json" -d '{"etat": "reseau"}' "$RETOUR/etat")
[ "$code" = 200 ] || { echo "ÉCHEC contact du serveur : HTTP $code"; exit 1; }
fin_etape
envoyer_spans
[ "$CODE_SPANS" = 200 ] || { echo "ÉCHEC spans refusés par nginx : HTTP $CODE_SPANS"; exit 1; }
faux=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer faux" \
  -H "Content-Type: application/json" -d '{"resourceSpans": []}' "$RETOUR/traces")
[ "$faux" = 403 ] || { echo "ÉCHEC un jeton faux n'est pas refusé : HTTP $faux"; exit 1; }
web=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Content-Type: application/json" \
  -d '{"resourceSpans": []}' "http://web/otel/v1/traces")
[ "$web" = 200 ] || { echo "ÉCHEC relais des spans de l'interface : HTTP $web"; exit 1; }
etape analyse
envoyer_spans
printf '{"retour": "%s", "jeton": "%s", "tache_par_le_reseau": true, "script": "travailleurs/sentinel.py"}' \
  "$RETOUR" "$JETON" > /tmp/tache.json
TRACEPARENT="00-$TRACE-$ETAPE_ID-01" MARS_TACHE=/tmp/tache.json python3 /t/sentinel.py 2>&1 | tail -3
fin_etape
span travailleur.demarrage "$RACINE" "$PARENT" "$RACINE_DEBUT" 1 ""
envoyer_spans
[ "$CODE_SPANS" = 200 ] || { echo "ÉCHEC derniers spans refusés : HTTP $CODE_SPANS"; exit 1; }
echo "travailleur simulé : $(python3 --version)"
