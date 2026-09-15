import { useEffect, useState, type ReactNode } from "react";
import { Radio, Target, ShieldAlert, CheckCircle2, AlertCircle, ChevronDown, ChevronRight, ChevronUp, Wrench, Info } from "lucide-react";
import "@/styles/kai.tokens.css";
import { useKaiState } from "@/lib/useKaiState";
import { KpiCard } from "@/components/kpi/KpiCard";
import { QualityBarPanel } from "@/components/panels/QualityBar";
import { ActivePrecisionCard } from "@/components/panels/ActivePrecisionCard";
import { PerSourcePrecisionPanel } from "@/components/panels/PerSourcePrecisionPanel";
import { PerSourceStabilityPanel } from "@/components/panels/PerSourceStabilityPanel";
import { SourceReliabilityPanel } from "@/components/panels/SourceReliabilityPanel";
import { RegimeStatusPanel } from "@/components/panels/RegimeStatusPanel";
import { LightningPanel } from "@/components/panels/LightningPanel";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { LivePortfolioTiles } from "@/components/panels/LivePortfolioTiles";
import { PortfolioSnapshotProvider } from "@/state/PortfolioSnapshotProvider";
import { ReentryGatePanel } from "@/components/panels/ReentryGatePanel";
import { TruthStatusBar } from "@/components/panels/TruthStatusBar";
import { CommandHeader } from "@/components/layout/CommandHeader";
import { ExecutiveSnapshot } from "@/components/panels/ExecutiveSnapshot";
import { AcutePointsBoard } from "@/components/panels/AcutePointsBoard";
import { NodeStatusKpi } from "@/components/panels/NodeStatusKpi";
import { AuditIntegrityKpi } from "@/components/panels/AuditIntegrityKpi";
import { AuditChainKpi } from "@/components/panels/AuditChainKpi";
import { ReplayStatusKpi } from "@/components/panels/ReplayStatusKpi";
import { TruthLayerKpi } from "@/components/panels/TruthLayerKpi";
import { EdgeTruthPanel } from "@/components/panels/EdgeTruthPanel";
import { NOverviewPanel } from "@/components/panels/NOverviewPanel";
import { SignalHeatmapPanel } from "@/components/panels/SignalHeatmap";
import { PremiumRuntimeBanner } from "@/components/panels/PremiumRuntimeBanner";
import { AgentsStatusCard } from "@/components/panels/AgentsStatusCard";
import { TimerHealthCard } from "@/components/panels/TimerHealthCard";
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
import { useApi } from "@/lib/useApi";
import { fetchLightningStatus } from "@/lib/api";

// Geplante Bereiche: ehrlich als Roadmap markiert (kein Reifegrad-Prozent mehr).
// Portfolio Snapshot, Risk Meter und Allocation sind als echte Live-Tiles
// umgesetzt (LivePortfolioTiles) und deshalb hier entfernt.
//
// 2026-09-15 DALI v2.1: Jede Karte traegt jetzt drei Dinge, die vorher fehlten —
// den EINEN naechsten Schritt, ein ausdrueckliches "Termin offen" (es gibt
// keinen, und ein leeres Feld liest sich wie "demnaechst") und, falls es die
// Funktion schon irgendwo gibt, den Weg dorthin. Die AI-Insights-Karte sagte
// selbst, dass die Seite existiert, und verlinkte sie nicht.
type DashboardPreparedPanel = {
  title: string;
  reason: string;
  detail: string;
  /** Der eine naechste Schritt, der diese Karte live macht. */
  nextStep: string;
  /** Wenn es die Funktion schon als eigene Seite gibt: dorthin verlinken. */
  route?: Route;
  routeLabel?: string;
};

const PREPARED_PANELS: DashboardPreparedPanel[] = [
  {
    title: "Equity / PnL Kurve",
    reason: "Kapital-Entwicklung über Zeit aus dem Paper-Execution-Ledger.",
    detail: "Rohdaten in artifacts/paper_execution_audit.jsonl.",
    nextStep: "Equity/PnL-Aggregations-Endpoint bauen.",
  },
  {
    title: "Sentiment Stream",
    reason: "Rolling Sentiment aus analysierten News- und Social-Dokumenten.",
    detail: "Backend-Ingestion läuft, nur die Aggregation fehlt.",
    nextStep: "GET /operator/recent-news bereitstellen.",
  },
  {
    title: "AI Insights",
    reason: "LLM-generierte Markt-Zusammenfassung mit Provider-Metadaten.",
    detail: "Die vollständige AI-Insights-Seite ist bereits nutzbar — hier fehlt nur die Kurzfassung.",
    nextStep: "Stabilen Insight-Endpoint für die Kurzkarte festlegen.",
    route: "ai",
    routeLabel: "AI Insights öffnen",
  },
];

