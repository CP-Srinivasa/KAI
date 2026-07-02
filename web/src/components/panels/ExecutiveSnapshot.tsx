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
import { useCurrency } from "@/state/CurrencyProvider";
import {
  fetchPortfolioSnapshot,
  fetchExposureSummary,
  fetchDiversificationOverview,
} from "@/lib/api";
import { allocationDonutData, concentrationTone } from "@/lib/executiveSnapshot";
import { cn } from "@/lib/utils";

function Metric({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
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
  const pf = useApi(fetchPortfolioSnapshot, 30_000);
  const ex = useApi(fetchExposureSummary, 30_000);
  const dv = useApi(fetchDiversificationOverview, 60_000);

  const portfolio = pf.state === "ready" ? pf.data : null;
  const exposure = ex.state === "ready" ? ex.data : null;
  const diversification = dv.state === "ready" ? dv.data : null;

  const largestPct =
    exposure?.largest_position_weight_pct ??
    diversification?.concentration?.btc_eth_short_term_pct ??
    null;
  const donut = allocationDonutData(diversification?.asset_distribution);
  const openCount = portfolio?.position_count ?? null;

  return (
    <Card padded>
      <CardHeader
        title="Executive Snapshot"
        subtitle="Lage auf einen Blick — Kapital, Klumpenrisiko, Allocation, Ausführungs-Zustand."
      />

      {pf.state === "error" && ex.state === "error" ? (
        <div className="py-3 text-xs text-neg">
          Snapshot-Endpoints unerreichbar — Lage nicht bestimmbar.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-12 md:gap-3">
          {/* Kapital-Zahlen */}
          <div className="grid grid-cols-2 gap-3 md:col-span-5">
            <Metric label="Equity" value={portfolio ? fmt(portfolio.total_equity_usd) : "—"} />
            <Metric label="Cash" value={portfolio ? fmt(portfolio.cash_usd) : "—"} />
            <Metric
              label="Realized PnL"
              value={portfolio ? fmt(portfolio.realized_pnl_usd) : "—"}
              tone={portfolio ? (portfolio.realized_pnl_usd >= 0 ? "pos" : "neg") : undefined}
            />
            <Metric
              label="Offene Positionen"
              value={openCount == null ? "—" : String(openCount)}
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
