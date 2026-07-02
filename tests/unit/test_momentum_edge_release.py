"""Tests for momentum_edge_release (G5) — cohort edge → release recommendation.

Reuses the edge_report cohort aggregation + edge_release_policy.decide_release on
the ``momentum_universe`` cohort. Honest gating: no cohort closes → unavailable;
below ``min_n`` → DISABLED (no defensible posterior). A live recommendation can
never be emitted without operator sign-off — this module only RECOMMENDS.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.risk.momentum_edge_release import build_momentum_release


def _close(symbol: str, src: str, entry: float, exit_px: float, ts: str) -> dict[str, Any]:
    return {
        "event_type": "position_closed",
        "symbol": symbol,
        "signal_source": src,
        "position_side": "long",
        "entry_price": entry,
        "exit_price": exit_px,
        "quantity": 1.0,
        "timestamp_utc": ts,
        "trade_pnl_usd": exit_px - entry,
        "reason": "tp",
    }


def _write_audit(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


class TestBuildMomentumRelease:
    def test_no_cohort_closes_unavailable(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        _write_audit(
            audit, [_close("BTC/USDT", "telegram_premium", 100.0, 101.0, "2026-06-26T01:00:00Z")]
        )
        res = build_momentum_release(audit, min_n=30)
        assert res["available"] is False
        assert res["reason"] == "no_cohort_closes"
        assert res["cohort"] == "momentum_universe"

    def test_insufficient_n_is_disabled(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        rows = [
            _close(
                "BTC/USDT", "momentum_universe", 100.0, 101.0 + i, f"2026-06-{i + 1:02d}T01:00:00Z"
            )
            for i in range(5)
        ]
        _write_audit(audit, rows)
        res = build_momentum_release(audit, min_n=30)
        assert res["available"] is True
        assert res["cohort"] == "momentum_universe"
        assert res["resolved"] == 5
        # n=5 < min_n=30 → no defensible posterior → DISABLED, no live sign-off.
        assert res["recommended_mode"] == "disabled"
        assert res["count"] == 5
        assert res["requires_operator_signoff"] is False

    def test_only_cohort_trades_counted(self, tmp_path: Path) -> None:
        audit = tmp_path / "audit.jsonl"
        _write_audit(
            audit,
            [
                _close("BTC/USDT", "momentum_universe", 100.0, 102.0, "2026-06-01T01:00:00Z"),
                _close("ETH/USDT", "telegram_premium", 100.0, 90.0, "2026-06-01T01:00:00Z"),
                _close("SOL/USDT", "momentum_universe", 100.0, 101.0, "2026-06-02T01:00:00Z"),
            ],
        )
        res = build_momentum_release(audit, min_n=30)
        assert res["resolved"] == 2  # only the two momentum_universe closes