// WP-4 / DALI v2.1: Der Fokus-Umschalter sass unter dem EdgeTruthPanel — also
// hinter dem, was er filtert — und sprang bei jedem Reload zurueck. Er gehoert
// in den Seitenkopf und muss seine Wahl behalten.
const FOCUS_KEY = "kai-dashboard-focus";
type DashboardFocus = "alles" | "problem";

function readFocus(): DashboardFocus {
  try {
    return localStorage.getItem(FOCUS_KEY) === "problem" ? "problem" : "alles";
  } catch {
    return "alles";
  }
}

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
  // Dieselbe Bedingung wie im Panel (ReentryGatePanel: status === "expired" oder
  // Zieldatum in der Vergangenheit) — bewusst hier gespiegelt, weil das Dashboard
  // ueber die PLATZIERUNG entscheidet, nicht ueber den Inhalt.
  const reentryTargetDate = data?.reentry?.target_date ?? null;
  const reentryArchived =
    data?.reentry?.status === "expired" ||
    (reentryTargetDate != null && new Date(reentryTargetDate) < new Date());
  // Nur noch fuer den Status-Pill im Command-Header. Das KAI-Live-Widget, das
  // diesen Zustand ebenfalls las, ist entfernt.
  const kai = useKaiState();
  // 2026-09-15: EIN Abruf von /dashboard/api/lightning fuer Node-KPI und
  // Lightning-Karte (vorher zwei Poller auf denselben Endpunkt alle 60 s).
  const lightning = useApi(fetchLightningStatus, 60_000);
  const [focus, setFocus] = useState<DashboardFocus>(readFocus);
  useEffect(() => {
    try {
      localStorage.setItem(FOCUS_KEY, focus);
    } catch {}
  }, [focus]);

  return (
    // 2026-09-09: EIN Portfolio-Snapshot fuer Executive-Snapshot und
    // Portfolio-Kacheln. Vorher holten sie ihn dreimal und die daraus
    // ableitbare Exposure noch zweimal extra — fuenf serverseitige Neubauten
    // desselben Snapshots alle 30 s, je drei Vollpaesse ueber ein 5,2-MB-Audit.
    <PortfolioSnapshotProvider>
    <div className="p-4 xl:p-5 space-y-4 xl:space-y-5 max-w-[1680px] mx-auto">
      {/* ===== 1 BETRIEBSLAGE — Backend, Datenalter und Handelsfreigabe getrennt, sticky ===== */}
      {/* WP-1.1: sticky Command Header — verdichtete, nie wegscrollende Lage-Leiste. */}
      <PanelErrorBoundary name="Command-Header">
        <CommandHeader
          kai={kai.state === "ready" ? kai.data : null}
          kaiError={kai.state === "error"}
          quality={data}
          regime={regime}
          priorityGate={priorityGate}
          qualityState={q.state}
        />
      </PanelErrorBoundary>

      {/* Seitenkopf: Titel + Fokus. Der Report-Zeitpunkt stand fuenfmal auf
          dieser Seite (Kopf, Lage-Leiste, Quality-Bar-Fuss, Agent-Roster,
          Truth-Diagnose) — er steht jetzt AUSSCHLIESSLICH im Lage-Streifen. */}
      <header className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-display text-fg">{t("pages.dashboard.title")}</h1>
          <p className="text-xs text-fg-muted mt-1">
            {data
              ? t("pages.dashboard.sub", {
                  p: (fp ?? 0).toFixed(2),
                  n: String(data.forward_resolved),
                })
              : "Live-Daten vom Backend, Auto-Refresh alle 30 s."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-2xs uppercase tracking-wider text-fg-subtle">Ansicht</span>
          <div
            className="inline-flex rounded-sm border border-line-subtle bg-bg-2 p-0.5"
            role="group"
            aria-label="Fokus-Modus"
          >
            {(["alles", "problem"] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFocus(f)}
                aria-pressed={focus === f}
                className={cn(
                  "min-h-[44px] min-w-[44px] px-3 rounded-xs text-xs font-medium lg:min-h-0 lg:h-7 lg:px-2.5 lg:text-2xs",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70",
                  focus === f ? "bg-bg-1 text-fg shadow-panel" : "text-fg-muted hover:text-fg",
                )}
              >
                {f === "alles" ? "Alles" : "Problem-Fokus"}
              </button>
            ))}
          </div>
        </div>
      </header>


      {/* ===== 2 HANDLUNGSBEDARF — was jetzt zu tun ist, vor allen Zahlen ===== */}
      {/* WP-1.3: Akute Punkte — handlungsorientierte Triage der blockierenden Gates/Probleme. */}
      <PanelErrorBoundary name="Akute-Punkte">
        <AcutePointsBoard
          quality={data}
          regime={regime}
          priorityGate={priorityGate}
          qualityState={q.state}
        />
      </PanelErrorBoundary>

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

      {/* Premium-Runtime-Wahrheit — laut sichtbar wenn entry_mode/Bridge/Source
          neue Premium-Paper-Entries blockiert. Read-only, kein Live-Eingriff. */}
      <PanelErrorBoundary name="Premium-Runtime-Banner">
        <PremiumRuntimeBanner />
      </PanelErrorBoundary>

      {/* ===== 3 ENTSCHEIDUNGSGRUNDLAGE — Kapital, Wahrheit, Kennzahlen, Signale ===== */}
      {/* WP-1.2: Executive Snapshot — prominente Lageübersicht direkt unter dem Header. */}
      <PanelErrorBoundary name="Executive-Snapshot">
        <ExecutiveSnapshot />
      </PanelErrorBoundary>

      {/* Wahrheitsstatus — kompakte Truth-Layer-Leiste (DALI 2026-06-04).
          Macht historical/24h/read-only/blocked/unproven auf einen Blick sichtbar. */}
      <PanelErrorBoundary name="Wahrheitsstatus">
        <TruthStatusBar
          quality={data}
          regime={regime}
          priorityGate={priorityGate}
          qualityState={q.state}
        />
      </PanelErrorBoundary>

      {/* Edge-Wahrheit (canonical vs. voller Stream, Quarantäne-Transparenz).
          Stand bis 2026-08-17 unten auf /portfolio, obwohl der eigene Kommentar
          dort "sicht- + nachvollziehbar im Dashboard" verlangte — die zentrale
          Aussage des Systems (canonical-Edge = NO_GO) war damit erst nach
          Seitenwechsel und langem Scrollen zu sehen.
          Bewusst VOR dem Fokus-Umschalter: die Edge-Wahrheit ist Lage, nicht
          Detail, und darf im Problem-Fokus nicht ausgeblendet werden. */}
      <PanelErrorBoundary name="Edge-Wahrheit">
        <EdgeTruthPanel />
      </PanelErrorBoundary>

      <div className={cn(focus === "problem" ? "hidden" : "space-y-4 xl:space-y-5")}>
      {/* 2026-09-08: KAI-Live-Widget entfernt (Operator-Entscheidung). Es trug die
          Spruchmaschine, einen Timer alle 45-90 s ohne document.hidden-Guard sowie
          Chat und Spracheingabe — keines davon in Gebrauch. Die Backend-Routen
          /api/kai/chat und /api/kai/transcribe bleiben vorerst bestehen, damit der
          Schnitt in einem Commit rueckgaengig zu machen ist. */}

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
                {/* v1→v2-Cutover-Hinweis nur solange das Backend v1-Daten als
                    disqualifiziert meldet — sonst ist der 6-Wochen-alte
                    Schema-Hinweis nicht mehr relevant (Operator 2026-06-15). */}
                {data.audit_v1_disqualified && (
                  <button
                    type="button"
                    aria-label="Daten vor 2026-05-02 14:30 UTC unter Schema v1 (NEO-P-101-r2 disqualified, via Backfill rekonstruiert). v2-only-Werte ab Cutover."
                    title="Daten vor 2026-05-02 14:30 UTC unter Schema v1 (NEO-P-101-r2 disqualified, via Backfill rekonstruiert). v2-only-Werte ab Cutover."
                    className="ml-1 inline-flex items-center align-middle text-fg-subtle hover:text-fg-muted cursor-help"
                  >
                    <Info size={11} aria-hidden />
                  </button>
                )}
              </span>
            ) : undefined
          }
        />
        {/* WP-1.4: Node-/Chain-Status-KPI (ehrlich gegen bestehendes Lightning-Endpoint). */}
        <PanelErrorBoundary name="Node-Status-KPI">
          <NodeStatusKpi status={lightning} />
        </PanelErrorBoundary>
        {/* #314: Audit-Integritäts-KPI (ehrlich gegen bestehendes /dashboard/api/integrity). */}
        <PanelErrorBoundary name="Audit-Integrität-KPI">
          <AuditIntegrityKpi />
        </PanelErrorBoundary>
        {/* #314: Replay-SSOT-Status-KPI (Integrität des Audit-Replays, hintergrund-gecacht). */}
        <PanelErrorBoundary name="Replay-SSOT-KPI">
          <ReplayStatusKpi />
        </PanelErrorBoundary>
        {/* #314: Audit-Chain Tamper-Evidence-KPI (Decision-Journal Hash-Chain verify). */}
        <PanelErrorBoundary name="Audit-Chain-KPI">
          <AuditChainKpi />
        </PanelErrorBoundary>
        {/* #314: Truth-Layer-Status-KPI (aus dem gepollten quality-Vertrag abgeleitet). */}
        <PanelErrorBoundary name="Truth-Layer-KPI">
          <TruthLayerKpi quality={data} qualityState={q.state} />
        </PanelErrorBoundary>
      </div>

      {/* Re-Entry-Gate (TV-Pivot D-125). 2026-09-08: Ein ABGELAUFENES Ziel stand
          hier in voller Kartengroesse direkt unter der KPI-Reihe — 115 Tage nach
          Stichtag las sich das wie der aktive Systemzustand. Die Panel-Logik war
          bereits richtig (sie erkennt den Ablauf und klappt die toten Balken ein);
          falsch war nur die Hierarchie. Archiviertes wandert deshalb ans
          Seitenende, und der Prime-Platz sagt stattdessen, dass kein Gate gilt. */}
      {reentryArchived ? (
        <div className="border-line-subtle text-fg-muted flex flex-wrap items-center gap-2 rounded-md border border-dashed px-3 py-2 text-xs">
          <span className="text-fg font-semibold">Kein aktives Re-Entry-Gate</span>
          <span>
            Das letzte Ziel ({reentryTargetDate}) liegt in der Vergangenheit. Solange kein neues
            gesetzt ist, gibt es hier keinen Freigabezustand zu lesen — die Historie steht am
            Seitenende.
          </span>
        </div>
      ) : (
        <PanelErrorBoundary name="Re-Entry-Gate">
          <ReentryGatePanel
            quality={data}
            qualityState={q.state}
            qualityError={q.state === "error" ? q.error.message : null}
            priorityGate={priorityGate}
          />
        </PanelErrorBoundary>
      )}

      {/* Signal-Matrix + Markt-Snapshot. Keine erzwungene Gleichhoehe. */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-7">
          <PanelErrorBoundary name="Signal-Matrix">
            <SignalHeatmapPanel />
          </PanelErrorBoundary>
        </div>
        <div className="col-span-12 lg:col-span-5">
          <PanelErrorBoundary name="Markt-Snapshot">
            {isTradingViewEnabled() ? (
              /* 2026-09-15: schwere Diagramme erst bei Bedarf — Embed-Script und
                 iframe laden erst nach Klick, nicht mit der Seite. */
              <TradingViewChart heightClass="h-[320px]" title="Markt-Snapshot" deferUntilClick />
            ) : (
              <Card padded>
                <CardHeader title="Markt-Snapshot" right={<Badge tone="muted">offline</Badge>} />
                <div className="py-6 text-center text-xs text-fg-subtle">
                  TradingView deaktiviert — Chart unter „Märkte" verfügbar.
                </div>
              </Card>
            )}
          </PanelErrorBoundary>
        </div>
      </div>

      {/* Recent Alerts */}
      <PanelErrorBoundary name="Recent-Alerts">
        <RecentAlertsCard
          data={data}
          state={q.state}
          generatedAt={data?.generated_at ?? null}
        />
      </PanelErrorBoundary>

      {/* ===== 4 VERTIEFUNG — Nachweise, Quellen, Betrieb, Historie — einklappbar ===== */}
      <DeepSection title="Analysen & Qualität" hint="Quality-Bar, Signal-Qualität, Trading-Loop, Active-Precision, die fünf n">
        {/* Die 5 „n" — SSOT-Disambiguierung der fünf resolved/n-Zähler (Dali
            2026-06-13). Hebt das einzige n hervor, das fürs #167-Edge-Gate zählt
            (resolved_real), und dämpft die vier anderen Pipelines bewusst ab.
            Top-Platzierung: Operator soll es ohne Seitenwechsel sehen. */}
        <PanelErrorBoundary name="n-Übersicht">
          <NOverviewPanel />
        </PanelErrorBoundary>

        {/* Analytics-Grid. 2026-09-15: `lg:[&>*]:h-full` / `lg:[&>*]:flex-1`
            entfernt — die erzwungene Gleichhoehe erzeugte Leerflaechen unter der
            kuerzeren Karte. Eine Karte ist so hoch wie ihr Inhalt. */}
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 lg:col-span-8">
            <PanelErrorBoundary name="Quality-Bar">
              <QualityBarPanel data={data} />
            </PanelErrorBoundary>
          </div>
          <div className="col-span-12 lg:col-span-4 flex flex-col gap-4">
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
            Keine erzwungene Gleichhoehe: Karten wachsen mit ihrem Inhalt. */}
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 lg:col-span-7">
            <PanelErrorBoundary name="Active-Precision">
              <ActivePrecisionCard data={provenance} />
            </PanelErrorBoundary>
          </div>
          {/* V-DB4a 2026-05-08: Per-source Active-Precision aus Hold-Report. */}
          <div className="col-span-12 lg:col-span-5">
            <PanelErrorBoundary name="Source-Precision">
              <PerSourcePrecisionPanel data={data} />
            </PanelErrorBoundary>
          </div>
        </div>
      </DeepSection>

      <DeepSection title="Quellen" hint="Zuverlässigkeit und Drift je Quelle">
        {/* Source-Reliability + Rolling Stability (Drift-Detection). */}
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 lg:col-span-5">
            <PanelErrorBoundary name="Source-Reliability">
              <SourceReliabilityPanel data={data} />
            </PanelErrorBoundary>
          </div>
          <div className="col-span-12 lg:col-span-7">
            <PanelErrorBoundary name="Source-Stability">
              <PerSourceStabilityPanel data={data} />
            </PanelErrorBoundary>
          </div>
        </div>
      </DeepSection>

      <DeepSection title="Markt & Node" hint="Markt-Regime und Lightning-Node">
        {/* REGIME-R1 (2026-05-09): Markt-Regime read-only-Beobachter (BTC + ETH).
            Read-only-Phase, kein TradingLoop-Block — Operator-Validierung über 14 Tage. */}
        <PanelErrorBoundary name="Markt-Regime">
          <RegimeStatusPanel data={regime} />
        </PanelErrorBoundary>

        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 md:col-span-6 lg:col-span-4">
            <PanelErrorBoundary name="Lightning-Node">
              <LightningPanel status={lightning} />
            </PanelErrorBoundary>
          </div>
        </div>
      </DeepSection>

      <DeepSection title="Agenten & Betrieb" hint="Agent-Roster und Timer-Gesundheit">
        {/* Agent Roster */}
        <PanelErrorBoundary name="Agent-Roster">
          <AgentsStatusCard />
        </PanelErrorBoundary>

        {/* DALI-P-101 mount-point candidate */}
        <PanelErrorBoundary name="Timer-Gesundheit">
          <TimerHealthCard />
        </PanelErrorBoundary>
      </DeepSection>

      <DeepSection title="Portfolio-Kacheln" hint="Paper-Positionen, Risiko, Allokation">
        {/* Quick-Win-Tiles: echte Paper-Mode-Daten aus laufenden Read-Endpoints. */}
        <PanelErrorBoundary name="Live-Portfolio-Tiles">
          <LivePortfolioTiles />
        </PanelErrorBoundary>
      </DeepSection>

      {/* Roadmap-Bereiche — default collapsed Ribbon, expandable zu vollem Grid */}
      <PreparedSection />

      <DeepSection title="Historie" hint="Abgelaufene Re-Entry-Ziele als Evidenz">
        {/* Historie: das abgelaufene Re-Entry-Ziel bleibt als Evidenz erhalten,
            aber am Seitenende statt in Prime-Position. Das Panel klappt die toten
            Balken selbst ein (ExpiredCollapse). */}
        {reentryArchived && (
          <PanelErrorBoundary name="Re-Entry-Gate (Historie)">
            <ReentryGatePanel
              quality={data}
              qualityState={q.state}
              qualityError={q.state === "error" ? q.error.message : null}
              priorityGate={priorityGate}
            />
          </PanelErrorBoundary>
        )}
      </DeepSection>

      </div>

      <DashboardFooter />
    </div>
    </PortfolioSnapshotProvider>
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
            className="inline-flex items-center min-h-[44px] lg:min-h-0 px-1 hover:text-fg focus:text-fg focus:outline-none focus-visible:underline underline-offset-2"
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

