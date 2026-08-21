"""Turn a directional hypothesis into net-bps trade samples — pure function.

A *hypothesis* is a decider: ``FeatureRow -> side`` where side is +1 (long),
-1 (short), or 0 (no trade). Given the aligned forward-return labels and a
round-trip cost, ``decisions_to_trades`` produces one :class:`TradeSample` per
taken trade:

    gross_bps = side * forward_return_bps
    net_bps   = gross_bps - round_trip_cost_bps

Rows where the decider says 0, or where the forward label is unavailable
(``None``, NaN, or either infinity), produce no trade. The decider reads only the
FeatureRow (which is causal by construction); the label is consumed here, never
exposed to the decider — preserving the feature/label separation that keeps the
backtest honest.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from typing import Literal

from app.analysis.features.feature_matrix import FeatureRow

Decider = Callable[[FeatureRow], int]


@dataclass(frozen=True)
class TradeSample:
    """One realized hypothetical trade."""

    timestamp_utc: str
    side: int  # +1 long, -1 short
    gross_bps: float
    net_bps: float


@dataclass(frozen=True)
class DecisionCounts:
    """Was aus den Feuerungen wurde — die Offenlegung, die n_valid erst ehrlich macht.

    Eine Feuerung ohne auswertbares Label ist NICHT dasselbe wie "kein Signal":
    die Regel hat gefeuert, nur war das Ergebnis nicht beobachtbar. Wer beides
    zusammenwirft, meldet ein n_valid, das eine andere Groesse ist als die, die
    es zu sein vorgibt.
    """

    raw_fires: int
    label_capable_fires: int
    data_unavailable: int


_LabelStatus = Literal["VALID", "DATA_UNAVAILABLE"]


@dataclass(frozen=True)
class _RowDecision:
    """One immutable per-row result shared by trade emission and disclosure."""

    decision: int
    label_status: _LabelStatus
    trade_sample: TradeSample | None


def decisions_to_trades_with_counts(
    rows: list[FeatureRow],
    forward_bps: list[float | None],
    decide: Decider,
    round_trip_cost_bps: float,
) -> tuple[list[TradeSample], DecisionCounts]:
    """Wie ``decisions_to_trades``, plus die Zaehlung der nicht auswertbaren Feuerungen.

    Getrennte Funktion statt geaenderter Signatur, damit die bestehenden Aufrufer
    unberuehrt bleiben. Der Decider und die Kostenarithmetik laufen pro Zeile
    genau einmal; Trades und Counts werden aus demselben Zwischenergebnis erzeugt.
    """
    outcomes = _emit_trades(rows, forward_bps, decide, round_trip_cost_bps)
    trades = [outcome.trade_sample for outcome in outcomes if outcome.trade_sample is not None]
    raw = sum(outcome.decision != 0 for outcome in outcomes)
    unavailable = sum(
        outcome.decision != 0 and outcome.label_status == "DATA_UNAVAILABLE" for outcome in outcomes
    )
    return trades, DecisionCounts(
        raw_fires=raw,
        label_capable_fires=len(trades),
        data_unavailable=unavailable,
    )


def decisions_to_trades(
    rows: list[FeatureRow],
    forward_bps: list[float | None],
    decide: Decider,
    round_trip_cost_bps: float,
) -> list[TradeSample]:
    """Apply a decider to a labeled feature matrix and emit net-bps trades.

    Args:
        rows: causal feature rows (oldest first).
        forward_bps: forward-return labels aligned to ``rows`` (non-finite = unavailable).
        decide: hypothesis mapping a row to side in {-1, 0, +1}.
        round_trip_cost_bps: total cost charged per taken trade. Must be numeric, finite, and >= 0.

    Returns:
        One TradeSample per row where side != 0 and a finite label exists.

    Raises:
        ValueError: length mismatch, invalid cost, or a side not in {-1,0,1}.
    """
    outcomes = _emit_trades(rows, forward_bps, decide, round_trip_cost_bps)
    return [outcome.trade_sample for outcome in outcomes if outcome.trade_sample is not None]


def _emit_trades(
    rows: list[FeatureRow],
    forward_bps: list[float | None],
    decide: Decider,
    round_trip_cost_bps: float,
) -> list[_RowDecision]:
    """Evaluate each row once and retain the result used by every downstream view."""
    if len(rows) != len(forward_bps):
        raise ValueError("rows and forward_bps must have equal length")
    if (
        isinstance(round_trip_cost_bps, bool)
        or not isinstance(round_trip_cost_bps, (int, float))
        or not isfinite(round_trip_cost_bps)
        or round_trip_cost_bps < 0
    ):
        raise ValueError("round_trip_cost_bps must be numeric, finite, and >= 0")

    outcomes: list[_RowDecision] = []
    for row, label in zip(rows, forward_bps, strict=True):
        side = decide(row)
        if isinstance(side, bool) or not isinstance(side, int) or side not in (-1, 0, 1):
            raise ValueError(f"decider must return integer -1, 0, or 1; got {side!r}")

        finite_label = label if label is not None and isfinite(label) else None
        label_status: _LabelStatus = "VALID" if finite_label is not None else "DATA_UNAVAILABLE"
        trade: TradeSample | None = None
        if side != 0 and finite_label is not None:
            gross = side * finite_label
            trade = TradeSample(
                timestamp_utc=row.timestamp_utc,
                side=side,
                gross_bps=gross,
                net_bps=gross - round_trip_cost_bps,
            )

        outcomes.append(
            _RowDecision(
                decision=side,
                label_status=label_status,
                trade_sample=trade,
            )
        )
    return outcomes
