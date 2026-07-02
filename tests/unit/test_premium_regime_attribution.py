"""2026-06-13: regime-AT-ENTRY attribution for premium/bridge intent execution.

Companion to test_regime_attribution.py (which covers the autonomous-loop path).
Premium-channel signals enter paper via ``PaperExecutionEngine.execute_intent``
(not the loop's direct ``create_order``), so the regime stamp is resolved there.
Behavior-focused: we assert the regime resolved at execution PROPAGATES onto the
position_closed event, that both stamping paths share ONE taxonomy via
``regime_label_at``, and that a missing classifier snapshot degrades to "" (the
fill must never be blocked by an absent regime).
"""

from __future__ import annotations

import json

import pytest

from app.execution import fees, paper_engine
from app.execution.order_intent import ExecutableOrderIntent
from app.execution.paper_engine import PaperExecutionEngine
from app.regime.lookup import regime_label_at


@pytest.fixture(autouse=True)
def _clear_cache():
    fees.reset_cache()
    yield
    fees.reset_cache()


def _read_jsonl(path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _intent(**overrides: object) -> ExecutableOrderIntent:
    defaults: dict[str, object] = {
        "symbol": "BTC/USDT",
        "side": "buy",
        "order_type": "market",
        "entry_type": "value",
        "entry_value": 100.0,
        "entry_min": None,
        "entry_max": None,
        "quantity": 0.1,
        "risk_allocation_pct": 5.0,
        "leverage": 1.0,
        "margin_mode": "cross",
        "stop_loss": 90.0,
        "take_profit_targets": (120.0,),
        "reduce_only": False,
        "source": "telegram_premium_channel_approved",
        "correlation_id": "corr-prem-1",
        "idempotency_key": "idem-prem-1",
    }
    defaults.update(overrides)
    return ExecutableOrderIntent(**defaults)  # type: ignore[arg-type]


def test_premium_intent_regime_propagates_to_position_closed(tmp_path, monkeypatch):
    """A premium intent executed via execute_intent must carry the regime
    resolved at execution onto its eventual position_closed event."""
    monkeypatch.setattr(paper_engine, "regime_label_at", lambda _sym, _ts: "risk_on_trending")
    audit = tmp_path / "exec.jsonl"
    engine = PaperExecutionEngine(audit_log_path=str(audit), initial_equity=10_000.0)

    order, fill = engine.execute_intent(_intent(), current_price=100.0, risk_check_id="rc-1")
    assert fill is not None

    closed = engine.close_position("BTC/USDT", current_price=120.0, reason="tp")
    assert closed is not None

    rows = _read_jsonl(audit)
    entry = next(r for r in rows if r.get("event_type") == "order_filled")
    closes = [r for r in rows if r.get("event_type") == "position_closed"]

    assert entry["regime"] == "risk_on_trending"
    assert len(closes) == 1
    assert closes[0]["regime"] == "risk_on_trending"
    # premium source attribution still travels alongside (no regression).
    assert closes[0]["signal_source"] == "telegram_premium_channel_approved"


def test_premium_intent_missing_regime_degrades_to_empty(tmp_path, monkeypatch):
    """No classifier snapshot -> regime "" (never None, never a blocked fill)."""
    monkeypatch.setattr(paper_engine, "regime_label_at", lambda _sym, _ts: "")
    audit = tmp_path / "exec.jsonl"
    engine = PaperExecutionEngine(audit_log_path=str(audit), initial_equity=10_000.0)

    order, fill = engine.execute_intent(
        _intent(idempotency_key="idem-prem-2"), current_price=100.0, risk_check_id="rc-2"
    )
    assert fill is not None
    entry = next(r for r in _read_jsonl(audit) if r.get("event_type") == "order_filled")
    assert entry.get("regime") == ""


def test_regime_label_at_fail_soft_returns_empty():
    """The shared SSOT must never raise: empty/garbage inputs -> "" not crash."""
    assert regime_label_at("", None) == ""
    assert regime_label_at("BTC/USDT", None) == ""
    assert regime_label_at("", "2026-06-13T10:00:00+00:00") == ""
