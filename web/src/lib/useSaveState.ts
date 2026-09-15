// Speicher-/Sende-Rueckmeldung als EIN Muster (DALI v2.1, Sprint-Prompt §6):
// sofort "Wird gespeichert …", "Gespeichert" erst nach erfolgreicher Antwort,
// Fehler mit Wiederholungsmoeglichkeit. Reine Zustandslogik, kein React-Render —
// testbar ohne DOM (useSaveState.test.ts prueft den Reducer).
import { useCallback, useEffect, useRef, useState } from "react";

export type SaveState =
  | { kind: "idle" }
  | { kind: "saving"; label: string }
  | { kind: "saved"; label: string; detail?: string }
  | { kind: "error"; label: string; message: string };

export type SaveEvent =
  | { type: "start"; label: string }
  | { type: "ok"; label: string; detail?: string }
  | { type: "fail"; label: string; message: string }
  | { type: "reset" };

/** Reiner Uebergang — was der Operator in jedem Moment sieht. */
export function saveReducer(_prev: SaveState, ev: SaveEvent): SaveState {
  switch (ev.type) {
    case "start":
      return { kind: "saving", label: ev.label };
    case "ok":
      return { kind: "saved", label: ev.label, detail: ev.detail };
    case "fail":
      return { kind: "error", label: ev.label, message: ev.message };
    case "reset":
    default:
      return { kind: "idle" };
  }
}

/** Deutscher Klartext je Zustand — Text UND Ton, nie nur Farbe. */
export function saveStateText(s: SaveState): string {
  switch (s.kind) {
    case "saving":
      return `${s.label} · wird gespeichert …`;
    case "saved":
      return `${s.label} · gespeichert${s.detail ? ` · ${s.detail}` : ""}`;
    case "error":
      return `${s.label} · fehlgeschlagen: ${s.message}`;
    case "idle":
    default:
      return "";
  }
}

export function useSaveState(options: { savedHideAfterMs?: number } = {}) {
  const { savedHideAfterMs = 4_000 } = options;
  const [state, setState] = useState<SaveState>({ kind: "idle" });
  const lastRun = useRef<(() => Promise<void>) | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current != null) window.clearTimeout(timer.current);
    };
  }, []);

  /** Fuehrt `fn` aus und spiegelt Start/Erfolg/Fehler. Erfolg blendet sich
   *  nach `savedHideAfterMs` aus; ein Fehler bleibt stehen, bis erneut versucht
   *  oder zurueckgesetzt wird. */
  const run = useCallback(
    async (label: string, fn: () => Promise<string | void>) => {
      const job = async () => {
        if (timer.current != null) window.clearTimeout(timer.current);
        setState(saveReducer({ kind: "idle" }, { type: "start", label }));
        try {
          const detail = await fn();
          setState((p) => saveReducer(p, { type: "ok", label, detail: detail ?? undefined }));
          timer.current = window.setTimeout(
            () => setState((p) => (p.kind === "saved" ? { kind: "idle" } : p)),
            savedHideAfterMs,
          );
        } catch (e) {
          const message = e instanceof Error ? e.message : String(e);
          setState((p) => saveReducer(p, { type: "fail", label, message }));
        }
      };
      lastRun.current = job;
      await job();
    },
    [savedHideAfterMs],
  );

  /** Wiederholt den letzten Versuch — die "Wiederholen"-Schaltflaeche. */
  const retry = useCallback(async () => {
    if (lastRun.current) await lastRun.current();
  }, []);

  const reset = useCallback(() => setState({ kind: "idle" }), []);

  return { state, run, retry, reset, busy: state.kind === "saving" };
}
