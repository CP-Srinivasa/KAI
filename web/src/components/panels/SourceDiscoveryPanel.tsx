// @data-source: /dashboard/api/source-discovery
import { Telescope } from "lucide-react";
import { Card, CardHeader, Badge, InfoHint, SectionLabel } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { LiveDot } from "@/components/ui/LiveDot";
import {
  fetchSourceDiscovery,
  type SourceDiscovery,
  type ProbationSource,
  type SourceProposal,
  type SourceDiscoveryRun,
} from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { sourceLabel } from "@/lib/sourceLabels";

// Autonome Quellen-Discovery (Phase 3 + 3b): macht den geschlossenen Loop sichtbar
// — Scout-Vorschläge, Quellen in PROBATION (mit Evidenz + Graduation-Fortschritt)
// und jüngste Läufe. Reiner Read, fail-closed.

const POLL_MS = 60_000;

function pct(v: number | null): string {
  return v == null ? "—" : `${v.toFixed(0)}%`;
}

function ProbationRow({ s, minRuns, minDel }: { s: ProbationSource; minRuns: number; minDel: number }) {
  return (
    <div className="flex items-center gap-2 py-1.5">
      <span className="min-w-0 flex-1 truncate text-sm text-fg">{sourceLabel(s.provider).label}</span>
      {s.graduation_eligible ? (
        <StatusPill kind="verified" label="bereit zum Einwechseln" dot={false} showIcon={false} />
      ) : (
        <span className="shrink-0 font-mono text-2xs text-fg-subtle">
          Läufe {s.runs}/{minRuns} · Signale {s.n}/{minDel}
        </span>
      )}
      <span className="w-16 shrink-0 text-right font-mono tabular-nums text-2xs text-fg-subtle">
        {s.n} Sig.
      </span>
      <span className="w-20 shrink-0 text-right font-mono tabular-nums text-2xs text-fg-subtle">
        ≥{pct(s.wilson_lower_pct)} sicher
      </span>
      <span className="w-14 shrink-0 text-right font-mono tabular-nums text-sm font-semibold text-fg">
        {pct(s.hit_rate_pct)}
      </span>
    </div>
  );
}

function ProposalRow({ p }: { p: SourceProposal }) {
  return (
    <div className="flex items-center gap-2 py-1" title={p.notes ?? undefined}>
      <span className="w-12 shrink-0 text-right font-mono text-2xs text-fg-subtle">
        {p.score == null ? "—" : p.score.toFixed(2)}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-fg">
        {p.provider ? sourceLabel(p.provider).label : p.url}
      </span>
      <span className="shrink-0 font-mono text-2xs text-fg-subtle">{p.access}</span>
      <span className="w-24 shrink-0 text-right font-mono text-2xs text-fg-subtle">
        {p.item_count == null ? "—" : `${p.item_count} Items`}
        {p.latest_age_days == null ? "" : ` · ${p.latest_age_days.toFixed(0)}d`}
      </span>
    </div>
  );
}

function RunRow({ r }: { r: SourceDiscoveryRun }) {
  const live = r.mode === "live";
  return (
    <li className="flex items-center gap-2 text-2xs">
      <Badge tone={live ? "pos" : "muted"}>{live ? "scharf" : "beobachtet"}</Badge>
      <span className="min-w-0 flex-1 truncate font-mono text-fg-subtle">
        {r.proposals_seen ?? 0} gesehen · {r.onboarded ?? 0} onboardet · {r.swaps_executed ?? 0} eingewechselt
      </span>
      <span className="shrink-0 font-mono text-fg-subtle" title={r.recorded_at_utc ?? undefined}>
        {(r.recorded_at_utc ?? "").slice(5, 10)}
      </span>
    </li>
  );
}

export function SourceDiscoveryPanel() {
  const polling = usePolling<SourceDiscovery>((signal) => fetchSourceDiscovery(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const data = polling.state === "ready" ? polling.data : null;
  const probation = data?.probation ?? [];
  const proposals = data?.proposals ?? [];
  const runs = data?.recent_runs ?? [];
  const eligible = data?.counts?.graduation_eligible ?? 0;

  return (
    <Card padded className="overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <Telescope size={14} className="text-info shrink-0" />
            Quellen-Discovery
          </span>
        }
        subtitle="Autonom gefunden, geprüft, in Probation evaluiert — bewährt sich eine Quelle, wechselt sie 1-zu-1 einen Schwächling aus."
        right={
          <div className="flex items-center gap-2">
            <LiveDot state={polling.state} generatedAt={null} staleAfterMs={26 * 60 * 60 * 1000} />
            {data?.discovery_enabled ? (
              <Badge tone="pos" dot>
                scharf
              </Badge>
            ) : (
              <Badge tone="muted" dot>
                nur Beobachtung
              </Badge>
            )}
            {eligible > 0 && (
              <Badge tone="pos" dot>
                {eligible} bereit
              </Badge>
            )}
          </div>
        }
      />

      {polling.state === "loading" && (
        <div className="py-4 text-center text-xs text-fg-subtle">Lade Discovery …</div>
      )}
      {polling.state === "error" && (
        <div className="rounded-sm border border-neg/30 bg-neg/5 px-3 py-2 text-2xs font-mono text-neg">
          Endpoint nicht erreichbar ({polling.error.kind}) · /dashboard/api/source-discovery
        </div>
      )}

      {data && (
        <>
          {!data.discovery_enabled && (
            <div className="mb-3 rounded-sm border border-line-subtle bg-bg-2 px-3 py-2 text-2xs text-fg-muted">
              Onboarding ist aus (<span className="font-mono">SOURCE_DISCOVERY_ENABLED=false</span>) —
              Vorschläge werden nur beobachtet, keine Quelle wird autonom aktiviert.
            </div>
          )}

          {/* Probation: was sich gerade beweist. */}
          <div className="flex items-center gap-2 pb-1">
            <SectionLabel className="flex-1">In Probation ({probation.length})</SectionLabel>
            <InfoHint
              label="Probation"
              side="left"
              hint="Autonom onboardete Quellen werden im Shadow mitgepollt (kein Eligibility-Boost), bis sie Evidenz haben. Ab Läufe ≥ Schwelle UND genug aufgelösten Signalen graduieren sie und wechseln einen schwachen aktiven Quell aus."
            />
          </div>
          {probation.length === 0 ? (
            <div className="py-2 text-2xs text-fg-subtle">
              Keine Quelle in Probation — wird befüllt, sobald Discovery scharf onboardet.
            </div>
          ) : (
            <div className="divide-y divide-line-subtle/60">
              {probation.map((s) => (
                <ProbationRow
                  key={s.provider}
                  s={s}
                  minRuns={data.min_probation_runs}
                  minDel={data.min_deliveries}
                />
              ))}
            </div>
          )}

          {/* Vorschläge: was als nächstes onboardbar wäre. */}
          {proposals.length > 0 && (
            <div className="mt-3 border-t border-line-subtle/60 pt-2">
              <SectionLabel className="pb-1">Kandidaten-Vorschläge ({proposals.length})</SectionLabel>
              <div className="divide-y divide-line-subtle/40">
                {proposals.map((p) => (
                  <ProposalRow key={p.url ?? p.provider} p={p} />
                ))}
              </div>
            </div>
          )}

          {/* Jüngste Läufe. */}
          {runs.length > 0 && (
            <div className="mt-3 border-t border-line-subtle/60 pt-2">
              <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
                Jüngste Discovery-Läufe
              </div>
              <ul className="space-y-0.5">
                {runs.map((r, i) => (
                  <RunRow key={`${r.recorded_at_utc}-${i}`} r={r} />
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
