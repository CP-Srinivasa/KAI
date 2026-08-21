"""Fail-closed numeric invariants for paper execution mutations."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.execution.order_intent import ExecutableOrderIntent
from app.execution.paper_engine import PaperExecutionEngine


def _engine(tmp_path: Path, *, initial_equity: float = 10_000.0) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=initial_equity,
        fee_pct=0.1,
        slippage_pct=0.05,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


def _intent() -> ExecutableOrderIntent:
    return ExecutableOrderIntent(
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        entry_type="market",
        entry_value=None,
        entry_min=None,
        entry_max=None,
        quantity=1.0,
        risk_allocation_pct=None,
        leverage=1.0,
        margin_mode="spot",
        stop_loss=90.0,
        take_profit_targets=(120.0,),
        reduce_only=False,
        source="finite-gate-test",
        correlation_id="corr-finite-gate",
        idempotency_key="idem-finite-gate",
    )


def _book_snapshot(engine: PaperExecutionEngine) -> tuple[object, set[str], dict[str, float]]:
    return (
        deepcopy(engine.portfolio),
        set(engine._filled_keys),
        dict(engine._partial_fill_ratios),
    )


def _strict_audit_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    def reject_constant(token: str) -> object:
        raise AssertionError(f"non-standard JSON constant written: {token}")

    return [
        json.loads(line, parse_constant=reject_constant)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _last_rejection(engine: PaperExecutionEngine) -> dict[str, Any]:
    rejected = [
        row
        for row in _strict_audit_records(engine.audit_path)
        if row.get("event_type") == "paper_execution_rejected"
    ]
    assert rejected
    return rejected[-1]


@pytest.mark.parametrize(
    "invalid",
    [True, False, "1", float("nan"), float("inf"), float("-inf"), 0.0, -1.0],
    ids=["true", "false", "string", "nan", "pos-inf", "neg-inf", "zero", "negative"],
)
def test_create_order_rejects_invalid_quantity_before_order_state(
    tmp_path: Path, invalid: object
) -> None:
    engine = _engine(tmp_path)
    before = _book_snapshot(engine)

    with pytest.raises(ValueError, match="quantity"):
        engine.create_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=cast(float, invalid),
            idempotency_key="invalid-quantity",
        )

    assert _book_snapshot(engine) == before
    records = _strict_audit_records(engine.audit_path)
    assert not any(row.get("event_type") == "order_created" for row in records)
    assert _last_rejection(engine)["field"] == "quantity"


@pytest.mark.parametrize(
    "invalid",
    [True, False, "0.5", float("nan"), float("inf"), float("-inf"), 0.0, -0.1, 1.1],
    ids=[
        "true",
        "false",
        "string",
        "nan",
        "pos-inf",
        "neg-inf",
        "zero",
        "negative",
        "above-one",
    ],
)
def test_create_order_rejects_invalid_partial_fill_ratio(tmp_path: Path, invalid: object) -> None:
    engine = _engine(tmp_path)
    before = _book_snapshot(engine)

    with pytest.raises(ValueError, match="partial_fill_ratio"):
        engine.create_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            partial_fill_ratio=cast(float, invalid),
            idempotency_key="invalid-ratio",
        )

    assert _book_snapshot(engine) == before
    assert _last_rejection(engine)["field"] == "partial_fill_ratio"


@pytest.mark.parametrize(
    "invalid",
    [True, False, "100", float("nan"), float("inf"), float("-inf"), 0.0, -1.0],
    ids=["true", "false", "string", "nan", "pos-inf", "neg-inf", "zero", "negative"],
)
def test_execute_intent_rejects_invalid_current_price_before_order_creation(
    tmp_path: Path, invalid: object
) -> None:
    engine = _engine(tmp_path)
    before = _book_snapshot(engine)

    with pytest.raises(ValueError, match="current_price"):
        engine.execute_intent(
            _intent(),
            current_price=cast(float, invalid),
            risk_check_id="risk-finite-gate",
        )

    assert _book_snapshot(engine) == before
    records = _strict_audit_records(engine.audit_path)
    assert not any(row.get("event_type") == "order_created" for row in records)
    assert _last_rejection(engine)["field"] == "current_price"


@pytest.mark.parametrize(
    "invalid",
    [True, False, "100", float("nan"), float("inf"), float("-inf"), 0.0, -1.0],
    ids=["true", "false", "string", "nan", "pos-inf", "neg-inf", "zero", "negative"],
)
def test_execute_intent_rejects_invalid_explicit_fill_price_before_order_creation(
    tmp_path: Path, invalid: object
) -> None:
    engine = _engine(tmp_path)
    before = _book_snapshot(engine)

    with pytest.raises(ValueError, match="fill_price"):
        engine.execute_intent(
            _intent(),
            current_price=100.0,
            fill_price=cast(float, invalid),
            risk_check_id="risk-finite-gate",
        )

    assert _book_snapshot(engine) == before
    records = _strict_audit_records(engine.audit_path)
    assert not any(row.get("event_type") == "order_created" for row in records)
    assert _last_rejection(engine)["field"] == "fill_price"


@pytest.mark.parametrize(
    "invalid",
    [True, "100", float("nan"), float("inf"), float("-inf"), 0.0, -1.0],
    ids=["bool", "string", "nan", "pos-inf", "neg-inf", "zero", "negative"],
)
def test_fill_order_defensively_rejects_invalid_price_without_mutation(
    tmp_path: Path, invalid: object
) -> None:
    engine = _engine(tmp_path)
    order = engine.create_order(
        symbol="BTC/USDT", side="buy", quantity=1.0, idempotency_key="bad-fill-price"
    )
    before = _book_snapshot(engine)

    assert engine.fill_order(order, cast(float, invalid)) is None

    assert _book_snapshot(engine) == before
    assert _last_rejection(engine)["field"] == "current_price"


@pytest.mark.parametrize(
    "invalid",
    [True, "1", float("nan"), float("inf"), float("-inf"), 0.0, -1.0],
    ids=["bool", "string", "nan", "pos-inf", "neg-inf", "zero", "negative"],
)
def test_fill_order_defensively_rejects_corrupt_order_quantity(
    tmp_path: Path, invalid: object
) -> None:
    engine = _engine(tmp_path)
    valid = engine.create_order(
        symbol="BTC/USDT", side="buy", quantity=1.0, idempotency_key="bad-order-quantity"
    )
    order = replace(valid, quantity=cast(float, invalid))
    before = _book_snapshot(engine)

    assert engine.fill_order(order, 100.0) is None

    assert _book_snapshot(engine) == before
    assert _last_rejection(engine)["field"] == "quantity"


@pytest.mark.parametrize(
    "invalid",
    [True, "100", float("nan"), float("inf"), float("-inf"), 0.0, -1.0],
    ids=["bool", "string", "nan", "pos-inf", "neg-inf", "zero", "negative"],
)
def test_monitor_rejects_invalid_price_before_liquidation_or_other_mutation(
    tmp_path: Path, invalid: object
) -> None:
    engine = _engine(tmp_path)
    order = engine.create_order(
        symbol="BTC/USDT",
        side="sell",
        quantity=1.0,
        position_side="short",
        leverage=2.0,
        idempotency_key="leveraged-short",
    )
    assert engine.fill_order(order, 100.0) is not None
    before = _book_snapshot(engine)

    assert engine.monitor_positions({"BTC/USDT": cast(float, invalid)}) == []

    assert _book_snapshot(engine) == before
    rejection = _last_rejection(engine)
    assert rejection["stage"] == "monitor_positions"
    assert rejection["field"] == "current_price"


@pytest.mark.parametrize(
    "invalid",
    [True, "0.5", float("nan"), float("inf"), float("-inf"), 0.0, -0.1, 1.1],
    ids=["bool", "string", "nan", "pos-inf", "neg-inf", "zero", "negative", "above-one"],
)
def test_tp_tier_ratio_is_rejected_before_position_mutation(
    tmp_path: Path, invalid: object
) -> None:
    engine = _engine(tmp_path)
    order = engine.create_order(
        symbol="BTC/USDT", side="buy", quantity=1.0, idempotency_key="tier-position"
    )
    assert engine.fill_order(order, 100.0) is not None
    before = _book_snapshot(engine)

    with pytest.raises(ValueError, match="take_profit_tier_ratio"):
        engine.set_position_tp_tiers("BTC/USDT", [(110.0, cast(float, invalid))])

    assert _book_snapshot(engine) == before
    assert _last_rejection(engine)["field"] == "take_profit_tier_ratio"


@pytest.mark.parametrize(
    "invalid",
    [True, "90", float("nan"), float("inf"), float("-inf"), 0.0, -1.0],
    ids=["bool", "string", "nan", "pos-inf", "neg-inf", "zero", "negative"],
)
def test_adjust_position_rejects_invalid_numeric_before_mutation(
    tmp_path: Path, invalid: object
) -> None:
    engine = _engine(tmp_path)
    order = engine.create_order(
        symbol="BTC/USDT", side="buy", quantity=1.0, idempotency_key="adjust-position"
    )
    assert engine.fill_order(order, 100.0) is not None
    before = _book_snapshot(engine)

    with pytest.raises(ValueError, match="stop_loss"):
        engine.adjust_position("BTC/USDT", stop_loss=cast(float, invalid))

    assert _book_snapshot(engine) == before
    assert _last_rejection(engine)["field"] == "stop_loss"


@pytest.mark.parametrize(
    "invalid",
    [True, False, "10", float("nan"), float("inf"), float("-inf"), -1.0, 10_001.0],
    ids=[
        "true",
        "false",
        "string",
        "nan",
        "pos-inf",
        "neg-inf",
        "negative",
        "above-100-percent",
    ],
)
def test_invalid_fee_rate_is_rejected_before_portfolio_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: object
) -> None:
    engine = _engine(tmp_path)
    order = engine.create_order(
        symbol="BTC/USDT", side="buy", quantity=1.0, idempotency_key="bad-fee"
    )
    before = _book_snapshot(engine)

    def invalid_fee(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            venue="paper",
            role="taker",
            bps_applied=invalid,
            table_version="test",
        )

    monkeypatch.setattr("app.execution.fees.lookup_order_fee", invalid_fee)

    assert engine.fill_order(order, 100.0) is None

    assert _book_snapshot(engine) == before
    assert _last_rejection(engine)["field"] == "fee_bps_applied"


def test_nonfinite_cash_delta_is_rejected_before_portfolio_mutation(tmp_path: Path) -> None:
    engine = PaperExecutionEngine(
        initial_equity=1.79e308,
        fee_pct=1.0,
        slippage_pct=0.0,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )
    order = engine.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=1.0,
        venue="legacy",
        idempotency_key="overflow-cash-delta",
    )
    before = _book_snapshot(engine)

    assert engine.fill_order(order, 1.79e308) is None

    assert _book_snapshot(engine) == before
    assert _last_rejection(engine)["field"] == "cash_delta_usd"


def test_cash_delta_rounded_to_zero_is_rejected_before_portfolio_mutation(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, initial_equity=1.0e308)
    order = engine.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=1.0,
        venue="legacy",
        idempotency_key="rounded-zero-cash-delta",
    )
    before = _book_snapshot(engine)

    assert engine.fill_order(order, 1.0) is None

    assert _book_snapshot(engine) == before
    assert _last_rejection(engine)["field"] == "cash_delta_usd"


def test_nonfinite_resulting_position_quantity_is_rejected_atomically(tmp_path: Path) -> None:
    engine = _engine(tmp_path, initial_equity=1.0e308)
    first = engine.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=1.0e308,
        idempotency_key="position-overflow-first",
    )
    assert engine.fill_order(first, 0.1) is not None
    second = engine.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=1.0e308,
        idempotency_key="position-overflow-second",
    )
    before = _book_snapshot(engine)

    assert engine.fill_order(second, 0.1) is None

    assert _book_snapshot(engine) == before
    assert _last_rejection(engine)["field"] == "position_quantity"


def test_nonfinite_resulting_realized_pnl_is_rejected_atomically(tmp_path: Path) -> None:
    engine = _engine(tmp_path, initial_equity=1.79e308)
    engine._slippage_pct = 0.0
    opening = engine.create_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=1.0,
        idempotency_key="pnl-overflow-open",
    )
    assert engine.fill_order(opening, 1.0e308) is not None
    engine.portfolio.cash = 0.0
    engine.portfolio.realized_pnl_usd = 1.7e308
    closing = engine.create_order(
        symbol="BTC/USDT",
        side="sell",
        quantity=1.0,
        idempotency_key="pnl-overflow-close",
    )
    before = _book_snapshot(engine)

    assert engine.fill_order(closing, 1.79e308) is None

    assert _book_snapshot(engine) == before
    assert _last_rejection(engine)["field"] == "realized_pnl_usd"


def test_audit_writer_refuses_nonstandard_json_constants(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    engine._append_audit("unsafe_probe", {"fee_usd": float("nan")})

    assert not any(
        row.get("event_type") == "unsafe_probe" for row in _strict_audit_records(engine.audit_path)
    )
