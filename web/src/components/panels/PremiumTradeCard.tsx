// @data-source: props (/operator/portfolio-snapshot)
import { memo, useMemo } from "react";
import { ArrowDownRight, ArrowUpRight, Minus, Target, X, Zap } from "lucide-react";
import { Badge, Card, CardHeader } from "@/components/ui/Primitives";
import type { PaperPosition, PaperPositionTpTier } from "@/lib/api";
import { useCurrency } from "@/state/CurrencyProvider";
import { cn } from "@/lib/utils";
import { formatDayTime } from "@/lib/time";

/**
 * PremiumTradeCard — Hero-Komponente für aktive Premium-Telegram-Signal-Positionen.
 *
 * Wurzel: 2026-05-14 Forensik (BAS/USDT, ASTER/USDT). Die Premium-Signal-Pipeline
 * hat technisch sauber geöffnet — Entry, SL, 4 TP-Tiers, Leverage, Quantity alles
 * korrekt — aber in der dichten Positions-Tabelle waren diese Trades nur eine
 * Zeile unter vielen. Der Operator brauchte 5 Sekunden um den Plan zu rekonstruieren
 * ("welche TPs schon erreicht, wo ist SL, wie viel margin liegt drin").
 * Diese Karte rendert pro offenem Premium-Trade alle Plan- + Live-Daten kompakt:
 * - Symbol + Richtung + Hebel als Hero
 * - Einstieg vs aktueller Markt + Trend-Pfeil
 * - SL-Distanz % + TP-Tier-Ladder mit Hit-Status
 * - Offener PnL + realisierter PnL kumuliert
 * - Klare "Wird geschlossen bei"-Aussage (nächster TP-Tier ODER SL)
 * - Hard-Confirm-Schließen-Button (inklusive no-market-data-Warnung aus #46)
 *
 * Nur-Frontend — alle Daten kommen aus `/operator/portfolio-snapshot`.
 */

type Props = {
  position: PaperPosition;
  fmt$: (v: number | null | undefined, digits?: number) => string;
  onClose: (symbol: string, idempotencyKey: string, mktUnavailable: boolean) => void;
  busy: boolean;
};

function _sourceLabel(source: string | null | undefined): string {
  if (!source) return "Quelle unbekannt";
  if (source.includes("telegram_premium")) return "Premium-Telegram";
  if (source.includes("dashboard")) return "Dashboard-Operator";
  return source;
}

function _formatOpenedAt(iso: string | null | undefined): string {
  if (!iso) return "";
  return formatDayTime(iso);
}

/**
 * Tier-Hit-Heuristik via reduzierte Quantity.
 *
 * Wenn die ursprüngliche Position-Quantity X war und aktuell nur noch Y offen ist,
 * wurde (X-Y)/X-Anteil bereits abgewickelt. Bei einem 4-Tier-Setup à 25% je Tier
 * heißt das: 25% closed → 1 Tier hit, 50% → 2 Tier hits, etc.
 *
 * Limitation: Wenn ein SL einen Teil reduziert hat, zählt dieser auch als "hit".
 * Bei einem ausgelösten SL ist die Position aber sowieso geschlossen und erscheint
 * nicht mehr als offen — wir sehen also nur den Tier-Reduktions-Pfad. Operator
 * akzeptiert die Heuristik per Doc-Comment.
 */
function _tiersHit(position: PaperPosition, tierCount: number): number {
  const initial = position.initial_quantity;
  const current = position.quantity;
  if (initial == null || initial <= 0 || tierCount <= 0) return 0;
  const closedFrac = (initial - current) / initial;
  return Math.min(tierCount, Math.max(0, Math.round(closedFrac * tierCount)));
}

function _nextTpPrice(
  tiers: PaperPositionTpTier[] | null | undefined,
  hitCount: number,
): number | null {
  if (!tiers || tiers.length === 0) return null;
  if (hitCount >= tiers.length) return null;
  return tiers[hitCount].price;
}

