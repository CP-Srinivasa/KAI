"""Unit tests for canonical read-only paper portfolio surfaces (Sprint 40)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.portfolio_read import (
    build_exposure_summary,
    build_portfolio_snapshot,
    build_positions_summary,
    compute_realized_by_asset,
)
from app.market_data.models import MarketDataSnapshot


def _write_audit(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _market_snapshot(
    *,
    symbol: str,
    price: float | None,
    is_stale: bool,
    available: bool,
    error: str | None,
) -> MarketDataSnapshot:
    return MarketDataSnapshot(
        symbol=symbol,
        provider="coingecko",
        retrieved_at_utc="2026-03-21T12:00:00+00:00",
        source_timestamp_utc=("2026-03-21T11:59:00+00:00" if price is not None else None),
        price=price,
        is_stale=is_stale,
        freshness_seconds=(60.0 if price is not None else None),
        available=available,
        error=error,
    )


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_empty_portfolio(tmp_path: Path) -> None:
    snapshot = await build_portfolio_snapshot(audit_path=tmp_path / "missing.jsonl")

    assert snapshot.position_count == 0
    assert snapshot.positions == ()
    assert snapshot.available is True
    assert snapshot.error is None
    assert snapshot.execution_enabled is False
    assert snapshot.write_back_allowed is False

    positions_payload = build_positions_summary(snapshot)
    exposure_payload = build_exposure_summary(snapshot)
    assert positions_payload["report_type"] == "paper_positions_summary"
    assert exposure_payload["report_type"] == "paper_exposure_summary"
    assert positions_payload["execution_enabled"] is False
    assert exposure_payload["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_open_positions_and_exposure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    _write_audit(
        audit_path,
        [
            {
                "event_type": "order_created",
                "order_id": "ord_1",
                "symbol": "BTC/USDT",
                "stop_loss": 48000.0,
                "take_profit": 70000.0,
            },
            {
                "event_type": "order_filled",
                "order_id": "ord_1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": 0.2,
                "fill_price": 50000.0,
                "filled_at": "2026-03-21T10:00:00+00:00",
                "portfolio_cash": 9000.0,
                "realized_pnl_usd": 0.0,
            },
            {
                "event_type": "order_created",
                "order_id": "ord_2",
                "symbol": "ETH/USDT",
            },
            {
                "event_type": "order_filled",
                "order_id": "ord_2",
                "symbol": "ETH/USDT",
                "side": "buy",
                "quantity": 1.0,
                "fill_price": 3000.0,
                "filled_at": "2026-03-21T10:01:00+00:00",
                "portfolio_cash": 5800.0,
                "realized_pnl_usd": 0.0,
            },
        ],
    )

    async def fake_market_data_snapshot(**kwargs):  # noqa: ANN003
        symbol = kwargs["symbol"]
        if symbol == "BTC/USDT":
            return _market_snapshot(
                symbol=symbol,
                price=60000.0,
                is_stale=False,
                available=True,
                error=None,
            )
        return _market_snapshot(
            symbol=symbol,
            price=3200.0,
            is_stale=False,
            available=True,
            error=None,
        )

    monkeypatch.setattr(
        "app.execution.portfolio_read.get_market_data_snapshot",
        fake_market_data_snapshot,
    )

    snapshot = await build_portfolio_snapshot(audit_path=audit_path)

    assert snapshot.position_count == 2
    assert snapshot.available is True
    assert snapshot.error is None
    assert snapshot.exposure_summary.mark_to_market_status == "ok"
    assert snapshot.exposure_summary.gross_exposure_usd == pytest.approx(15200.0)
    assert snapshot.total_market_value_usd == pytest.approx(15200.0)
    assert snapshot.total_equity_usd == pytest.approx(21000.0)

    btc = next(position for position in snapshot.positions if position.symbol == "BTC/USDT")
    assert btc.market_value_usd == pytest.approx(12000.0)
    assert btc.unrealized_pnl_usd == pytest.approx(2000.0)
    assert btc.stop_loss == pytest.approx(48000.0)
    assert btc.take_profit == pytest.approx(70000.0)


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_short_is_liability_not_double_counted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the 2026-06 equity-swing bug (20k→60k→20k).

    A SHORT in profit must (1) show POSITIVE unrealized PnL (green), (2) be a
    liability in equity (cash + long - short), NOT added on top of the cash that
    already holds the sale proceeds. Mirrors the engine SSOT
    PaperPortfolio.total_equity / PaperPosition.unrealized_pnl.
    """
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    _write_audit(
        audit_path,
        [
            # long BTC
            {
                "event_type": "order_filled",
                "order_id": "ord_1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": 0.2,
                "fill_price": 50000.0,
                "fee_usd": 10.0,
                "filled_at": "2026-06-24T10:00:00+00:00",
                "portfolio_cash": 9990.0,
                "realized_pnl_usd": 0.0,
            },
            # short ETH (sell to open) — proceeds increase cash
            {
                "event_type": "order_filled",
                "order_id": "ord_2",
                "symbol": "ETH/USDT",
                "side": "sell",
                "position_side": "short",
                "quantity": 2.0,
                "fill_price": 3000.0,
                "fee_usd": 6.0,
                "filled_at": "2026-06-24T10:01:00+00:00",
                "portfolio_cash": 15984.0,
                "realized_pnl_usd": 0.0,
            },
        ],
    )

    async def fake_market_data_snapshot(**kwargs):  # noqa: ANN003
        if kwargs["symbol"] == "BTC/USDT":
            return _market_snapshot(
                symbol="BTC/USDT", price=60000.0, is_stale=False, available=True, error=None
            )
        # ETH dropped 3000 -> 2800: the short is in PROFIT.
        return _market_snapshot(
            symbol="ETH/USDT", price=2800.0, is_stale=False, available=True, error=None
        )

    monkeypatch.setattr(
        "app.execution.portfolio_read.get_market_data_snapshot",
        fake_market_data_snapshot,
    )

    snapshot = await build_portfolio_snapshot(audit_path=audit_path)

    eth = next(p for p in snapshot.positions if p.symbol == "ETH/USDT")
    btc = next(p for p in snapshot.positions if p.symbol == "BTC/USDT")
    # Short in profit -> positive unrealized (was negative before the fix -> red).
    assert eth.unrealized_pnl_usd == pytest.approx(400.0)
    assert btc.unrealized_pnl_usd == pytest.approx(2000.0)
    # market_value stays gross-positive per position.
    assert eth.market_value_usd == pytest.approx(5600.0)
    # Equity = cash + long(12000) - short(5600) = 15984 + 6400 = 22384.
    # The bug added the short -> 15984 + 12000 + 5600 = 33584.
    assert snapshot.total_equity_usd == pytest.approx(22384.0)
    assert snapshot.total_equity_usd != pytest.approx(33584.0)
    # Gross market value unchanged (sum of |mv|), used by "in Position" display.
    assert snapshot.total_market_value_usd == pytest.approx(17600.0)
    assert snapshot.total_unrealized_pnl_usd == pytest.approx(2400.0)
    assert snapshot.total_fees_usd == pytest.approx(16.0)
    # Net exposure = long - short; gross = long + short.
    assert snapshot.exposure_summary.net_exposure_usd == pytest.approx(6400.0)
    assert snapshot.exposure_summary.gross_exposure_usd == pytest.approx(17600.0)


