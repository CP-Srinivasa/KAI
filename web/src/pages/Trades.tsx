import { Fragment } from "react";
import { AlertCircle, RefreshCw, ArrowLeftRight } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card, CardHeader } from "@/components/ui/Primitives";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import { fetchTradingLoopStatus, fetchRecentCycles, type TradingCycle, type TradingLoopStatus, type RecentCyclesSummary } from "@/lib/api";
import type { AsyncState } from "@/lib/useApi";
import { cn } from "@/lib/utils";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { LABEL_DE, CYCLE_STATUS_EXPLAIN } from "@/lib/labels";

// 2026-05-10 DALI-T1: Klartext-Synopsis aus Cycle-Buckets ableiten.
// Operator-Frage "was sagt mir die Seite?" wird in 1 Satz beantwortet.
function summarizeCycles(cyclesList: TradingCycle[]): string {
  if (cyclesList.length === 0) return "Noch keine Cycles in der jüngsten Historie.";
  const counts: Record<string, number> = {};
  for (const c of cyclesList) counts[c.status] = (counts[c.status] ?? 0) + 1;
  const parts: string[] = [];
  const completed = counts.completed ?? 0;
  if (completed > 0) parts.push(`${completed}× ausgeführt`);
  if (counts.no_signal) parts.push(`${counts.no_signal}× kein Signal`);
  if (counts.no_market_data) parts.push(`${counts.no_market_data}× keine Markt-Daten`);
  if (counts.consensus_rejected) parts.push(`${counts.consensus_rejected}× Konsens abgelehnt`);
  if (counts.order_failed) parts.push(`${counts.order_failed}× Order fehlgeschlagen`);
  if (counts.stale_data) parts.push(`${counts.stale_data}× veraltete Daten`);
  // letzten ausgeführten Trade finden
  const lastCompleted = [...cyclesList].reverse().find((c) => c.status === "completed");
  const lastSegment = lastCompleted
    ? ` — letzter Trade ${lastCompleted.symbol} ${formatTimeShort(lastCompleted.started_at)}`
    : "";
  return `Letzte ${cyclesList.length} Cycles: ${parts.join(", ") || "—"}${lastSegment}.`;
}


// 2026-05-12 DALI-arcade-T1: Bucket-Aggregation + Systemstatus-Headline.
// Spec 4.1 + 4.2. Liefert die 5 Buckets als feste Felder + abgeleitete
// tone/headline. Keine erfundenen Felder - nur status_counts/recent_cycles
// als Quelle (RecentCyclesSummary aus lib/api.ts).
type CyclesHealth = {
  total: number;
  analyzed: number;
  validSignals: number;
  executed: number;
  riskBlocked: number;
  apiFail: number;
  ghostsTotal: number;
  headline: string;
  tone: "pos" | "neg" | "warn" | "info" | "muted";
};

function aggregateCyclesHealth(cycles: TradingCycle[]): CyclesHealth {
  const sc: Record<string, number> = {};
  for (const c of cycles) sc[c.status] = (sc[c.status] ?? 0) + 1;
  const total = cycles.length;
  const executed = sc.completed ?? 0;
  const riskBlocked =
    (sc.risk_rejected ?? 0) + (sc.consensus_rejected ?? 0) + (sc.priority_rejected ?? 0);
  const apiFail =
    (sc.no_market_data ?? 0) + (sc.stale_data ?? 0) + (sc.order_failed ?? 0);
  const validSignals =
    executed + (sc.risk_rejected ?? 0) + (sc.consensus_rejected ?? 0) + (sc.order_failed ?? 0);
  const ghostsTotal = riskBlocked + apiFail;
  const orderFail = sc.order_failed ?? 0;

  let headline: string;
  let tone: CyclesHealth["tone"];
  if (total === 0) {
    headline = "Noch keine Cycles im aktuellen Fenster";
    tone = "muted";
  } else if (orderFail >= 3) {
    headline = "Systemstatus: INSTABIL - Execution-Layer pruefen";
    tone = "neg";
  } else if (total > 0 && ghostsTotal / total > 0.4) {
    headline = "Systemstatus: Aufmerksamkeit noetig";
    tone = "warn";
  } else if (executed === 0 && total >= 10) {
    headline = "Systemstatus: ruhig - kein Trade-Anlass";
    tone = "info";
  } else {
    headline = "Systemstatus: stabil";
    tone = "pos";
  }

  return { total, analyzed: total, validSignals, executed, riskBlocked, apiFail, ghostsTotal, headline, tone };
}

