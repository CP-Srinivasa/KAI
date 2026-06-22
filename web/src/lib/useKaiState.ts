// Hook: poll /api/kai/state and surface a typed KaiRuntimeState for the SPA.
// Phase 1 polls every 30s; future phases will switch to SSE via /events.

import { useEffect, useState } from "react";
import { createFallbackState } from "../kai/stateResolver";
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
        // Network / 5xx: genuinely treat as OFFLINE (fail-closed, not pretending IDLE).
        const fallback = createFallbackState(
          "OFFLINE",
          err instanceof Error ? err.message : "kai state fetch failed",
        );
        setStateValue({
          state: "ready",
          data: fallback,
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
