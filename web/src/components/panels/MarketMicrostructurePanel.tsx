// @data-source: /dashboard/api/chain (live, KAIs eigene bitcoind)
//
// Markt-Mikrostruktur für die Märkte-Seite: das, was KAI WAHR weiß, kommt aus der
// eigenen Full Node (On-Chain-Fee als Gauge, Mempool, Tip-Höhe). Externe
// Derivate-/Sentiment-Metriken (Funding/OI/Liquidations/Sentiment/Momentum) haben
// noch keinen verdrahteten Live-Endpoint → sie erscheinen ehrlich als
// "ausstehend" in einer Quellen-Matrix statt mit erfundenen Werten (No-Fake-Doktrin).
import type { ReactNode } from "react";
import { Activity, Database } from "lucide-react";
import { Card, CardHeader, Badge } from "@/components/ui/Primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { LiveDot } from "@/components/ui/LiveDot";
import { Gauge } from "@/components/viz/Gauge";
import { fetchChainStatus, type ChainStatus } from "@/lib/api";
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

// Externe Markt-Mikrostruktur — Metrik → Quelle. Read-only, noch nicht verdrahtet.
const EXTERNAL_SOURCES: ReadonlyArray<{ metric: string; src: string }> = [
  { metric: "Funding-Rate", src: "CoinGlass" },
  { metric: "Open Interest", src: "CoinGlass" },
  { metric: "Liquidations", src: "CoinGlass" },
  { metric: "Sentiment", src: "Glassnode" },
  { metric: "Momentum", src: "Dune" },
];

export function MarketMicrostructurePanel() {
  const polling = usePolling<ChainStatus>((signal) => fetchChainStatus(signal), {
    intervalMs: POLL_MS,
    pauseWhenHidden: true,
    retry: { maxAttempts: 3, baseMs: 2_000 },
  });
  const data = polling.state === "ready" ? polling.data : null;
  const ok = data?.state === "ok";
  const fee = ok ? data.fee_sat_vb : null;

  return (
    <Card padded className="synthwave-pulse-edge overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            <Activity size={14} className="text-info shrink-0" />
            Markt-Mikrostruktur
          </span>
        }
        subtitle="On-Chain aus eigener bitcoind · externe Derivate-Quellen read-only"
        right={
          <LiveDot
            state={polling.state}
            generatedAt={data ? data.generated_at : null}
            staleAfterMs={90_000}
            downAfterMs={240_000}
          />
        }
      />

      {/* On-Chain — echte Werte aus KAIs eigener Node (kein Dritter). */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
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

      {/* Externe Derivate-/Sentiment-Metriken — ehrlich nicht verdrahtet. */}
      <div className="mt-4">
        <div className="mb-1.5 flex items-center gap-1.5 text-2xs uppercase tracking-wider text-fg-subtle">
          <Database size={11} /> Externe Markt-Metriken · Quellen-Matrix
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
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
          No-Fake: externe Quellen ohne verdrahteten Live-Endpoint zeigen „ausstehend" statt
          erfundener Werte. Funding/OI sind als Bayes-Evidence (shadow) vorhanden, hier noch nicht
          als Markt-Chart.
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
