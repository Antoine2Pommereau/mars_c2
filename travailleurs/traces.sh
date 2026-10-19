#!/bin/bash
# Spans OpenTelemetry du script de démarrage des travailleurs (étape D2), sourcé par travailleurs/demarrage.sh.
# Chaque étape devient un span OTLP en JSON, rattaché à la trace du lancement (TRACEPARENT, remis par cloud-init) ;
# les spans attendent dans un fichier et partent vers le serveur (RETOUR/traces, jeton JETON de l'exécution) dès que
# le réseau privé fonctionne. Variables attendues : TRACEPARENT (facultatif), RETOUR, JETON ; SPANS pour changer le
# fichier d'attente (essais : scripts/verifier_images.sh traces).
SPANS=${SPANS:-/var/log/mars-spans.json}
TP=${TRACEPARENT:-}
nid() { od -An -N8 -tx1 /dev/urandom | tr -d ' \n'; }
ns() { date +%s%N; }
TRACE=${TP:3:32}
PARENT=${TP:36:16}
[ ${#TRACE} -eq 32 ] || { TRACE=$(nid)$(nid); PARENT=""; }
RACINE=$(nid)
RACINE_DEBUT=$(ns)
ETAPE=""
ETAPE_ATTRS=""
# Texte sûr dans du JSON : ni guillemet, ni barre oblique inverse, ni retour à la ligne
texte_json() { printf '%s' "${1:-}" | tr -d '"\\' | tr '\n\r\t' '   ' | head -c "${2:-200}"; }
# attribut CLE VALEUR : ajouté au span de l'étape en cours
attribut() {
  ETAPE_ATTRS="${ETAPE_ATTRS:+$ETAPE_ATTRS,}{\"key\":\"$1\",\"value\":{\"stringValue\":\"$(texte_json "$2" 300)\"}}"
}
# span NOM ID PARENT DEBUT CODE MESSAGE [ATTRIBUTS] (code 1 : réussi, 2 : erreur ; attributs : liste OTLP en JSON)
span() {
  printf '{"traceId":"%s","spanId":"%s","parentSpanId":"%s","name":"%s","kind":1,"startTimeUnixNano":"%s","endTimeUnixNano":"%s","status":{"code":%s,"message":"%s"},"attributes":[%s]}\n' \
    "$TRACE" "$2" "$3" "$1" "$4" "$(ns)" "$5" "$(texte_json "${6:-}")" "${7:-}" >> "$SPANS"
}
etape() { fin_etape; ETAPE=$1; ETAPE_ID=$(nid); ETAPE_DEBUT=$(ns); ETAPE_ATTRS=""; }
fin_etape() {
  [ -n "$ETAPE" ] && span "demarrage.$ETAPE" "$ETAPE_ID" "$RACINE" "$ETAPE_DEBUT" "${1:-1}" "${2:-}" "$ETAPE_ATTRS"
  ETAPE=""; ETAPE_ATTRS=""
}
envoyer_spans() {
  [ -s "$SPANS" ] || return 0
  { printf '{"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"mars-travailleur"}},{"key":"service.namespace","value":{"stringValue":"mars-c2"}}]},"scopeSpans":[{"scope":{"name":"mars.demarrage"},"spans":['
    paste -sd, "$SPANS"; printf ']}]}]}'; } > /tmp/mars-spans-lot.json
  CODE_SPANS=$(curl -s -m 10 -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $JETON" \
       -H "Content-Type: application/json" --data-binary @/tmp/mars-spans-lot.json "$RETOUR/traces")
  [ "$CODE_SPANS" = "200" ] && : > "$SPANS"
  return 0
}
