import { useMemo } from "react";
import { Radio, ExternalLink } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  fetchRecentEnvelopes,
  type EnvelopeRecord,
  type EnvelopeRecentResponse,
} from "@/lib/api";
import { formatRelative, formatAbsolute } from "@/lib/time";
import { usePolling } from "@/lib/usePolling";
import { useRouter } from "@/state/Router";
import { cn } from "@/lib/utils";

type SymbolRow = {
  symbol: string;
  long: number;
  short: number;
  parsed: number;
  approved: number;
  rejected: number;
  pending: number;
  open: number;
  closed: number;
  latestTs: string;
  latestState: string | null;
  latestTone: "pos" | "warn" | "neg" | "muted";
  totalSignals: number;
};

const POLL_MS = 60_000;
const MAX_ROWS = 8;

function statusTone(
  status: string | null,
  premiumTone?: string | null,
): "pos" | "warn" | "neg" | "muted" {
  if (premiumTone === "pos") return "pos";
  if (premiumTone === "warn") return "warn";
  if (premiumTone === "neg") return "neg";
  if (!status) return "muted";
  const s = status.toLowerCase();
  if (s === "duplicate" || s === "blocked") return "warn";
  if (s === "rejected" || s === "failed") return "neg";
  return "muted";
}

export function SignalHeatmapPanel() {
  const state = usePolling<EnvelopeRecentResponse>(
    (signal) => fetchRecentEnvelopes(50, signal),
    { intervalMs: POLL_MS, pauseWhenHidden: true, retry: { maxAttempts: 3, baseMs: 2_000 } },
  );
  const { navigate } = useRouter();

  const view = useMemo(() => {
    if (state.state !== "ready") return null;
    const signals = state.data.records.filter(
      (x) => x.message_type === "signal" && x.signal,
    );
    const deduped = dedupeSignals(signals);
    return { rows: aggregate(deduped), totalSignals: deduped.length };
  }, [state]);

  return (
    <Card padded>
      <CardHeader
        title="Signal-Matrix"
        subtitle={
          view
            ? `${view.rows.length} Symbole · ${view.totalSignals} Signale aus den letzten 50 Envelopes`
            : "Welche fachlichen Premium-Signale sind aktuell aktiv?"
        }
        right={
          <Badge tone="info" dot>
            Live
          </Badge>
        }
      />
      {state.state === "loading" && (
        <div className="py-6 text-center text-xs text-fg-subtle">Lade Signale …</div>
      )}
      {state.state === "error" && (
        <div className="py-4 text-xs text-neg break-words">
          Konnte Signale nicht laden: {state.error.message}
        </div>
      )}
      {view && view.rows.length === 0 && (
        <EmptyState
          icon={<Radio size={18} />}
          title="Noch keine Signale im Fenster"
          hint="Externe Signal-Envelopes landen automatisch hier, sobald sie über Dashboard oder Webhook eintreffen."
          className="my-2"
        />
      )}
      {view && view.rows.length > 0 && (
        <HeatmapTable
          rows={view.rows.slice(0, MAX_ROWS)}
          overflow={Math.max(0, view.rows.length - MAX_ROWS)}
          onSelect={() => navigate("external")}
        />
      )}
    </Card>
  );
}

function aggregate(envs: EnvelopeRecord[]): SymbolRow[] {
  const by = new Map<string, SymbolRow>();
  for (const env of envs) {
    const s = env.signal;
    if (!s?.symbol) continue;
    const ts = env.timestamp_utc ?? "";
    const direction = (s.direction ?? "").toLowerCase();
    const row = by.get(s.symbol) ?? {
      symbol: s.symbol,
      long: 0,
      short: 0,
      parsed: 0,
      approved: 0,
      rejected: 0,
      pending: 0,
      open: 0,
      closed: 0,
      latestTs: ts,
      latestState: env.premium_state ?? env.status ?? null,
      latestTone: statusTone(env.status ?? null, env.premium_state_tone),
      totalSignals: 0,
    };
    if (direction === "long") row.long += 1;
    else if (direction === "short") row.short += 1;
    applyStateBucket(row, env);
    row.totalSignals += 1;
    if (ts > row.latestTs) {
      row.latestTs = ts;
      row.latestState = env.premium_state ?? env.status ?? null;
      row.latestTone = statusTone(env.status ?? null, env.premium_state_tone);
    }
    by.set(s.symbol, row);
  }
  return [...by.values()].sort((a, b) => b.latestTs.localeCompare(a.latestTs));
}

function applyStateBucket(row: SymbolRow, env: EnvelopeRecord) {
  const state = (env.premium_state ?? env.stage ?? env.status ?? "").toLowerCase();
  if (
    state === "parsed" ||
    state === "envelope_accepted" ||
    state === "accepted" ||
    state === "new"
  ) {
    row.parsed += 1;
  }
  if (state === "approved" || state === "awaiting_approval") {
    row.approved += 1;
  }
  if (
    state.includes("rejected") ||
    state === "entry_disabled" ||
    state === "source_skipped" ||
    state === "risk_rejected" ||
    state === "market_data_failed" ||
    state === "paper_execution_failed" ||
    state === "requires_scale_review"
  ) {
    row.rejected += 1;
  }
  if (state === "pending_entry" || state === "bridge_pending" || state === "pending") {
    row.pending += 1;
  }
  if (state === "position_open" || state === "partially_closed") {
    row.open += 1;
  }
  if (
    state === "closed_tp" ||
    state === "closed_sl" ||
    state === "closed_manual" ||
    state === "closed_unknown" ||
    state === "reconciled_completion"
  ) {
    row.closed += 1;
  }
}

