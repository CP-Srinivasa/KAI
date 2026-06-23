import { useState } from "react";
import { AlertTriangle, ShieldCheck, TrendingUp, Minus, HelpCircle } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import { fetchEdgeVerdict } from "@/lib/api";
import { cn } from "@/lib/utils";

// @data-source: /dashboard/api/edge-window

/**
 * Edge-Truth-Panel (2026-06-23).
 *
 * Macht die kosten-bereinigte Edge-Aussage im Dashboard sicht- UND
 * nachvollziehbar — die Lücke aus der Edge-Epochen-Forensik
 * (memory kai_edge_epoch_contamination_20260623): bisher lebte die canonical
 * Edge nur in der CLI. Zeigt das Verdikt + den Ehrlichkeits-Kontext (welche
 * Quellen, wieviele Closes nach Quelle/Quarantäne ausgeschlossen) und lässt
 * zwischen "echter Generator" (canonical, kontaminationssicher) und "voller
 * Stream" (als kontaminiert markiert) umschalten.
 *
 * Truth-Linie: "kein bewiesener Edge" ist eine EHRLICHE Messung, kein Fehler —
 * UI kommuniziert das explizit (warn statt neg, erklärende Subzeile) und warnt
 * bei Mittel>0/Median<0 vor Ausreißer-Artefakten (DALI-Audit 2026-06-23).
 */

function fmtPct(p: number | null): string {
  if (p == null) return "—";
  return `${(p * 100).toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
}

function fmtBps(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)} bps`;
}

function fmtDay(s: string | null): string {
  if (!s) return "?";
  const dt = new Date(s);
  return Number.isNaN(dt.getTime())
    ? "?"
    : dt.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="text-3xs uppercase tracking-wider text-fg-subtle">{label}</div>
      <div className={cn("font-mono text-sm", tone)}>{value}</div>
    </div>
  );
}

const MODES = [
  {
    v: true,
    label: "Echter Generator",
    hint: "Nur attribuierte Generator-Quellen (autonomous_generator/real_analysis) — kontaminationssicher. Die belastbare Edge-Aussage.",
  },
  {
    v: false,
    label: "Voller Stream",
    hint: "Alle Closes inkl. unattributierter Mai-Canary-Trades — als kontaminiert markiert, nur zum Vergleich.",
  },
] as const;

