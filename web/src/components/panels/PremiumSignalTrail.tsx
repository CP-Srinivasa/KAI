// @data-source: /api/premium-signals/trail
import { memo, useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  Pause,
  Play,
  RefreshCw,
  Target,
  X,
} from "lucide-react";
import { Badge, Card, CardHeader } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import { SignalAnalyticsBlock } from "@/components/panels/PremiumSignalAnalytics";
import {
  fetchPremiumSignalTrail,
  postManualFill,
  postReconcileCompletion,
  postReprocess,
  type PremiumSignalOrphanCompletion,
  type PremiumSignalTrailEntry,
  type PremiumSignalTrailStage,
} from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { cn } from "@/lib/utils";
import { useCurrency } from "@/state/CurrencyProvider";

/**
 * PremiumSignalTrail — End-to-End-Sicht pro Premium-Telegram-Signal.
 *
 * Wurzel: 2026-05-20 /goal-Sprint. Vier wiederholte Operator-Diagnosen
 * ("Signale grün als External, kommen nicht im Portfolio an") waren
 * tatsächlich vier verschiedene legitime Endzustände, die das Dashboard
 * bisher nicht differenziert hat:
 *   - CLOSED (TP-Tiers durchgelaufen)
 *   - BRIDGE_REJECTED (risk_gate / size / etc.)
 *   - PAPER_REJECTED (scale-drift → invalid_sl)
 *   - SOURCE_SKIPPED (Auto-Fill nicht aktiv für Signal)
 *
 * Datenquelle: `/api/premium-signals/trail` (Backend joint
 * raw + envelope + bridge + paper-audit zu einem Trail pro Envelope).
 *
 * Polling 60s — Premium-Signale sind seltene Events, kein Live-Tick nötig.
 */

type Props = { limit?: number };

type OverallTone = "pos" | "neg" | "warn" | "info" | "muted" | "ai";

const TRAIL_POLL_MS = 60_000;

// 2026-06-04 RC-2/RC-5: die State-Machine zerlegt das frühere Sammel-"CLOSED"
// (das IMMER grün war — auch bei Verlust/SL/unbekanntem PnL) in echte States.
// Grün nur noch bei tatsächlichem Erfolg (TP/offen/teilgeschlossen). Verlust
// (SL), globaler Kill-Switch und "PnL unbekannt" sind NICHT mehr grün.
const OVERALL_TONE: Record<string, OverallTone> = {
  OPEN: "info",
  PARTIALLY_CLOSED: "pos",
  CLOSED_TP: "pos",
  CLOSED_SL: "neg",
  CLOSED_MANUAL: "info",
  CLOSED: "pos", // Legacy-Alias (alte Audit-Records)
  ENTRY_DISABLED: "neg",
  BRIDGE_REJECTED: "neg",
  PAPER_REJECTED: "warn",
  SCALE_REJECTED: "warn",
  RISK_REJECTED: "neg",
  SOURCE_SKIPPED: "muted",
  NOT_APPROVED: "warn",
  PENDING_ENTRY: "muted",
  REQUIRES_REVIEW: "warn",
  EXPIRED: "muted",
  UNKNOWN: "muted",
};

const OVERALL_LABEL: Record<string, string> = {
  OPEN: "Position offen",
  PARTIALLY_CLOSED: "Teilziel erreicht",
  CLOSED_TP: "Trade abgeschlossen (TP)",
  CLOSED_SL: "Stop Loss",
  CLOSED_MANUAL: "Manuell geschlossen",
  CLOSED: "Trade fertig", // Legacy-Alias
  ENTRY_DISABLED: "Execution gestoppt (entry_mode)",
  BRIDGE_REJECTED: "Bridge abgelehnt",
  PAPER_REJECTED: "Paper-Engine abgelehnt",
  SCALE_REJECTED: "Skalierung abgelehnt",
  RISK_REJECTED: "Risk-Gate abgelehnt",
  SOURCE_SKIPPED: "Quelle übersprungen",
  NOT_APPROVED: "Nicht genehmigt",
  PENDING_ENTRY: "Wartet auf Entry",
  REQUIRES_REVIEW: "Prüfung nötig",
  EXPIRED: "TTL abgelaufen",
  UNKNOWN: "Unklar",
};

const ACTION_LABEL: Record<string, string> = {
  manual_fill: "Manuell fillen",
  wait_or_reprocess: "Reprocess",
  review_reason: "—",
  review_scale: "—",
  review_allowlist: "—",
  expired_review: "—",
  monitor: "—",
  none: "—",
};

