import { useState } from "react";
import { Radio, Target, ShieldAlert, CheckCircle2, AlertCircle, ChevronDown, ChevronUp, Wrench, Info } from "lucide-react";
import "@/styles/kai.tokens.css";
import { KaiLiveWidget } from "@/components/kai/KaiLiveWidget";
import { useKaiState } from "@/lib/useKaiState";
import { KpiCard } from "@/components/kpi/KpiCard";
import { QualityBarPanel } from "@/components/panels/QualityBar";
import { ActivePrecisionCard } from "@/components/panels/ActivePrecisionCard";
import { PerSourcePrecisionPanel } from "@/components/panels/PerSourcePrecisionPanel";
import { PerSourceStabilityPanel } from "@/components/panels/PerSourceStabilityPanel";
import { SourceReliabilityPanel } from "@/components/panels/SourceReliabilityPanel";
import { RegimeStatusPanel } from "@/components/panels/RegimeStatusPanel";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { ReentryGatePanel } from "@/components/panels/ReentryGatePanel";
import { SignalHeatmapPanel } from "@/components/panels/SignalHeatmap";
import { AgentsStatusCard } from "@/components/panels/AgentsStatusCard";
import { SignalQualityCard } from "@/components/panels/SignalQualityCard";
import { TradingLoopCard } from "@/components/panels/TradingLoopCard";
import { RecentAlertsCard } from "@/components/panels/RecentAlertsCard";
import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import { TradingViewChart, isTradingViewEnabled } from "@/components/trading/tradingview";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { useT } from "@/i18n/I18nProvider";
import { useDashboardQuality } from "@/lib/useDashboardQuality";
import { useDashboardProvenance } from "@/lib/useDashboardProvenance";
import { useDashboardRegime } from "@/lib/useDashboardRegime";
import { usePriorityGate } from "@/lib/usePriorityGate";
import { cn } from "@/lib/utils";
import {
  tierLiftTone,
  formatTierLift,
  evaluateTierLiftSignificance,
  TIER_LIFT_INSIGNIFICANT_LABEL,
  TIER_LIFT_INSIGNIFICANT_TOOLTIP,
} from "@/lib/tierLift";
import { useRouter, type Route } from "@/state/Router";

// DALI v2 S3 M1d: PREPARED_PANELS mit explizitem Entwicklungsstatus (Master-Spec G3).
// Operator sieht sofort welches Modul in welcher Phase steckt - statt nur
// "Integration ausstehend" als Pauschal-Badge.
type DashboardPreparedPanel = {
  title: string;
  reason: string;
  detail: string;
  phase: "planning" | "skeleton" | "beta" | "stable";
  progress?: number;
  timeline?: string;
};

const PREPARED_PANELS: DashboardPreparedPanel[] = [
  {
    title: "Portfolio Snapshot",
    reason: "Paper-Portfolio mit Mark-to-Market und Exposure-Summary auf dem Dashboard.",
    detail: "Backend bereit: GET /operator/portfolio-snapshot. Eigene Portfolio-Page liest das schon — Dashboard-Tile fehlt.",
    phase: "skeleton",
    progress: 60,
    timeline: "geplant für Sprint nach Backtesting-Endpoint",
  },
  {
    title: "Risk Meter",
    reason: "Risiko-Score aus Exposure, Korrelation und Paper-PnL-Drawdown als Hero-KPI.",
    detail: "Backend bereit: GET /operator/exposure-summary. Risk-Page nutzt es — Dashboard-Tile fehlt.",
    phase: "skeleton",
    progress: 55,
    timeline: "Phase 2 — gekoppelt an Risk-Modul (S5)",
  },
  {
    title: "Equity / PnL Kurve",
    reason: "Kapital-Entwicklung über Zeit aus Paper-Execution-Audit (Ledger).",
    detail: "Rohdaten in artifacts/paper_execution_audit.jsonl. Aggregations-Endpoint geplant.",
    phase: "planning",
    progress: 20,
    timeline: "Phase 2 — nach Sub-Account-KYC",
  },
  {
    title: "Sentiment Stream",
    reason: "Rolling Sentiment aus analysierten News- und Social-Dokumenten.",
    detail: "Backend-Ingestion läuft. Aggregations-Endpoint für Frontend-Stream offen.",
    phase: "planning",
    progress: 25,
    timeline: "Phase 2 — gekoppelt an News-Modul (S6)",
  },
  {
    title: "Allocation",
    reason: "Asset-Allokation aus Portfolio-Snapshot als Donut/Treemap.",
    detail: "Daten in Portfolio-Snapshot vorhanden. UI-Visualisierung fehlt.",
    phase: "skeleton",
    progress: 45,
    timeline: "Phase 2 — gekoppelt an Portfolio-Modul (S4)",
  },
  {
    title: "AI Insights",
    reason: "LLM-generierte Markt-Zusammenfassung mit Provider-Metadaten.",
    detail: "Eigene AI-Insights-Page existiert. Dashboard-Tile als Kurzform fehlt.",
    phase: "beta",
    progress: 70,
    timeline: "Phase 3 — nach Insight-Endpoint-Stabilisierung",
  },
];

