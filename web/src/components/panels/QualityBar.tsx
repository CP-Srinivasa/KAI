import type { ReactNode } from "react";
import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import { useT } from "@/i18n/I18nProvider";
import { cn } from "@/lib/utils";
import type { DashboardQuality } from "@/lib/api";
import { tierLiftCiOverlap, tierLiftTone, type Tone } from "@/lib/tone";
import { LiftUncertainBadge } from "@/components/quality/LiftUncertainBadge";

// Lokalisierung der gate_status raw-snake-Strings analog zu verdictText() in
// ActivePrecisionCard. Unbekannte Werte fallen by-design auf den raw-Key
// zurück (Forensik-Anker, Debug-Sichtbarkeit).
function gateStatusText(t: (key: string) => string, raw: string | null | undefined): string {
  if (!raw) return "—";
  const map: Record<string, string> = {
    hold_releasable: t("primitives.gate_status_hold_releasable"),
    blocked: t("primitives.gate_status_blocked"),
  };
  return map[raw] ?? raw;
}

type Row = {
  label: string;
  value: number | null;
  target: number;
  format: (n: number) => string;
  hint?: ReactNode;
  toneFn?: (value: number | null) => Tone;
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
      // D-149: priority_corr (Pearson) ist auf P7-P10-Band nicht aussagekraeftig.
      // Wir zeigen jetzt priority_tier_lift_pct = High-Conviction-HitRate
      // minus Standard-Tier-HitRate. Ziel realistisch >=15pp Lift.
      label: t("primitives.priority_tier_lift"),
      value: data?.priority_tier_lift_pct ?? null,
      target: 15,
      format: (n) => `${n >= 0 ? "+" : ""}${n.toFixed(1)}pp`,
      toneFn: tierLiftTone,
      hint: (() => {
        if (
          data?.priority_tier_high_conviction_resolved == null ||
          data?.priority_tier_standard_resolved == null
        ) {
          return data?.priority_tier_lift_pct == null
            ? t("primitives.priority_tier_lift_insufficient")
            : undefined;
        }
        const n = data.priority_tier_high_conviction_resolved + data.priority_tier_standard_resolved;
        return (
          <span className="inline-flex items-center">
            <span>n={n}</span>
            {tierLiftCiOverlap(data) && <LiftUncertainBadge />}
          </span>
        );
      })(),
    },
    // Row 4 (paper_fills) + Row 5 (paper_fills_with_pnl) beide entfernt
    // — Re-Entry-Metriken gehören in ReentryGatePanel (DALI-P-026 Original-Plan,
    // NEO-F-PANEL-CHECK-2026-05-04-003 P2-Korrektur). QualityBar zeigt nur noch
    // Re-Entry-unabhängige Operator-Signalqualität.
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
          const tone: Tone = r.toneFn ? r.toneFn(r.value) : ok ? "pos" : "neutral";
          return (
            <div key={r.label}>
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-fg font-medium truncate">{r.label}</span>
                  {r.hint && <span className="text-2xs text-fg-subtle shrink-0">{r.hint}</span>}
                </div>
                <div className="flex items-center gap-1.5 font-mono shrink-0">
                  <span
                    className={cn(
                      "text-sm font-semibold",
                      tone === "pos" && "text-pos",
                      tone === "neg" && "text-neg",
                      tone === "warn" && "text-warn",
                      tone === "info" && "text-info",
                      tone === "ai" && "text-ai",
                      tone === "neutral" && "text-fg",
                    )}
                  >
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
            {t("primitives.gate_status_label")}:{" "}
            <span
              className={cn(
                "font-mono font-semibold",
                data.gate_status === "hold_releasable" ? "text-pos" : "text-warn",
              )}
              title={data.gate_status ?? undefined}
            >
              {gateStatusText(t, data.gate_status)}
            </span>
          </span>
          <span>
            Forward: <span className="font-mono">{data.forward_hits}</span> hits /{" "}
            <span className="font-mono">{data.forward_miss}</span> miss (
            <span className="font-mono">{data.forward_resolved}</span> resolved)
          </span>
          {/* V1-Backfill-Marker lebt im ReentryGatePanel direkt am PnL-Wert
              (DALI-P-026-r1 Folge-Cleanup) — hier nicht mehr doppelt. */}
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
