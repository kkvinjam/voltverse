import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In local development `npm run dev` proxies /api to the local gateway started by local/docker-compose.yml.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": { target: process.env.VV_API ?? "http://127.0.0.1:8080", changeOrigin: true } },
  },
  build: { outDir: "dist", assetsDir: "assets", sourcemap: false },
});
