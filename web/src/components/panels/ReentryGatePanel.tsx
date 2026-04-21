import { memo, useMemo } from "react";
import { AlertTriangle, Flag, Target, Coins, Calendar } from "lucide-react";
import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type { DashboardQuality, DashboardProvenance } from "@/lib/api";

// Re-Entry-Gate (TV-Pivot D-125, Ziel 2026-05-16):
// Entweder ≥200 resolved directional alerts ODER ≥10 paper fills mit PnL.
// Quelle: /dashboard/api/quality (active_resolved_count, paper_fills_with_pnl)
//       + /dashboard/api/provenance (verdict, overall precision).

const REENTRY_DATE_ISO = "2026-05-16";
const ALERTS_TARGET = 200;
const FILLS_TARGET = 10;

function daysUntil(targetIso: string): number {
  const target = new Date(`${targetIso}T00:00:00Z`).getTime();
  const now = Date.now();
  return Math.max(0, Math.ceil((target - now) / (1000 * 60 * 60 * 24)));
}

type GateState =
  | { kind: "path_a_met"; label: string }
  | { kind: "path_b_met"; label: string }
  | { kind: "both_met"; label: string }
  | { kind: "open"; closest: "a" | "b"; pctToClosest: number };

function evaluateGate(alerts: number, fills: number): GateState {
  const aMet = alerts >= ALERTS_TARGET;
  const bMet = fills >= FILLS_TARGET;
  if (aMet && bMet) return { kind: "both_met", label: "Beide Pfade erreicht" };
  if (aMet) return { kind: "path_a_met", label: `Pfad A erreicht (${alerts} alerts)` };
  if (bMet) return { kind: "path_b_met", label: `Pfad B erreicht (${fills} fills)` };
  const pctA = alerts / ALERTS_TARGET;
  const pctB = fills / FILLS_TARGET;
  return pctA >= pctB
    ? { kind: "open", closest: "a", pctToClosest: pctA }
    : { kind: "open", closest: "b", pctToClosest: pctB };
}

function verdictShort(v: string | undefined): string {
  if (!v) return "—";
  const map: Record<string, string> = {
    tv_significantly_better_than_rss: "TV > RSS",
    rss_significantly_better_than_tv: "RSS > TV",
    overlapping_confidence_intervals_no_significant_difference: "CIs überlappen",
    insufficient_sample_for_split_comparison: "n zu klein",
  };
  return map[v] ?? v;
}

function verdictTone(v: string | undefined): "pos" | "warn" | "neg" | "muted" {
  if (v === "tv_significantly_better_than_rss") return "pos";
  if (v === "rss_significantly_better_than_tv") return "neg";
  if (v === "overlapping_confidence_intervals_no_significant_difference") return "warn";
  return "muted";
}

export type QualityFetchState = "loading" | "ready" | "error";

