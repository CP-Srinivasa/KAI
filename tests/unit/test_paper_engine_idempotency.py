"""Regression-Tests für den Q-Duplicate-Bug 2026-05-09 (Sprint C, 2026-05-12).

Hintergrund
-----------
Am 2026-05-09 16:21:18 produzierte die Bridge zwei identische order_created+
order_filled-Paare für Q/USDT mit demselben idempotency_key
``opbridge:ENV-20260509162116-6c51f5bf``, 333ms auseinander. Ursache: zwei
parallele ``run_tick()``-Aufrufe erzeugten zwei verschiedene
``PaperExecutionEngine``-Instanzen mit jeweils eigenem ``_filled_keys``-Set.

Fix (Sprint C):
1. ``audit_replay.replay_paper_audit`` liefert jetzt ``filled_idempotency_keys``.
2. ``PaperExecutionEngine.rehydrate_from_audit`` populiert ``self._filled_keys``
   aus diesem Set — cross-process Race-Schutz.
3. ``create_order`` wirft ``DuplicateOrderError`` BEVOR ein order_created-Audit
   geschrieben wird, wenn der idempotency_key bereits gefilled ist.

Diese Tests bestätigen alle drei Punkte.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.audit_replay import replay_paper_audit
from app.execution.paper_engine import DuplicateOrderError, PaperExecutionEngine


@pytest.fixture
def audit_with_filled_order(tmp_path: Path) -> Path:
    """Build a minimal audit-jsonl with one successful order_created + order_filled."""
    audit_path = tmp_path / "paper_execution_audit.jsonl"
    idem = "opbridge:ENV-20260509162116-6c51f5bf"
    rows = [
        {
            "event_type": "order_created",
            "timestamp_utc": "2026-05-09T16:21:18.044850+00:00",
            "order_id": "ord_test_aaa",
            "symbol": "Q/USDT",
            "side": "buy",
            "quantity": 100.0,
            "order_type": "market",
            "limit_price": None,
            "stop_loss": 0.014,
            "take_profit": 0.016,
            "created_at": "2026-05-09T16:21:18.044800+00:00",
            "idempotency_key": idem,
            "status": "pending",
            "risk_check_id": "rck_test",
            "position_side": "long",
            "leverage": 10.0,
            "source": "telegram_premium_channel_approved",
        },
        {
            "event_type": "order_filled",
            "timestamp_utc": "2026-05-09T16:21:18.067114+00:00",
            "fill_id": "fill_test_aaa",
            "order_id": "ord_test_aaa",
            "symbol": "Q/USDT",
            "side": "buy",
            "quantity": 100.0,
            "fill_price": 0.0155,
            "fee_usd": 0.0155,
            "filled_at": "2026-05-09T16:21:18.067000+00:00",
            "slippage_pct": 0.05,
            "position_side": "long",
            "portfolio_cash": 8000.0,
            "realized_pnl_usd": 0.0,
        },
    ]
    audit_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    return audit_path


def test_replay_returns_filled_idempotency_keys(audit_with_filled_order: Path) -> None:
    """Sprint C: replay must surface idempotency_keys from order_created."""
    result = replay_paper_audit(audit_with_filled_order)
    assert result.available
    assert "opbridge:ENV-20260509162116-6c51f5bf" in result.filled_idempotency_keys


def test_rehydrate_populates_filled_keys(audit_with_filled_order: Path) -> None:
    """Sprint C: rehydrate_from_audit must seed _filled_keys cross-instance."""
    eng = PaperExecutionEngine(initial_equity=10000.0, live_enabled=False)
    eng.rehydrate_from_audit(audit_with_filled_order)
    assert "opbridge:ENV-20260509162116-6c51f5bf" in eng._filled_keys


def test_create_order_rejects_duplicate_idempotency_key(audit_with_filled_order: Path) -> None:
    """Sprint C: A second create_order for a known-filled key must raise."""
    eng = PaperExecutionEngine(initial_equity=10000.0, live_enabled=False)
    eng.rehydrate_from_audit(audit_with_filled_order)
    with pytest.raises(DuplicateOrderError):
        eng.create_order(
            symbol="Q/USDT",
            side="buy",
            quantity=100.0,
            order_type="market",
            idempotency_key="opbridge:ENV-20260509162116-6c51f5bf",
            position_side="long",
            source="telegram_premium_channel_approved",
            leverage=10.0,
        )


def test_two_parallel_engines_share_filled_keys_via_audit(audit_with_filled_order: Path) -> None:
    """Sprint C: two engines rehydrated from the same audit reject the dup key.

    Das ist die echte Q/USDT-Race-Reproduktion: Bridge baut bei jedem
    run_tick() eine neue Engine-Instanz. Beide rehydraten parallel vom audit
    und sehen denselben filled_keys-Snapshot → beide blocken den dup-Key.
    """
    eng_a = PaperExecutionEngine(initial_equity=10000.0, live_enabled=False)
    eng_a.rehydrate_from_audit(audit_with_filled_order)
    eng_b = PaperExecutionEngine(initial_equity=10000.0, live_enabled=False)
    eng_b.rehydrate_from_audit(audit_with_filled_order)
    for eng in (eng_a, eng_b):
        with pytest.raises(DuplicateOrderError):
            eng.create_order(
                symbol="Q/USDT",
                side="buy",
                quantity=100.0,
                order_type="market",
                idempotency_key="opbridge:ENV-20260509162116-6c51f5bf",
                position_side="long",
            )


def test_create_order_accepts_fresh_idempotency_key(audit_with_filled_order: Path) -> None:
    """Sanity: Sprint-C-Fix darf nicht ALLE orders blocken — nur den Dup-Key."""
    eng = PaperExecutionEngine(initial_equity=10000.0, live_enabled=False)
    eng.rehydrate_from_audit(audit_with_filled_order)
    # different symbol + different envelope = fresh idempotency key
    order = eng.create_order(
        symbol="XYZ/USDT",
        side="buy",
        quantity=50.0,
        order_type="market",
        idempotency_key="opbridge:ENV-20260512-different",
        position_side="long",
    )
    assert order.symbol == "XYZ/USDT"
    assert order.idempotency_key == "opbridge:ENV-20260512-different"


def test_paperposition_leverage_and_source_persist_through_replay(
    audit_with_filled_order: Path,
) -> None:
    """Sprint A: audit-replay muss leverage + source aus order_created in
    der rehydrierten PaperPosition wiederherstellen."""
    result = replay_paper_audit(audit_with_filled_order)
    assert result.available
    pos = result.positions["Q/USDT"]
    assert pos.leverage == 10.0
    assert pos.source == "telegram_premium_channel_approved"
