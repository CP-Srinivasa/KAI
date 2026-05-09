import { memo, useMemo } from "react";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import type { DashboardQuality } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  data: DashboardQuality | null;
  state: "loading" | "ready" | "error";
  generatedAt: string | null;
};

// DALI-F-031 — Vier semantische Klassen statt drei Tones.
// Healthy: System tut, was es soll (kein Trade-Trigger ist normal).
// Risk-Veto: Schutz hat aktiv blockiert — eigene Klasse, NICHT Fehler.
// Data-Issue: Pipeline-Defekt. Failure: echter Bug.
type OutcomeClass = "healthy" | "risk" | "data" | "fail";

const OUTCOME_CLASS: Record<string, OutcomeClass> = {
  completed: "healthy",
  no_signal: "healthy",
  priority_rejected: "healthy",
  risk_rejected: "risk",
  consensus_rejected: "risk",
  no_market_data: "data",
  stale_data: "data",
  order_failed: "fail",
};

const CLASS_BAR: Record<OutcomeClass, string> = {
  healthy: "bg-info/70",
  risk: "bg-ai/80",
  data: "bg-warn",
  fail: "bg-neg",
};

const CLASS_DOT: Record<OutcomeClass, string> = {
  healthy: "bg-info",
  risk: "bg-ai",
  data: "bg-warn",
  fail: "bg-neg",
};

const CLASS_LABEL: Record<OutcomeClass, string> = {
  healthy: "Healthy",
  risk: "Risk-Veto",
  data: "Daten-Issue",
  fail: "Execution-Fehler",
};

const OUTCOME_LABEL: Record<string, string> = {
  completed: "Trade ausgelöst",
  no_signal: "Kein Signal",
  priority_rejected: "Priority-Filter",
  no_market_data: "Keine Marktdaten",
  stale_data: "Daten zu alt",
  risk_rejected: "Risk-Filter blockiert",
  consensus_rejected: "Kein Konsens",
  order_failed: "Order-Fehler",
};

const OUTCOME_HINT: Record<string, string> = {
  completed: "Voll-Cycle inkl. Order-Submit",
  no_signal: "Cycle sauber, aber keine Trigger-Bedingung erfüllt",
  priority_rejected: "Signal unter Priority-Threshold — bewusst übersprungen",
  no_market_data: "Tick-Source antwortet nicht",
  stale_data: "Letzter Tick älter als Stale-Threshold",
  risk_rejected: "Risk-Engine hat aktiv geschützt (Exposure/DD/Correlation)",
  consensus_rejected: "Multi-Agent-Consensus unter Schwellwert",
  order_failed: "Execution-Layer-Fehler — Logs prüfen",
};

function classOf(key: string): OutcomeClass {
  return OUTCOME_CLASS[key] ?? "healthy";
}

