"""Edge-discovery runner — the live wiring: real backfill -> search -> verdict.

Composes the engine end to end::

    BinanceAdapter (historical klines) -> OHLCV history -> feature matrix
    -> forward-return labels -> hypothesis search (BH-controlled) -> verdict

The pure orchestration (:func:`run_symbol_search`, :func:`summarize_universe`)
is unit-tested with an injected fetch. :func:`main` is the live entry: it talks
to the public Binance REST API (read-only, no auth, no capital), uses the real
``CostModel`` round-trip cost, and writes a JSON report to ``artifacts/research/``.

Honesty contract: this runner is built to find edge *or to report there is none*.
Zero survivors across the universe is a valid, expected outcome — not a bug.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.analysis.features.feature_matrix import (
    TRAIL_RETURN_WINDOW,
    FeatureRow,
    build_feature_matrix,
)
from app.analysis.features.forward_returns import compute_forward_return_bps
from app.market_data.history_loader import FetchKlines, load_ohlcv_history
from app.market_data.kline_windows import interval_to_ms
from app.market_data.models import OHLCV
from app.research.evaluate import SearchReport, search_hypotheses
from app.research.ledger import HypothesisLedger, LedgerEntry, hypothesis_key
from app.research.samples import Decider

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSE: tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")
DEFAULT_TIMEFRAME = "1h"
DEFAULT_HORIZON = 4  # forward-return horizon in bars
DEFAULT_LOOKBACK_DAYS = 180
MAX_LOOKBACK_DAYS = 730  # hard cap (~2y): bounds backfill cost / memory
DEFAULT_MIN_TRADES = 50
DEFAULT_ALPHA = 0.05
LEDGER_PATH = Path("artifacts/research/hypothesis_ledger.jsonl")
_MS_PER_DAY = 86_400_000
_PAPER_VENUE = "paper"
_FALLBACK_COST_BPS = 20.0  # 2 x 10bp/side paper, if CostModel/config is unavailable


# --- hypothesis set (standard TA rules; every decider is None-safe) ------------


def default_hypotheses() -> list[tuple[str, Decider]]:
    """A curated, fixed set of textbook TA rules to test for edge.

    Each decider reads only the (causal) FeatureRow and returns +1/-1/0; None
    features (warm-up) map to 0 (no trade).
    """

    def rsi_oversold_long(r: FeatureRow) -> int:
        return 1 if (r.rsi_14 is not None and r.rsi_14 < 30.0) else 0

    def rsi_overbought_short(r: FeatureRow) -> int:
        return -1 if (r.rsi_14 is not None and r.rsi_14 > 70.0) else 0

    def macd_trend(r: FeatureRow) -> int:
        if r.macd is None:
            return 0
        return 1 if r.macd > 0 else -1

    def bollinger_revert_long(r: FeatureRow) -> int:
        return 1 if (r.bollinger_z_20 is not None and r.bollinger_z_20 < -2.0) else 0

    def bollinger_revert_short(r: FeatureRow) -> int:
        return -1 if (r.bollinger_z_20 is not None and r.bollinger_z_20 > 2.0) else 0

    def adx_trend(r: FeatureRow) -> int:
        if r.adx_14 is None or r.plus_di_14 is None or r.minus_di_14 is None or r.adx_14 < 25.0:
            return 0
        return 1 if r.plus_di_14 > r.minus_di_14 else -1

    # Time-series momentum (Liu-Tsyvinski-Wu): the most credible documented crypto
    # factor. Trailing-return SIGN predicts the forward return. Added to the SAME
    # BH-FDR set so the multiple-testing bar rises honestly (research doctrine,
    # docs/research/edge_discovery_strategy_20260625.md) — no free pass.
    def ts_momentum_long(r: FeatureRow) -> int:
        return 1 if (r.trail_return_20 is not None and r.trail_return_20 > 0.0) else 0

    def ts_momentum(r: FeatureRow) -> int:
        if r.trail_return_20 is None:
            return 0
        return 1 if r.trail_return_20 > 0.0 else -1

    # Risk-adjusted TS-momentum (Moskowitz-Ooi-Pedersen): trade only HIGH-conviction
    # momentum — the trailing return normalised to the horizon volatility
    # (per-bar realized vol scaled to the lookback by sqrt(window)) must exceed
    # ~1 sigma. Filters the weak/noisy momentum the raw-sign rule trades into the cost.
    _vol_horizon = math.sqrt(float(TRAIL_RETURN_WINDOW))

    def tsmom_vol_scaled(r: FeatureRow) -> int:
        if r.trail_return_20 is None or r.realized_vol_24 is None or r.realized_vol_24 <= 0.0:
            return 0
        z = r.trail_return_20 / (r.realized_vol_24 * _vol_horizon)
        if z > 1.0:
            return 1
        if z < -1.0:
            return -1
        return 0

    # Trend-confirmed momentum: only take the momentum direction when the market is
    # actually trending (ADX > 25). Momentum is a trend factor; gating it on trend
    # strength is the textbook way to avoid trading it through chop.
    def tsmom_adx_confirmed(r: FeatureRow) -> int:
        if r.trail_return_20 is None or r.adx_14 is None or r.adx_14 < 25.0:
            return 0
        return 1 if r.trail_return_20 > 0.0 else -1

    return [
        ("rsi_oversold_long", rsi_oversold_long),
        ("rsi_overbought_short", rsi_overbought_short),
        ("macd_trend", macd_trend),
        ("bollinger_revert_long", bollinger_revert_long),
        ("bollinger_revert_short", bollinger_revert_short),
        ("adx_trend", adx_trend),
        ("ts_momentum_long", ts_momentum_long),
        ("ts_momentum", ts_momentum),
        ("tsmom_vol_scaled", tsmom_vol_scaled),
        ("tsmom_adx_confirmed", tsmom_adx_confirmed),
    ]


# --- per-symbol search (pure given an injected fetch) --------------------------


@dataclass(frozen=True)
class SymbolSearchResult:
    """Result of searching one symbol's history."""

    symbol: str
    n_candles: int
    gap_bars: int
    report: SearchReport


