"""Goal 2026-06-10 — paper-learning P1 safety harness.

Covers:
  (A) max_daily_paper_entries cap: default 0 == no-op; cap=N blocks the (N+1)th
      autonomous entry of the UTC day with status PAPER_CAP_REACHED + reason_code.
  (C) paper_trade_label record: every autonomous fill emits a fully-labelled
      production_paper record (mode/direction/source/confidence/threshold/regime).

Behaviour-level: drives ``run_cycle`` end-to-end through the real
SignalGenerator + MockMarketDataAdapter + PaperExecutionEngine, asserting on the
returned cycle status and the audit files on disk — not on private internals.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.orchestrator.models import CycleStatus
from app.orchestrator.trading_loop import (
    TradingLoop,
    count_paper_entries_today,
)
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.signals.generator import SignalGenerator


def _limits(**overrides) -> RiskLimits:
    defaults = {
        "initial_equity": 100000.0,
        "max_risk_per_trade_pct": 0.25,
        "max_daily_loss_pct": 100.0,  # never trip the loss limit in these tests
        "max_total_drawdown_pct": 100.0,
        "max_open_positions": 50,  # never trip the position cap
        "max_leverage": 1.0,
        "require_stop_loss": True,
        "allow_averaging_down": False,
        "allow_martingale": False,
        "kill_switch_enabled": True,
        "min_signal_confidence": 0.75,
        "min_signal_confluence_count": 2,
    }
    defaults.update(overrides)
    return RiskLimits(**defaults)


def _loop(tmp_path: Path) -> TradingLoop:
    exec_eng = PaperExecutionEngine(
        initial_equity=100000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "exec_audit.jsonl"),
    )
    return TradingLoop(
        risk_engine=RiskEngine(_limits()),
        execution_engine=exec_eng,
        market_data_adapter=MockMarketDataAdapter(),
        signal_generator=SignalGenerator(
            min_confidence=0.75,
            min_confluence=2,
            stop_loss_pct=2.5,
            take_profit_pct=5.0,
        ),
        audit_log_path=str(tmp_path / "loop_audit.jsonl"),
    )


def _bullish(document_id: str) -> AnalysisResult:
    return AnalysisResult(
        document_id=document_id,
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.85,
        relevance_score=0.90,
        impact_score=0.80,
        confidence_score=0.85,
        novelty_score=0.70,
        actionable=True,
        affected_assets=["BTC", "BTC/USDT"],
        tags=["etf", "bullish"],
        spam_probability=0.02,
        explanation_short="Strong bullish catalyst.",
        explanation_long="Detail.",
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ── count_paper_entries_today helper ──────────────────────────────────────────


def _write_audit(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_count_missing_file_is_zero(tmp_path: Path) -> None:
    assert count_paper_entries_today(tmp_path / "nope.jsonl") == 0


def test_count_only_today_opening_fills(tmp_path: Path) -> None:
    now = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    today = now.date().isoformat()
    yesterday = "2026-06-09"
    audit = tmp_path / "exec.jsonl"
    _write_audit(
        audit,
        [
            # today opening long → counts
            {
                "event_type": "order_filled",
                "timestamp_utc": f"{today}T01:00:00+00:00",
                "side": "buy",
                "position_side": "long",
            },
            # today opening short → counts
            {
                "event_type": "order_filled",
                "timestamp_utc": f"{today}T02:00:00+00:00",
                "side": "sell",
                "position_side": "short",
            },
            # today EXIT of a long (sell/long) → must NOT count
            {
                "event_type": "order_filled",
                "timestamp_utc": f"{today}T03:00:00+00:00",
                "side": "sell",
                "position_side": "long",
            },
            # today EXIT of a short (buy/short) → must NOT count
            {
                "event_type": "order_filled",
                "timestamp_utc": f"{today}T04:00:00+00:00",
                "side": "buy",
                "position_side": "short",
            },
            # yesterday opening → out of window
            {
                "event_type": "order_filled",
                "timestamp_utc": f"{yesterday}T23:00:00+00:00",
                "side": "buy",
                "position_side": "long",
            },
            # non-fill event today → ignored
            {
                "event_type": "order_created",
                "timestamp_utc": f"{today}T05:00:00+00:00",
                "side": "buy",
                "position_side": "long",
            },
        ],
    )
    assert count_paper_entries_today(audit, now=now) == 2


def test_count_legacy_rows_without_position_side_default_long(tmp_path: Path) -> None:
    now = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    today = now.date().isoformat()
    audit = tmp_path / "exec.jsonl"
    _write_audit(
        audit,
        [
            {
                "event_type": "order_filled",
                "timestamp_utc": f"{today}T01:00:00+00:00",
                "side": "buy",
            },  # legacy: no position_side → treated as long open
        ],
    )
    assert count_paper_entries_today(audit, now=now) == 1


# ── (A) cap enforcement via run_cycle ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cap_default_zero_is_no_op(tmp_path: Path, monkeypatch) -> None:
    """Default cap (0) must never block — behaviour identical to today."""
    monkeypatch.delenv("EXECUTION_MAX_DAILY_PAPER_ENTRIES", raising=False)
    loop = _loop(tmp_path)
    c1 = await loop.run_cycle(_bullish("d1"), "BTC/USDT")
    c2 = await loop.run_cycle(_bullish("d2"), "ETH/USDT")
    assert c1.status == CycleStatus.COMPLETED
    assert c2.status == CycleStatus.COMPLETED


@pytest.mark.asyncio
async def test_cap_blocks_after_n_entries(tmp_path: Path, monkeypatch) -> None:
    """cap=1 → first entry fills, second is PAPER_CAP_REACHED with reason_code."""
    monkeypatch.setenv("EXECUTION_MAX_DAILY_PAPER_ENTRIES", "1")
    loop = _loop(tmp_path)

    c1 = await loop.run_cycle(_bullish("d1"), "BTC/USDT")
    assert c1.status == CycleStatus.COMPLETED  # under cap

    c2 = await loop.run_cycle(_bullish("d2"), "ETH/USDT")
    assert c2.status == CycleStatus.PAPER_CAP_REACHED
    joined = "|".join(c2.notes)
    assert "paper_daily_cap_reached:1|cap:1" in joined
    assert "reason_code:PAPER_DAILY_CAP_REACHED" in joined


@pytest.mark.asyncio
async def test_cap_two_allows_two_then_blocks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_MAX_DAILY_PAPER_ENTRIES", "2")
    loop = _loop(tmp_path)
    s1 = (await loop.run_cycle(_bullish("d1"), "BTC/USDT")).status
    s2 = (await loop.run_cycle(_bullish("d2"), "ETH/USDT")).status
    s3 = (await loop.run_cycle(_bullish("d3"), "SOL/USDT")).status
    assert s1 == CycleStatus.COMPLETED
    assert s2 == CycleStatus.COMPLETED
    assert s3 == CycleStatus.PAPER_CAP_REACHED


# ── (C) paper_trade_label record ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fill_emits_labelled_trade_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "paper")
    audit_path = tmp_path / "exec_audit.jsonl"
    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(_bullish("doc_label"), "BTC/USDT")
    assert cycle.status == CycleStatus.COMPLETED

    rows = _read_jsonl(audit_path)
    labels = [r for r in rows if r.get("event_type") == "paper_trade_label"]
    assert len(labels) == 1
    label = labels[0]

    # All six required axes present and correctly typed.
    assert label["trade_class"] == "production_paper"
    assert label["mode"] == "paper"
    assert label["direction"] == "long"
    assert label["source_name"] == "autonomous_generator"  # real doc id → generator
    assert label["source_id"] == "doc_label"
    assert label["confidence"] == pytest.approx(0.85)
    assert "threshold_used" in label
    assert "regime" in label  # forensic stamp key present (value may be None)
    # Joinable back to the canonical fill.
    assert label["order_id"]
    assert label["fill_id"]
    assert label["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_no_label_emitted_when_no_fill(tmp_path: Path, monkeypatch) -> None:
    """A cycle that produces no signal must not emit a trade-label."""
    monkeypatch.delenv("EXECUTION_MAX_DAILY_PAPER_ENTRIES", raising=False)
    audit_path = tmp_path / "exec_audit.jsonl"
    loop = _loop(tmp_path)
    neutral = AnalysisResult(
        document_id="doc_neutral",
        sentiment_label=SentimentLabel.NEUTRAL,
        sentiment_score=0.0,
        relevance_score=0.2,
        impact_score=0.1,
        confidence_score=0.4,
        novelty_score=0.1,
        actionable=False,
        affected_assets=[],
        tags=[],
        spam_probability=0.1,
        explanation_short="No event.",
        explanation_long="Nothing.",
    )
    cycle = await loop.run_cycle(neutral, "BTC/USDT")
    assert cycle.status != CycleStatus.COMPLETED
    labels = [r for r in _read_jsonl(audit_path) if r.get("event_type") == "paper_trade_label"]
    assert labels == []