function TradingLoopCardImpl({ data, state, generatedAt }: Props) {
  const { entries, total, summary } = useMemo(() => {
    const list = data
      ? Object.entries(data.loop_status_counts).sort((a, b) => b[1] - a[1])
      : [];
    const sum = list.reduce((acc, [, v]) => acc + v, 0);
    const buckets: Record<OutcomeClass, number> = {
      healthy: 0,
      risk: 0,
      data: 0,
      fail: 0,
    };
    list.forEach(([k, v]) => {
      buckets[classOf(k)] += v;
    });
    return { entries: list, total: sum, summary: buckets };
  }, [data]);

  return (
    <Card padded>
      <CardHeader
        title="Trading Loop Status"
        subtitle={total > 0 ? `${total} Cycles im Fenster` : undefined}
        right={<LiveDot state={state} generatedAt={generatedAt} />}
      />
      {entries.length > 0 ? (() => {
        // Synthwave Stufe 2: die dominante Klasse pulsiert dezent (atmender Glow).
        const dominant = (["healthy", "risk", "data", "fail"] as const).reduce(
          (acc, k) => (summary[k] > summary[acc] ? k : acc),
          "healthy" as const,
        );
        return (
        <div className="space-y-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-2xs">
            {(["healthy", "risk", "data", "fail"] as const).map((c) => {
              const v = summary[c];
              const pct = total > 0 ? (v / total) * 100 : 0;
              const isDominant = c === dominant && v > 0;
              const pulseColor =
                c === "healthy" ? "var(--info)"
                : c === "risk" ? "var(--ai)"
                : c === "data" ? "var(--warn)"
                : "var(--neg)";
              // 2026-05-08 Operator-Folge: kritische Klassen atmen permanent —
              // unabhaengig davon ob sie dominant sind. fail = neg, data = warn.
              // Operator soll immer sehen: hier ist was zu beachten.
              const isAttention =
                v > 0 && (c === "fail" || c === "data");
              const attentionClass =
                c === "fail" ? "attention-breathe-neg"
                : c === "data" ? "attention-breathe-warn"
                : "";
              return (
                <div
                  key={c}
                  className={cn(
                    "rounded-xs border border-line-subtle bg-bg-2 px-2 py-1.5",
                    isDominant && !isAttention && "neon-pulse",
                    isAttention && attentionClass,
                  )}
                  style={isDominant && !isAttention ? ({ ["--neon-pulse-color" as string]: pulseColor } as React.CSSProperties) : undefined}
                >
                  <div className="flex items-center gap-1.5">
                    <span
                      className={cn("h-2 w-2 rounded-xs", CLASS_DOT[c])}
                      aria-hidden
                    />
                    <span className="text-fg-muted truncate">
                      {CLASS_LABEL[c]}
                    </span>
                  </div>
                  <div className="mt-0.5 font-mono font-semibold tabular-nums text-fg">
                    {pct.toFixed(1)}%
                    <span className="ml-1 text-fg-subtle font-normal">
                      ({v})
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          <div
            className="h-2.5 w-full rounded-sm overflow-hidden flex bg-bg-3"
            role="img"
            aria-label={`Loop-Verteilung: ${entries
              .map(([k, v]) => `${OUTCOME_LABEL[k] ?? k} ${v}`)
              .join(", ")}`}
          >
            {entries.map(([k, v]) => {
              const c = classOf(k);
              const pct = total > 0 ? (v / total) * 100 : 0;
              if (pct <= 0) return null;
              return (
                <div
                  key={k}
                  className={cn(CLASS_BAR[c], "relative")}
                  style={{ width: `${pct}%` }}
                  title={`${OUTCOME_LABEL[k] ?? k}: ${v} (${pct.toFixed(1)}%) — ${k}`}
                />
              );
            })}
          </div>

          <div className="space-y-1 pt-1">
            {entries.map(([k, v]) => {
              const c = classOf(k);
              const pct = total > 0 ? (v / total) * 100 : 0;
              return (
                <div
                  key={k}
                  className="flex items-center justify-between text-xs"
                  title={OUTCOME_HINT[k] ?? k}
                >
                  <span className="inline-flex items-center gap-2 text-fg-muted min-w-0">
                    <span
                      className={cn(
                        "inline-block h-2 w-2 rounded-xs shrink-0",
                        CLASS_DOT[c],
                      )}
                      aria-hidden
                    />
                    <span className="truncate">{OUTCOME_LABEL[k] ?? k}</span>
                    <span className="text-2xs text-fg-subtle font-mono shrink-0 hidden md:inline">
                      {k}
                    </span>
                  </span>
                  <span className="font-mono font-semibold tabular-nums shrink-0">
                    {v}
                    <span className="ml-1.5 text-2xs text-fg-subtle font-normal">
                      {pct.toFixed(0)}%
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
        );
      })() : (
        <div className="py-4 text-center text-xs text-fg-subtle">
          Noch keine Cycles im aktuellen Fenster
        </div>
      )}
    </Card>
  );
}

export const TradingLoopCard = memo(TradingLoopCardImpl);
