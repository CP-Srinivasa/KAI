import { useEffect, useState } from "react";
import { Radio, ArrowUpRight, ArrowDownRight, ExternalLink } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/EmptyState";
import { fetchRecentEnvelopes, type EnvelopeRecord } from "@/lib/api";
import { formatRelative, formatAbsolute } from "@/lib/time";
import { useRouter } from "@/state/Router";
import { cn } from "@/lib/utils";

type SymbolRow = {
  symbol: string;
  long: number;
  short: number;
  latestTs: string;
  latestStatus: string | null;
  totalSignals: number;
};

type State =
  | { kind: "loading" }
  | { kind: "ready"; rows: SymbolRow[]; totalSignals: number }
  | { kind: "error"; message: string };

const POLL_MS = 60_000;
const MAX_ROWS = 8;

function statusTone(status: string | null): "pos" | "warn" | "neg" | "muted" {
  if (!status) return "muted";
  const s = status.toLowerCase();
  if (s === "executed" || s === "ok" || s === "new") return "pos";
  if (s === "duplicate" || s === "blocked") return "warn";
  if (s === "rejected" || s === "failed") return "neg";
  return "muted";
}

export function SignalHeatmapPanel() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const { navigate } = useRouter();

  useEffect(() => {
    const ctrl = new AbortController();
    let cancelled = false;

    const load = async () => {
      try {
        const r = await fetchRecentEnvelopes(50, ctrl.signal);
        if (cancelled) return;
        const signals = r.records.filter((x) => x.message_type === "signal" && x.signal);
        const rows = aggregate(signals);
        setState({ kind: "ready", rows, totalSignals: signals.length });
      } catch (e) {
        if (cancelled) return;
        setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
      }
    };

    load();
    const id = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      ctrl.abort();
      window.clearInterval(id);
    };
  }, []);

  return (
    <Card padded>
      <CardHeader
        title="Signal-Matrix"
        subtitle={
          state.kind === "ready"
            ? `${state.rows.length} Symbole · ${state.totalSignals} Signale (Fenster ≤50)`
            : undefined
        }
        right={
          <Badge tone="muted" dot>
            live
          </Badge>
        }
      />
      {state.kind === "loading" && (
        <div className="py-6 text-center text-xs text-fg-subtle">Lade Signale …</div>
      )}
      {state.kind === "error" && (
        <div className="py-4 text-xs text-neg break-words">
          Konnte Signale nicht laden: {state.message}
        </div>
      )}
      {state.kind === "ready" && state.rows.length === 0 && (
        <EmptyState
          icon={<Radio size={18} />}
          title="Noch keine Signale im Fenster"
          hint="Externe Signal-Envelopes landen automatisch hier, sobald sie über Dashboard oder Webhook eintreffen."
          className="my-2"
        />
      )}
      {state.kind === "ready" && state.rows.length > 0 && (
        <HeatmapTable
          rows={state.rows.slice(0, MAX_ROWS)}
          overflow={Math.max(0, state.rows.length - MAX_ROWS)}
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
      latestTs: ts,
      latestStatus: env.status ?? null,
      totalSignals: 0,
    };
    if (direction === "long") row.long += 1;
    else if (direction === "short") row.short += 1;
    row.totalSignals += 1;
    if (ts > row.latestTs) {
      row.latestTs = ts;
      row.latestStatus = env.status ?? null;
    }
    by.set(s.symbol, row);
  }
  return [...by.values()].sort((a, b) => b.latestTs.localeCompare(a.latestTs));
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
      <div className="grid grid-cols-[1fr_auto_auto_auto] items-center gap-2 px-1 pb-1 text-2xs uppercase tracking-wide text-fg-subtle font-mono">
        <span>Symbol</span>
        <span className="text-center">Long</span>
        <span className="text-center">Short</span>
        <span className="text-right">Zuletzt</span>
      </div>
      {rows.map((r) => (
        <button
          key={r.symbol}
          onClick={onSelect}
          className="w-full grid grid-cols-[1fr_auto_auto_auto] items-center gap-2 px-1 py-1.5 rounded-sm text-xs hover:bg-bg-2 transition-colors text-left"
          title={`${r.totalSignals} Signale · latest ${formatAbsolute(r.latestTs)}`}
        >
          <span className="font-mono font-semibold truncate">{r.symbol}</span>
          <DirCell count={r.long} dir="long" />
          <DirCell count={r.short} dir="short" />
          <span className="font-mono text-2xs text-fg-muted text-right inline-flex items-center gap-1.5 justify-end">
            <StatusDotTone tone={statusTone(r.latestStatus)} />
            <span className="whitespace-nowrap">{formatRelative(r.latestTs)}</span>
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

function DirCell({ count, dir }: { count: number; dir: "long" | "short" }) {
  if (count === 0) {
    return <span className="w-[52px] text-center text-fg-subtle font-mono text-xs">—</span>;
  }
  const tone = dir === "long" ? "pos" : "neg";
  const Icon = dir === "long" ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className={cn(
        "w-[52px] inline-flex items-center justify-center gap-1 rounded-xs border px-1.5 py-0.5 text-2xs font-mono font-semibold",
        tone === "pos"
          ? "border-pos/30 bg-pos/10 text-pos"
          : "border-neg/30 bg-neg/10 text-neg",
      )}
    >
      <Icon size={10} />
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
  return <span className={cn("h-1.5 w-1.5 rounded-full inline-block", bg)} aria-hidden />;
}
