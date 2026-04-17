import { useT } from "@/i18n/I18nProvider";
import { PageHeader } from "@/layout/PageHeader";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { Badge, Card, CardHeader } from "@/components/ui/Primitives";
import { useDashboardQuality } from "@/lib/useDashboardQuality";
import { cn } from "@/lib/utils";

export function AIInsightsPage() {
  const { t } = useT();
  const q = useDashboardQuality();

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.ai.title")}
        sub="Modell-Qualität aus Phase 5 Hold-Metrics — echte Werte, keine Demo-Zahlen"
      />

      {q.state === "ready" && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <KpiCard
            label="Forward Precision"
            value={q.data.forward_precision_pct != null ? `${q.data.forward_precision_pct.toFixed(2)}%` : "—"}
            note={`${q.data.forward_hits}/${q.data.forward_resolved} resolved · Gate 60%`}
            tone={(q.data.forward_precision_pct ?? 0) >= 60 ? "pos" : "warn"}
          />
          <KpiCard
            label="Priority-Hit Correlation"
            value={q.data.priority_corr?.toFixed(4) ?? "—"}
            note="Gate ≥ 0.40"
            tone={(q.data.priority_corr ?? 0) >= 0.4 ? "pos" : "warn"}
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
        <Card padded>
          <CardHeader
            title="Gate-Status (D-105)"
            subtitle="Production-Gate 2026-04-23"
            right={
              <Badge tone={q.data.gate_status === "green" ? "pos" : q.data.gate_status === "amber" ? "warn" : "neg"} dot>
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

function KpiCard({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "pos" | "neg" | "warn" | "muted" }) {
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
