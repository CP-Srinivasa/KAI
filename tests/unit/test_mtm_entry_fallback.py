"""C-Fix 2026-06-13: display-only entry-cost Mark-to-Market fallback for symbols
the price provider does not list (Bybit microcaps: SKYAI, COAI, …).

Invariant under test: the fallback gives the Portfolio view a number instead of
a blank, WITHOUT loosening the fail-closed gate fields — position_risk must
still classify an unpriced position as RISK_UNKNOWN.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.execution.portfolio_read import build_portfolio_snapshot
from app.market_data.models import MarketDataSnapshot
from app.observability.position_risk import RISK_UNKNOWN, classify_position


def _write_audit(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _audit_rows() -> list[dict[str, object]]:
    return [
        {"event_type": "order_created", "order_id": "o1", "symbol": "BTC/USDT"},
        {
            "event_type": "order_filled",
            "order_id": "o1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "quantity": 0.2,
            "fill_price": 50000.0,
            "filled_at": "2026-06-13T10:00:00+00:00",
            "portfolio_cash": 9000.0,
            "realized_pnl_usd": 0.0,
        },
        {"event_type": "order_created", "order_id": "o2", "symbol": "SKYAI/USDT"},
        {
            "event_type": "order_filled",
            "order_id": "o2",
            "symbol": "SKYAI/USDT",
            "side": "buy",
            "quantity": 4000.0,
            "fill_price": 0.325,
            "filled_at": "2026-06-13T10:01:00+00:00",
            "portfolio_cash": 7700.0,
            "realized_pnl_usd": 0.0,
        },
    ]


def _snapshot(symbol: str, price: float | None) -> MarketDataSnapshot:
    return MarketDataSnapshot(
        symbol=symbol,
        provider="coingecko",
        retrieved_at_utc="2026-06-13T12:00:00+00:00",
        source_timestamp_utc=("2026-06-13T11:59:00+00:00" if price is not None else None),
        price=price,
        is_stale=False,
        freshness_seconds=(60.0 if price is not None else None),
        available=price is not None,
        error=(None if price is not None else "symbol_not_listed"),
    )


@pytest.mark.asyncio
async def test_unlisted_symbol_gets_entry_fallback_display(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_audit(audit, _audit_rows())

    async def fake_md(**kwargs):  # noqa: ANN003
        sym = kwargs["symbol"]
        # BTC priced; SKYAI unlistable (provider returns no price)
        return _snapshot(sym, 60000.0 if sym == "BTC/USDT" else None)

    monkeypatch.setattr("app.execution.portfolio_read.get_market_data_snapshot", fake_md)

    snap = await build_portfolio_snapshot(audit_path=audit)
    by = {p.symbol: p for p in snap.positions}

    btc = by["BTC/USDT"]
    assert btc.mark_basis == "live"
    assert btc.market_value_usd == pytest.approx(12000.0)
    assert btc.display_value_usd == pytest.approx(12000.0)

    sky = by["SKYAI/USDT"]
    # gate-relevant fields stay None/False — fail-closed preserved
    assert sky.market_price is None
    assert sky.market_value_usd is None
    assert sky.unrealized_pnl_usd is None
    assert sky.market_data_available is False
    # display-only fallback = entry cost (4000 × 0.325)
    assert sky.mark_basis == "entry_fallback"
    assert sky.display_value_usd == pytest.approx(1300.0)


@pytest.mark.asyncio
async def test_entry_fallback_does_not_loosen_risk_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: the fallback must NOT make an unpriced position look
    healthy to the gate. position_risk reads market_price/available, which stay
    None/False, so the position is RISK_UNKNOWN (→ promotion_gate fail-closed)."""
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_audit(audit, _audit_rows())

    async def fake_md(**kwargs):  # noqa: ANN003
        sym = kwargs["symbol"]
        return _snapshot(sym, 60000.0 if sym == "BTC/USDT" else None)

    monkeypatch.setattr("app.execution.portfolio_read.get_market_data_snapshot", fake_md)

    snap = await build_portfolio_snapshot(audit_path=audit)
    sky = next(p for p in snap.positions if p.symbol == "SKYAI/USDT")

    classified = classify_position(
        sky.to_json_dict(), loss_threshold_pct=1.0, now=datetime.now(UTC)
    )
    assert classified["risk_status"] == RISK_UNKNOWN
    assert classified["unrealized_pnl_usd"] is None
