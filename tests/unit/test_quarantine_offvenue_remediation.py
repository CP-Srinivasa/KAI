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


# ---------------------------------------------------------------------------
# 5b. Integration: apply → re-replay → cumulative cash (Fix 1 regression guard)
# ---------------------------------------------------------------------------


def _open_fill_row(
    order_id: str, symbol: str, price: float, qty: float, portfolio_cash: float
) -> dict:
    """Build a raw order_filled (open) row with explicit portfolio_cash."""
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "timestamp_utc": _ts(),
        "order_id": order_id,
        "fill_id": f"fill_{order_id}",
        "symbol": symbol,
        "side": "buy",
        "position_side": "long",
        "quantity": qty,
        "fill_price": price,
        "fee_usd": 0.0,
        "fee_bps_applied": 10.0,
        "filled_at": _ts(),
        "portfolio_cash": portfolio_cash,
        "realized_pnl_usd": 0.0,
    }


def test_apply_then_replay_multi_target_cumulative_cash(tmp_path: Path) -> None:
    """Integration: apply_closes + re-replay proves cumulative cash correctness.

    Regression guard for the multi-target cash mis-statement bug (Fix 1):
    each planned close must contribute its recovery to a running total, not
    independently add to the same pre-loop base.  audit_replay uses last-write-
    wins for portfolio_cash, so only the correct running accumulation produces
    the right final cash after N closes.

    This test FAILS before Fix 1 (gets base_cash + last_recovery only) and
    PASSES after Fix 1 (gets base_cash + Σ all_recoveries).
    """
    from app.execution.audit_replay import replay_paper_audit

    audit = tmp_path / "artifacts" / "paper_execution_audit.jsonl"

    # Two open positions with known prices and quantities.
    slx_price, slx_qty = 0.05, 10.0  # cost recovered = 0.5
    act_price, act_qty = 0.12, 10.0  # cost recovered = 1.2
    base_cash = 8_000.0  # cash after both opens (last portfolio_cash in file)

    recovered_slx = slx_qty * slx_price  # 0.5
    recovered_act = act_qty * act_price  # 1.2
    expected_cash = base_cash + recovered_slx + recovered_act  # 8_001.7

    _write_jsonl(
        audit,
        [
            _order_created("oid_slx", "SLX/USDT", "buy", "long"),
            _open_fill_row(
                "oid_slx", "SLX/USDT", slx_price, slx_qty, portfolio_cash=base_cash + recovered_act
            ),
            _order_created("oid_act", "ACT/USDT", "buy", "long"),
            _open_fill_row("oid_act", "ACT/USDT", act_price, act_qty, portfolio_cash=base_cash),
        ],
    )

    mod = _import_script()

    # --- STEP 1: plan and apply ---
    plans = mod.plan_closes(audit_path=audit, targets=["SLX/USDT", "ACT/USDT"])
    assert len(plans) == 2, f"Expected 2 plans before apply, got {len(plans)}"
    mod.apply_closes(audit_path=audit, plans=plans)

    # --- STEP 2: re-replay the mutated audit ---
    replay2 = replay_paper_audit(audit)

    # (a) Both target positions gone
    assert "SLX/USDT" not in replay2.positions, "SLX/USDT must be closed after apply"
    assert "ACT/USDT" not in replay2.positions, "ACT/USDT must be closed after apply"

    # (b) Cash == base + Σ recovered (proves Fix 1 — NOT just the last target)
    assert replay2.cash_usd == pytest.approx(expected_cash, rel=1e-9), (
        f"cash mismatch: expected {expected_cash}, got {replay2.cash_usd}; "
        "likely Fix-1 missing (last-write-wins drops earlier recoveries)"
    )

    # (c) Realized PnL unchanged (flat closes add 0 price-PnL)
    assert replay2.realized_pnl_usd == pytest.approx(0.0, abs=1e-9), (
        f"realized_pnl should be 0.0, got {replay2.realized_pnl_usd}"
    )

    # (d) Second apply run plans nothing — idempotent
    plans2 = mod.plan_closes(audit_path=audit, targets=["SLX/USDT", "ACT/USDT"])
    assert plans2 == [], f"Expected 0 plans on second run (idempotent), got {len(plans2)}: {plans2}"
