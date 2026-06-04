import { Badge, Card, CardHeader, InfoHint, ProgressBar } from "@/components/ui/Primitives";
import type { DashboardQuality } from "@/lib/api";
import { sourceLabel } from "@/lib/sourceLabels";
import { cn } from "@/lib/utils";

type SourceReliability = NonNullable<DashboardQuality["source_reliability"]>;
type SourceScore = SourceReliability["top_sources"][number];
type Tone = "pos" | "warn" | "neg" | "muted";

function tierTone(tier: string): Tone {
  if (tier === "trusted") return "pos";
  if (tier === "watch" || tier === "insufficient") return "warn";
  if (tier === "low") return "neg";
  return "muted";
}

function fmtPct(value: number | null | undefined): string {
  return value == null ? "-" : value.toFixed(1) + "%";
}

function fmtModifier(value: number): string {
  if (value > 0) return "+" + String(value);
  return String(value);
}

function healthTone(status: string | null | undefined): Tone {
  if (status === "critical") return "neg";
  if (status === "warning" || status === "stale") return "warn";
  if (status === "ok") return "pos";
  return "muted";
}

const DOT: Record<Tone, string> = {
  pos: "bg-pos",
  warn: "bg-warn",
  neg: "bg-neg",
  muted: "bg-fg-subtle/50",
};

function SourceRow({ score }: { score: SourceScore }) {
  const display = sourceLabel(score.source_name);
  const tone = tierTone(score.tier);
  return (
    <div className="rounded-md border border-line-subtle bg-bg-2/30 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex items-start gap-1.5">
          <span className={cn("mt-1 h-2 w-2 shrink-0 rounded-full", DOT[tone])} />
          <div className="min-w-0">
            <div className="break-words text-xs font-semibold text-fg" title={score.source_name}>
              {display.label}
            </div>
            <div className="mt-0.5 text-2xs font-mono text-fg-subtle">
              n={score.n} - {score.hits}/{score.hits + score.miss} Treffer
            </div>
          </div>
        </div>
        <Badge tone={tone === "muted" ? "muted" : tone} className="shrink-0">
          {score.tier}
        </Badge>
      </div>
      <div className="mt-2">
        <ProgressBar
          value={score.point_estimate_pct}
          target={65}
          tone={tone === "pos" ? "pos" : tone === "neg" ? "neg" : "auto"}
          sufficientSample={score.n >= 20}
          label={display.label + " reliability"}
          size="sm"
        />
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2 text-2xs">
        <span className="text-fg-subtle">Wilson low {fmtPct(score.wilson_lower_95_pct)}</span>
        <span className="font-mono text-fg-muted">prio {fmtModifier(score.priority_modifier)}</span>
      </div>
    </div>
  );
}

export function SourceReliabilityPanel({ data }: { data: DashboardQuality | null }) {
  const rel = data?.source_reliability;
  if (!rel || rel.status !== "ok") {
    return (
      <Card padded>
        <CardHeader
          title="Source-Reliability"
          subtitle="monitor/source_reliability.json noch nicht verfuegbar."
          right={<Badge tone="muted">{rel?.status ?? "loading"}</Badge>}
        />
        <div className="text-xs text-fg-subtle">keine Reliability-Scores</div>
      </Card>
    );
  }

  const tierCounts = rel.tier_counts ?? {};
  const riskSources = (tierCounts.low ?? 0) + (tierCounts.watch ?? 0);
  const topSources = rel.top_sources ?? [];
  const unknown = rel.unknown_bucket;
  const statusTone = healthTone(rel.quality_status);
  const trustedCount = rel.trusted_count ?? tierCounts.trusted ?? 0;

  return (
    <Card padded>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-1.5">
            Source-Reliability
            <InfoHint
              label="Source-Reliability"
              hint="Wilson-basierter Source-Score aus monitor/source_reliability.json. Modifier wirken auf die Signal-Prioritaet."
            />
          </span>
        }
        subtitle={
          String(rel.source_count) +
          " Quellen - Fenster " +
          String(rel.window_days ?? "-") +
          " Tage"
        }
        right={
          <Badge tone={statusTone} dot>
            {rel.quality_status ?? "unverified"}
          </Badge>
        }
      />

      {rel.health_warning ? (
        <div className="mb-3 rounded-sm border border-warn/30 bg-warn/10 px-3 py-2 text-2xs font-mono text-warn">
          {trustedCount === 0 ? "0 trusted Quellen - " : ""}
          {rel.health_warning}
        </div>
      ) : null}

      <div className="grid grid-cols-3 gap-2">
        <MiniStat label="trusted" value={trustedCount} tone={trustedCount > 0 ? "pos" : "warn"} />
        <MiniStat label="watch/low" value={riskSources} tone={riskSources > 0 ? "warn" : "muted"} />
        <MiniStat label="insuff." value={tierCounts.insufficient ?? 0} tone="muted" />
      </div>

      {unknown && (
        <div className="mt-3 rounded-md border border-warn/20 bg-warn/5 p-3 text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold text-warn">unknown-Bucket</span>
            <Badge tone={tierTone(unknown.tier)}>{unknown.tier}</Badge>
          </div>
          <div className="mt-1 text-fg-muted">
            n={unknown.n} - Hit-Rate {fmtPct(unknown.point_estimate_pct)} - Modifier{" "}
            {fmtModifier(unknown.priority_modifier)}
          </div>
        </div>
      )}

      <div className="mt-3 space-y-2">
        {topSources.length === 0 ? (
          <div className="text-xs text-fg-subtle">keine aufgeloesten Quellen</div>
        ) : (
          topSources.slice(0, 5).map((score) => (
            <SourceRow key={score.source_name} score={score} />
          ))
        )}
      </div>
    </Card>
  );
}

function MiniStat({ label, value, tone }: { label: string; value: number; tone: Tone }) {
  return (
    <div className="rounded-md border border-line-subtle bg-bg-2/30 p-2">
      <div className="text-2xs text-fg-subtle">{label}</div>
      <div
        className={cn(
          "mt-1 font-mono text-base font-semibold",
          tone === "pos" && "text-pos",
          tone === "warn" && "text-warn",
          tone === "neg" && "text-neg",
          tone === "muted" && "text-fg-muted",
        )}
      >
        {value}
      </div>
    </div>
  );
}
