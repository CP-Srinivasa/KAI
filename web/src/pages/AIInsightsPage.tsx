import { Sparkles } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { PageHeader } from "@/layout/PageHeader";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { Badge, Card, CardHeader } from "@/components/ui/Primitives";
import { useDashboardQuality } from "@/lib/useDashboardQuality";
import { cn } from "@/lib/utils";
import { tierLiftTone } from "@/lib/tone";

type PrecisionTone = "pos" | "warn" | "neg" | "muted";

function formatPct(value: number | null | undefined, digits = 2): string {
  return value == null ? "—" : `${value.toFixed(digits)}%`;
}

function precisionTone(pct: number | null | undefined): PrecisionTone {
  if (pct == null) return "muted";
  if (pct >= 60) return "pos";
  if (pct >= 45) return "warn";
  return "neg";
}

export function AIInsightsPage() {
  const { t } = useT();
  const q = useDashboardQuality();

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.ai.title")}
        sub="Welche Quellen liefern zuverlässige Signale — und wie sicher sind wir."
        tone="ai"
        icon={<Sparkles size={18} />}
      />

      {q.state === "ready" && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          <KpiCard
            label="Active Precision"
            value={formatPct(q.data.active_precision_pct)}
            note={`${q.data.active_hits}/${q.data.active_resolved_count} resolved · ohne Legacy (${q.data.legacy_resolved_count}) · Gate 60%`}
            tone={precisionTone(q.data.active_precision_pct)}
          />
          <KpiCard
            label="Forward Precision"
            value={formatPct(q.data.forward_precision_pct)}
            note={`${q.data.forward_hits}/${q.data.forward_resolved} resolved · Gate 60%`}
            tone={precisionTone(q.data.forward_precision_pct)}
          />
          <KpiCard
            label="Priority Tier-Lift"
            value={
              q.data.priority_tier_lift_pct != null
                ? `${q.data.priority_tier_lift_pct >= 0 ? "+" : ""}${q.data.priority_tier_lift_pct.toFixed(1)}pp`
                : "—"
            }
            note={
              q.data.priority_tier_lift_pct == null
                ? "awaiting standard-tier sample"
                : "Gate ≥ +15pp"
            }
            tone={((): PrecisionTone => {
              const t = tierLiftTone(q.data.priority_tier_lift_pct);
              if (t === "pos" || t === "warn" || t === "neg") return t;
              return "muted";
            })()}
          />
          <KpiCard
            label="Paper Fills"
            value={String(q.data.paper_fills)}
            note={`${q.data.paper_cycles} cycles · Gate ≥ 10`}
            tone={q.data.paper_fills >= 10 ? "pos" : "warn"}
          />
        </div>
      )}

      {q.state === "ready" && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-subtle font-mono -mt-2">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-pos inline-block" /> ok ≥ 60%
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-warn inline-block" /> warn 45–60%
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-neg inline-block" /> crit &lt; 45%
          </span>
          <span className="text-fg-subtle/80">Precision-Schwellen · Production-Gate 60%</span>
        </div>
      )}

      {q.state === "ready" && (
        <Card padded>
          <CardHeader
            title="Baseline vs. Active"
            subtitle={`Legacy-Split an source=unknown (pre-D-139) · Cutoff-Fallback ${q.data.legacy_unknown_cutoff ?? "—"}`}
          />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono">
            <div className="border border-line-subtle rounded-sm p-3">
              <div className="text-2xs uppercase text-fg-subtle mb-1">Baseline (alle resolved)</div>
              <div className="text-lg text-fg-muted">
                {formatPct(q.data.precision_pct)}
              </div>
              <div className="text-xs text-fg-subtle mt-1">
                {q.data.hits}H / {q.data.misses}M · n={q.data.resolved_count}
              </div>
            </div>
            <div className="border border-line rounded-sm p-3 bg-bg-2">
              <div className="text-2xs uppercase text-fg-subtle mb-1">Active (ohne Legacy)</div>
              <div
                className={cn(
                  "text-lg",
                  precisionTone(q.data.active_precision_pct) === "pos" && "text-pos",
                  precisionTone(q.data.active_precision_pct) === "warn" && "text-warn",
                  precisionTone(q.data.active_precision_pct) === "neg" && "text-neg",
                  precisionTone(q.data.active_precision_pct) === "muted" && "text-fg-muted",
                )}
              >
                {formatPct(q.data.active_precision_pct)}
              </div>
              <div className="text-xs text-fg-subtle mt-1">
                {q.data.active_hits}H / {q.data.active_misses}M · n={q.data.active_resolved_count}
              </div>
            </div>
          </div>
          <div className="mt-2 text-xs text-fg-subtle">
            Legacy-Bucket: {q.data.legacy_resolved_count} resolved docs ohne DB-Source (pre-D-139 Persistenz-Bug).
            Diese inflationieren die Baseline-Miss-Quote ohne die aktuelle Pipeline zu beschreiben.
          </div>
        </Card>
      )}

      {q.state === "ready" && (
        <Card padded>
          <CardHeader
            title="Gate-Status (D-105)"
            subtitle="Production-Gate 2026-04-23"
            right={
              <Badge
                tone={
                  q.data.gate_status === "hold_releasable"
                    ? "pos"
                    : q.data.gate_status === "hold_remains_active"
                      ? "warn"
                      : "neg"
                }
                dot
              >
                {q.data.gate_status ?? "—"}
              </Badge>
            }
          />
          {q.data.blocking_reasons.length > 0 ? (
            <ul className="text-xs text-fg-muted space-y-1 font-mono break-words">
              {q.data.blocking_reasons.map((r, i) => (
                <li key={i}>· {r}</li>
              ))}
            </ul>
          ) : (
            <div className="text-xs text-pos">Keine Blocking-Reasons. Gate grün.</div>
          )}
        </Card>
      )}

      <PreparedPanel
        title="Modell-Observationen & Entscheidungspfad"
        reason="Narrative Insights (warum ein Signal entstanden ist, welche Features dominierten, welche Gates durchlaufen wurden) brauchen einen neuen Explain-Endpoint."
        detail="Geplant: GET /operator/signals/{id}/explain — ausbauen auf decision-pack-Level. Phase 2."
      />

      <PreparedPanel
        title="Feature-Drift & Kalibrierungs-Hinweise"
        reason="Drift-Tracking über Zeit (Confidence vs. Outcome) ist in den Artifacts angelegt, aber noch nicht visualisiert."
        detail="Quelle: ph5_hold_metrics_report.json + alert_outcomes.jsonl — Aggregations-API folgt."
      />
    </div>
  );
}

function KpiCard({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: PrecisionTone }) {
  return (
    <Card padded>
      <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">{label}</div>
      <div
        className={cn(
          "mt-1 font-mono text-2xl font-semibold",
          tone === "pos" && "text-pos",
          tone === "neg" && "text-neg",
          tone === "warn" && "text-warn",
          tone === "muted" && "text-fg-muted",
        )}
      >
        {value}
      </div>
      {note && <div className="mt-1 text-2xs text-fg-subtle font-mono">{note}</div>}
    </Card>
  );
}