function _formatTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function _formatRelativeAge(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
}

function StageDot({ stage }: { stage: PremiumSignalTrailStage }): JSX.Element {
  // Mapping: stage.ok=true → grün, ok=false aber name in [closed,paper] mit
  // reason "position_never_opened" → muted (nie geöffnet ist OK kontextabhängig)
  // ok=false sonst → rot (Bruchpunkt)
  const isNeverOpened = stage.reason === "position_never_opened";
  const tone: OverallTone = stage.ok
    ? "pos"
    : isNeverOpened
      ? "muted"
      : stage.name === "bridge" || stage.name === "paper"
        ? "neg"
        : "warn";
  const Icon = stage.ok ? Check : isNeverOpened ? Pause : X;
  const tooltip = [
    `${stage.label}`,
    stage.ts ? `· ${_formatTs(stage.ts)}` : null,
    stage.reason ? `· ${stage.reason}` : null,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div
      className={cn(
        "flex items-center gap-1 px-1.5 py-0.5 rounded-xs border text-2xs font-mono",
        "border-line-subtle/40",
        tone === "pos" && "bg-pos/10 text-pos border-pos/30",
        tone === "neg" && "bg-neg/10 text-neg border-neg/30",
        tone === "warn" && "bg-warn/10 text-warn border-warn/30",
        tone === "muted" && "bg-bg-2 text-fg-muted",
      )}
      title={tooltip}
    >
      <Icon size={10} />
      <span className="capitalize">{stage.name}</span>
    </div>
  );
}

