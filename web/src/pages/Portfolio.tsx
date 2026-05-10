import { AlertCircle, RefreshCw, Briefcase } from "lucide-react";
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
import { useCurrency } from "@/state/CurrencyProvider";
import { humanizeLabel } from "@/lib/labels";

export function PortfolioPage() {
  const { t } = useT();
  const { fmt } = useCurrency();
  const fmt$ = (v: number | null | undefined, digits = 2) =>
    v == null ? "—" : fmt(v, undefined, digits);
  const snap = useApi(fetchPortfolioSnapshot, 30_000);
  const exposure = useApi(fetchExposureSummary, 30_000);

  const positions: PaperPosition[] = snap.state === "ready" ? snap.data.positions : [];
  const unrealized = positions.reduce((sum, p) => sum + (p.unrealized_pnl_usd ?? 0), 0);

  // DALI-P1: Bucket-Decomposition
  const totalEquity = snap.state === "ready" ? snap.data.total_equity_usd : 0;
  const cash = snap.state === "ready" ? snap.data.cash_usd : 0;
  const positionsValue = snap.state === "ready" ? snap.data.total_market_value_usd : 0;
  const realized = snap.state === "ready" ? snap.data.realized_pnl_usd : 0;
  // Stacked-Bar: nur die positiven Anteile, realized kann negativ sein → Anteil clamp.
  const denomForBar = Math.max(positionsValue + cash + Math.max(realized, 0), 1);
  const pctPositions = (positionsValue / denomForBar) * 100;
  const pctCash = (cash / denomForBar) * 100;
  const pctRealized = (Math.max(realized, 0) / denomForBar) * 100;

  // DALI-P2: PnL-Heatmap-Pills — höchster absoluter PnL für Skalierung.
  const maxAbsPnl = Math.max(...positions.map((p) => Math.abs(p.unrealized_pnl_usd ?? 0)), 1);

  // DALI-P5-Lite: Konzentrations-Tone basierend auf Largest-Position-Weight.
  const largestPct = exposure.state === "ready" ? exposure.data.largest_position_weight_pct ?? 0 : 0;
  const concentrationTone = largestPct > 70 ? "neg" : largestPct > 40 ? "warn" : "pos";

  return (
    <div className="p-5 xl:p-6 space-y-5 max-w-[1680px] mx-auto">
      <PageHeader
        title={t("pages.portfolio.title")}
        tone="accent"
        icon={<Briefcase size={18} />}
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

      {/* DALI-P1: Equity-Decomposition als Bucket-Modell.
          Operator: "Was ist auf der Börse, was im Trade, was Withdraw, was
          auf dem Konto, wo Gewinne." Stacked-Bar visualisiert den
          Geld-Aufenthaltsort, Hero-Number ist Total-Equity. */}
      {snap.state === "ready" && (
        <Card padded>
          <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
            <div>
              <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">Gesamt-Equity</div>
              <div
                className={cn(
                  "font-mono text-3xl font-semibold",
                  totalEquity > 0 ? "text-pos" : totalEquity < 0 ? "text-neg" : "text-fg",
                )}
              >
                {fmt$(totalEquity)}
              </div>
            </div>
            <Badge tone="info" dot>Paper-Konto · {snap.data.source}</Badge>
          </div>
          <div className="flex h-3 w-full overflow-hidden rounded-xs border border-line-subtle bg-bg-2">
            <div
              className="bg-info"
              style={{ width: `${pctPositions}%` }}
              title={`In offenen Positionen: ${fmt$(positionsValue)}`}
            />
            <div
              className="bg-pos"
              style={{ width: `${pctCash}%` }}
              title={`Cash: ${fmt$(cash)}`}
            />
            <div
              className="bg-ai"
              style={{ width: `${pctRealized}%` }}
              title={`Realized PnL (kumuliert positiv): ${fmt$(Math.max(realized, 0))}`}
            />
          </div>
          <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
            <BucketLabel
              tone="info"
              label="In Positionen"
              value={fmt$(positionsValue)}
              sub={`${snap.data.position_count} offen`}
            />
            <BucketLabel
              tone="pos"
              label="Cash (Paper)"
              value={fmt$(cash)}
              sub="liquide"
            />
            <BucketLabel
              tone={realized < 0 ? "neg" : "ai"}
              label="Realized PnL"
              value={fmt$(realized)}
              sub="kumuliert"
            />
          </div>
          <div className="mt-3 pt-3 border-t border-line-subtle/40 flex items-baseline justify-between text-xs">
            <span className="text-fg-subtle">Unrealized PnL (offene Positionen)</span>
            <span
              className={cn(
                "font-mono font-semibold",
                unrealized > 0 ? "text-pos" : unrealized < 0 ? "text-neg" : "text-fg-muted",
              )}
            >
              {unrealized >= 0 ? "+" : ""}
              {fmt$(unrealized)}
            </span>
          </div>
        </Card>
      )}

      {/* DALI-P4-Lite: Ehrlicher Hinweis auf fehlende Buckets im Paper-Mode.
          Operator hat explizit nach On-Exchange / Withdrawn / On-Account
          gefragt — diese existieren erst im Live-Mode-Datenmodell. */}
      <PreparedPanel
        title="Buckets: On-Exchange · On-Account · Withdrawn"
        reason="KAI führt aktuell nur den Paper-Account-Cash separat. Tatsächliche On-Exchange-Balances (echte Positionen auf Binance/Bybit), Withdraw-Historie und freier Account-Cash sind im Live-Mode-Datenmodell vorgesehen — aber im Paper-Mode nicht relevant."
        detail={
          <>
            Phase Live (Sprint 39+): Exchange-Balance-Reader joint Positions + freie Margins. Withdraw-Audit aus{" "}
            <span className="font-mono">exchange_relay.py</span> ergibt Withdrawals pro Asset. Vor Live-Mode keine sinnvolle Visualisierung.
          </>
        }
      />

      {/* DALI-P2: Per-Asset-Unrealized-PnL als Heatmap-Pills.
          Operator: "Wo sind die Gewinne gemacht worden." */}
      {snap.state === "ready" && positions.length > 0 && (
        <Card padded>
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-2">
            Unrealized PnL nach Asset (offene Positionen)
          </div>
          <div className="flex flex-wrap gap-1.5">
            {positions.map((p) => {
              const pnl = p.unrealized_pnl_usd ?? 0;
              const tone = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "muted";
              const intensity = Math.min(Math.abs(pnl) / maxAbsPnl, 1);
              return (
                <span
                  key={p.symbol}
                  title={`Entry ${fmt$(p.avg_entry_price)} · Markt ${fmt$(p.market_price)} · Wert ${fmt$(p.market_value_usd)}`}
                  className={cn(
                    "inline-flex items-baseline gap-1.5 rounded-sm border px-2 py-1 text-xs font-mono",
                    tone === "pos" && "border-pos/30 text-pos",
                    tone === "neg" && "border-neg/30 text-neg",
                    tone === "muted" && "border-line-subtle text-fg-muted",
                  )}
                  style={{
                    backgroundColor:
                      tone === "pos"
                        ? `rgb(var(--pos) / ${0.04 + intensity * 0.12})`
                        : tone === "neg"
                          ? `rgb(var(--neg) / ${0.04 + intensity * 0.12})`
                          : undefined,
                  }}
                >
                  <span className="font-semibold">{p.symbol}</span>
                  <span>
                    {pnl >= 0 ? "+" : ""}
                    {fmt$(pnl, 0)}
                  </span>
                </span>
              );
            })}
          </div>
          <div className="mt-2 text-2xs text-fg-subtle leading-relaxed">
            Stärke der Hintergrund-Färbung = Größe des PnL relativ zur größten Bewegung. Hover für Entry/Markt-Preis.
          </div>
        </Card>
      )}

      {/* PreparedPanel für Realized-PnL-pro-Asset (Backend-Lücke). */}
      <PreparedPanel
        title="Realized PnL nach Asset"
        reason="Per-Asset-Aufschlüsselung der realisierten Gewinne braucht Aggregation aus paper_execution_audit.jsonl — Endpoint folgt in Phase 2."
        detail={
          <>
            Quelle: <span className="font-mono">artifacts/paper_execution_audit.jsonl</span>. Geplant:{" "}
            <span className="font-mono">GET /operator/portfolio/realized-by-asset</span>.
          </>
        }
      />

      {exposure.state === "ready" && (
        <Card padded>
          <CardHeader
            title="Exposure"
            right={
              <Badge tone={exposure.data.mark_to_market_status === "ok" ? "pos" : "warn"} dot>
                MtM {exposure.data.mark_to_market_status}
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
              k="largest_position"
              v={
                exposure.data.largest_position_symbol
                  ? `${exposure.data.largest_position_symbol} (${exposure.data.largest_position_weight_pct?.toFixed(1)}%)`
                  : "—"
              }
            />
          </div>
          {/* DALI-P5-Lite: Konzentrations-Indikator */}
          {exposure.data.largest_position_symbol && (
            <div className="mt-3 pt-3 border-t border-line-subtle/40">
              <div className="flex items-baseline justify-between text-2xs mb-1">
                <span className="text-fg-subtle">Konzentrationsrisiko</span>
                <span
                  className={cn(
                    "font-mono",
                    concentrationTone === "neg" && "text-neg",
                    concentrationTone === "warn" && "text-warn",
                    concentrationTone === "pos" && "text-pos",
                  )}
                >
                  {largestPct.toFixed(1)}% in {exposure.data.largest_position_symbol}
                </span>
              </div>
              <div className="h-1.5 w-full rounded-xs bg-bg-2 overflow-hidden">
                <div
                  className={cn(
                    "h-full transition-all",
                    concentrationTone === "neg" && "bg-neg",
                    concentrationTone === "warn" && "bg-warn",
                    concentrationTone === "pos" && "bg-pos",
                  )}
                  style={{ width: `${Math.min(largestPct, 100)}%` }}
                />
              </div>
              <div className="mt-1 text-2xs text-fg-subtle">
                {concentrationTone === "neg" && "Hoch konzentriert (>70%) — Klumpenrisiko."}
                {concentrationTone === "warn" && "Erhöhte Konzentration (40–70%)."}
                {concentrationTone === "pos" && "Diversifiziert (<40% in einer Position)."}
              </div>
            </div>
          )}
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
        <div className="relative">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
            <thead>
              <tr className="text-fg-subtle text-2xs uppercase tracking-wider">
                <th className="text-left font-semibold px-4 py-2">Symbol</th>
                <th className="text-right font-semibold px-4 py-2">Qty</th>
                <th className="text-right font-semibold px-4 py-2">Entry</th>
                <th className="text-right font-semibold px-4 py-2">Markt</th>
                <th className="text-right font-semibold px-4 py-2">Wert</th>
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
                    {(p.unrealized_pnl_usd ?? 0) >= 0 && p.unrealized_pnl_usd != null ? "+" : ""}
                    {fmt$(p.unrealized_pnl_usd)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-fg-subtle">{fmt$(p.stop_loss)}</td>
                  <td className="px-4 py-2 text-right font-mono text-fg-subtle">{fmt$(p.take_profit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          <div
            className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-bg-1 to-transparent md:hidden"
            aria-hidden
          />
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

function BucketLabel({
  tone,
  label,
  value,
  sub,
}: {
  tone: "info" | "pos" | "ai" | "neg" | "muted";
  label: string;
  value: string;
  sub: string;
}) {
  const accentBar =
    tone === "info" ? "bg-info"
    : tone === "pos" ? "bg-pos"
    : tone === "ai" ? "bg-ai"
    : tone === "neg" ? "bg-neg"
    : "bg-fg-subtle/40";
  const accentText =
    tone === "info" ? "text-info"
    : tone === "pos" ? "text-pos"
    : tone === "ai" ? "text-ai"
    : tone === "neg" ? "text-neg"
    : "text-fg-muted";
  return (
    <div className="flex items-start gap-2">
      <span className={cn("mt-1 h-3 w-1 rounded-full shrink-0", accentBar)} aria-hidden />
      <div className="min-w-0">
        <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">{label}</div>
        <div className={cn("font-mono font-semibold text-base", accentText)}>{value}</div>
        <div className="text-2xs text-fg-subtle font-mono">{sub}</div>
      </div>
    </div>
  );
}

function RowKV({ k, v, tone }: { k: string; v: string; tone?: "pos" | "neg" | "warn" | "muted" }) {
  return (
    <div className="flex items-baseline justify-between gap-2 overflow-hidden border-b border-line-subtle/50 py-1">
      <span className="min-w-0 truncate text-2xs uppercase tracking-wide text-fg-subtle" title={k}>
        {humanizeLabel(k)}
      </span>
      <span className={cn(
        "shrink-0 font-mono font-medium text-sm text-right",
        tone === "pos" && "text-pos",
        tone === "neg" && "text-neg",
        tone === "warn" && "text-warn",
        tone === "muted" && "text-fg-muted",
        !tone && "text-fg",
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
