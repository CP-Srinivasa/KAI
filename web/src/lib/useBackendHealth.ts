import { useEffect, useRef, useState } from "react";
import { ApiError, fetchHealth } from "./api";

export type BackendStatus =
  | { state: "checking"; version: null; detail: null }
  | { state: "connected"; version: string; detail: null }
  | { state: "unauthorized"; version: null; detail: string }
  | { state: "offline"; version: null; detail: string };

const INITIAL: BackendStatus = { state: "checking", version: null, detail: null };

export function useBackendHealth(pollMs: number = 30_000): BackendStatus {
  const [status, setStatus] = useState<BackendStatus>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function ping() {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const res = await fetchHealth(ctrl.signal);
        if (cancelled) return;
        setStatus({ state: "connected", version: res.version, detail: null });
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && (e.kind === "unauthorized" || e.kind === "forbidden")) {
          setStatus({ state: "unauthorized", version: null, detail: e.message });
        } else {
          const msg = e instanceof Error ? e.message : "network error";
          setStatus({ state: "offline", version: null, detail: msg });
        }
      }
    }

    ping();
    const id = window.setInterval(ping, pollMs);
    return () => {
      cancelled = true;
      abortRef.current?.abort();
      window.clearInterval(id);
    };
  }, [pollMs]);

  return status;
}
