// @data-source: /operator/portfolio-snapshot · /operator/exposure-summary · /api/diversification/overview
//
// Executive Snapshot (UI-Update 2026.06, WP-1.2 / Konzept §4B). Die prominente
// Lageübersicht direkt unter dem Command Header: Equity/Cash/PnL, Klumpenrisiko
// als Gauge, Allocation als Donut, offene Positionen und Execution/Write-Back-
// Zustand auf einen Blick. Visuell-forward (Viz-Primitives WP-0.2), nur echte
// Daten — bei Lücke ehrlich degradiert, kein Fake.
import { Card, CardHeader } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { Gauge } from "@/components/viz/Gauge";
import { Donut } from "@/components/viz/Donut";
import { useApi } from "@/lib/useApi";
import { useSharedPortfolioSnapshot } from "@/state/PortfolioSnapshotProvider";
import { exposureFromSnapshot } from "@/lib/exposureFromSnapshot";
import { useCurrency } from "@/state/CurrencyProvider";
import {
  fetchDiversificationOverview,
} from "@/lib/api";
import { allocationDonutData, concentrationTone } from "@/lib/executiveSnapshot";
import { cn } from "@/lib/utils";

function Metric({
  label,
  value,
  tone,
  unavailable = false,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg";
  /** true = die Quelle antwortet nicht. Ein stummer Strich sieht aus wie "0" oder
   *  "nichts los"; fehlende Kapitalzahlen muessen als FEHLEND lesbar sein. */
  unavailable?: boolean;
}) {
  if (unavailable) {
    return (
      <div>
        <div className="text-2xs uppercase tracking-wider text-fg-subtle">{label}</div>
        <div className="text-fg-subtle font-mono text-lg font-semibold" title="Quelle nicht erreichbar">
          n/v
        </div>
      </div>
    );
  }
  return (
    <div>
      <div className="text-2xs uppercase tracking-wider text-fg-subtle">{label}</div>
      <div
        className={cn(
          "font-mono text-lg font-semibold",
          tone === "pos" ? "text-pos" : tone === "neg" ? "text-neg" : "text-fg",
        )}
      >
        {value}
      </div>
    </div>
  );
}

