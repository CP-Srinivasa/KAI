import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import type { DashboardQuality } from "@/lib/api";
import { cn } from "@/lib/utils";

// V-DB4a 2026-05-08: Per-source active precision panel.
// Quelle: /dashboard/api/quality.per_source_active_precision (Backend-Field
// neu freigegeben durch dashboard.py:V-DB4a). Zeigt n / hit-rate / Wilson-Lower
// pro Source plus Gate-Status. Operator sieht direkt, welche Sources
// systemtisch tragen und welche das Forward-Signal verwaessern.

type SourceMetrics = NonNullable<DashboardQuality["per_source_active_precision"]>["by_source"][string];

function gateBadgeTone(m: SourceMetrics): "pos" | "warn" | "neg" | "muted" {
  if (m.passes_gate) return "pos";
  if (m.n_threshold_met && !m.wilson_low_threshold_met) return "neg";
  if (!m.n_threshold_met && m.wilson_low_threshold_met) return "warn";
  return "muted";
}

function gateLabel(m: SourceMetrics): string {
  if (m.passes_gate) return "PASS";
  if (m.n_threshold_met && !m.wilson_low_threshold_met) return "low rate";
  if (!m.n_threshold_met) return `n<${50}`;
  return "—";
}

export function PerSourcePrecisionPanel({ data }: { data: DashboardQuality | null }) {
  const psp = data?.per_source_active_precision;
  if (!psp) {
    return (
      <Card padded>
        <CardHeader
          title="Source-Precision"
          subtitle="Per-source Active-Precision wird im naechsten Hold-Report berechnet."
        />
        <div className="text-2xs text-fg-subtle">noch keine Daten</div>
      </Card>
    );
  }

  const sources = Object.entries(psp.by_source).sort(
    (a, b) => (b[1].resolved ?? 0) - (a[1].resolved ?? 0),
  );

  const passing = sources.filter(([, m]) => m.passes_gate).length;
  const total = sources.length;

  return (
    <Card padded>
      <CardHeader
        title="Source-Precision (active)"
        subtitle={`Gate: n≥${psp.min_resolved} · Wilson-Lower≥${psp.min_wilson_low_pct.toFixed(0)}% · ${passing}/${total} passing`}
        right={
          <Badge tone={passing > 0 ? "pos" : passing === 0 && total > 0 ? "warn" : "muted"} dot>
            {passing}/{total}
          </Badge>
        }
      />
      <div className="space-y-2.5">
        {sources.map(([source, m]) => {
          const tone = gateBadgeTone(m);
          const ciLo = m.ci_low_pct;
          const ciHi = m.ci_high_pct;
          return (
            <div key={source} className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 items-baseline">
              <div className="min-w-0">
                <div className="flex items-baseline justify-between gap-2 text-xs">
                  <span className="text-fg font-medium truncate">{source}</span>
                  <span className="font-mono text-2xs text-fg-subtle shrink-0">
                    n={m.resolved} ({m.hits}/{m.misses})
                  </span>
                </div>
                <div className="mt-1 flex items-baseline gap-2">
                  <ProgressBar
                    value={m.hit_rate_pct}
                    target={psp.min_wilson_low_pct}
                    tone={tone === "pos" ? "pos" : tone === "neg" ? "neg" : "auto"}
                    sufficientSample={m.n_threshold_met}
                    label={`${source} hit rate`}
                    size="sm"
                    className="flex-1"
                  />
                  <span className={cn(
                    "font-mono text-2xs shrink-0 w-[100px] text-right",
                    tone === "pos" && "text-pos",
                    tone === "neg" && "text-neg",
                    tone === "warn" && "text-warn",
                    tone === "muted" && "text-fg-subtle",
                  )}>
                    {m.hit_rate_pct != null ? `${m.hit_rate_pct.toFixed(1)}%` : "—"}
                    {ciLo != null && ciHi != null && (
                      <span className="text-fg-subtle/70 ml-1">
                        [{ciLo.toFixed(0)}–{ciHi.toFixed(0)}]
                      </span>
                    )}
                  </span>
                </div>
              </div>
              <Badge tone={tone === "muted" ? "muted" : tone} className="shrink-0">
                {gateLabel(m)}
              </Badge>
            </div>
          );
        })}
      </div>
      {sources.length === 0 && (
        <div className="text-2xs text-fg-subtle">keine Source mit active resolutions</div>
      )}
      <div className="mt-4 pt-3 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed">
        Wilson-95% Konfidenzintervalle in Klammern. <span className="text-pos">PASS</span>: n≥{psp.min_resolved} und CI-Lower≥{psp.min_wilson_low_pct.toFixed(0)}%.
        <span className="text-neg ml-1">low rate</span>: n erfüllt aber Hit-Rate zu niedrig.
        <span className="text-warn ml-1">n&lt;{psp.min_resolved}</span>: Sample noch zu klein für Aussage.
      </div>
    </Card>
  );
}