function ReentryGatePanelImpl({
  quality,
  provenance,
  qualityState = "ready",
  qualityError,
}: {
  quality: DashboardQuality | null;
  provenance: DashboardProvenance | null;
  qualityState?: QualityFetchState;
  qualityError?: string | null;
}) {
  const daysLeft = useMemo(() => daysUntil(REENTRY_DATE_ISO), []);

  const computed = useMemo(() => {
    if (qualityState !== "ready" || quality == null) return null;
    const alerts = quality.active_resolved_count ?? 0;
    const fills = quality.paper_fills_with_pnl ?? 0;
    const pnlUsd = quality.paper_realized_pnl_usd ?? 0;
    const gate = evaluateGate(alerts, fills);
    return { alerts, fills, pnlUsd, gate, banner: bannerProps(gate, daysLeft) };
  }, [quality, qualityState, daysLeft]);

  // DALI-F-028: bei loading/error keinen 0/0-Gate-Versagen-Visual zeigen,
  // sondern Skeleton bzw. Error-Banner. Countdown bleibt sichtbar, weil er
  // nicht von API-Daten abhängt.
  if (!computed || quality == null) {
    return (
      <ReentryGatePanelFallback
        state={qualityState}
        daysLeft={daysLeft}
        errorMessage={qualityError ?? null}
      />
    );
  }

  const { alerts, fills, pnlUsd, gate, banner } = computed;

  return (
    <Card padded>
      <CardHeader
        title="Re-Entry-Gate"
        subtitle={`TV-Pivot D-125 · Ziel: ${REENTRY_DATE_ISO} · Pfad A (alerts ≥${ALERTS_TARGET}) ODER Pfad B (fills ≥${FILLS_TARGET})`}
        right={
          <Badge tone={daysLeft <= 7 ? "warn" : "muted"} dot>
            <Calendar size={10} />
            {daysLeft === 0 ? "heute" : `${daysLeft} Tage`}
          </Badge>
        }
      />

      <div
        className={cn(
          "mt-1 mb-3 rounded-sm border px-3 py-2 text-xs flex items-center gap-2",
          banner.className,
        )}
        role="status"
      >
        <Flag size={14} className="shrink-0" />
        <span className="font-semibold">{banner.title}</span>
        <span className="text-fg-muted">·</span>
        <span className="text-fg-muted">{banner.detail}</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <ProgressRow
          icon={<Target size={12} />}
          label="Pfad A — Resolved Alerts (active)"
          current={alerts}
          target={ALERTS_TARGET}
          met={gate.kind === "path_a_met" || gate.kind === "both_met"}
          helper={
            quality
              ? `${quality.active_hits} hits · ${quality.active_misses} miss · ${fmtPct(quality.active_precision_pct)} precision`
              : undefined
          }
        />
        <ProgressRow
          icon={<Coins size={12} />}
          label="Pfad B — Paper Fills mit PnL"
          current={fills}
          target={FILLS_TARGET}
          met={gate.kind === "path_b_met" || gate.kind === "both_met"}
          helper={
            <>
              Realized PnL:{" "}
              <span className={cn("font-mono", pnlUsd > 0 ? "text-pos" : pnlUsd < 0 ? "text-neg" : "")}>
                {pnlUsd >= 0 ? "+" : ""}
                {pnlUsd.toFixed(2)} USD
              </span>
              {quality ? (
                <>
                  {" "}· {quality.paper_positions_closed} closed
                </>
              ) : null}
            </>
          }
        />
      </div>

      {provenance ? (
        <div className="mt-3 pt-3 border-t border-line-subtle flex items-center justify-between flex-wrap gap-2 text-2xs font-mono text-fg-muted">
          <div className="flex items-center gap-2">
            <span className="text-fg-subtle uppercase tracking-wide">TV-4 Verdict</span>
            <Badge tone={verdictTone(provenance.verdict)}>{verdictShort(provenance.verdict)}</Badge>
          </div>
          <div className="flex items-center gap-3">
            <span>
              overall: <span className="font-semibold">{fmtPct(provenance.overall.hit_rate_pct)}</span>
              {provenance.overall.ci_low_pct != null && provenance.overall.ci_high_pct != null && (
                <span className="text-fg-subtle">
                  {" "}[{provenance.overall.ci_low_pct.toFixed(1)}–{provenance.overall.ci_high_pct.toFixed(1)}]
                </span>
              )}
            </span>
            <span>
              n={provenance.overall.resolved}
            </span>
          </div>
        </div>
      ) : null}
    </Card>
  );
}

function bannerProps(
  gate: GateState,
  daysLeft: number,
): { title: string; detail: string; className: string } {
  if (gate.kind === "both_met" || gate.kind === "path_a_met" || gate.kind === "path_b_met") {
    return {
      title: gate.label,
      detail: "Gate-Kriterium erfüllt — Re-Entry-Entscheidung kann am Stichtag getroffen werden.",
      className: "border-pos/30 bg-pos/10 text-pos",
    };
  }
  const pct = (gate.pctToClosest * 100).toFixed(0);
  const path = gate.closest === "a" ? "A (alerts)" : "B (fills)";
  if (daysLeft <= 7) {
    return {
      title: "Gate offen · Deadline ≤7 Tage",
      detail: `Nächster Pfad ${path} bei ${pct}% — bei Nichterreichen bis ${REENTRY_DATE_ISO} kein weiterer Aufschub.`,
      className: "border-neg/30 bg-neg/10 text-neg",
    };
  }
  return {
    title: "Gate offen",
    detail: `Nächster Pfad ${path} bei ${pct}% — ${daysLeft} Tage bis Stichtag.`,
    className: "border-warn/30 bg-warn/10 text-warn",
  };
}

