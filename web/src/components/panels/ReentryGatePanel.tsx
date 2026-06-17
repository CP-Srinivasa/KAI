// @data-source: props (/dashboard/api/quality)
import { memo, useMemo, type ReactNode } from "react";
import { AlertTriangle, Flag, Target, Coins, Calendar, Info, ChevronRight, Archive } from "lucide-react";
import { Card, CardHeader, Badge, ProgressBar } from "@/components/ui/Primitives";
import { Explainer } from "@/components/ui/InfoOverlay";
import { useT } from "@/i18n/I18nProvider";
import { cn } from "@/lib/utils";
import { useCurrency } from "@/state/CurrencyProvider";
import type { DashboardQuality, PriorityGateSummary } from "@/lib/api";
import { resolvePriorityVerdict } from "@/lib/truthStatus";
import { getMetricContract, getMetricWarning } from "@/lib/labels";

// Re-Entry-Gate (TV-Pivot D-125, Ziel 2026-05-16):
// Entweder ≥200 resolved directional alerts ODER ≥10 paper fills mit PnL.
// Quelle: /dashboard/api/quality (active_resolved_count, paper_fills_with_pnl)
//       + /dashboard/api/provenance (verdict, overall precision).

const REENTRY_DATE_ISO = "2026-05-16";
const ALERTS_TARGET = 200;
const FILLS_TARGET = 10;

// Pi-Daten-Cutover 2026-05-02 14:30 UTC: alles davor unter Schema v1 (NEO-P-101-r2
// disqualified, via Backfill rekonstruiert). Operator soll wissen, dass Realized-PnL
// pre-Cutover nicht direkt mit post-Cutover-Werten verglichen werden kann.
const V1_DISQUALIFIED_TOOLTIP =
  "Daten vor 2026-05-02 14:30 UTC unter Schema v1 (NEO-P-101-r2 disqualified, " +
  "via Backfill rekonstruiert). v2-only-Werte ab Cutover.";

