"""Tests: intent_builder (S7-Extraktion aus der Bridge) + document_id-Fix.

V2-Nebenbefund 2026-06-12: ALLE Premium-Closes im Audit trugen
``document_id=""`` weil die Bridge die stabile Signal-Identität
(``payload.signal_id``) nie in den ``ExecutableOrderIntent`` schrieb. Diese
Tests pinnen die Durchreichung Ende-zu-Ende: payload → intent → kwargs →
order_created/position_closed-Audit-Events.
"""

from __future__ import annotations

from typing import Any

from app.execution.execution_protocol import executable_intent_to_paper_kwargs
from app.execution.intent_builder import (
    build_executable_intent,
    document_id_from_payload,
    entry_bounds,
    float_or_none,
)
from app.execution.paper_engine import PaperExecutionEngine

# Mirrors the real FIGHT/USDT approved-envelope payload shape (2026-06-10).
_PREMIUM_PAYLOAD: dict[str, object] = {
    "signal_id": "SIG-TGCH-0DB7E135B525-FIGHTUSDT",
    "source_uid": "telegram:-1001275462917:23903",
    "entry_type": "limit",
    "entry_value": 0.004205,
    "stop_loss": 0.00402,
    "leverage": 1.0,
}


def _build(payload: dict[str, object]) -> Any:
    return build_executable_intent(
        envelope_id="ENV-APP-telegram-1001275462917-23903-de432587",
        source="telegram_premium_channel_approved",
        payload=payload,
        symbol="FIGHT/USDT",
        side="buy",
        entry_price=0.004205,
        stop_loss=0.00402,
        targets=[0.004225],
    )


# ── document_id derivation ───────────────────────────────────────────────────


def test_document_id_prefers_signal_id() -> None:
    assert document_id_from_payload(_PREMIUM_PAYLOAD) == "SIG-TGCH-0DB7E135B525-FIGHTUSDT"


def test_document_id_falls_back_to_source_uid() -> None:
    payload = dict(_PREMIUM_PAYLOAD)
    del payload["signal_id"]
    assert document_id_from_payload(payload) == "telegram:-1001275462917:23903"


def test_document_id_blank_and_none_values_skip_to_fallback() -> None:
    payload = dict(_PREMIUM_PAYLOAD, signal_id="   ")
    assert document_id_from_payload(payload) == "telegram:-1001275462917:23903"
    payload["signal_id"] = None
    assert document_id_from_payload(payload) == "telegram:-1001275462917:23903"


def test_document_id_empty_for_payload_without_identity() -> None:
    # dashboard paste / structured text: keeps the previous ""-behaviour
    assert document_id_from_payload({"entry_type": "market"}) == ""


# ── builder propagates into the intent (and keeps prior semantics) ──────────


def test_build_intent_carries_document_id() -> None:
    intent = _build(_PREMIUM_PAYLOAD)
    assert intent.document_id == "SIG-TGCH-0DB7E135B525-FIGHTUSDT"
    # unchanged pre-extraction semantics
    assert intent.idempotency_key == "opbridge:ENV-APP-telegram-1001275462917-23903-de432587"
    assert intent.correlation_id == "ENV-APP-telegram-1001275462917-23903-de432587"
    assert intent.side == "BUY"
    assert intent.order_type == "limit"


def test_build_intent_without_identity_defaults_empty() -> None:
    intent = _build({"entry_type": "limit", "entry_value": 0.004205})
    assert intent.document_id == ""


def test_entry_bounds_and_float_keep_bridge_semantics() -> None:
    assert float_or_none(True) is None
    assert float_or_none(2) == 2.0
    assert entry_bounds({"entry_type": "range", "entry_min": 1.0, "entry_max": 2.0}) == (1.0, 2.0)
    # Pinned pre-extraction quirk, NOT desired behaviour: the chained guard
    # ``emax <= emin <= 0`` only rejects when the range is inverted AND
    # emin <= 0 — positive inverted and negative ranges pass through. Kept 1:1
    # in the extraction PR; fixing it is a separate, deliberate semantics change.
    assert entry_bounds({"entry_type": "range", "entry_min": 2.0, "entry_max": 1.0}) == (2.0, 1.0)
    assert entry_bounds({"entry_type": "range", "entry_min": -1.0, "entry_max": -2.0}) == (
        None,
        None,
    )
    assert entry_bounds({"entry_type": "range", "entry_min": 1.0}) == (None, None)
    assert entry_bounds({"entry_type": "limit"}) == (None, None)


# ── kwargs mapping + audit end-to-end (the regression that bit) ─────────────


def test_paper_kwargs_include_document_id() -> None:
    intent = _build(_PREMIUM_PAYLOAD)
    kwargs = executable_intent_to_paper_kwargs(intent)
    assert kwargs["document_id"] == "SIG-TGCH-0DB7E135B525-FIGHTUSDT"


def test_audit_events_carry_document_id_through_close(tmp_path) -> None:
    """order_created, order_filled context and position_closed must be joinable
    to the originating premium signal — exactly what was broken live."""
    import json

    audit = tmp_path / "audit.jsonl"
    engine = PaperExecutionEngine(
        initial_equity=10000.0, live_enabled=False, audit_log_path=str(audit)
    )

    intent = build_executable_intent(
        envelope_id="ENV-TEST-1",
        source="telegram_premium_channel_approved",
        payload=dict(_PREMIUM_PAYLOAD, entry_type="market", quantity=100.0),
        symbol="FIGHT/USDT",
        side="buy",
        entry_price=None,
        stop_loss=0.00402,
        targets=[0.004225],
        quantity=100.0,
    )
    order, fill = engine.execute_intent(intent, current_price=0.004205, risk_check_id="rck_test")
    assert fill is not None
    assert order.document_id == "SIG-TGCH-0DB7E135B525-FIGHTUSDT"

    engine.close_position("FIGHT/USDT", 0.0042, reason="stop")

    events = [json.loads(line) for line in audit.read_text().splitlines()]
    by_type = {e["event_type"]: e for e in events}
    assert by_type["order_created"]["document_id"] == "SIG-TGCH-0DB7E135B525-FIGHTUSDT"
    assert by_type["position_closed"]["document_id"] == "SIG-TGCH-0DB7E135B525-FIGHTUSDT"
