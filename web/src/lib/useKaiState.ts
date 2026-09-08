// Hook: poll /api/kai/state and surface a typed KaiRuntimeState for the SPA.
// Phase 1 polls every 30s; future phases will switch to SSE via /events.

import { useEffect, useState } from "react";
import type { KaiRuntimeState } from "../kai/types";

type KaiStateState =
  | { state: "loading" }
  | { state: "ready"; data: KaiRuntimeState }
  | { state: "error"; error: { kind: string; message: string } };

const REFRESH_INTERVAL_MS = 30_000;

export function useKaiState(): KaiStateState {
  const [stateValue, setStateValue] = useState<KaiStateState>({ state: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function fetchOnce(): Promise<void> {
      try {
        const res = await fetch("/api/kai/state", { headers: { Accept: "application/json" } });
        if (!res.ok) {
          // Auth/permission failures are NOT "node offline" — surface them as a
          // real error so the header shows an honest "unavailable" pill instead
          // of a fake OFFLINE state that looks like a valid runtime status.
          if (res.status === 401 || res.status === 403) {
            if (!cancelled) {
              setStateValue({
                state: "error",
                error: { kind: "unauthorized", message: `HTTP ${res.status}` },
              });
            }
            return;
          }
          throw new Error(`HTTP ${res.status}`);
        }
        const body = (await res.json()) as KaiRuntimeState;
        if (cancelled) return;
        setStateValue({ state: "ready", data: body });
      } catch (err) {
        if (cancelled) return;
        // 2026-09-08: Frueher wurde hier ein OFFLINE-Zustand ERFUNDEN und als
        // `state: "ready"` gesetzt. Der Header zeigte daraufhin `Live · OFFLINE`,
        // als waere OFFLINE ein gemessener Laufzeitzustand des Knotens.
        // Tatsaechlich ist nur der Request gescheitert — ueber den Knoten wissen
        // wir dann NICHTS. Ein Fehler, der wie eine Messung aussieht, ist teurer
        // als ein sichtbarer Ausfall. Der Header fuehrt fuer genau diesen Fall
        // bereits "KAI · n/v" (fail-closed); konsistent zum 401/403-Zweig oben.
        setStateValue({
          state: "error",
          error: {
            kind: "unavailable",
            message: err instanceof Error ? err.message : "kai state fetch failed",
          },
        });
      }
    }

    fetchOnce();
    const id = window.setInterval(fetchOnce, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  return stateValue;
}
