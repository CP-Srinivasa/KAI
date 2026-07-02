"""Phase-1 offline gate: does scheduled token-unlock pressure have an edge?

Composes the existing edge-discovery engine with the unlock-pressure feature:

    BinanceAdapter (historical klines)  +  unlock_events.json (DefiLlama)
      -> feature matrix (forward unlock-pressure aligned causally, schedule public)
      -> forward-return labels
      -> BH-FDR hypothesis search over {TA+funding+unlock} JOINTLY (honest bar)
      -> cumulative hypothesis ledger + JSON report

The unlock deciders are tested in the SAME batch as the TA/funding set so the
multiple-testing correction is honest, and every (hypothesis x config) is recorded
in the shared ledger so the cumulative trial count (which deflates any later Sharpe
at promotion) stays visible.

Unlike the whale-transfer gate, this is the doctrine's strongest documented
capital-free edge candidate (~88.5% negative 72h around large unlocks), so a
survivor is plausible — but it still must clear the full BH-FDR + bucket bar. Zero
survivors is also a valid, honest result.

Run (after building the events artifact):
    python scripts/build_unlock_events.py
    python scripts/unlock_pressure_research.py --lookback-days 730
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
from app.analysis.features.unlock_align import UnlockEvent
from app.market_data.history_loader import load_ohlcv_history
from app.research.confound import beta_neutral_forward_returns, timing_alpha_report
from app.research.evaluate import search_hypotheses
from app.research.ledger import HypothesisLedger
from app.research.runner import (
    DEFAULT_MIN_TRADES,
    LEDGER_PATH,
    SymbolSearchResult,
    _resolve_cost_bps,
    aggregates_to_ledger_entries,
    build_fetch,
    default_hypotheses,
    summarize_universe,
)
from app.research.unlock_hypotheses import unlock_hypotheses

logger = logging.getLogger(__name__)

TIMEFRAME = "1h"
DEFAULT_HORIZONS = (24, 72, 168)  # bars = 1d/3d/7d; the unlock edge acts on ~72h–7d
MAX_LOOKBACK_DAYS = 730
_MS_PER_DAY = 86_400_000
DEFAULT_EVENTS = Path("artifacts/research/unlock_events.json")
UNLOCK_NAMES = frozenset(n for n, _ in unlock_hypotheses())
# Beta-neutral (drift-de-confounded) variants — the SAME unlock deciders under
# distinct names so they get their own ledger keys and run in the SAME BH-FDR
# batch against drift-de-meaned labels (confound control, app.research.confound).
BN_UNLOCK_HYPOTHESES = [(f"{n}_beta_neutral", fn) for n, fn in unlock_hypotheses()]
BN_UNLOCK_NAMES = frozenset(n for n, _ in BN_UNLOCK_HYPOTHESES)


def _load_events(path: Path) -> dict[str, dict[str, Any]]:
    """Load {symbol: {"events": [UnlockEvent], "max_supply": float|None}}; skip empties."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for sym, info in (doc.get("tokens") or {}).items():
        events = [UnlockEvent(int(ms), float(amt)) for ms, amt in (info.get("events") or [])]
        if not events:
            continue
        out[sym] = {"events": events, "max_supply": info.get("max_supply")}
    return out


def _unlock_timed(r: FeatureRow) -> bool:
    """Bar where upcoming unlock pressure is unusually high (z>1) — the timed set."""
    return r.unlock_frac_fwd_z is not None and r.unlock_frac_fwd_z > 1.0


