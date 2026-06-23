import { useState } from "react";
import {
  AlertTriangle,
  ShieldCheck,
  TrendingUp,
  Minus,
  HelpCircle,
  ChevronDown,
} from "lucide-react";
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

function fmtMedianPct(bps: number): string {
  return Math.abs(bps / 100).toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}

// Übersetzt das Verdikt in einen Klartext-Satz für Nicht-Quants.
function plainSentence(args: {
  insufficient: boolean;
  proven: boolean;
  median_net_bps: number;
  trade_count: number;
}): string {
  const { insufficient, proven, median_net_bps, trade_count } = args;
  if (insufficient) {
    return `Zu wenige abgeschlossene Trades (n=${trade_count}), um einen Vorteil überhaupt zu belegen.`;
  }
  const pct = fmtMedianPct(median_net_bps);
  const verb = median_net_bps >= 0 ? `liegt bei ~+${pct} %` : `verliert ~${pct} %`;
  if (proven) {
    return `Verdient KAI nach Kosten Geld? Aktuell plausibel ja — der typische Trade ${verb} nach Gebühren.`;
  }
  return `Verdient KAI nach Kosten Geld? Noch nicht bewiesen — der typische Trade ${verb} nach Gebühren.`;
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
  const [showInfo, setShowInfo] = useState(false);
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

      {/* Verdikt-Hero, Klartext zuerst: Ampel-Headline -> Plain-Satz -> große
          Wahrscheinlichkeit mit verständlichem Label. Icon trägt die Semantik
          redundant zur Farbe (WCAG 1.4.1). */}
      <div className="mb-3">
        <div className="mb-1.5 flex items-center gap-2">
          <VerdictIcon className={cn("h-5 w-5 shrink-0", verdictColor)} aria-hidden />
          <span className={cn("text-base font-semibold leading-tight", verdictColor)}>
            {verdictText}
          </span>
        </div>
        <p className="mb-2 text-xs leading-relaxed text-fg">
          {plainSentence({
            insufficient,
            proven,
            median_net_bps: d.median_net_bps,
            trade_count: d.trade_count,
          })}
        </p>
        <div className="flex items-baseline gap-2">
          <span className={cn("font-mono text-2xl font-semibold leading-none", verdictColor)}>
            {fmtPct(d.p_mu_net_positive)}
          </span>
          <span className="text-2xs leading-snug text-fg-subtle">
            Wahrscheinlichkeit, dass der Vorteil wirklich positiv ist · n = {d.trade_count}
          </span>
        </div>
      </div>

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

      {/* Info-Feld zum Nachlesen: was Edge/Wahrheit/die Zahlen bedeuten. Klappt
          aus, damit die Hauptansicht ruhig bleibt. */}
      <div className="mt-3 border-t border-line-subtle pt-3">
        <button
          type="button"
          onClick={() => setShowInfo((s) => !s)}
          aria-expanded={showInfo}
          className="inline-flex items-center gap-1.5 text-2xs text-fg-subtle hover:text-fg"
        >
          <HelpCircle className="h-3 w-3" aria-hidden />
          Was bedeutet das?
          <ChevronDown
            className={cn("h-3 w-3 transition-transform", showInfo && "rotate-180")}
            aria-hidden
          />
        </button>
        {showInfo && (
          <div className="mt-2 space-y-1.5 text-2xs leading-relaxed text-fg-subtle">
            <p>
              <span className="text-fg">Edge</span> — Verdient die Strategie nach Abzug aller Kosten
              (Gebühren/Spread) systematisch Geld pro abgeschlossenem Trade? Das ist die eine Frage,
              die über alles entscheidet.
            </p>
            <p>
              <span className="text-fg">Wahrheit / Canonical</span> — Gezeigt wird die saubere Zahl:
              nur echte Generator-Quellen, die korrupten Mai-Canary-Trades (z. B. ein MATIC-Fake)
              sind rausgerechnet. „Voller Stream" ist dieselbe Zahl mit dem Fake drin — nur zum
              Vergleich, als kontaminiert markiert.
            </p>
            <p>
              <span className="text-fg">Die große Prozentzahl</span> — Wahrscheinlichkeit, dass der
              wahre Vorteil positiv ist. Unter ~50 % heißt: eher Verlust als Gewinn. Für „bewiesen"
              bräuchte es klar über 90 %.
            </p>
            <p>
              <span className="text-fg">Median Netto</span> — der typische Trade nach Kosten
              (100 bps = 1 %). Negativ = der mittlere Trade verliert.
            </p>
            <p>
              <span className="text-fg">Mittel Netto</span> — der Durchschnitt. Liegt er über dem
              Median, ziehen wenige Ausreißer den Schnitt hoch — dann ist der Median ehrlicher.
            </p>
            <p>
              <span className="text-fg">Realized Σ</span> — die tatsächlich realisierte Summe in USD
              über das angezeigte Fenster.
            </p>
            <p>
              <span className="text-fg">„Entscheidet nichts, belegt Evidenz"</span> — das Panel löst
              keine Trades aus; es ist reine Beweislage. „Kein bewiesener Edge" ist eine ehrliche
              Messung, kein Fehler — KAI handelt nicht auf unbewiesenem Edge.
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}
