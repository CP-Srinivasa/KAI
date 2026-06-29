"""Tests for deliverable 5: quarantine_offvenue_positions.py remediation script.

TDD: dry-run path asserts correct planned output; idempotency asserts no
double-planning for already-closed symbols.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers: build minimal audit fixtures
# ---------------------------------------------------------------------------


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _order_created(order_id: str, symbol: str, side: str, position_side: str) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "order_created",
        "timestamp_utc": _ts(),
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "position_side": position_side,
        "quantity": 10.0,
        "stop_loss": None,
        "take_profit": None,
        "leverage": None,
        "source": "",
        "correlation_id": "",
        "idempotency_key": f"idem_{order_id}",
    }


def _order_filled_open(order_id: str, symbol: str, price: float) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": _ts(),
        "order_id": order_id,
        "fill_id": f"fill_{order_id}",
        "symbol": symbol,
        "side": "buy",
        "position_side": "long",
        "quantity": 10.0,
        "fill_price": price,
        "fee_usd": 0.05,
        "fee_bps_applied": 10.0,
        "filled_at": _ts(),
        "portfolio_cash": 9_000.0,
        "realized_pnl_usd": 0.0,
    }


def _order_filled_close(order_id: str, symbol: str, price: float) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": _ts(),
        "order_id": order_id,
        "fill_id": f"fill_{order_id}_close",
        "symbol": symbol,
        "side": "sell",
        "position_side": "long",
        "quantity": 10.0,
        "fill_price": price,
        "fee_usd": 0.05,
        "fee_bps_applied": 10.0,
        "filled_at": _ts(),
        "portfolio_cash": 10_000.0,
        "realized_pnl_usd": 0.0,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------------------
# Import the script under test
# ---------------------------------------------------------------------------


def _import_script():
    """Import the remediation script module."""
    import importlib.util
    from pathlib import Path

    script_path = (
        Path(__file__).parent.parent.parent / "scripts" / "quarantine_offvenue_positions.py"
    )
    spec = importlib.util.spec_from_file_location("quarantine_offvenue_positions", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 5a. Dry-run: plans exactly one close for an open target
# ---------------------------------------------------------------------------


def test_dryrun_plans_close_for_open_target(tmp_path: Path) -> None:
    """Dry-run plans exactly one close at entry price with quarantine reason."""
    audit = tmp_path / "artifacts" / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            _order_created("oid1", "SLX/USDT", "buy", "long"),
            _order_filled_open("oid1", "SLX/USDT", price=0.05),
        ],
    )

    mod = _import_script()
    plans = mod.plan_closes(
        audit_path=audit,
        targets=["SLX/USDT"],
    )

    assert len(plans) == 1
    plan = plans[0]
    assert plan["symbol"] == "SLX/USDT"
    assert plan["close_reason"] == "quarantine_off_venue_unpriceable"
    assert plan["exit_price"] == pytest.approx(plan["entry_price"])  # flat-close
    assert plan["trade_pnl_usd"] == pytest.approx(0.0)


def test_dryrun_idempotent_already_closed(tmp_path: Path) -> None:
    """If the position is already net-flat, plan_closes returns 0 entries."""
    audit = tmp_path / "artifacts" / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            _order_created("oid1", "SLX/USDT", "buy", "long"),
            _order_filled_open("oid1", "SLX/USDT", price=0.05),
            # Close the position before remediation runs.
            _order_filled_close("oid2", "SLX/USDT", price=0.05),
        ],
    )

    mod = _import_script()
    plans = mod.plan_closes(
        audit_path=audit,
        targets=["SLX/USDT"],
    )

    assert plans == [], "Expected 0 plans for an already-closed position"


def test_dryrun_skips_symbol_not_open(tmp_path: Path) -> None:
    """Symbols in the target list but not open are skipped."""
    audit = tmp_path / "artifacts" / "paper_execution_audit.jsonl"
    # Empty audit → no open positions at all.
    _write_jsonl(audit, [])

    mod = _import_script()
    plans = mod.plan_closes(
        audit_path=audit,
        targets=["ACT/USDT", "SLX/USDT", "O/USDT"],
    )

    assert plans == []


def test_dryrun_only_closes_targets(tmp_path: Path) -> None:
    """Non-target open positions are NOT planned for closure."""
    audit = tmp_path / "artifacts" / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            _order_created("oid_slx", "SLX/USDT", "buy", "long"),
            _order_filled_open("oid_slx", "SLX/USDT", price=0.05),
            _order_created("oid_btc", "BTC/USDT", "buy", "long"),
            _order_filled_open("oid_btc", "BTC/USDT", price=50_000.0),
        ],
    )

    mod = _import_script()
    plans = mod.plan_closes(
        audit_path=audit,
        targets=["SLX/USDT"],  # BTC not in target list
    )

    assert len(plans) == 1
    assert plans[0]["symbol"] == "SLX/USDT"


def test_dryrun_entry_price_is_avg_entry(tmp_path: Path) -> None:
    """The planned exit_price equals the position's avg_entry_price (0 price-PnL)."""
    audit = tmp_path / "artifacts" / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            _order_created("oid1", "ACT/USDT", "buy", "long"),
            _order_filled_open("oid1", "ACT/USDT", price=0.12),
        ],
    )

    mod = _import_script()
    plans = mod.plan_closes(audit_path=audit, targets=["ACT/USDT"])

    assert len(plans) == 1
    assert plans[0]["entry_price"] == pytest.approx(0.12)
    assert plans[0]["exit_price"] == pytest.approx(0.12)
