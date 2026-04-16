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
}: Props) {
  const deltaTone = delta === undefined ? "neutral" : delta > 0 ? "pos" : delta < 0 ? "neg" : "neutral";
  const stroke = strokeFor(tone);

  return (
    <Card className="relative overflow-hidden" padded>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle">
            {icon && <span className="text-fg-subtle">{icon}</span>}
            <span className="truncate">{label}</span>
          </div>
          <div className="mt-2 flex items-baseline gap-1.5">
            <span className="text-[26px] leading-none font-semibold tracking-tight text-fg font-mono">
              {value}
            </span>
            {unit && <span className="text-xs text-fg-muted font-medium">{unit}</span>}
          </div>
          {(delta !== undefined || deltaLabel) && (
            <div className="mt-1.5 flex items-center gap-1.5 text-xs">
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

      {helper && (
        <div className="mt-3 pt-3 border-t border-line-subtle text-2xs text-fg-muted">{helper}</div>
      )}
    </Card>
  );
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
