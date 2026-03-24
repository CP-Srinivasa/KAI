"""Tests for the canonical DecisionRecord-backed journal compatibility layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.decisions.journal import (
    DecisionJournalSummary,
    RiskAssessment,
    append_decision_jsonl,
    build_decision_journal_summary,
    create_decision_instance,
    load_decision_journal,
)
from app.execution.models import DecisionRecord


def _risk_kwargs() -> dict[str, object]:
    return {
        "risk_level": "low",
        "max_position_pct": 0.25,
        "drawdown_remaining_pct": 95.0,
    }


def _valid_kwargs(*, mode: str = "paper") -> dict[str, object]:
    return {
        "symbol": "BTC/USDT",
        "market": "crypto",
        "venue": "binance_paper",
        "mode": mode,
        "thesis": "BTC bullish breakout above resistance",
        "supporting_factors": ["Volume spike", "RSI divergence"],
        "contradictory_factors": ["High funding rate"],
        "confidence_score": 0.85,
        "market_regime": "bullish",
        "volatility_state": "moderate",
        "liquidity_state": "healthy",
        "risk_assessment": RiskAssessment(**_risk_kwargs()),
        "entry_logic": "Break above 68k",
        "exit_logic": "Trail stop at 2%",
        "stop_loss": 66500.0,
        "take_profit": 72000.0,
        "invalidation_condition": "Close below 65k",
        "position_size_rationale": "0.25% risk per trade",
        "max_loss_estimate": 25.0,
        "data_sources_used": ["CryptoPanic", "TradingView"],
        "model_version": "gpt-4o-2024-11-20",
        "prompt_version": "v1.2",
    }


def _create(**overrides: object) -> DecisionRecord:
    payload = _valid_kwargs()
    payload.update(overrides)
    return create_decision_instance(**payload)


def test_create_decision_instance_returns_canonical_decision_record() -> None:
    decision = _create()

    assert isinstance(decision, DecisionRecord)
    assert decision.mode.value == "paper"
    assert decision.approval_state.value == "audit_only"
    assert decision.execution_state.value == "paper_only"
    assert decision.decision_id.startswith("dec_")


def test_create_decision_instance_research_stays_non_executable() -> None:
    decision = _create(mode="research")

    assert decision.approval_state.value == "audit_only"
    assert decision.execution_state.value == "not_executable"


def test_create_requires_thesis_min_length() -> None:
    with pytest.raises(ValueError, match="thesis"):
        _create(thesis="short")


def test_create_requires_supporting_factors() -> None:
    with pytest.raises(ValueError, match="supporting_factors"):
        _create(supporting_factors=[])


def test_create_requires_data_sources() -> None:
    with pytest.raises(ValueError, match="data_sources_used"):
        _create(data_sources_used=[])


def test_create_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        _create(mode="invalid_mode")


def test_create_rejects_blank_symbol() -> None:
    with pytest.raises(ValueError, match="symbol"):
        _create(symbol="  ")


def test_create_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError, match="confidence_score"):
        _create(confidence_score=1.5)


def test_decision_instance_is_frozen() -> None:
    decision = _create()
    with pytest.raises((ValidationError, TypeError, AttributeError)):
        decision.symbol = "ETH/USDT"  # type: ignore[misc]


def test_journal_summary_frozen() -> None:
    summary = DecisionJournalSummary(
        generated_at="x",
        journal_path="y",
        total_count=0,
        by_mode={},
        by_approval={},
        by_execution={},
        symbols=[],
    )
    with pytest.raises(AttributeError):
        summary.total_count = 99  # type: ignore[misc]


def test_summary_execution_enabled_always_false() -> None:
    summary = DecisionJournalSummary(
        generated_at="x",
        journal_path="y",
        total_count=0,
        by_mode={},
        by_approval={},
        by_execution={},
        symbols=[],
    )
    payload = summary.to_json_dict()
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


def test_append_creates_file(tmp_path: Path) -> None:
    decision = _create()
    path = tmp_path / "journal.jsonl"

    result = append_decision_jsonl(decision, path)

    assert result.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["symbol"] == "BTC/USDT"
    assert "report_type" not in payload


def test_append_is_additive(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"

    append_decision_jsonl(_create(mode="paper"), path)
    append_decision_jsonl(_create(mode="research"), path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_load_empty_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "nonexistent.jsonl"
    assert load_decision_journal(path) == []


def test_load_round_trip_current_canonical_rows(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    original = _create(timestamp_utc="2026-01-01T00:00:00+00:00")
    append_decision_jsonl(original, path)

    loaded = load_decision_journal(path)

    assert loaded == [original]


def test_load_normalizes_legacy_rows_into_canonical_decision_record(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    legacy_payload = {
        "report_type": "decision_instance",
        "decision_id": "legacy_001",
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        "symbol": "BTC/USDT",
        "market": "crypto",
        "venue": "paper",
        "mode": "research",
        "thesis": "BTC breakout supported by strong accumulation trend",
        "supporting_factors": ["Accumulation"],
        "contradictory_factors": [],
        "confidence_score": 0.8,
        "market_regime": "bullish",
        "volatility_state": "moderate",
        "liquidity_state": "healthy",
        "risk_assessment": {
            "risk_level": "low",
            "max_position_pct": 0.25,
            "drawdown_remaining_pct": 95.0,
        },
        "entry_logic": "Break above resistance",
        "exit_logic": "Trail stop",
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "invalidation_condition": "Close below support",
        "position_size_rationale": "manual sizing",
        "max_loss_estimate": 0.0,
        "data_sources_used": ["operator_input"],
        "model_version": "manual",
        "prompt_version": "v0",
        "approval_state": "pending",
        "execution_state": "pending",
    }
    path.write_text(json.dumps(legacy_payload) + "\n", encoding="utf-8")

    loaded = load_decision_journal(path)

    assert len(loaded) == 1
    record = loaded[0]
    assert isinstance(record, DecisionRecord)
    assert record.approval_state.value == "pending"
    assert record.execution_state.value == "not_executable"
    assert record.entry_logic.summary == "Break above resistance"
    assert record.exit_logic.summary == "Trail stop"
    assert record.stop_loss is None
    assert record.take_profit is None


def test_load_fails_closed_on_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text('{"decision_id":"bad"}\nINVALID_JSON\n', encoding="utf-8")

    with pytest.raises(ValueError, match="line 1|line 2"):
        load_decision_journal(path)


def test_summary_empty_journal() -> None:
    summary = build_decision_journal_summary([])
    assert summary.total_count == 0
    assert summary.symbols == []
    assert summary.avg_confidence is None
    assert summary.latest_decision_id is None


def test_summary_counts_canonical_states() -> None:
    entries = [
        _create(mode="paper"),
        _create(mode="paper", symbol="ETH/USDT"),
        _create(mode="research", symbol="SOL/USDT"),
    ]

    summary = build_decision_journal_summary(entries)

    assert summary.total_count == 3
    assert summary.by_mode == {"paper": 2, "research": 1}
    assert summary.by_approval == {"audit_only": 3}
    assert summary.by_execution == {"paper_only": 2, "not_executable": 1}
    assert summary.avg_confidence == 0.85


def test_summary_latest_tracks_last_entry() -> None:
    entries = [
        _create(symbol="BTC/USDT", timestamp_utc="2026-01-01T00:00:00+00:00"),
        _create(symbol="ETH/USDT", timestamp_utc="2026-01-02T00:00:00+00:00"),
        _create(symbol="SOL/USDT", timestamp_utc="2026-01-03T00:00:00+00:00"),
    ]

    summary = build_decision_journal_summary(entries)

    assert summary.latest_timestamp == "2026-01-03T00:00:00+00:00"
