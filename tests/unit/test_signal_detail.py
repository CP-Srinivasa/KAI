"""Unit tests for the read-only signal-detail aggregator (operator endpoint)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.decisions import signal_detail


def _rec(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "decision_id": "DS-1",
        "timestamp_utc": "2026-06-03T12:00:00Z",
        "symbol": "BTC/USDT",
        "market": "crypto",
        "mode": "paper",
        "venue": "paper",
        "confidence_score": 0.62,
        "market_regime": "trend_up/vol_low",
        "volatility_state": "low",
        "liquidity_state": "ok",
        "stop_loss": 100.0,
        "take_profit": 130.0,
        "max_loss_estimate": 25.0,
        "approval_state": "audit_only",
        "execution_state": "filled",
        "thesis": "strong catalyst",
        "entry_logic": "break and retest",
        "exit_logic": "tp or sl",
        "invalidation_condition": "loses level",
        "supporting_factors": ["a", "b"],
        "contradictory_factors": ["c"],
        "position_size_rationale": "1% risk",
        "risk_assessment": {"risk_level": "medium"},
        "data_sources_used": ["coingecko"],
        "model_version": "m1",
        "prompt_version": "p1",
        "document_id": "doc-1",
    }
    base.update(over)
    return base


@pytest.fixture
def patch_journal(monkeypatch):
    def _set(records: list[dict[str, Any]]):
        monkeypatch.setattr(signal_detail, "load_decision_journal", lambda _p: records)

    return _set


def test_happy_path(patch_journal, tmp_path: Path) -> None:
    patch_journal([_rec(), _rec(decision_id="DS-2", symbol="ETH/USDT")])
    out = signal_detail.build_signal_detail("DS-1", audit_path=tmp_path / "none.jsonl")
    assert out is not None
    assert out["signal_id"] == "DS-1"
    assert out["symbol"] == "BTC/USDT"
    assert out["confidence"] == 0.62
    assert out["confidence_status"] == "available"
    assert out["risk_geometry"] == {
        "stop_loss": 100.0,
        "take_profit": 130.0,
        "max_loss_estimate": 25.0,
    }
    assert out["explain_summary"] == "strong catalyst"
    assert out["linked_execution"] is None


def test_unknown_id_returns_none(patch_journal, tmp_path: Path) -> None:
    patch_journal([_rec()])
    assert signal_detail.build_signal_detail("NOPE", audit_path=tmp_path / "x.jsonl") is None


def test_missing_confidence_is_not_available(patch_journal, tmp_path: Path) -> None:
    rec = _rec()
    del rec["confidence_score"]
    patch_journal([rec])
    out = signal_detail.build_signal_detail("DS-1", audit_path=tmp_path / "x.jsonl")
    assert out is not None
    assert out["confidence"] is None
    assert out["confidence_status"] == "not_available"


def test_no_fabricated_side_when_absent(patch_journal, tmp_path: Path) -> None:
    patch_journal([_rec()])
    out = signal_detail.build_signal_detail("DS-1", audit_path=tmp_path / "x.jsonl")
    assert out is not None
    assert out["side"] is None
    assert out["side_status"] == "not_available"


def test_linked_execution_join_and_side(patch_journal, tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    audit.write_text(
        json.dumps({"decision_id": "OTHER", "event_type": "x"})
        + "\n"
        + json.dumps(
            {
                "decision_id": "DS-1",
                "event_type": "order_filled",
                "order_id": "o-9",
                "side": "buy",
                "symbol": "BTC/USDT",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    patch_journal([_rec()])
    out = signal_detail.build_signal_detail("DS-1", audit_path=audit)
    assert out is not None
    assert out["linked_execution"]["order_id"] == "o-9"
    assert out["side"] == "buy"  # resolved from the linked execution
    assert out["side_status"] == "available"


def test_malformed_audit_is_fail_soft(patch_journal, tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    audit.write_text("{not json}\n", encoding="utf-8")
    patch_journal([_rec()])
    out = signal_detail.build_signal_detail("DS-1", audit_path=audit)
    assert out is not None
    assert out["linked_execution"] is None  # malformed audit never breaks the lookup


def test_malformed_journal_propagates(monkeypatch, tmp_path: Path) -> None:
    def _raise(_p):
        raise ValueError("malformed journal")

    monkeypatch.setattr(signal_detail, "load_decision_journal", _raise)
    with pytest.raises(ValueError):
        signal_detail.build_signal_detail("DS-1", audit_path=tmp_path / "x.jsonl")


def test_explain_happy_and_caveats(patch_journal) -> None:
    rec = _rec()
    del rec["confidence_score"]  # -> caveat
    patch_journal([rec])
    out = signal_detail.build_signal_explain("DS-1")
    assert out is not None
    assert out["thesis"] == "strong catalyst"
    assert out["supporting_factors"] == ["a", "b"]
    assert "confidence_not_available" in out["caveats"]
    assert "gate_decision_not_recorded" in out["caveats"]


def test_explain_unknown_returns_none(patch_journal) -> None:
    patch_journal([_rec()])
    assert signal_detail.build_signal_explain("NOPE") is None
