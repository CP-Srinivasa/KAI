"""Turn a directional hypothesis into net-bps trade samples — pure function.

A *hypothesis* is a decider: ``FeatureRow -> side`` where side is +1 (long),
-1 (short), or 0 (no trade). Given the aligned forward-return labels and a
round-trip cost, ``decisions_to_trades`` produces one :class:`TradeSample` per
taken trade:

    gross_bps = side * forward_return_bps
    net_bps   = gross_bps - round_trip_cost_bps

Rows where the decider says 0, or where the forward label is None (warm-up tail,
no future bar), produce no trade. The decider reads only the FeatureRow (which is
causal by construction); the label is consumed here, never exposed to the
decider — preserving the feature/label separation that keeps the backtest honest.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.analysis.features.feature_matrix import FeatureRow

Decider = Callable[[FeatureRow], int]


@dataclass(frozen=True)
class TradeSample:
    """One realized hypothetical trade."""

    timestamp_utc: str
    side: int  # +1 long, -1 short
    gross_bps: float
    net_bps: float


def decisions_to_trades(
    rows: list[FeatureRow],
    forward_bps: list[float | None],
    decide: Decider,
    round_trip_cost_bps: float,
) -> list[TradeSample]:
    """Apply a decider to a labeled feature matrix and emit net-bps trades.

    Args:
        rows: causal feature rows (oldest first).
        forward_bps: forward-return labels aligned to ``rows`` (None = no label).
        decide: hypothesis mapping a row to side in {-1, 0, +1}.
        round_trip_cost_bps: total cost charged per taken trade. Must be >= 0.

    Returns:
        One TradeSample per row where side != 0 and a label exists.

    Raises:
        ValueError: length mismatch, negative cost, or a side not in {-1,0,1}.
    """
    if len(rows) != len(forward_bps):
        raise ValueError("rows and forward_bps must have equal length")
    if round_trip_cost_bps < 0:
        raise ValueError("round_trip_cost_bps must be >= 0")

    trades: list[TradeSample] = []
    for row, label in zip(rows, forward_bps, strict=True):
        side = decide(row)
        if side not in (-1, 0, 1):
            raise ValueError(f"decider must return -1, 0, or 1; got {side!r}")
        if side == 0 or label is None:
            continue
        gross = side * label
        trades.append(
            TradeSample(
                timestamp_utc=row.timestamp_utc,
                side=side,
                gross_bps=gross,
                net_bps=gross - round_trip_cost_bps,
            )
        )
    return trades
