import { AlertCircle, RefreshCw, CheckCircle2, XCircle } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card, CardHeader } from "@/components/ui/Primitives";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import { fetchTradingLoopStatus, fetchRecentCycles, type TradingCycle } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { modeTone, type TradingMode } from "@/state/AppState";

export function TradesPage() {
  const { t } = useT();
  const status = useApi(fetchTradingLoopStatus, 20_000);
  const cycles = useApi((s) => fetchRecentCycles(30, s), 15_000);

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.trades.title")}
        sub={
          status.state === "ready"
            ? `Mode: ${status.data.mode} · ${status.data.total_cycles} cycles · last: ${status.data.last_cycle_status ?? "—"}`
            : "Paper-Trading Loop Status"
        }
        right={
          <Button onClick={() => { status.reload(); cycles.reload(); }} variant="outline" size="sm">
            <RefreshCw size={12} /> Aktualisieren
          </Button>
        }
      />

      {status.state === "error" && <ErrorCard kind={status.error.kind} message={status.error.message} path="/operator/trading-loop/status" />}

      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <Kpi label="Mode" value={status.state === "ready" ? status.data.mode : "—"} tone={status.state === "ready" ? modeTone(status.data.mode as TradingMode) : "muted"} />
        <Kpi label="Total Cycles" value={status.state === "ready" ? String(status.data.total_cycles) : "—"} />
        <Kpi
          label="Last Status"
          value={status.state === "ready" ? status.data.last_cycle_status ?? "—" : "—"}
          tone={
            status.state === "ready" && status.data.last_cycle_status === "completed"
              ? "pos"
              : status.state === "ready" && status.data.last_cycle_status === "order_failed"
                ? "neg"
                : "warn"
          }
        />
        <Kpi
          label="Auto-Loop"
          value={status.state === "ready" ? (status.data.auto_loop_enabled ? "aktiv" : "aus") : "—"}
          tone={status.state === "ready" && status.data.auto_loop_enabled ? "pos" : "muted"}
        />
      </div>

      {status.state === "ready" && (
        <Card padded>
          <CardHeader
            title="Execution-Guardrails"
            right={
              <Badge tone={status.data.execution_enabled ? "pos" : "muted"} dot>
                {status.data.execution_enabled ? "execution enabled" : "paper/shadow only"}
              </Badge>
            }
          />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
            <RowKV k="write_back_allowed" v={String(status.data.write_back_allowed)} tone={status.data.write_back_allowed ? "pos" : "muted"} />
            <RowKV k="run_once_allowed" v={String(status.data.run_once_allowed)} tone={status.data.run_once_allowed ? "pos" : "warn"} />
            <RowKV k="run_once_block_reason" v={status.data.run_once_block_reason ?? "—"} />
            <RowKV k="last_cycle_id" v={status.data.last_cycle_id?.slice(-14) ?? "—"} />
            <RowKV k="last_cycle_symbol" v={status.data.last_cycle_symbol ?? "—"} />
            <RowKV
              k="last_cycle_completed_at"
              v={status.data.last_cycle_completed_at?.substring(0, 19).replace("T", " ") ?? "—"}
            />
          </div>
        </Card>
      )}

      <Card padded={false}>
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-line-subtle">
          <div className="text-sm font-semibold tracking-tight text-fg">Letzte Trading-Cycles</div>
          <div className="text-2xs text-fg-subtle font-mono">
            {cycles.state === "ready" ? `${cycles.data.recent_cycles.length} Einträge` : ""}
          </div>
        </div>
        {cycles.state === "error" ? (
          <div className="p-4">
            <ErrorCard kind={cycles.error.kind} message={cycles.error.message} path="/operator/trading-loop/recent-cycles" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-fg-subtle text-2xs uppercase tracking-wider">
                  <th className="text-left font-semibold px-4 py-2">Started</th>
                  <th className="text-left font-semibold px-4 py-2">Symbol</th>
                  <th className="text-left font-semibold px-4 py-2">Status</th>
                  <th className="text-center font-semibold px-4 py-2">Data</th>
                  <th className="text-center font-semibold px-4 py-2">Sig</th>
                  <th className="text-center font-semibold px-4 py-2">Risk</th>
                  <th className="text-center font-semibold px-4 py-2">Order</th>
                  <th className="text-center font-semibold px-4 py-2">Fill</th>
                  <th className="text-left font-semibold px-4 py-2">Notes</th>
                </tr>
              </thead>
              <tbody>
                {cycles.state === "loading" && (
                  <tr><td colSpan={9} className="px-4 py-6 text-center text-fg-subtle">{t("common.loading")}</td></tr>
                )}
                {cycles.state === "ready" && cycles.data.recent_cycles.length === 0 && (
                  <tr><td colSpan={9} className="px-4 py-6 text-center text-fg-subtle">{t("common.no_data")}</td></tr>
                )}
                {cycles.state === "ready" && cycles.data.recent_cycles.slice().reverse().map((c) => <CycleRow key={c.cycle_id} c={c} />)}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <PreparedPanel
        title="Guarded run-once trigger"
        reason="POST /operator/trading-loop/run-once (Idempotency-Key nötig) erlaubt gezielten Cycle im Paper/Shadow-Modus."
        detail="UI-Trigger erfordert confirm-flow + Idempotency-Key-Generierung — in Phase 2 geplant."
      />
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
  return (
    <tr className="border-t border-line-subtle hover:bg-bg-2">
      <td className="px-4 py-2 font-mono text-2xs text-fg-subtle">{c.started_at.substring(11, 19)}</td>
      <td className="px-4 py-2 font-mono font-semibold">{c.symbol}</td>
      <td className="px-4 py-2"><Badge tone={toneFor(c.status)}>{c.status}</Badge></td>
      <td className="px-4 py-2 text-center"><BoolDot v={c.market_data_fetched} /></td>
      <td className="px-4 py-2 text-center"><BoolDot v={c.signal_generated} /></td>
      <td className="px-4 py-2 text-center"><BoolDot v={c.risk_approved} /></td>
      <td className="px-4 py-2 text-center"><BoolDot v={c.order_created} /></td>
      <td className="px-4 py-2 text-center"><BoolDot v={c.fill_simulated} /></td>
      <td className="px-4 py-2 font-mono text-2xs text-fg-subtle max-w-[360px] truncate">
        {c.notes.join(" · ") || "—"}
      </td>
    </tr>
  );
}

function BoolDot({ v }: { v: boolean }) {
  return v ? <CheckCircle2 size={13} className="text-pos inline" /> : <XCircle size={13} className="text-fg-subtle inline" />;
}

function Kpi({ label, value, tone = "neutral" }: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "warn" | "info" | "neutral" | "muted";
}) {
  return (
    <Card padded>
      <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">{label}</div>
      <div className={cn(
        "mt-1 font-mono text-lg font-semibold",
        tone === "pos" && "text-pos",
        tone === "neg" && "text-neg",
        tone === "warn" && "text-warn",
        tone === "info" && "text-info",
        tone === "muted" && "text-fg-muted",
      )}>
        {value}
      </div>
    </Card>
  );
}

function RowKV({ k, v, tone }: { k: string; v: string; tone?: "pos" | "neg" | "warn" | "muted" }) {
  return (
    <div className="flex items-center justify-between gap-2 overflow-hidden border-b border-line-subtle/50 py-1">
      <span className="min-w-0 truncate font-mono text-2xs text-fg-subtle">{k}</span>
      <span className={cn(
        "shrink-0 font-mono text-right",
        tone === "pos" && "text-pos",
        tone === "neg" && "text-neg",
        tone === "warn" && "text-warn",
        tone === "muted" && "text-fg-muted",
      )}>{v}</span>
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
