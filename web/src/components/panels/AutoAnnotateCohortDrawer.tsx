import { memo, useCallback, useState } from "react";
import { ChevronDown, ChevronRight, FlaskConical } from "lucide-react";
import { Badge, Button, Card } from "@/components/ui/Primitives";
import { usePolling } from "@/lib/usePolling";
import { formatRelative } from "@/lib/time";
import { cn } from "@/lib/utils";
import {
  fetchAutoAnnotateCohortReport,
  type AutoAnnotateCohortReport,
  type CohortCounters,
  type CohortRangePreset,
} from "@/lib/api";

const POLL_MS = 5 * 60 * 1000;

const COHORT_LABELS: Record<keyof AutoAnnotateCohortReport["cohorts"], string> = {
  fresh_auto: "fresh_auto",
  backfill: "backfill",
  reeval: "reeval",
  other: "other",
  latest_per_doc: "latest_per_doc",
  fresh_dispatch: "fresh_dispatch",
};

const COHORT_HINTS: Record<keyof AutoAnnotateCohortReport["cohorts"], string> = {
  fresh_auto: "auto:-Notes (Live-Annotator)",
  backfill: "backfill:-Notes (Re-Eval-Backlog)",
  reeval: "reeval:-Notes (manuelle Re-Bewertung)",
  other: "Legacy / unbekannter Prefix",
  latest_per_doc: "Dedup nach document_id",
  fresh_dispatch: "Outcome-Join mit alert_audit (Dispatch-Window)",
};

const RANGES: { value: CohortRangePreset; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
];

function formatRatePct(value: number | null): string {
  if (value === null) return "—";
  return `${value.toFixed(1)}%`;
}

type MiniCardProps = {
  name: string;
  hint: string;
  counters: CohortCounters;
  extra?: string;
};

function CohortMiniCard({ name, hint, counters, extra }: MiniCardProps): JSX.Element {
  const tone =
    counters.hit_rate_pct === null
      ? "muted"
      : counters.hit_rate_pct >= 50
        ? "pos"
        : counters.hit_rate_pct >= 25
          ? "warn"
          : "neg";

  return (
    <div className="rounded-md border border-line-subtle bg-bg-2 p-3 flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-2xs text-fg-muted truncate">{name}</span>
        <Badge tone={tone}>{formatRatePct(counters.hit_rate_pct)}</Badge>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-xl font-semibold text-fg tabular-nums">{counters.total}</span>
        <span className="text-2xs text-fg-subtle">
          {counters.resolved} aufgelöst · {counters.inconclusive} offen
        </span>
      </div>
      <div className="text-2xs text-fg-subtle leading-snug">
        {hint}
        {extra && <span className="block text-fg-muted">{extra}</span>}
      </div>
    </div>
  );
}

export const AutoAnnotateCohortDrawer = memo(function AutoAnnotateCohortDrawer(): JSX.Element {
  const [open, setOpen] = useState(false);
  const [range, setRange] = useState<CohortRangePreset>("7d");
  const [dispatchedWindow, setDispatchedWindow] = useState(false);

  const fetcher = useCallback(
    (signal: AbortSignal) => fetchAutoAnnotateCohortReport(range, dispatchedWindow, signal),
    [range, dispatchedWindow],
  );
  const polling = usePolling(fetcher, { intervalMs: POLL_MS, pauseWhenHidden: true });

  return (
    <Card padded={false} className="overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "w-full flex items-center gap-3 px-5 py-4",
          "hover:bg-bg-2 transition-colors text-left",
        )}
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown size={16} className="text-fg-muted shrink-0" />
        ) : (
          <ChevronRight size={16} className="text-fg-muted shrink-0" />
        )}
        <FlaskConical size={16} className="text-fg-muted shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-fg">Annotation-Kohorten</div>
          <div className="text-2xs text-fg-subtle truncate">
            V5-Followup Forensik · 6 Kohorten · letzte {range}
          </div>
        </div>
        {polling.state === "ready" && (
          <span className="text-2xs text-fg-muted tabular-nums">
            {polling.data.raw_rows} Annotationen
          </span>
        )}
      </button>

      {open && (
        <div className="border-t border-line-subtle p-5 flex flex-col gap-4">
          <div className="flex items-center justify-end gap-3 flex-wrap">
            <div className="flex items-center rounded-md border border-line-subtle bg-bg-2 p-0.5">
              {RANGES.map((r) => (
                <button
                  key={r.value}
                  type="button"
                  onClick={() => setRange(r.value)}
                  className={cn(
                    "px-2.5 py-1 text-2xs font-mono rounded-sm transition-colors",
                    range === r.value
                      ? "bg-bg-3 text-fg"
                      : "text-fg-muted hover:text-fg",
                  )}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-2xs text-fg-muted cursor-pointer">
              <input
                type="checkbox"
                checked={dispatchedWindow}
                onChange={(e) => setDispatchedWindow(e.target.checked)}
                className="h-3 w-3 accent-fg"
              />
              dispatch-window
            </label>
          </div>

          {polling.state === "loading" && (
            <div className="text-2xs text-fg-subtle">Lade Kohorten…</div>
          )}

          {polling.state === "error" && (
            <div className="text-2xs text-neg">
              Fehler: {polling.error.message}
            </div>
          )}

          {polling.state === "ready" && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {(Object.keys(COHORT_LABELS) as Array<keyof typeof COHORT_LABELS>).map((key) => {
                  const c = polling.data.cohorts[key];
                  let extra: string | undefined;
                  if (key === "latest_per_doc") {
                    const d = polling.data.cohorts.latest_per_doc;
                    extra = `${d.unique_document_ids} unique · ${d.duplicate_rows_removed} dupes removed`;
                  } else if (key === "fresh_dispatch") {
                    const d = polling.data.cohorts.fresh_dispatch;
                    extra = `${d.missing_audit} ohne audit-join`;
                  }
                  return (
                    <CohortMiniCard
                      key={key}
                      name={COHORT_LABELS[key]}
                      hint={COHORT_HINTS[key]}
                      counters={c}
                      extra={extra}
                    />
                  );
                })}
              </div>

              <div className="flex items-center justify-between text-2xs text-fg-subtle pt-2 border-t border-line-subtle">
                <span>
                  Stand: {formatRelative(polling.data.generated_at)} ·
                  timestamp_basis: <span className="font-mono">{polling.data.window.timestamp_basis}</span>
                </span>
                <span className="font-mono">
                  Forensik-Memory: kai_dispatch_filter_root_befund_20260524
                </span>
              </div>

              {polling.data.invalid_timestamp > 0 && (
                <div className="text-2xs text-warn">
                  Warnung: {polling.data.invalid_timestamp} Outcomes mit invalidem Timestamp übersprungen.
                </div>
              )}
            </>
          )}

          <div className="flex justify-end">
            <Button
              variant="ghost"
              onClick={() => {
                window.open("/api/alerts/auto-annotate-report", "_blank");
              }}
            >
              Roh-JSON öffnen
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
});
