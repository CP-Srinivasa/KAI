// @data-source: /dashboard/api/chain (live) · /dashboard/api/markets/derivatives (live) · /dashboard/api/markets/sentiment (live) · /dashboard/api/markets/liquidations (live) · /dashboard/api/markets/liquidations-stream (live) · /dashboard/api/markets/momentum (live)
//
// Markt-Mikrostruktur für die Märkte-Seite — strikt das, was KAI WAHR weiß, alle
// aus freien/eigenen Quellen (No-Fake, kein erfundener Wert):
//   • On-Chain (Fee-Gauge, Mempool, Tip) aus der EIGENEN bitcoind.
//   • Perp-Funding + Open Interest aus KAIs EIGENER Ingestion (bybit-Snapshot).
//   • Sentiment aus dem freien Fear-&-Greed-Index (alternative.me, server-gecacht).
//   • Liquidationen (Long/Short-Pressure) aus OKX public liquidation-orders.
//   • Momentum (24h-Änderung) aus dem freien Binance-24h-Ticker.
import type { ReactNode } from "react";
import { Activity, TrendingUp, TrendingDown, Gauge as GaugeIcon, Flame } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { LiveDot } from "@/components/ui/LiveDot";
import { Gauge } from "@/components/viz/Gauge";
import {
  fetchChainStatus,
  fetchDerivatives,
  fetchSentiment,
  fetchLiquidations,
  fetchLiquidationsStream,
  fetchMomentum,
  type ChainStatus,
  type DerivativesSnapshot,
  type DerivativeRow,
  type SentimentSnapshot,
  type LiquidationsSnapshot,
  type LiquidationRow,
  type LiquidationStreamSnapshot,
  type MomentumSnapshot,
  type MomentumRow,
} from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import type { Tone } from "@/lib/tone";

const POLL_MS = 60_000;
const FEE_MAX = 50; // sat/vB — Gauge-Skala (>50 = Spitzenlast)

function feeTone(fee: number | null): Tone {
  if (fee == null) return "neutral";
  if (fee < 5) return "pos";
  if (fee < 20) return "info";
  if (fee < 50) return "warn";
  return "neg";
}

// Fear & Greed 0..100 → Ton (Extreme an beiden Enden = Vorsicht).
function sentimentTone(v: number): Tone {
  if (v < 25 || v >= 75) return "neg";
  if (v < 45 || v >= 55) return "warn";
  return "info";
}

