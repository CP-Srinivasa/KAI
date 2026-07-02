"""Unit tests for read-only open-position risk classification (Blocker #3/#4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.observability.position_risk import (
    RISK_NO,
    RISK_OPEN,
    RISK_UNKNOWN,
    build_positions_risk_snapshot,
    classify_position,
)

NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)


def _pos(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "DOT/USDT",
        "quantity": 810.0,
        "avg_entry_price": 1.2106,
        "market_price": 1.12,
        "market_data_available": True,
        "market_data_is_stale": False,
        "position_side": "long",
        "source": "loop",
        "stop_loss": 1.0,
        "opened_at": (NOW - timedelta(hours=5)).isoformat(),
    }
    base.update(overrides)
    return base


def test_long_open_loss_is_risk_open() -> None:
    out = classify_position(_pos(), loss_threshold_pct=1.0, now=NOW)
    assert out["risk_status"] == RISK_OPEN
    assert out["unrealized_pnl_usd"] is not None
    assert out["unrealized_pnl_usd"] < 0
    assert out["unrealized_pnl_pct"] < -1.0
    assert out["age_seconds"] == 5 * 3600


def test_long_in_profit_is_no_risk() -> None:
    out = classify_position(_pos(market_price=1.30), loss_threshold_pct=1.0, now=NOW)
    assert out["risk_status"] == RISK_NO
    assert out["unrealized_pnl_usd"] > 0


def test_small_loss_within_threshold_is_no_risk() -> None:
    # ~ -0.5% loss, threshold 1% -> no_risk
    out = classify_position(_pos(market_price=1.2046), loss_threshold_pct=1.0, now=NOW)
    assert out["risk_status"] == RISK_NO


def test_short_in_profit_when_price_drops() -> None:
    out = classify_position(
        _pos(position_side="short", market_price=1.12), loss_threshold_pct=1.0, now=NOW
    )
    assert out["risk_status"] == RISK_NO
    assert out["unrealized_pnl_usd"] > 0


def test_short_open_loss_when_price_rises() -> None:
    out = classify_position(
        _pos(position_side="short", market_price=1.40), loss_threshold_pct=1.0, now=NOW
    )
    assert out["risk_status"] == RISK_OPEN
    assert out["unrealized_pnl_usd"] < 0


def test_unavailable_price_is_data_unknown() -> None:
    out = classify_position(
        _pos(market_data_available=False, market_price=None),
        loss_threshold_pct=1.0,
        now=NOW,
    )
    assert out["risk_status"] == RISK_UNKNOWN
    assert out["unrealized_pnl_usd"] is None


def test_stale_price_is_data_unknown() -> None:
    out = classify_position(_pos(market_data_is_stale=True), loss_threshold_pct=1.0, now=NOW)
    assert out["risk_status"] == RISK_UNKNOWN


class _FakePos:
    def __init__(self, d: dict[str, object]) -> None:
        self._d = d

    def to_json_dict(self) -> dict[str, object]:
        return self._d


class _FakeSnapshot:
    def __init__(self, positions: list[dict[str, object]]) -> None:
        self.positions = tuple(_FakePos(p) for p in positions)
        self.execution_enabled = False
        self.available = True


def test_snapshot_overall_risk_open_when_any_bleeds() -> None:
    snap = _FakeSnapshot([_pos(), _pos(symbol="ETH/USDT", market_price=1.30)])
    report = build_positions_risk_snapshot(snap, entry_mode="disabled", now=NOW)
    assert report["overall_risk_status"] == RISK_OPEN
    assert report["risk_open_count"] == 1
    assert report["position_count"] == 2
    assert report["entry_mode"] == "disabled"
    assert report["execution_enabled"] is False


def test_snapshot_unknown_dominates_when_no_bleed() -> None:
    snap = _FakeSnapshot(
        [
            _pos(market_price=1.30),
            _pos(symbol="X/USDT", market_data_available=False, market_price=None),
        ]
    )
    report = build_positions_risk_snapshot(snap, entry_mode="disabled", now=NOW)
    assert report["overall_risk_status"] == RISK_UNKNOWN
    assert report["data_unknown_count"] == 1


def test_snapshot_no_risk_when_all_flat_or_profit() -> None:
    snap = _FakeSnapshot([_pos(market_price=1.30), _pos(symbol="ETH/USDT", market_price=1.25)])
    report = build_positions_risk_snapshot(snap, entry_mode="paper", now=NOW)
    assert report["overall_risk_status"] == RISK_NO
    assert report["risk_open_count"] == 0
    assert report["data_unknown_count"] == 0
