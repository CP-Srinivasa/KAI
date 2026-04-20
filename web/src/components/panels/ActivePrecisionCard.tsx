import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type { DashboardProvenance, ProvenanceMetrics } from "@/lib/api";

// Active-Precision split per Signal-Source mit Wilson 95%-CI.
// Quelle: /dashboard/api/provenance (app/alerts/provenance_metrics.py).
// Zweck: Re-Entry-Verdict am 2026-05-16 — nur Sources mit sample_sufficient
// (≥30 resolved) sind judgment-ready. Legacy-unknown-Bucket bleibt sichtbar
// damit Operator erkennt, wie viel Altlast die Baseline drückt.

function fmtPct(n: number | null): string {
  return n == null ? "—" : `${n.toFixed(1)}%`;
}

function ciLabel(m: ProvenanceMetrics): string {
  if (m.ci_low_pct == null || m.ci_high_pct == null) return "—";
  return `${m.ci_low_pct.toFixed(1)}–${m.ci_high_pct.toFixed(1)}`;
}

function verdictTone(v: string): "pos" | "warn" | "neg" | "neutral" {
  if (v === "tv_significantly_better_than_rss") return "pos";
  if (v === "rss_significantly_better_than_tv") return "neg";
  if (v === "overlapping_confidence_intervals_no_significant_difference") return "warn";
  return "neutral";
}

function verdictText(v: string): string {
  const map: Record<string, string> = {
    tv_significantly_better_than_rss: "TV > RSS (signifikant)",
    rss_significantly_better_than_tv: "RSS > TV (signifikant)",
    overlapping_confidence_intervals_no_significant_difference:
      "CIs überlappen — kein signifikanter Unterschied",
    insufficient_sample_for_split_comparison:
      "Stichprobe zu klein für Split-Vergleich",
  };
  return map[v] ?? v;
}

export function ActivePrecisionCard({
  data,
}: {
  data: DashboardProvenance | null;
}) {
  if (!data) {
    return (
      <Card padded>
        <CardHeader
          title="Active Precision (pro Source)"
          subtitle="lädt…"
        />
      </Card>
    );
  }

  const sourcesByResolvedDesc = [...data.by_source].sort(
    (a, b) => b.resolved - a.resolved,
  );

  const tvPipe = data.tradingview_pipeline;

  return (
    <Card padded>
      <CardHeader
        title="Active Precision (pro Source)"
        subtitle={`Wilson 95%-CI · min Sample ${data.min_sample_for_judgment} resolved`}
        right={
          <Badge tone={verdictTone(data.verdict)} dot>
            {data.overall.resolved} resolved
          </Badge>
        }
      />

      {/* Overall row */}
      <div className="mb-3 rounded-md border border-line-subtle px-3 py-2.5 bg-bg-2">
        <div className="flex items-baseline justify-between gap-3 text-xs">
          <span className="text-fg font-medium">Overall (alle Sources)</span>
          <div className="flex items-center gap-3 font-mono shrink-0">
            <span className="text-sm font-semibold text-fg">
              {fmtPct(data.overall.hit_rate_pct)}
            </span>
            <span className="text-2xs text-fg-subtle">
              CI {ciLabel(data.overall)}
            </span>
            <span className="text-2xs text-fg-muted">
              n={data.overall.resolved}
            </span>
          </div>
        </div>
      </div>

      {/* Per-source list */}
      <div className="space-y-2">
        {sourcesByResolvedDesc.length === 0 && (
          <div className="text-xs text-fg-muted py-2">
            Noch keine auflösbaren Outcomes pro Source.
          </div>
        )}
        {sourcesByResolvedDesc.map((m) => {
          const hasValue = m.hit_rate_pct != null;
          const pct = hasValue ? Math.min(100, m.hit_rate_pct!) : 0;
          const green = hasValue && m.sample_sufficient && m.hit_rate_pct! >= 60;
          const orange =
            hasValue && m.sample_sufficient && m.hit_rate_pct! >= 40 && !green;
          return (
            <div key={m.source}>
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-fg font-medium truncate">{m.source}</span>
                  {!m.sample_sufficient && (
                    <span className="text-2xs text-fg-subtle shrink-0">
                      n&lt;{data.min_sample_for_judgment}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 font-mono shrink-0">
                  <span
                    className={cn(
                      "text-sm font-semibold",
                      green ? "text-pos" : orange ? "text-warn" : "text-fg",
                    )}
                  >
                    {fmtPct(m.hit_rate_pct)}
                  </span>
                  <span className="text-2xs text-fg-subtle">
                    CI {ciLabel(m)}
                  </span>
                  <span className="text-2xs text-fg-muted">
                    {m.hits}h/{m.misses}m
                  </span>
                </div>
              </div>
              <div className="mt-1 h-1 rounded-full bg-bg-3 overflow-hidden">
                <div
                  className={cn(
                    "h-full rounded-full transition-all",
                    !hasValue
                      ? "bg-bg-3"
                      : green
                        ? "bg-pos"
                        : orange
                          ? "bg-warn"
                          : "bg-neg",
                  )}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>

      {/* Verdict + TV-Pipeline */}
      <div className="mt-4 pt-3 border-t border-line-subtle text-2xs text-fg-muted leading-relaxed space-y-1">
        <div>
          Verdict:{" "}
          <span
            className={cn(
              "font-mono font-semibold",
              verdictTone(data.verdict) === "pos" && "text-pos",
              verdictTone(data.verdict) === "warn" && "text-warn",
              verdictTone(data.verdict) === "neg" && "text-neg",
            )}
          >
            {verdictText(data.verdict)}
          </span>
        </div>
        <div>
          TV-Pipeline: pending=
          <span className="font-mono">{tvPipe.pending_events}</span>, smoke=
          <span className="font-mono">{tvPipe.smoke_test_events}</span>, real=
          <span className="font-mono">{tvPipe.real_events}</span>
        </div>
        {data.notes.length > 0 && (
          <ul className="list-disc pl-4 space-y-0.5">
            {data.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        )}
        {data.generated_at && (
          <div className="font-mono text-fg-subtle">
            {data.generated_at.substring(0, 19).replace("T", " ")}
          </div>
        )}
      </div>
    </Card>
  );
}
