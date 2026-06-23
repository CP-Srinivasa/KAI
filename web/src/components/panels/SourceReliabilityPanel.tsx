// @data-source: props (parent-provided)
import { AlertTriangle } from "lucide-react";
import { Badge, Card, CardHeader, InfoHint, ProgressBar } from "@/components/ui/Primitives";
import type { DashboardQuality } from "@/lib/api";
import { sourceLabel } from "@/lib/sourceLabels";
import { getMetricContract } from "@/lib/labels";
import { cn } from "@/lib/utils";

type SourceReliability = NonNullable<DashboardQuality["source_reliability"]>;
type SourceScore = SourceReliability["top_sources"][number];
type Tone = "pos" | "warn" | "neg" | "muted";

// DALI Truth-Sprint 2026-06-04: small-n-Quellen duerfen nicht belastbar wirken.
// n >= TRUSTED_N: belastbare Stichprobe. n < PROVISIONAL_N: vorlaeufig.
const TRUSTED_N = 20;
const PROVISIONAL_N = 5;

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
  const sufficient = score.n >= TRUSTED_N;
  const provisional = score.n < PROVISIONAL_N;
  // 100% bei n=1/2 ist statistisch wertlos — Punktschaetzung gedaempft anzeigen.
  const heroicButThin =
    !sufficient && (score.point_estimate_pct ?? 0) >= 99 && score.n <= 2;
  return (
    <div
      className={cn(
        "rounded-md border bg-bg-2/30 p-3",
        provisional ? "border-warn/25" : "border-line-subtle",
      )}
    >
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
        <div className="flex flex-col items-end gap-1 shrink-0">
          <Badge tone={tone === "muted" ? "muted" : tone}>{score.tier}</Badge>
          {!sufficient && (
            <span
              className="rounded-xs border border-warn/40 bg-warn/10 px-1 py-0 text-[9px] font-mono uppercase tracking-wider text-warn"
              title={`Stichprobe n=${score.n} < ${TRUSTED_N} — nicht belastbar, nur vorläufige Tendenz.`}
            >
              {provisional ? "small-n" : "vorläufig"}
            </span>
          )}
        </div>
      </div>
      <div className="mt-2">
        <ProgressBar
          value={score.point_estimate_pct}
          target={65}
          tone={!sufficient ? "muted" : tone === "pos" ? "pos" : tone === "neg" ? "neg" : "auto"}
          sufficientSample={sufficient}
          label={display.label + " reliability"}
          size="sm"
        />
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2 text-2xs">
        <span className="text-fg-subtle">
          {sufficient ? (
            <>Hit-Rate {fmtPct(score.point_estimate_pct)}</>
          ) : (
            <span className="text-warn" title="Punktschätzung bei kleiner Stichprobe nicht belastbar.">
              {fmtPct(score.point_estimate_pct)} (vorläufig{heroicButThin ? ", n≤2" : ""})
            </span>
          )}
        </span>
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
  // metric_contract als autoritative Erklaerung (P0-Truth-Layer), Tooltip-Quelle.
  const relExplain = getMetricContract(data?.metric_contract, "source_reliability")?.explanation;

  return (
    <Card padded>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-1.5">
            Source-Reliability
            <InfoHint
              label="Source-Reliability"
              hint={
                "Wilson-basierter Source-Score aus monitor/source_reliability.json. Modifier wirken auf die Signal-Prioritaet." +
                (relExplain ? ` ${relExplain}` : "")
              }
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

      {trustedCount === 0 ? (
        <div
          className="mb-3 rounded-sm border border-warn/40 bg-warn/10 px-3 py-2.5"
          role="status"
        >
          <div className="flex items-center gap-2 text-warn">
            <AlertTriangle size={16} className="shrink-0" />
            <span className="text-sm font-semibold">0/{rel.source_count} trusted Sources</span>
          </div>
          <p className="mt-1 text-2xs text-fg-muted leading-relaxed">
            Frühphasen-Evidenz, kein Integritätsbruch: keine Quelle erreicht das Trust-Gate
            (n≥30 Hard-Outcomes + Wilson-Lower≥0,65); Trust-Boosts sind fail-closed (wirkungslos).
            {rel.health_warning ? ` ${rel.health_warning}` : ""}
          </p>
        </div>
      ) : rel.health_warning ? (
        <div className="mb-3 rounded-sm border border-warn/30 bg-warn/10 px-3 py-2 text-2xs font-mono text-warn">
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

      {topSources.length > 0 && (() => {
        const shown = topSources.slice(0, 5);
        const anyTrusted = shown.some((s) => s.tier === "trusted" && s.n >= TRUSTED_N);
        const listTitle = anyTrusted
          ? "Quellen"
          : trustedCount === 0
            ? "Vorläufige Quellen — nicht belastbar"
            : "Quellen mit kleinen Stichproben";
        return (
          <div className="mt-3 mb-1.5 flex items-center gap-1.5">
            <span className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-subtle">
              {listTitle}
            </span>
            {!anyTrusted && (
              <InfoHint
                label="Vorläufige Quellen"
                hint={`Keine Quelle erreicht eine belastbare Stichprobe (n≥${TRUSTED_N}). Die gezeigten Hit-Raten sind Tendenzen, keine validierten Scores.`}
              />
            )}
          </div>
        );
      })()}
      <div className="space-y-2">
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