function TrailRow({
  entry,
  busy,
  highlighted,
  onAction,
}: {
  entry: PremiumSignalTrailEntry;
  busy: boolean;
  highlighted: boolean;
  onAction: (entry: PremiumSignalTrailEntry, action: "manual_fill" | "reprocess") => void;
}): JSX.Element {
  const { fmt, fmtPrice } = useCurrency();
  const overall = entry.overall;
  const overallTone = OVERALL_TONE[overall] ?? "muted";
  const overallLabel = OVERALL_LABEL[overall] ?? overall;
  const direction = (entry.direction ?? entry.side ?? "—").toLowerCase();
  const isLong = direction === "long" || direction === "buy";
  const directionLabel = isLong ? "LONG" : direction === "short" || direction === "sell" ? "SHORT" : "—";

  const showManualFill = entry.next_action_hint === "manual_fill";
  const showReprocess = entry.next_action_hint === "wait_or_reprocess";
  const actionAvailable = showManualFill || showReprocess;

  // Closed-Reason als Sub-Label wenn Trade fertig ist
  const closedSub =
    overall.startsWith("CLOSED") && entry.paper_close_reason
      ? ` · ${entry.paper_close_reason}`
      : "";

  return (
    <div
      id={`trail-row-${entry.envelope_id}`}
      className={cn(
        "rounded-md border border-line-subtle bg-bg-2/60 p-3 scroll-mt-4",
        highlighted && "trail-pulse",
      )}
    >
      {/* Header: Symbol + Side + Leverage + Overall + Received */}
      <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono text-sm font-semibold">{entry.symbol}</span>
          <Badge tone={isLong ? "pos" : "neg"}>{directionLabel}</Badge>
          {entry.leverage != null && entry.leverage > 0 && (
            <Badge tone="ai">{entry.leverage}x</Badge>
          )}
          <Badge tone={overallTone}>
            {overallLabel}
            {closedSub}
          </Badge>
          {entry.scale_unknown && (
            <Badge tone="warn" title="scale_resolver konnte am Receive-Zeitpunkt keinen Preis abrufen — Skalierung pending">
              Skala unbekannt
            </Badge>
          )}
        </div>
        <div className="text-2xs text-fg-subtle font-mono" title={entry.received_at ?? ""}>
          {_formatTs(entry.received_at)} · {_formatRelativeAge(entry.received_at)} her
        </div>
      </div>

      {/* Stage-Lanes: 6 horizontale Pills */}
      <div className="flex items-center gap-1 mb-2 flex-wrap">
        {entry.stages.map((s, i) => (
          <span key={`${s.name}-${i}`} className="flex items-center">
            <StageDot stage={s} />
            {i < entry.stages.length - 1 && (
              <ChevronRight size={10} className="text-fg-subtle/40 mx-0.5" />
            )}
          </span>
        ))}
      </div>

      {/* Auswertung: Kapital · Ergebnis · Entry · Quelle · Targets · Hinweise
          (2026-05-28 /goal). Fallback auf die schlanke Detail-Row, wenn der
          Backend-Trail noch keinen analytics-Block liefert (Backward-Compat). */}
      {entry.analytics ? (
        <>
          <SignalAnalyticsBlock analytics={entry.analytics} />
          <div className="mt-1.5 text-2xs font-mono text-fg-subtle">
            <span className="text-fg-muted">SL </span>
            {fmtPrice(entry.stop_loss)}
          </div>
        </>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-2xs">
          <div className="font-mono">
            <span className="text-fg-subtle">Entry </span>
            <span className="text-fg">{fmtPrice(entry.entry_value)}</span>
          </div>
          <div className="font-mono">
            <span className="text-fg-subtle">SL </span>
            <span className="text-fg">{fmtPrice(entry.stop_loss)}</span>
          </div>
          <div className="font-mono">
            <span className="text-fg-subtle">TPs </span>
            <span className="text-fg">
              {entry.targets.length > 0
                ? entry.targets.map((t) => fmtPrice(t)).join(" / ")
                : "—"}
            </span>
          </div>
          <div className="font-mono">
            <span className="text-fg-subtle">Realisiert </span>
            <span
              className={cn(
                entry.realized_pnl_usd != null && entry.realized_pnl_usd > 0 && "text-pos",
                entry.realized_pnl_usd != null && entry.realized_pnl_usd < 0 && "text-neg",
                (entry.realized_pnl_usd == null || entry.realized_pnl_usd === 0) &&
                  "text-fg-muted",
              )}
            >
              {entry.realized_pnl_usd != null && entry.realized_pnl_usd > 0 ? "+" : ""}
              {fmt(entry.realized_pnl_usd)}
            </span>
          </div>
        </div>
      )}

      {/* Action-Row: nur wenn next_action_hint actionable */}
      {actionAvailable && (
        <div className="mt-2 pt-2 border-t border-line-subtle/40 flex items-center justify-end gap-2">
          {showManualFill && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onAction(entry, "manual_fill")}
              className={cn(
                "inline-flex items-center gap-1 text-2xs font-mono px-2 py-1 rounded-xs border border-line",
                "hover:border-info hover:text-info disabled:opacity-50 transition-colors",
              )}
              title="Envelope manuell durch die Approval+Bridge schicken (Operator-Override für nicht-approved Signale)"
            >
              <Play size={10} /> {ACTION_LABEL.manual_fill}
            </button>
          )}
          {showReprocess && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onAction(entry, "reprocess")}
              className={cn(
                "inline-flex items-center gap-1 text-2xs font-mono px-2 py-1 rounded-xs border border-line",
                "hover:border-info hover:text-info disabled:opacity-50 transition-colors",
              )}
              title="Frische Bridge-Tick triggern (envelope_id informativ)"
            >
              <RefreshCw size={10} /> {ACTION_LABEL.wait_or_reprocess}
            </button>
          )}
        </div>
      )}

      {/* Info-Row: review_*-Hints ohne Action */}
      {!actionAvailable &&
        entry.next_action_hint !== "monitor" &&
        entry.next_action_hint !== "none" && (
          <div className="mt-2 pt-2 border-t border-line-subtle/40 text-2xs text-fg-subtle font-mono flex items-center gap-1">
            <AlertTriangle size={10} className="text-warn" />
            Hinweis: <span className="text-fg">{entry.next_action_hint}</span>
          </div>
        )}
    </div>
  );
}

