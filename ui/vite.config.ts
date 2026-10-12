import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the API; production build is served by the FastAPI orchestrator.
export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
});
