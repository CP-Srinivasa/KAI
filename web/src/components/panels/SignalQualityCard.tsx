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

function fmtPct(v: number | null | undefined): string {
  return v != null ? `${v.toFixed(2)}%` : "—";
}

function SignalQualityCardImpl({ data, state, generatedAt }: Props) {
  const rows = useMemo<Array<[string, string, string?]>>(
    () =>
      data
        ? [
            ["Actionable Rate", fmtPct(data.actionable_rate_pct)],
            ["False Positive", fmtPct(data.false_positive_pct), "neg"],
            ["High-P Hit Rate", fmtPct(data.high_priority_hit_rate_pct), "pos"],
            ["Low-P Hit Rate", fmtPct(data.low_priority_hit_rate_pct)],
            ["Directional Docs", String(data.directional_count)],
          ]
        : [],
    [data],
  );

  return (
    <Card padded>
      <CardHeader
        title="Signal-Qualität"
        right={<LiveDot state={state} generatedAt={generatedAt} />}
      />
      {data ? (
        <div className="space-y-1.5">
          {rows.map(([k, v, tone]) => (
            <div key={k} className="flex items-center justify-between text-xs">
              <span className="text-fg-muted">{k}</span>
              <span
                className={cn(
                  "font-mono font-semibold",
                  tone === "pos" && "text-pos",
                  tone === "neg" && "text-neg",
                )}
              >
                {v}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div className="py-4 text-center text-xs text-fg-subtle">
          Quality-Report lädt …
        </div>
      )}
    </Card>
  );
}

export const SignalQualityCard = memo(SignalQualityCardImpl);