function OrphanRow({
  orphan,
  busy,
  onReconcile,
}: {
  orphan: PremiumSignalOrphanCompletion;
  busy: boolean;
  onReconcile: (o: PremiumSignalOrphanCompletion) => void;
}): JSX.Element {
  const { fmtPrice } = useCurrency();
  return (
    <div className="rounded-md border border-warn/30 bg-warn/5 p-3 flex items-center justify-between gap-3 flex-wrap">
      <div className="flex items-center gap-2 flex-wrap min-w-0">
        <Target size={14} className="text-warn shrink-0" />
        <span className="font-mono text-sm font-semibold">{orphan.symbol}</span>
        <Badge tone="warn">Orphan TP-Hit</Badge>
        <span className="text-2xs text-fg-subtle font-mono">
          touched {fmtPrice(orphan.touch_price)}
        </span>
        <span className="text-2xs text-fg-subtle font-mono" title={orphan.timestamp_utc ?? ""}>
          {_formatTs(orphan.timestamp_utc)} · {_formatRelativeAge(orphan.timestamp_utc)} her
        </span>
        {orphan.reason && (
          <span className="text-2xs text-fg-muted font-mono">({orphan.reason})</span>
        )}
      </div>
      <button
        type="button"
        disabled={busy}
        onClick={() => onReconcile(orphan)}
        className={cn(
          "inline-flex items-center gap-1 text-2xs font-mono px-2 py-1 rounded-xs border border-line",
          "hover:border-warn hover:text-warn disabled:opacity-50 transition-colors shrink-0",
        )}
        title="Forciert einen Reconcile-Run für diese Completion-Meldung; nützlich wenn eine Position außerhalb des Channels geschlossen wurde."
      >
        <RefreshCw size={10} /> Reconcile erzwingen
      </button>
    </div>
  );
}

