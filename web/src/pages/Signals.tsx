import { useMemo, useState } from "react";
import { AlertCircle, RefreshCw, CheckCircle2, XCircle, Clock, Ban } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card } from "@/components/ui/Primitives";
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

      <Card padded={false}>
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-line-subtle flex-wrap">
          <div className="flex items-center gap-1 flex-wrap">
            {tabs.map((tb) => (
              <button
                key={tb.id}
                onClick={() => setTab(tb.id)}
                className={cn(
                  "h-8 px-3 rounded-sm text-xs font-medium inline-flex items-center gap-2",
                  tab === tb.id ? "bg-bg-3 text-fg" : "text-fg-muted hover:bg-bg-2 hover:text-fg",
                )}
              >
                {tb.label}
                <span className="text-2xs font-mono text-fg-subtle">{tb.count}</span>
              </button>
            ))}
          </div>
          <div className="text-2xs text-fg-subtle font-mono">
            {cycles.state === "ready" ? `${cycles.data.total_cycles} cycles insgesamt` : ""}
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-fg-subtle text-2xs uppercase tracking-wider">
                <th className="text-left font-semibold px-4 py-2">Cycle</th>
                <th className="text-left font-semibold px-4 py-2">Symbol</th>
                <th className="text-left font-semibold px-4 py-2">Status</th>
                <th className="text-center font-semibold px-4 py-2">Data</th>
                <th className="text-center font-semibold px-4 py-2">Signal</th>
                <th className="text-center font-semibold px-4 py-2">Risk</th>
                <th className="text-center font-semibold px-4 py-2">Order</th>
                <th className="text-left font-semibold px-4 py-2">Completed</th>
              </tr>
            </thead>
            <tbody>
              {cycles.state === "loading" && (
                <tr>
                  <td colSpan={8} className="px-4 py-6 text-center text-fg-subtle">{t("common.loading")}</td>
                </tr>
              )}
              {cycles.state === "ready" && filtered.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-6 text-center text-fg-subtle">{t("common.no_data")}</td>
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

function CycleRow({ c }: { c: TradingCycle }) {
  const toneFor = (s: string) => {
    if (s === "completed") return "pos";
    if (s === "no_signal") return "muted";
    if (s === "no_market_data" || s === "stale_data") return "warn";
    if (s === "order_failed" || s === "consensus_rejected") return "neg";
    return "neutral";
  };
  const completed = c.completed_at ?? "";
  return (
    <tr className="border-t border-line-subtle hover:bg-bg-2">
      <td className="px-4 py-2 font-mono text-2xs text-fg-subtle">{c.cycle_id.slice(-12)}</td>
      <td className="px-4 py-2 font-mono font-semibold">{c.symbol}</td>
      <td className="px-4 py-2">
        <Badge tone={toneFor(c.status)}>{c.status}</Badge>
      </td>
      <td className="px-4 py-2 text-center">
        <BoolDot v={c.market_data_fetched} />
      </td>
      <td className="px-4 py-2 text-center">
        <BoolDot v={c.signal_generated} />
      </td>
      <td className="px-4 py-2 text-center">
        <BoolDot v={c.risk_approved} />
      </td>
      <td className="px-4 py-2 text-center">
        <BoolDot v={c.order_created} />
      </td>
      <td className="px-4 py-2 font-mono text-2xs text-fg-subtle">
        {completed ? completed.substring(11, 19) : "—"}
      </td>
    </tr>
  );
}

function BoolDot({ v }: { v: boolean }) {
  if (v) return <CheckCircle2 size={13} className="text-pos inline" />;
  return <Clock size={13} className="text-fg-subtle inline" />;
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
