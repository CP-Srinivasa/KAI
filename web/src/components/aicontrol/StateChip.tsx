// Ein Zustand der KI-Kontrollstation als Chip: Symbol + Label, Grund im Tooltip und fuer
// Screenreader. `null` heisst "unbekannt" -- nie ein leerer Chip.

import { STATE_META, type AiControlState, type StateTone } from "@/lib/aiControl";
import { cn } from "@/lib/utils";

const TONE: Record<StateTone, string> = {
  pos: "text-pos border-pos/40",
  info: "text-info border-info/40",
  warn: "text-warn border-warn/50",
  neg: "text-neg border-neg/60 glow-neg",
  ai: "text-ai border-ai/50",
  muted: "text-fg-subtle border-line",
};

export function StateChip({ state, reason }: { state: AiControlState | null; reason: string | null }) {
  const meta = state ? STATE_META[state] : { label: "UNBEKANNT", symbol: "?", tone: "muted" as const };
  return (
    <span
      title={reason ?? meta.label}
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm border px-1.5 py-0.5 font-mono text-2xs tracking-wider",
        TONE[meta.tone],
      )}
    >
      <span aria-hidden>{meta.symbol}</span>
      <span>{meta.label}</span>
      {reason ? <span className="sr-only"> – {reason}</span> : null}
    </span>
  );
}