// Funding-Rate (8h-Anteil) → Basispunkte. 0.0001 = 1bp.
function fundingBps(rate: number | null): string {
  if (rate == null) return "—";
  const bps = rate * 10_000;
  const sign = bps > 0 ? "+" : "";
  return `${sign}${bps.toFixed(3)} bp`;
}
function fundingColor(rate: number | null): string {
  if (rate == null) return "text-fg-subtle";
  if (rate > 0) return "text-pos";
  if (rate < 0) return "text-warn";
  return "text-fg";
}
function fmtOi(oi: number | null): string {
  if (oi == null) return "—";
  return oi.toLocaleString("de-DE", { maximumFractionDigits: 0 });
}
function fmtSz(sz: number): string {
  return sz.toLocaleString("de-DE", { maximumFractionDigits: 2 });
}
function fmtUsd(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toLocaleString("de-DE", { maximumFractionDigits: 2 })}M`;
  if (v >= 1_000) return `$${(v / 1_000).toLocaleString("de-DE", { maximumFractionDigits: 1 })}k`;
  return `$${v.toLocaleString("de-DE", { maximumFractionDigits: 0 })}`;
}
function fmtPrice(p: number): string {
  return p.toLocaleString("de-DE", { maximumFractionDigits: p >= 100 ? 0 : 2 });
}

export function MarketMicrostructurePanel() {
  const polling = usePolling<ChainStatus>((signal) => fetchChainStatus(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const deriv = usePolling<DerivativesSnapshot>((signal) => fetchDerivatives(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const sent = usePolling<SentimentSnapshot>((signal) => fetchSentiment(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const liq = usePolling<LiquidationsSnapshot>((signal) => fetchLiquidations(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const mom = usePolling<MomentumSnapshot>((signal) => fetchMomentum(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const lstr = usePolling<LiquidationStreamSnapshot>((signal) => fetchLiquidationsStream(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const data = polling.state === "ready" ? polling.data : null;
  const ok = data?.state === "ok";
  const fee = ok ? data.fee_sat_vb : null;
  const rows: DerivativeRow[] = deriv.state === "ready" ? deriv.data.rows : [];
  const sentiment = sent.state === "ready" && sent.data.available ? sent.data : null;
  const liqRows: LiquidationRow[] =
    liq.state === "ready" && liq.data.available ? liq.data.rows : [];
  const momRows: MomentumRow[] = mom.state === "ready" && mom.data.available ? mom.data.rows : [];
  const liqStream = lstr.state === "ready" ? lstr.data : null;

  return (
    <Card padded className="synthwave-pulse-edge overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <Activity size={14} className="text-info shrink-0" />
            Markt-Mikrostruktur
          </span>
        }
        subtitle="On-Chain (eigene bitcoind) · Perp-Funding/OI (eigene Ingestion) · read-only"
        right={
          <LiveDot
            state={polling.state}
            generatedAt={data ? data.generated_at : null}
            staleAfterMs={90_000}
            downAfterMs={240_000}
          />
        }
      />

      {/* On-Chain (eigene Node) + Sentiment (Fear & Greed) — zwei Gauges, echte Werte. */}
      <div className="grid grid-cols-2 items-end gap-3 lg:grid-cols-4">
        <div className="flex flex-col items-center">
          <Gauge
            value={fee}
            min={0}
            max={FEE_MAX}
            tone={feeTone(fee)}
            label={fee != null ? fee.toFixed(1) : "—"}
            className="w-28 h-16"
          />
          <span className="mt-0.5 text-2xs uppercase tracking-wider text-fg-subtle">
            On-Chain Fee · sat/vB
          </span>
        </div>
        <div className="flex flex-col items-center">
          <Gauge
            value={sentiment ? sentiment.value : null}
            min={0}
            max={100}
            tone={sentiment ? sentimentTone(sentiment.value) : "neutral"}
            label={sentiment ? String(sentiment.value) : "—"}
            className="w-28 h-16"
          />
          <span className="mt-0.5 flex items-center gap-1 text-2xs uppercase tracking-wider text-fg-subtle">
            <GaugeIcon size={9} /> Fear &amp; Greed
          </span>
          {sentiment && (
            <span className="text-[10px] text-fg-muted">{sentiment.classification}</span>
          )}
        </div>
        <Tile label="Mempool" value={ok ? `${data.mempool_tx.toLocaleString("de-DE")} tx` : "—"} />
        <Tile
          label="Tip-Höhe"
          value={ok && data.blocks ? data.blocks.toLocaleString("de-DE") : "—"}
          badge={
            ok ? (
              <Badge tone={data.synced ? "pos" : "warn"}>{data.synced ? "synced" : "syncing"}</Badge>
            ) : null
          }
        />
      </div>

      {!ok && (
        <div className="mt-2 text-2xs text-fg-subtle">
          On-Chain-Quelle{" "}
          {data?.state === "disabled"
            ? "deaktiviert (default-off)"
            : data?.state === "pending"
              ? "wärmt auf"
              : "nicht erreichbar"}{" "}
          — Werte erscheinen, sobald die eigene bitcoind antwortet.
        </div>
      )}

      {/* Perp-Derivate — echte Funding/OI aus eigener Ingestion (bybit-Snapshot). */}
      <div className="mt-4">
        <div className="mb-1.5 flex items-center gap-1.5 text-2xs uppercase tracking-wider text-fg-subtle">
          <TrendingUp size={11} /> Perp-Derivate · Funding &amp; Open Interest (eigene Ingestion)
        </div>
        {rows.length > 0 ? (
          <div className="overflow-hidden rounded-sm border border-line-subtle">
            <div className="grid grid-cols-[auto_1fr_1fr_auto] gap-x-3 bg-bg-2/40 px-2.5 py-1 text-[10px] uppercase tracking-wider text-fg-subtle">
              <span>Symbol</span>
              <span className="text-right">Funding · 8h</span>
              <span className="text-right">Open Interest</span>
              <span className="text-right">OI-z</span>
            </div>
            {rows.map((r) => (
              <div
                key={r.symbol}
                className="grid grid-cols-[auto_1fr_1fr_auto] items-baseline gap-x-3 border-t border-line-subtle px-2.5 py-1 text-xs"
              >
                <span className="font-mono text-fg">{r.symbol}</span>
                <span className={`text-right font-mono tabular-nums ${fundingColor(r.funding_rate)}`}>
                  {fundingBps(r.funding_rate)}
                </span>
                <span className="text-right font-mono tabular-nums text-fg">{fmtOi(r.open_interest)}</span>
                <span
                  className={`text-right font-mono tabular-nums ${
                    r.oi_change_zscore != null && Math.abs(r.oi_change_zscore) >= 2
                      ? "text-warn"
                      : "text-fg-subtle"
                  }`}
                >
                  {r.oi_change_zscore != null ? r.oi_change_zscore.toFixed(2) : "—"}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 text-2xs text-fg-subtle">
            {deriv.state === "error"
              ? "Derivate-Endpoint nicht erreichbar."
              : "Keine Funding/OI-Snapshots im Cache — erscheint, sobald der Refresh-Service Daten schreibt."}
          </div>
        )}
        <p className="mt-1 text-[10px] text-fg-subtle">
          Quelle: KAIs eigener Funding/OI-Snapshot-Cache (read-only), kein Dritt-API-Call im
          Request-Pfad. Positives Funding = Longs zahlen.
        </p>
      </div>

      {/* Liquidationen — OKX public (frei), Long/Short-Pressure je Symbol. */}
      <div className="mt-4">
        <div className="mb-1.5 flex items-center gap-1.5 text-2xs uppercase tracking-wider text-fg-subtle">
          <Flame size={11} className="text-warn" /> Liquidationen · Long/Short-Pressure (OKX)
        </div>
        {liqRows.length > 0 ? (
          <div className="space-y-1.5">
            {liqRows.map((r) => {
              // USD-Notional bevorzugt (sz × ctVal × bkPx); Fallback sz, falls
              // ctVal nicht auflösbar war (long_usd+short_usd == 0).
              const usdMode = r.long_usd + r.short_usd > 0;
              const longVal = usdMode ? r.long_usd : r.long_sz;
              const shortVal = usdMode ? r.short_usd : r.short_sz;
              const total = longVal + shortVal;
              const longPct = total > 0 ? (longVal / total) * 100 : 0;
              const fmt = usdMode ? fmtUsd : fmtSz;
              return (
                <div key={r.symbol} className="text-xs">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-mono text-fg">{r.symbol}</span>
                    <span className="font-mono tabular-nums text-[10px] text-fg-subtle">
                      {r.events} Events
                    </span>
                  </div>
                  <div className="mt-0.5 flex h-2 overflow-hidden rounded-xs bg-bg-2">
                    <div
                      className="bg-neg/70"
                      style={{ width: `${longPct}%` }}
                      title={`Long-Liqs ${fmt(longVal)}`}
                    />
                    <div
                      className="bg-pos/70"
                      style={{ width: `${100 - longPct}%` }}
                      title={`Short-Liqs ${fmt(shortVal)}`}
                    />
                  </div>
                  <div className="mt-0.5 flex justify-between text-[10px]">
                    <span className="text-neg">Long-Liqs {fmt(longVal)}</span>
                    <span className="text-pos">Short-Liqs {fmt(shortVal)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 text-2xs text-fg-subtle">
            {liq.state === "error"
              ? "Liquidations-Endpoint nicht erreichbar."
              : "Keine aktuellen Liquidationen im Cache."}
          </div>
        )}
        <p className="mt-1 text-[10px] text-fg-subtle">
          Quelle: OKX public liquidation-orders (frei, kein Key); USD-Notional = sz × ctVal × bkPx.
          Long-Liqs (rot) = Longs rekt; Short-Liqs (grün) = Shorts rekt.
        </p>
      </div>

      {/* Liquidations-Canary — Binance all-market !forceOrder@arr (#316, read-only). */}
      <div className="mt-4">
        <div className="mb-1.5 flex items-center gap-1.5 text-2xs uppercase tracking-wider text-fg-subtle">
          <Flame size={11} className="text-warn" /> Liquidations-Canary · Binance all-market
          {liqStream && (
            <Badge tone={liqStream.stream_connected ? "pos" : "neg"}>
              {liqStream.stream_connected ? "live" : "offline"}
            </Badge>
          )}
          <Badge tone="warn">snapshot-limited</Badge>
        </div>
        {liqStream && liqStream.metrics.total_events > 0 ? (
          (() => {
            const m = liqStream.metrics;
            const total = m.long_notional_usd_15m + m.short_notional_usd_15m;
            const longPct = total > 0 ? (m.long_notional_usd_15m / total) * 100 : 0;
            return (
              <div className="space-y-2">
                <div className="grid grid-cols-3 gap-2">
                  <Tile label="Events/min" value={String(m.events_per_min)} />
                  <Tile label="Notional 5m" value={fmtUsd(m.notional_usd["5m"] ?? 0)} />
                  <Tile label="Notional 15m" value={fmtUsd(m.notional_usd["15m"] ?? 0)} />
                </div>
                {total > 0 && (
                  <div>
                    <div className="flex h-2 overflow-hidden rounded-xs bg-bg-2">
                      <div
                        className="bg-neg/70"
                        style={{ width: `${longPct}%` }}
                        title={`Long-Liqs ${fmtUsd(m.long_notional_usd_15m)}`}
                      />
                      <div
                        className="bg-pos/70"
                        style={{ width: `${100 - longPct}%` }}
                        title={`Short-Liqs ${fmtUsd(m.short_notional_usd_15m)}`}
                      />
                    </div>
                    <div className="mt-0.5 flex justify-between text-[10px]">
                      <span className="text-neg">Long-Liqs 15m {fmtUsd(m.long_notional_usd_15m)}</span>
                      <span className="text-pos">Short-Liqs 15m {fmtUsd(m.short_notional_usd_15m)}</span>
                    </div>
                  </div>
                )}
                <div className="text-[10px] text-fg-subtle">
                  Größte Einzel-Liq 15m: {fmtUsd(m.largest_event_usd_15m)}
                  {m.imbalance_15m != null && (
                    <> · Imbalance {(m.imbalance_15m * 100).toFixed(0)}% {m.imbalance_15m > 0 ? "Long-lastig" : "Short-lastig"}</>
                  )}
                </div>
              </div>
            );
          })()
        ) : (
          <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 text-2xs text-fg-subtle">
            {lstr.state === "error"
              ? "Canary-Endpoint nicht erreichbar."
              : liqStream && !liqStream.stream_connected
                ? "Stream offline — kai-liquidation-stream nicht aktiv."
                : "Verbunden, noch keine Liquidationen erfasst (ruhiger Markt)."}
          </div>
        )}
        <p className="mt-1 text-[10px] text-fg-subtle">
          Quelle: Binance !forceOrder@arr (frei, kein Key). Snapshot-limitiert (nur größte
          Liquidation pro Symbol/1000 ms) → unterzählt, kein Markt-Total. Read-only, kein
          Trade-Signal (erst Edge-Messung).
        </p>
      </div>

      {/* Momentum — echte 24h-Änderung je Symbol (Binance, frei). */}
      <div className="mt-4">
        <div className="mb-1.5 flex items-center gap-1.5 text-2xs uppercase tracking-wider text-fg-subtle">
          <TrendingUp size={11} /> Momentum · 24h-Änderung (Binance)
        </div>
        {momRows.length > 0 ? (
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3">
            {momRows.map((r) => {
              const up = r.change_pct_24h >= 0;
              return (
                <div
                  key={r.symbol}
                  className="flex items-center justify-between gap-2 rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-1.5"
                >
                  <div className="min-w-0">
                    <div className="font-mono text-xs text-fg">{r.symbol}</div>
                    <div className="font-mono text-[10px] text-fg-subtle">{fmtPrice(r.last_price)}</div>
                  </div>
                  <div className={`flex items-center gap-1 font-mono tabular-nums text-sm ${up ? "text-pos" : "text-neg"}`}>
                    {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                    {up ? "+" : ""}
                    {r.change_pct_24h.toFixed(2)}%
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2 text-2xs text-fg-subtle">
            {mom.state === "error"
              ? "Momentum-Endpoint nicht erreichbar."
              : "Momentum-Daten werden geladen."}
          </div>
        )}
        <p className="mt-1 text-[10px] text-fg-subtle">
          Quelle: Binance 24h-Ticker (frei, kein Key) — echte 24h-Preisänderung, kein abgeleiteter
          Score.
        </p>
      </div>
    </Card>
  );
}

function Tile({ label, value, badge }: { label: string; value: string; badge?: ReactNode }) {
  return (
    <div className="rounded-sm border border-line-subtle bg-bg-2/40 px-2.5 py-2">
      <div className="text-2xs uppercase tracking-wider text-fg-subtle">{label}</div>
      <div className="mt-0.5 flex items-baseline justify-between gap-2">
        <span className="font-mono tabular-nums text-fg">{value}</span>
        {badge}
      </div>
    </div>
  );
}
