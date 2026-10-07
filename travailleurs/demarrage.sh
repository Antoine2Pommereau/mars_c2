#!/bin/bash
# Démarrage d'un travailleur MARS C2, lancé par cloud-init (mars/travailleurs.py) :
#   1. interface du réseau privé trouvée par son adresse MAC, configurée en DHCP si le système ne l'a pas fait ;
#   2. contact du serveur sur son adresse privée (état « reseau »), journal envoyé dès que le réseau fonctionne ;
#   3. image d'analyse tirée du registre GitHub, script du travailleur exécuté, journal final envoyé, arrêt.
# Chaque étape est écrite dans /var/log/mars-travailleur.log et sur la console série (lignes « MARS »), lisible depuis
# la console Scaleway même si le réseau privé ne fonctionne pas. En cas d'échec, la machine s'arrête au bout de
# 10 minutes (le temps de lire la console) ; le serveur la détruit au plus tard 10 minutes après sa création.
set -uo pipefail
. /mars/demarrage.env                 # RETOUR, JETON, MAC, IMAGE, SCRIPT, GPU
LOG=/var/log/mars-travailleur.log

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
echec() { log "ECHEC : $*"; detail ip -br addr; detail ip route; shutdown -h +10; exit 1; }
a_une_ipv4() { ip -4 -br addr show "$1" 2>/dev/null | grep -q '[0-9]\.[0-9]'; }

log "démarrage ; réseau privé attendu sur l'interface d'adresse $MAC ; serveur $RETOUR"

# 1. Interface privée
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
CODE=000
for _ in $(seq 1 30); do
  CODE=$(signal reseau)
  [ "$CODE" = "200" ] && break
  sleep 5
done
[ "$CODE" = "200" ] || echec "serveur injoignable sur le réseau privé ($RETOUR, réponse $CODE)"
log "serveur joint sur le réseau privé"
envoyer_journal

# 3. Analyse
log "téléchargement de l'image $IMAGE"
detail docker pull -q "$IMAGE" || { envoyer_journal; echec "image $IMAGE introuvable"; }
envoyer_journal
log "analyse"
# shellcheck disable=SC2086
docker run --rm $GPU --env-file /mars/env -e MARS_TACHE=/mars/tache.json -v /mars:/mars:ro \
  --entrypoint python3 "$IMAGE" "/mars/$SCRIPT" 2>&1 | tee -a "$LOG"
log "analyse terminée (code ${PIPESTATUS[0]})"
envoyer_journal
shutdown -h now