async def run(
    lookback_days: int, horizons: tuple[int, ...], events_path: Path, min_trades: int
) -> int:
    from app.market_data.binance_adapter import BinanceAdapter

    if not events_path.exists():
        logger.error("events artifact missing: %s (run build_unlock_events.py first)", events_path)
        return 2

    tokens = _load_events(events_path)
    if not tokens:
        logger.error("no usable unlock tokens in %s", events_path)
        return 2
    lookback_days = max(1, min(lookback_days, MAX_LOOKBACK_DAYS))
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = end_ms - lookback_days * _MS_PER_DAY

    fetch = build_fetch(BinanceAdapter().get_ohlcv)
    cost_bps = _resolve_cost_bps()
    hypotheses = default_hypotheses() + unlock_hypotheses()

    # Backfill + build the feature matrix once per symbol (labels differ per horizon).
    per_symbol: dict[str, tuple[list[FeatureRow], list[float], int]] = {}
    for symbol, info in tokens.items():
        pair = f"{symbol}/USDT"
        try:
            history = await load_ohlcv_history(pair, TIMEFRAME, start_ms, end_ms, fetch)
        except Exception as exc:  # noqa: BLE001 — one missing listing must not kill the run
            logger.warning("%s backfill failed (%s); skipping", pair, exc)
            continue
        if not history.candles:
            logger.warning("%s: no candles; skipping", pair)
            continue
        rows = build_feature_matrix(
            history.candles,
            unlock_events=info["events"],
            unlock_max_supply=info["max_supply"],
        )
        closes = [c.close for c in history.candles]
        per_symbol[pair] = (rows, closes, history.gap_bars)
        n_z = sum(1 for r in rows if r.unlock_frac_fwd_z is not None)
        n_hot = sum(
            1 for r in rows if r.unlock_frac_fwd_z is not None and r.unlock_frac_fwd_z > 1.0
        )
        logger.info(
            "%s: %d candles, unlock_z defined=%d, z>1 bars=%d (events=%d)",
            pair,
            len(rows),
            n_z,
            n_hot,
            len(info["events"]),
        )

    if not per_symbol:
        logger.error("no symbols backfilled; aborting")
        return 2

    universe = tuple(sorted(per_symbol))
    confound = timing_alpha_report(
        per_symbol,
        max(horizons),
        cost_bps,
        timed=_unlock_timed,
        side=-1,
        label="unlock-timed short",
    )

    ledger = HypothesisLedger(LEDGER_PATH)
    as_of = datetime.fromtimestamp(end_ms / 1000, tz=UTC).isoformat()
    report_horizons: list[dict[str, Any]] = []
    any_unlock_survivor = False
    any_bn_survivor = False

    for horizon in horizons:
        results: list[SymbolSearchResult] = []
        for pair, (rows, closes, gap_bars) in per_symbol.items():
            labels = compute_forward_return_bps(closes, horizon)
            report = search_hypotheses(hypotheses, rows, labels, cost_bps, min_trades=min_trades)
            results.append(SymbolSearchResult(pair, len(rows), gap_bars, report))

        aggregates = summarize_universe(results)
        entries = aggregates_to_ledger_entries(
            aggregates,
            timeframe=TIMEFRAME,
            horizon=horizon,
            round_trip_cost_bps=cost_bps,
            universe=universe,
            min_trades=min_trades,
            alpha=0.05,
            as_of_utc=as_of,
            lookback_days=lookback_days,
            recorded_at_utc=as_of,
        )
        for entry in entries:
            ledger.record(entry)

        # Confound control: the SAME unlock deciders against DRIFT-DE-MEANED labels
        # in the same BH-FDR batch (honest cumulative trial count). A survivor here
        # beat the alt-beta drift rather than riding it. In-sample baseline ⇒ a
        # diagnostic, NOT a tradeable label (see app.research.confound).
        bn_results: list[SymbolSearchResult] = []
        for pair, (rows, closes, gap_bars) in per_symbol.items():
            bn_labels = beta_neutral_forward_returns(compute_forward_return_bps(closes, horizon))
            bn_report = search_hypotheses(
                BN_UNLOCK_HYPOTHESES, rows, bn_labels, cost_bps, min_trades=min_trades
            )
            bn_results.append(SymbolSearchResult(pair, len(rows), gap_bars, bn_report))
        bn_aggregates = summarize_universe(bn_results)
        for entry in aggregates_to_ledger_entries(
            bn_aggregates,
            timeframe=TIMEFRAME,
            horizon=horizon,
            round_trip_cost_bps=cost_bps,
            universe=universe,
            min_trades=min_trades,
            alpha=0.05,
            as_of_utc=as_of,
            lookback_days=lookback_days,
            recorded_at_utc=as_of,
        ):
            ledger.record(entry)
        bn_aggs = [a for a in bn_aggregates if a.name in BN_UNLOCK_NAMES]
        any_bn_survivor |= any(a.n_symbols_survived > 0 for a in bn_aggs)

        unlock_aggs = [a for a in aggregates if a.name in UNLOCK_NAMES]
        any_unlock_survivor |= any(a.n_symbols_survived > 0 for a in unlock_aggs)
        report_horizons.append(
            {
                "horizon": horizon,
                "unlock": [
                    {
                        "name": a.name,
                        "symbols_survived": a.n_symbols_survived,
                        "symbols_evaluated": a.n_symbols_evaluated,
                        "mean_net_bps": round(a.mean_net_bps, 3),
                        "total_trades": a.total_trades,
                    }
                    for a in unlock_aggs
                ],
                "unlock_beta_neutral": [
                    {
                        "name": a.name,
                        "symbols_survived": a.n_symbols_survived,
                        "symbols_evaluated": a.n_symbols_evaluated,
                        "mean_net_bps": round(a.mean_net_bps, 3),
                        "total_trades": a.total_trades,
                    }
                    for a in bn_aggs
                ],
            }
        )
        for a in (*unlock_aggs, *bn_aggs):
            logger.info(
                "h=%d %-30s survived=%d/%d mean_net=%+.2fbps trades=%d",
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
    out_path = out_dir / f"unlock_edge_search_{stamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "generated_at_utc": as_of,
                "timeframe": TIMEFRAME,
                "lookback_days": lookback_days,
                "universe": list(universe),
                "round_trip_cost_bps": cost_bps,
                "min_trades": min_trades,
                "hypotheses_tested_cumulative": ledger.tested_count(),
                "confound_check": {"horizon": max(horizons), "per_symbol": confound},
                "beta_neutral_control": {
                    "any_survivor": any_bn_survivor,
                    "in_sample_baseline": True,
                    "note": (
                        "Drift-de-meaned labels (confound diagnostic, NOT a tradeable "
                        "label). A survivor beat alt-beta, not just rode it; a tradeable "
                        "claim still needs an out-of-sample baseline + full validation gate."
                    ),
                },
                "horizons": report_horizons,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("wrote %s", out_path)
    logger.info(
        "VERDICT: raw=%s / beta-neutral=%s unlock survivor across %s horizons -- %s",
        ">=1" if any_unlock_survivor else "0",
        ">=1" if any_bn_survivor else "0",
        list(horizons),
        "beta-neutral candidate(s) to scrutinize"
        if any_bn_survivor
        else "no de-confounded unlock-pressure edge (valid $0 result; raw survivors, if any, are alt-beta)",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase-1 token-unlock pressure offline edge gate.")
    parser.add_argument("--lookback-days", type=int, default=730)
    parser.add_argument(
        "--horizons", default="24,72,168", help="comma-separated forward horizons (bars)"
    )
    parser.add_argument("--events", default=str(DEFAULT_EVENTS), help="unlock_events.json path")
    parser.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES)
    args = parser.parse_args()
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    return asyncio.run(run(args.lookback_days, horizons, Path(args.events), args.min_trades))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
