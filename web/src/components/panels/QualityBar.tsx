import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import { useT } from "@/i18n/I18nProvider";
import { cn } from "@/lib/utils";
import type { DashboardQuality } from "@/lib/api";
import {
  tierLiftTone,
  formatTierLift,
  evaluateTierLiftSignificance,
  TIER_LIFT_INSIGNIFICANT_LABEL,
  TIER_LIFT_INSIGNIFICANT_TOOLTIP,
  type Tone,
} from "@/lib/tierLift";

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
  hint?: string;
  /** Optional title-Tooltip auf dem hint-span (D-1: "Lift unsicher"-Erklaerung). */
  hintTooltip?: string;
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
    (() => {
      // D-149: priority_corr (Pearson) ist auf P7-P10-Band nicht aussagekraeftig.
      // Wir zeigen jetzt priority_tier_lift_pct = High-Conviction-HitRate
      // minus Standard-Tier-HitRate. Ziel realistisch >=15pp Lift.
      // V-DB5 A-1/A-2/A-3 + D-1: Format/Tone/Significance via shared lib/tierLift.
      const sig = evaluateTierLiftSignificance(data);
      const ptl = data?.priority_tier_lift_pct ?? null;
      let hint: string | undefined;
      let hintTooltip: string | undefined;
      if (sig.sampleN == null) {
        hint = ptl == null ? t("primitives.priority_tier_lift_insufficient") : undefined;
      } else if (sig.isSignificant === false) {
        hint = `n=${sig.sampleN} (${TIER_LIFT_INSIGNIFICANT_LABEL})`;
        hintTooltip = TIER_LIFT_INSIGNIFICANT_TOOLTIP;
      } else {
        hint = `n=${sig.sampleN}`;
      }
      return {
        label: t("primitives.priority_tier_lift"),
        value: ptl,
        target: 15,
        format: (n: number) => formatTierLift(n),
        toneFn: tierLiftTone,
        hint,
        hintTooltip,
      } satisfies Row;
    })(),
    // Row 4 (paper_fills) + Row 5 (paper_fills_with_pnl) beide entfernt
    // — Re-Entry-Metriken gehören in ReentryGatePanel (DALI-P-026 Original-Plan,
    // NEO-F-PANEL-CHECK-2026-05-04-003 P2-Korrektur). QualityBar zeigt nur noch
    // Re-Entry-unabhängige Operator-Signalqualität.
  ];

  const met = rows.filter((r) => r.value != null && r.value >= r.target).length;
  const total = rows.length;

  // DALI v2 S3 M1a: Klartext-Bewertung nach Master-Spec G2.
  // Operator sieht ohne Zahlen-Interpretation, wie es um die Pipeline steht.
  let verdictText: string;
  let verdictTone: "pos" | "warn" | "neg";
  if (met === total) {
    verdictText = "Alle Ziele erreicht";
    verdictTone = "pos";
  } else if (met >= Math.ceil(total / 2)) {
    verdictText = "Teilweise — Aufmerksamkeit";
    verdictTone = "warn";
  } else {
    verdictText = "Ziele verfehlt";
    verdictTone = "neg";
  }

  return (
    <Card padded>
      <CardHeader
        title={t("primitives.quality_bar")}
        subtitle={t("primitives.quality_bar_sub")}
        right={
          <Badge tone={verdictTone} dot>
            {met}/{total} · {verdictText}
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
                  {r.hint && (
                    <span
                      className="text-2xs text-fg-subtle shrink-0"
                      title={r.hintTooltip}
                    >
                      {r.hint}
                    </span>
                  )}
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
            Aus <span className="font-mono">{data.forward_resolved}</span> aufgelösten Signalen:{" "}
            <span className="font-mono text-pos">{data.forward_hits}</span> Treffer ·{" "}
            <span className="font-mono text-neg">{data.forward_miss}</span> Fehler
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