function dedupeSignals(envs: EnvelopeRecord[]): EnvelopeRecord[] {
  const byOrigin = new Map<string, EnvelopeRecord>();
  for (const env of envs) {
    const signal = env.signal;
    const key =
      env.origin_signal_id ||
      signal?.origin_signal_id ||
      signal?.source_uid ||
      env.envelope_id ||
      `${signal?.symbol ?? "unknown"}:${env.timestamp_utc ?? ""}`;
    const prev = byOrigin.get(key);
    if (!prev || (env.timestamp_utc ?? "") > (prev.timestamp_utc ?? "")) {
      byOrigin.set(key, env);
    }
  }
  return [...byOrigin.values()];
}

function HeatmapTable({
  rows,
  overflow,
  onSelect,
}: {
  rows: SymbolRow[];
  overflow: number;
  onSelect: () => void;
}) {
  return (
    <div className="space-y-1">
      <div className="grid grid-cols-[1fr_44px_44px_44px_44px_44px_44px_minmax(92px,auto)] items-center gap-1.5 px-1 pb-1 text-2xs uppercase tracking-wide text-fg-subtle font-mono">
        <span>Symbol</span>
        <span className="text-center">Parsed</span>
        <span className="text-center">Appr</span>
        <span className="text-center">Reject</span>
        <span className="text-center">Pend</span>
        <span className="text-center">Open</span>
        <span className="text-center">Closed</span>
        <span className="text-right">State</span>
      </div>
      {rows.map((r) => (
        <button
          key={r.symbol}
          onClick={onSelect}
          className="w-full grid grid-cols-[1fr_44px_44px_44px_44px_44px_44px_minmax(92px,auto)] items-center gap-1.5 px-1 py-1.5 rounded-sm text-xs hover:bg-bg-2 transition-colors text-left"
          title={`${r.totalSignals} Signal${r.totalSignals === 1 ? "" : "e"} · Long ${r.long} · Short ${r.short} · letztes ${formatAbsolute(r.latestTs)}`}
        >
          <span className="font-mono font-semibold truncate">{r.symbol}</span>
          <CountCell count={r.parsed} tone="muted" />
          <CountCell count={r.approved} tone="warn" />
          <CountCell count={r.rejected} tone="neg" />
          <CountCell count={r.pending} tone="warn" />
          <CountCell count={r.open} tone="pos" />
          <CountCell count={r.closed} tone={r.closed > 0 ? "pos" : "muted"} />
          <span className="font-mono text-2xs text-fg-muted text-right inline-flex items-center gap-1.5 justify-end">
            <StatusDotTone tone={r.latestTone} />
            <span className="whitespace-nowrap">{r.latestState ?? "—"}</span>
            <span className="hidden sm:inline text-fg-subtle/70">
              {formatRelative(r.latestTs)}
            </span>
          </span>
        </button>
      ))}
      {overflow > 0 && (
        <button
          onClick={onSelect}
          className="w-full flex items-center justify-center gap-1.5 py-1.5 mt-1 text-2xs font-mono text-fg-subtle hover:text-fg transition-colors"
        >
          +{overflow} weitere
          <ExternalLink size={10} />
        </button>
      )}
    </div>
  );
}

function CountCell({
  count,
  tone,
}: {
  count: number;
  tone: "pos" | "warn" | "neg" | "muted";
}) {
  if (count === 0) {
    return (
      <span className="w-full inline-flex items-center justify-center text-fg-subtle font-mono text-xs tabular-nums h-[20px]">
        —
      </span>
    );
  }
  return (
    <span
      className={cn(
        "w-full inline-flex items-center justify-center rounded-xs border px-1 py-0.5 text-2xs font-mono font-semibold tabular-nums",
        tone === "pos"
          ? "border-pos/30 bg-pos/10 text-pos"
          : tone === "warn"
            ? "border-warn/30 bg-warn/10 text-warn"
            : tone === "neg"
              ? "border-neg/30 bg-neg/10 text-neg"
              : "border-line-subtle bg-bg-2 text-fg-muted",
      )}
    >
      {count}
    </span>
  );
}

function StatusDotTone({ tone }: { tone: "pos" | "warn" | "neg" | "muted" }) {
  const bg =
    tone === "pos"
      ? "bg-pos"
      : tone === "warn"
        ? "bg-warn"
        : tone === "neg"
          ? "bg-neg"
          : "bg-fg-subtle/50";
  // Synthwave Stufe 2: aktive Dots glühen in ihrer Tone.
  const glow =
    tone === "pos" ? "glow-pos"
    : tone === "warn" ? "glow-warn"
    : tone === "neg" ? "glow-neg"
    : "";
  return <span className={cn("h-1.5 w-1.5 rounded-full inline-block", bg, glow)} aria-hidden />;
}
