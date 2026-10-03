#!/bin/bash
# Démarre MARS C2 : base et API (Docker), service d'inférence (GPU du Mac), interface (Vite)
cd "$(dirname "$0")"
source .venv/bin/activate

echo "Base et API..."
docker compose up -d db backend web

echo "Service d'inférence..."
uvicorn inference.app:app --port 8001 > logs/inference.log 2>&1 &
INFER=$!

echo "Interface..."
(cd web && npm run dev > ../logs/web.log 2>&1) &
WEB=$!

trap 'echo; echo "Arrêt..."; kill $INFER $WEB 2>/dev/null; pkill -f "vite" 2>/dev/null; exit 0' INT TERM

until curl -sf localhost:8001/v1/health > /dev/null; do sleep 1; done
echo "Prêt : http://localhost:5173  (Ctrl + C pour tout arrêter, journaux dans logs/)"
wait
