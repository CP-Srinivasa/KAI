"""Beta-neutral validation of the unlock-short survivor — is it edge or alt-beta?

The Phase-1 gate found unlock_imminent_short surviving BH-FDR @7d, but the confound
check showed it is mostly the 2024-26 alt-downtrend (always-short already profits).
This script answers the decisive question with the real promotion gate:

  1. DE-OVERLAP: a 7d (168-bar) hold makes adjacent hourly entries ~identical
     (massively autocorrelated → inflated n). Keep only entries spaced >= horizon
     apart → ~independent trades. The honest n.
  2. BETA-NEUTRALISE: per symbol, alpha_i = mean_fwd - fwd_i (short timing return in
     excess of the symbol's own unconditional forward return). >0 means unlock timing
     beats simply being short. This removes the alt-beta the confound check exposed.
  3. GATE: run the de-overlapped beta-neutral alpha through the doctrine's
     ``evaluate_edge_validation`` (DSR deflated by the honest cumulative trial count,
     MinTRL, n>=100, outlier-robust) + an autocorrelation-robust bootstrap p(mean>0).
  4. FUNDING CONTEXT: realized perp funding carry over each hold (a short earns
     positive funding), reported alongside — a real cost the OHLCV backtest omits.

Read-only, capital-free. ``evaluate_edge_validation`` is a promotion gate, never an
entry-path import. A "not ready" verdict (likely — unlock events are rare, so the
independent n is small) is the honest, valuable result: do not promote.

Run: python scripts/unlock_validation.py --horizon 168
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.analysis.features.feature_matrix import build_feature_matrix
from app.analysis.features.forward_returns import compute_forward_return_bps
from app.analysis.features.unlock_align import UnlockEvent
from app.market_data.history_loader import load_ohlcv_history
from app.observability.edge_validation_gate import evaluate_edge_validation
from app.observability.l2_evidence_eval import moving_block_bootstrap_p_mean_positive
from app.research.ledger import HypothesisLedger
from app.research.runner import (
    LEDGER_PATH,
    _resolve_cost_bps,
    build_fetch,
)

logger = logging.getLogger(__name__)

TIMEFRAME = "1h"
MAX_LOOKBACK_DAYS = 730
_MS_PER_DAY = 86_400_000
_MS_PER_BAR = 3_600_000  # 1h
DEFAULT_EVENTS = Path("artifacts/research/unlock_events.json")


def _iso_to_ms(ts: str) -> int | None:
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _funding_carry_bps(funding: list[tuple[int, float]], open_ms: int, horizon_ms: int) -> float:
    """Realized funding a SHORT earns over the hold: +sum(rate) for rate>0 (longs pay)."""
    upper = open_ms + horizon_ms
    return sum(rate for ms, rate in funding if open_ms < ms <= upper) * 10_000.0


def select_independent(indices: Sequence[int], min_spacing: int) -> list[int]:
    """Greedy left-to-right pick of ascending bar indices spaced >= ``min_spacing`` apart.

    A 7d (168-bar) hold makes adjacent hourly entries near-identical trades; keeping
    only entries at least one horizon apart yields ~independent holds, so n and the
    Sharpe deflation are honest instead of autocorrelation-inflated.
    """
    chosen: list[int] = []
    last = -(10**12)
    for i in indices:
        if i - last >= min_spacing:
            chosen.append(i)
            last = i
    return chosen


def _load_events(path: Path) -> dict[str, dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for sym, info in (doc.get("tokens") or {}).items():
        events = [UnlockEvent(int(ms), float(amt)) for ms, amt in (info.get("events") or [])]
        if events:
            out[sym] = {"events": events, "max_supply": info.get("max_supply")}
    return out


async def run(horizon: int, lookback_days: int, events_path: Path) -> int:
    from app.market_data.binance_adapter import BinanceAdapter
    from app.market_data.binance_futures_adapter import BinanceFuturesAdapter

    if not events_path.exists():
        logger.error("events artifact missing: %s", events_path)
        return 2
    tokens = _load_events(events_path)
    lookback_days = max(1, min(lookback_days, MAX_LOOKBACK_DAYS))
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = end_ms - lookback_days * _MS_PER_DAY
    horizon_ms = horizon * _MS_PER_BAR

    fetch = build_fetch(BinanceAdapter().get_ohlcv)
    fetch_funding = BinanceFuturesAdapter().get_funding_rate_history
    cost = _resolve_cost_bps()
    trials = HypothesisLedger(LEDGER_PATH).tested_count()
    logger.info("validation: horizon=%d bars, cost=%.1fbps, trials=%d", horizon, cost, trials)

    pooled_alpha: list[float] = []
    per_symbol: list[dict[str, Any]] = []

    for sym, info in tokens.items():
        pair = f"{sym}/USDT"
        try:
            history = await load_ohlcv_history(pair, TIMEFRAME, start_ms, end_ms, fetch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s backfill failed (%s)", pair, exc)
            continue
        if not history.candles:
            continue
        rows = build_feature_matrix(
            history.candles, unlock_events=info["events"], unlock_max_supply=info["max_supply"]
        )
        closes = [c.close for c in history.candles]
        labels = compute_forward_return_bps(closes, horizon)
        all_fwd = [x for x in labels if x is not None]
        if len(all_fwd) < 50:
            continue
        mean_fwd = sum(all_fwd) / len(all_fwd)

        # Timed shorts (z>1, label defined), then de-overlap to independent holds.
        timed = [
            (i, x)
            for i, r in enumerate(rows)
            if r.unlock_frac_fwd_z is not None
            and r.unlock_frac_fwd_z > 1.0
            and (x := labels[i]) is not None
        ]
        keep = set(select_independent([i for i, _ in timed], horizon))
        indep: list[tuple[int, float]] = [(i, lab) for i, lab in timed if i in keep]
        if not indep:
            continue

        alpha = [mean_fwd - lab for _i, lab in indep]  # beta-neutral short timing alpha (bps)
        raw_short_net = [(-lab - cost) for _i, lab in indep]  # absolute short net (bps)
        try:
            funding = await fetch_funding(pair, start_ms, end_ms)
        except Exception:  # noqa: BLE001 — funding is context, not the gate
            funding = []
        carries = [
            _funding_carry_bps(funding, _iso_to_ms(rows[i].timestamp_utc) or 0, horizon_ms)
            for i, _lab in indep
        ]

        pooled_alpha.extend(alpha)
        p_alpha = moving_block_bootstrap_p_mean_positive(alpha)
        verdict = evaluate_edge_validation(alpha, trials=trials, min_n=100)
        rec = {
            "symbol": pair,
            "n_timed_overlapping": len(timed),
            "n_independent": len(indep),
            "alpha_mean_bps": round(sum(alpha) / len(alpha), 1),
            "alpha_p_mean_positive": p_alpha,
            "raw_short_net_mean_bps": round(sum(raw_short_net) / len(raw_short_net), 1),
            "funding_carry_mean_bps": round(sum(carries) / len(carries), 1) if carries else None,
            "gate_ready": verdict.ready,
            "deflated_sharpe": verdict.deflated_sharpe,
        }
        per_symbol.append(rec)
        logger.info(
            "%-10s n_indep=%-3d alpha=%+.0fbps p=%s raw_short=%+.0f fund=%+.0f ready=%s (dsr=%s)",
            pair,
            len(indep),
            rec["alpha_mean_bps"],
            f"{p_alpha:.2f}" if p_alpha is not None else "n/a",
            rec["raw_short_net_mean_bps"],
            rec["funding_carry_mean_bps"] or 0.0,
            verdict.ready,
            f"{verdict.deflated_sharpe:.2f}" if verdict.deflated_sharpe else "n/a",
        )

    pooled_verdict = evaluate_edge_validation(pooled_alpha, trials=trials, min_n=100)
    pooled_p = moving_block_bootstrap_p_mean_positive(pooled_alpha) if pooled_alpha else None
    logger.info(
        "POOLED beta-neutral alpha: n=%d mean=%+.0fbps p=%s gate_ready=%s (dsr=%s, min_trl=%s)",
        len(pooled_alpha),
        (sum(pooled_alpha) / len(pooled_alpha)) if pooled_alpha else 0.0,
        f"{pooled_p:.3f}" if pooled_p is not None else "n/a",
        pooled_verdict.ready,
        f"{pooled_verdict.deflated_sharpe:.3f}" if pooled_verdict.deflated_sharpe else "n/a",
        f"{pooled_verdict.min_trl:.0f}" if pooled_verdict.min_trl else "n/a",
    )

    out_dir = Path("artifacts/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(end_ms / 1000, tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"unlock_validation_{stamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.fromtimestamp(end_ms / 1000, tz=UTC).isoformat(),
                "horizon_bars": horizon,
                "round_trip_cost_bps": cost,
                "trials": trials,
                "per_symbol": per_symbol,
                "pooled": pooled_verdict.to_dict() | {"p_mean_positive": pooled_p},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("wrote %s", out_path)
    ready_names = [r["symbol"] for r in per_symbol if r["gate_ready"]]
    logger.info(
        "VERDICT: %s -- %s",
        f"beta-neutral edge READY for {ready_names}"
        if ready_names
        else "NO beta-neutral unlock edge clears the gate",
        "pooled ready"
        if pooled_verdict.ready
        else "pooled NOT ready (honest: unlock events too rare for n>=100 robust edge)",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Beta-neutral validation of the unlock-short signal."
    )
    parser.add_argument("--horizon", type=int, default=168)
    parser.add_argument("--lookback-days", type=int, default=730)
    parser.add_argument("--events", default=str(DEFAULT_EVENTS))
    args = parser.parse_args()
    return asyncio.run(run(args.horizon, args.lookback_days, Path(args.events)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
