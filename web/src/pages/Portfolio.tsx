import { AlertCircle, RefreshCw } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card, CardHeader } from "@/components/ui/Primitives";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import {
  fetchPortfolioSnapshot,
  fetchExposureSummary,
  type PaperPosition,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { PreparedPanel } from "@/components/panels/PreparedPanel";

const fmt$ = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `$${v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;

export function PortfolioPage() {
  const { t } = useT();
  const snap = useApi(fetchPortfolioSnapshot, 30_000);
  const exposure = useApi(fetchExposureSummary, 30_000);

  const positions: PaperPosition[] = snap.state === "ready" ? snap.data.positions : [];
  const unrealized = positions.reduce((sum, p) => sum + (p.unrealized_pnl_usd ?? 0), 0);

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.portfolio.title")}
        sub={
          snap.state === "ready"
            ? `Paper Portfolio · ${snap.data.position_count} Positionen · Quelle: ${snap.data.source}`
            : "Paper Portfolio Snapshot"
        }
        right={
          <Button
            onClick={() => { snap.reload(); exposure.reload(); }}
            variant="outline"
            size="sm"
          >
            <RefreshCw size={12} /> Aktualisieren
          </Button>
        }
      />

      {snap.state === "error" && <ErrorCard kind={snap.error.kind} message={snap.error.message} path="/operator/portfolio-snapshot" />}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi label="Total Equity" value={fmt$(snap.state === "ready" ? snap.data.total_equity_usd : null)} tone="pos" />
        <Kpi label="Cash" value={fmt$(snap.state === "ready" ? snap.data.cash_usd : null)} />
        <Kpi label="Market Value" value={fmt$(snap.state === "ready" ? snap.data.total_market_value_usd : null)} />
        <Kpi
          label="Unrealized PnL"
          value={fmt$(snap.state === "ready" ? unrealized : null)}
          tone={unrealized > 0 ? "pos" : unrealized < 0 ? "neg" : "neutral"}
        />
      </div>

      {exposure.state === "ready" && (
        <Card padded>
          <CardHeader
            title="Exposure"
            right={
              <Badge tone={exposure.data.mark_to_market_status === "ok" ? "pos" : "warn"} dot>
                mtm {exposure.data.mark_to_market_status}
              </Badge>
            }
          />
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs">
            <RowKV k="gross_exposure" v={fmt$(exposure.data.gross_exposure_usd)} />
            <RowKV k="net_exposure" v={fmt$(exposure.data.net_exposure_usd)} />
            <RowKV k="priced_positions" v={String(exposure.data.priced_position_count)} />
            <RowKV k="stale_positions" v={String(exposure.data.stale_position_count)} tone={exposure.data.stale_position_count > 0 ? "warn" : undefined} />
            <RowKV k="unavailable_price" v={String(exposure.data.unavailable_price_count)} tone={exposure.data.unavailable_price_count > 0 ? "warn" : undefined} />
            <RowKV
              k="largest"
              v={
                exposure.data.largest_position_symbol
                  ? `${exposure.data.largest_position_symbol} (${exposure.data.largest_position_weight_pct?.toFixed(1)}%)`
                  : "—"
              }
            />
          </div>
        </Card>
      )}
      {exposure.state === "error" && <ErrorCard kind={exposure.error.kind} message={exposure.error.message} path="/operator/exposure-summary" />}

      <Card padded={false}>
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-line-subtle">
          <div className="text-sm font-semibold tracking-tight text-fg">Offene Positionen</div>
          <div className="text-2xs text-fg-subtle font-mono">
            {snap.state === "ready" ? `${snap.data.position_count} Positionen` : ""}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-fg-subtle text-2xs uppercase tracking-wider">
                <th className="text-left font-semibold px-4 py-2">Symbol</th>
                <th className="text-right font-semibold px-4 py-2">Qty</th>
                <th className="text-right font-semibold px-4 py-2">Entry</th>
                <th className="text-right font-semibold px-4 py-2">Market</th>
                <th className="text-right font-semibold px-4 py-2">Value</th>
                <th className="text-right font-semibold px-4 py-2">Unrealized</th>
                <th className="text-right font-semibold px-4 py-2">SL</th>
                <th className="text-right font-semibold px-4 py-2">TP</th>
              </tr>
            </thead>
            <tbody>
              {snap.state === "loading" && (
                <tr><td colSpan={8} className="px-4 py-6 text-center text-fg-subtle">{t("common.loading")}</td></tr>
              )}
              {snap.state === "ready" && positions.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-6 text-center text-fg-subtle">{t("common.no_data")}</td></tr>
              )}
              {positions.map((p) => (
                <tr key={p.symbol} className="border-t border-line-subtle hover:bg-bg-2">
                  <td className="px-4 py-2 font-mono font-semibold">{p.symbol}</td>
                  <td className="px-4 py-2 text-right font-mono">{p.quantity.toFixed(6)}</td>
                  <td className="px-4 py-2 text-right font-mono">{fmt$(p.avg_entry_price)}</td>
                  <td className="px-4 py-2 text-right font-mono">{fmt$(p.market_price)}</td>
                  <td className="px-4 py-2 text-right font-mono">{fmt$(p.market_value_usd)}</td>
                  <td className={cn(
                    "px-4 py-2 text-right font-mono",
                    (p.unrealized_pnl_usd ?? 0) > 0 && "text-pos",
                    (p.unrealized_pnl_usd ?? 0) < 0 && "text-neg",
                  )}>
                    {fmt$(p.unrealized_pnl_usd)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-fg-subtle">{fmt$(p.stop_loss)}</td>
                  <td className="px-4 py-2 text-right font-mono text-fg-subtle">{fmt$(p.take_profit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <PreparedPanel
        title="Equity-Kurve & historische PnL"
        reason="Equity-Verlauf, Drawdown-Kurve und Realized-PnL-Historie."
        detail="Quelle: artifacts/paper_execution_audit.jsonl — Aggregations-Endpoint folgt in Phase 2."
      />
    </div>
  );
}

function Kpi({ label, value, tone = "neutral" }: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "warn" | "info" | "neutral" | "muted";
}) {
  return (
    <Card padded>
      <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">{label}</div>
      <div className={cn(
        "mt-1 font-mono text-lg font-semibold",
        tone === "pos" && "text-pos",
        tone === "neg" && "text-neg",
        tone === "warn" && "text-warn",
        tone === "info" && "text-info",
        tone === "muted" && "text-fg-muted",
      )}>
        {value}
      </div>
    </Card>
  );
}

function RowKV({ k, v, tone }: { k: string; v: string; tone?: "pos" | "neg" | "warn" | "muted" }) {
  return (
    <div className="flex items-center justify-between gap-2 overflow-hidden border-b border-line-subtle/50 py-1">
      <span className="min-w-0 truncate font-mono text-2xs text-fg-subtle">{k}</span>
      <span className={cn(
        "shrink-0 font-mono text-right",
        tone === "pos" && "text-pos",
        tone === "neg" && "text-neg",
        tone === "warn" && "text-warn",
        tone === "muted" && "text-fg-muted",
      )}>{v}</span>
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
