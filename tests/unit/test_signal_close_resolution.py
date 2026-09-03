"""STAB-2026-09-01 §5 — a closed paper position must never render as Open.

``_derive_premium_state`` read only the envelope audit and
``bridge_pending_orders.jsonl``. Bridge stage ``filled`` maps to ``POSITION_OPEN``
and nothing downstream ever revised it, so an envelope whose paper position had
long since been stopped out or taken profit kept rendering as
"Paper Position eröffnet" forever.

Measured on the live log: **67 envelopes** sat in the green Open cell while a
``position_closed`` event existed for them — the matrix showed Open = 73 against
3 genuinely open in the canonical trail, a 24x overstatement. Two read models
disagreed about the same envelopes, and the panel's own legend
("echte Position erst ab Open/Closed") pointed the operator at the wrong one.

The close events are now part of the same read. This makes the panel MORE red,
which is the correct direction and must not be softened.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.routers.signals import _load_close_index, _resolve_close
from app.premium.state_machine import PremiumSignalState

ENV_ID = "ENV-TVP-5db123d377794dee"


def _audit(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "paper_execution_audit.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return p


def _closed(reason: str, pnl: float | None = 12.5, corr: str = ENV_ID) -> dict:
    return {
        "event_type": "position_closed",
        "correlation_id": corr,
        "reason": reason,
        "trade_pnl_usd": pnl,
        "symbol": "ETH/USDT",
        "timestamp_utc": "2026-08-31T02:54:25.894894+00:00",
    }


# --------------------------------------------------------------------------
# POSITIVE CONTROL — the real regression
# --------------------------------------------------------------------------
def test_a_stopped_out_position_is_not_open(tmp_path: Path) -> None:
    """The exact live case: ENV-TVP-5db123d377794dee, filled 01:02Z, stopped 02:54Z."""
    idx = _load_close_index(_audit(tmp_path, [_closed("stop", -8.0)]))
    state = _resolve_close(PremiumSignalState.POSITION_OPEN, {"envelope_id": ENV_ID}, idx)
    assert state == PremiumSignalState.CLOSED_SL
    assert state != PremiumSignalState.POSITION_OPEN


@pytest.mark.parametrize(
    ("reason", "pnl", "expected"),
    [
        ("stop", -8.0, PremiumSignalState.CLOSED_SL),
        ("take", 21.0, PremiumSignalState.CLOSED_TP),
        ("manual", 3.0, PremiumSignalState.CLOSED_MANUAL),
        ("take", None, PremiumSignalState.CLOSED_UNKNOWN),
    ],
)
def test_close_reason_maps_to_the_right_terminal_state(
    tmp_path: Path, reason: str, pnl: float | None, expected: PremiumSignalState
) -> None:
    idx = _load_close_index(_audit(tmp_path, [_closed(reason, pnl)]))
    assert (
        _resolve_close(PremiumSignalState.POSITION_OPEN, {"envelope_id": ENV_ID}, idx) == expected
    )


def test_the_origin_envelope_id_also_resolves(tmp_path: Path) -> None:
    """Approved envelopes carry the close under their origin id."""
    idx = _load_close_index(_audit(tmp_path, [_closed("stop", -1.0)]))
    state = _resolve_close(
        PremiumSignalState.POSITION_OPEN,
        {"envelope_id": "ENV-OTHER", "origin_envelope_id": ENV_ID},
        idx,
    )
    assert state == PremiumSignalState.CLOSED_SL


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS — it must detect the event, not always return closed
# --------------------------------------------------------------------------
def test_without_a_close_event_the_position_stays_open(tmp_path: Path) -> None:
    """THE control that proves the test detects the close rather than the state.

    Same fixture minus the position_closed line.
    """
    idx = _load_close_index(_audit(tmp_path, []))
    state = _resolve_close(PremiumSignalState.POSITION_OPEN, {"envelope_id": ENV_ID}, idx)
    assert state == PremiumSignalState.POSITION_OPEN


def test_a_close_for_a_different_envelope_does_not_leak(tmp_path: Path) -> None:
    idx = _load_close_index(_audit(tmp_path, [_closed("stop", -1.0, corr="ENV-SOMEONE-ELSE")]))
    assert (
        _resolve_close(PremiumSignalState.POSITION_OPEN, {"envelope_id": ENV_ID}, idx)
        == PremiumSignalState.POSITION_OPEN
    )


def test_a_partial_close_does_not_terminate_the_position(tmp_path: Path) -> None:
    """A tp_tier partial leaves the position open; only a full close overturns."""
    partial = {
        "event_type": "position_partial_closed",
        "correlation_id": ENV_ID,
        "reason": "tp_tier",
        "trade_pnl_usd": 5.0,
    }
    idx = _load_close_index(_audit(tmp_path, [partial]))
    assert idx == {}
    assert (
        _resolve_close(PremiumSignalState.POSITION_OPEN, {"envelope_id": ENV_ID}, idx)
        == PremiumSignalState.POSITION_OPEN
    )


@pytest.mark.parametrize(
    "state",
    [
        PremiumSignalState.PARSED,
        PremiumSignalState.APPROVED,
        PremiumSignalState.BRIDGE_REJECTED,
        PremiumSignalState.REQUIRES_REVIEW,
        PremiumSignalState.CLOSED_TP,
    ],
)
def test_non_open_states_are_never_rewritten(tmp_path: Path, state: PremiumSignalState) -> None:
    """A close event must not resurrect or relabel a state that was never Open."""
    idx = _load_close_index(_audit(tmp_path, [_closed("stop", -1.0)]))
    assert _resolve_close(state, {"envelope_id": ENV_ID}, idx) == state


def test_a_missing_audit_file_leaves_states_untouched(tmp_path: Path) -> None:
    """Fail-safe: no audit must not silently close everything."""
    idx = _load_close_index(tmp_path / "does_not_exist.jsonl")
    assert idx == {}
    assert (
        _resolve_close(PremiumSignalState.POSITION_OPEN, {"envelope_id": ENV_ID}, idx)
        == PremiumSignalState.POSITION_OPEN
    )


def test_a_corrupt_line_does_not_break_the_index(tmp_path: Path) -> None:
    p = tmp_path / "paper_execution_audit.jsonl"
    p.write_text("not json\n" + json.dumps(_closed("stop", -1.0)) + "\n", encoding="utf-8")
    idx = _load_close_index(p)
    assert ENV_ID in idx


def test_the_latest_close_wins(tmp_path: Path) -> None:
    idx = _load_close_index(_audit(tmp_path, [_closed("stop", -1.0), _closed("take", 9.0)]))
    assert (
        _resolve_close(PremiumSignalState.POSITION_OPEN, {"envelope_id": ENV_ID}, idx)
        == PremiumSignalState.CLOSED_TP
    )
