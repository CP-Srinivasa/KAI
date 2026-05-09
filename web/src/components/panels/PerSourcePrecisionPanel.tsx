import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import type { DashboardQuality } from "@/lib/api";
import { cn } from "@/lib/utils";
import { sourceLabel } from "@/lib/sourceLabels";

// V-DB4a 2026-05-08: Per-source active precision panel.
// Quelle: /dashboard/api/quality.per_source_active_precision (Backend-Field
// neu freigegeben durch dashboard.py:V-DB4a). Zeigt n / hit-rate / Wilson-Lower
// pro Source plus Gate-Status. Operator sieht direkt, welche Sources
// systemtisch tragen und welche das Forward-Signal verwaessern.
//
// Operator-Folge 2026-05-08 (DALI-Polish):
//   - items-center statt items-baseline fuer optisch ruhigere Reihen.
//   - ProgressBar size="md" — sichtbarer in der Sidebar-Spalte.
//   - "low rate"-Badge atmet (attention-breathe-neg) — kritischer Fall.
//
// V-DB5 audit (2026-05-09):
//   E-2: gateLabel mit min_resolved-Closure statt hardcoded 50.
//   G-2: Tone-Dot vor Source-Label (A11y / Rot-Gruen-Schwaeche).
//   I-4: Deutsche Microcopy (OK / "Quote zu niedrig" / "(aktiv)") +
//        humanisierte Source-Labels via lib/sourceLabels.

type SourceMetrics = NonNullable<DashboardQuality["per_source_active_precision"]>["by_source"][string];
type GateTone = "pos" | "warn" | "neg" | "muted";

function gateBadgeTone(m: SourceMetrics): GateTone {
  if (m.passes_gate) return "pos";
  if (m.n_threshold_met && !m.wilson_low_threshold_met) return "neg";
  if (!m.n_threshold_met && m.wilson_low_threshold_met) return "warn";
  return "muted";
}

function gateLabel(m: SourceMetrics, minResolved: number): string {
  if (m.passes_gate) return "OK";
  if (m.n_threshold_met && !m.wilson_low_threshold_met) return "Quote zu niedrig";
  if (!m.n_threshold_met) return `n<${minResolved}`;
  return "—";
}

const TONE_DOT_CLASS: Record<GateTone, string> = {
  pos: "bg-pos",
  warn: "bg-warn",
  neg: "bg-neg",
  muted: "bg-fg-subtle/50",
};

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
        title="Source-Precision (aktiv)"
        subtitle={`Gate: n≥${psp.min_resolved} · Wilson-Lower≥${psp.min_wilson_low_pct.toFixed(0)}% · ${passing}/${total} OK`}
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
          const display = sourceLabel(source);
          const titleText = display.hint
            ? `${display.hint} · Backend-Key: ${source}`
            : `Backend-Key: ${source}`;
          return (
            <div key={source} className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 items-center">
              <div className="min-w-0">
                <div className="flex items-center justify-between gap-2 text-xs">
                  <div className="flex items-center gap-1.5 min-w-0">
                    {/* G-2: Tone-Dot vor Label — A11y-Konsistenz mit ActivePrecisionCard. */}
                    <span
                      className={cn(
                        "inline-block h-2 w-2 rounded-full shrink-0",
                        TONE_DOT_CLASS[tone],
                      )}
                      aria-hidden="true"
                    />
                    <span
                      className="text-fg font-medium truncate"
                      title={titleText}
                    >
                      {display.label}
                    </span>
                  </div>
                  <span className="font-mono text-2xs text-fg-subtle shrink-0">
                    n={m.resolved} ({m.hits}/{m.misses})
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <ProgressBar
                    value={m.hit_rate_pct}
                    target={psp.min_wilson_low_pct}
                    tone={tone === "pos" ? "pos" : tone === "neg" ? "neg" : "auto"}
                    sufficientSample={m.n_threshold_met}
                    label={`${display.label} hit rate`}
                    size="md"
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
              <Badge
                tone={tone === "muted" ? "muted" : tone}
                className={cn(
                  "shrink-0",
                  // "Quote zu niedrig" (n erfuellt aber Hit-Rate zu niedrig) atmet —
                  // das ist der kritische Fall, keine Stichprobe-Toleranz.
                  tone === "neg" && "attention-breathe-neg",
                )}
              >
                {gateLabel(m, psp.min_resolved)}
              </Badge>
            </div>
          );
        })}
      </div>
      {sources.length === 0 && (
        <div className="text-2xs text-fg-subtle">keine Source mit aktiven Auflösungen</div>
      )}
      <div className="mt-4 pt-3 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed">
        Wilson-95% Konfidenzintervalle in Klammern. <span className="text-pos">OK</span>: n≥{psp.min_resolved} und CI-Lower≥{psp.min_wilson_low_pct.toFixed(0)}%.
        <span className="text-neg ml-1">Quote zu niedrig</span>: n erfüllt, aber Hit-Rate unter dem Floor.
        <span className="text-warn ml-1">n&lt;{psp.min_resolved}</span>: Sample noch zu klein für Aussage.
      </div>
    </Card>
  );
}
