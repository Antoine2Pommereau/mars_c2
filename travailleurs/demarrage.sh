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
# GPU (lot C4, essai du 19/10/2026 sur un RENDER-S : « failed to initialize NVML: Driver Not Loaded ») : avant l'analyse,
# étape controle_gpu (nvidia-smi) ; un GPU inutilisable est signalé au serveur (état « gpu_inutilisable », message)
# sans lancer l'analyse : le serveur détruit l'instance, marque le type incompatible et remet l'analyse en attente.
# Chemins surchargeables pour l'essai sur le Mac (tests/test_demarrage.py) ; en production, les valeurs par défaut.
set -uo pipefail
MARS=${MARS_DIR:-/mars}
. "$MARS/demarrage.env"               # RETOUR, JETON, MAC, IMAGE, SCRIPT, GPU, TRACEPARENT
LOG=${MARS_LOG:-/var/log/mars-travailleur.log}
CONSOLE=${MARS_CONSOLE:-/dev/console}
NET=${MARS_NET_DIR:-/sys/class/net}
. "$MARS/traces.sh"                   # spans des étapes (TRACEPARENT, RETOUR, JETON)

log() { echo "MARS $(date -u +%H:%M:%S) $*" | tee -a "$LOG" > "$CONSOLE"; }
detail() { "$@" 2>&1 | tee -a "$LOG" > "$CONSOLE"; }
# signal ETAT [MESSAGE] [ETAPE] : état du travailleur au serveur (réseau privé, jeton de l'exécution)
signal() {
  curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $JETON" \
       -H "Content-Type: application/json" \
       -d "{\"etat\": \"$1\", \"message\": \"$(texte_json "${2:-}" 1500)\", \"etape\": \"${3:-}\"}" "$RETOUR/etat"
}
envoyer_journal() {
  curl -s -m 20 -o /dev/null -X POST -H "Authorization: Bearer $JETON" -H "Content-Type: text/plain" \
       --data-binary @"$LOG" "$RETOUR/journal" || true
}
# echec MOTIF : sortie en échec. L'état « echec » part au serveur tout de suite, avec l'étape et le motif (essai 26 du
# 19/10/2026 : sans lui, le serveur attendait la fin de la durée de vie, 45 min, instance arrêtée mais facturée). Le
# serveur détruit l'instance à la minute suivante. Avant le contact du serveur (réseau privé), l'envoi échoue en
# silence : le délai de démarrage du serveur prend le relais.
echec() {
  local etape=${ETAPE:-demarrage}
  log "ECHEC ($etape) : $*"; detail ip -br addr; detail ip route
  fin_etape 2 "$*"
  signal echec "$*" "$etape" > /dev/null
  envoyer_journal
  span "travailleur.demarrage" "$RACINE" "$PARENT" "$RACINE_DEBUT" 2 "$*"; envoyer_spans
  shutdown -h +10; exit 1
}
a_une_ipv4() { ip -4 -br addr show "$1" 2>/dev/null | grep -q '[0-9]\.[0-9]'; }
# GPU inutilisable : journal, message (dernières lignes utiles et dmesg nvidia), état signalé au serveur, étape en
# erreur, arrêt sans analyse. Le serveur détruit l'instance aussitôt.
gpu_inutilisable() {
  local msg="$1" dm
  dm=$(dmesg 2>/dev/null | grep -i nvidia | tail -n 5)
  log "GPU INUTILISABLE : $msg"
  [ -n "$dm" ] && { log "dmesg nvidia :"; echo "$dm" | tee -a "$LOG" > "$CONSOLE"; }
  attribut gpu.message "$msg"
  fin_etape 2 "GPU inutilisable : $msg"
  signal gpu_inutilisable "$msg${dm:+ | dmesg : $dm}" > /dev/null
  envoyer_journal
  span "travailleur.demarrage" "$RACINE" "$PARENT" "$RACINE_DEBUT" 2 "GPU inutilisable"
  envoyer_spans
  shutdown -h +10; exit 1
}

log "démarrage ; réseau privé attendu sur l'interface d'adresse $MAC ; serveur $RETOUR"

# 1. Interface privée
etape interface_privee
IF=""
for _ in $(seq 1 30); do
  for d in "$NET"/*; do
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

# 3. GPU demandé : contrôle avant tout téléchargement (nvidia-smi : nom, pilote, mémoire, CUDA)
case "$GPU" in
  *--gpus*)
    etape controle_gpu
    SORTIE_GPU=$(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1)
    CODE_GPU=$?
    if [ "$CODE_GPU" != "0" ] || [ -z "$(echo "$SORTIE_GPU" | grep -v '^ *$')" ]; then
      gpu_inutilisable "nvidia-smi (code $CODE_GPU) : $(echo "$SORTIE_GPU" | grep -v '^ *$' | tail -n 3)"
    fi
    IFS=',' read -r GPU_NOM GPU_PILOTE GPU_MEMOIRE <<< "$(echo "$SORTIE_GPU" | head -n 1)"
    GPU_CUDA=$(nvidia-smi 2>/dev/null | grep -o 'CUDA Version: [0-9.]*' | awk '{print $3}')
    log "GPU : $GPU_NOM, pilote $GPU_PILOTE, mémoire $GPU_MEMOIRE, CUDA ${GPU_CUDA:-inconnue}"
    attribut gpu.nom "$(echo "$GPU_NOM" | xargs)"; attribut gpu.pilote "$(echo "$GPU_PILOTE" | xargs)"
    attribut gpu.memoire "$(echo "$GPU_MEMOIRE" | xargs)"; attribut gpu.cuda "${GPU_CUDA:-inconnue}"
    fin_etape
    ;;
esac

# 4. Analyse : Docker installé s'il manque (image Ubuntu officielle), puis image d'analyse
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
SORTIE=$(mktemp)
# shellcheck disable=SC2086
docker run --rm $GPU --env-file "$MARS/env" -e MARS_TACHE=/mars/tache.json -e "TRACEPARENT=00-$TRACE-$ETAPE_ID-01" \
  -v "$MARS:/mars:ro" --entrypoint python3 "$IMAGE" "/mars/$SCRIPT" 2>&1 | tee -a "$LOG" "$SORTIE"
CODE_ANALYSE=${PIPESTATUS[0]}
log "analyse terminée (code $CODE_ANALYSE)"
# Filet : le conteneur n'a pas pu recevoir le GPU (NVML, CDI) malgré le contrôle
if [ "$CODE_ANALYSE" = "125" ] && grep -qiE 'NVML|CDI|nvidia-container' "$SORTIE"; then
  gpu_inutilisable "docker run --gpus (code 125) : $(grep -v '^ *$' "$SORTIE" | tail -n 3)"
fi
# Autre sortie en échec : codes 125 à 127, Docker n'a pas lancé le conteneur (machine) ; sinon, l'analyse elle même
if [ "$CODE_ANALYSE" != "0" ]; then
  case "$CODE_ANALYSE" in 125|126|127) ETAPE=analyse_docker ;; esac
  echec "code de sortie $CODE_ANALYSE : $(grep -iE 'error|erreur|exception|traceback' "$SORTIE" | tail -n 1)"
fi
fin_etape
span "travailleur.demarrage" "$RACINE" "$PARENT" "$RACINE_DEBUT" 1 ""
envoyer_spans
envoyer_journal
# Le serveur détruit l'instance dès réception du résultat, tant qu'elle tourne encore (action terminate) ; l'arrêt
# différé n'est qu'un filet si la destruction tardait
shutdown -h +10
