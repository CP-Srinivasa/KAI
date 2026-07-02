"""Regression: paper engine rejects side-conflicts and untradeable opens (DQ 2026-06-25).

- A long entry must NOT be accepted while a SHORT is open on the same symbol
  (the 4 ETH/BTC side-conflicts on 06-23/24 came from this gap). Symmetric to the
  pre-existing short-on-open-long guard.
- Opening an untradeable symbol (self-pair) is rejected by the backstop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.execution.paper_engine import PaperExecutionEngine


def _engine(tmp_path: Path) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=100_000.0,
        live_enabled=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


def test_long_buy_rejected_when_short_open(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    short = eng.create_order(
        symbol="ETH/USDT",
        side="sell",
        quantity=1.0,
        order_type="market",
        position_side="short",
        stop_loss=3200.0,
        take_profit=2800.0,
    )
    assert eng.fill_order(short, 3000.0) is not None
    assert eng._portfolio.positions["ETH/USDT"].position_side == "short"

    # Long entry on the same symbol while the short is open → rejected, no blend.
    long = eng.create_order(
        symbol="ETH/USDT",
        side="buy",
        quantity=1.0,
        order_type="market",
        position_side="long",
        stop_loss=2800.0,
        take_profit=3200.0,
    )
    assert eng.fill_order(long, 3000.0) is None
    pos = eng._portfolio.positions["ETH/USDT"]
    assert pos.position_side == "short"
    assert pos.quantity == pytest.approx(1.0)


def test_open_untradeable_symbol_rejected(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="USDT/USDT",
        side="sell",
        quantity=10.0,
        order_type="market",
        position_side="short",
        stop_loss=110.0,
        take_profit=90.0,
    )
    assert eng.fill_order(order, 100.0) is None
    assert "USDT/USDT" not in eng._portfolio.positions
