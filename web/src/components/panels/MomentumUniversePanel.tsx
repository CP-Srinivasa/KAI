import { useState } from "react";
import { HelpCircle, ChevronDown } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import { fetchMomentumUniverse } from "@/lib/api";
import { cn } from "@/lib/utils";

// @data-source: /dashboard/api/momentum-universe

/**
 * Momentum-Universe-Panel (G0). Zeigt das aus EIGENEN Börsendaten gebildete
 * Universe der meist-gehandelten × best-performenden Coins (Volumen-Percentile ×
 * Mehrfenster-Return-Percentile → robuster Universe-Score). READ-ONLY "Sicht":
 * beeinflusst noch KEIN Sizing/Kapital — Rotation (G1) + Edge-Messung/Promote
 * (G2/G5) folgen. Kein TradingView-Scraping (ToS): die Rangliste rechnen wir selbst.
 */

function fmtPct(x: number): string {
  return `${Math.round(Math.max(0, Math.min(1, x)) * 100)}%`;
}

function fmtTs(s: string | undefined): string {
  if (!s) return "—";
  const dt = new Date(s);
  return Number.isNaN(dt.getTime())
    ? "—"
    : dt.toLocaleString("de-DE", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
}

export function MomentumUniversePanel() {
  const [showInfo, setShowInfo] = useState(false);
  const data = useApi((signal) => fetchMomentumUniverse(signal), 60_000, [], {
    maxAttempts: 2,
    baseMs: 1500,
  });

  const header = (
    <CardHeader
      title="Momentum-Universe"
      subtitle="Meist-gehandelt × Best-Performer aus eigenen Börsendaten — reine Sicht, kein Handel"
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
  if (!d.available || !d.universe || d.universe.length === 0) {
    return (
      <Card padded>
        {header}
        <div className="py-6 text-center text-sm text-fg-subtle">
          Noch kein Universe-Snapshot ({d.reason ?? d.error ?? "leer"}) —{" "}
          <span className="font-mono">trading-bot momentum-universe build</span> erzeugt einen.
        </div>
      </Card>
    );
  }

  const rows = d.universe;
  return (
    <Card padded>
      {header}

      <div className="mb-2 flex items-center justify-between text-2xs text-fg-subtle">
        <span>{d.count ?? rows.length} Coins</span>
        <span>Stand {fmtTs(d.ts)}</span>
      </div>

      <div className="space-y-1">
        {rows.map((r) => (
          <div key={r.symbol} className="flex items-center gap-2">
            <span className="w-5 shrink-0 text-right font-mono text-2xs text-fg-subtle">
              {r.rank}
            </span>
            <span className="w-24 shrink-0 truncate font-mono text-xs text-fg">{r.symbol}</span>
            <div
              className="h-2 flex-1 overflow-hidden rounded-full bg-bg-3"
              title={`Universe-Score ${fmtPct(r.universe_score)}`}
            >
              <div
                className="h-full rounded-full bg-accent/70"
                style={{ width: `${Math.max(0, Math.min(1, r.universe_score)) * 100}%` }}
              />
            </div>
            <span
              className="w-11 shrink-0 text-right font-mono text-2xs tabular-nums text-fg-muted"
              title="Volumen-Percentile (most traded)"
            >
              V {fmtPct(r.volume_score)}
            </span>
            <span
              className="w-11 shrink-0 text-right font-mono text-2xs tabular-nums text-fg-muted"
              title="Momentum-Percentile (best performer)"
            >
              M {fmtPct(r.momentum_score)}
            </span>
          </div>
        ))}
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
          <ChevronDown
            className={cn("h-3 w-3 transition-transform", showInfo && "rotate-180")}
            aria-hidden
          />
        </button>
        {showInfo && (
          <div className="mt-2 space-y-1.5 text-2xs leading-relaxed text-fg-subtle">
            <p>
              <span className="text-fg">V (Volumen)</span> — Percentile-Rang des 24h-Handelsvolumens
              (most traded). <span className="text-fg">M (Momentum)</span> — Percentile-Rang der
              Mehrfenster-Returns (24h/7d/30d, best performer).
            </p>
            <p>
              <span className="text-fg">Universe-Score</span> — gewichtete, robust normalisierte
              Mischung beider; ein einzelner Ausreißer dominiert die Rangliste nicht.
            </p>
            <p>
              Aus <span className="text-fg">eigenen</span> Börsendaten gerechnet — kein
              TradingView-Scraping (ToS). READ-ONLY Sicht: beeinflusst noch KEIN Sizing/Kapital;
              Rotation (G1) und kosten-netto Edge-Messung/Promote (G2/G5) folgen.
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}
