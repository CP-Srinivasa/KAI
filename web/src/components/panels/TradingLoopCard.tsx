import { memo, useMemo } from "react";
import { Activity } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import type { DashboardQuality } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = { data: DashboardQuality | null };

const LOOP_STATUS_TONE: Record<string, "pos" | "neg" | "warn" | "muted" | "info"> = {
  completed: "pos",
  no_signal: "muted",
  no_market_data: "warn",
  stale_data: "warn",
  risk_rejected: "warn",
  consensus_rejected: "neg",
  priority_rejected: "muted",
  order_failed: "neg",
};

const TONE_BG: Record<string, string> = {
  pos: "bg-pos",
  neg: "bg-neg",
  warn: "bg-warn",
  muted: "bg-fg-subtle/40",
  info: "bg-info",
};

function TradingLoopCardImpl({ data }: Props) {
  const { entries, total } = useMemo(() => {
    const list = data
      ? Object.entries(data.loop_status_counts).sort((a, b) => b[1] - a[1])
      : [];
    const sum = list.reduce((acc, [, v]) => acc + v, 0);
    return { entries: list, total: sum };
  }, [data]);

  return (
    <Card padded>
      <CardHeader
        title="Trading Loop Status"
        subtitle={total > 0 ? `${total} Cycles im Fenster` : undefined}
        right={
          <Badge tone="muted">
            <Activity size={10} />
            live
          </Badge>
        }
      />
      {entries.length > 0 ? (
        <div className="space-y-2">
          <div
            className="h-2 w-full rounded-sm overflow-hidden flex bg-bg-3"
            role="img"
            aria-label={`Loop-Status-Verteilung: ${entries.map(([k, v]) => `${k} ${v}`).join(", ")}`}
          >
            {entries.map(([k, v]) => {
              const tone = LOOP_STATUS_TONE[k] ?? "muted";
              const pct = total > 0 ? (v / total) * 100 : 0;
              if (pct <= 0) return null;
              return (
                <div
                  key={k}
                  className={TONE_BG[tone]}
                  style={{ width: `${pct}%` }}
                  title={`${k}: ${v} (${pct.toFixed(1)}%)`}
                />
              );
            })}
          </div>
          <div className="space-y-1 pt-1">
            {entries.map(([k, v]) => {
              const tone = LOOP_STATUS_TONE[k] ?? "muted";
              const pct = total > 0 ? (v / total) * 100 : 0;
              return (
                <div key={k} className="flex items-center justify-between text-xs">
                  <span className="inline-flex items-center gap-2 text-fg-muted font-mono">
                    <span
                      className={cn("inline-block h-2 w-2 rounded-xs", TONE_BG[tone])}
                      aria-hidden
                    />
                    {k}
                  </span>
                  <span className="font-mono font-semibold tabular-nums">
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
      ) : (
        <div className="py-4 text-center text-xs text-fg-subtle">
          Noch keine Cycles im aktuellen Fenster
        </div>
      )}
    </Card>
  );
}

export const TradingLoopCard = memo(TradingLoopCardImpl);
