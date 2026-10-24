#!/bin/bash
# Démarre MARS C2 sur le Mac : base et API (Docker), interface de développement (Vite)
cd "$(dirname "$0")"

echo "Base et API..."
docker compose up -d db backend web

echo "Interface..."
mkdir -p logs
(cd web && npm run dev > ../logs/web.log 2>&1) &
WEB=$!

trap 'echo; echo "Arrêt..."; kill $WEB 2>/dev/null; pkill -f "vite" 2>/dev/null; exit 0' INT TERM

until curl -sf localhost:8000/api/health > /dev/null; do sleep 1; done
echo "Prêt : http://localhost:5173  (Ctrl + C pour tout arrêter, journaux dans logs/)"
wait
