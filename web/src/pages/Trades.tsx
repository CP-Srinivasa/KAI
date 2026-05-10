import { AlertCircle, RefreshCw, CheckCircle2, XCircle, ArrowLeftRight, Activity } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card, CardHeader, Kpi } from "@/components/ui/Primitives";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import { fetchTradingLoopStatus, fetchRecentCycles, type TradingCycle } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { LABEL_DE, CYCLE_STATUS_EXPLAIN, humanizeLabel } from "@/lib/labels";

// 2026-05-10 DALI-T1: Klartext-Synopsis aus Cycle-Buckets ableiten.
// Operator-Frage "was sagt mir die Seite?" wird in 1 Satz beantwortet.
function summarizeCycles(cyclesList: TradingCycle[]): string {
  if (cyclesList.length === 0) return "Noch keine Cycles in der jüngsten Historie.";
  const counts: Record<string, number> = {};
  for (const c of cyclesList) counts[c.status] = (counts[c.status] ?? 0) + 1;
  const parts: string[] = [];
  const completed = counts.completed ?? 0;
  if (completed > 0) parts.push(`${completed}× ausgeführt`);
  if (counts.no_signal) parts.push(`${counts.no_signal}× kein Signal`);
  if (counts.no_market_data) parts.push(`${counts.no_market_data}× keine Markt-Daten`);
  if (counts.consensus_rejected) parts.push(`${counts.consensus_rejected}× Konsens abgelehnt`);
  if (counts.order_failed) parts.push(`${counts.order_failed}× Order fehlgeschlagen`);
  if (counts.stale_data) parts.push(`${counts.stale_data}× veraltete Daten`);
  // letzten ausgeführten Trade finden
  const lastCompleted = [...cyclesList].reverse().find((c) => c.status === "completed");
  const lastSegment = lastCompleted
    ? ` — letzter Trade ${lastCompleted.symbol} ${formatTimeShort(lastCompleted.started_at)}`
    : "";
  return `Letzte ${cyclesList.length} Cycles: ${parts.join(", ") || "—"}${lastSegment}.`;
}

function formatTimeShort(iso: string): string {
  try {
    const dt = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - dt.getTime();
    const diffH = Math.round(diffMs / 3600000);
    if (diffH < 1) return "vor weniger als 1h";
    if (diffH < 24) return `vor ${diffH}h`;
    return `vor ${Math.round(diffH / 24)}d`;
  } catch {
    return iso.substring(11, 16);
  }
}

const CYCLE_DOT_TONE: Record<string, string> = {
  completed: "bg-pos",
  no_signal: "bg-fg-subtle/40",
  no_market_data: "bg-warn",
  stale_data: "bg-warn",
  consensus_rejected: "bg-neg",
  order_failed: "bg-neg",
};

// Notes-humanize: kurz lesbar statt snake_case-Roh-String.
function humanizeNote(note: string): string {
  // Beispiel: "signal_block:reason=consensus" → "signal block (reason=consensus)"
  const [head, tail] = note.split(":");
  const headHum = LABEL_DE[head ?? ""] ?? head?.replace(/_/g, " ") ?? note;
  if (tail) return `${headHum} (${tail})`;
  return headHum;
}

