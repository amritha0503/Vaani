import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Everything is bundled into backend/static/app and served by FastAPI. Nothing
// is fetched from a CDN at runtime -- the console has to draw with the network
// cable pulled, which is the whole resilience claim, and a script tag pointing
// at unpkg would quietly break it.
const API = "http://127.0.0.1:8000";
const proxied = [
  "/queue", "/calls", "/exposure", "/audit", "/demo", "/dispatch", "/roads",
  "/surface.png", "/surface", "/health", "/intake", "/twilio", "/voice",
  "/clusters",
];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/static/app/",
  build: {
    outDir: "../backend/static/app",
    emptyOutDir: true,
    // One CSS file and one JS file, named without hashes: the operator refreshes
    // a laptop, not a CDN, and stable names make the served copy inspectable.
    rollupOptions: {
      output: {
        entryFileNames: "console.js",
        chunkFileNames: "console-[name].js",
        assetFileNames: "console.[ext]",
      },
    },
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      proxied.map((p) => [p, { target: API, changeOrigin: true }])
    ),
  },
});
