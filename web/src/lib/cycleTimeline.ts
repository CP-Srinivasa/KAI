// Pure Helfer: Trading-Cycles → TimelineRail-Segmente (WP-3.3 / Konzept §13).
// Färbt jeden Cycle nach Ausgang, damit die jüngste Loop-Historie als
// Prozess-Flow lesbar ist. Getrennt → testbar.
import type { Tone } from "@/lib/tone";
import type { RailItem } from "@/components/viz/TimelineRail";
import type { TradingCycle } from "@/lib/api";

const FAIL = new Set(["order_failed", "consensus_rejected", "priority_rejected", "error"]);
const WARN = new Set(["risk_blocked", "diversification_blocked", "entry_mode_blocked", "stale"]);

/** Cycle-Status → Tone. completed=pos, no_signal=neutral, Fehler=neg, Block=warn. */
export function cycleTone(status: string): Tone {
  if (status === "completed") return "pos";
  if (status === "no_signal") return "neutral";
  if (FAIL.has(status)) return "neg";
  if (WARN.has(status)) return "warn";
  return "info";
}

/** Cycles → TimelineRail-Items (chronologisch, gleich breit). Pure/testbar. */
export function cyclesToRail(cycles: TradingCycle[]): RailItem[] {
  return cycles.map((c, i) => ({
    key: `${i}-${c.status}`,
    label: c.status,
    tone: cycleTone(c.status),
  }));
}