export function TradesPage() {
  const { t } = useT();
  const status = useApi(fetchTradingLoopStatus, 20_000);
  const cycles = useApi((s) => fetchRecentCycles(30, s), 15_000);

  const cyclesList = cycles.state === "ready" ? cycles.data.recent_cycles : [];
  const completed24h = cyclesList.filter((c) => c.status === "completed").length;

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.trades.title")}
        tone="pos"
        icon={<ArrowLeftRight size={18} />}
        sub={
          status.state === "ready"
            ? `Mode: ${status.data.mode} · Letzter Status: ${status.data.last_cycle_status ?? "—"}`
            : "Was wurde zuletzt ausgeführt und mit welchem Ergebnis."
        }
        right={
          <Button onClick={() => { status.reload(); cycles.reload(); }} variant="outline" size="sm">
            <RefreshCw size={12} /> Aktualisieren
          </Button>
        }
      />

      {status.state === "error" && <ErrorCard kind={status.error.kind} message={status.error.message} path="/operator/trading-loop/status" />}

      {/* DALI-T1: Hero-Banner mit Klartext-Synopsis + Mini-Sparkline.
          Beantwortet "was sagt mir die Seite" in einem Satz und einer
          Pillen-Reihe — Operator scannt Muster (Wand aus grauen Pillen
          = Signal-Drought, rote Pille = Order-Failed) ohne Tabelle. */}
      {cycles.state === "ready" && cyclesList.length > 0 && (
        <Card padded className="border-l-4 border-l-info">
          <div className="flex items-start gap-3">
            <Activity size={18} className="text-info mt-0.5 shrink-0" aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-fg leading-relaxed">
                {summarizeCycles(cyclesList)}
              </div>
              <div className="mt-2 flex items-center gap-1 flex-wrap">
                {cyclesList.slice(-30).map((c, i) => (
                  <span
                    key={i}
                    className={cn("h-3 w-1.5 rounded-xs", CYCLE_DOT_TONE[c.status] ?? "bg-fg-muted")}
                    title={`${LABEL_DE[c.status] ?? c.status} · ${c.symbol}`}
                  />
                ))}
              </div>
              <div className="mt-1 text-2xs text-fg-subtle font-mono">
                letzte {Math.min(cyclesList.length, 30)} Cycles · Hover für Status pro Cycle
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* DALI-T2: Hero-Number "Ausgeführte Trades" col-span-2, sekundäre KPIs
          rechts daneben. Mode raus (steht im Topbar+PageHeader-sub). Total-
          Cycles raus (im Hero-Banner-Synopsis sichtbar). */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi
          label="Ausgeführte Trades"
          value={String(completed24h)}
          sub={`von ${cyclesList.length} Cycles in der jüngsten Historie`}
          tone={completed24h > 0 ? "pos" : "muted"}
          size="hero"
          className="md:col-span-2"
        />
        <Kpi
          label="Letzter Status"
          value={
            status.state === "ready"
              ? LABEL_DE[status.data.last_cycle_status ?? ""] ?? status.data.last_cycle_status ?? "—"
              : "—"
          }
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
                {status.data.execution_enabled ? "execution aktiv" : "paper / shadow"}
              </Badge>
            }
          />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
            <RowKV k="write_back_allowed" v={status.data.write_back_allowed ? "erlaubt" : "gesperrt"} tone={status.data.write_back_allowed ? "pos" : "muted"} />
            <RowKV k="run_once_allowed" v={status.data.run_once_allowed ? "bereit" : "blockiert"} tone={status.data.run_once_allowed ? "pos" : "warn"} />
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
                  <th className="text-center font-semibold px-4 py-2">Daten</th>
                  <th className="text-center font-semibold px-4 py-2">Signal</th>
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
      <td className="px-4 py-2">
        <span title={CYCLE_STATUS_EXPLAIN[c.status] ?? c.status}>
          <Badge tone={toneFor(c.status)}>{LABEL_DE[c.status] ?? c.status}</Badge>
        </span>
      </td>
      <td className="px-4 py-2 text-center"><BoolDot v={c.market_data_fetched} /></td>
      <td className="px-4 py-2 text-center"><BoolDot v={c.signal_generated} /></td>
      <td className="px-4 py-2 text-center"><BoolDot v={c.risk_approved} /></td>
      <td className="px-4 py-2 text-center"><BoolDot v={c.order_created} /></td>
      <td className="px-4 py-2 text-center"><BoolDot v={c.fill_simulated} /></td>
      <td className="px-4 py-2 max-w-[420px]">
        <div className="flex flex-wrap gap-1">
          {c.notes.slice(0, 3).map((n, i) => (
            <span
              key={i}
              title={n}
              className="inline-flex items-center rounded-xs border border-line-subtle bg-bg-2 px-1.5 py-0.5 text-[10px] font-mono text-fg-subtle"
            >
              {humanizeNote(n)}
            </span>
          ))}
          {c.notes.length > 3 && (
            <span className="text-2xs text-fg-subtle">+{c.notes.length - 3}</span>
          )}
          {c.notes.length === 0 && (
            <span className="text-2xs text-fg-subtle italic">—</span>
          )}
        </div>
      </td>
    </tr>
  );
}

function BoolDot({ v }: { v: boolean }) {
  return v ? <CheckCircle2 size={13} className="text-pos inline" /> : <XCircle size={13} className="text-fg-subtle inline" />;
}

function RowKV({ k, v, tone }: { k: string; v: string; tone?: "pos" | "neg" | "warn" | "muted" }) {
  return (
    <div className="flex items-center justify-between gap-2 overflow-hidden border-b border-line-subtle/50 py-1">
      <span className="min-w-0 truncate font-mono text-2xs text-fg-subtle" title={k}>{humanizeLabel(k)}</span>
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
