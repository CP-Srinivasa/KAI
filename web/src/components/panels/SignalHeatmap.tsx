// @data-source: /signals/envelope/recent
import { useMemo } from "react";
import { Radio, ExternalLink } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/EmptyState";
import { LiveDot } from "@/components/ui/LiveDot";
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
  // STAB-2026-09-01 §10 — one counter per backend lifecycle bucket. These SUM to
  // totalSignals by construction, because the backend mapping is a total
  // function and this panel no longer invents its own buckets.
  recognised: number;
  eligible: number;
  rejected: number;
  review: number;
  submitted: number;
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
          <LiveDot
            state={state.state}
            // STAB-2026-09-01 §30: this endpoint ships no data timestamp, so the
          // badge must say so rather than report the fetch time as freshness.
          generatedAt={null}
          dataTimestampKnown={false}
            staleAfterMs={POLL_MS * 1.5}
            downAfterMs={POLL_MS * 4}
          />
        }
      />
      {/* 2026-06-04 DALI: explizit machen, dass die Spalten Lifecycle-Stufen
          sind — eine grüne Zahl in "Parsed"/"Appr" ist KEIN gehandelter Trade.
          Grün bedeutet nur in "Open"/"Closed" echte Execution. */}
      <div className="mb-2 -mt-1 text-2xs text-fg-subtle">
        Envelope-Parsing <span className="text-fg-muted font-semibold">≠</span> Execution
        {" — "}
        <span className="text-fg-muted">Erk./Zul.</span> sind erkannt, nicht gehandelt;
        echte Position erst ab <span className="text-pos">Offen</span>/<span className="text-pos">Zu</span>.
        {" "}
        <span className="text-warn">Prüf.</span> = abgelaufen/ungültig, keine Ablehnung.
        {" "}Die Spalten summieren sich zur Gesamtzahl.
      </div>
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
      recognised: 0,
      eligible: 0,
      rejected: 0,
      review: 0,
      submitted: 0,
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

/**
 * STAB-2026-09-01 §10 — delegate, do not re-derive.
 *
 * This function used to bucket with six independent `if` statements over the raw
 * state string. A state matching none of them landed in NO column while
 * `totalSignals` incremented anyway, so the columns did not sum to the header:
 * 21 of 45 PremiumSignalState values had no home, which over the live log meant
 * 207 of 3890 rows (5.3%) were counted and never rendered — 205 of them
 * `requires_review` (TTL expired: the setup was fine, the entry never printed)
 * and 5 `invalid`. Precisely the actionable class was the invisible one.
 *
 * The backend now owns the partition. An envelope without a bucket falls to
 * `review` rather than silently to nothing.
 */
function applyStateBucket(row: SymbolRow, env: EnvelopeRecord) {
  const bucket = env.lifecycle_bucket ?? "review";
  switch (bucket) {
    case "recognised":
      row.recognised += 1;
      break;
    case "eligible":
      row.eligible += 1;
      break;
    case "rejected":
      row.rejected += 1;
      break;
    case "submitted_paper":
      row.submitted += 1;
      break;
    case "opened_paper":
      row.open += 1;
      break;
    case "closed_paper":
      row.closed += 1;
      break;
    case "review":
    default:
      row.review += 1;
      break;
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
      <div className="grid grid-cols-[1fr_40px_40px_40px_40px_40px_40px_40px_minmax(92px,auto)] items-center gap-1.5 px-1 pb-1 text-2xs uppercase tracking-wide text-fg-subtle font-mono">
        <span>Symbol</span>
        <span className="text-center">Erk.</span>
        <span className="text-center">Zul.</span>
        <span className="text-center">Abgel.</span>
        {/* §10: expired/invalid signals have a visible home. They are NOT
            rejections — nothing refused them, the window closed. */}
        <span className="text-center" title="Abgelaufen oder ungültig — braucht einen Blick, ist keine Ablehnung">
          Prüf.
        </span>
        <span className="text-center">Eingr.</span>
        <span className="text-center">Offen</span>
        <span className="text-center">Zu</span>
        <span className="text-right">State</span>
      </div>
      {rows.map((r) => (
        <button
          key={r.symbol}
          onClick={onSelect}
          className="w-full grid grid-cols-[1fr_40px_40px_40px_40px_40px_40px_40px_minmax(92px,auto)] items-center gap-1.5 px-1 py-1.5 rounded-sm text-xs hover:bg-bg-2 transition-colors text-left"
          title={`${r.totalSignals} Signal${r.totalSignals === 1 ? "" : "e"} · Long ${r.long} · Short ${r.short} · letztes ${formatAbsolute(r.latestTs)}`}
        >
          <span className="font-mono font-semibold truncate">{r.symbol}</span>
          <CountCell count={r.recognised} tone="muted" />
          <CountCell count={r.eligible} tone="warn" />
          <CountCell count={r.rejected} tone="neg" />
          <CountCell count={r.review} tone="warn" />
          <CountCell count={r.submitted} tone="warn" />
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
