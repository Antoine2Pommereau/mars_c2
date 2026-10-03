import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// En développement, l'API est relayée sous /api, flux SSE compris.
// Cible par défaut le port 8000 ; surchargeable par API_PROXY_TARGET (utile pour viser un backend de worktree).
const target = process.env.API_PROXY_TARGET ?? "http://localhost:8000";
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": { target, changeOrigin: true } },
  },
});