export function ExecutiveSnapshot() {
  const { fmt } = useCurrency();
  // 2026-09-09: /operator/exposure-summary war ein zweiter Round-Trip auf
  // dieselbe serverseitige Berechnung — build_exposure_summary
  // (portfolio_read.py:812) ist nur snapshot.exposure_summary.to_json_dict()
  // plus vier Felder, die der Snapshot mitliefert. Portfolio und Exposure sind
  // damit EINE Quelle, nicht zwei; entsprechend zaehlt `degraded` unten nur
  // noch zwei Quellen statt drei.
  const pf = useSharedPortfolioSnapshot();
  const dv = useApi(fetchDiversificationOverview, 60_000);

  const portfolio = pf.state === "ready" ? pf.data : null;
  const exposure = pf.state === "ready" ? exposureFromSnapshot(pf.data) : null;
  const diversification = dv.state === "ready" ? dv.data : null;

  const largestPct =
    exposure?.largest_position_weight_pct ??
    diversification?.concentration?.btc_eth_short_term_pct ??
    null;
  const donut = allocationDonutData(diversification?.asset_distribution);
  const openCount = portfolio?.position_count ?? null;

  // Je Quelle getrennt: welche fehlt, und laesst sie sich erneut holen?
  const degraded = [
    pf.state === "error" ? "Portfolio/Exposure" : null,
    dv.state === "error" ? "Allocation" : null,
  ].filter((x): x is string => x !== null);
  const allDown = pf.state === "error" && dv.state === "error";
  const reloadDegraded = () => {
    if (pf.state === "error") pf.reload();
    if (dv.state === "error") dv.reload();
  };

  return (
    <Card padded>
      <CardHeader
        title="Executive Snapshot"
        subtitle="Lage auf einen Blick — Kapital, Klumpenrisiko, Allocation, Ausführungs-Zustand."
      />

      {/* 2026-09-08: Vorher hing die einzige Fehlermeldung an
          `pf.state === "error" && pf.state === "error"` — BEIDE mussten fallen.
          Bei Teilausfall zeigte der wichtigste Block der Seite stumme Striche,
          was sich wie "keine Positionen" liest statt wie "nicht bestimmbar".
          Jetzt traegt der Block immer eine Aussage, und jede Zone kennt ihren
          eigenen Zustand. */}
      {degraded.length > 0 && (
        <div className="border-fg-subtle/25 text-fg-muted mb-3 flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs">
          <span className="text-fg font-semibold">Lage unvollständig</span>
          <span>
            {degraded.join(" · ")} {degraded.length === 1 ? "antwortet" : "antworten"} nicht — die
            fehlenden Felder stehen auf n/v, nicht auf null.
          </span>
          <button
            type="button"
            onClick={reloadDegraded}
            className="border-fg-subtle/30 text-fg-muted hover:text-fg hover:border-fg-subtle/60 ml-auto rounded-md border px-2 py-0.5 transition-colors"
          >
            Erneut laden
          </button>
        </div>
      )}

      {allDown ? (
        <div className="text-fg-muted py-3 text-xs">
          Keine der drei Snapshot-Quellen antwortet — die Lage ist derzeit nicht bestimmbar.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-12 md:gap-3">
          {/* Kapital-Zahlen */}
          <div className="grid grid-cols-2 gap-3 md:col-span-5">
            <Metric
              label="Equity"
              value={portfolio ? fmt(portfolio.total_equity_usd) : "—"}
              unavailable={pf.state === "error"}
            />
            <Metric
              label="Cash"
              value={portfolio ? fmt(portfolio.cash_usd) : "—"}
              unavailable={pf.state === "error"}
            />
            <Metric
              label="Realized PnL"
              value={portfolio ? fmt(portfolio.realized_pnl_usd) : "—"}
              tone={portfolio ? (portfolio.realized_pnl_usd >= 0 ? "pos" : "neg") : undefined}
              unavailable={pf.state === "error"}
            />
            <Metric
              label="Offene Positionen"
              value={openCount == null ? "—" : String(openCount)}
              unavailable={pf.state === "error"}
            />
            <div className="col-span-2 flex flex-wrap gap-1.5 pt-1">
              {exposure && (
                <>
                  <StatusPill
                    kind={exposure.execution_enabled ? "live" : "execution-off"}
                    label={exposure.execution_enabled ? "Execution an" : "Execution aus"}
                  />
                  <StatusPill
                    kind={exposure.write_back_allowed ? "operational" : "write-back-locked"}
                    label={exposure.write_back_allowed ? "Write-Back frei" : "Write-Back gesperrt"}
                  />
                </>
              )}
            </div>
          </div>

          {/* Klumpenrisiko-Gauge */}
          <div className="flex flex-col items-center justify-center md:col-span-3">
            <Gauge
              value={largestPct}
              min={0}
              max={100}
              tone={concentrationTone(largestPct)}
              label={largestPct == null ? "—" : `${largestPct.toFixed(0)}%`}
              className="h-16 w-32"
            />
            <div className="mt-1 text-2xs text-fg-subtle">
              Klumpenrisiko{exposure?.largest_position_symbol ? ` · ${exposure.largest_position_symbol}` : ""}
            </div>
          </div>

          {/* Allocation-Donut */}
          <div className="flex items-center gap-3 md:col-span-4">
            {donut.length > 0 ? (
              <>
                <Donut data={donut} centerLabel={openCount != null ? String(openCount) : undefined} className="h-20 w-20 shrink-0" />
                <ul className="min-w-0 flex-1 space-y-0.5 text-2xs">
                  {donut.map((d) => (
                    <li key={d.label} className="flex items-center justify-between gap-2">
                      <span className="truncate text-fg-muted">{d.label}</span>
                      <span className="font-mono text-fg-subtle">{d.value.toFixed(0)}%</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <div className="text-2xs text-fg-subtle">
                {openCount === 0 ? "Keine offenen Positionen — reines Cash." : "Keine Allocation-Daten."}
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
