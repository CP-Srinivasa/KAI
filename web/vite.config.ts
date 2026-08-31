import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

import { localDevBanner, resolveApiBase } from "./src/lib/resolveApiBase.js";

// Backend base URL fuer den DEV-SERVER-PROXY. Wird NICHT ins Bundle injiziert:
// produktiv fetcht das Frontend Same-Origin relativ (Asset-Base /dashboard/).
// H6 Dev-Guard 2026-08-31: kein stiller Rueckfall auf 127.0.0.1 mehr. Ohne
// VITE_KAI_API_BASE und ohne KAI_ALLOW_LOCAL_DEV_BACKEND=1 bricht der DEV-Server
// ab; Build und Preview bleiben unberuehrt. Siehe src/lib/resolveApiBase.ts.
export default defineConfig((configEnv) => {
  const { command, mode } = configEnv;
  const isPreview = Boolean((configEnv as { isPreview?: boolean }).isPreview);
  const env = loadEnv(mode, process.cwd(), "");
  const resolved = resolveApiBase(command, env, isPreview);
  if (resolved.kind === "local-dev-optin") {
    console.warn(localDevBanner());
  }

  const proxied = {
    target: resolved.base,
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
    // Nur wenn eine Base feststeht (Dev-Server). Bei Build/Preview entfaellt der
    // server-Block vollstaendig -> im Produktionspfad existiert kein localhost.
    ...(resolved.base
      ? {
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
        }
      : {}),
  };
});