function formatTimeShort(iso: string): string {
  try {
    const dt = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - dt.getTime();
    const diffH = Math.round(diffMs / 3600000);
    if (diffH < 1) return "vor weniger als 1h";
    if (diffH < 24) return `vor ${diffH}h`;
    return `vor ${Math.round(diffH / 24)}d`;
  } catch {
    return iso.substring(11, 16);
  }
}

// Notes-humanize: kurz lesbar statt snake_case-Roh-String.
function humanizeNote(note: string): string {
  // Beispiel: "signal_block:reason=consensus" → "signal block (reason=consensus)"
  const [head, tail] = note.split(":");
  const headHum = LABEL_DE[head ?? ""] ?? head?.replace(/_/g, " ") ?? note;
  if (tail) return `${headHum} (${tail})`;
  return headHum;
}

export function TradesPage() {
  const { t } = useT();
  const status = useApi(fetchTradingLoopStatus, 20_000);
  const cycles = useApi((s) => fetchRecentCycles(30, s), 15_000);

  const cyclesList = cycles.state === "ready" ? cycles.data.recent_cycles : [];

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.trades.title")}
        tone="pos"
        icon={<ArrowLeftRight size={18} />}
        // 2026-05-12 DALI-arcade-T1: divider=false - die Synthwave-Linie
        // wandert als border-top an die erste Card (Cycles-Healthcheck).
        // Vorher waren BEIDE aktiv -> schwebende Doppellinie (F-001).
        divider={false}
        sub={
          status.state === "ready"
            ? `Mode: ${status.data.mode} · Letzter Cycle: ${
                status.data.last_cycle_completed_at
                  ? formatTimeShort(status.data.last_cycle_completed_at)
                  : "—"
              }${
                status.data.last_cycle_status
                  ? ` · ${LABEL_DE[status.data.last_cycle_status] ?? status.data.last_cycle_status}`
                  : ""
              }`
            : "Was hat KAI entschieden - und mit welchem Ergebnis?"
        }
        right={
          <Button onClick={() => { status.reload(); cycles.reload(); }} variant="outline" size="sm">
            <RefreshCw size={12} /> Aktualisieren
          </Button>
        }
      />

      {status.state === "error" && <ErrorCard kind={status.error.kind} message={status.error.message} path="/operator/trading-loop/status" />}

      {/* 2026-05-12 DALI-arcade-T1: Cycles-Healthcheck-Card.
          Ersetzt den alten Hero-KPI "0 Trades von 30 Cycles" (F-002), der
          Operator demoralisierte ohne zu erklaeren. Stattdessen:
          - Headline mit Systemstatus-Tone (stabil/Aufmerksamkeit/INSTABIL)
          - 5 semantische Buckets (analysiert / valide / ausgefuehrt / risk-blocked / API-Fail)
          - 1-Zeilen-Klartext-Synopsis darunter
          - Auto-Loop + Mode als kompakte Footer-Pillen
          Synthwave-pulse-edge ist hier als border-top integriert (F-001-Fix). */}
      <CyclesHealthcheckCard
        cycles={cycles}
        status={status}
        cyclesList={cyclesList}
      />

      {/* DALI-T-Guardrails-v2: ausdrucksvolle Schutzschalter-Ansicht.
          Operator: "Was sollen mir die Werte sagen, ausdrucksstaerker
          und visueller darstellen."
          Drei klare Status-Pillen (Execution / Write-Back / Run-Once)
          + kompakte Letzter-Cycle-Karte mit Symbol + Status + relativer
          Zeit. RowKV-Snake-Case-Liste raus. */}
      {status.state === "ready" && (
        <Card padded>
          <CardHeader
            title="Schutzschalter"
            subtitle="Was darf KAI gerade machen — und was ist gesperrt?"
          />

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
            <GuardrailPill
              label="Real-Order-Execution"
              active={status.data.execution_enabled}
              onText="aktiv"
              offText="aus"
              activeTone="neg"
              hint={status.data.execution_enabled
                ? "Echte Orders werden auf der Boerse platziert."
                : "Nur Paper/Shadow — keine echten Orders."}
            />
            <GuardrailPill
              label="Trading-Journal Schreiben"
              active={status.data.write_back_allowed}
              onText="erlaubt"
              offText="gesperrt"
              activeTone="warn"
              hint={status.data.write_back_allowed
                ? "Cycles werden ins Journal geschrieben."
                : "Read-only — Journal ist gesperrt."}
            />
            <GuardrailPill
              label="Run-Once Trigger"
              active={status.data.run_once_allowed}
              onText="bereit"
              offText="blockiert"
              activeTone="pos"
              hint={status.data.run_once_allowed
                ? "Operator kann manuell einen Cycle anstossen."
                : status.data.run_once_block_reason ?? "Run-Once aktuell nicht moeglich."}
            />
          </div>

          {/* Letzter Cycle als kompakte Hervor-Card */}
          {status.data.last_cycle_symbol && (
            <div className="rounded-md border border-line-subtle bg-bg-2 p-3">
              <div className="flex items-baseline justify-between gap-3 flex-wrap">
                <div className="flex items-baseline gap-3">
                  <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Letzter Cycle</span>
                  <span className="font-mono font-semibold text-base text-fg">{status.data.last_cycle_symbol}</span>
                  {status.data.last_cycle_status && (
                    <span title={CYCLE_STATUS_EXPLAIN[status.data.last_cycle_status] ?? status.data.last_cycle_status}>
                      <Badge
                        tone={
                          status.data.last_cycle_status === "completed" ? "pos"
                          : status.data.last_cycle_status === "order_failed" || status.data.last_cycle_status === "consensus_rejected"
                            ? "neg"
                            : "warn"
                        }
                      >
                        {LABEL_DE[status.data.last_cycle_status] ?? status.data.last_cycle_status}
                      </Badge>
                    </span>
                  )}
                </div>
                <span
                  className="text-2xs text-fg-subtle font-mono"
                  title={status.data.last_cycle_id ? `Cycle-ID: ${status.data.last_cycle_id}` : undefined}
                >
                  {status.data.last_cycle_completed_at
                    ? formatTimeShort(status.data.last_cycle_completed_at)
                    : "—"}
                </span>
              </div>
            </div>
          )}
        </Card>
      )}

      <Card padded={false} className="overflow-hidden">
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-line-subtle">
          <div className="text-sm font-semibold tracking-tight text-fg">Letzte Trading-Cycles</div>
          <div className="text-2xs text-fg-subtle font-mono">
            {cycles.state === "ready" ? `${cycles.data.recent_cycles.length} Einträge` : ""}
          </div>
        </div>
        {cycles.state === "error" ? (
          <div className="p-4">
            <ErrorCard kind={cycles.error.kind} message={cycles.error.message} path="/operator/trading-loop/recent-cycles" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-fg-subtle text-2xs uppercase tracking-wider">
                  <th className="text-left font-semibold px-4 py-2">Zeit</th>
                  <th className="text-left font-semibold px-4 py-2">Symbol</th>
                  <th className="text-left font-semibold px-4 py-2">Status</th>
                  <th className="text-left font-semibold px-4 py-2 whitespace-nowrap">
                    Pipeline
                    <span className="ml-1 normal-case text-fg-subtle/70 font-normal">(Daten → Signal → Risk → Order → Fill)</span>
                  </th>
                  <th className="text-left font-semibold px-4 py-2">Notes</th>
                </tr>
              </thead>
              <tbody>
                {cycles.state === "loading" && (
                  <tr><td colSpan={5} className="px-4 py-6 text-center text-fg-subtle">{t("common.loading")}</td></tr>
                )}
                {cycles.state === "ready" && cycles.data.recent_cycles.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-6 text-center text-fg-subtle">{t("common.no_data")}</td></tr>
                )}
                {cycles.state === "ready" && cycles.data.recent_cycles.slice().reverse().map((c) => <CycleRow key={c.cycle_id} c={c} />)}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <PreparedPanel
        title="Guarded run-once trigger"
        reason="POST /operator/trading-loop/run-once (Idempotency-Key nötig) erlaubt gezielten Cycle im Paper/Shadow-Modus."
        detail="UI-Trigger erfordert confirm-flow + Idempotency-Key-Generierung — in Phase 2 geplant."
      />
    </div>
  );
}