export const PremiumTradeCard = memo(function PremiumTradeCard({
  position: p,
  fmt$,
  onClose,
  busy,
}: Props): JSX.Element {
  const { fmtNum } = useCurrency();
  const isLong = (p.position_side ?? "long").toLowerCase() === "long";
  const sideTone = isLong ? "pos" : "neg";
  const sideLabel = isLong ? "LONG" : "SHORT";
  const SideIcon = isLong ? ArrowUpRight : ArrowDownRight;

  const mktUnavailable = p.market_data_available === false;
  const isStale = p.market_data_is_stale === true;

  const tiers = p.take_profit_tiers ?? [];
  const tierCount = tiers.length;
  const tiersHit = useMemo(() => _tiersHit(p, tierCount), [p, tierCount]);
  const nextTp = useMemo(() => _nextTpPrice(tiers, tiersHit), [tiers, tiersHit]);

  // Markt-vs-Entry-Bewegung in % — nutzbar als Trend-Indikator wenn Markt da.
  const moveFromEntryPct = useMemo(() => {
    if (p.market_price == null || p.avg_entry_price == null || p.avg_entry_price === 0) return null;
    const raw = ((p.market_price - p.avg_entry_price) / p.avg_entry_price) * 100;
    // LONG: positive Bewegung = Gewinn. SHORT: negative Bewegung = Gewinn.
    return isLong ? raw : -raw;
  }, [p.market_price, p.avg_entry_price, isLong]);

  // SL- und nächstes-TP-Distanz vom Markt-Preis (für "wie nah am Trigger").
  const slDistancePct = useMemo(() => {
    if (p.market_price == null || p.stop_loss == null || p.market_price === 0) return null;
    return ((p.stop_loss - p.market_price) / p.market_price) * 100;
  }, [p.market_price, p.stop_loss]);
  const nextTpDistancePct = useMemo(() => {
    if (p.market_price == null || nextTp == null || p.market_price === 0) return null;
    return ((nextTp - p.market_price) / p.market_price) * 100;
  }, [p.market_price, nextTp]);

  // Unrealized PnL in % auf den Notional-Einsatz (Quantity * AvgEntry).
  const unrealPnlPct = useMemo(() => {
    if (p.unrealized_pnl_usd == null || p.avg_entry_price == null || p.quantity == null) return null;
    const notional = p.avg_entry_price * p.quantity;
    if (notional === 0) return null;
    return (p.unrealized_pnl_usd / notional) * 100;
  }, [p.unrealized_pnl_usd, p.avg_entry_price, p.quantity]);

  const moveTone = moveFromEntryPct == null ? "muted" : moveFromEntryPct > 0 ? "pos" : moveFromEntryPct < 0 ? "neg" : "muted";
  const MoveIcon = moveFromEntryPct == null
    ? Minus
    : moveFromEntryPct > 0
      ? ArrowUpRight
      : moveFromEntryPct < 0
        ? ArrowDownRight
        : Minus;

  return (
    <Card padded className="synthwave-pulse-edge overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-lg font-semibold">{p.symbol}</span>
            <Badge tone={sideTone}>
              <SideIcon size={10} /> {sideLabel}
            </Badge>
            {p.leverage != null && p.leverage > 0 && (
              <Badge tone="ai" title="Hebel (Leverage) — Multiplikator des Markt-Risikos auf das eingesetzte Margin">
                <Zap size={10} /> {p.leverage}x
              </Badge>
            )}
            {mktUnavailable && (
              <Badge tone="info" title="Preis-Provider liefert keinen Kurs — Position lebt, SL/TP aktiv, nur die Live-Bewertung fehlt.">
                Preis steht aus
              </Badge>
            )}
            {isStale && !mktUnavailable && (
              <Badge tone="warn" title="Marktpreis ist veraltet (stale) — Bewertung evtl. ungenau.">
                Preis ·alt
              </Badge>
            )}
          </span>
        }
        subtitle={
          <span className="text-2xs text-fg-subtle">
            {_sourceLabel(p.source)}
            {p.opened_at && <> · geöffnet {_formatOpenedAt(p.opened_at)}</>}
          </span>
        }
      />

      {/* Row 1: Einstieg | Markt | Bewegung */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
        <div className="rounded-md border border-line-subtle bg-bg-2 p-2.5">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
            Einstieg
          </div>
          <div className="font-mono text-base font-semibold text-fg">{fmt$(p.avg_entry_price)}</div>
          <div className="mt-0.5 text-2xs text-fg-subtle">
            {fmtNum(p.quantity, { maxDigits: 2 })} Stk
          </div>
        </div>
        <div className="rounded-md border border-line-subtle bg-bg-2 p-2.5">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
            Markt
          </div>
          <div className={cn(
            "font-mono text-base font-semibold",
            mktUnavailable ? "text-info" : isStale ? "text-warn" : "text-fg",
          )}>
            {mktUnavailable ? "kein Kurs" : fmt$(p.market_price)}
          </div>
          <div className="mt-0.5 text-2xs text-fg-subtle">
            Wert {fmt$(p.market_value_usd)}
          </div>
        </div>
        <div className="rounded-md border border-line-subtle bg-bg-2 p-2.5">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
            Bewegung vs Einstieg
          </div>
          <div className={cn(
            "font-mono text-base font-semibold flex items-center gap-1",
            moveTone === "pos" && "text-pos",
            moveTone === "neg" && "text-neg",
            moveTone === "muted" && "text-fg-muted",
          )}>
            <MoveIcon size={14} />
            {moveFromEntryPct == null ? "—" : `${moveFromEntryPct >= 0 ? "+" : ""}${moveFromEntryPct.toFixed(2)}%`}
          </div>
          <div className="mt-0.5 text-2xs text-fg-subtle">
            {isLong ? "Long: Markt > Einstieg = Profit" : "Short: Markt < Einstieg = Profit"}
          </div>
        </div>
      </div>

      {/* Row 2: SL + TP-Ladder */}
      <div className="rounded-md border border-line-subtle bg-bg-2 p-3 mb-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
            Plan (Stop & Take-Profit-Tiers)
          </span>
          <span className="text-2xs text-fg-subtle font-mono">
            {tiersHit}/{tierCount || 1} Tier erreicht
          </span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {/* SL-Block */}
          <div className="flex items-center gap-2 text-xs">
            <Badge tone="neg" dot>SL</Badge>
            <span className="font-mono text-fg">{fmt$(p.stop_loss)}</span>
            {slDistancePct != null && (
              <span className="text-2xs text-fg-subtle font-mono">
                ({slDistancePct >= 0 ? "+" : ""}{slDistancePct.toFixed(2)}% vom Markt)
              </span>
            )}
          </div>
          {/* Nächster TP */}
          <div className="flex items-center gap-2 text-xs">
            <Badge tone="pos" dot>nächster TP</Badge>
            {nextTp != null ? (
              <>
                <span className="font-mono text-fg">{fmt$(nextTp)}</span>
                {nextTpDistancePct != null && (
                  <span className="text-2xs text-fg-subtle font-mono">
                    ({nextTpDistancePct >= 0 ? "+" : ""}{nextTpDistancePct.toFixed(2)}% vom Markt)
                  </span>
                )}
              </>
            ) : (
              <span className="text-2xs text-fg-subtle">alle Tiers erreicht</span>
            )}
          </div>
        </div>
        {/* TP-Ladder visualisiert */}
        {tierCount > 0 && (
          <div className="mt-3">
            <div className="flex items-center gap-1 mb-1">
              {tiers.map((tier, idx) => {
                const hit = idx < tiersHit;
                return (
                  <div
                    key={idx}
                    className={cn(
                      "flex-1 h-1.5 rounded-xs transition-all",
                      hit ? "bg-pos" : "bg-line",
                    )}
                    title={`TP${idx + 1}: ${fmt$(tier.price)} · ${(tier.qty_share * 100).toFixed(0)}% qty${hit ? " — erreicht" : ""}`}
                  />
                );
              })}
            </div>
            <div className="flex items-center gap-1 text-2xs text-fg-subtle font-mono">
              {tiers.map((tier, idx) => (
                <span
                  key={idx}
                  className={cn(
                    "flex-1 text-center",
                    idx < tiersHit && "text-pos font-semibold",
                  )}
                  title={`Tier ${idx + 1}`}
                >
                  {fmt$(tier.price)}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Row 3: PnL */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
        <div className="rounded-md border border-line-subtle bg-bg-2 p-2.5">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
            Offener Gewinn / Verlust
          </div>
          <div className={cn(
            "font-mono text-base font-semibold flex items-baseline gap-2",
            (p.unrealized_pnl_usd ?? 0) > 0 && "text-pos",
            (p.unrealized_pnl_usd ?? 0) < 0 && "text-neg",
            (p.unrealized_pnl_usd ?? 0) === 0 && "text-fg-muted",
          )}>
            <span>
              {(p.unrealized_pnl_usd ?? 0) > 0 ? "+" : ""}
              {fmt$(p.unrealized_pnl_usd)}
            </span>
            {unrealPnlPct != null && (
              <span className="text-2xs">
                ({unrealPnlPct >= 0 ? "+" : ""}{unrealPnlPct.toFixed(2)}% Notional)
              </span>
            )}
          </div>
          <div className="mt-0.5 text-2xs text-fg-subtle">
            Noch nicht realisiert — schwankt mit dem Markt
          </div>
        </div>
        <div className="rounded-md border border-line-subtle bg-bg-2 p-2.5">
          <div className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
            Realisierter Gewinn (kumuliert)
          </div>
          <div className={cn(
            "font-mono text-base font-semibold",
            (p.realized_pnl_usd ?? 0) > 0 && "text-pos",
            (p.realized_pnl_usd ?? 0) < 0 && "text-neg",
            (p.realized_pnl_usd ?? 0) === 0 && "text-fg-muted",
          )}>
            {fmt$(p.realized_pnl_usd)}
          </div>
          <div className="mt-0.5 text-2xs text-fg-subtle">
            Über alle Tier-Closes dieses Trades
          </div>
        </div>
      </div>

      {/* Row 4: Close-Trigger-Klartext + Schließen-Button */}
      <div className="flex items-center justify-between gap-3 flex-wrap pt-2 border-t border-line-subtle/40">
        <div className="text-2xs text-fg-muted leading-relaxed flex items-center gap-2">
          <Target size={12} />
          <span>
            Wird automatisch geschlossen bei{" "}
            <span className="font-mono text-fg">
              {nextTp != null ? fmt$(nextTp) : "—"}
            </span>{" "}
            (nächster TP-Tier) oder{" "}
            <span className="font-mono text-fg">{fmt$(p.stop_loss)}</span> (SL).
          </span>
        </div>
        <button
          type="button"
          disabled={busy}
          className={cn(
            "inline-flex items-center gap-1.5 text-xs font-mono px-3 py-1.5 rounded-md border border-line",
            "hover:border-neg hover:text-neg disabled:opacity-50 transition-colors",
          )}
          onClick={() => {
            const key = `close-${p.symbol}-${Date.now()}`;
            onClose(p.symbol, key, mktUnavailable);
          }}
        >
          <X size={12} /> Position schließen
        </button>
      </div>
    </Card>
  );
});
