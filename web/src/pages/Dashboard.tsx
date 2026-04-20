import { useState } from "react";
import { Radio, Target, ShieldAlert, CheckCircle2, Activity, AlertCircle, Inbox, ChevronDown, ChevronUp, Wrench } from "lucide-react";
import { KpiCard } from "@/components/kpi/KpiCard";
import { QualityBarPanel } from "@/components/panels/QualityBar";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/EmptyState";
import { useT } from "@/i18n/I18nProvider";
import { useDashboardQuality } from "@/lib/useDashboardQuality";
import { formatAbsolute, formatRelative } from "@/lib/time";
import { cn } from "@/lib/utils";
import { useRouter, type Route } from "@/state/Router";

const PREPARED_PANELS: Array<{ title: string; reason: string; detail: string }> = [
  {
    title: "Portfolio Snapshot",
    reason: "Paper-Portfolio-Snapshot mit Mark-to-Market und Exposure-Summary.",
    detail: "Backend: GET /operator/portfolio-snapshot — wird in Phase 2 angebunden.",
  },
  {
    title: "Risk Meter",
    reason: "Risiko-Score aus Exposure, Correlation und Paper-PnL-Drawdown.",
    detail: "Ableitung aus /operator/exposure-summary — Phase 2.",
  },
  {
    title: "Equity / PnL Kurve",
    reason: "Equity-Kurve aus Paper-Execution-Audit (Ledger).",
    detail: "Quelle: artifacts/paper_execution_audit.jsonl — Aggregation in Phase 2.",
  },
  {
    title: "Sentiment Stream",
    reason: "Rolling Sentiment aus analysierten Dokumenten.",
    detail: "Erfordert neuen Aggregations-Endpoint — Phase 2.",
  },
  {
    title: "Allocation",
    reason: "Asset-Allokation aus Portfolio-Snapshot.",
    detail: "Phase 2.",
  },
  {
    title: "AI Insights",
    reason: "LLM-generierte Markt-Zusammenfassung mit Provider-Metadaten.",
    detail: "Erfordert neuen Insight-Endpoint — Phase 3.",
  },
];

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
          <h1 className="text-display text-fg">
            {t("pages.dashboard.title")}
          </h1>
          <p className="text-xs text-fg-muted mt-1">
            {data
              ? t("pages.dashboard.sub", {
                  p: (fp ?? 0).toFixed(2),
                  n: String(data.forward_resolved),
                })
              : "Live-Daten vom Backend, Auto-Refresh alle 30 s."}
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
          target={60}
          valueNumeric={fp ?? undefined}
          gapUnit="pp"
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
          target={50}
          valueNumeric={rc ?? undefined}
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
          target={0.4}
          valueNumeric={pc ?? undefined}
          deltaLabel="Ziel: ≥0.40"
          tone={pc != null && pc >= 0.4 ? "pos" : "warn"}
          icon={<Radio size={12} />}
        />
        <KpiCard
          label={t("primitives.paper_fills_real")}
          value={pf != null ? String(pf) : "—"}
          target={10}
          valueNumeric={pf ?? undefined}
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

      {/* Vorbereitete Bereiche — default collapsed Ribbon, expandable zu vollem Grid */}
      <PreparedSection />

      <DashboardFooter />
    </div>
  );
}

function DashboardFooter() {
  const { t } = useT();
  const { navigate } = useRouter();
  const buildMode = import.meta.env.MODE;
  const buildHash = (import.meta.env.VITE_BUILD_HASH as string | undefined) ?? "dev";
  const buildTooltip = t("dashboard.footer_build_tooltip", { mode: buildMode, hash: buildHash });

  const navItems: Array<{ route: Route; key: string }> = [
    { route: "agents", key: "footer_nav_agents" },
    { route: "alerts", key: "footer_nav_alerts" },
    { route: "settings", key: "footer_nav_settings" },
  ];

  return (
    <footer
      className="pt-4 pb-2 text-2xs text-fg-subtle font-mono flex flex-wrap items-center gap-x-3 gap-y-1 justify-between"
      role="contentinfo"
    >
      <span>{t("dashboard.footer_version")}</span>
      <nav aria-label="Footer-Navigation" className="flex items-center gap-x-3">
        {navItems.map((it) => (
          <button
            key={it.route}
            type="button"
            onClick={() => navigate(it.route)}
            className="hover:text-fg focus:text-fg focus:outline-none focus-visible:underline underline-offset-2"
          >
            {t(`dashboard.${it.key}`)}
          </button>
        ))}
        <span
          className="text-fg-muted"
          title={buildTooltip}
          aria-label={buildTooltip}
        >
          {t("dashboard.footer_build_label")} {buildHash}
        </span>
      </nav>
      <span>{t("dashboard.footer_phase")}</span>
    </footer>
  );
}

