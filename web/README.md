# Interface tactique de MARS C2

React, TypeScript, Vite, Tailwind CSS, MapLibre, TanStack Query.

* Développement, avec rechargement à chaud : `npm install` puis `npm run dev`, et ouvrir http://localhost:5173.
  L'API (port 8000) est relayée sous `/api`, flux SSE compris.
* Production : `docker compose up -d --build web`, puis http://localhost:8081.
* Vérification des types : `npm run typecheck`.
