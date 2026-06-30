// Pure helpers for portfolio equity composition.
//
// The paper engine credits short-sale proceeds to CASH and books the short's
// current market value as a LIABILITY (app/execution/portfolio_read.py
// :_signed_market_value → total_equity = cash + Σ signed market value, short = −).
// Consequence the UI must not hide: the displayed cash balance is partly
// BORROWED — it includes proceeds that flow back out when the short is bought
// back. Painting the full cash as "frei"/green overstates what is actually free.
//
// These helpers split open positions into the long market value (an asset that
// adds to equity) and the short liability (money owed on buy-back), so the
// dashboard can show the honest net contribution (equity − cash) and flag the
// borrowed portion of cash.

export type PortfolioPositionLike = {
  position_side?: string | null;
  market_value_usd: number | null;
};

export type EquityComposition = {
  /** Σ market value of long positions — an asset, adds to equity. */
  longMarketValue: number;
  /** Σ market value of short positions — money owed on buy-back (a liability). */
  shortLiability: number;
  /** long − short: the net contribution of open positions to equity.
   *  Reconciles to (total_equity − cash) from the backend snapshot. */
  netPositionValue: number;
};

/** Split open positions into long asset value vs short liability. Positions
 *  without a live price (market_value_usd null) contribute 0 — matching the
 *  backend, which sums `market_value_usd or 0`. Side defaults to long. */
export function computeEquityComposition(
  positions: readonly PortfolioPositionLike[],
): EquityComposition {
  let longMarketValue = 0;
  let shortLiability = 0;
  for (const p of positions) {
    const mv = p.market_value_usd ?? 0;
    if ((p.position_side ?? "long") === "short") {
      shortLiability += mv;
    } else {
      longMarketValue += mv;
    }
  }
  return {
    longMarketValue,
    shortLiability,
    netPositionValue: longMarketValue - shortLiability,
  };
}
