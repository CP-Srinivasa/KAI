import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import { useT } from "@/i18n/I18nProvider";
import { cn } from "@/lib/utils";
import type { DashboardQuality } from "@/lib/api";

const V1_DISQUALIFIED_TOOLTIP =
  "Daten vor 2026-05-02 14:30 UTC unter Schema v1 (NEO-P-101-r2 disqualified, " +
  "via Backfill rekonstruiert). v2-only-Werte ab Cutover.";

type Row = {
  label: string;
  value: number | null;
  target: number;
  format: (n: number) => string;
  hint?: string;
};

// Echt-Daten Variante. Die Kennzahlen kommen aus /dashboard/api/quality
// (siehe app/api/routers/dashboard.py). Keine Mock-Werte.
export function QualityBarPanel({ data }: { data: DashboardQuality | null }) {
  const { t } = useT();

  const rows: Row[] = [
    {
      label: t("primitives.forward_precision"),
      value: data?.forward_precision_pct ?? null,
      target: 60,
      format: (n) => `${n.toFixed(2)}%`,
      hint:
        data?.precision_pct != null
          ? `Raw: ${data.precision_pct.toFixed(2)}%`
          : undefined,
    },
    {
      label: t("primitives.resolved_alerts"),
      value: data?.resolved_count ?? null,
      target: 50,
      format: (n) => `${n}`,
    },
    {
      label: t("primitives.priority_hit_corr"),
      value: data?.priority_corr ?? null,
      target: 0.4,
      format: (n) => n.toFixed(3),
    },
    {
      label: t("primitives.paper_fills_real"),
      value: data?.paper_fills ?? null,
      target: 10,
      format: (n) => `${n}`,
      hint:
        data?.paper_fills_with_pnl != null
          ? `PnL-Fills: ${data.paper_fills_with_pnl}`
          : undefined,
    },
    {
      label: "Paper Fills (PnL) — Re-Entry-Gate",
      value: data?.paper_fills_with_pnl ?? null,
      target: 10,
      format: (n) => `${n}`,
      hint:
        data?.paper_realized_pnl_usd != null
          ? `Σ realized: $${data.paper_realized_pnl_usd.toFixed(0)}`
          : undefined,
    },
  ];

  const met = rows.filter((r) => r.value != null && r.value >= r.target).length;
  const total = rows.length;

  return (
    <Card padded>
      <CardHeader
        title={t("primitives.quality_bar")}
        subtitle={t("primitives.quality_bar_sub")}
        right={
          <Badge tone={met === total ? "pos" : met >= 2 ? "warn" : "neg"} dot>
            {met}/{total}
          </Badge>
        }
      />
      <div className="space-y-3.5">
        {rows.map((r) => {
          const hasValue = r.value != null;
          const ok = hasValue && r.value! >= r.target;
          return (
            <div key={r.label}>
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-fg font-medium truncate">{r.label}</span>
                  {r.hint && <span className="text-2xs text-fg-subtle shrink-0">{r.hint}</span>}
                </div>
                <div className="flex items-center gap-1.5 font-mono shrink-0">
                  <span className={cn("text-sm font-semibold", ok ? "text-pos" : "text-fg")}>
                    {hasValue ? r.format(r.value!) : "—"}
                  </span>
                  <span className="text-fg-subtle">/ {r.format(r.target)}</span>
                </div>
              </div>
              <ProgressBar
                value={r.value}
                target={r.target}
                tone={ok ? "pos" : "auto"}
                size="md"
                label={r.label}
                className="mt-1.5"
              />
            </div>
          );
        })}
      </div>
      {data && (
        <div className="mt-5 pt-4 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed flex flex-wrap gap-x-4 gap-y-1">
          <span>
            Gate:{" "}
            <span
              className={cn(
                "font-mono font-semibold",
                data.gate_status === "hold_releasable" ? "text-pos" : "text-warn",
              )}
            >
              {data.gate_status ?? "—"}
            </span>
          </span>
          <span>
            Forward: <span className="font-mono">{data.forward_hits}</span> hits /{" "}
            <span className="font-mono">{data.forward_miss}</span> miss (
            <span className="font-mono">{data.forward_resolved}</span> resolved)
          </span>
          <span>
            Paper:{" "}
            <span className="font-mono">{data.paper_fills_with_pnl}</span>/10 PnL-Fills ·{" "}
            <span className="font-mono">{data.paper_positions_closed}</span> closed · Σ{" "}
            <span
              className={cn(
                "font-mono",
                data.paper_realized_pnl_usd > 0
                  ? "text-pos"
                  : data.paper_realized_pnl_usd < 0
                    ? "text-neg"
                    : "text-fg-muted",
              )}
            >
              ${data.paper_realized_pnl_usd.toFixed(0)}
            </span>
            {data.audit_v1_disqualified && (
              <span
                aria-label={V1_DISQUALIFIED_TOOLTIP}
                title={V1_DISQUALIFIED_TOOLTIP}
                className="ml-1.5 inline-flex items-center rounded-md border border-fg-subtle/30 px-1 py-0 text-[9px] uppercase tracking-wide font-mono text-fg-subtle hover:text-fg cursor-help select-none"
              >
                v1→v2 backfill
              </span>
            )}
          </span>
          {data.generated_at && (
            <span className="ml-auto font-mono">
              {data.generated_at.substring(0, 19).replace("T", " ")}
            </span>
          )}
        </div>
      )}
    </Card>
  );
}
