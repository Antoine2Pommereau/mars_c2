#!/bin/bash
# Démarre MARS C2 : base et API (Docker), service d'inférence (GPU du Mac), interface (Vite)
set -euo pipefail
cd "$(dirname "$0")"

# Journaux : le dossier doit exister avant toute redirection
mkdir -p logs

# Environnement Python : sans venv, uvicorn ne trouverait pas le paquet
if [ ! -f .venv/bin/activate ]; then
  echo "Environnement .venv introuvable : créer le venv et installer les dépendances avant de lancer." >&2
  exit 1
fi
source .venv/bin/activate

# Port 8001 déjà occupé : un ancien service répondrait à la sonde, on refuse de démarrer
if lsof -i:8001 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Le port 8001 est déjà pris : un service d'inférence tourne sans doute déjà. Arrêter l'ancien avant de relancer." >&2
  exit 1
fi

echo "Base et API..."
docker compose up -d db backend web

echo "Service d'inférence..."
uvicorn inference.app:app --port 8001 > logs/inference.log 2>&1 &
INFER=$!

echo "Interface..."
(cd web && npm run dev > ../logs/web.log 2>&1) &
WEB=$!

trap 'echo; echo "Arrêt..."; kill "$INFER" "$WEB" 2>/dev/null || true; pkill -f "uvicorn inference.app:app --port 8001" 2>/dev/null || true; pkill -f "vite" 2>/dev/null || true; exit 0' INT TERM

until curl -sf localhost:8001/v1/health > /dev/null; do sleep 1; done
echo "Prêt : http://localhost:5173  (Ctrl + C pour tout arrêter, journaux dans logs/)"
wait
