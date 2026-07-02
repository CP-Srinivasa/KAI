import { useBackendHealth } from "@/lib/useBackendHealth";

// Ehrliche, persistent sichtbare Anzeige des Backend-Zustands.
// Grün = verbunden. Rot = offline.
// Auth läuft jetzt über Cloudflare Access (vor dem Tunnel) — keine API-Key-
// Eingabe mehr im Browser nötig.
export function BackendStatusBanner() {
  const s = useBackendHealth();

  let tone = "bg-bg-2 text-fg-muted";
  let label = "Backend wird geprüft …";
  let dot = "bg-fg-subtle";
  if (s.state === "connected") {
    tone = "bg-bg-2 text-fg-muted";
    dot = "bg-pos";
    label = `Backend verbunden · v${s.version}`;
  } else if (s.state === "unauthorized") {
    // Sollte mit CF Access nicht mehr auftreten — /health ist öffentlich.
    // Falls doch: CF-Access-Session abgelaufen → reload erzwingt Re-Login.
    tone = "bg-warn/10 text-warn";
    dot = "bg-warn";
    label = "Backend erreichbar, aber Auth fehlgeschlagen — Seite neu laden";
  } else if (s.state === "offline") {
    tone = "bg-neg/10 text-neg";
    dot = "bg-neg";
    label = `Backend offline · ${s.detail ?? ""}`.trim();
  }

  return (
    <div
      className={`min-h-6 flex items-center gap-2 px-5 text-2xs border-b border-line-subtle ${tone}`}
      role="status"
      aria-live="polite"
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full shrink-0 ${dot}`} />
      <span className="font-mono min-w-0 truncate">{label}</span>
    </div>
  );
}
