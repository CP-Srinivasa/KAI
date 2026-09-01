import { cn } from "@/lib/utils";
import { useSharedNow } from "@/lib/useSharedNow";

// DALI-F-032 — Liveness-Indikator pro Karte. Ableitung aus
// (state, generatedAt, now). Erste sichtbare Bewegung auf der Seite ist der
// gruene Pulse — Anker fuer "hier passiert was". Stale/Down/Loading sind
// statisch. Threshold default 60s (= 2x quality-refresh-Intervall).

type Props = {
  state: "loading" | "ready" | "error";
  /**
   * When the DATA was produced — never when it was fetched.
   *
   * STAB-2026-09-01 §30: several panels passed their own fetch time here
   * (`new Date(state.fetchedAt).toISOString()`), which makes `now - generatedAt`
   * zero by construction, so the badge said "live" no matter how old the payload
   * behind it was. A request timestamp cannot measure data freshness; it only
   * measures that a request happened.
   */
  generatedAt: string | null;
  /**
   * Set false when the caller cannot supply a real data timestamp. The badge
   * then reads "unbekannt" instead of claiming liveness it cannot support.
   */
  dataTimestampKnown?: boolean;
  staleAfterMs?: number;
  downAfterMs?: number;
  className?: string;
};

type Phase = "live" | "stale" | "down" | "loading" | "unknown";

const PHASE_DOT: Record<Phase, string> = {
  live: "bg-pos",
  stale: "bg-warn",
  down: "bg-neg",
  loading: "bg-fg-subtle",
  unknown: "bg-fg-subtle",
};

const PHASE_LABEL: Record<Phase, string> = {
  live: "live",
  stale: "stale",
  down: "offline",
  loading: "lädt",
  unknown: "unbekannt",
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
  dataTimestampKnown = true,
  staleAfterMs = 60_000,
  downAfterMs = 300_000,
  className,
}: Props) {
  // One shared 5s clock for all LiveDots instead of one timer per instance.
  const now = useSharedNow();

  const ageMs =
    generatedAt && state === "ready"
      ? Math.max(0, now - new Date(generatedAt).getTime())
      : null;

  let phase: Phase;
  if (state === "loading") phase = "loading";
  else if (state === "error") phase = "down";
  // §30: no usable data timestamp => UNKNOWN. Previously this fell through to
  // "live" whenever a caller handed in its own fetch time.
  else if (!dataTimestampKnown) phase = "unknown";
  else if (ageMs == null) phase = "unknown";
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
            : phase === "unknown"
              ? "Datenalter unbekannt"
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
