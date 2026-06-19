// @data-source: /operator/portfolio-snapshot · /operator/exposure-summary · /operator/trading-loop/recent-cycles
import type { ReactNode } from "react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import {
  fetchPortfolioSnapshot,
  fetchExposureSummary,
  fetchRecentCycles,
  type PaperPosition,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useCurrency } from "@/state/CurrencyProvider";

// Quick-Win-Tiles: ersetzen die früheren PreparedPanel-Stubs (Portfolio Snapshot,
// Risk Meter, Allocation, Recent Cycles) durch ECHTE Daten aus bereits live
// laufenden Read-Endpoints. Alles read-only, Paper-Mode, kein neuer Endpoint,
// keine Chart-Library. Ehrliche Loading-/Empty-/Error-Zustände statt Fake-%.
// Geld-Formatierung über die kanonische SSOT (useCurrency().fmt) — EUR/USD-Toggle.

function TileShell({
  title,
  right,
  children,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card padded className="overflow-hidden">
      <CardHeader title={title} right={right} />
      <div className="text-xs text-fg-muted">{children}</div>
    </Card>
  );
}

function StateLine({
  state,
  error,
  emptyLabel,
  emptyHint,
}: {
  state: "loading" | "ready" | "error";
  error?: { kind: string; message: string } | null;
  emptyLabel?: string;
  emptyHint?: string;
}) {
  if (state === "loading") {
    return <p className="text-fg-subtle">lädt …</p>;
  }
  return (
    <div className="space-y-1">
      <p className="text-neg font-semibold">{emptyLabel ?? "Backend nicht erreichbar"}</p>
      {error && (
        <p className="text-2xs text-fg-subtle break-words">
          {error.kind} · {error.message}
        </p>
      )}
      {emptyHint && <p className="text-2xs text-fg-subtle">{emptyHint}</p>}
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-fg-subtle">{label}</span>
      <span
        className={cn(
          "font-mono tabular-nums",
          tone === "pos" && "text-pos",
          tone === "neg" && "text-neg",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function PortfolioTile() {
  const { fmt } = useCurrency();
  const q = useApi(fetchPortfolioSnapshot, 30_000);
  return (
    <TileShell
      title="Portfolio Snapshot"
      right={
        <Badge tone="info" dot>
          Paper-Mode
        </Badge>
      }
    >
      {q.state !== "ready" ? (
        <StateLine
          state={q.state}
          error={q.error}
          emptyLabel="Portfolio-Backend nicht erreichbar"
        />
      ) : (
        <div>
          <Metric label="Equity" value={fmt(q.data.total_equity_usd)} />
          <Metric label="Cash" value={fmt(q.data.cash_usd)} />
          <Metric
            label="Realized PnL"
            value={fmt(q.data.realized_pnl_usd)}
            tone={q.data.realized_pnl_usd >= 0 ? "pos" : "neg"}
          />
          <Metric label="Offene Positionen" value={String(q.data.position_count)} />
          <p className="text-2xs text-fg-subtle mt-1">Live aus /operator/portfolio-snapshot</p>
        </div>
      )}
    </TileShell>
  );
}

function RiskMeterTile() {
  const { fmt } = useCurrency();
  const q = useApi(fetchExposureSummary, 30_000);
  const data = q.state === "ready" ? q.data : null;
  const biasPct =
    data && data.gross_exposure_usd > 0
      ? Math.abs(data.net_exposure_usd / data.gross_exposure_usd) * 100
      : 0;
  return (
    <TileShell
      title="Risk Meter"
      right={
        data ? (
          <Badge tone={data.mark_to_market_status === "ok" ? "pos" : "warn"} dot>
            {data.mark_to_market_status === "ok" ? "frisch" : data.mark_to_market_status}
          </Badge>
        ) : undefined
      }
    >
      {!data ? (
        <StateLine state={q.state} error={q.error} emptyLabel="Risk-Backend nicht erreichbar" />
      ) : data.gross_exposure_usd <= 0 ? (
        data.unavailable_price_count > 0 ? (
          <p className="text-warn">
            {data.unavailable_price_count} Position(en) im Buch, aber ohne Live-Kurs — nicht
            mark-to-market bewertbar (Einstandswert im Allocation-Tile).
          </p>
        ) : (
          <p className="text-fg-subtle">Kein Markteinsatz — Buch ist flach (Paper-Mode).</p>
        )
      ) : (
        <div>
          <Metric label="Gross Exposure" value={fmt(data.gross_exposure_usd)} />
          <Metric label="Net Exposure" value={fmt(data.net_exposure_usd)} />
          <Metric
            label="Directional Bias"
            value={`${biasPct.toFixed(0)}% ${data.net_exposure_usd >= 0 ? "long" : "short"}`}
          />
          {data.largest_position_symbol && (
            <Metric
              label="Größte Position"
              value={`${data.largest_position_symbol} (${(data.largest_position_weight_pct ?? 0).toFixed(0)}%)`}
            />
          )}
          <p className="text-2xs text-fg-subtle mt-1">Live aus /operator/exposure-summary</p>
        </div>
      )}
    </TileShell>
  );
}

export type AllocationItem = { symbol: string; value: number; pct: number; marked: boolean };

// Pure: absolute value per position, share of total, sorted desc.
// Uses mark-to-market (market_value_usd) when the price provider covers the
// symbol; otherwise falls back to the backend's entry-basis display_value_usd
// (e.g. Microcaps wie ZEREBRO, die CoinGecko nicht kennt) statt die Position
// stillschweigend zu verstecken. `marked=false` markiert Einstands-Basis (nicht
// mark-to-market), damit das UI es ehrlich beschriften kann. Positionen ohne
// jeden Wert (beide null) fallen raus. Total 0 -> empty (caller shows empty).
export function computeAllocation(positions: PaperPosition[]): {
  items: AllocationItem[];
  total: number;
} {
  const valued = positions
    .map((p) => {
      const marked = p.market_value_usd != null;
      const raw = marked ? p.market_value_usd : p.display_value_usd ?? null;
      return { symbol: p.symbol, value: Math.abs(raw ?? 0), marked };
    })
    .filter((p) => p.value > 0)
    .sort((a, b) => b.value - a.value);
  const total = valued.reduce((s, p) => s + p.value, 0);
  const items: AllocationItem[] =
    total > 0 ? valued.map((p) => ({ ...p, pct: (p.value / total) * 100 })) : [];
  return { items, total };
}

function AllocationTile() {
  const q = useApi(fetchPortfolioSnapshot, 30_000);
  const positions: PaperPosition[] = q.state === "ready" ? q.data.positions : [];
  const { items: priced, total } = computeAllocation(positions);
  return (
    <TileShell title="Allocation" right={<Badge tone="info" dot>Paper-Mode</Badge>}>
      {q.state !== "ready" ? (
        <StateLine state={q.state} error={q.error} emptyLabel="Allocation-Backend nicht erreichbar" />
      ) : priced.length === 0 || total <= 0 ? (
        <p className="text-fg-subtle">Keine bewertbaren Positionen (Paper-Buch leer/ohne Preis).</p>
      ) : (
        <div className="space-y-1.5">
          {priced.slice(0, 8).map((p) => (
            <div key={p.symbol}>
              <div className="flex justify-between text-2xs">
                <span className="font-mono">
                  {p.symbol}
                  {!p.marked && <span className="ml-1 text-warn">· Einstand</span>}
                </span>
                <span className="text-fg-subtle tabular-nums">{p.pct.toFixed(1)}%</span>
              </div>
              <div className="h-1.5 w-full rounded-full bg-bg-3 overflow-hidden">
                <div
                  className={cn("h-full rounded-full", p.marked ? "bg-ai" : "bg-warn")}
                  style={{ width: `${p.pct}%` }}
                />
              </div>
            </div>
          ))}
          {priced.some((p) => !p.marked) && (
            <p className="text-2xs text-warn">· Einstand = ohne Live-Kurs, Einstandswert (nicht M2M)</p>
          )}
          <p className="text-2xs text-fg-subtle">Aus /operator/portfolio-snapshot abgeleitet</p>
        </div>
      )}
    </TileShell>
  );
}

function RecentCyclesTile() {
  const q = useApi((s) => fetchRecentCycles(20, s), 30_000);
  const data = q.state === "ready" ? q.data : null;
  return (
    <TileShell title="Recent Trading Cycles" right={<Badge tone="info" dot>Paper-Mode</Badge>}>
      {!data ? (
        <StateLine
          state={q.state}
          error={q.error}
          emptyLabel="Loop-Backend nicht erreichbar"
          emptyHint="Cloudflare/Server prüfen, dann erneut laden."
        />
      ) : data.total_cycles === 0 ? (
        <p className="text-fg-subtle">Keine Cycles im Fenster.</p>
      ) : (
        <div>
          <Metric label="Cycles (letzte 20)" value={String(data.total_cycles)} />
          {Object.entries(data.status_counts)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 5)
            .map(([k, v]) => (
              <Metric key={k} label={k} value={String(v)} />
            ))}
          <p className="text-2xs text-fg-subtle mt-1">
            Live aus /operator/trading-loop/recent-cycles
          </p>
        </div>
      )}
    </TileShell>
  );
}

export function LivePortfolioTiles() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      <PortfolioTile />
      <RiskMeterTile />
      <AllocationTile />
      <RecentCyclesTile />
    </div>
  );
}
