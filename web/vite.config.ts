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
      rollupOptions: {
        output: {
          // Vendor-Splitting: hält Initial-Bundle klein, cached Vendor-Chunks separat.
          // recharts entfernt (PR Dashboard-Truth/Resilience): war app-weit nur für
          // eine KPI-Sparkline genutzt und zog ~510 kB in den eager First-Paint —
          // ersetzt durch dependency-freies Inline-SVG (components/kpi/Sparkline).
          //
          // Funktions- statt Objekt-Form: Vite 8 bundelt mit rolldown, und dessen
          // `manualChunks` nimmt ausschliesslich eine Funktion ("manualChunks is not
          // a function"). Die Objekt-Form ordnete Paketnamen Chunks zu; hier wird
          // stattdessen der Modulpfad geprueft.
          //
          // Reihenfolge ist bedeutungstragend: `lucide-react` enthaelt "react" als
          // Teilstring und muss VOR der React-Regel greifen, sonst landen die Icons
          // im vendor-react-Chunk und vendor-icons bleibt leer.
          manualChunks(id: string) {
            const path = id.replace(/\\/g, "/");
            if (!path.includes("/node_modules/")) return;
            if (/\/node_modules\/lucide-react\//.test(path)) return "vendor-icons";
            // scheduler ist eine Laufzeit-Abhaengigkeit von react-dom — ohne sie
            // wandert sie in den Haupt-Chunk und der Split waere unvollstaendig.
            if (/\/node_modules\/(react|react-dom|scheduler)\//.test(path)) {
              return "vendor-react";
            }
            return;
          },
        },
      },
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
