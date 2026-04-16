import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Backend base URL for proxy + same-origin fetches. Override via VITE_KAI_API_BASE.
// LAN-reachable dev: set host:true (already below) and VITE_KAI_API_BASE to LAN IP.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiBase = env.VITE_KAI_API_BASE || "http://127.0.0.1:8000";

  const proxied = {
    target: apiBase,
    changeOrigin: true,
    secure: false,
  };

  return {
    plugins: [react()],
    // Serve assets under /dashboard/ in production so FastAPI can mount the
    // build output at /dashboard without path surgery. Dev server stays at /.
    base: mode === "production" ? "/dashboard/" : "/",
    resolve: {
      alias: { "@": path.resolve(__dirname, "src") },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: false,
    },
    server: {
      host: true,
      port: 5173,
      proxy: {
        "/health": proxied,
        "/dashboard/api": proxied,
        "/operator": proxied,
        "/alerts": proxied,
        "/sources": proxied,
        "/research": proxied,
        "/query": proxied,
      },
    },
  };
});
