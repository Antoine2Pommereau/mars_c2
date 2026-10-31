import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// En développement, l'API est relayée sous /api, flux SSE compris : par défaut le port 8000 (API locale, ou celle du
// serveur par un tunnel SSH) ; MARS_API désigne une autre API (par exemple MARS_API=http://localhost:8765).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Empreinte du commit de l'image (web/Dockerfile, MARS_VERSION), montrée par l'indicateur « Version »
  define: { __MARS_VERSION__: JSON.stringify(process.env.MARS_VERSION ?? "dev") },
  // Paquet découpé (audit final, partie A du lot Z5) : MapLibre et React dans leurs fichiers, qui changent rarement et
  // restent en cache d'une version à l'autre ; la fiche de signalement chargée seulement sur sa page (main.tsx)
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/maplibre-gl")) return "maplibre";
          if (/node_modules\/(react|react-dom|scheduler|@tanstack)\//.test(id)) return "react";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.MARS_API ?? "http://localhost:8000", changeOrigin: true } },
  },
});
