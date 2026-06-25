import { useState } from "react";
import { HelpCircle, ChevronDown, ArrowRight } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import { fetchChurnReport } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useCurrency } from "@/state/CurrencyProvider";

// @data-source: /dashboard/api/churn

/**
 * Churn- / Fee-Effizienz-Panel (Operator /goal 2026-06-25, „Trades-pro-Edge").
 *
 * Macht sichtbar, was die erste Churn-Analyse + Red-Team (S-001) ergaben: die
 * Strategie ist VOR Kosten ~break-even, erst die Round-Trip-Fees machen daraus
 * einen Verlust. Zeigt Brutto-vor-Fees → Netto-nach-Fees, den Fee-Drag und die
 * Fees/Handelstag-Kadenz (Varianz-Sicht). Echte Audit-Fees inkl. TP-Tier-Partials.
 *
 * Truth-Linie: READ-ONLY Messung, ändert KEIN Handelsverhalten. Im Paper sind die
 * Fees SIMULIERT — der Wert ist die Fee-Effizienz-Spur Richtung Live-Kapital (G2),
 * NICHT ein Hebel zum Weniger-Handeln (Engpass = zu WENIGE Trades).
 */

function fmtMin(m: number | null): string {
  if (m == null) return "—";
  if (m < 90) return `${m.toFixed(0)} min`;
  if (m < 1440) return `${(m / 60).toFixed(1)} h`;
  return `${(m / 1440).toFixed(1)} d`;
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

export function ChurnPanel() {
  const { fmt } = useCurrency();
  const [showInfo, setShowInfo] = useState(false);
  const data = useApi((signal) => fetchChurnReport(signal), 60_000, [], {
    maxAttempts: 2,
    baseMs: 1500,
  });

  const header = (
    <CardHeader
      title="Churn / Fee-Effizienz"
      subtitle="Brutto-vor-Fees → Netto-nach-Fees je Round-Trip — reine Messung, kein Handelseingriff"
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
          Churn-Fenster nicht verfügbar: {d.error ?? d.note ?? "keine Realisierungen"}
        </div>
      </Card>
    );
  }

  const grossPos = d.gross_usd >= 0;
  const netPos = d.net_usd >= 0;
  // Kernsatz: dreht die Fee-Last ein Plus in ein Minus?
  const flipped = grossPos && !netPos;
  const maxDayFee = Math.max(1, ...d.per_day.map((p) => p.fee_spend_usd));

  const dragLabel =
    d.fee_drag_pct == null
      ? "n/a (Brutto ≈ 0 → Fees dominieren)"
      : `${(d.fee_drag_pct / 100).toFixed(1)}× der Brutto-Bewegung`;

  return (
    <Card padded>
      {header}

      {/* Hero: Brutto -> (Fees) -> Netto, der eine Satz der Fee-Effizienz. */}
      <div className="mb-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <div className="flex flex-col">
            <span className="text-3xs uppercase tracking-wider text-fg-subtle">Vor Fees</span>
            <span className={cn("font-mono text-xl font-semibold", grossPos ? "text-pos" : "text-neg")}>
              {grossPos ? "+" : ""}
              {fmt(d.gross_usd)}
            </span>
          </div>
          <ArrowRight className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden />
          <div className="flex flex-col">
            <span className="text-3xs uppercase tracking-wider text-fg-subtle">
              − Fees {fmt(d.round_trip_fees_usd)}
            </span>
            <span className={cn("font-mono text-xl font-semibold", netPos ? "text-pos" : "text-neg")}>
              {netPos ? "+" : ""}
              {fmt(d.net_usd)}
            </span>
          </div>
        </div>
        <p className="mt-1.5 text-xs leading-relaxed text-fg">
          {flipped
            ? `Vor Kosten ~${grossPos ? "+" : ""}${fmt(d.gross_usd)}, nach Fees ${fmt(d.net_usd)} — die Round-Trip-Gebühren kehren ein knappes Plus in einen Verlust um.`
            : grossPos
              ? `Auch nach Fees positiv (${fmt(d.net_usd)}).`
              : `Schon vor Kosten negativ (${fmt(d.gross_usd)}) — die Fees vertiefen den Verlust auf ${fmt(d.net_usd)}.`}
        </p>
      </div>

      {/* Kennzahlen */}
      <div className="mb-3 grid grid-cols-3 gap-3">
        <Metric label="Fee-Drag" value={dragLabel} tone="text-warn" />
        <Metric
          label="Fees / Handelstag"
          value={fmt(d.fee_spend_per_trading_day)}
          tone="text-neg"
        />
        <Metric label="Trades / Tag" value={d.trades_per_trading_day.toFixed(1)} />
      </div>

      {/* Fees/Handelstag-Trend: Varianz auf einen Blick. */}
      <div className="mb-3">
        <div className="mb-1 flex items-center justify-between text-3xs uppercase tracking-wider text-fg-subtle">
          <span>Fees je Handelstag (Trend)</span>
          <span>Ø {fmt(d.fee_spend_per_trading_day)}</span>
        </div>
        <div className="space-y-1">
          {d.per_day.slice(-14).map((p) => (
            <div key={p.date} className="flex items-center gap-2">
              <span className="w-10 shrink-0 font-mono text-3xs text-fg-subtle">
                {fmtDay(p.date)}
              </span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-bg-3">
                <div
                  className="h-full rounded-full bg-warn/70"
                  style={{ width: `${(p.fee_spend_usd / maxDayFee) * 100}%` }}
                />
              </div>
              <span className="w-16 shrink-0 text-right font-mono text-3xs tabular-nums text-fg-subtle">
                {fmt(p.fee_spend_usd)}
              </span>
              <span className="w-7 shrink-0 text-right font-mono text-3xs tabular-nums text-fg-muted">
                {p.realizations}×
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Kontext */}
      <div className="space-y-1 border-t border-line-subtle pt-3 text-2xs text-fg-subtle">
        <div>
          {d.realization_count} Realisierungen (finale {d.final_close_count} + partielle{" "}
          {d.partial_count}; {d.excluded_count} quarantäniert) über {d.trading_days} Handelstage.
          Haltedauer median {fmtMin(d.hold_minutes_median)} · &lt;1h{" "}
          {d.hold_under_1h_pct?.toFixed(0) ?? "—"}%.
        </div>
        {d.by_reason.length > 0 && (
          <div className="flex flex-wrap gap-x-3 gap-y-0.5">
            {d.by_reason.map((r) => (
              <span key={r.reason} className="font-mono">
                {r.reason}: {r.count}×{" "}
                <span className={r.net_usd >= 0 ? "text-pos" : "text-neg"}>
                  {r.net_usd >= 0 ? "+" : ""}
                  {fmt(r.net_usd)}
                </span>
              </span>
            ))}
          </div>
        )}
        <div className="text-fg-muted">
          Fenster: {fmtDay(d.window_start)}–{fmtDay(d.window_end)} · echte Audit-Fees inkl. Partials
        </div>
      </div>

      {/* Info-Feld */}
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
              <span className="text-fg">Brutto vor Fees</span> — die reine Preis-Bewegung aller
              abgeschlossenen Round-Trips, bevor Gebühren abgezogen werden.
            </p>
            <p>
              <span className="text-fg">Netto nach Fees</span> — dasselbe nach Abzug der
              tatsächlichen Open- + Close-Gebühren. Die Differenz IST die Fee-Last.
            </p>
            <p>
              <span className="text-fg">Fee-Drag</span> — wie viel die Gebühren im Verhältnis zur
              Brutto-Bewegung ausmachen. „1,0×" = Fees so groß wie die Bewegung; höher = die
              Bewegung wird von Kosten aufgefressen.
            </p>
            <p>
              <span className="text-fg">Im Paper sind die Fees simuliert.</span> Der Wert dieser
              Spur ist die Fee-Effizienz als Voraussetzung für echtes Kapital (Gate G2) — NICHT ein
              Grund, weniger zu handeln (der Engpass ist zu wenig Evidenz, nicht zu viel Churn).
            </p>
            <p>
              Eine Mindesthaltedauer wäre hier kontraproduktiv: die kurzen Trades sind Stop-Outs,
              die auch bei längerem Halten weiter ins Minus laufen (datenbelegt 2026-06-25).
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}
