"""Tests for the paper-fill quality snapshot concentration/drawdown additions.

DS-20260528-V4: covers the pure functions (max drawdown, concentration) and the
dust-closure exclusion in the report builder.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# The snapshot lives under scripts/ (not a package) — load it by path.
_SPEC = importlib.util.spec_from_file_location(
    "paper_fill_quality_snapshot",
    Path(__file__).resolve().parents[2] / "scripts" / "paper_fill_quality_snapshot.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)

max_drawdown_from_peak = _mod.max_drawdown_from_peak
concentration = _mod.concentration
_build_report = _mod._build_report


# ── max_drawdown_from_peak ───────────────────────────────────────────────────


def test_drawdown_empty_series() -> None:
    out = max_drawdown_from_peak([])
    assert out["max_drawdown_usd"] == 0.0
    assert out["peak_usd"] is None


def test_drawdown_monotonic_increase_is_zero() -> None:
    out = max_drawdown_from_peak([100.0, 200.0, 300.0])
    assert out["max_drawdown_usd"] == 0.0


def test_drawdown_peak_to_trough() -> None:
    # peak 4853, trough 3797 → DD 1056 (the real 2026-05-28 shape).
    series = [1608.0, 4845.0, 4853.0, 4714.0, 3813.0, 3797.0]
    out = max_drawdown_from_peak(series)
    assert out["max_drawdown_usd"] == 1056.0
    assert out["peak_usd"] == 4853.0
    assert out["trough_usd"] == 3797.0


def test_drawdown_recovers_then_drops_again_takes_max() -> None:
    # DD1 = 50 (100->50), recover to 120, DD2 = 90 (120->30) → max 90.
    series = [100.0, 50.0, 120.0, 30.0]
    out = max_drawdown_from_peak(series)
    assert out["max_drawdown_usd"] == 90.0


# ── concentration ────────────────────────────────────────────────────────────


def test_concentration_empty() -> None:
    out = concentration([])
    assert out["n_trades"] == 0
    assert out["top1_abs_share_pct"] == 0.0


def test_concentration_uses_absolute_pnl() -> None:
    # One huge loss dominates magnitude even though net could be small.
    out = concentration([5643.0, -2406.0, 3.0, 1.0, -2.0])
    assert out["n_trades"] == 5
    # gross = 5643+2406+3+1+2 = 8055; top1 = 5643 → 70.1%
    assert out["top1_abs_share_pct"] == 70.1
    # top3 = 5643+2406+3 = 8052 → 99.96 → 100.0
    assert out["top3_abs_share_pct"] == 100.0


def test_concentration_even_spread() -> None:
    out = concentration([10.0, 10.0, 10.0, 10.0])
    assert out["top1_abs_share_pct"] == 25.0


# ── dust exclusion in _build_report ──────────────────────────────────────────


def _closure(symbol: str, qty: float, pnl: float, cum: float) -> dict[str, object]:
    return {
        "event_type": "position_closed",
        "timestamp_utc": "2026-05-28T00:00:00+00:00",
        "symbol": symbol,
        "reason": "stop",
        "quantity": qty,
        "trade_pnl_usd": pnl,
        "realized_pnl_usd": cum,
    }


def test_build_report_excludes_dust_closures() -> None:
    closures = [
        _closure("BTC/USDT", 0.02, 100.0, 100.0),
        _closure("ETH/USDT", 1e-16, 0.0, 100.0),  # dust
        _closure("BTC/USDT", 0.01, -40.0, 60.0),
    ]
    report = _build_report(closures)
    totals = report["totals"]
    assert totals["closures"] == 2  # dust excluded from the count
    assert totals["dust_excluded"] == 1
    assert report["concentration"]["n_trades"] == 2


def test_build_report_concentration_and_drawdown_present() -> None:
    closures = [
        _closure("BTC/USDT", 0.02, 100.0, 100.0),
        _closure("BTC/USDT", 0.02, -60.0, 40.0),
    ]
    report = _build_report(closures)
    assert "concentration" in report
    assert "drawdown" in report
    assert report["drawdown"]["max_drawdown_usd"] == 60.0  # 100 → 40