/**
 * Vertiefung — Nachweise und Detailanalysen hinter einem Klick.
 *
 * Die Uebersicht trug 20 Panels in einer flachen Kette: Handlungsbedarf stand
 * hinter dem Kapitalstand, Quellen-Statistik und Historie konkurrierten mit dem
 * akuten Befund um dieselbe Aufmerksamkeit. Hier ist nichts entfernt — es
 * konkurriert nur nicht mehr.
 */
function DeepSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: ReactNode;
}) {
  // Uebersicht -> Details -> zurueck: der Auf-/Zu-Zustand bleibt je Gruppe
  // erhalten (localStorage, wie der Fokus-Modus). Sonst steht der Operator nach
  // jedem Seitenwechsel wieder vor sechs geschlossenen Gruppen.
  const storageKey = "kai-dashboard-deep:" + title;
  const [open, setOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(storageKey) === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(storageKey, open ? "1" : "0");
    } catch {}
  }, [open, storageKey]);
  return (
    <section className="space-y-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full min-h-[44px] flex items-center justify-between gap-3 rounded-sm border border-line-subtle bg-bg-2 px-4 py-2 text-left hover:bg-bg-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70"
      >
        <span className="min-w-0">
          <span className="block text-sm font-semibold tracking-tight text-fg">{title}</span>
          <span className="block text-2xs text-fg-subtle">{hint}</span>
        </span>
        <span className="text-fg-subtle shrink-0" aria-hidden>
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </button>
      {open && <div className="space-y-4 xl:space-y-5">{children}</div>}
    </section>
  );
}

