import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

// DALI-F-032 — Liveness-Indikator pro Karte. Ableitung aus
// (state, generatedAt, now). Erste sichtbare Bewegung auf der Seite ist der
// gruene Pulse — Anker fuer "hier passiert was". Stale/Down/Loading sind
// statisch. Threshold default 60s (= 2x quality-refresh-Intervall).

type Props = {
  state: "loading" | "ready" | "error";
  generatedAt: string | null;
  staleAfterMs?: number;
  downAfterMs?: number;
  className?: string;
};

type Phase = "live" | "stale" | "down" | "loading";

const PHASE_DOT: Record<Phase, string> = {
  live: "bg-pos",
  stale: "bg-warn",
  down: "bg-neg",
  loading: "bg-fg-subtle",
};

const PHASE_LABEL: Record<Phase, string> = {
  live: "live",
  stale: "stale",
  down: "offline",
  loading: "lädt",
};

function relativeAge(ms: number): string {
  if (ms < 1000) return "jetzt";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `vor ${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `vor ${m} min`;
  const h = Math.floor(m / 60);
  return `vor ${h} h`;
}

export function LiveDot({
  state,
  generatedAt,
  staleAfterMs = 60_000,
  downAfterMs = 300_000,
  className,
}: Props) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(id);
  }, []);

  const ageMs =
    generatedAt && state === "ready"
      ? Math.max(0, now - new Date(generatedAt).getTime())
      : null;

  let phase: Phase;
  if (state === "loading") phase = "loading";
  else if (state === "error") phase = "down";
  else if (ageMs == null) phase = "loading";
  else if (ageMs > downAfterMs) phase = "down";
  else if (ageMs > staleAfterMs) phase = "stale";
  else phase = "live";

  const microcopy =
    phase === "live" && ageMs != null
      ? `live · ${relativeAge(ageMs)}`
      : phase === "stale" && ageMs != null
        ? `stale · ${relativeAge(ageMs)}`
        : phase === "down" && ageMs != null
          ? `offline · letzter Tick ${relativeAge(ageMs)}`
          : phase === "down"
            ? "offline"
            : "lädt …";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-xs border px-1.5 py-0.5 text-2xs font-medium",
        phase === "live" && "border-pos/25 bg-pos/10 text-pos",
        phase === "stale" && "border-warn/25 bg-warn/10 text-warn",
        phase === "down" && "border-neg/25 bg-neg/10 text-neg",
        phase === "loading" && "border-line-subtle bg-bg-2 text-fg-muted",
        className,
      )}
      title={microcopy}
      aria-label={microcopy}
    >
      <span className="relative inline-flex h-2 w-2">
        {phase === "live" && (
          <span
            className={cn(
              "absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping",
              PHASE_DOT[phase],
            )}
            aria-hidden
          />
        )}
        <span
          className={cn(
            "relative inline-flex rounded-full h-2 w-2",
            PHASE_DOT[phase],
            phase === "live" && "glow-pos",
            phase === "stale" && "glow-warn",
            phase === "down" && "glow-neg",
          )}
          aria-hidden
        />
      </span>
      <span>{PHASE_LABEL[phase]}</span>
    </span>
  );
}
