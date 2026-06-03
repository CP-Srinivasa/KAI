import { useMemo } from "react";
import { Wallet, ShieldAlert, PieChart } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { PanelErrorBoundary } from "@/components/PanelErrorBoundary";
import { useApi } from "@/lib/useApi";
import { useCurrency } from "@/state/CurrencyProvider";
import { fetchPortfolioSnapshot, fetchExposureSummary, type PaperPosition } from "@/lib/api";
import { cn } from "@/lib/utils";

/* Dashboard Quick-Win-Tiles (Remediation-Sprint 2026-06-03, Goal §3).
   Drei kompakte Live-Kacheln auf bestehenden, stabilen Endpoints — keine neuen
   Backend-Routen, keine neuen npm-Deps:

   - Portfolio Snapshot → GET /operator/portfolio-snapshot
   - Risk Meter         → GET /operator/exposure-summary
   - Allocation         → abgeleitet aus portfolio-snapshot.positions

   Diese drei Module waren zuvor PreparedPanel-Stubs ("Dashboard-Roadmap"). Da
   die Endpoints existieren und Portfolio-/Risk-Page sie bereits konsumieren,
   ist das reine Frontend-Wiederverwendung. AI-Insights bleibt bewusst Stub —
   dafür existiert noch kein stabiler Insight-Endpoint. */

const ALLOC_PALETTE = ["bg-info", "bg-pos", "bg-ai", "bg-warn", "bg-neg", "bg-fg-subtle"] as const;
const ALLOC_TOP_N = 5;

type AllocSlice = { label: string; valueUsd: number; pct: number; colorClass: string };

function buildAllocation(positions: PaperPosition[]): { slices: AllocSlice[]; total: number } {
  const priced = positions
    .map((p) => ({ symbol: p.symbol, value: p.market_value_usd }))
    .filter((p): p is { symbol: string; value: number } => typeof p.value === "number" && p.value > 0)
    .sort((a, b) => b.value - a.value);
  const total = priced.reduce((acc, p) => acc + p.value, 0);
  if (total <= 0) return { slices: [], total: 0 };

  const top = priced.slice(0, ALLOC_TOP_N);
  const restValue = priced.slice(ALLOC_TOP_N).reduce((acc, p) => acc + p.value, 0);

  const slices: AllocSlice[] = top.map((p, i) => ({
    label: p.symbol,
    valueUsd: p.value,
    pct: (p.value / total) * 100,
    colorClass: ALLOC_PALETTE[i % ALLOC_PALETTE.length],
  }));
  if (restValue > 0) {
    slices.push({
      label: "Andere",
      valueUsd: restValue,
      pct: (restValue / total) * 100,
      colorClass: ALLOC_PALETTE[ALLOC_PALETTE.length - 1],
    });
  }
  return { slices, total };
}

function TileShell({
  title,
  subtitle,
  icon,
  right,
  children,
}: {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="h-full">
      <CardHeader
        title={
          <span className="inline-flex items-center gap-1.5">
            <span className="text-fg-subtle" aria-hidden>
              {icon}
            </span>
            {title}
          </span>
        }
        subtitle={subtitle}
        right={right}
      />
      {children}
    </Card>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "warn" | "muted";
}) {
  const toneClass =
    tone === "pos"
      ? "text-pos"
      : tone === "neg"
        ? "text-neg"
        : tone === "warn"
          ? "text-warn"
          : "text-fg";
  return (
    <div className="min-w-0">
      <div className="text-2xs uppercase tracking-wider text-fg-subtle">{label}</div>
      <div className={cn("font-mono text-sm truncate", toneClass)}>{value}</div>
    </div>
  );
}

function Loading() {
  return <div className="text-xs text-fg-subtle animate-pulse">lädt …</div>;
}

function ErrLine({ message }: { message: string }) {
  return <div className="text-xs text-neg break-words">Fehler: {message}</div>;
}