function PreparedSection() {
  const [expanded, setExpanded] = useState(false);
  return (
    <section className="space-y-3">
      <button
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
        className="w-full flex items-center justify-between gap-3 rounded-sm border border-line-subtle bg-bg-2 hover:bg-bg-3 transition-colors px-4 py-2.5 text-left"
      >
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <Wrench size={14} className="text-fg-subtle shrink-0" aria-hidden />
          <h2 className="text-sm font-semibold tracking-tight text-fg-muted uppercase shrink-0">
            Vorbereitet
          </h2>
          <span className="text-2xs text-fg-subtle font-mono shrink-0">
            {PREPARED_PANELS.length}
            <span className="hidden sm:inline"> Bereiche · Integration ausstehend</span>
          </span>
          {!expanded && (
            <div className="hidden md:flex items-center gap-1.5 flex-wrap min-w-0 overflow-hidden">
              {PREPARED_PANELS.map((p) => (
                <span
                  key={p.title}
                  className="inline-flex items-center gap-1 rounded-xs border border-dashed border-line px-1.5 py-0.5 text-2xs font-medium text-fg-subtle bg-bg-1"
                >
                  <span className="h-1 w-1 rounded-full bg-fg-subtle/60" aria-hidden />
                  {p.title}
                </span>
              ))}
            </div>
          )}
        </div>
        <span className="text-fg-subtle shrink-0">
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </button>
      {expanded && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {PREPARED_PANELS.map((p) => (
            <PreparedPanel key={p.title} title={p.title} reason={p.reason} detail={p.detail} />
          ))}
        </div>
      )}
    </section>
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
        <div className="py-4 text-center text-xs text-fg-subtle">
          Quality-Report lädt …
        </div>
      )}
    </Card>
  );
}

const LOOP_STATUS_TONE: Record<string, "pos" | "neg" | "warn" | "muted" | "info"> = {
  completed: "pos",
  no_signal: "muted",
  no_market_data: "warn",
  stale_data: "warn",
  risk_rejected: "warn",
  consensus_rejected: "neg",
  order_failed: "neg",
};

const TONE_BG: Record<string, string> = {
  pos: "bg-pos",
  neg: "bg-neg",
  warn: "bg-warn",
  muted: "bg-fg-subtle/40",
  info: "bg-info",
};

function TradingLoopCard({ data }: { data: ReturnType<typeof useDashboardQuality>["data"] }) {
  const entries = data
    ? Object.entries(data.loop_status_counts).sort((a, b) => b[1] - a[1])
    : [];
  const total = entries.reduce((acc, [, v]) => acc + v, 0);
  return (
    <Card padded>
      <CardHeader
        title="Trading Loop Status"
        subtitle={total > 0 ? `${total} Cycles im Fenster` : undefined}
        right={
          <Badge tone="muted">
            <Activity size={10} />
            live
          </Badge>
        }
      />
      {entries.length > 0 ? (
        <div className="space-y-2">
          <div
            className="h-2 w-full rounded-sm overflow-hidden flex bg-bg-3"
            role="img"
            aria-label={`Loop-Status-Verteilung: ${entries.map(([k, v]) => `${k} ${v}`).join(", ")}`}
          >
            {entries.map(([k, v]) => {
              const tone = LOOP_STATUS_TONE[k] ?? "muted";
              const pct = total > 0 ? (v / total) * 100 : 0;
              if (pct <= 0) return null;
              return (
                <div
                  key={k}
                  className={TONE_BG[tone]}
                  style={{ width: `${pct}%` }}
                  title={`${k}: ${v} (${pct.toFixed(1)}%)`}
                />
              );
            })}
          </div>
          <div className="space-y-1 pt-1">
            {entries.map(([k, v]) => {
              const tone = LOOP_STATUS_TONE[k] ?? "muted";
              const pct = total > 0 ? (v / total) * 100 : 0;
              return (
                <div key={k} className="flex items-center justify-between text-xs">
                  <span className="inline-flex items-center gap-2 text-fg-muted font-mono">
                    <span className={cn("inline-block h-2 w-2 rounded-xs", TONE_BG[tone])} aria-hidden />
                    {k}
                  </span>
                  <span className="font-mono font-semibold tabular-nums">
                    {v}
                    <span className="ml-1.5 text-2xs text-fg-subtle font-normal">{pct.toFixed(0)}%</span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="py-4 text-center text-xs text-fg-subtle">
          Noch keine Cycles im aktuellen Fenster
        </div>
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
        <EmptyState
          icon={<Inbox size={18} />}
          title="Noch keine Alerts in diesem Fenster"
          hint="Directional Alerts erscheinen hier in Echtzeit, sobald die Pipeline sie dispatched. Quality-Report refreshed alle 30s."
          className="my-2"
        />
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
                  <td className="py-2 pr-3 text-xs text-fg-muted" title={formatAbsolute(a.dispatched_at)}>
                    {formatRelative(a.dispatched_at)}
                  </td>
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
