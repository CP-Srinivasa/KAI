// @data-source: /dashboard/api/chain (live) · /dashboard/api/markets/derivatives (live) · /dashboard/api/markets/sentiment (live)
//
// Markt-Mikrostruktur für die Märkte-Seite — strikt das, was KAI WAHR weiß:
//   • On-Chain (Fee-Gauge, Mempool, Tip) aus der EIGENEN bitcoind.
//   • Perp-Funding + Open Interest aus KAIs EIGENER Ingestion (bybit-Snapshot).
//   • Sentiment aus dem freien Fear-&-Greed-Index (alternative.me, server-gecacht).
// Metriken ohne verdrahteten Datenpfad (Liquidations/Momentum) bleiben ehrlich
// „ausstehend" in einer Quellen-Matrix statt erfundener Werte (No-Fake).
import type { ReactNode } from "react";
import { Activity, Database, TrendingUp, Gauge as GaugeIcon } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { LiveDot } from "@/components/ui/LiveDot";
import { Gauge } from "@/components/viz/Gauge";
import {
  fetchChainStatus,
  fetchDerivatives,
  fetchSentiment,
  type ChainStatus,
  type DerivativesSnapshot,
  type DerivativeRow,
  type SentimentSnapshot,
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

// Metriken ohne eigenen Datenpfad — ehrlich „ausstehend".
const EXTERNAL_SOURCES: ReadonlyArray<{ metric: string; src: string }> = [
  { metric: "Liquidations", src: "CoinGlass" },
  { metric: "Momentum", src: "Dune" },
];

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
  const data = polling.state === "ready" ? polling.data : null;
  const ok = data?.state === "ok";
  const fee = ok ? data.fee_sat_vb : null;
  const rows: DerivativeRow[] = deriv.state === "ready" ? deriv.data.rows : [];
  const sentiment = sent.state === "ready" && sent.data.available ? sent.data : null;

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

      {/* Metriken ohne eigenen Datenpfad — ehrlich „ausstehend". */}
      <div className="mt-4">
        <div className="mb-1.5 flex items-center gap-1.5 text-2xs uppercase tracking-wider text-fg-subtle">
          <Database size={11} /> Weitere Markt-Metriken · Quellen-Matrix
        </div>
        <div className="grid grid-cols-2 gap-2">
          {EXTERNAL_SOURCES.map((s) => (
            <div key={s.metric} className="rounded-sm border border-line-subtle bg-bg-2/40 px-2 py-1.5">
              <div className="truncate text-2xs font-medium text-fg">{s.metric}</div>
              <div className="mt-0.5 flex items-center justify-between gap-1">
                <span className="truncate font-mono text-[10px] text-fg-subtle">{s.src}</span>
                <StatusPill kind="pending" showIcon={false} />
              </div>
            </div>
          ))}
        </div>
        <p className="mt-1.5 text-[10px] leading-relaxed text-fg-subtle">
          No-Fake: Metriken ohne verdrahteten Datenpfad zeigen „ausstehend" statt erfundener Werte.
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
