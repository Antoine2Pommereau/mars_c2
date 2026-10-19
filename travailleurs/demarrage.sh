#!/bin/bash
# Démarrage d'un travailleur MARS C2, lancé par cloud-init (mars/travailleurs.py) :
#   1. interface du réseau privé trouvée par son adresse MAC, configurée en DHCP si le système ne l'a pas fait ;
#   2. contact du serveur sur son adresse privée (état « reseau »), journal envoyé dès que le réseau fonctionne ;
#   3. image d'analyse tirée du registre GitHub, script du travailleur exécuté, journal final envoyé, arrêt.
# Chaque étape est écrite dans /var/log/mars-travailleur.log et sur la console série (lignes « MARS »), lisible depuis
# la console Scaleway même si le réseau privé ne fonctionne pas. En cas d'échec, la machine s'arrête au bout de
# 10 minutes (le temps de lire la console) ; le serveur la détruit au plus tard 10 minutes après sa création.
# Traces (étape D2) : chaque étape est un span OTLP (JSON) rattaché à la trace du lancement (TRACEPARENT), gardé dans
# un fichier puis envoyé au serveur dès que le réseau privé fonctionne ; le script d'analyse reçoit le contexte de
# l'étape « analyse », ses propres spans s'y rattachent (travailleurs/otel_mini.py).
set -uo pipefail
. /mars/demarrage.env                 # RETOUR, JETON, MAC, IMAGE, SCRIPT, GPU, TRACEPARENT
LOG=/var/log/mars-travailleur.log
. /mars/traces.sh                     # spans des étapes (TRACEPARENT, RETOUR, JETON)

log() { echo "MARS $(date -u +%H:%M:%S) $*" | tee -a "$LOG" > /dev/console; }
detail() { "$@" 2>&1 | tee -a "$LOG" > /dev/console; }
signal() {
  curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $JETON" \
       -H "Content-Type: application/json" -d "{\"etat\": \"$1\"}" "$RETOUR/etat"
}
envoyer_journal() {
  curl -s -m 20 -o /dev/null -X POST -H "Authorization: Bearer $JETON" -H "Content-Type: text/plain" \
       --data-binary @"$LOG" "$RETOUR/journal" || true
}
echec() {
  log "ECHEC : $*"; detail ip -br addr; detail ip route
  fin_etape 2 "$*"; span "travailleur.demarrage" "$RACINE" "$PARENT" "$RACINE_DEBUT" 2 "$*"; envoyer_spans
  shutdown -h +10; exit 1
}
a_une_ipv4() { ip -4 -br addr show "$1" 2>/dev/null | grep -q '[0-9]\.[0-9]'; }

log "démarrage ; réseau privé attendu sur l'interface d'adresse $MAC ; serveur $RETOUR"

# 1. Interface privée
etape interface_privee
IF=""
for _ in $(seq 1 30); do
  for d in /sys/class/net/*; do
    [ "$(cat "$d/address" 2>/dev/null)" = "$MAC" ] && IF=$(basename "$d")
  done
  [ -n "$IF" ] && break
  sleep 2
done
[ -n "$IF" ] || echec "aucune interface d'adresse $MAC : réseau privé non rattaché à l'instance"
ip link set "$IF" up
if ! a_une_ipv4 "$IF"; then
  log "interface $IF sans adresse : configuration DHCP"
  if command -v netplan > /dev/null; then
    # Routes et DNS du réseau privé ignorés : la sortie vers Internet reste sur l'interface publique
    printf 'network:\n  version: 2\n  ethernets:\n    mars-prive:\n      match: {macaddress: "%s"}\n      dhcp4: true\n      dhcp4-overrides: {use-routes: false, use-dns: false}\n' \
      "$MAC" > /etc/netplan/60-mars-prive.yaml
    chmod 600 /etc/netplan/60-mars-prive.yaml
    detail netplan apply
  fi
  for _ in $(seq 1 20); do a_une_ipv4 "$IF" && break; sleep 3; done
  if ! a_une_ipv4 "$IF" && command -v dhclient > /dev/null; then detail dhclient -v "$IF"; fi
fi
a_une_ipv4 "$IF" || echec "aucune adresse DHCP sur l'interface privée $IF"
log "interface privée $IF : $(ip -4 -br addr show "$IF" | awk '{print $3}')"

# 2. Contact du serveur
etape contact_serveur
CODE=000
for _ in $(seq 1 30); do
  CODE=$(signal reseau)
  [ "$CODE" = "200" ] && break
  sleep 5
done
[ "$CODE" = "200" ] || echec "serveur injoignable sur le réseau privé ($RETOUR, réponse $CODE)"
log "serveur joint sur le réseau privé"
fin_etape
envoyer_journal
envoyer_spans

# 3. Analyse : Docker installé s'il manque (image Ubuntu officielle), puis image d'analyse
log "système : $(. /etc/os-release && echo "$PRETTY_NAME"), disque $(df -h / | awk 'NR==2 {print $2 " dont " $4 " libres"}')"
if ! command -v docker > /dev/null; then
  etape installation_docker
  log "installation de Docker"
  export DEBIAN_FRONTEND=noninteractive
  detail apt-get -qq update && detail apt-get -qq install -y docker.io > /dev/null \
    || { envoyer_journal; echec "installation de Docker impossible"; }
  systemctl start docker
fi
envoyer_journal
etape telechargement_image
log "téléchargement de l'image $IMAGE"
detail docker pull -q "$IMAGE" || { envoyer_journal; echec "image $IMAGE introuvable"; }
envoyer_journal
etape analyse
envoyer_spans
log "analyse"
# shellcheck disable=SC2086
docker run --rm $GPU --env-file /mars/env -e MARS_TACHE=/mars/tache.json -e "TRACEPARENT=00-$TRACE-$ETAPE_ID-01" \
  -v /mars:/mars:ro --entrypoint python3 "$IMAGE" "/mars/$SCRIPT" 2>&1 | tee -a "$LOG"
CODE_ANALYSE=${PIPESTATUS[0]}
log "analyse terminée (code $CODE_ANALYSE)"
if [ "$CODE_ANALYSE" = "0" ]; then fin_etape; else fin_etape 2 "code de sortie $CODE_ANALYSE"; fi
span "travailleur.demarrage" "$RACINE" "$PARENT" "$RACINE_DEBUT" 1 ""
envoyer_spans
envoyer_journal
# Le serveur détruit l'instance dès réception du résultat, tant qu'elle tourne encore (action terminate) ; l'arrêt
# différé n'est qu'un filet si la destruction tardait
shutdown -h +10