async def run_symbol_search(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    fetch: FetchKlines,
    hypotheses: list[tuple[str, Decider]],
    round_trip_cost_bps: float,
    horizon: int = DEFAULT_HORIZON,
    alpha: float = 0.05,
    min_trades: int = DEFAULT_MIN_TRADES,
) -> SymbolSearchResult:
    """Backfill one symbol and run the hypothesis search over it."""
    history = await load_ohlcv_history(symbol, timeframe, start_ms, end_ms, fetch)
    rows = build_feature_matrix(history.candles)
    closes = [c.close for c in history.candles]
    labels = compute_forward_return_bps(closes, horizon)
    report = search_hypotheses(
        hypotheses,
        rows,
        labels,
        round_trip_cost_bps,
        alpha=alpha,
        min_trades=min_trades,
    )
    return SymbolSearchResult(
        symbol=symbol,
        n_candles=len(history.candles),
        gap_bars=history.gap_bars,
        report=report,
    )


# --- cross-symbol aggregation (pure) -------------------------------------------


@dataclass(frozen=True)
class HypothesisAggregate:
    """A hypothesis's standing across the whole universe."""

    name: str
    n_symbols_evaluated: int
    n_symbols_survived: int
    mean_net_bps: float  # trade-weighted across symbols
    total_trades: int


def summarize_universe(results: list[SymbolSearchResult]) -> list[HypothesisAggregate]:
    """Aggregate per-symbol verdicts into one standing per hypothesis."""
    names: list[str] = []
    for res in results:
        for verdict in res.report.verdicts:
            if verdict.name not in names:
                names.append(verdict.name)

    aggregates: list[HypothesisAggregate] = []
    for name in names:
        evaluated = 0
        survived = 0
        total_trades = 0
        weighted_sum = 0.0
        for res in results:
            for verdict in res.report.verdicts:
                if verdict.name != name:
                    continue
                evaluated += 1
                survived += int(verdict.survives)
                n = verdict.result.summary.n
                total_trades += n
                weighted_sum += verdict.result.summary.mean_bps * n
        mean = weighted_sum / total_trades if total_trades > 0 else 0.0
        aggregates.append(
            HypothesisAggregate(
                name=name,
                n_symbols_evaluated=evaluated,
                n_symbols_survived=survived,
                mean_net_bps=mean,
                total_trades=total_trades,
            )
        )
    return aggregates


# --- live entry ----------------------------------------------------------------


def aggregates_to_ledger_entries(
    aggregates: list[HypothesisAggregate],
    *,
    timeframe: str,
    horizon: int,
    round_trip_cost_bps: float,
    universe: Sequence[str],
    min_trades: int,
    alpha: float,
    as_of_utc: str,
    lookback_days: int,
    recorded_at_utc: str,
) -> list[LedgerEntry]:
    """Map cross-symbol aggregates to ledger entries (survived = any symbol survived)."""
    universe_list = sorted(universe)
    entries: list[LedgerEntry] = []
    for agg in aggregates:
        key = hypothesis_key(
            name=agg.name,
            timeframe=timeframe,
            horizon=horizon,
            round_trip_cost_bps=round_trip_cost_bps,
            universe=universe,
            min_trades=min_trades,
            alpha=alpha,
        )
        entries.append(
            LedgerEntry(
                key=key,
                name=agg.name,
                timeframe=timeframe,
                horizon=horizon,
                round_trip_cost_bps=round_trip_cost_bps,
                universe=universe_list,
                survived=agg.n_symbols_survived > 0,
                mean_net_bps=agg.mean_net_bps,
                total_trades=agg.total_trades,
                n_symbols_survived=agg.n_symbols_survived,
                as_of_utc=as_of_utc,
                lookback_days=lookback_days,
                recorded_at_utc=recorded_at_utc,
            )
        )
    return entries


