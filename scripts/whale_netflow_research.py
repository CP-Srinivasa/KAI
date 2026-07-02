"""Phase-0 offline gate: does whale exchange-flow have a directional edge?

Composes the existing edge-discovery engine end-to-end with the whale-flow
features wired in:

    BinanceAdapter (historical klines)  +  whale_flow_events.json
      -> feature matrix (coin/stable netflow aligned causally, no look-ahead)
      -> forward-return labels
      -> BH-FDR hypothesis search over {TA+funding+whale} JOINTLY  (honest bar)
      -> cumulative hypothesis ledger + JSON report

The whale deciders are tested in the SAME batch as the TA/funding set so the
multiple-testing correction is honest, and every (hypothesis x config) is
recorded in the shared ledger so the cumulative trial count (which deflates any
later Sharpe at promotion) stays visible.

Honesty contract: this is built to find an edge OR to report there is none. Zero
whale survivors is the expected outcome (large transfers predict volatility more
than direction, and are lagging/noisy) — and a valid, $0 result.

Run (after building the events artifact):
    python scripts/build_whale_flow_series.py --archive wa_archive.json.gz
    python scripts/whale_netflow_research.py --lookback-days 365
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.analysis.features.feature_matrix import FeatureRow, build_feature_matrix
from app.analysis.features.forward_returns import compute_forward_return_bps
from app.analysis.features.whale_flow_align import FlowPoint
from app.market_data.history_loader import load_ohlcv_history
from app.research.evaluate import search_hypotheses
from app.research.ledger import HypothesisLedger
from app.research.runner import (
    DEFAULT_MIN_TRADES,
    LEDGER_PATH,
    SymbolSearchResult,
    _resolve_cost_bps,
    aggregates_to_ledger_entries,
    build_fetch,
    summarize_universe,
)
from app.research.whale_hypotheses import whale_hypotheses

logger = logging.getLogger(__name__)

UNIVERSE = ("BTC/USDT", "ETH/USDT", "SOL/USDT")  # the assets with archive coverage
TIMEFRAME = "1h"
DEFAULT_HORIZONS = (4, 12, 24)  # bars; whale flow acts on a 1h–24h horizon
MAX_LOOKBACK_DAYS = 730
_MS_PER_DAY = 86_400_000
DEFAULT_EVENTS = Path("artifacts/research/whale_flow_events.json")
WHALE_NAMES = frozenset(n for n, _ in whale_hypotheses())


def _load_events(path: Path) -> tuple[dict[str, list[FlowPoint]], list[FlowPoint]]:
    """Load the events artifact into per-coin FlowPoints + market-wide stable flows."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    coin = {
        sym: [FlowPoint(int(ms), float(usd)) for ms, usd in rows]
        for sym, rows in (doc.get("coin") or {}).items()
    }
    stable = [FlowPoint(int(ms), float(usd)) for ms, usd in (doc.get("stable") or [])]
    return coin, stable


