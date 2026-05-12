import { useState } from "react";
import { AlertCircle, RefreshCw, Play, Target, X } from "lucide-react";
import { useT } from "@/i18n/I18nProvider";
import { Badge, Button, Card, CardHeader } from "@/components/ui/Primitives";
import { PageHeader } from "@/layout/PageHeader";
import { useApi } from "@/lib/useApi";
import {
  fetchPortfolioSnapshot,
  fetchExposureSummary,
  postReprocess,
  postReconcileCompletion,
  postPositionRepair,
  postManualFill,
  fetchPendingEnvelopes,
  type PaperPosition,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { PreparedPanel } from "@/components/panels/PreparedPanel";
import { useCurrency } from "@/state/CurrencyProvider";

/**
 * Adaptive Decimal-Digits für Preis-Anzeige.
 *
 * Wurzel des 2026-05-12 "0.01-Bug": Sub-Cent-Tokens wie Q/USDT (0.01535)
 * wurden bei fixen `digits=2` als "0,01" gerendert — Operator nahm an die
 * Pipeline schreibe 0.01-Defaults, in Wahrheit truncierte das Frontend.
 * Adaptive-Bucketing nach abs(value):
 *   < 0.001   → 8 digits (e.g. SHIB 0.00001234)
 *   < 0.01    → 6 digits (e.g. 1000LUNC 0.103)
 *   < 1       → 4 digits (e.g. Q/USDT 0.0153)
 *   < 100     → 4 digits (e.g. DOGE 0.234, SOL 12.45)
 *   sonst     → 2 digits (e.g. BTC 67451.23)
 */
function priceDigits(v: number | null | undefined): number {
  if (v == null || !Number.isFinite(v)) return 2;
  const abs = Math.abs(v);
  if (abs < 0.001) return 8;
  if (abs < 0.01) return 6;
  if (abs < 1) return 4;
  if (abs < 100) return 4;
  return 2;
}

export function PortfolioPage() {
  const { t } = useT();
  const { fmt } = useCurrency();
  // Adaptive Decimals als Default — Operator-Auftrag 2026-05-12 Sektion 7
  // verlangt "keine automatische Umwandlung auf 0.01" für Display.
  const fmt$ = (v: number | null | undefined, digits?: number) =>
    v == null ? "—" : fmt(v, undefined, digits ?? priceDigits(v));
  const snap = useApi(fetchPortfolioSnapshot, 30_000);
  const exposure = useApi(fetchExposureSummary, 30_000);

  const positions: PaperPosition[] = snap.state === "ready" ? snap.data.positions : [];
  const unrealized = positions.reduce((sum, p) => sum + (p.unrealized_pnl_usd ?? 0), 0);
  // Sprint E (2026-05-12): Operator-Actions — Per-Action busy-flag + last-outcome.
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  function runAction(key: string, run: () => Promise<unknown>): void {
    if (actionBusy) return;
    setActionBusy(key);
    setActionMsg(null);
    run()
      .then((r) => {
        setActionMsg({ kind: "ok", text: `${key}: ${shortOutcome(r)}` });
        snap.reload();
      })
      .catch((e: Error) => setActionMsg({ kind: "err", text: `${key}: ${e.message}` }))
      .finally(() => setActionBusy(null));
  }

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

      {/* Sprint E (2026-05-12): Operator-Actions für Premium-Signal-Pipeline */}
      <Card padded>
        <CardHeader title="Operator-Aktionen" />
        <div className="flex flex-wrap gap-2 mt-2">
          <Button
            size="sm"
            variant="outline"
            disabled={actionBusy !== null}
            onClick={() => {
              const key = `reprocess-${Date.now()}`;
              runAction("Reprocess Bridge", () => postReprocess(undefined, key));
            }}
          >
            <Play size={12} /> Reprocess Bridge
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={actionBusy !== null}
            onClick={() => {
              const sym = window.prompt("Symbol (z.B. TRUTH/USDT) für Target-Completion-Reconcile?");
              if (!sym) return;
              const priceStr = window.prompt("Touch-Price (leer = aktueller Markt)?") ?? "";
              const price = priceStr.trim() ? Number(priceStr.replace(",", ".")) : undefined;
              const key = `reconcile-${sym}-${Date.now()}`;
              runAction(`Reconcile ${sym}`, () =>
                postReconcileCompletion(sym, Number.isFinite(price) ? price : undefined, key),
              );
            }}
          >
            <Target size={12} /> Target-Completion erzwingen
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={actionBusy !== null}
            onClick={() => {
              const env = window.prompt("Envelope-ID (aus pending-envelopes oder Telegram-Log)?");
              if (!env) return;
              const key = `fill-${env}`;
              runAction(`Manual Fill ${env.slice(0, 12)}…`, () => postManualFill(env, key));
            }}
          >
            <Play size={12} /> Manueller Signal-Fill
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={actionBusy !== null}
            onClick={async () => {
              setActionBusy("Pending-Liste");
              setActionMsg(null);
              try {
                const r = await fetchPendingEnvelopes(50);
                const summary = r.envelopes
                  .map((e) => `${e.symbol ?? "?"} · ${e.current_bridge_stage ?? "—"} · ${e.envelope_id}`)
                  .join("\n");
                setActionMsg({
                  kind: "ok",
                  text: `${r.count} pending envelopes:\n${summary || "(keine)"}`,
                });
              } catch (e) {
                setActionMsg({ kind: "err", text: `Pending-Liste: ${(e as Error).message}` });
              } finally {
                setActionBusy(null);
              }
            }}
          >
            <RefreshCw size={12} /> Pending-Envelopes
          </Button>
        </div>
        {actionBusy && (
          <div className="text-2xs text-fg-subtle mt-2 font-mono">
            … {actionBusy} läuft
          </div>
        )}
        {actionMsg && (
          <div
            className={cn(
              "text-2xs mt-2 font-mono whitespace-pre-wrap break-all",
              actionMsg.kind === "ok" ? "text-pos" : "text-neg",
            )}
          >
            {actionMsg.text}
          </div>
        )}
        <div className="text-2xs text-fg-subtle mt-3">
          Idempotent via interne Action-Keys. Aktionen erscheinen in
          <span className="font-mono"> artifacts/premium_signal_actions.jsonl</span>.
        </div>
      </Card>

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
                <th className="text-left font-semibold px-3 py-2">Symbol</th>
                <th className="text-left font-semibold px-3 py-2">Side</th>
                <th className="text-right font-semibold px-3 py-2">Lev</th>
                <th className="text-right font-semibold px-3 py-2">Qty</th>
                <th className="text-right font-semibold px-3 py-2">Entry</th>
                <th className="text-right font-semibold px-3 py-2">Market</th>
                <th className="text-right font-semibold px-3 py-2">Value</th>
                <th className="text-right font-semibold px-3 py-2">Unrealized</th>
                <th className="text-right font-semibold px-3 py-2">Realized</th>
                <th className="text-right font-semibold px-3 py-2">SL</th>
                <th className="text-right font-semibold px-3 py-2">TP</th>
                <th className="text-left font-semibold px-3 py-2">Source</th>
                <th className="text-right font-semibold px-3 py-2">Aktion</th>
              </tr>
            </thead>
            <tbody>
              {snap.state === "loading" && (
                <tr><td colSpan={13} className="px-4 py-6 text-center text-fg-subtle">{t("common.loading")}</td></tr>
              )}
              {snap.state === "ready" && positions.length === 0 && (
                <tr><td colSpan={13} className="px-4 py-6 text-center text-fg-subtle">{t("common.no_data")}</td></tr>
              )}
              {positions.map((p) => {
                const side = (p.position_side ?? "long").toUpperCase();
                const sideTone = side === "SHORT" ? "neg" : "pos";
                const lev = p.leverage;
                const tierCount = (p.take_profit_tiers ?? []).length;
                const sourceLabel = formatSource(p.source);
                const isStale = p.market_data_is_stale === true;
                const mktUnavailable = p.market_data_available === false;
                return (
                  <tr key={`${p.symbol}-${p.correlation_id ?? "no-cid"}`} className="border-t border-line-subtle hover:bg-bg-2">
                    <td className="px-3 py-2 font-mono font-semibold whitespace-nowrap">
                      <div>{p.symbol}</div>
                      {p.opened_at && (
                        <div className="text-2xs text-fg-subtle font-normal mt-0.5">
                          {formatOpenedAt(p.opened_at)}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={sideTone}>{side}</Badge>
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {lev != null && lev > 0 ? `${lev}x` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{formatQty(p.quantity)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmt$(p.avg_entry_price)}</td>
                    <td className={cn(
                      "px-3 py-2 text-right font-mono",
                      (isStale || mktUnavailable) && "text-warn",
                    )}>
                      {fmt$(p.market_price)}
                      {isStale && <span className="ml-1 text-2xs">·stale</span>}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{fmt$(p.market_value_usd)}</td>
                    <td className={cn(
                      "px-3 py-2 text-right font-mono",
                      (p.unrealized_pnl_usd ?? 0) > 0 && "text-pos",
                      (p.unrealized_pnl_usd ?? 0) < 0 && "text-neg",
                    )}>
                      {fmt$(p.unrealized_pnl_usd)}
                    </td>
                    <td className={cn(
                      "px-3 py-2 text-right font-mono",
                      (p.realized_pnl_usd ?? 0) > 0 && "text-pos",
                      (p.realized_pnl_usd ?? 0) < 0 && "text-neg",
                    )}>
                      {fmt$(p.realized_pnl_usd)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-fg-subtle">{fmt$(p.stop_loss)}</td>
                    <td className="px-3 py-2 text-right font-mono text-fg-subtle">
                      {fmt$(p.take_profit)}
                      {tierCount > 1 && (
                        <span className="ml-1 text-2xs text-info">+{tierCount - 1}</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-left text-2xs text-fg-subtle whitespace-nowrap">
                      {sourceLabel}
                    </td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      <button
                        type="button"
                        disabled={actionBusy !== null}
                        className={cn(
                          "inline-flex items-center gap-1 text-2xs font-mono px-2 py-1 rounded border border-line-subtle",
                          "hover:border-neg hover:text-neg disabled:opacity-50",
                        )}
                        onClick={() => {
                          if (!window.confirm(`Position ${p.symbol} schließen (Notfall-Close zum avg_entry)?`)) return;
                          const key = `close-${p.symbol}-${Date.now()}`;
                          runAction(`Close ${p.symbol}`, () =>
                            postPositionRepair(p.symbol, "close", { idempotency_key: key }),
                          );
                        }}
                      >
                        <X size={10} /> Close
                      </button>
                    </td>
                  </tr>
                );
              })}
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
    <div className="flex items-baseline justify-between gap-2 overflow-hidden border-b border-line-subtle/50 py-1">
      <span className="min-w-0 truncate font-mono text-2xs uppercase tracking-wide text-fg-subtle">{k}</span>
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

/**
 * Adaptive Quantity-Formatter. Sub-cent-Tokens haben oft 6-stellige Qty
 * (e.g. Q/USDT 85488.547329). High-price-Tokens (BTC) haben 0.01-stellige
 * Qty. fixed `toFixed(6)` macht beides unleserlich.
 */
function formatQty(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toLocaleString("de-DE", { maximumFractionDigits: 2 });
  if (abs >= 1) return v.toLocaleString("de-DE", { maximumFractionDigits: 4 });
  return v.toLocaleString("de-DE", { maximumFractionDigits: 6 });
}

function formatOpenedAt(iso: string): string {
  try {
    const d = new Date(iso);
    if (!Number.isFinite(d.getTime())) return iso.slice(0, 16);
    const today = new Date();
    const sameDay = d.toDateString() === today.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) +
      " " + d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso.slice(0, 16);
  }
}

/**
 * Short, human-readable Source-Label. Maps the long internal source-tags
 * the bridge writes (e.g. `telegram_premium_channel_approved`) to chip-friendly
 * compact labels. Unknown tags fall through with truncated original.
 */
/**
 * Concise toast-message for an action outcome. Avoids dumping the full JSON
 * payload into the operator-visible Action-Banner.
 */
function shortOutcome(r: unknown): string {
  if (r == null || typeof r !== "object") return "ok";
  const obj = r as Record<string, unknown>;
  if (typeof obj.status === "string") return String(obj.status);
  if (obj.tick && typeof obj.tick === "object") {
    const t = obj.tick as Record<string, unknown>;
    return `filled=${t.filled ?? 0} pending=${t.newly_pending ?? 0}+${t.re_pending ?? 0}`;
  }
  return "ok";
}

function formatSource(src: string | null | undefined): string {
  if (!src) return "—";
  const s = src.toLowerCase();
  if (s.startsWith("telegram_premium_channel")) {
    return s.endsWith("_approved") ? "TG·premium·✓" : "TG·premium";
  }
  if (s === "dashboard") return "dashboard";
  if (s.startsWith("tradingview")) return "TV";
  if (s.length > 18) return s.slice(0, 16) + "…";
  return s;
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