export function Dashboard() {
  const { t } = useT();
  const q = useDashboardQuality();
  const data = q.state === "ready" ? q.data : null;
  const p = useDashboardProvenance();
  const provenance = p.state === "ready" ? p.data : null;
  const r = useDashboardRegime();
  const regime = r.state === "ready" ? r.data : null;
  const pg = usePriorityGate();
  const priorityGate = pg.state === "ready" ? pg.data : null;

  const fp = data?.forward_precision_pct ?? null;
  const rc = data?.resolved_count ?? null;
  // D-149: priority_corr ist deprecated; wir nutzen priority_tier_lift_pct
  // (High-Conviction-HitRate minus Standard-Tier-HitRate).
  const ptl = data?.priority_tier_lift_pct ?? null;
  const pf = data?.paper_fills ?? null;
  const kai = useKaiState();

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
        <Card padded className="border-neg/30 bg-neg/5 attention-breathe-neg">
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

      {/* KAI LIVE — Persona non grata. Hero-Strip per DALI-Audit 2026-05-03. */}
      {kai.state === "ready" && (
        <PanelErrorBoundary name="KaiLiveWidget">
          <KaiLiveWidget runtimeState={kai.data} language="de" />
        </PanelErrorBoundary>
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
          label={t("primitives.priority_tier_lift")}
          value={formatTierLift(ptl)}
          target={15}
          valueNumeric={ptl ?? undefined}
          gapUnit="pp"
          deltaLabel="Ziel: ≥+15pp"
          tone={tierLiftTone(ptl)}
          icon={<Radio size={12} />}
          helper={
            data?.priority_tier_lift_pct == null
              ? t("primitives.priority_tier_lift_insufficient")
              : data?.priority_tier_high_conviction_resolved != null &&
                  data?.priority_tier_standard_resolved != null
                ? (() => {
                    // V-DB5 A-1/A-2/A-3 + D-1: Single-source-of-truth via lib/tierLift.
                    const sig = evaluateTierLiftSignificance(data);
                    const hLo = data.priority_tier_high_conviction_ci_low_pct;
                    const hHi = data.priority_tier_high_conviction_ci_high_pct;
                    const sLo = data.priority_tier_standard_ci_low_pct;
                    const sHi = data.priority_tier_standard_ci_high_pct;
                    return (
                      <span className="font-mono">
                        n={sig.sampleN}
                        {" "}· P≥{data.priority_tier_high_conviction_threshold ?? "?"}:
                        {" "}<span className="text-pos">{data.priority_tier_high_conviction_hit_rate_pct?.toFixed(1) ?? "?"}%</span>
                        {hLo != null && hHi != null && (
                          <span className="text-fg-subtle/80"> [{hLo.toFixed(0)}–{hHi.toFixed(0)}]</span>
                        )}
                        {" "}· P&lt;{data.priority_tier_high_conviction_threshold ?? "?"}:
                        {" "}<span className="text-warn">{data.priority_tier_standard_hit_rate_pct?.toFixed(1) ?? "?"}%</span>
                        {sLo != null && sHi != null && (
                          <span className="text-fg-subtle/80"> [{sLo.toFixed(0)}–{sHi.toFixed(0)}]</span>
                        )}
                        {sig.isSignificant === false && (
                          <span
                            className="ml-1.5 text-fg-subtle italic"
                            title={TIER_LIFT_INSIGNIFICANT_TOOLTIP}
                          >
                            {TIER_LIFT_INSIGNIFICANT_LABEL}
                          </span>
                        )}
                      </span>
                    );
                  })()
                : undefined
          }
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
                <button
                  type="button"
                  aria-label="Daten vor 2026-05-02 14:30 UTC unter Schema v1 (NEO-P-101-r2 disqualified, via Backfill rekonstruiert). v2-only-Werte ab Cutover."
                  title="Daten vor 2026-05-02 14:30 UTC unter Schema v1 (NEO-P-101-r2 disqualified, via Backfill rekonstruiert). v2-only-Werte ab Cutover."
                  className="ml-1 inline-flex items-center align-middle text-fg-subtle hover:text-fg-muted cursor-help"
                >
                  <Info size={11} aria-hidden />
                </button>
              </span>
            ) : undefined
          }
        />
      </div>

      {/* Re-Entry-Gate (TV-Pivot D-125 · Stichtag 2026-05-16) */}
      <PanelErrorBoundary name="Re-Entry-Gate">
        <ReentryGatePanel
          quality={data}
          qualityState={q.state}
          qualityError={q.state === "error" ? q.error.message : null}
          priorityGate={priorityGate}
        />
      </PanelErrorBoundary>

      {/* REGIME-R1 (2026-05-09): Markt-Regime read-only-Beobachter (BTC + ETH).
          Read-only-Phase, kein TradingLoop-Block — Operator-Validierung über 14 Tage. */}
      <PanelErrorBoundary name="Markt-Regime">
        <RegimeStatusPanel data={regime} />
      </PanelErrorBoundary>

      {/* Aktiver Analytics-Grid — Symmetrie ab lg: linke Card streckt sich,
          rechter Stack teilt sich die Hoehe via flex-1. Mobile bleibt block-stack. */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-8 lg:[&>*]:h-full">
          <PanelErrorBoundary name="Quality-Bar">
            <QualityBarPanel data={data} />
          </PanelErrorBoundary>
        </div>
        <div className="col-span-12 lg:col-span-4 flex flex-col gap-4 lg:[&>*]:flex-1">
          <PanelErrorBoundary name="Signal-Qualität">
            <SignalQualityCard
              data={data}
              state={q.state}
              generatedAt={data?.generated_at ?? null}
            />
          </PanelErrorBoundary>
          <PanelErrorBoundary name="Trading-Loop-Status">
            <TradingLoopCard
              data={data}
              state={q.state}
              generatedAt={data?.generated_at ?? null}
            />
          </PanelErrorBoundary>
        </div>
      </div>

      {/* Active-Precision-Split (Source-Breakdown mit Wilson-CI).
          Beide Spalten strecken Card auf gleiche Hoehe ab lg. */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-7 lg:[&>*]:h-full">
          <PanelErrorBoundary name="Active-Precision">
            <ActivePrecisionCard data={provenance} />
          </PanelErrorBoundary>
        </div>
        {/* V-DB4a 2026-05-08: Per-source Active-Precision aus Hold-Report. */}
        <div className="col-span-12 lg:col-span-5 lg:[&>*]:h-full">
          <PanelErrorBoundary name="Source-Precision">
            <PerSourcePrecisionPanel data={data} />
          </PanelErrorBoundary>
        </div>
      </div>

      {/* Source-Reliability + Rolling Stability (Drift-Detection). */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-5 lg:[&>*]:h-full">
          <PanelErrorBoundary name="Source-Reliability">
            <SourceReliabilityPanel data={data} />
          </PanelErrorBoundary>
        </div>
        <div className="col-span-12 lg:col-span-7 lg:[&>*]:h-full">
          <PanelErrorBoundary name="Source-Stability">
            <PerSourceStabilityPanel data={data} />
          </PanelErrorBoundary>
        </div>
      </div>

      {/* Signal-Matrix + Market-Snapshot — gleiche Hoehe ab lg. */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-7 lg:[&>*]:h-full">
          <PanelErrorBoundary name="Signal-Matrix">
            <SignalHeatmapPanel />
          </PanelErrorBoundary>
        </div>
        <div className="col-span-12 lg:col-span-5 lg:[&>*]:h-full">
          <PanelErrorBoundary name="Markt-Snapshot">
            {isTradingViewEnabled() ? (
              <TradingViewChart heightClass="h-[320px]" title="Markt-Snapshot" />
            ) : (
              <Card padded>
                <CardHeader title="Markt-Snapshot" right={<Badge tone="muted">offline</Badge>} />
                <div className="py-8 text-center text-xs text-fg-subtle">
                  TradingView deaktiviert — Chart unter „Märkte" verfügbar.
                </div>
              </Card>
            )}
          </PanelErrorBoundary>
        </div>
      </div>

      {/* Agent Roster */}
      <PanelErrorBoundary name="Agent-Roster">
        <AgentsStatusCard />
      </PanelErrorBoundary>

      {/* Recent Alerts */}
      <PanelErrorBoundary name="Recent-Alerts">
        <RecentAlertsCard
          data={data}
          state={q.state}
          generatedAt={data?.generated_at ?? null}
        />
      </PanelErrorBoundary>

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
            <PreparedPanel
              key={p.title}
              title={p.title}
              reason={p.reason}
              detail={p.detail}
              phase={p.phase}
              progress={p.progress}
              timeline={p.timeline}
            />
          ))}
        </div>
      )}
    </section>
  );
}