// 2026-05-12 DALI-arcade-T1: Cycles-Healthcheck-Card.
// Spec 2.1 (Wireframe) + 4.1 (Field-Mapping) + 4.2 (Headline-Regel).
// Synthwave-pulse-edge als border-top - integriert, nicht schwebend (F-001-Fix).
function CyclesHealthcheckCard({
  cycles,
  status,
  cyclesList,
}: {
  cycles: AsyncState<RecentCyclesSummary>;
  status: AsyncState<TradingLoopStatus>;
  cyclesList: TradingCycle[];
}) {
  if (cycles.state === "loading") {
    return (
      <Card padded className="synthwave-pulse-edge">
        <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
          Cycles-Healthcheck
        </div>
        <div className="mt-2 text-sm text-fg-subtle">Daten werden geladen ...</div>
      </Card>
    );
  }
  const health = aggregateCyclesHealth(cyclesList);
  const synopsis = summarizeCycles(cyclesList);

  const toneTextClass =
    health.tone === "neg" ? "text-neg"
    : health.tone === "warn" ? "text-warn"
    : health.tone === "info" ? "text-info"
    : health.tone === "pos" ? "text-pos"
    : "text-fg-subtle";

  // Attention-breathe ist eine bestehende Glow-Utility - dezent, kein Stroboskop.
  // Bei reduced-motion ohnehin gestoppt. Wir wenden sie nur an, wenn die
  // Klasse global existiert; sonst fallback auf nur die Tone-Farbe.
  const attentionClass =
    health.tone === "neg" ? "attention-breathe-neg"
    : health.tone === "warn" ? "attention-breathe-warn"
    : "";

  // Aria-Label: Klartext-Zusammenfassung fuer Screen-Reader (A11y, F-009).
  const ariaLabel = health.total === 0
    ? "Cycles-Healthcheck. Noch keine Cycles im aktuellen Fenster."
    : `Cycles-Healthcheck. ${health.total} Cycles analysiert. ` +
      `${health.executed} ausgefuehrt, ${health.riskBlocked} durch Risk-Engine blockiert, ` +
      `${health.apiFail} durch API oder Exchange fehlgeschlagen. ${health.headline}.`;

  return (
    <Card
      padded
      className={cn("synthwave-pulse-edge", attentionClass)}
    >
      <div role="region" aria-label={ariaLabel}>
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0 flex-1">
            <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
              Cycles-Healthcheck
            </div>
            <div className={cn("mt-1 text-base font-semibold tracking-tight", toneTextClass)}>
              {health.total} Cycles analysiert
              {health.total > 0 && (
                <span className="text-fg-subtle font-normal">
                  {" "}- {health.headline.replace(/^Systemstatus: /, "")}
                </span>
              )}
            </div>
          </div>
          {status.state === "ready" && (
            <div className="flex items-center gap-2 shrink-0 flex-wrap">
              <Badge tone={status.data.auto_loop_enabled ? "pos" : "muted"} dot>
                Auto-Loop {status.data.auto_loop_enabled ? "aktiv" : "aus"}
              </Badge>
              <Badge tone="info">Mode: {status.data.mode}</Badge>
            </div>
          )}
        </div>

        {/* 5-Bucket-Strip - Operator-Prompt 13 */}
        <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5">
          <BucketTile label="analysiert" value={health.analyzed} tone="info" />
          <BucketTile label="valide Signale" value={health.validSignals} tone="ai" />
          <BucketTile label="ausgefuehrt" value={health.executed} tone="pos" />
          <BucketTile label="risk-blocked" value={health.riskBlocked} tone="warn" />
          <BucketTile label="API-Fail" value={health.apiFail} tone="neg" />
        </div>

        {health.total > 0 && (
          <div className="mt-3 text-xs text-fg-muted leading-relaxed">
            {synopsis}
          </div>
        )}

        {health.total === 0 && (
          <div className="mt-3 text-xs text-fg-subtle italic">
            Sobald der erste Cycle laeuft, erscheinen hier Statistik und Systemstatus.
          </div>
        )}
      </div>
    </Card>
  );
}

