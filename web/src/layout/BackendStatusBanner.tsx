import { useState } from "react";
import { useBackendHealth } from "@/lib/useBackendHealth";
import { setApiToken, hasApiToken } from "@/lib/api";

// Ehrliche, persistent sichtbare Anzeige des Backend-Zustands.
// Grün = verbunden. Orange = Auth fehlt. Rot = offline.
export function BackendStatusBanner() {
  const s = useBackendHealth();
  const [editing, setEditing] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const tokenSet = hasApiToken();

  let tone = "bg-bg-2 text-fg-muted";
  let label = "Backend wird geprüft …";
  let dot = "bg-fg-subtle";
  if (s.state === "connected") {
    tone = "bg-bg-2 text-fg-muted";
    dot = "bg-pos";
    label = `Backend verbunden · v${s.version}${tokenSet ? " · API-Key aktiv" : " · kein API-Key"}`;
  } else if (s.state === "unauthorized") {
    tone = "bg-warn/10 text-warn";
    dot = "bg-warn";
    label = "Backend erreichbar, aber API-Key fehlt oder ungültig";
  } else if (s.state === "offline") {
    tone = "bg-neg/10 text-neg";
    dot = "bg-neg";
    label = `Backend offline · ${s.detail ?? ""}`.trim();
  }

  function saveToken(e: React.FormEvent) {
    e.preventDefault();
    setApiToken(tokenInput.trim());
    setEditing(false);
    setTokenInput("");
    // Kein Reload nötig — nächster fetch nutzt neuen Token aus localStorage.
  }

  return (
    <div
      className={`min-h-6 flex items-center gap-2 px-5 text-2xs border-b border-line-subtle ${tone}`}
      role="status"
      aria-live="polite"
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${dot}`} />
      <span className="font-mono">{label}</span>
      <div className="ml-auto flex items-center gap-2">
        {editing ? (
          <form onSubmit={saveToken} className="flex items-center gap-1.5">
            <input
              type="password"
              autoFocus
              placeholder="APP_API_KEY"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              className="h-5 px-2 text-2xs font-mono rounded-xs border border-line-subtle bg-bg-1 text-fg placeholder:text-fg-subtle focus:outline-none focus:border-accent w-52"
            />
            <button
              type="submit"
              className="h-5 px-2 text-2xs font-semibold rounded-xs bg-accent text-white"
            >
              OK
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setTokenInput("");
              }}
              className="h-5 px-1.5 text-2xs text-fg-muted hover:text-fg"
            >
              ×
            </button>
          </form>
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="text-2xs underline decoration-dotted underline-offset-2 hover:text-fg"
          >
            {tokenSet ? "API-Key ändern" : "API-Key setzen"}
          </button>
        )}
        {tokenSet && !editing && (
          <button
            type="button"
            onClick={() => setApiToken("")}
            className="text-2xs text-fg-subtle hover:text-neg"
            title="API-Key entfernen"
          >
            ⌫
          </button>
        )}
      </div>
    </div>
  );
}
