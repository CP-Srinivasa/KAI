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
import { toNum } from "@/lib/num";

// Quick-Win-Tiles: ersetzen die früheren PreparedPanel-Stubs (Portfolio Snapshot,
// Risk Meter, Allocation, Recent Cycles) durch ECHTE Daten aus bereits live
// laufenden Read-Endpoints. Alles read-only, Paper-Mode, kein neuer Endpoint,
// keine Chart-Library. Ehrliche Loading-/Empty-/Error-Zustände statt Fake-%.

export function fmtUsd(v: number | string | null | undefined): string {
  const n = toNum(v);
  if (n === null) return "—";
  return `${n >= 0 ? "" : "-"}$${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

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
          <Metric label="Equity" value={fmtUsd(q.data.total_equity_usd)} />
          <Metric label="Cash" value={fmtUsd(q.data.cash_usd)} />
          <Metric
            label="Realized PnL"
            value={fmtUsd(q.data.realized_pnl_usd)}
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
        <p className="text-fg-subtle">Kein Markteinsatz — Buch ist flach (Paper-Mode).</p>
      ) : (
        <div>
          <Metric label="Gross Exposure" value={fmtUsd(data.gross_exposure_usd)} />
          <Metric label="Net Exposure" value={fmtUsd(data.net_exposure_usd)} />
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

export type AllocationItem = { symbol: string; value: number; pct: number };

// Pure: absolute market value per position, share of total, sorted desc. Skips
// positions without a priced market value. Total 0 -> empty (caller shows empty).
export function computeAllocation(positions: PaperPosition[]): {
  items: AllocationItem[];
  total: number;
} {
  const priced = positions
    .map((p) => ({ symbol: p.symbol, value: Math.abs(p.market_value_usd ?? 0) }))
    .filter((p) => p.value > 0)
    .sort((a, b) => b.value - a.value);
  const total = priced.reduce((s, p) => s + p.value, 0);
  const items: AllocationItem[] =
    total > 0 ? priced.map((p) => ({ ...p, pct: (p.value / total) * 100 })) : [];
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
                <span className="font-mono">{p.symbol}</span>
                <span className="text-fg-subtle tabular-nums">{p.pct.toFixed(1)}%</span>
              </div>
              <div className="h-1.5 w-full rounded-full bg-bg-3 overflow-hidden">
                <div className="h-full rounded-full bg-ai" style={{ width: `${p.pct}%` }} />
              </div>
            </div>
          ))}
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
