import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// En développement, l'API est relayée sous /api, flux SSE compris : par défaut le port 8000 (API locale, ou celle du
// serveur par un tunnel SSH) ; MARS_API désigne une autre API (par exemple MARS_API=http://localhost:8765).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.MARS_API ?? "http://localhost:8000", changeOrigin: true } },
  },
});
