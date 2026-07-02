import pytest

from app.signals.models import (
    IllegalStateTransitionError,
    SignalCandidate,
    SignalDirection,
    SignalState,
    SignalStateMachine,
)


def test_legal_transitions():
    SignalStateMachine.validate_transition(SignalState.PENDING, SignalState.APPROVED)
    SignalStateMachine.validate_transition(SignalState.PENDING, SignalState.REJECTED)
    SignalStateMachine.validate_transition(SignalState.PENDING, SignalState.CANCELLED)
    SignalStateMachine.validate_transition(SignalState.APPROVED, SignalState.EXECUTED)
    SignalStateMachine.validate_transition(SignalState.APPROVED, SignalState.REJECTED)
    SignalStateMachine.validate_transition(SignalState.APPROVED, SignalState.CANCELLED)
    SignalStateMachine.validate_transition(SignalState.EXECUTED, SignalState.CLOSED)


def test_illegal_transitions():
    illegal_cases = [
        (SignalState.CLOSED, SignalState.EXECUTED),
        (SignalState.REJECTED, SignalState.APPROVED),
        (SignalState.EXECUTED, SignalState.APPROVED),
        (SignalState.PENDING, SignalState.CLOSED),
        (SignalState.CANCELLED, SignalState.EXECUTED),
    ]
    for from_state, to_state in illegal_cases:
        with pytest.raises(IllegalStateTransitionError):
            SignalStateMachine.validate_transition(from_state, to_state)


def test_immutability():
    candidate = SignalCandidate(
        decision_id="test_decision",
        timestamp_utc="2026-05-14T00:00:00+00:00",
        symbol="BTC/USDT",
        market="crypto",
        venue="paper",
        mode="paper",
        direction=SignalDirection.LONG,
        thesis="Test",
        supporting_factors=(),
        contradictory_factors=(),
        confidence_score=0.9,
        confluence_count=3,
        market_regime="trending",
        volatility_state="normal",
        liquidity_state="adequate",
        entry_price=60000.0,
        stop_loss_price=58000.0,
        take_profit_price=64000.0,
        invalidation_condition="Test",
        risk_assessment="Low",
        position_size_rationale="Test",
        max_loss_estimate_pct=0.02,
        data_sources_used=("test",),
        source_document_id="doc1",
        model_version="1.0",
        prompt_version="1.0",
        execution_state=SignalState.APPROVED,
    )

    updated, transition = candidate.with_execution_state(
        SignalState.EXECUTED, source="test", reason="test_filled"
    )

    assert updated is not candidate
    assert updated.execution_state == SignalState.EXECUTED
    assert candidate.execution_state == SignalState.APPROVED


def test_illegal_state_transition_error_is_value_error():
    assert issubclass(IllegalStateTransitionError, ValueError)

    try:
        SignalStateMachine.validate_transition(SignalState.CLOSED, SignalState.EXECUTED)
    except ValueError as e:
        assert isinstance(e, IllegalStateTransitionError)