function PreparedSection() {
  const [expanded, setExpanded] = useState(false);
  const { navigate } = useRouter();
  return (
    <section className="space-y-3">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
        className="w-full min-h-[44px] flex items-center justify-between gap-3 rounded-sm border border-line-subtle bg-bg-2 hover:bg-bg-3 transition-colors px-4 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70"
      >
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <Wrench size={14} className="text-fg-subtle shrink-0" aria-hidden />
          {/* "Vorbereitet" klang nach fertig-und-wartend. Diese Bereiche sind
              geplant, und zwar ohne Termin — beides steht jetzt im Kopf. */}
          <h2 className="text-sm font-semibold tracking-tight text-fg-muted shrink-0">
            Geplant
          </h2>
          <span className="text-2xs text-fg-subtle font-mono shrink-0">
            {PREPARED_PANELS.length}
            <span className="hidden sm:inline"> Bereiche · Termin offen</span>
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
        <span className="text-fg-subtle shrink-0" aria-hidden>
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
              status="roadmap"
              // Status, naechster Schritt und Terminlage in einer Zeile. Kein
              // erfundenes Datum, kein Quartal, kein "bald" — "Termin offen"
              // ist die Wahrheit und als solche lesbar.
              nextStep={p.nextStep}
              action={
                p.route && p.routeLabel ? (
                  <button
                    type="button"
                    onClick={() => navigate(p.route as Route)}
                    className="inline-flex items-center gap-1.5 min-h-[44px] lg:min-h-0 lg:h-7 px-2.5 rounded-sm border border-line-subtle bg-bg-2 text-2xs font-medium text-fg hover:bg-bg-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70"
                  >
                    {p.routeLabel}
                    <ChevronRight size={12} aria-hidden />
                  </button>
                ) : undefined
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}
