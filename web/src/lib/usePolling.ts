import { useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export type PollingState<T> =
  | { state: "loading"; data: null; error: null }
  // fetchedAt: epoch-ms of the last successful fetch. Lets a panel show honest
  // "zuletzt aktualisiert vor Xs" even when the backend payload carries no
  // generated_at — no silent stale-freeze.
  | { state: "ready"; data: T; error: null; fetchedAt: number }
  | { state: "error"; data: null; error: { kind: string; message: string } };

export interface PollingOptions {
  intervalMs: number;
  pauseWhenHidden?: boolean;
  retry?: { maxAttempts: number; baseMs: number };
}

const RETRYABLE_KINDS = new Set(["network", "server"]);

export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  opts: PollingOptions,
): PollingState<T> {
  const { intervalMs, pauseWhenHidden = true, retry } = opts;
  const [state, setState] = useState<PollingState<T>>({
    state: "loading",
    data: null,
    error: null,
  });

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    let timerId: number | null = null;
    let attempt = 0;
    let abortCtrl: AbortController | null = null;

    const clearTimer = () => {
      if (timerId != null) {
        window.clearTimeout(timerId);
        timerId = null;
      }
    };

    const schedule = (delayMs: number) => {
      clearTimer();
      if (cancelled) return;
      if (pauseWhenHidden && document.visibilityState === "hidden") return;
      timerId = window.setTimeout(run, delayMs);
    };

    async function run() {
      timerId = null;
      if (cancelled) return;
      if (pauseWhenHidden && document.visibilityState === "hidden") return;

      abortCtrl?.abort();
      abortCtrl = new AbortController();
      const ctrl = abortCtrl;

      try {
        const data = await fetcherRef.current(ctrl.signal);
        if (cancelled) return;
        attempt = 0;
        setState({ state: "ready", data, error: null, fetchedAt: Date.now() });
        schedule(intervalMs);
      } catch (e) {
        if (cancelled) return;
        if (ctrl.signal.aborted) return;

        const errInfo =
          e instanceof ApiError
            ? { kind: e.kind, message: e.message }
            : { kind: "unknown", message: (e as Error).message };

        setState({ state: "error", data: null, error: errInfo });

        const retryable = retry && RETRYABLE_KINDS.has(errInfo.kind);
        if (retryable && attempt < retry.maxAttempts) {
          const backoff = retry.baseMs * Math.pow(2, attempt);
          attempt += 1;
          schedule(backoff);
        } else {
          attempt = 0;
          schedule(intervalMs);
        }
      }
    }

    function onVisibility() {
      if (cancelled) return;
      if (document.visibilityState === "visible") {
        if (timerId == null) run();
      } else if (pauseWhenHidden) {
        clearTimer();
        abortCtrl?.abort();
      }
    }

    run();
    if (pauseWhenHidden) {
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      cancelled = true;
      clearTimer();
      abortCtrl?.abort();
      if (pauseWhenHidden) {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
  }, [intervalMs, pauseWhenHidden, retry?.maxAttempts, retry?.baseMs]);

  return state;
}