// Bucket-Kachel: Label (klein, gedimmt) + grosse tonalisierte Number.
// Klein gehalten - 5 davon nebeneinander auf Desktop, 2-3 auf Mobile.
function BucketTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "info" | "ai" | "pos" | "warn" | "neg";
}) {
  const accentBar =
    tone === "pos" ? "bg-pos"
    : tone === "neg" ? "bg-neg"
    : tone === "warn" ? "bg-warn"
    : tone === "ai" ? "bg-ai"
    : "bg-info";
  const valueColor =
    tone === "pos" ? "text-pos"
    : tone === "neg" ? "text-neg"
    : tone === "warn" ? "text-warn"
    : tone === "ai" ? "text-ai"
    : "text-info";
  return (
    <div className="rounded-md border border-line-subtle bg-bg-2 p-2.5 flex gap-2 items-stretch">
      <span className={cn("w-1 rounded-full shrink-0", accentBar)} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
          {label}
        </div>
        <div className={cn("mt-0.5 font-mono font-semibold text-xl tabular-nums", valueColor)}>
          {value}
        </div>
      </div>
    </div>
  );
}

function CycleRow({ c }: { c: TradingCycle }) {
  const toneFor = (s: string) => {
    if (s === "completed") return "pos";
    if (s === "no_signal") return "muted";
    if (s === "no_market_data" || s === "stale_data") return "warn";
    if (s === "order_failed" || s === "consensus_rejected") return "neg";
    return "neutral";
  };
  // 2026-05-10 DALI-T-Pipeline: 5x BoolDot-Spalten → 1 Pipeline-Visualisierung
  // mit Neon-Lichtpunkten (cyan-glow für erreicht, gedimmt sonst). Verbindungs-
  // linien zwischen Steps zeigen Fluss; abgebrochen wo der Cycle stoppte.
  // completed-Rows bekommen subtilen pos-Glow als Zeilen-Hintergrund.
  return (
    <tr
      className={cn(
        "border-t border-line-subtle hover:bg-bg-2",
        c.status === "completed" && "bg-pos/[0.03]",
      )}
    >
      <td className="px-4 py-2 font-mono text-2xs text-fg-subtle whitespace-nowrap">
        {c.started_at.substring(11, 19)}
      </td>
      <td className="px-4 py-2 font-mono font-semibold whitespace-nowrap">{c.symbol}</td>
      <td className="px-4 py-2">
        <span title={CYCLE_STATUS_EXPLAIN[c.status] ?? c.status}>
          <Badge tone={toneFor(c.status)}>{LABEL_DE[c.status] ?? c.status}</Badge>
        </span>
      </td>
      <td className="px-4 py-2">
        <CyclePipeline c={c} />
      </td>
      <td className="px-4 py-2 max-w-[420px]">
        <div className="flex flex-wrap gap-1">
          {c.notes.slice(0, 3).map((n, i) => (
            <span
              key={i}
              title={n}
              className="inline-flex items-center rounded-xs border border-line-subtle bg-bg-2 px-1.5 py-0.5 text-[10px] font-mono text-fg-subtle"
            >
              {humanizeNote(n)}
            </span>
          ))}
          {c.notes.length > 3 && (
            <span className="text-2xs text-fg-subtle">+{c.notes.length - 3}</span>
          )}
          {c.notes.length === 0 && (
            <span className="text-2xs text-fg-subtle italic">—</span>
          )}
        </div>
      </td>
    </tr>
  );
}

