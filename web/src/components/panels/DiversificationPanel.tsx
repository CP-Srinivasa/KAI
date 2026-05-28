import { Layers, AlertTriangle, Compass } from "lucide-react";
import { Badge, Card, CardHeader } from "@/components/ui/Primitives";
import { useApi } from "@/lib/useApi";
import { fetchDiversificationOverview } from "@/lib/api";
import { cn } from "@/lib/utils";

// Asset-Diversification / Klumpenrisiko-Panel.
// Beantwortet: Wie breit ist das Buch gestreut, wo sind Cluster, welche
// diversifizierten Alternativen gibt es, wie ist Short-Term vs Reserve.
// Read-only, keine teuren Render-Berechnungen (eine flache Map über <=N Rows).
export function DiversificationPanel() {
  const data = useApi(fetchDiversificationOverview, 60_000);

  if (data.state !== "ready") {
    return (
      <Card padded>
        <CardHeader title="Diversifikation & Klumpenrisiko" subtitle="Konzentrationsanalyse" />
        <div className="text-sm text-fg-subtle py-6 text-center">
          {data.state === "error" ? `Fehler beim Laden: ${data.error.message}` : "Lädt …"}
        </div>
      </Card>
    );
  }

  const d = data.data;
  const conc = d.concentration;
  if (d.available === false || !conc) {
    return (
      <Card padded>
        <CardHeader title="Diversifikation & Klumpenrisiko" subtitle="Konzentrationsanalyse" />
        <div className="text-sm text-fg-subtle py-6 text-center">
          Nicht verfügbar: {d.error ?? "keine bewertbaren Positionen"}
        </div>
      </Card>
    );
  }

  const btcEth = conc.btc_eth_short_term_pct;
  const btcEthOver = btcEth != null && btcEth > 40;
  const dist = (d.asset_distribution ?? []).filter((r) => (r.weight_pct ?? 0) > 0);
  const warnings = d.cluster_warnings ?? [];
  const candidates = (d.candidates ?? []).filter((c) => c.included);
  const sources = d.by_source ?? [];

  return (
    <Card padded>
      <CardHeader
        title="Diversifikation & Klumpenrisiko"
        subtitle={`Short-Term-Sleeve · Guard: ${d.guard_enabled ? d.guard_mode : "aus"} · Universe ${d.universe_size ?? "?"} Assets`}
        right={
          btcEth != null ? (
            <Badge tone={btcEthOver ? "warn" : "pos"}>
              BTC/ETH kurzfristig {btcEth.toFixed(0)}%
            </Badge>
          ) : (
            <Badge tone="muted">BTC/ETH n/a</Badge>
          )
        }
      />

      {/* Short-Term vs Reserve Split */}
      <div className="mt-3 grid grid-cols-3 gap-2 text-2xs">
        <SplitTile
          label="Short-Term"
          value={`$${fmtUsd(conc.short_term_gross_usd)}`}
          icon={<Layers size={12} />}
        />
        <SplitTile label="Reserve" value={`$${fmtUsd(conc.reserve_gross_usd)}`} />
        <SplitTile
          label="Positionen"
          value={`${conc.priced_position_count}${conc.unpriced_position_count ? ` (+${conc.unpriced_position_count} ohne Preis)` : ""}`}
        />
      </div>

      {/* Asset-Verteilung (Short-Term-Sleeve) */}
      {dist.length > 0 ? (
        <div className="mt-4">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-1">
            Asset-Verteilung (Short-Term)
          </div>
          <div className="space-y-1">
            {dist.slice(0, 10).map((r) => {
              const w = r.weight_pct ?? 0;
              const over = w > 25;
              return (
                <div key={r.symbol} className="flex items-center gap-2 text-xs">
                  <span className="font-semibold w-24 shrink-0 truncate">{r.symbol}</span>
                  <div className="flex-1 h-2 rounded-sm bg-bg-2 overflow-hidden">
                    <div
                      className={cn("h-full rounded-sm", over ? "bg-warn" : "bg-accent")}
                      style={{ width: `${Math.min(100, w)}%` }}
                    />
                  </div>
                  <span className="font-mono w-12 text-right text-fg-subtle">{w.toFixed(0)}%</span>
                  <span className="text-2xs text-fg-subtle w-28 shrink-0 truncate">
                    {r.correlation_group}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="mt-4 text-sm text-fg-subtle text-center py-3">
          Kein bewertbares Short-Term-Exposure.
        </div>
      )}

      {/* Klumpenrisiko-Warnungen */}
      {warnings.length > 0 && (
        <div className="mt-4">
          <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-warn font-semibold mb-1">
            <AlertTriangle size={12} /> Klumpenrisiko
          </div>
          <ul className="space-y-0.5">
            {warnings.slice(0, 6).map((w, i) => (
              <li key={i} className="text-2xs text-fg-subtle leading-snug">
                · {w}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Diversifizierte Alternativen / Scan-Kandidaten */}
      {candidates.length > 0 && (
        <div className="mt-4">
          <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-1">
            <Compass size={12} /> Diversifizierte Kandidaten (statt BTC/ETH-Schleife)
          </div>
          <div className="flex flex-wrap gap-1.5">
            {candidates.map((c) => (
              <span
                key={c.symbol}
                title={c.reasons.join(" · ")}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-sm bg-bg-2 text-2xs"
              >
                <span className="font-semibold">{c.symbol}</span>
                <span className="font-mono text-fg-subtle">{c.adjusted_score.toFixed(2)}</span>
                <span className="text-fg-subtle">{c.correlation_group}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Signalherkunft */}
      {sources.length > 0 && (
        <div className="mt-4">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-1">
            Signalherkunft
          </div>
          <div className="flex flex-wrap gap-2 text-2xs">
            {sources.map((s) => (
              <span key={s.source} className="text-fg-subtle">
                <span className="text-fg font-semibold">{s.source}</span>{" "}
                {s.weight_pct != null ? `${s.weight_pct.toFixed(0)}%` : "—"}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="mt-3 text-2xs text-fg-subtle">
        Konzentrationsgrenzen gelten nur für den Short-Term-Sleeve. Reserve
        (BTC/ETH long-term) ist bewusst ausgenommen. Fehlende Daten = nicht
        bewertbar (keine Schätzung).
      </div>
    </Card>
  );
}

function SplitTile({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="rounded-sm bg-bg-2 px-2 py-1.5">
      <div className="flex items-center gap-1 text-fg-subtle uppercase tracking-wider">
        {icon}
        {label}
      </div>
      <div className="font-mono font-semibold text-fg mt-0.5 truncate">{value}</div>
    </div>
  );
}

function fmtUsd(v: number): string {
  if (!Number.isFinite(v)) return "0";
  if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1) + "k";
  return v.toFixed(0);
}
