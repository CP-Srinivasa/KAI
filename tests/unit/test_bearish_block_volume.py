"""Tests for the bearish block-volume report (DS-20260528-V5).

Covers the pure aggregation: window filtering, reason filtering, priority
bucketing, per-day counts and confidence stats.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "bearish_block_volume",
    Path(__file__).resolve().parents[2] / "scripts" / "bearish_block_volume.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)

aggregate_bearish_blocks = _mod.aggregate_bearish_blocks
priority_bucket = _mod.priority_bucket
window_start = _mod.window_start


def _block(reason: str, day: str, priority: int = 10, conf: float | None = 0.8) -> dict:
    return {
        "block_reason": reason,
        "blocked_at": f"{day}T12:00:00+00:00",
        "priority": priority,
        "directional_confidence": conf,
        "source_name": "beincrypto",
        "normalized_title": f"headline {day}",
    }


def test_priority_bucket() -> None:
    assert priority_bucket(10) == "p>=10"
    assert priority_bucket(8) == "p=8/9"
    assert priority_bucket(5) == "p<8"
    assert priority_bucket(None) == "unknown"


def test_window_start() -> None:
    assert window_start(date(2026, 5, 28), 14) == date(2026, 5, 14)


def test_aggregate_filters_reason_and_window() -> None:
    today = date(2026, 5, 28)
    records = [
        _block("bearish_directional_disabled", "2026-05-20"),  # in
        _block("bearish_directional_disabled", "2026-05-27"),  # in
        _block("not_actionable", "2026-05-27"),  # wrong reason
        _block("bearish_directional_disabled", "2026-05-01"),  # out of 14d window
    ]
    agg = aggregate_bearish_blocks(records, today, days=14)
    assert agg["total_bearish_blocks"] == 2
    assert agg["per_day"] == {"2026-05-20": 1, "2026-05-27": 1}


def test_aggregate_priority_and_confidence() -> None:
    today = date(2026, 5, 28)
    records = [
        _block("bearish_directional_disabled", "2026-05-25", priority=10, conf=0.9),
        _block("bearish_directional_disabled", "2026-05-25", priority=8, conf=0.5),
        _block("bearish_directional_disabled", "2026-05-26", priority=10, conf=None),
    ]
    agg = aggregate_bearish_blocks(records, today, days=14)
    assert agg["by_priority"] == {"p>=10": 2, "p=8/9": 1}
    conf = agg["confidence"]
    assert conf["n_with_confidence"] == 2  # None excluded
    assert conf["ge_0_7"] == 1  # only 0.9
    assert agg["blocks_per_day_avg"] == round(3 / 14, 2)


def test_aggregate_empty() -> None:
    agg = aggregate_bearish_blocks([], date(2026, 5, 28), days=14)
    assert agg["total_bearish_blocks"] == 0
    assert agg["confidence"]["mean"] is None
    assert agg["top_samples"] == []