export function EdgeTruthPanel() {
  const [canonical, setCanonical] = useState(true);
  const data = useApi((signal) => fetchEdgeVerdict(canonical, signal), 60_000, [canonical], {
    maxAttempts: 2,
    baseMs: 1500,
  });

  const toggle = (
    <div className="inline-flex items-center gap-2">
      <span className="text-3xs uppercase tracking-wider text-fg-subtle">Datenbasis</span>
      <div className="inline-flex items-center rounded-sm border border-line-subtle bg-bg-2 p-0.5">
        {MODES.map(({ v, label, hint }) => (
          <button
            key={String(v)}
            type="button"
            title={hint}
            onClick={() => setCanonical(v)}
            className={cn(
              "px-2 py-0.5 text-3xs font-mono rounded-xs transition-colors whitespace-nowrap",
              canonical === v ? "bg-info/15 text-info" : "text-fg-subtle hover:text-fg",
            )}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );

  const header = (
    <CardHeader
      title="Edge-Wahrheit"
      subtitle="Kosten-bereinigte Edge je abgeschlossenem Round-Trip — entscheidet nichts, belegt Evidenz"
      right={toggle}
    />
  );

  if (data.state !== "ready") {
    return (
      <Card padded>
        {header}
        <div className="py-6 text-center text-sm text-fg-subtle">
          {data.state === "error" ? `Fehler beim Laden: ${data.error.message}` : "Lädt …"}
        </div>
      </Card>
    );
  }

  const d = data.data;
  if (!d.available) {
    return (
      <Card padded>
        {header}
        <div className="py-6 text-center text-sm text-fg-subtle">
          Edge-Fenster nicht verfügbar: {d.error ?? "unbekannt"}
        </div>
      </Card>
    );
  }

  const proven = d.p_mu_net_positive != null && d.p_mu_net_positive >= 0.5;
  const insufficient = d.p_mu_net_positive == null;
  // "kein Beweis" ist NICHT "negativer Edge" -> warn (gelb), nicht neg (rot).
  const verdictTone = insufficient ? "muted" : proven ? "pos" : "warn";
  const verdictColor =
    verdictTone === "pos" ? "text-pos" : verdictTone === "warn" ? "text-warn" : "text-fg-muted";
  const verdictText = insufficient
    ? `Zu wenige Trades (n=${d.trade_count})`
    : proven
      ? "Edge plausibel positiv"
      : "Kein bewiesener Edge";
  const VerdictIcon = insufficient ? HelpCircle : proven ? TrendingUp : Minus;
  // Ausreißer-Artefakt: Mittel > 0 aber Median < 0 -> Schnitt von wenigen Trades getragen.
  const meanOutlierArtifact = d.median_net_bps < 0 && d.mean_net_bps > 0;

  return (
    <Card padded>
      {header}

      {/* Verdikt-Hero — der eine Satz, den der Operator lesen muss. Icon trägt die
          Semantik redundant zur Farbe (WCAG 1.4.1). */}
      <div className="mb-3 flex items-center gap-2.5">
        <VerdictIcon className={cn("h-5 w-5 shrink-0", verdictColor)} aria-hidden />
        <div>
          <div className={cn("font-mono text-2xl font-semibold leading-none", verdictColor)}>
            {fmtPct(d.p_mu_net_positive)}
          </div>
          <div className="mt-1 text-xs text-fg">
            {verdictText} ·{" "}
            <span className="text-fg-subtle">
              P(Netto-Edge &gt; 0), n = {d.trade_count}
            </span>
          </div>
        </div>
      </div>

      {/* Ehrlichkeit: Abwesenheit von Beweis ist korrekt + gewollt, kein Defekt. */}
      {!proven && !insufficient && (
        <p className="mb-3 text-2xs leading-relaxed text-fg-subtle">
          Die Daten belegen (noch) keinen kosten-bereinigten Vorteil — das ist eine ehrliche
          Messung, kein Fehler. KAI handelt nicht auf unbewiesenem Edge.
        </p>
      )}

      {/* Kennzahlen */}
      <div className="mb-3 grid grid-cols-3 gap-3">
        <Metric
          label="Median Netto"
          value={fmtBps(d.median_net_bps)}
          tone={d.median_net_bps >= 0 ? "text-pos" : "text-neg"}
        />
        <Metric
          label="Mittel Netto"
          value={fmtBps(d.mean_net_bps)}
          tone={d.mean_net_bps >= 0 ? "text-pos" : "text-neg"}
        />
        <Metric
          label="Realized Σ (USD)"
          value={`${d.realized_pnl_usd_sum >= 0 ? "+" : ""}${d.realized_pnl_usd_sum.toFixed(0)}`}
          tone={d.realized_pnl_usd_sum >= 0 ? "text-pos" : "text-neg"}
        />
      </div>
      {meanOutlierArtifact && (
        <div className="mb-3 text-2xs text-warn">
          Mittel positiv, Median negativ — der Schnitt wird von wenigen Ausreißern getragen, nicht
          von der Mehrheit der Trades. Median ist hier ehrlicher.
        </div>
      )}

      {/* Quellen-/Ehrlichkeits-Kontext */}
      <div className="space-y-1.5 border-t border-line-subtle pt-3 text-2xs text-fg-subtle">
        {d.canonical ? (
          <div className="flex items-start gap-1.5">
            <ShieldCheck className="mt-0.5 h-3 w-3 shrink-0 text-pos" aria-hidden />
            <span>
              <span className="text-fg">Canonical</span> — nur attribuierte Generator-Quellen
              {d.source_allowlist ? ` (${d.source_allowlist.join(", ")})` : ""}.{" "}
              {d.closes_excluded_by_source} Close(s) nach Quelle ausgeschlossen (epochen-fremd /
              unattributiert).
            </span>
          </div>
        ) : (
          <div className="flex items-start gap-1.5">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warn" aria-hidden />
            <span>
              <span className="text-warn">Voller Stream — kontaminiert.</span> Enthält
              unattributierte Mai-Canary-Closes. Für die belastbare Generator-Edge auf „Echter
              Generator" wechseln.
            </span>
          </div>
        )}
        <div>
          {d.quarantine_excluded_count} korrupte Close(s) per forensischer Signatur quarantäniert
          (aus allen Edge-Zahlen ausgeschlossen).
        </div>
        {d.live_orders_attempted > 0 && (
          <div className="text-neg">
            ⚠ {d.live_orders_attempted} Nicht-Paper-Fill(s) im Fenster — Integritäts-Alarm.
          </div>
        )}
        <div className="text-fg-muted">
          Fenster: {fmtDay(d.window_started_at)}–{fmtDay(d.window_ended_at)}
        </div>
      </div>
    </Card>
  );
}
