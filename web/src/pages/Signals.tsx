import { useMemo, useState } from "react";
import { AlertCircle, RefreshCw, Activity, Radio } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import {
  LABEL_DE,
  CYCLE_STATUS_EXPLAIN,
  CYCLE_STATUS_TITLE,
  CYCLE_STATUS_REASON,
  CYCLE_STATUS_TAB,
  PIPELINE_STEP_LABELS,
  TERM_EXPLAIN,
  type PipelineStepKey,
} from "@/lib/labels";
import { Badge, Button, Card, InfoHint, SectionLabel, StatusDot } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import {
  fetchOperatorReadiness,
  fetchRecentCycles,
  type TradingCycle,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { PreparedPanel } from "@/components/panels/PreparedPanel";

type TabId = "all" | "order_failed" | "consensus_rejected" | "no_market_data" | "no_signal" | "completed";

// 2026-05-11 DALI-T6 (Signals-Pipeline-Control-Center):
// Vorher: 4 nackte Dots + EN-hardcoded Tabs, Tabelle wirkte als Debug-Overlay.
// Jetzt: priorisierte Tabs (Fehler vorne), Triage-Strip oben, Stepper mit
// Schritt-Namen + Tooltip, Mobile-Card-Stack, InfoHints für Fachbegriffe.

const FAIL_STATUSES = new Set(["order_failed", "sl_failed", "risk_rejected"]);
const WARN_STATUSES = new Set(["no_market_data", "stale_data", "gate_blocked"]);

type StepDef = { key: PipelineStepKey; reached: boolean };

function buildSteps(c: TradingCycle): StepDef[] {
  return [
    { key: "data", reached: c.market_data_fetched },
    { key: "signal", reached: c.signal_generated },
    { key: "risk", reached: c.risk_approved },
    { key: "order", reached: c.order_created },
    { key: "position", reached: false },
  ];
}


export function SignalsPage() {
  const { t } = useT();
  const readiness = useApi(fetchOperatorReadiness, 60_000);
  const cycles = useApi((s) => fetchRecentCycles(50, s), 20_000);
  const [tab, setTab] = useState<TabId>("all");

  const allCycles = cycles.state === "ready" ? cycles.data.recent_cycles : [];
  const counts = cycles.state === "ready" ? cycles.data.status_counts : {};

  const filtered = useMemo(() => {
    if (tab === "all") return allCycles;
    return allCycles.filter((c) => c.status === tab);
  }, [tab, allCycles]);

  // Triage-Counts aus vorhandenen Daten - kein neuer Endpoint.
  const activeFailCount = useMemo(
    () => allCycles.filter((c) => FAIL_STATUSES.has(c.status)).length,
    [allCycles],
  );
  const warnCount = useMemo(
    () => allCycles.filter((c) => WARN_STATUSES.has(c.status)).length,
    [allCycles],
  );
  const completedCount = useMemo(
    () => allCycles.filter((c) => c.status === "completed").length,
    [allCycles],
  );

  // Tab-Reihenfolge: Operator-Vorgabe - Fehler vorne, Completed hinten.
  const tabs: { id: TabId; label: string; count: number; tone: "info" | "neg" | "warn" | "muted" | "pos" }[] = [
    { id: "all", label: CYCLE_STATUS_TAB.all, count: allCycles.length, tone: "info" },
    { id: "order_failed", label: CYCLE_STATUS_TAB.order_failed, count: counts.order_failed ?? 0, tone: "neg" },
    { id: "consensus_rejected", label: CYCLE_STATUS_TAB.consensus_rejected, count: counts.consensus_rejected ?? 0, tone: "neg" },
    { id: "no_market_data", label: CYCLE_STATUS_TAB.no_market_data, count: counts.no_market_data ?? 0, tone: "warn" },
    { id: "no_signal", label: CYCLE_STATUS_TAB.no_signal, count: counts.no_signal ?? 0, tone: "muted" },
    { id: "completed", label: CYCLE_STATUS_TAB.completed, count: counts.completed ?? 0, tone: "pos" },
  ];

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.signals.title")}
        tone="info"
        icon={<Radio size={18} />}
        divider={false}
        sub={
          readiness.state === "ready"
            ? `Status: ${readiness.data.status} · Execution ${readiness.data.execution_enabled ? "aktiv" : "aus"} · Write-Back ${readiness.data.write_back_allowed ? "erlaubt" : "gesperrt"}`
            : "Live-Cycles der Trading-Loop"
        }
        right={
          <Button onClick={() => cycles.reload()} variant="outline" size="sm">
            <RefreshCw size={12} /> Aktualisieren
          </Button>
        }
      />

      {cycles.state === "ready" && allCycles.length > 0 && (
        <TriageStrip
          activeFailCount={activeFailCount}
          warnCount={warnCount}
          completedCount={completedCount}
          totalCycles={cycles.data.total_cycles}
        />
      )}

      {cycles.state === "error" && (
        <ErrorCard kind={cycles.error.kind} message={cycles.error.message} path="/operator/trading-loop/recent-cycles" />
      )}


      <Card padded={false} className="synthwave-pulse-edge overflow-hidden">
        <div className="flex items-start justify-between gap-3 px-4 pt-3 border-b border-line-subtle flex-wrap">
          <div className="flex items-center gap-0 flex-wrap -mb-px" role="tablist">
            {tabs.map((tb) => {
              const active = tab === tb.id;
              const activeBorderTone =
                tb.tone === "neg" ? "border-neg shadow-[0_2px_8px_-2px_rgb(var(--neg)/0.5)]"
                : tb.tone === "warn" ? "border-warn shadow-[0_2px_8px_-2px_rgb(var(--warn)/0.5)]"
                : tb.tone === "pos" ? "border-pos shadow-[0_2px_8px_-2px_rgb(var(--pos)/0.5)]"
                : tb.tone === "muted" ? "border-fg-subtle/60"
                : "border-info shadow-[0_2px_8px_-2px_rgb(var(--info)/0.5)]";
              const countTone =
                tb.tone === "neg" ? (active ? "bg-neg/15 text-neg" : "text-fg-subtle")
                : tb.tone === "warn" ? (active ? "bg-warn/15 text-warn" : "text-fg-subtle")
                : tb.tone === "pos" ? (active ? "bg-pos/15 text-pos" : "text-fg-subtle")
                : tb.tone === "muted" ? (active ? "bg-bg-3 text-fg-muted" : "text-fg-subtle")
                : (active ? "bg-info/15 text-info" : "text-fg-subtle");
              return (
                <button
                  key={tb.id}
                  onClick={() => setTab(tb.id)}
                  role="tab"
                  className={cn(
                    "h-9 px-3 text-xs font-medium inline-flex items-center gap-2 border-b-2 transition-colors",
                    active ? `text-fg ${activeBorderTone}` : "border-transparent text-fg-muted hover:text-fg hover:border-line",
                  )}
                  aria-pressed={active}
                  aria-selected={active}
                >
                  {tb.label}
                  <span className={cn("text-2xs font-mono rounded-xs px-1", countTone)}>
                    {tb.count}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="text-2xs text-fg-subtle font-mono pb-2 self-end inline-flex items-center gap-2">
            {cycles.state === "ready" ? `${cycles.data.total_cycles} cycles insgesamt` : ""}
            <InfoHint
              label="Pipeline-Schritte"
              hint={
                <span>
                  Marktdaten → Signal-Analyse → Risiko-Prüfung → Order-Ausführung.
                  Position-Management ist ein separater Folgeprozess nach erfolgreicher Order.
                </span>
              }
              side="left"
            />
          </div>
        </div>

        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-fg-subtle text-2xs uppercase tracking-wider">
                <th className="text-left font-semibold px-4 py-2">Wann</th>
                <th className="text-left font-semibold px-4 py-2">Symbol</th>
                <th className="text-left font-semibold px-4 py-2">Was passierte</th>
                <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">
                  Pipeline
                  <span className="ml-1 normal-case text-fg-subtle/70 font-normal">
                    (Daten → Signal → Risk → Order)
                  </span>
                </th>
                <th className="text-left font-semibold px-4 py-2">Dauer</th>
              </tr>
            </thead>
            <tbody>
              {cycles.state === "loading" && (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-fg-subtle">{t("common.loading")}</td>
                </tr>
              )}
              {cycles.state === "ready" && filtered.length === 0 && (
                <tr>
                  <td colSpan={5}>
                    <EmptyState
                      icon={<Activity size={18} />}
                      title={tab === "all" ? "Keine Cycles in diesem Zeitfenster" : `Keine Cycles mit Status: ${tabs.find((x) => x.id === tab)?.label}`}
                      hint={
                        tab === "all"
                          ? "Die Trading-Loop hat in der aktuellen Window-Größe keine Cycles produziert. Quality-Report /dashboard/api/quality refreshed alle 30s."
                          : "Kein Cycle hat diesen Status. Zurück auf Alle für vollen Verlauf."
                      }
                      action={
                        tab === "all" ? undefined : (
                          <Button onClick={() => setTab("all")} variant="outline" size="sm">
                            Alle Cycles zeigen
                          </Button>
                        )
                      }
                      className="m-4"
                    />
                  </td>
                </tr>
              )}
              {filtered.slice().reverse().map((c) => (
                <CycleRow key={c.cycle_id} c={c} />
              ))}
            </tbody>
          </table>
        </div>

        <div className="md:hidden divide-y divide-line-subtle">
          {cycles.state === "loading" && (
            <div className="px-4 py-6 text-center text-fg-subtle text-xs">{t("common.loading")}</div>
          )}
          {cycles.state === "ready" && filtered.length === 0 && (
            <EmptyState
              icon={<Activity size={18} />}
              title={tab === "all" ? "Keine Cycles" : `Keine Cycles: ${tabs.find((x) => x.id === tab)?.label}`}
              hint={tab === "all" ? "Trading-Loop hat keine Cycles im Window." : "Zurück auf Alle für vollen Verlauf."}
              action={tab === "all" ? undefined : (
                <Button onClick={() => setTab("all")} variant="outline" size="sm">Alle Cycles zeigen</Button>
              )}
              className="m-4"
            />
          )}
          {filtered.slice().reverse().map((c) => (
            <CycleCardMobile key={c.cycle_id} c={c} />
          ))}
        </div>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <PreparedPanel
          title="Signal-Detailansicht"
          reason="Rich-Metadaten pro Signal (Confidence, Impact, Priority, Quelle, verknüpfte News, Gate-Checks, Begründung)."
          detail="Erfordert neuen Endpoint GET /operator/signals/{id} - in Phase 2 geplant."
        />
        <PreparedPanel
          title="Signal-Filter und Backtesting"
          reason="Query-DSL über historische Signale mit Treffer-Analyse."
          detail="/query/validate ist verfügbar, historische Signal-Query fehlt - Phase 2."
        />
      </div>
    </div>
  );
}


// ---------- Triage-Strip ----------

function TriageStrip({
  activeFailCount,
  warnCount,
  completedCount,
  totalCycles,
}: {
  activeFailCount: number;
  warnCount: number;
  completedCount: number;
  totalCycles: number;
}) {
  const hasAttention = activeFailCount > 0 || warnCount > 0;
  return (
    <Card
      padded={false}
      className={cn(
        "px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-2",
        hasAttention && activeFailCount > 0 && "border-neg/30 glow-neg",
        hasAttention && activeFailCount === 0 && warnCount > 0 && "border-warn/30",
      )}
    >
      <SectionLabel className="mr-1">Triage</SectionLabel>
      <TriageStat
        tone="neg"
        label="Aktive Fehler"
        value={activeFailCount}
        pulse={activeFailCount > 0}
        hint="Order-Failed / SL-Failed / Risk-Rejected in den letzten Cycles."
      />
      <TriageStat
        tone="warn"
        label="Warnungen"
        value={warnCount}
        pulse={warnCount > 0 && activeFailCount === 0}
        hint="Keine Markt-Daten, veraltete Daten oder Quality-Gate-Block."
      />
      <TriageStat
        tone="pos"
        label="Trades ausgeführt"
        value={completedCount}
        hint="Cycles, die eine Order erfolgreich erzeugt haben."
      />
      <div className="text-2xs text-fg-subtle font-mono ml-auto">
        Fenster: {totalCycles} cycles
      </div>
    </Card>
  );
}

function TriageStat({
  tone,
  label,
  value,
  pulse = false,
  hint,
}: {
  tone: "neg" | "warn" | "pos";
  label: string;
  value: number;
  pulse?: boolean;
  hint: string;
}) {
  const valueColor =
    tone === "neg" ? "text-neg" : tone === "warn" ? "text-warn" : "text-pos";
  return (
    <div className="inline-flex items-center gap-2" title={hint}>
      <StatusDot tone={tone} pulse={pulse && value > 0} />
      <span className="text-2xs uppercase tracking-wider text-fg-subtle">{label}</span>
      <span className={cn("font-mono font-semibold text-sm tabular-nums", value > 0 ? valueColor : "text-fg-muted")}>
        {value}
      </span>
    </div>
  );
}

// ---------- Time + Duration helpers ----------

function formatCycleTime(iso: string): string {
  try {
    const dt = new Date(iso);
    const now = new Date();
    const diffSec = Math.round((now.getTime() - dt.getTime()) / 1000);
    if (diffSec < 60) return `vor ${diffSec}s`;
    if (diffSec < 3600) return `vor ${Math.round(diffSec / 60)}min`;
    if (diffSec < 86400) return `vor ${Math.round(diffSec / 3600)}h`;
    return `vor ${Math.round(diffSec / 86400)}d`;
  } catch {
    return iso.substring(11, 19);
  }
}

function formatDuration(start: string, end: string | undefined): string {
  if (!end) return "läuft";
  try {
    const startMs = new Date(start).getTime();
    const endMs = new Date(end).getTime();
    const diffMs = endMs - startMs;
    if (diffMs < 1000) return `${diffMs}ms`;
    if (diffMs < 60000) return `${(diffMs / 1000).toFixed(1)}s`;
    return `${Math.round(diffMs / 60000)}min`;
  } catch {
    return "-";
  }
}


// ---------- Cycle Row (Desktop) ----------

function rowToneClasses(status: string): string {
  if (FAIL_STATUSES.has(status)) return "border-l-2 border-l-neg/60 bg-neg/[0.03] hover:bg-neg/[0.06]";
  if (status === "consensus_rejected" || status === "priority_rejected") return "border-l-2 border-l-neg/40 hover:bg-bg-2";
  if (WARN_STATUSES.has(status)) return "border-l-2 border-l-warn/50 bg-warn/[0.03] hover:bg-warn/[0.06]";
  if (status === "completed") return "border-l-2 border-l-pos/40 bg-pos/[0.03] hover:bg-pos/[0.06]";
  if (status === "no_signal") return "opacity-70 hover:opacity-100 hover:bg-bg-2";
  return "hover:bg-bg-2";
}

function statusBadgeTone(status: string): "pos" | "neg" | "warn" | "muted" | "neutral" {
  if (status === "completed") return "pos";
  if (status === "no_signal") return "muted";
  if (WARN_STATUSES.has(status)) return "warn";
  if (FAIL_STATUSES.has(status) || status === "consensus_rejected" || status === "priority_rejected") return "neg";
  return "neutral";
}

function CycleRow({ c }: { c: TradingCycle }) {
  const title = CYCLE_STATUS_TITLE[c.status] ?? LABEL_DE[c.status] ?? c.status;
  const reason = CYCLE_STATUS_REASON[c.status];
  const explain = CYCLE_STATUS_EXPLAIN[c.status];
  const noteHint = c.status === "order_failed" && c.notes.length > 0 ? c.notes[0] : null;
  return (
    <tr className={cn("border-t border-line-subtle transition-colors", rowToneClasses(c.status))}>
      <td className="px-4 py-3 whitespace-nowrap align-top" title={`Cycle-ID: ${c.cycle_id}`}>
        <div className="font-mono text-xs text-fg">{formatCycleTime(c.started_at)}</div>
        <div className="text-2xs text-fg-subtle font-mono">{c.started_at.substring(11, 19)} UTC</div>
      </td>
      <td className="px-4 py-3 font-mono font-semibold whitespace-nowrap align-top">{c.symbol}</td>
      <td className="px-4 py-3 align-top max-w-[28rem]">
        <div className="flex items-start gap-2">
          <Badge tone={statusBadgeTone(c.status)}>{LABEL_DE[c.status] ?? c.status}</Badge>
          <div className="min-w-0">
            <div className="text-xs text-fg font-medium leading-snug" title={explain ?? c.status}>{title}</div>
            {reason && <div className="text-2xs text-fg-muted mt-0.5 leading-snug">{reason}</div>}
            {noteHint && (
              <div className="text-2xs text-fg-subtle mt-0.5 font-mono break-words" title="Aus cycle.notes[0]">
                Note: {noteHint}
              </div>
            )}
          </div>
        </div>
      </td>
      <td className="px-4 py-3 align-top">
        <CyclePipeline c={c} />
      </td>
      <td className="px-4 py-3 font-mono text-2xs text-fg-subtle whitespace-nowrap align-top">
        {formatDuration(c.started_at, c.completed_at ?? undefined)}
      </td>
    </tr>
  );
}

function CycleCardMobile({ c }: { c: TradingCycle }) {
  const title = CYCLE_STATUS_TITLE[c.status] ?? LABEL_DE[c.status] ?? c.status;
  const reason = CYCLE_STATUS_REASON[c.status];
  const explain = CYCLE_STATUS_EXPLAIN[c.status];
  const noteHint = c.status === "order_failed" && c.notes.length > 0 ? c.notes[0] : null;
  return (
    <div className={cn("p-4 transition-colors", rowToneClasses(c.status))}>
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="min-w-0">
          <div className="font-mono text-sm font-semibold text-fg">{c.symbol}</div>
          <div className="text-2xs text-fg-subtle font-mono" title={`Cycle-ID: ${c.cycle_id}`}>
            {formatCycleTime(c.started_at)} - {c.started_at.substring(11, 19)} UTC - {formatDuration(c.started_at, c.completed_at ?? undefined)}
          </div>
        </div>
        <Badge tone={statusBadgeTone(c.status)}>{LABEL_DE[c.status] ?? c.status}</Badge>
      </div>
      <div className="text-xs text-fg font-medium leading-snug" title={explain ?? c.status}>{title}</div>
      {reason && <div className="text-2xs text-fg-muted mt-0.5 leading-snug">{reason}</div>}
      {noteHint && (
        <div className="text-2xs text-fg-subtle mt-1 font-mono break-words">
          Note: {noteHint}
        </div>
      )}
      <div className="mt-3">
        <CyclePipelineVertical c={c} />
      </div>
    </div>
  );
}


// ---------- Pipeline-Stepper ----------

function lastReachedRealStep(steps: StepDef[]): number {
  for (let j = steps.length - 2; j >= 0; j--) {
    if (steps[j].reached) return j;
  }
  return -1;
}

function CyclePipeline({ c }: { c: TradingCycle }) {
  const steps = buildSteps(c);
  const lastIdx = lastReachedRealStep(steps);
  const isFail = FAIL_STATUSES.has(c.status) || c.status === "consensus_rejected" || c.status === "priority_rejected";
  const isWarn = WARN_STATUSES.has(c.status);
  const stoppedAt = lastIdx >= 0 && lastIdx < steps.length - 2 && (isFail || isWarn) ? lastIdx : -1;
  return (
    <div className="inline-flex items-center gap-0">
      {steps.map((s, i) => {
        const label = PIPELINE_STEP_LABELS[s.key];
        const isLast = i === steps.length - 1;
        const isPosition = s.key === "position";
        const isStopAt = i === stoppedAt;
        const dotTone =
          isPosition ? "bg-fg-subtle/15 border border-dashed border-fg-subtle/30"
          : !s.reached ? "bg-fg-subtle/20"
          : isStopAt && isFail ? "bg-neg glow-neg animate-pulse"
          : isStopAt && isWarn ? "bg-warn glow-warn animate-pulse"
          : "bg-info glow-info";
        const lineTone =
          isLast ? ""
          : isPosition ? "bg-fg-subtle/10"
          : steps[i + 1] && steps[i + 1].reached ? "bg-info/45"
          : s.reached && isStopAt && isFail ? "bg-neg/30"
          : s.reached && isStopAt && isWarn ? "bg-warn/30"
          : "bg-fg-subtle/15";
        const reachedMark = isPosition ? "nicht im Cycle-Schema" : s.reached ? "erreicht" : "nicht erreicht";
        const stopExtra = isStopAt ? ` - Hier gestoppt: ${CYCLE_STATUS_EXPLAIN[c.status] ?? ""}` : "";
        const posNote = isPosition ? " - nach Order-Ausführung in separatem Prozess." : "";
        return (
          <span key={s.key} className="inline-flex items-center">
            <span className="inline-flex flex-col items-center" title={`${label.short}: ${reachedMark}${stopExtra}${posNote}`}>
              <span
                className={cn(
                  "inline-block h-2.5 w-2.5 rounded-full shrink-0 transition-transform hover:scale-150",
                  dotTone,
                )}
              />
              <span className={cn(
                "mt-1 text-[10px] leading-none whitespace-nowrap",
                isPosition ? "text-fg-subtle/50 italic"
                : !s.reached ? "text-fg-subtle/70"
                : isStopAt && isFail ? "text-neg"
                : isStopAt && isWarn ? "text-warn"
                : "text-fg-muted",
              )}>{label.short}</span>
            </span>
            {!isLast && <span className={cn("inline-block h-px w-6 shrink-0 mt-[-12px] mx-0.5", lineTone)} />}
          </span>
        );
      })}
    </div>
  );
}

function CyclePipelineVertical({ c }: { c: TradingCycle }) {
  const steps = buildSteps(c);
  const lastIdx = lastReachedRealStep(steps);
  const isFail = FAIL_STATUSES.has(c.status) || c.status === "consensus_rejected" || c.status === "priority_rejected";
  const isWarn = WARN_STATUSES.has(c.status);
  const stoppedAt = lastIdx >= 0 && lastIdx < steps.length - 2 && (isFail || isWarn) ? lastIdx : -1;
  return (
    <div className="flex flex-col gap-0">
      {steps.map((s, i) => {
        const label = PIPELINE_STEP_LABELS[s.key];
        const isLast = i === steps.length - 1;
        const isPosition = s.key === "position";
        const isStopAt = i === stoppedAt;
        const dotTone =
          isPosition ? "bg-fg-subtle/15 border border-dashed border-fg-subtle/30"
          : !s.reached ? "bg-fg-subtle/20"
          : isStopAt && isFail ? "bg-neg glow-neg animate-pulse"
          : isStopAt && isWarn ? "bg-warn glow-warn animate-pulse"
          : "bg-info glow-info";
        const lineTone =
          isLast ? ""
          : isPosition ? "bg-fg-subtle/10"
          : steps[i + 1] && steps[i + 1].reached ? "bg-info/45"
          : s.reached && isStopAt && isFail ? "bg-neg/30"
          : s.reached && isStopAt && isWarn ? "bg-warn/30"
          : "bg-fg-subtle/15";
        const textTone =
          isPosition ? "text-fg-subtle/60 italic"
          : !s.reached ? "text-fg-subtle"
          : isStopAt && isFail ? "text-neg font-medium"
          : isStopAt && isWarn ? "text-warn font-medium"
          : "text-fg-muted";
        return (
          <div key={s.key} className="flex items-start gap-2.5">
            <div className="flex flex-col items-center pt-0.5">
              <span className={cn("inline-block h-2.5 w-2.5 rounded-full shrink-0", dotTone)} />
              {!isLast && <span className={cn("w-px h-6 shrink-0", lineTone)} />}
            </div>
            <div className="pb-2 -mt-0.5">
              <div className={cn("text-2xs leading-tight", textTone)}>
                {label.short}
                {isPosition && <span className="ml-1 text-fg-subtle/50 normal-case">(separater Folgeprozess)</span>}
              </div>
              {isStopAt && (
                <div className="text-2xs text-fg-subtle mt-0.5 leading-tight">
                  Hier gestoppt
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ErrorCard({ kind, message, path }: { kind: string; message: string; path: string }) {
  return (
    <Card padded className="border-neg/30 bg-neg/5">
      <div className="flex items-start gap-3 text-xs text-neg">
        <AlertCircle size={16} className="mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="font-semibold">Endpoint nicht erreichbar</div>
          <div className="text-fg-muted mt-1 break-words">{kind} · {message}</div>
          <div className="text-2xs text-fg-subtle mt-1 font-mono break-all">{path}</div>
        </div>
      </div>
    </Card>
  );
}

// Marker, damit TERM_EXPLAIN nicht als unused-Import gewarnt wird.
void TERM_EXPLAIN;