function ProgressRow({
  icon,
  label,
  current,
  target,
  met,
  helper,
}: {
  icon: React.ReactNode;
  label: string;
  current: number;
  target: number;
  met: boolean;
  helper?: React.ReactNode;
}) {
  return (
    <div className="rounded-sm border border-line-subtle bg-bg-1 p-2.5 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-2xs font-mono uppercase tracking-wide text-fg-muted">
          {icon}
          {label}
        </span>
        <span className={cn("font-mono text-sm font-semibold", met ? "text-pos" : "text-fg")}>
          {current}
          <span className="text-fg-subtle font-normal">/{target}</span>
        </span>
      </div>
      <ProgressBar
        value={current}
        target={target}
        tone={met ? "pos" : "auto"}
        size="md"
        label={label}
      />
      {helper ? (
        <div className="text-2xs text-fg-subtle font-mono">{helper}</div>
      ) : null}
    </div>
  );
}

function ReentryGatePanelFallback({
  state,
  daysLeft,
  errorMessage,
}: {
  state: QualityFetchState;
  daysLeft: number;
  errorMessage: string | null;
}) {
  const isError = state === "error";
  const banner = isError
    ? {
        title: "Gate-Daten nicht erreichbar",
        detail:
          errorMessage ??
          "/dashboard/api/quality antwortet nicht — Gate-Status unbekannt, Deadline läuft weiter.",
        className: "border-neg/30 bg-neg/10 text-neg",
        icon: <AlertTriangle size={14} className="shrink-0" />,
      }
    : {
        title: "Lade Gate-Daten …",
        detail: "Auto-Refresh alle 30 s · Pfad A/B werden geprüft.",
        className: "border-line-subtle bg-bg-2 text-fg-muted",
        icon: <Flag size={14} className="shrink-0" />,
      };

  return (
    <Card padded>
      <CardHeader
        title="Re-Entry-Gate"
        subtitle={`TV-Pivot D-125 · Ziel: ${REENTRY_DATE_ISO} · Pfad A (alerts ≥${ALERTS_TARGET}) ODER Pfad B (fills ≥${FILLS_TARGET})`}
        right={
          <Badge tone={daysLeft <= 7 ? "warn" : "muted"} dot>
            <Calendar size={10} />
            {daysLeft === 0 ? "heute" : `${daysLeft} Tage`}
          </Badge>
        }
      />

      <div
        className={cn(
          "mt-1 mb-3 rounded-sm border px-3 py-2 text-xs flex items-center gap-2",
          banner.className,
        )}
        role="status"
        aria-live="polite"
      >
        {banner.icon}
        <span className="font-semibold">{banner.title}</span>
        <span className="text-fg-muted">·</span>
        <span className="text-fg-muted break-words">{banner.detail}</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <SkeletonRow icon={<Target size={12} />} label="Pfad A — Resolved Alerts (active)" target={ALERTS_TARGET} />
        <SkeletonRow icon={<Coins size={12} />} label="Pfad B — Paper Fills mit PnL" target={FILLS_TARGET} />
      </div>
    </Card>
  );
}

function SkeletonRow({
  icon,
  label,
  target,
}: {
  icon: React.ReactNode;
  label: string;
  target: number;
}) {
  return (
    <div className="rounded-sm border border-line-subtle bg-bg-1 p-2.5 space-y-1.5" aria-busy>
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-2xs font-mono uppercase tracking-wide text-fg-muted">
          {icon}
          {label}
        </span>
        <span className="font-mono text-sm font-semibold text-fg-subtle">
          —<span className="text-fg-subtle font-normal">/{target}</span>
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-bg-3 overflow-hidden animate-pulse" aria-hidden />
      <div className="text-2xs text-fg-subtle font-mono opacity-60">lädt …</div>
    </div>
  );
}

function fmtPct(n: number | null | undefined): string {
  return n == null ? "—" : `${n.toFixed(1)}%`;
}

export const ReentryGatePanel = memo(ReentryGatePanelImpl);