def test_compute_realized_by_asset_recent_trades(tmp_path: Path) -> None:
    """recent_trades lists individual closes, newest first, sign-correct PnL."""
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    _write_audit(
        audit_path,
        [
            {
                "event_type": "position_closed",
                "symbol": "BTC/USDT",
                "position_side": "long",
                "trade_pnl_usd": 120.5,
                "fee_usd": 3.0,
                "entry_price": 50000.0,
                "exit_price": 50600.0,
                "timestamp_utc": "2026-06-24T11:00:00+00:00",
                "source": "autonomous_generator",
            },
            {
                "event_type": "position_closed",
                "symbol": "ETH/USDT",
                "position_side": "short",
                "trade_pnl_usd": 45.0,
                "fee_usd": 2.0,
                "entry_price": 3000.0,
                "exit_price": 2977.5,
                "timestamp_utc": "2026-06-24T12:00:00+00:00",
                "source": "autonomous_generator",
            },
        ],
    )

    result = compute_realized_by_asset(audit_path)
    recent = result["recent_trades"]
    assert isinstance(recent, list)
    assert len(recent) == 2
    # Newest first.
    assert recent[0]["symbol"] == "ETH/USDT"
    assert recent[0]["position_side"] == "short"
    assert recent[0]["trade_pnl_usd"] == pytest.approx(45.0)
    assert recent[0]["win"] is True
    assert recent[1]["symbol"] == "BTC/USDT"
    assert recent[1]["trade_pnl_usd"] == pytest.approx(120.5)


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_marks_stale_market_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    _write_audit(
        audit_path,
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": 0.5,
                "fill_price": 50000.0,
                "filled_at": "2026-03-21T10:00:00+00:00",
                "portfolio_cash": 1000.0,
                "realized_pnl_usd": 0.0,
            }
        ],
    )

    async def fake_market_data_snapshot(**kwargs):  # noqa: ANN003
        return _market_snapshot(
            symbol=kwargs["symbol"],
            price=51000.0,
            is_stale=True,
            available=True,
            error="stale_data",
        )

    monkeypatch.setattr(
        "app.execution.portfolio_read.get_market_data_snapshot",
        fake_market_data_snapshot,
    )

    snapshot = await build_portfolio_snapshot(audit_path=audit_path)

    assert snapshot.available is False
    assert snapshot.error == "market_data_unavailable_for_open_positions"
    assert snapshot.exposure_summary.mark_to_market_status == "degraded"
    assert snapshot.exposure_summary.stale_position_count == 1
    assert snapshot.exposure_summary.unavailable_price_count == 1
    assert snapshot.positions[0].market_price is None
    assert snapshot.positions[0].market_data_is_stale is True
    assert snapshot.positions[0].market_data_available is False
    assert snapshot.positions[0].market_data_error == "stale_data"


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_mixed_fresh_and_stale_prices_degrades_but_stays_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    _write_audit(
        audit_path,
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": 0.2,
                "fill_price": 50000.0,
                "filled_at": "2026-03-21T10:00:00+00:00",
                "portfolio_cash": 9000.0,
                "realized_pnl_usd": 0.0,
            },
            {
                "event_type": "order_filled",
                "order_id": "ord_2",
                "symbol": "ETH/USDT",
                "side": "buy",
                "quantity": 1.0,
                "fill_price": 3000.0,
                "filled_at": "2026-03-21T10:01:00+00:00",
                "portfolio_cash": 6000.0,
                "realized_pnl_usd": 0.0,
            },
        ],
    )

    async def fake_market_data_snapshot(**kwargs):  # noqa: ANN003
        if kwargs["symbol"] == "BTC/USDT":
            return _market_snapshot(
                symbol="BTC/USDT",
                price=60000.0,
                is_stale=False,
                available=True,
                error=None,
            )
        return _market_snapshot(
            symbol="ETH/USDT",
            price=3200.0,
            is_stale=True,
            available=True,
            error="stale_data",
        )

    monkeypatch.setattr(
        "app.execution.portfolio_read.get_market_data_snapshot",
        fake_market_data_snapshot,
    )

    snapshot = await build_portfolio_snapshot(audit_path=audit_path)

    assert snapshot.available is True
    assert snapshot.error is None
    assert snapshot.exposure_summary.mark_to_market_status == "degraded"
    assert snapshot.exposure_summary.priced_position_count == 1
    assert snapshot.exposure_summary.stale_position_count == 1
    assert snapshot.exposure_summary.unavailable_price_count == 1
    btc = next(position for position in snapshot.positions if position.symbol == "BTC/USDT")
    eth = next(position for position in snapshot.positions if position.symbol == "ETH/USDT")
    assert btc.market_price == pytest.approx(60000.0)
    assert eth.market_price is None
    assert eth.market_data_is_stale is True
    assert eth.market_data_available is False


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_fail_closed_when_all_open_positions_unpriced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    _write_audit(
        audit_path,
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "quantity": 0.1,
                "fill_price": 50000.0,
                "filled_at": "2026-03-21T10:00:00+00:00",
                "portfolio_cash": 5000.0,
                "realized_pnl_usd": 0.0,
            }
        ],
    )

    async def fake_market_data_snapshot(**kwargs):  # noqa: ANN003
        return _market_snapshot(
            symbol=kwargs["symbol"],
            price=None,
            is_stale=True,
            available=False,
            error="timeout",
        )

    monkeypatch.setattr(
        "app.execution.portfolio_read.get_market_data_snapshot",
        fake_market_data_snapshot,
    )

    snapshot = await build_portfolio_snapshot(audit_path=audit_path)

    assert snapshot.available is False
    assert snapshot.error == "market_data_unavailable_for_open_positions"
    assert snapshot.exposure_summary.unavailable_price_count == 1
    assert snapshot.execution_enabled is False
    assert snapshot.write_back_allowed is False


@pytest.mark.asyncio
async def test_build_portfolio_snapshot_fail_closed_on_invalid_audit_json(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    audit_path.write_text("{this-is-not-json}\n", encoding="utf-8")

    snapshot = await build_portfolio_snapshot(audit_path=audit_path)

    assert snapshot.available is False
    assert snapshot.error == "audit_json_decode_error_line_1"
    assert snapshot.position_count == 0
