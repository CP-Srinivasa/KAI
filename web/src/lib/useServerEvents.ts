import { useEffect, useRef, useState } from "react";

export type ServerEventPayload = {
  event: string;
  ts: string;
  [key: string]: unknown;
};

export type ServerEventsState =
  | { state: "connecting" }
  | { state: "open" }
  | { state: "error"; attempt: number; retryInMs: number };

type Options = {
  events: readonly string[];
  enabled?: boolean;
  initialBackoffMs?: number;
  maxBackoffMs?: number;
};

const DEFAULT_INITIAL_BACKOFF = 1_000;
const DEFAULT_MAX_BACKOFF = 30_000;

// NEO-P-005: live-push channel for the operator dashboard. EventSource
// handles base-level reconnects, but the browser resets its own attempt
// counter on every successful `open`, so a flapping tunnel would hammer
// the backend. We layer explicit exponential backoff on top and reset it
// only on a *clean* open that delivered at least one message.
export function useServerEvents(
  url: string,
  onEvent: (evt: ServerEventPayload) => void,
  opts: Options,
): ServerEventsState {
  const { events, enabled = true, initialBackoffMs = DEFAULT_INITIAL_BACKOFF, maxBackoffMs = DEFAULT_MAX_BACKOFF } = opts;
  const [state, setState] = useState<ServerEventsState>({ state: "connecting" });
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const eventsKey = events.join("|");

  useEffect(() => {
    if (!enabled) return;

    let es: EventSource | null = null;
    let retryTimer: number | null = null;
    let attempt = 0;
    let receivedSinceOpen = false;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      setState((prev) => (prev.state === "open" ? prev : { state: "connecting" }));
      es = new EventSource(url);

      es.onopen = () => {
        if (cancelled) return;
        receivedSinceOpen = false;
        setState({ state: "open" });
      };

      const handle = (ev: MessageEvent) => {
        if (cancelled) return;
        receivedSinceOpen = true;
        try {
          const parsed = JSON.parse(ev.data) as Record<string, unknown>;
          onEventRef.current({ event: ev.type, ...parsed } as ServerEventPayload);
        } catch {
          // Malformed payload — drop silently; keepalive comments never reach here.
        }
      };

      for (const name of events) {
        es.addEventListener(name, handle as EventListener);
      }

      es.onerror = () => {
        if (cancelled) return;
        es?.close();
        es = null;
        // Only escalate backoff when an open connection produced nothing —
        // a tunnel idle-timeout after healthy traffic is not a flap.
        if (!receivedSinceOpen) attempt += 1;
        else attempt = 1;
        const delay = Math.min(initialBackoffMs * 2 ** (attempt - 1), maxBackoffMs);
        setState({ state: "error", attempt, retryInMs: delay });
        retryTimer = window.setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      es?.close();
    };
  }, [url, enabled, eventsKey, initialBackoffMs, maxBackoffMs]);

  return state;
}
