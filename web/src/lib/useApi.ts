import { useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export type AsyncState<T> =
  | { state: "loading"; data: null; error: null; reload: () => void }
  // fetchedAt: epoch-ms des letzten erfolgreichen Fetch → ehrliche
  // "zuletzt aktualisiert vor Xs"-Anzeige auch ohne Backend-generated_at.
  | { state: "ready"; data: T; error: null; reload: () => void; fetchedAt: number }
  | { state: "error"; data: null; error: { kind: string; message: string; status: number }; reload: () => void };

/** Opt-in exponential backoff on transient (network/server) errors, mirroring
 *  usePolling. Default off → existing callers keep their exact behaviour. */
export type UseApiRetry = { maxAttempts: number; baseMs: number };

// Transient errors worth a fast retry; auth/not-found/bad-response are terminal
// (a retry would just repeat the same failure).
const RETRYABLE_KINDS = new Set(["network", "server"]);

// Generischer Polling-Hook für async-Fetcher. Jeder aktive Dashboard-Bereich nutzt
// diesen Hook, damit Lade-, Fehler- und Refresh-Verhalten überall identisch sind.
export function useApi<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  refreshMs: number | null = 30_000,
  deps: readonly unknown[] = [],
  retry?: UseApiRetry,
): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    state: "loading",
    data: null,
    error: null,
    reload: () => {},
  });
  const abortRef = useRef<AbortController | null>(null);
  const tickRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let attempt = 0;
    let retryTimer: number | undefined;

    const load = async () => {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const data = await fetcher(ctrl.signal);
        if (cancelled) return;
        attempt = 0;
        setState({ state: "ready", data, error: null, reload, fetchedAt: Date.now() });
      } catch (e) {
        if (cancelled) return;
        const errInfo =
          e instanceof ApiError
            ? { kind: e.kind, message: e.message, status: e.status }
            : { kind: "unknown", message: (e as Error).message, status: 0 };
        setState({ state: "error", data: null, error: errInfo, reload });
        // Fast backoff retry for transient errors (opt-in); the regular interval
        // still ticks independently. Terminal errors (auth/404) are not retried.
        if (retry && RETRYABLE_KINDS.has(errInfo.kind) && attempt < retry.maxAttempts) {
          const backoff = retry.baseMs * Math.pow(2, attempt);
          attempt += 1;
          retryTimer = window.setTimeout(load, backoff);
        }
      }
    };

    const reload = () => {
      tickRef.current += 1;
      void load();
    };

    void load();
    let id: number | undefined;
    if (refreshMs != null && refreshMs > 0) {
      id = window.setInterval(load, refreshMs);
    }
    return () => {
      cancelled = true;
      abortRef.current?.abort();
      if (id != null) window.clearInterval(id);
      if (retryTimer != null) window.clearTimeout(retryTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshMs, retry?.maxAttempts, retry?.baseMs, ...deps]);

  return state;
}
