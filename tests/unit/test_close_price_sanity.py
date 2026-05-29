"""DS-20260529-V1: paper-engine close-price circuit breaker.

Backstop for the MATIC phantom-PnL incident on symbols the upstream
cross-provider guard cannot validate (single resolving provider). A close
implying a per-trade return beyond MAX_CLOSE_RETURN_PCT (default 200%) is
rejected so a stale/wrong price source cannot book phantom profit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.paper_engine import PaperExecutionEngine


def _engine(tmp_path: Path, **kwargs: object) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=10000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
        **kwargs,  # type: ignore[arg-type]
    )


def _open_long(eng: PaperExecutionEngine, symbol: str, entry: float, qty: float) -> None:
    order = eng.create_order(
        symbol=symbol, side="buy", quantity=qty, idempotency_key=f"open_{symbol}"
    )
    fill = eng.fill_order(order, current_price=entry)
    assert fill is not None


def _audit_events(tmp_path: Path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def test_sane_close_books_normally(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _open_long(eng, "ETH/USDT", entry=2000.0, qty=1.0)
    fill = eng.close_position("ETH/USDT", current_price=2100.0, reason="take")
    assert fill is not None
    assert "ETH/USDT" not in eng.portfolio.positions


def test_phantom_close_rejected_and_position_preserved(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    # MATIC opened at the real ~0.088, monitor hands a stale BitMEX 0.40875 (+364%).
    _open_long(eng, "MATIC/USDT", entry=0.088, qty=1000.0)
    realized_before = eng.portfolio.realized_pnl_usd
    fill = eng.close_position("MATIC/USDT", current_price=0.40875, reason="take")
    assert fill is None  # rejected
    assert "MATIC/USDT" in eng.portfolio.positions  # left open
    assert eng.portfolio.realized_pnl_usd == realized_before  # no phantom PnL booked
    events = _audit_events(tmp_path)
    assert any(e.get("event_type") == "close_price_sanity_rejected" for e in events)
    rej = next(e for e in events if e.get("event_type") == "close_price_sanity_rejected")
    assert rej["symbol"] == "MATIC/USDT"
    assert rej["implied_return_pct"] > 200.0


def test_threshold_env_override_allows_extreme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_CLOSE_RETURN_PCT", "5.0")  # allow up to +500%
    eng = _engine(tmp_path)
    _open_long(eng, "MATIC/USDT", entry=0.088, qty=1000.0)
    fill = eng.close_position("MATIC/USDT", current_price=0.40875, reason="take")
    assert fill is not None  # +364% now under the 500% cap
    assert "MATIC/USDT" not in eng.portfolio.positions


def test_monitor_tier_close_rejected_on_phantom(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _open_long(eng, "MATIC/USDT", entry=0.088, qty=1000.0)
    eng.set_position_tp_tiers("MATIC/USDT", [(0.40, 1.0)])
    realized_before = eng.portfolio.realized_pnl_usd
    # Monitor price 0.40875 would trigger the tier — must be rejected as phantom.
    fills = eng.monitor_positions({"MATIC/USDT": 0.40875})
    assert fills == []
    assert "MATIC/USDT" in eng.portfolio.positions
    assert eng.portfolio.realized_pnl_usd == realized_before
    events = _audit_events(tmp_path)
    assert any(e.get("event_type") == "close_price_sanity_rejected" for e in events)
