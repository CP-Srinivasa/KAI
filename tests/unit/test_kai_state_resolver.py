"""Tests for app.messaging.kai_state_resolver."""

from __future__ import annotations

from app.messaging.kai_state_resolver import (
    KAI_STATE_PRIORITY,
    KaiRuntimeState,
    create_fallback_state,
    fail_closed_state,
    is_valid_kai_state,
    resolve_kai_state,
)


def _rt(state: str, comment: str = "test") -> KaiRuntimeState:
    return KaiRuntimeState(
        state=state,
        severity="info",
        priority=KAI_STATE_PRIORITY[state],
        status_label=state,
        color="#000",
        icon="kai_test",
        animation="test",
        comment=comment,
        timestamp="2026-05-03T00:00:00+00:00",
    )


def test_error_overrides_warning():
    winner = resolve_kai_state([_rt("WARNING"), _rt("ERROR")])
    assert winner.state == "ERROR"


def test_warning_overrides_signal():
    winner = resolve_kai_state([_rt("SIGNAL"), _rt("WARNING")])
    assert winner.state == "WARNING"


def test_signal_overrides_security():
    winner = resolve_kai_state([_rt("SECURITY"), _rt("SIGNAL")])
    assert winner.state == "SIGNAL"


def test_security_overrides_analysis():
    winner = resolve_kai_state([_rt("ANALYSIS"), _rt("SECURITY")])
    assert winner.state == "SECURITY"


def test_analysis_overrides_idle():
    winner = resolve_kai_state([_rt("IDLE"), _rt("ANALYSIS")])
    assert winner.state == "ANALYSIS"


def test_offline_is_lowest_priority():
    winner = resolve_kai_state([_rt("OFFLINE"), _rt("IDLE")])
    assert winner.state == "IDLE"


def test_empty_list_returns_offline_fallback():
    winner = resolve_kai_state([])
    assert winner.state == "OFFLINE"
    assert winner.source == "fallback"


def test_winner_keeps_payload():
    winner = resolve_kai_state([_rt("IDLE", "calm"), _rt("ERROR", "fire")])
    assert winner.state == "ERROR"
    assert winner.comment == "fire"


def test_create_fallback_state_for_error():
    state = create_fallback_state("ERROR", "explode")
    assert state.state == "ERROR"
    assert state.severity == "critical"
    assert state.comment == "explode"


def test_fail_closed_state_forces_error():
    state = fail_closed_state("config invalid")
    assert state.state == "ERROR"
    assert state.severity == "critical"
    assert state.source == "fail_closed_guard"
    assert "config invalid" in state.comment


def test_is_valid_kai_state():
    for s in ("IDLE", "ANALYSIS", "SIGNAL", "WARNING", "SECURITY", "ERROR", "OFFLINE"):
        assert is_valid_kai_state(s)
    assert not is_valid_kai_state("PARTY")
    assert not is_valid_kai_state(123)
    assert not is_valid_kai_state(None)
    assert not is_valid_kai_state("")


def test_to_dict_serialises_with_camelcase():
    state = create_fallback_state("ANALYSIS", "scanning")
    d = state.to_dict()
    assert d["state"] == "ANALYSIS"
    assert d["statusLabel"] == "ANALYSIS"
    assert "comment" in d
    assert "timestamp" in d


def test_invalid_state_is_sanitised_to_offline():
    bogus = KaiRuntimeState(
        state="WHATEVER",  # invalid
        severity="info",
        priority=999,
        status_label="X",
        color="#000",
        icon="x",
        animation="x",
        comment="bogus",
        timestamp="2026-05-03T00:00:00+00:00",
    )
    winner = resolve_kai_state([bogus])
    assert winner.state == "OFFLINE"
    assert winner.source == "fallback"
