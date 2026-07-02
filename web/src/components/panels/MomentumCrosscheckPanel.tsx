import { useState } from "react";
import { HelpCircle, ChevronDown } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import { fetchMomentumCrosscheck } from "@/lib/api";
import { cn } from "@/lib/utils";

// @data-source: /dashboard/api/momentum-crosscheck

/**
 * Momentum-Crosscheck-Panel (G4). Stellt den eigenen Universe-Momentum-Rang
 * (best performer) dem eigenen TA-Rating gegenüber (ToS-konformer TradingView-
 * Ratings-Ersatz, aus EIGENEM OHLCV gerechnet — kein Scraping). Macht
 * Übereinstimmung vs. Divergenz sichtbar: ein hoher Momentum-Performer, den die
 * TA als „sell" liest, ist ein Mean-Reversion-Risiko. READ-ONLY, kein Sizing.
 */

const AGREEMENT_META: Record<string, { label: string; tone: string }> = {
  agree_bullish: { label: "Einig bullish", tone: "text-pos" },
  agree_bearish: { label: "Einig bearish", tone: "text-neg" },
  divergence: { label: "Divergenz", tone: "text-warn" },
  ta_only_bullish: { label: "Nur TA bullish", tone: "text-info" },
  neutral: { label: "Neutral", tone: "text-fg-subtle" },
  unavailable: { label: "Keine TA", tone: "text-fg-subtle" },
};

function fmtTs(s: string | undefined): string {
  if (!s) return "—";
  const dt = new Date(s);
  return Number.isNaN(dt.getTime())
    ? "—"
    : dt.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function MomentumCrosscheckPanel() {
  const [showInfo, setShowInfo] = useState(false);
  const data = useApi((signal) => fetchMomentumCrosscheck(signal), 60_000, [], {
    maxAttempts: 2,
    baseMs: 1500,
  });

  const header = (
    <CardHeader
      title="Momentum-Crosscheck"
      subtitle="Eigener Momentum-Rang vs. eigenes TA-Rating — Übereinstimmung & Divergenz, reine Sicht"
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
  if (!d.available || !d.rows || d.rows.length === 0) {
    return (
      <Card padded>
        {header}
        <div className="py-6 text-center text-sm text-fg-subtle">
          Noch kein Cross-Check-Snapshot ({d.reason ?? d.error ?? "leer"}) —{" "}
          <span className="font-mono">trading-bot momentum-universe crosscheck</span> erzeugt einen.
        </div>
      </Card>
    );
  }

  const rows = d.rows;
  const divergences = rows.filter((r) => r.agreement === "divergence").length;

  return (
    <Card padded>
      {header}

      <div className="mb-2 flex items-center justify-between text-2xs text-fg-subtle">
        <span>
          {d.count ?? rows.length} Coins · {divergences} Divergenz{divergences === 1 ? "" : "en"}
        </span>
        <span>Stand {fmtTs(d.ts)}</span>
      </div>

      <div className="space-y-1">
        {rows.map((r) => {
          const meta = AGREEMENT_META[r.agreement] ?? AGREEMENT_META.neutral;
          return (
            <div key={r.symbol} className="flex items-center gap-2">
              <span className="w-5 shrink-0 text-right font-mono text-2xs text-fg-subtle">{r.rank}</span>
              <span className="w-24 shrink-0 truncate font-mono text-xs text-fg">{r.symbol}</span>
              <span
                className="w-12 shrink-0 text-right font-mono text-2xs tabular-nums text-fg-muted"
                title="Momentum-Percentile (best performer)"
              >
                M {Math.round(Math.max(0, Math.min(1, r.momentum_score)) * 100)}%
              </span>
              <span
                className={cn("w-20 shrink-0 text-right font-mono text-2xs", meta.tone)}
                title={`TA-Rating (eigenes OHLCV) · RSI ${r.rsi ?? "—"}`}
              >
                {r.ta_label}
              </span>
              <span
                className={cn(
                  "w-16 shrink-0 text-right font-mono text-2xs tabular-nums",
                  r.funding_signal === "long_crowded"
                    ? "text-warn"
                    : r.funding_signal === "short_crowded"
                      ? "text-info"
                      : "text-fg-subtle",
                )}
                title={`8h-Funding-Rate · ${r.funding_signal}`}
              >
                {r.funding_bps == null ? "F —" : `F ${r.funding_bps > 0 ? "+" : ""}${r.funding_bps.toFixed(1)}`}
              </span>
              <span
                className={cn(
                  "w-14 shrink-0 text-right font-mono text-2xs tabular-nums",
                  r.vol_regime === "high_vol"
                    ? "text-warn"
                    : r.vol_regime === "low_vol"
                      ? "text-pos"
                      : "text-fg-subtle",
                )}
                title={`Tages-Volatilität (ATR%) · Regime: ${r.vol_regime}`}
              >
                {r.atr_pct == null ? "V —" : `V ${r.atr_pct.toFixed(1)}%`}
              </span>
              <span className={cn("flex-1 truncate text-right text-2xs font-medium", meta.tone)}>
                {meta.label}
              </span>
            </div>
          );
        })}
      </div>

      <div className="mt-3 border-t border-line-subtle pt-3">
        <button
          type="button"
          onClick={() => setShowInfo((s) => !s)}
          aria-expanded={showInfo}
          className="inline-flex items-center gap-1.5 text-2xs text-fg-subtle hover:text-fg"
        >
          <HelpCircle className="h-3 w-3" aria-hidden />
          Was bedeutet das?
          <ChevronDown className={cn("h-3 w-3 transition-transform", showInfo && "rotate-180")} aria-hidden />
        </button>
        {showInfo && (
          <div className="mt-2 space-y-1.5 text-2xs leading-relaxed text-fg-subtle">
            <p>
              <span className="text-fg">M</span> = Momentum-Percentile (best performer aus eigenem
              Volumen/Return). <span className="text-fg">TA-Rating</span> = eigenes technisches
              Rating (RSI + SMA-Cross) aus <span className="text-fg">eigenem</span> OHLCV — der
              ToS-konforme Ersatz für TradingView-Ratings (kein Scraping).
            </p>
            <p>
              <span className="text-warn">Divergenz</span> — hoher Momentum-Performer, den die TA als
              „sell" liest (Mean-Reversion-Risiko). <span className="text-pos">Einig bullish</span> —
              beide bestätigen.
            </p>
            <p>
              <span className="text-fg">F</span> = 8h-Funding-Rate (bps), keyless von der Börse.{" "}
              <span className="text-warn">long_crowded</span> (≥ +5 bps) = überhitzte Longs zahlen
              fürs Halten — dokumentierter Mean-Reversion-Druck; das verstärkt eine Divergenz.
            </p>
            <p>
              <span className="text-fg">V</span> = Tages-Volatilität (ATR%) aus eigenem OHLCV.{" "}
              <span className="text-warn">high_vol</span> (≥ 8%) = choppy → Momentum WENIGER
              vertrauenswürdig; <span className="text-pos">low_vol</span> (≤ 3%) = ruhig/trendig →
              vertrauenswürdiger (KAIs Edge ist regime-abhängig). Reine Sicht; beeinflusst KEIN
              Sizing/Kapital.
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}
