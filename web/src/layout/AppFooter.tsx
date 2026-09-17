import { useBackendHealth } from "@/lib/useBackendHealth";

// Schlanker Footer auf allen Seiten (SP-8 Teil 2): der EINE Platz fuer die
// Backend-Version, die vorher im BackendStatusBanner stand. Gleicher geteilter
// /health-Poller wie die Topbar-Pille, kein zusaetzlicher Abruf.
// Das untere Padding haelt ihn frei von der mobilen Bottom-Nav.
export function AppFooter() {
  const s = useBackendHealth();
  return (
    <footer className="border-t border-line-subtle px-4 xl:px-5 pt-2 pb-[calc(env(safe-area-inset-bottom)+4.25rem)] md:pb-2 text-2xs font-mono text-fg-subtle">
      {s.state === "connected" ? `Backend v${s.version}` : "Backend-Version n/v"}
    </footer>
  );
}