// Neon-Pipeline: 5 Steps als runde Lichtpunkte mit Glow + Verbindungslinien.
// Reached-Steps: cyan/info mit glow-info. Nicht erreicht: gedimmtes fg-subtle/20.
// Fail-Step (z.B. order_failed bei letztem reached): wird in neg/glow-neg gerendert.
function CyclePipeline({ c }: { c: TradingCycle }) {
  const steps = [
    { reached: c.market_data_fetched, label: "Markt-Daten", key: "data" },
    { reached: c.signal_generated, label: "Signal erzeugt", key: "signal" },
    { reached: c.risk_approved, label: "Risk-Gate bestanden", key: "risk" },
    { reached: c.order_created, label: "Order erstellt", key: "order" },
    { reached: c.fill_simulated, label: "Fill simuliert", key: "fill" },
  ];
  // Den letzten erreichten Step finden — wenn der Cycle dort gestoppt ist und
  // Status ein Fehler-Status ist, soll dieser Punkt rot leuchten.
  // (findLastIndex ist ES2023 — target ist niedriger, also manueller Loop.)
  let lastReachedIdx = -1;
  for (let j = steps.length - 1; j >= 0; j--) {
    if (steps[j].reached) {
      lastReachedIdx = j;
      break;
    }
  }
  const isFail = c.status === "order_failed" || c.status === "consensus_rejected";
  const isWarn = c.status === "no_market_data" || c.status === "stale_data";
  const stoppedAt = lastReachedIdx >= 0 && lastReachedIdx < steps.length - 1 ? lastReachedIdx : -1;
  return (
    <div className="flex items-center gap-0">
      {steps.map((s, i) => {
        const isStopAt = i === stoppedAt && (isFail || isWarn);
        const dotTone = !s.reached
          ? "bg-fg-subtle/20"
          : isStopAt && isFail
            ? "bg-neg glow-neg"
            : isStopAt && isWarn
              ? "bg-warn glow-warn"
              : "bg-info glow-info";
        const lineTone =
          i < steps.length - 1
            ? steps[i + 1].reached
              ? "bg-info/45"
              : s.reached && isStopAt && isFail
                ? "bg-neg/30"
                : s.reached && isStopAt && isWarn
                  ? "bg-warn/30"
                  : "bg-fg-subtle/15"
            : "";
        return (
          <Fragment key={s.key}>
            <span
              title={`${s.label}: ${s.reached ? "✓ erreicht" : "× nicht erreicht"}`}
              className={cn("h-2.5 w-2.5 rounded-full shrink-0 transition-transform hover:scale-150", dotTone)}
            />
            {i < steps.length - 1 && (
              <span className={cn("h-px w-4 shrink-0", lineTone)} />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

// Schutzschalter-Pille: visueller Status-Indikator mit Klartext + Hint.
// Active/Inactive-Tone unterscheiden sich semantisch — bei "Real-Execution
// aktiv" ist das ein Warn-/Neg-Zustand (Live-Trading läuft!), bei "Write-Back
// erlaubt" ist das ein Warn-Zustand (Journal wird geschrieben), bei "Run-Once
// bereit" ist das ein Pos-Zustand (Operator kann triggern). Daher der
// activeTone-Prop — Inactive ist immer muted.
function GuardrailPill({
  label,
  active,
  onText,
  offText,
  activeTone,
  hint,
}: {
  label: string;
  active: boolean;
  onText: string;
  offText: string;
  activeTone: "pos" | "neg" | "warn";
  hint: string;
}) {
  const tone = active ? activeTone : "muted";
  const accentBar =
    tone === "pos" ? "bg-pos"
    : tone === "neg" ? "bg-neg"
    : tone === "warn" ? "bg-warn"
    : "bg-fg-subtle/40";
  const valueColor =
    tone === "pos" ? "text-pos"
    : tone === "neg" ? "text-neg"
    : tone === "warn" ? "text-warn"
    : "text-fg-muted";
  return (
    <div className="rounded-md border border-line-subtle bg-bg-2 p-3 flex gap-2">
      <span className={cn("h-full w-1 rounded-full shrink-0", accentBar)} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">{label}</div>
        <div className={cn("mt-1 font-mono font-semibold text-sm", valueColor)}>
          {active ? onText : offText}
        </div>
        <div className="mt-1 text-2xs text-fg-subtle leading-relaxed">{hint}</div>
      </div>
    </div>
  );
}

function ErrorCard({ kind, message, path }: { kind: string; message: string; path: string }) {
  return (
    <Card padded className="border-neg/30 bg-neg/5">
      <div className="flex items-start gap-3 text-xs text-neg">
        <AlertCircle size={16} className="mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="font-semibold">Endpoint nicht erreichbar</div>
          <div className="text-fg-muted mt-1 break-words">{kind} · {message}</div>
          <div className="text-2xs text-fg-subtle mt-1 font-mono break-all">{path}</div>
        </div>
      </div>
    </Card>
  );
}
