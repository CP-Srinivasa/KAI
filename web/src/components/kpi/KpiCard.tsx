import type { ReactNode } from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/Primitives";
import { LineChart, Line, ResponsiveContainer, YAxis } from "recharts";

type Props = {
  label: string;
  value: ReactNode;
  unit?: string;
  delta?: number;
  deltaLabel?: string;
  helper?: ReactNode;
  spark?: { x: number; y: number }[];
  tone?: "pos" | "neg" | "neutral" | "warn" | "info" | "ai";
  icon?: ReactNode;
  target?: number;
  valueNumeric?: number;
  gapUnit?: string;
};

export function KpiCard({
  label,
  value,
  unit,
  delta,
  deltaLabel,
  helper,
  spark,
  tone = "neutral",
  icon,
  target,
  valueNumeric,
  gapUnit = "",
}: Props) {
  const deltaTone = delta === undefined ? "neutral" : delta > 0 ? "pos" : delta < 0 ? "neg" : "neutral";
  const stroke = strokeFor(tone);

  const hasGap = target !== undefined && valueNumeric !== undefined && Number.isFinite(valueNumeric);
  const gap = hasGap ? (valueNumeric as number) - (target as number) : undefined;
  const progressPct = hasGap && (target as number) !== 0
    ? Math.max(0, Math.min(100, ((valueNumeric as number) / (target as number)) * 100))
    : undefined;
  const gapTone: "pos" | "neg" | "warn" =
    gap === undefined ? "warn" : gap >= 0 ? "pos" : "neg";

  return (
    <Card
      className={cn(
        "relative overflow-hidden border-l-2",
        tone === "pos" && "border-l-pos/70 glow-border-pos",
        // 2026-05-08 Operator-Folge: KPI mit neg/warn-Tone atmet permanent —
        // Operator sieht sofort wo etwas unter Threshold liegt.
        tone === "neg" && "border-l-neg/70 glow-border-neg attention-breathe-neg",
        tone === "warn" && "border-l-warn/70 glow-border-warn attention-breathe-warn",
        tone === "info" && "border-l-info/70 glow-border-info",
        tone === "ai" && "border-l-ai/70 glow-border-ai",
        tone === "neutral" && "border-l-line-subtle",
      )}
      padded
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle">
            {icon && <span className="text-fg-subtle">{icon}</span>}
            <span className="truncate">{label}</span>
          </div>
          <div className="mt-2 flex items-baseline gap-1.5">
            <span
              className={cn(
                "text-[26px] leading-none font-semibold tracking-tight font-mono",
                tone === "pos" && "text-pos",
                tone === "neg" && "text-neg",
                tone === "warn" && "text-warn",
                tone === "info" && "text-info",
                tone === "ai" && "text-ai",
                tone === "neutral" && "text-fg",
              )}
            >
              {value}
            </span>
            {unit && <span className="text-xs text-fg-muted font-medium">{unit}</span>}
          </div>
          {(delta !== undefined || deltaLabel || hasGap) && (
            <div className="mt-1.5 flex items-center gap-1.5 text-xs flex-wrap">
              {delta !== undefined && (
                <span
                  className={cn(
                    "inline-flex items-center gap-0.5 font-mono font-medium",
                    deltaTone === "pos" && "text-pos",
                    deltaTone === "neg" && "text-neg",
                    deltaTone === "neutral" && "text-fg-muted",
                  )}
                >
                  {deltaTone === "pos" && <ArrowUpRight size={12} />}
                  {deltaTone === "neg" && <ArrowDownRight size={12} />}
                  {deltaTone === "neutral" && <Minus size={12} />}
                  {Math.abs(delta).toFixed(Math.abs(delta) < 10 ? 2 : 1)}
                  {deltaLabel ? "%" : ""}
                </span>
              )}
              {hasGap && gap !== undefined && (
                <span
                  className={cn(
                    "inline-flex items-center gap-0.5 rounded-xs px-1.5 py-0.5 font-mono text-2xs",
                    gapTone === "pos" && "bg-pos/10 text-pos",
                    gapTone === "neg" && "bg-neg/10 text-neg",
                  )}
                  title={`Differenz Wert minus Ziel · Ziel: ${(target as number).toLocaleString("de-DE")}${gapUnit}`}
                >
                  <span className="text-fg-subtle/80 mr-0.5">Δ</span>
                  {gap >= 0 ? "+" : ""}
                  {formatGap(gap)}
                  {gapUnit}
                  <span className="ml-0.5 text-fg-subtle/80">
                    {gap >= 0 ? "über" : "bis"} Ziel
                  </span>
                </span>
              )}
              {deltaLabel && <span className="text-fg-subtle">{deltaLabel}</span>}
            </div>
          )}
        </div>

        {spark && (
          <div className="w-[92px] h-10 -mr-1 opacity-90 pointer-events-none">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={spark} margin={{ top: 4, bottom: 2, left: 0, right: 0 }}>
                <YAxis hide domain={["dataMin - 5", "dataMax + 5"]} />
                <Line
                  type="monotone"
                  dataKey="y"
                  stroke={stroke}
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {progressPct !== undefined && (() => {
        const isOutOfRange = progressPct === 0 && (tone === "warn" || tone === "neg");
        const fillWidth = isOutOfRange ? 100 : progressPct;
        return (
          <div
            className={cn(
              "mt-3 h-1.5 rounded-full overflow-hidden",
              tone === "pos" && "bg-pos/20",
              tone === "neg" && "bg-neg/20",
              tone === "warn" && "bg-warn/20",
              tone === "info" && "bg-info/20",
              tone === "ai" && "bg-ai/20",
              tone === "neutral" && "bg-bg-3",
            )}
            aria-hidden
          >
            <div
              className={cn(
                "h-full transition-[width] duration-300",
                tone === "pos" && "bg-pos",
                tone === "neg" && "bg-neg",
                tone === "warn" && "bg-warn",
                tone === "info" && "bg-info",
                tone === "ai" && "bg-ai",
                tone === "neutral" && "bg-fg-muted",
              )}
              style={{ width: `${fillWidth}%` }}
            />
          </div>
        );
      })()}

      {helper && (
        <div className="mt-3 pt-3 border-t border-line-subtle text-2xs text-fg-muted">{helper}</div>
      )}
    </Card>
  );
}

function formatGap(gap: number): string {
  const abs = Math.abs(gap);
  if (abs >= 100) return gap.toFixed(0);
  if (abs >= 10) return gap.toFixed(1);
  return gap.toFixed(2);
}

function strokeFor(tone: Props["tone"]): string {
  switch (tone) {
    case "pos":
      return "rgb(var(--pos))";
    case "neg":
      return "rgb(var(--neg))";
    case "warn":
      return "rgb(var(--warn))";
    case "info":
      return "rgb(var(--info))";
    case "ai":
      return "rgb(var(--ai))";
    default:
      return "rgb(var(--accent))";
  }
}
