// @data-source: /dashboard/api/source-activity
import { Activity } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { LiveDot } from "@/components/ui/LiveDot";
import { fetchSourceActivity, type SourceActivity } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { sourceLabel } from "@/lib/sourceLabels";

// Quellen-Live-Zyklus: welche Quelle liefert gerade, welche ist verstummt.
// Reiner Read über /dashboard/api/source-activity (DB-Aggregat) — kein Fake.
// Quellen kommen newest-first vom Backend; verstummte (kein Fetch > 7d) sind
// markiert, ``silent_count`` als Ausfall-Indikator oben.

const POLL_MS = 60_000;

function ago(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "0m";
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

export function SourceActivityPanel() {
  const polling = usePolling<SourceActivity>((signal) => fetchSourceActivity(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const data = polling.state === "ready" ? polling.data : null;

  return (
    <Card padded className="overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <Activity size={14} className="text-info shrink-0" />
            Quellen-Aktivität
          </span>
        }
        subtitle="Live-Ingestion-Zyklus — welche Quelle liefert, welche ist verstummt"
        right={
          <div className="flex items-center gap-2">
            <LiveDot
              state={polling.state}
              generatedAt={data ? data.generated_at : null}
              staleAfterMs={90_000}
              downAfterMs={240_000}
            />
            {data && data.silent_count > 0 ? (
              <Badge tone="warn" dot>
                {data.silent_count} verstummt
              </Badge>
            ) : data ? (
              <Badge tone="pos" dot>
                alle aktiv
              </Badge>
            ) : null}
          </div>
        }
      />

      {polling.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Quellen-Aktivität …</div>
      )}

      {polling.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-2xs font-mono text-neg">
          Endpoint nicht erreichbar ({polling.error.kind}) ·{" "}
          <span className="font-mono">/dashboard/api/source-activity</span>
        </div>
      )}

      {data && data.sources.length === 0 && (
        <div className="py-3 text-center text-xs text-fg-subtle">Noch keine Quellen-Dokumente.</div>
      )}

      {data && data.sources.length > 0 && (
        <div className="mt-1 divide-y divide-line-subtle/60">
          {data.sources.map((s) => (
            <div key={s.source_name} className="flex items-center gap-3 py-1.5">
              <span className="min-w-0 flex-1 truncate text-sm text-fg">
                {sourceLabel(s.source_name).label}
              </span>
              <span className="shrink-0 font-mono tabular-nums text-2xs text-fg-subtle">
                {s.window_count} · {data.window_hours}h
              </span>
              <span
                className="w-10 shrink-0 text-right font-mono tabular-nums text-2xs text-fg-subtle"
                title={s.last_fetched_at ?? undefined}
              >
                {ago(s.last_fetched_at)}
              </span>
              {s.silent && <StatusPill kind="stale" label="verstummt" dot={false} />}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
