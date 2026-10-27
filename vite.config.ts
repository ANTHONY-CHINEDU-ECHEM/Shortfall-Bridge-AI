import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api/v1": "http://localhost:8000", "/cds-services": "http://localhost:8000" },
  },
  worker: { format: "es" },
  build: {
    sourcemap: true,
    target: "es2022",
    chunkSizeWarningLimit: 600,
  },
});