def build_fetch(get_ohlcv: Callable[..., Awaitable[list[OHLCV]]]) -> FetchKlines:
    """Adapt an adapter's ``get_ohlcv`` into the loader's FetchKlines contract."""

    async def fetch(symbol: str, timeframe: str, start_ms: int, limit: int) -> list[OHLCV]:
        return await get_ohlcv(symbol, timeframe, limit=limit, start_time_ms=start_ms)

    return fetch


def _resolve_cost_bps() -> float:
    """Real round-trip cost (fees+spread+slippage) for the paper venue; fail-soft."""
    try:
        from app.execution.cost_model import CostModel

        return CostModel().round_trip(venue=_PAPER_VENUE).total_cost_bps
    except Exception as exc:  # noqa: BLE001 — research tool must not crash on config
        logger.warning(
            "CostModel unavailable (%s); using fallback %.1f bps", exc, _FALLBACK_COST_BPS
        )
        return _FALLBACK_COST_BPS


async def main(
    universe: tuple[str, ...] = DEFAULT_UNIVERSE,
    timeframe: str = DEFAULT_TIMEFRAME,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    horizon: int = DEFAULT_HORIZON,
    now_ms: int | None = None,
) -> int:
    """Live run: backfill the universe from Binance and search for edge."""
    from app.market_data.binance_adapter import BinanceAdapter

    lookback_days = max(1, min(lookback_days, MAX_LOOKBACK_DAYS))
    interval_to_ms(timeframe)  # validate timeframe early (raises on unsupported)
    end_ms = now_ms if now_ms is not None else int(datetime.now(UTC).timestamp() * 1000)
    start_ms = end_ms - lookback_days * _MS_PER_DAY

    fetch = build_fetch(BinanceAdapter().get_ohlcv)
    cost_bps = _resolve_cost_bps()
    hypotheses = default_hypotheses()

    results: list[SymbolSearchResult] = []
    for symbol in universe:
        try:
            res = await run_symbol_search(
                symbol, timeframe, start_ms, end_ms, fetch, hypotheses, cost_bps, horizon=horizon
            )
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not kill the run
            logger.warning("symbol %s failed: %s", symbol, exc)
            continue
        results.append(res)
        logger.info(
            "%s: %d candles (gap %d) -> %d/%d survivors",
            symbol,
            res.n_candles,
            res.gap_bars,
            res.report.n_survivors,
            res.report.n_hypotheses,
        )

    aggregates = summarize_universe(results)
    as_of = datetime.fromtimestamp(end_ms / 1000, tz=UTC).isoformat()

    # Cumulative hypothesis ledger: record every (hypothesis x config) so the
    # search never blindly re-tests and the total test count stays visible.
    ledger = HypothesisLedger(LEDGER_PATH)
    seen_before = ledger.keys()
    entries = aggregates_to_ledger_entries(
        aggregates,
        timeframe=timeframe,
        horizon=horizon,
        round_trip_cost_bps=cost_bps,
        universe=universe,
        min_trades=DEFAULT_MIN_TRADES,
        alpha=DEFAULT_ALPHA,
        as_of_utc=as_of,
        lookback_days=lookback_days,
        recorded_at_utc=as_of,
    )
    repeats = sum(1 for entry in entries if entry.key in seen_before)
    for entry in entries:
        ledger.record(entry)
    logger.info(
        "ledger: %d recorded (%d new, %d repeat-config) -> %d distinct configs total",
        len(entries),
        len(entries) - repeats,
        repeats,
        ledger.tested_count(),
    )

    out_dir = Path("artifacts/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(end_ms / 1000, tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"edge_search_{stamp}.json"
    doc = {
        "generated_at_utc": as_of,
        "timeframe": timeframe,
        "lookback_days": lookback_days,
        "horizon": horizon,
        "round_trip_cost_bps": cost_bps,
        "repeats_this_run": repeats,
        "hypotheses_tested_cumulative": ledger.tested_count(),
        "symbols": [
            {"symbol": r.symbol, "n_candles": r.n_candles, "gap_bars": r.gap_bars} for r in results
        ],
        "hypotheses": [
            {
                "name": a.name,
                "symbols_evaluated": a.n_symbols_evaluated,
                "symbols_survived": a.n_symbols_survived,
                "mean_net_bps": round(a.mean_net_bps, 3),
                "total_trades": a.total_trades,
            }
            for a in aggregates
        ],
    }
    out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    total_survivors = sum(a.n_symbols_survived for a in aggregates)
    logger.info("wrote %s", out_path)
    logger.info(
        "VERDICT: %d (hypothesis x symbol) survivors across %d symbols; %s",
        total_survivors,
        len(results),
        "no robust edge found" if total_survivors == 0 else "candidate(s) to scrutinize",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(asyncio.run(main()))