export function LivePortfolioTiles() {
  const snap = useApi(fetchPortfolioSnapshot, 30_000);
  const exposure = useApi(fetchExposureSummary, 30_000);
  const { fmt } = useCurrency();
  const fmt$ = (v: number | null | undefined, digits = 2) =>
    v == null ? "—" : fmt(v, undefined, digits);

  const alloc = useMemo(
    () => (snap.state === "ready" ? buildAllocation(snap.data.positions) : { slices: [], total: 0 }),
    [snap],
  );

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {/* Portfolio Snapshot */}
      <PanelErrorBoundary name="Portfolio Snapshot">
        <TileShell
          title="Portfolio Snapshot"
          subtitle="Live aus /operator/portfolio-snapshot"
          icon={<Wallet size={14} />}
          right={
            snap.state === "ready" ? (
              <Badge tone="neutral" dot title={`Quelle: ${snap.data.source}`}>
                {snap.data.source}
              </Badge>
            ) : undefined
          }
        >
          {snap.state === "loading" && <Loading />}
          {snap.state === "error" && <ErrLine message={snap.error.message} />}
          {snap.state === "ready" && (
            <div className="space-y-3">
              <div>
                <div className="text-2xs uppercase tracking-wider text-fg-subtle">Equity</div>
                <div className="font-mono text-xl text-fg">{fmt$(snap.data.total_equity_usd)}</div>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <Metric label="Cash" value={fmt$(snap.data.cash_usd)} tone="muted" />
                <Metric
                  label="Realized PnL"
                  value={fmt$(snap.data.realized_pnl_usd)}
                  tone={snap.data.realized_pnl_usd >= 0 ? "pos" : "neg"}
                />
                <Metric label="Positionen" value={String(snap.data.position_count)} tone="muted" />
              </div>
            </div>
          )}
        </TileShell>
      </PanelErrorBoundary>

      {/* Risk Meter */}
      <PanelErrorBoundary name="Risk Meter">
        <TileShell
          title="Risk Meter"
          subtitle="Live aus /operator/exposure-summary"
          icon={<ShieldAlert size={14} />}
          right={
            exposure.state === "ready" ? (
              <Badge tone={exposure.data.available ? "info" : "warn"} dot>
                {exposure.data.mark_to_market_status}
              </Badge>
            ) : undefined
          }
        >
          {exposure.state === "loading" && <Loading />}
          {exposure.state === "error" && <ErrLine message={exposure.error.message} />}
          {exposure.state === "ready" && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <Metric label="Gross Exposure" value={fmt$(exposure.data.gross_exposure_usd)} />
                <Metric label="Net Exposure" value={fmt$(exposure.data.net_exposure_usd)} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Metric
                  label="Größte Position"
                  value={
                    exposure.data.largest_position_symbol
                      ? `${exposure.data.largest_position_symbol} ${
                          exposure.data.largest_position_weight_pct != null
                            ? `${exposure.data.largest_position_weight_pct.toFixed(1)}%`
                            : ""
                        }`.trim()
                      : "—"
                  }
                  tone={
                    (exposure.data.largest_position_weight_pct ?? 0) >= 40 ? "warn" : "muted"
                  }
                />
                <Metric
                  label="Stale / kein Preis"
                  value={`${exposure.data.stale_position_count} / ${exposure.data.unavailable_price_count}`}
                  tone={
                    exposure.data.stale_position_count + exposure.data.unavailable_price_count > 0
                      ? "warn"
                      : "muted"
                  }
                />
              </div>
            </div>
          )}
        </TileShell>
      </PanelErrorBoundary>

      {/* Allocation */}
      <PanelErrorBoundary name="Allocation">
        <TileShell
          title="Allocation"
          subtitle="Marktwert-Anteile je Asset"
          icon={<PieChart size={14} />}
        >
          {snap.state === "loading" && <Loading />}
          {snap.state === "error" && <ErrLine message={snap.error.message} />}
          {snap.state === "ready" && alloc.slices.length === 0 && (
            <div className="text-xs text-fg-subtle">Keine bepreisten Positionen.</div>
          )}
          {snap.state === "ready" && alloc.slices.length > 0 && (
            <div className="space-y-3">
              <div className="flex h-3 w-full overflow-hidden rounded-full bg-bg-3" role="img" aria-label="Asset-Allokation">
                {alloc.slices.map((s) => (
                  <div
                    key={s.label}
                    className={cn("h-full", s.colorClass)}
                    style={{ width: `${s.pct}%` }}
                    title={`${s.label}: ${s.pct.toFixed(1)}%`}
                  />
                ))}
              </div>
              <ul className="space-y-1">
                {alloc.slices.map((s) => (
                  <li key={s.label} className="flex items-center justify-between gap-2 text-xs">
                    <span className="inline-flex items-center gap-1.5 min-w-0">
                      <span className={cn("inline-block h-2 w-2 rounded-full shrink-0", s.colorClass)} aria-hidden />
                      <span className="truncate text-fg-muted">{s.label}</span>
                    </span>
                    <span className="font-mono text-fg-subtle shrink-0">{s.pct.toFixed(1)}%</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </TileShell>
      </PanelErrorBoundary>
    </div>
  );
}