async def run(
    lookback_days: int, horizons: tuple[int, ...], events_path: Path, min_trades: int
) -> int:
    from app.market_data.binance_adapter import BinanceAdapter

    if not events_path.exists():
        logger.error(
            "events artifact missing: %s (run build_whale_flow_series.py first)", events_path
        )
        return 2

    coin_events, stable_flows = _load_events(events_path)
    lookback_days = max(1, min(lookback_days, MAX_LOOKBACK_DAYS))
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = end_ms - lookback_days * _MS_PER_DAY

    fetch = build_fetch(BinanceAdapter().get_ohlcv)
    cost_bps = _resolve_cost_bps()
    hypotheses = whale_hypotheses()  # whale set first; TA/funding appended for the honest bar
    from app.research.runner import default_hypotheses

    hypotheses = default_hypotheses() + hypotheses

    # Backfill + build the feature matrix once per symbol (labels differ per horizon).
    per_symbol: dict[str, tuple[list[FeatureRow], list[float], int]] = {}
    for symbol in UNIVERSE:
        base = symbol.split("/")[0]
        coin_flows = coin_events.get(base, [])
        history = await load_ohlcv_history(symbol, TIMEFRAME, start_ms, end_ms, fetch)
        rows = build_feature_matrix(
            history.candles, coin_flows=coin_flows, stable_flows=stable_flows
        )
        closes = [c.close for c in history.candles]
        per_symbol[symbol] = (rows, closes, history.gap_bars)
        n_coin_z = sum(1 for r in rows if r.coin_netflow_z is not None)
        n_stable_z = sum(1 for r in rows if r.stable_netflow_z is not None)
        logger.info(
            "%s: %d candles, coin_netflow_z defined=%d, stable_netflow_z defined=%d (coin_events=%d)",
            symbol,
            len(rows),
            n_coin_z,
            n_stable_z,
            len(coin_flows),
        )

    ledger = HypothesisLedger(LEDGER_PATH)
    as_of = datetime.fromtimestamp(end_ms / 1000, tz=UTC).isoformat()
    report_horizons: list[dict[str, Any]] = []
    any_whale_survivor = False

    for horizon in horizons:
        results: list[SymbolSearchResult] = []
        for symbol, (rows, closes, gap_bars) in per_symbol.items():
            labels = compute_forward_return_bps(closes, horizon)
            report = search_hypotheses(hypotheses, rows, labels, cost_bps, min_trades=min_trades)
            results.append(SymbolSearchResult(symbol, len(rows), gap_bars, report))

        aggregates = summarize_universe(results)
        entries = aggregates_to_ledger_entries(
            aggregates,
            timeframe=TIMEFRAME,
            horizon=horizon,
            round_trip_cost_bps=cost_bps,
            universe=UNIVERSE,
            min_trades=min_trades,
            alpha=0.05,
            as_of_utc=as_of,
            lookback_days=lookback_days,
            recorded_at_utc=as_of,
        )
        for entry in entries:
            ledger.record(entry)

        whale_aggs = [a for a in aggregates if a.name in WHALE_NAMES]
        any_whale_survivor |= any(a.n_symbols_survived > 0 for a in whale_aggs)
        report_horizons.append(
            {
                "horizon": horizon,
                "whale": [
                    {
                        "name": a.name,
                        "symbols_survived": a.n_symbols_survived,
                        "symbols_evaluated": a.n_symbols_evaluated,
                        "mean_net_bps": round(a.mean_net_bps, 3),
                        "total_trades": a.total_trades,
                    }
                    for a in whale_aggs
                ],
            }
        )
        for a in whale_aggs:
            logger.info(
                "h=%d %-20s survived=%d/%d mean_net=%+.2fbps trades=%d",
                horizon,
                a.name,
                a.n_symbols_survived,
                a.n_symbols_evaluated,
                a.mean_net_bps,
                a.total_trades,
            )

    out_dir = Path("artifacts/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(end_ms / 1000, tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"whale_edge_search_{stamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "generated_at_utc": as_of,
                "timeframe": TIMEFRAME,
                "lookback_days": lookback_days,
                "universe": sorted(UNIVERSE),
                "round_trip_cost_bps": cost_bps,
                "min_trades": min_trades,
                "hypotheses_tested_cumulative": ledger.tested_count(),
                "horizons": report_horizons,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("wrote %s", out_path)
    logger.info(
        "VERDICT: %s whale survivor across %s horizons -- %s",
        ">=1" if any_whale_survivor else "0",
        list(horizons),
        "candidate(s) to scrutinize"
        if any_whale_survivor
        else "no whale-flow edge (valid $0 result)",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase-0 whale-flow offline edge gate.")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument(
        "--horizons", default="4,12,24", help="comma-separated forward horizons (bars)"
    )
    parser.add_argument("--events", default=str(DEFAULT_EVENTS), help="whale_flow_events.json path")
    parser.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES)
    args = parser.parse_args()
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    return asyncio.run(run(args.lookback_days, horizons, Path(args.events), args.min_trades))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