function daysUntil(targetIso: string): number {
  const target = new Date(`${targetIso}T00:00:00Z`).getTime();
  const now = Date.now();
  return Math.ceil((target - now) / (1000 * 60 * 60 * 24));
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

export type QualityFetchState = "loading" | "ready" | "error";

function ReentryGatePanelImpl({
  quality,
  qualityState = "ready",
  qualityError,
  priorityGate = null,
}: {
  quality: DashboardQuality | null;
  qualityState?: QualityFetchState;
  qualityError?: string | null;
  priorityGate?: PriorityGateSummary | null;
}) {
  const { t } = useT();
  const { fmt } = useCurrency();
  const targetDate = quality?.reentry?.target_date ?? REENTRY_DATE_ISO;
  const daysLeft = useMemo(() => daysUntil(targetDate), [targetDate]);
  const targetExpired = quality?.reentry?.status === "expired" || daysLeft < 0;

  const computed = useMemo(() => {
    if (qualityState !== "ready" || quality == null) return null;
    const alerts = quality.active_resolved_count ?? 0;
    const fills = quality.paper_fills_with_pnl ?? 0;
    const pnlUsd = quality.paper_realized_pnl_usd ?? 0;
    const v1Disqualified = quality.audit_v1_disqualified ?? false;
    const gate = evaluateGate(alerts, fills);
    return {
      alerts,
      fills,
      pnlUsd,
      v1Disqualified,
      gate,
      banner: bannerProps(gate, daysLeft, targetExpired, targetDate),
    };
  }, [quality, qualityState, daysLeft, targetExpired, targetDate]);

  // DALI-F-028: bei loading/error keinen 0/0-Gate-Versagen-Visual zeigen,
  // sondern Skeleton bzw. Error-Banner. Countdown bleibt sichtbar, weil er
  // nicht von API-Daten abhängt.
  if (!computed || quality == null) {
    return (
      <ReentryGatePanelFallback
        state={qualityState}
        daysLeft={daysLeft}
        targetExpired={targetExpired}
        targetDate={targetDate}
        errorMessage={qualityError ?? null}
      />
    );
  }

  const { alerts, fills, pnlUsd, v1Disqualified, gate, banner } = computed;

  return (
    <Card padded>
      <CardHeader
        title="Re-Entry-Gate"
        subtitle={
          targetExpired
            ? `TV-Pivot D-125 · Ziel ${targetDate} archiviert · historische Evidenz`
            : `TV-Pivot D-125 · Ziel: ${targetDate} · Pfad A (alerts ≥${ALERTS_TARGET}) ODER Pfad B (fills ≥${FILLS_TARGET})`
        }
        right={
          <Badge tone={targetExpired ? "muted" : daysLeft <= 7 ? "warn" : "muted"} dot>
            {targetExpired ? <Archive size={10} /> : <Calendar size={10} />}
            {targetExpired ? "archiviert" : daysLeft === 0 ? "heute" : `${daysLeft} Tage`}
          </Badge>
        }
      />

      {targetExpired ? (
        <div
          className="mt-1 mb-3 rounded-sm border border-line-subtle bg-bg-2 px-3 py-2"
          role="status"
        >
          <div className="flex items-center gap-2 text-fg-muted">
            <Archive size={14} className="shrink-0" />
            <span className="text-xs font-medium">{banner.title}</span>
            <span className="ml-auto inline-flex items-center gap-1 rounded-xs border border-line-subtle px-1.5 py-0.5 text-2xs font-mono uppercase tracking-wider text-fg-subtle">
              Historie
            </span>
          </div>
          <p className="mt-1 text-2xs text-fg-subtle leading-relaxed">{banner.detail}</p>
        </div>
      ) : (
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
      )}

      <ExpiredCollapse expired={targetExpired}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <ProgressRow
          icon={<Target size={12} />}
          label={t("primitives.reentry_path_a")}
          current={alerts}
          target={ALERTS_TARGET}
          met={gate.kind === "path_a_met" || gate.kind === "both_met"}
          helper={
            quality ? (
              <>
                <span>
                  {t("primitives.reentry_path_a_helper", {
                    hits: quality.active_hits,
                    misses: quality.active_misses,
                    precision: fmtPct(quality.active_precision_pct),
                  })}
                </span>
                {quality.legacy_unknown_cutoff && (() => {
                  const cutoffMs = Date.parse(quality.legacy_unknown_cutoff);
                  if (Number.isNaN(cutoffMs)) return null;
                  const dayMs = 24 * 60 * 60 * 1000;
                  const day = Math.max(1, Math.floor((Date.now() - cutoffMs) / dayMs));
                  const rate = (alerts / day).toFixed(1);
                  return (
                    <>
                      {" · "}
                      <span className="text-fg-subtle">
                        {t("primitives.reentry_path_a_cutoff", {
                          cutoff: quality.legacy_unknown_cutoff,
                          day,
                          rate,
                        })}
                      </span>
                    </>
                  );
                })()}
              </>
            ) : undefined
          }
        />
        <ProgressRow
          icon={<Coins size={12} />}
          label={t("primitives.reentry_path_b")}
          current={fills}
          target={FILLS_TARGET}
          met={gate.kind === "path_b_met" || gate.kind === "both_met"}
          helper={
            <>
              <span className="text-warn">
                {quality.paper_evidence?.scope === "cutoff_since" ? "Cutoff/lifetime" : "Lifetime"} · nicht 24h
              </span>
              {" · "}
              Realized PnL:{" "}
              <span className={cn("font-mono", pnlUsd > 0 ? "text-pos" : pnlUsd < 0 ? "text-neg" : "")}>
                {pnlUsd >= 0 ? "+" : ""}
                {fmt(pnlUsd)}
              </span>
              {v1Disqualified && (
                <>
                  {" "}
                  <span
                    aria-label={V1_DISQUALIFIED_TOOLTIP}
                    title={V1_DISQUALIFIED_TOOLTIP}
                    className="inline-flex items-center align-middle gap-0.5 rounded-sm border border-warn/40 bg-warn/10 px-1 py-0 text-[9px] uppercase tracking-wide font-mono text-warn cursor-help select-none"
                  >
                    <Info size={9} aria-hidden />
                    v1→v2 Backfill
                  </span>
                </>
              )}
              {quality ? (
                <>
                  {" "}· {quality.paper_positions_closed} closed
                  {quality.paper_evidence ? (
                    <>
                      {" "}· 24h fills {quality.paper_evidence.fills_recent_24h}
                      {" "}· 24h PnL {fmt(quality.paper_evidence.realized_pnl_recent_24h_usd)}
                    </>
                  ) : null}
                </>
              ) : null}
            </>
          }
        />
      </div>

      <PaperEvidenceSplit quality={quality} pnlUsd={pnlUsd} fills={fills} />
      </ExpiredCollapse>

      <div className="mt-3 pt-3 border-t border-line-subtle text-2xs font-mono text-fg-subtle">
        Source-Vergleich und TV-4-Verdict: siehe <span className="text-fg-muted">Active Precision</span> unten.
      </div>

      {quality?.reentry?.warning ? (
        <div
          className={cn(
            "mt-3 rounded-sm border px-3 py-2 text-2xs font-mono",
            targetExpired
              ? "border-line-subtle bg-bg-2 text-fg-subtle"
              : "border-warn/30 bg-warn/10 text-warn",
          )}
        >
          {quality.reentry.warning}
        </div>
      ) : null}

      {priorityGate ? <PriorityGateRow summary={priorityGate} /> : null}
      <Explainer
        className="mt-3"
        summary="Was ist das Re-Entry-Gate?"
        what="Ein TV-Pivot-Ziel (D-125) mit zwei Pfaden: Pfad A (genug Alerts) ODER Pfad B (genug Paper-Fills). Erfüllt ein Pfad sein Kriterium, kann am Stichtag eine Re-Entry-Entscheidung getroffen werden."
        why="Ist das historische Ziel abgelaufen, bleibt der Fortschritt Evidenz, ist aber KEIN aktueller Freigabezustand — es braucht dann eine neue Gate-Definition. Lifetime-Fortschritt nicht mit aktueller 24h-Lage verwechseln."
      />
    </Card>
  );
}

// Abgelaufenes Ziel verschwendet keinen Prime-Platz mehr: die toten Pfad-A/B-
// Progressbars (historischer 200/10-Maßstab) wandern in ein eingeklapptes
// Neon-<details>; Logik/Evidenz bleiben on-demand erreichbar (Operator 2026-06-15).
function ExpiredCollapse({ expired, children }: { expired: boolean; children: ReactNode }) {
  if (!expired) return <>{children}</>;
  return (
    <details className="group mt-1 rounded-sm border border-line-subtle bg-bg-2/30">
      <summary className="flex cursor-pointer select-none list-none items-center gap-1.5 px-3 py-2 text-2xs font-mono uppercase tracking-wider text-fg-subtle attention-breathe-warn hover:text-warn">
        <ChevronRight size={12} className="shrink-0 transition-transform group-open:rotate-90" aria-hidden />
        Historischer Fortschritt · Ziel abgelaufen — aufklappen
      </summary>
      <div className="px-1 pb-2 pt-1 opacity-90">{children}</div>
    </details>
  );
}

// DALI Truth-Sprint 2026-06-04: 144-vs-0 explizit als zwei Spalten — links die
// historische Lifetime-Evidenz, rechts die aktuelle 24h-Lage. So liest niemand
// mehr historische Fills als aktuellen Fortschritt.
function PaperEvidenceSplit({
  quality,
  pnlUsd,
  fills,
}: {
  quality: DashboardQuality;
  pnlUsd: number;
  fills: number;
}) {
  const { fmt } = useCurrency();
  const ev = quality.paper_evidence;
  const scopeBadge =
    ev?.scope === "cutoff_since" ? "Cutoff/Lifetime" : "Lifetime";
  const recent24h = ev?.fills_recent_24h ?? 0;
  const pnl24h = ev?.realized_pnl_recent_24h_usd ?? 0;
  const closed24h = ev?.closed_recent_24h ?? 0;
  const feesNote = ev?.fees_slippage_included;
  // metric_contract als autoritative Erklaerung (keine Doppel-Wahrheit, nur
  // Tooltip-Quelle). Fehlt der Contract, bleibt das Feld leer.
  const contract = quality.metric_contract;
  const histExplain =
    getMetricContract(contract, "paper_fills_with_pnl")?.explanation ?? undefined;
  const recent24hWarn = getMetricWarning(contract, "paper_fills_recent_24h");

  return (
    <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
      {/* Links: historische Evidenz (Archiv-Anmutung) */}
      <div className="rounded-sm border border-line-subtle bg-bg-2/40 p-2.5 space-y-1.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span
            className="text-2xs font-mono uppercase tracking-wider text-fg-subtle"
            title={histExplain}
          >
            Historische Evidenz
          </span>
          <span className="rounded-xs border border-warn/40 bg-warn/10 px-1 py-0 text-[9px] font-mono uppercase tracking-wider text-warn">
            {scopeBadge}
          </span>
          <span className="rounded-xs border border-warn/40 bg-warn/10 px-1 py-0 text-[9px] font-mono uppercase tracking-wider text-warn">
            not 24h
          </span>
        </div>
        <div className="text-xs font-mono text-fg">
          {fills} fills ·{" "}
          <span className={cn(pnlUsd > 0 ? "text-pos" : pnlUsd < 0 ? "text-neg" : "")}>
            {pnlUsd >= 0 ? "+" : ""}
            {fmt(pnlUsd)}
          </span>
        </div>
        <div className="text-2xs text-fg-subtle">
          {quality.paper_positions_closed} closed gesamt
        </div>
      </div>

      {/* Rechts: aktuelle 24h-Lage (Current-Pulse-Anmutung) */}
      <div className="rounded-sm border border-line-subtle bg-bg-1 p-2.5 space-y-1.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-2xs font-mono uppercase tracking-wider text-fg-subtle">
            Aktuelle 24h-Lage
          </span>
          <span
            className={cn(
              "rounded-xs border px-1 py-0 text-[9px] font-mono uppercase tracking-wider",
              recent24h > 0
                ? "border-info/40 bg-info/10 text-info"
                : "border-line bg-bg-2 text-fg-subtle",
            )}
          >
            rolling 24h
          </span>
        </div>
        <div className="text-xs font-mono text-fg">
          {recent24h} fills ·{" "}
          <span className={cn(pnl24h > 0 ? "text-pos" : pnl24h < 0 ? "text-neg" : "text-fg-subtle")}>
            {pnl24h >= 0 ? "+" : ""}
            {fmt(pnl24h)}
          </span>
        </div>
        <div className="text-2xs text-fg-subtle">{closed24h} closed (24h)</div>
        {recent24hWarn && (
          <div className="text-2xs text-warn leading-snug">{recent24hWarn}</div>
        )}
      </div>

      <div className="sm:col-span-2 text-2xs text-fg-muted leading-relaxed">
        Historische Evidence erfüllt; aktuelle 24h-Ausführung separat bewerten.
        {feesNote && feesNote !== "yes" ? (
          <span
            className="ml-1.5 inline-flex items-center gap-0.5 rounded-xs border border-warn/40 bg-warn/10 px-1 py-0 text-[9px] font-mono uppercase tracking-wider text-warn align-middle cursor-help"
            title="Paper-PnL ist diagnostisch — Gebühren/Slippage/Intrabar-Risiken sind nicht garantiert eingerechnet."
          >
            <Info size={9} aria-hidden />
            Simulation Limits
          </span>
        ) : null}
      </div>
    </div>
  );
}

function PriorityGateRow({ summary }: { summary: PriorityGateSummary }) {
  // D-184 + DALI Truth-Sprint 2026-06-04: das Priority-Gate bekommt einen
  // klaren Verdict + Heartbeat. 0 filled darf nur dann neutral statt gruen
  // wirken, wenn der Loop nachweislich lebt — sonst HEARTBEAT UNKNOWN.
  const {
    threshold,
    gate_active,
    priority_rejected,
    other_rejected,
    completed,
    total_cycles,
    window_hours,
    priority_quality,
    top_reject_reason,
    heartbeat_status,
  } = summary;

  const verdict = resolvePriorityVerdict(summary);
  const verdictTone: "neg" | "warn" | "info" | "muted" =
    verdict.tone === "critical"
      ? "neg"
      : verdict.tone === "warn"
        ? "warn"
        : verdict.tone === "info"
          ? "info"
          : "muted";
  // filled nur gruen, wenn Loop verifiziert lebt UND nicht stale.
  const heartbeatHealthy =
    heartbeat_status === "active" || heartbeat_status === "active_blocking";
  const filledTone = completed > 0 && heartbeatHealthy ? "text-pos" : "text-fg";
  const blockedPct =
    total_cycles > 0 ? Math.round((priority_rejected / total_cycles) * 100) : 0;
  const lift = priority_quality?.high_priority_lift_pct;

  return (
    <div
      className={cn(
        "mt-2 pt-2 border-t border-line-subtle flex items-center justify-between flex-wrap gap-2 text-2xs font-mono text-fg-muted",
        verdict.tone === "critical" && "attention-breathe-neg",
      )}
      role="status"
      aria-label={`Priority-Gate ${verdict.verdict}, ${priority_rejected} von ${total_cycles} Cycles in ${window_hours}h blockiert, ${completed} filled`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-fg-subtle uppercase tracking-wide">Priority-Gate</span>
        <Badge tone={verdictTone} dot>
          {verdict.verdict}
        </Badge>
        <Badge tone={gate_active ? "warn" : "muted"}>
          {gate_active ? `aktiv · P≥${threshold}` : `aus · P≥${threshold}`}
        </Badge>
      </div>
      <div className="flex items-center gap-3 flex-wrap">
        <span>
          <span className="font-semibold">{priority_rejected}</span>
          <span className="text-fg-subtle"> rejected</span>
          {total_cycles > 0 && (
            <span className="text-fg-subtle">
              {" "}({blockedPct}% v. {total_cycles})
            </span>
          )}
        </span>
        <span>
          <span className={cn("font-semibold", filledTone)}>{completed}</span>
          <span className="text-fg-subtle"> filled</span>
        </span>
        {other_rejected > 0 && (
          <span>
            <span className="font-semibold">{other_rejected}</span>
            <span className="text-fg-subtle"> other-reject</span>
          </span>
        )}
        {lift != null && (
          <span className={cn(lift > 0 ? "text-pos" : "text-warn")}>
            Lift {lift > 0 ? "+" : ""}
            {lift.toFixed(2)}pp
          </span>
        )}
        <span className="text-fg-subtle">· {window_hours}h</span>
      </div>
      {top_reject_reason && (
        <div className="basis-full flex items-center gap-1.5 flex-wrap text-fg-subtle">
          Top-Reject:
          <span className="rounded-xs border border-line bg-bg-2 px-1 py-0 text-fg-muted">
            {top_reject_reason}
          </span>
        </div>
      )}
      {verdict.tone !== "ok" && verdict.tone !== "info" && (
        <div className={cn("basis-full", verdict.tone === "critical" ? "text-neg" : "text-warn")}>
          {verdict.detail}
        </div>
      )}
    </div>
  );
}

function bannerProps(
  gate: GateState,
  daysLeft: number,
  targetExpired: boolean,
  targetDate: string,
): { title: string; detail: string; className: string } {
  if (targetExpired) {
    return {
      title: "Historisches Ziel · archiviert",
      detail: "Re-Entry-Fortschritt bleibt als Evidenz erhalten; das alte Ziel ist kein aktueller Freigabezustand.",
      className: "border-line-subtle bg-bg-2 text-fg-muted",
    };
  }
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
      detail: `Nächster Pfad ${path} bei ${pct}% — bei Nichterreichen bis ${targetDate} kein weiterer Aufschub.`,
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
  targetExpired,
  targetDate,
  errorMessage,
}: {
  state: QualityFetchState;
  daysLeft: number;
  targetExpired: boolean;
  targetDate: string;
  errorMessage: string | null;
}) {
  const { t } = useT();
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
        subtitle={
          targetExpired
            ? `TV-Pivot D-125 · Ziel ${targetDate} archiviert · historische Evidenz`
            : `TV-Pivot D-125 · Ziel: ${targetDate} · Pfad A (alerts ≥${ALERTS_TARGET}) ODER Pfad B (fills ≥${FILLS_TARGET})`
        }
        right={
          <Badge tone={targetExpired ? "muted" : daysLeft <= 7 ? "warn" : "muted"} dot>
            {targetExpired ? <Archive size={10} /> : <Calendar size={10} />}
            {targetExpired ? "archiviert" : daysLeft === 0 ? "heute" : `${daysLeft} Tage`}
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
        <SkeletonRow icon={<Target size={12} />} label={t("primitives.reentry_path_a")} target={ALERTS_TARGET} />
        <SkeletonRow icon={<Coins size={12} />} label={t("primitives.reentry_path_b")} target={FILLS_TARGET} />
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
