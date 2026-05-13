import { useMemo } from "react";
import { Radio, ArrowUpRight, ArrowDownRight, ExternalLink } from "lucide-react";
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
  latestTs: string;
  latestStatus: string | null;
  totalSignals: number;
};

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
    return { rows: aggregate(signals), totalSignals: signals.length };
  }, [state]);

  return (
    <Card padded>
      <CardHeader
        title="Signal-Matrix"
        subtitle={
          view
            ? `${view.rows.length} Symbole · ${view.totalSignals} Signale aus den letzten 50 Envelopes`
            : "Welche Symbole sind aktuell aktiv und mit welchem Sentiment?"
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
      <div className="grid grid-cols-[1fr_52px_52px_auto] items-center gap-2 px-1 pb-1 text-2xs uppercase tracking-wide text-fg-subtle font-mono">
        <span>Symbol</span>
        <span className="text-center">Long</span>
        <span className="text-center">Short</span>
        <span className="text-right">Zuletzt</span>
      </div>
      {rows.map((r) => (
        <button
          key={r.symbol}
          onClick={onSelect}
          className="w-full grid grid-cols-[1fr_52px_52px_auto] items-center gap-2 px-1 py-1.5 rounded-sm text-xs hover:bg-bg-2 transition-colors text-left"
          title={`${r.totalSignals} Signal${r.totalSignals === 1 ? "" : "e"} · letztes ${formatAbsolute(r.latestTs)}`}
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
    // DALI v2 S3 M1b: tabular-nums + min-width damit "—" exakt unter den
    // Zahlen-Pillen ausgerichtet bleibt (Operator: "ordentlich strukturieren").
    return (
      <span className="w-full inline-flex items-center justify-center text-fg-subtle font-mono text-xs tabular-nums h-[20px]">
        —
      </span>
    );
  }
  const tone = dir === "long" ? "pos" : "neg";
  const Icon = dir === "long" ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className={cn(
        "w-full inline-flex items-center justify-center gap-1 rounded-xs border px-1.5 py-0.5 text-2xs font-mono font-semibold tabular-nums",
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
  // Synthwave Stufe 2: aktive Dots glühen in ihrer Tone.
  const glow =
    tone === "pos" ? "glow-pos"
    : tone === "warn" ? "glow-warn"
    : tone === "neg" ? "glow-neg"
    : "";
  return <span className={cn("h-1.5 w-1.5 rounded-full inline-block", bg, glow)} aria-hidden />;
}
