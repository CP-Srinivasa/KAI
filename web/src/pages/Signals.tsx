import { useMemo, useState } from "react";
import { AlertCircle, RefreshCw, XCircle, Ban, Activity, Radio } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { LABEL_DE, CYCLE_STATUS_EXPLAIN } from "@/lib/labels";
import { Badge, Button, Card } from "@/components/ui/Primitives";
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

type TabId = "all" | "no_signal" | "completed" | "consensus_rejected" | "order_failed" | "no_market_data";

// Die Page zeigt den Live-Zustand der Signal-Pipeline über den Trading-Loop.
// Reichhaltige Per-Signal-Metadaten (confidence, impact, priority, related news)
// erfordern einen neuen Backend-Endpoint — sind als vorbereiteter Bereich markiert.
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

  const tabs: { id: TabId; label: string; count: number }[] = [
    { id: "all", label: "Alle", count: allCycles.length },
    { id: "no_signal", label: "No Signal", count: counts.no_signal ?? 0 },
    { id: "completed", label: "Completed", count: counts.completed ?? 0 },
    { id: "consensus_rejected", label: "Consensus rejected", count: counts.consensus_rejected ?? 0 },
    { id: "order_failed", label: "Order failed", count: counts.order_failed ?? 0 },
    { id: "no_market_data", label: "No Market Data", count: counts.no_market_data ?? 0 },
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

      {cycles.state === "error" && (
        <ErrorCard kind={cycles.error.kind} message={cycles.error.message} path="/operator/trading-loop/recent-cycles" />
      )}

      <Card padded={false} className="synthwave-pulse-edge overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-4 pt-3 border-b border-line-subtle flex-wrap">
          <div className="flex items-center gap-0 flex-wrap -mb-px">
            {tabs.map((tb) => {
              const active = tab === tb.id;
              return (
                <button
                  key={tb.id}
                  onClick={() => setTab(tb.id)}
                  className={cn(
                    "h-9 px-3 text-xs font-medium inline-flex items-center gap-2 border-b-2 transition-colors",
                    active
                      ? "border-info text-fg shadow-[0_2px_8px_-2px_rgb(var(--info)/0.5)]"
                      : "border-transparent text-fg-muted hover:text-fg hover:border-line",
                  )}
                  aria-pressed={active}
                >
                  {tb.label}
                  <span
                    className={cn(
                      "text-2xs font-mono rounded-xs px-1",
                      active ? "bg-info/15 text-info" : "text-fg-subtle",
                    )}
                  >
                    {tb.count}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="text-2xs text-fg-subtle font-mono pb-2">
            {cycles.state === "ready" ? `${cycles.data.total_cycles} cycles insgesamt` : ""}
          </div>
        </div>

        <div className="overflow-x-auto">
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
                      title={tab === "all" ? "Keine Cycles in diesem Zeitfenster" : `Keine Cycles mit Status '${tabs.find((x) => x.id === tab)?.label}'`}
                      hint={
                        tab === "all"
                          ? "Die Trading-Loop hat in der aktuellen Window-Größe keine Cycles produziert. Quality-Report /dashboard/api/quality refreshed alle 30s."
                          : "Kein Cycle hat diesen Status. Zurück auf 'Alle' für vollen Verlauf."
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
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <PreparedPanel
          title="Signal-Detailansicht"
          reason="Rich-Metadaten pro Signal (Confidence, Impact, Priority, Quelle, verknüpfte News, Gate-Checks, Begründung)."
          detail="Erfordert neuen Endpoint GET /operator/signals/{id} — in Phase 2 geplant."
        />
        <PreparedPanel
          title="Signal-Filter & Backtesting"
          reason="Query-DSL über historische Signale mit Treffer-Analyse."
          detail="/query/validate ist verfügbar, historische Signal-Query fehlt — Phase 2."
        />
      </div>
    </div>
  );
}

// 2026-05-10 DALI-Signals-Klartext: Cycle-ID als Tooltip + relative Zeit als
// primäre Identifikation. Pipeline-Visualisierung mit Neon-Glow und Stop-
// Markierung wo der Cycle gescheitert ist.
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
    return "—";
  }
}

function CycleRow({ c }: { c: TradingCycle }) {
  const toneFor = (s: string) => {
    if (s === "completed") return "pos";
    if (s === "no_signal") return "muted";
    if (s === "no_market_data" || s === "stale_data") return "warn";
    if (s === "order_failed" || s === "consensus_rejected" || s === "priority_rejected") return "neg";
    return "neutral";
  };
  return (
    <tr className={cn("border-t border-line-subtle hover:bg-bg-2", c.status === "completed" && "bg-pos/[0.03]")}>
      <td className="px-4 py-2 whitespace-nowrap" title={`Cycle-ID: ${c.cycle_id}`}>
        <div className="font-mono text-xs text-fg">{formatCycleTime(c.started_at)}</div>
        <div className="text-2xs text-fg-subtle font-mono">{c.started_at.substring(11, 19)} UTC</div>
      </td>
      <td className="px-4 py-2 font-mono font-semibold whitespace-nowrap">{c.symbol}</td>
      <td className="px-4 py-2">
        <span title={CYCLE_STATUS_EXPLAIN[c.status] ?? c.status}>
          <Badge tone={toneFor(c.status)}>{LABEL_DE[c.status] ?? c.status}</Badge>
        </span>
      </td>
      <td className="px-4 py-2">
        <CyclePipeline c={c} />
      </td>
      <td className="px-4 py-2 font-mono text-2xs text-fg-subtle whitespace-nowrap">
        {formatDuration(c.started_at, c.completed_at ?? undefined)}
      </td>
    </tr>
  );
}

// 2026-05-10 DALI-Signals-Pipeline: 4 Steps als Neon-Lichtpunkte mit Verbindungs-
// linien. Operator: "DATA SIGNAL RISK ORDER schein tot keine Bewegung keine Farben."
// Lösung: erreicht=cyan/glow-info; Stop-Step bei Fehler=neg/glow-neg oder
// warn/glow-warn; last-reached pulst leicht (zeigt "hier ist der Cycle stehen
// geblieben").
function CyclePipeline({ c }: { c: TradingCycle }) {
  const steps = [
    { reached: c.market_data_fetched, label: "Markt-Daten geholt", key: "data" },
    { reached: c.signal_generated, label: "Signal erzeugt", key: "signal" },
    { reached: c.risk_approved, label: "Risk-Gate bestanden", key: "risk" },
    { reached: c.order_created, label: "Order erstellt", key: "order" },
  ];
  let lastReachedIdx = -1;
  for (let j = steps.length - 1; j >= 0; j--) {
    if (steps[j].reached) {
      lastReachedIdx = j;
      break;
    }
  }
  const isFail = c.status === "order_failed" || c.status === "consensus_rejected" || c.status === "priority_rejected";
  const isWarn = c.status === "no_market_data" || c.status === "stale_data";
  const stoppedAt = lastReachedIdx >= 0 && lastReachedIdx < steps.length - 1 && (isFail || isWarn) ? lastReachedIdx : -1;
  return (
    <div className="flex items-center">
      {steps.map((s, i) => {
        const isStopAt = i === stoppedAt;
        const dotClass = !s.reached
          ? "bg-fg-subtle/20"
          : isStopAt && isFail
            ? "bg-neg glow-neg animate-pulse"
            : isStopAt && isWarn
              ? "bg-warn glow-warn animate-pulse"
              : "bg-info glow-info";
        const lineClass =
          i < steps.length - 1
            ? steps[i + 1].reached
              ? "bg-info/45"
              : s.reached && isStopAt && isFail
                ? "bg-neg/30"
                : s.reached && isStopAt && isWarn
                  ? "bg-warn/30"
                  : "bg-fg-subtle/15"
            : "";
        return (
          <span key={s.key} className="inline-flex items-center">
            <span
              title={`${s.label}: ${s.reached ? "✓ erreicht" : "× nicht erreicht"}`}
              className={cn("inline-block h-2.5 w-2.5 rounded-full shrink-0 transition-transform hover:scale-150", dotClass)}
            />
            {i < steps.length - 1 && <span className={cn("inline-block h-px w-5 shrink-0", lineClass)} />}
          </span>
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

// Marker, damit unused-Ban-Symbol nicht linting-mäßig stört
void Ban;
void XCircle;
