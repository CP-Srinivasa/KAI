// @data-source: /dashboard/api/source-lifecycle
import { ListOrdered } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { LiveDot } from "@/components/ui/LiveDot";
import { fetchSourceLifecycle, type SourceLifecycle, type SourceRankEntry } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { sourceLabel } from "@/lib/sourceLabels";

// Source-Lifecycle-Ranking (Phase 4): die kanonische, gefüllte Rangliste aus
// monitor/source_ranking.json. Anders als die Gate-gefilterte Top/Flop-Liste
// zeigt sie AUCH provisorische Quellen (n unter Validierungs-Schwelle) — ehrlich
// als „provisorisch" markiert, nie als belastbar. Pin/Silent/Rotation als Flags,
// jüngste Statuswechsel als Audit-Spur darunter. Reiner Read, fail-closed.

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

function pct(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(0)}%`;
}

function RankRow({ e }: { e: SourceRankEntry }) {
  return (
    <div className="flex items-center gap-2 py-1.5">
      <span className="w-5 shrink-0 text-right font-mono text-2xs text-fg-subtle">{e.rank}</span>
      <span className="min-w-0 flex-1 truncate text-sm text-fg">
        {sourceLabel(e.source_name).label}
      </span>
      {e.pinned && <StatusPill kind="verified" label="pinned" dot={false} showIcon={false} />}
      {e.silent && <StatusPill kind="stale" label="verstummt" dot={false} showIcon={false} />}
      {e.rotation_flagged && (
        <Badge tone="warn" dot>
          Rotation
        </Badge>
      )}
      {e.provisional && (
        <StatusPill kind="unverified" label="provisorisch" dot={false} showIcon={false} />
      )}
      <span className="w-10 shrink-0 text-right font-mono tabular-nums text-2xs text-fg-subtle">
        {e.n}n
      </span>
      <span
        className="w-12 shrink-0 text-right font-mono tabular-nums text-sm font-semibold text-fg"
        title="Wilson-Lower 95% (Vertrauens-Untergrenze der Trefferquote)"
      >
        {pct(e.wilson_lower_95)}
      </span>
    </div>
  );
}

export function SourceLifecyclePanel() {
  const polling = usePolling<SourceLifecycle>((signal) => fetchSourceLifecycle(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const data = polling.state === "ready" ? polling.data : null;
  const counts = data?.counts ?? {};
  const pinned = counts.pinned ?? 0;
  const rotation = counts.rotation_flagged ?? 0;

  return (
    <Card padded className="overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <ListOrdered size={14} className="text-info shrink-0" />
            Quellen-Lifecycle-Ranking
          </span>
        }
        subtitle="Top-N nach Wilson-Vertrauen — inkl. provisorischer Quellen (ehrlich markiert)"
        right={
          <div className="flex items-center gap-2">
            <LiveDot
              state={polling.state}
              generatedAt={data ? data.generated_at : null}
              staleAfterMs={26 * 60 * 60 * 1000}
              downAfterMs={50 * 60 * 60 * 1000}
            />
            {pinned > 0 && (
              <Badge tone="pos" dot>
                {pinned} pinned
              </Badge>
            )}
            {rotation > 0 && (
              <Badge tone="warn" dot>
                {rotation} Rotation
              </Badge>
            )}
          </div>
        }
      />

      {polling.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Lifecycle-Ranking …</div>
      )}

      {polling.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-2xs font-mono text-neg">
          Endpoint nicht erreichbar ({polling.error.kind}) ·{" "}
          <span className="font-mono">/dashboard/api/source-lifecycle</span>
        </div>
      )}

      {data && !data.available && (
        <div className="py-3 text-center text-xs text-fg-subtle">
          Noch kein Ranking — der recalc-Job hat <span className="font-mono">source_ranking.json</span>{" "}
          noch nicht geschrieben.
        </div>
      )}

      {data && data.available && data.ranked.length === 0 && (
        <div className="py-3 text-center text-xs text-fg-subtle">
          Noch keine attribuierte Quelle mit Outcome im Fenster.
        </div>
      )}

      {data && data.available && data.ranked.length > 0 && (
        <div className="mt-1 divide-y divide-line-subtle/60">
          {data.ranked.map((e) => (
            <RankRow key={e.source_name} e={e} />
          ))}
        </div>
      )}

      {data && data.recent_events.length > 0 && (
        <div className="mt-3 border-t border-line-subtle/60 pt-2">
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
            Jüngste Statuswechsel
          </div>
          <ul className="space-y-0.5">
            {data.recent_events.map((ev, i) => (
              <li
                key={`${ev.source}-${ev.recorded_at_utc}-${i}`}
                className="flex items-center gap-2 text-2xs"
              >
                <span className="min-w-0 flex-1 truncate text-fg-muted">
                  {sourceLabel(ev.source).label}
                </span>
                <span className="shrink-0 font-mono text-fg-subtle">
                  {ev.from_status} → {ev.to_status}
                </span>
                <span
                  className="w-8 shrink-0 text-right font-mono text-fg-subtle"
                  title={ev.recorded_at_utc}
                >
                  {ago(ev.recorded_at_utc)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
