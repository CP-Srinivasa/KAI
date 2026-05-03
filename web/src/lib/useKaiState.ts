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
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = (await res.json()) as KaiRuntimeState;
        if (!cancelled) setStateValue({ state: "ready", data: body });
      } catch (err) {
        if (cancelled) return;
        // Fail-closed: surface ERROR rather than pretending IDLE.
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
