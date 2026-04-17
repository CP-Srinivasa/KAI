import { Radio, Target, ShieldAlert, CheckCircle2, Activity, AlertCircle } from "lucide-react";
import { KpiCard } from "@/components/kpi/KpiCard";
import { QualityBarPanel } from "@/components/panels/QualityBar";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { useT } from "@/i18n/I18nProvider";
import { useDashboardQuality } from "@/lib/useDashboardQuality";
import { cn } from "@/lib/utils";

export function Dashboard() {
  const { t } = useT();
  const q = useDashboardQuality();
  const data = q.state === "ready" ? q.data : null;

  const fp = data?.forward_precision_pct ?? null;
  const rc = data?.resolved_count ?? null;
  const pc = data?.priority_corr ?? null;
  const pf = data?.paper_fills ?? null;

  return (
    <div className="p-5 xl:p-6 space-y-5 xl:space-y-6 max-w-[1680px] mx-auto">
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-fg">
            {t("pages.dashboard.title")}
          </h1>
          <p className="text-xs text-fg-muted mt-1">
            {data
              ? t("pages.dashboard.sub", {
                  p: (fp ?? 0).toFixed(2),
                  n: String(data.forward_resolved),
                })
              : "Live-Daten vom Backend, Auto-Refresh alle 60 s."}
          </p>
        </div>
        <div className="flex items-center gap-2 text-2xs font-mono text-fg-subtle">
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              q.state === "ready"
                ? "bg-pos"
                : q.state === "error"
                  ? "bg-neg"
                  : "bg-fg-subtle",
            )}
          />
          <span>
            {q.state === "ready"
              ? `Report: ${(data?.generated_at ?? "").substring(0, 19).replace("T", " ")}`
              : q.state === "error"
                ? `Fehler: ${q.error.message}`
                : "lädt …"}
          </span>
        </div>
      </header>

      {q.state === "error" && (
        <Card padded className="border-neg/30 bg-neg/5">
          <div className="flex items-start gap-3 text-xs text-neg">
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            <div className="min-w-0">
              <div className="font-semibold">Quality-Endpoint unerreichbar</div>
              <div className="text-fg-muted mt-1 break-words">
                {q.error.kind} · {q.error.message}
              </div>
              <div className="text-2xs text-fg-subtle mt-1 font-mono break-all">GET /dashboard/api/quality</div>
            </div>
          </div>
        </Card>
      )}

      {/* Aktive KPI-Row — ausschließlich echte Zahlen aus /dashboard/api/quality */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 xl:gap-4">
        <KpiCard
          label={t("primitives.forward_precision")}
          value={fp != null ? fp.toFixed(1) : "—"}
          unit="%"
          deltaLabel={data ? `Ziel: ≥60%` : "—"}
          tone={fp != null && fp >= 60 ? "pos" : "warn"}
          icon={<Target size={12} />}
          helper={
            data ? (
              <span>
                <span className="text-pos font-mono">{data.forward_hits}</span> hits ·{" "}
                <span className="text-neg font-mono">{data.forward_miss}</span> miss ·{" "}
                <span className="font-mono">{data.forward_resolved}</span> resolved
              </span>
            ) : undefined
          }
        />
        <KpiCard
          label={t("primitives.resolved_alerts")}
          value={rc != null ? String(rc) : "—"}
          deltaLabel="Ziel: ≥50"
          tone={rc != null && rc >= 50 ? "pos" : "warn"}
          icon={<CheckCircle2 size={12} />}
          helper={
            data ? (
              <span>
                <span className="text-pos font-mono">{data.hits}</span> hits ·{" "}
                <span className="text-neg font-mono">{data.misses}</span> miss
              </span>
            ) : undefined
          }
        />
        <KpiCard
          label={t("primitives.priority_hit_corr")}
          value={pc != null ? pc.toFixed(3) : "—"}
          deltaLabel="Ziel: ≥0.40"
          tone={pc != null && pc >= 0.4 ? "pos" : "warn"}
          icon={<Radio size={12} />}
        />
        <KpiCard
          label={t("primitives.paper_fills_real")}
          value={pf != null ? String(pf) : "—"}
          deltaLabel="Ziel: ≥10"
          tone={pf != null && pf >= 10 ? "pos" : "warn"}
          icon={<ShieldAlert size={12} />}
          helper={
            data ? (
              <span>
                <span className="font-mono">{data.paper_cycles}</span> cycles ·{" "}
                <span className="font-mono">{data.real_price_cycles}</span> real-price
              </span>
            ) : undefined
          }
        />
      </div>

      {/* Aktiver Analytics-Grid */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-8">
          <QualityBarPanel data={data} />
        </div>
        <div className="col-span-12 lg:col-span-4 space-y-4">
          <SignalQualityCard data={data} />
          <TradingLoopCard data={data} />
        </div>
      </div>

      {/* Recent Alerts */}
      <RecentAlertsCard data={data} />

      {/* Vorbereitete Bereiche — ehrlich gekennzeichnet, keine Mock-Zahlen */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-tight text-fg-muted uppercase">
            Vorbereitet · Integration ausstehend
          </h2>
          <span className="text-2xs text-fg-subtle font-mono">
            Werden in Phase 2 an echte Endpoints angebunden
          </span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          <PreparedPanel
            title="Portfolio Snapshot"
            reason="Paper-Portfolio-Snapshot mit Mark-to-Market und Exposure-Summary."
            detail="Backend: GET /operator/portfolio-snapshot — wird in Phase 2 angebunden."
          />
          <PreparedPanel
            title="Risk Meter"
            reason="Risiko-Score aus Exposure, Correlation und Paper-PnL-Drawdown."
            detail="Ableitung aus /operator/exposure-summary — Phase 2."
          />
          <PreparedPanel
            title="Equity / PnL Kurve"
            reason="Equity-Kurve aus Paper-Execution-Audit (Ledger)."
            detail="Quelle: artifacts/paper_execution_audit.jsonl — Aggregation in Phase 2."
          />
          <PreparedPanel
            title="Sentiment Stream"
            reason="Rolling Sentiment aus analysierten Dokumenten."
            detail="Erfordert neuen Aggregations-Endpoint — Phase 2."
          />
          <PreparedPanel
            title="Allocation"
            reason="Asset-Allokation aus Portfolio-Snapshot."
            detail="Phase 2."
          />
          <PreparedPanel
            title="AI Insights"
            reason="LLM-generierte Markt-Zusammenfassung mit Provider-Metadaten."
            detail="Erfordert neuen Insight-Endpoint — Phase 3."
          />
        </div>
      </section>

      <footer className="pt-4 pb-2 text-2xs text-fg-subtle font-mono flex items-center justify-between">
        <span>{t("dashboard.footer_version")}</span>
        <span>{t("dashboard.footer_phase")}</span>
      </footer>
    </div>
  );
}

function SignalQualityCard({ data }: { data: ReturnType<typeof useDashboardQuality>["data"] }) {
  const rows: Array<[string, string, string?]> = data
    ? [
        ["Actionable Rate", fmtPct(data.actionable_rate_pct)],
        ["False Positive", fmtPct(data.false_positive_pct), "neg"],
        ["High-P Hit Rate", fmtPct(data.high_priority_hit_rate_pct), "pos"],
        ["Low-P Hit Rate", fmtPct(data.low_priority_hit_rate_pct)],
        ["Directional Docs", String(data.directional_count)],
      ]
    : [];

  return (
    <Card padded>
      <CardHeader
        title="Signal-Qualität"
        right={
          <Badge tone="muted">
            <Activity size={10} />
            live
          </Badge>
        }
      />
      {data ? (
        <div className="space-y-1.5">
          {rows.map(([k, v, tone]) => (
            <div key={k} className="flex items-center justify-between text-xs">
              <span className="text-fg-muted">{k}</span>
              <span
                className={cn(
                  "font-mono font-semibold",
                  tone === "pos" && "text-pos",
                  tone === "neg" && "text-neg",
                )}
              >
                {v}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-xs text-fg-subtle">Keine Daten</div>
      )}
    </Card>
  );
}

function TradingLoopCard({ data }: { data: ReturnType<typeof useDashboardQuality>["data"] }) {
  const entries = data
    ? Object.entries(data.loop_status_counts).sort((a, b) => b[1] - a[1])
    : [];
  return (
    <Card padded>
      <CardHeader
        title="Trading Loop Status"
        right={
          <Badge tone="muted">
            <Activity size={10} />
            live
          </Badge>
        }
      />
      {entries.length > 0 ? (
        <div className="space-y-1.5">
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-center justify-between text-xs">
              <span className="text-fg-muted font-mono">{k}</span>
              <span className="font-mono font-semibold">{v}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-xs text-fg-subtle">Keine Cycles aufgezeichnet</div>
      )}
    </Card>
  );
}

function RecentAlertsCard({ data }: { data: ReturnType<typeof useDashboardQuality>["data"] }) {
  const rows = data?.recent_alerts ?? [];
  return (
    <Card padded>
      <CardHeader
        title="Letzte Directional Alerts"
        subtitle={`${rows.length} Einträge aus alert_audit.jsonl (jüngste zuerst)`}
        right={
          <Badge tone="muted" dot>
            live
          </Badge>
        }
      />
      {rows.length === 0 ? (
        <div className="text-xs text-fg-subtle py-6 text-center">Keine Alerts</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-2xs uppercase tracking-wide text-fg-subtle border-b border-line-subtle">
                <th className="text-left py-2 pr-3 font-medium">Doc</th>
                <th className="text-left py-2 pr-3 font-medium">Sentiment</th>
                <th className="text-center py-2 pr-3 font-medium">P</th>
                <th className="text-left py-2 pr-3 font-medium">Assets</th>
                <th className="text-left py-2 pr-3 font-medium">Dispatched</th>
                <th className="text-left py-2 pr-3 font-medium">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a, i) => (
                <tr key={`${a.doc_id}-${i}`} className="border-b border-line-subtle/60 last:border-0">
                  <td className="py-2 pr-3 font-mono text-2xs text-fg-muted">{a.doc_id}</td>
                  <td className="py-2 pr-3">
                    <SentimentBadge s={a.sentiment} />
                  </td>
                  <td className="py-2 pr-3 text-center font-mono">{a.priority ?? "—"}</td>
                  <td className="py-2 pr-3 font-mono text-2xs text-fg-muted">
                    {a.assets.length ? a.assets.join(", ") : "—"}
                  </td>
                  <td className="py-2 pr-3 font-mono text-2xs text-fg-subtle">{a.dispatched_at}</td>
                  <td className="py-2 pr-3">
                    <OutcomeBadge o={a.outcome} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function SentimentBadge({ s }: { s: string }) {
  if (!s) return <span className="text-fg-subtle">—</span>;
  const tone = s === "bullish" ? "pos" : s === "bearish" ? "neg" : "muted";
  return <Badge tone={tone}>{s}</Badge>;
}

function OutcomeBadge({ o }: { o: string }) {
  if (!o) return <Badge tone="muted">pending</Badge>;
  const tone = o === "hit" ? "pos" : o === "miss" ? "neg" : "muted";
  return <Badge tone={tone}>{o}</Badge>;
}

function fmtPct(v: number | null | undefined): string {
  return v != null ? `${v.toFixed(2)}%` : "—";
}
