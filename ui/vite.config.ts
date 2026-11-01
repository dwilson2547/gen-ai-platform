import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: the API runs on :8097 (../start.sh).
export default defineConfig({
  plugins: [react()],
  server: { port: 5177, proxy: { "/api": "http://localhost:8097" } },
});