export const PremiumSignalTrail = memo(function PremiumSignalTrail({
  limit = 20,
}: Props): JSX.Element {
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchPremiumSignalTrail(limit, signal),
    [limit],
  );
  const polling = usePolling(fetcher, {
    intervalMs: TRAIL_POLL_MS,
    pauseWhenHidden: true,
  });
  const [busyEnv, setBusyEnv] = useState<string | null>(null);
  const [busyOrphan, setBusyOrphan] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [highlightedEnvelopeId, setHighlightedEnvelopeId] = useState<string | null>(null);
  const [missingHashId, setMissingHashId] = useState<string | null>(null);
  const pendingHashRef = useRef<string | null>(null);

  const handleOrphanReconcile = useCallback(
    async (o: PremiumSignalOrphanCompletion) => {
      setBusyOrphan(o.source_envelope_id ?? o.symbol);
      setActionError(null);
      try {
        const key = `trail-orphan-${o.symbol}-${o.timestamp_utc ?? Date.now()}`;
        await postReconcileCompletion(o.symbol, o.touch_price ?? undefined, key);
      } catch (e) {
        setActionError(
          `reconcile fehlgeschlagen für ${o.symbol}: ${(e as Error).message}`,
        );
      } finally {
        setBusyOrphan(null);
      }
    },
    [],
  );

  // DALI-P-103: deep-link from external-signals feed via sessionStorage token.
  // sessionStorage statt URL-Hash, weil der KAI-Router (state/Router.tsx) hash-
  // basierte Routes nutzt und ein "#trail-XYZ" als unbekannte Route auf
  // "dashboard" zurückfallen würde. Token wird gesetzt vor navigate("portfolio")
  // und hier beim ersten ready-Poll konsumiert + gelöscht.
  useEffect(() => {
    try {
      const token = window.sessionStorage.getItem("kai.trail.target");
      pendingHashRef.current = token;
      if (token) setMissingHashId(null);
    } catch {
      pendingHashRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (polling.state !== "ready") return;
    const targetId = pendingHashRef.current;
    if (!targetId) return;
    const found = polling.data.trail.some((e) => e.envelope_id === targetId);
    pendingHashRef.current = null;
    try {
      window.sessionStorage.removeItem("kai.trail.target");
    } catch {
      // ignore quota / privacy mode
    }
    if (!found) {
      setMissingHashId(targetId);
      return;
    }
    setHighlightedEnvelopeId(targetId);
    const node = document.getElementById(`trail-row-${targetId}`);
    if (node) node.scrollIntoView({ behavior: "smooth", block: "center" });
    const t = window.setTimeout(() => setHighlightedEnvelopeId(null), 1500);
    return () => window.clearTimeout(t);
  }, [polling]);

  const handleAction = useCallback(
    async (
      entry: PremiumSignalTrailEntry,
      action: "manual_fill" | "reprocess",
    ) => {
      setBusyEnv(entry.envelope_id);
      setActionError(null);
      try {
        if (action === "manual_fill") {
          const key = `trail-fill-${entry.envelope_id}-${Date.now()}`;
          await postManualFill(entry.envelope_id, key);
        } else {
          const key = `trail-reprocess-${entry.envelope_id}-${Date.now()}`;
          await postReprocess(entry.envelope_id, key);
        }
      } catch (e) {
        setActionError(
          `${action} fehlgeschlagen für ${entry.symbol}: ${(e as Error).message}`,
        );
      } finally {
        setBusyEnv(null);
      }
    },
    [],
  );

  const subtitle =
    polling.state === "ready"
      ? `${polling.data.count} Signale · Auto-Refresh alle ${TRAIL_POLL_MS / 1000}s`
      : polling.state === "loading"
        ? "lädt …"
        : "Fehler beim Laden";

  return (
    <Card padded className="synthwave-pulse-edge overflow-hidden">
      <CardHeader
        title="Premium-Signal Trail"
        subtitle={subtitle}
        right={
          <LiveDot
            state={polling.state}
            generatedAt={
              polling.state === "ready" ? new Date(polling.fetchedAt).toISOString() : null
            }
            staleAfterMs={90_000}
            downAfterMs={240_000}
          />
        }
      />

      {polling.state === "loading" && (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-20 rounded-md border border-line-subtle bg-bg-2 animate-pulse"
            />
          ))}
        </div>
      )}

      {polling.state === "error" && (
        <div className="rounded-md border border-neg/30 bg-neg/10 p-3 text-xs font-mono text-neg">
          {polling.error.kind}: {polling.error.message}
        </div>
      )}

      {polling.state === "ready" && polling.data.trail.length === 0 && (
        <div className="rounded-md border border-line-subtle bg-bg-2 p-4 text-2xs text-fg-subtle text-center">
          Keine Premium-Signal-Envelopes in den letzten {limit} Einträgen.
        </div>
      )}

      {missingHashId && (
        <div className="rounded-md border border-warn/40 bg-warn/10 p-3 text-2xs">
          <div className="font-semibold text-warn mb-1">
            Signal nicht im aktuellen Trail-Fenster
          </div>
          <div className="text-fg-muted">
            Envelope{" "}
            <span className="font-mono break-all">{missingHashId}</span>{" "}
            ist nicht unter den letzten {limit} Einträgen. Vermutlich älter
            oder anderer Run — bitte den Trail-Backend direkt befragen via{" "}
            <span className="font-mono">
              /api/premium-signals/trail?limit=100
            </span>
            .
          </div>
        </div>
      )}

      {polling.state === "ready" && polling.data.trail.length > 0 && (
        <div className="space-y-2 max-h-[600px] overflow-y-auto pr-1">
          {polling.data.trail.map((entry, i) => (
            <TrailRow
              key={`${entry.envelope_id}-${i}`}
              entry={entry}
              busy={busyEnv === entry.envelope_id}
              highlighted={highlightedEnvelopeId === entry.envelope_id}
              onAction={handleAction}
            />
          ))}
        </div>
      )}

      {polling.state === "ready" &&
        (polling.data.orphan_completions?.length ?? 0) > 0 && (
          <div className="mt-4 pt-3 border-t border-line-subtle/40">
            <div className="flex items-center gap-2 mb-2">
              <Target size={12} className="text-warn" />
              <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
                Orphan Target-Completions
              </span>
              <Badge tone="warn">{polling.data.orphan_completions!.length}</Badge>
            </div>
            <div className="text-2xs text-fg-subtle mb-2 leading-relaxed">
              🎯-Channel-Meldungen die KEINE passende offene Position fanden
              (Position war bereits geschlossen oder nie eröffnet). Reconciler
              hat sie sauber als orphan markiert — kein silent fail.
            </div>
            <div className="space-y-2">
              {polling.data.orphan_completions!.map((o, i) => (
                <OrphanRow
                  key={`${o.source_envelope_id ?? o.symbol}-${i}`}
                  orphan={o}
                  busy={busyOrphan === (o.source_envelope_id ?? o.symbol)}
                  onReconcile={handleOrphanReconcile}
                />
              ))}
            </div>
          </div>
        )}

      {actionError && (
        <div className="mt-2 rounded-md border border-neg/30 bg-neg/10 p-2 text-2xs font-mono text-neg">
          {actionError}
        </div>
      )}

      <div className="text-2xs text-fg-subtle mt-3 leading-relaxed">
        Joint Audit-Streams: <span className="font-mono">raw</span> ·{" "}
        <span className="font-mono">envelope</span> ·{" "}
        <span className="font-mono">bridge</span> ·{" "}
        <span className="font-mono">paper</span>. Closed/Open kommt aus dem
        Paper-Engine-Audit, Bridge-Reject zeigt risk_gate / paper_engine-Reasons.
      </div>
    </Card>
  );
});
