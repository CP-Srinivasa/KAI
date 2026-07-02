"""NEO-P-002-r3 — integration: r3 provider output through the REAL loop.

Closes the two crosscheck findings on PR #174 that the driver-level unit tests
could not cover because they used a spy runner:

  Finding 1 (No-Execution): proving the *driver* owns no execution is not the
    same as proving ``run_cycle(SHADOW, real_analysis)`` itself produces no
    order/fill. Here we run the REAL ``TradingLoop`` (entry_mode=disabled,
    shadow_diagnostics=on) and assert order_created is False AND no
    ``order_filled`` event is written.

  Finding 2 (source attribution): the whole point of r3 is that a real, mapped
    analysis carries a non-``loop_control_*`` document_id and is therefore
    attributed ``source=autonomous_generator`` (NOT ``canary_probe``). The spy
    can never observe the loop's attribution. Here we feed an AnalysisResult
    produced by the r3 provider's own ``canonical_to_analysis_result`` and assert
    the recorded shadow candidate's source on the real ledger.

Construction mirrors ``test_trading_loop_shadow_candidate.py`` (MockMarketData,
real generator/risk/exec) so the only new variable is *where the analysis came
from*: the r3 provider, not a hand-written fixture.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.core.domain.document import CanonicalDocument
from app.core.enums import SentimentLabel
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.observability.real_analysis_provider import canonical_to_analysis_result
from app.orchestrator.models import CycleStatus
from app.orchestrator.trading_loop import TradingLoop
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.signals.generator import SignalGenerator


def _limits() -> RiskLimits:
    return RiskLimits(
        initial_equity=10000.0,
        max_risk_per_trade_pct=0.25,
        max_daily_loss_pct=1.0,
        max_total_drawdown_pct=5.0,
        max_open_positions=5,
        max_leverage=1.0,
        require_stop_loss=True,
        allow_averaging_down=False,
        allow_martingale=False,
        kill_switch_enabled=True,
        min_signal_confidence=0.75,
        min_signal_confluence_count=2,
        regime_filter_enabled=False,
    )


def _loop(tmp_path: Path) -> TradingLoop:
    exec_eng = PaperExecutionEngine(
        initial_equity=10000.0,
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
            min_confidence=0.75, min_confluence=2, stop_loss_pct=2.5, take_profit_pct=5.0
        ),
        audit_log_path=str(tmp_path / "loop_audit.jsonl"),
    )


def _strong_real_doc() -> CanonicalDocument:
    """A REAL analyzed document (genuine uuid id, no loop_control_ prefix) whose
    mapped AnalysisResult is strong enough to make the generator emit a signal —
    so a shadow candidate is actually recorded and its source is observable."""
    did = uuid.uuid4()
    return CanonicalDocument(
        id=did,
        url=f"https://example.test/{did}",
        title="ETH strong bullish catalyst",
        subtitle="Detailed reasoning for the directional call.",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.85,
        relevance_score=0.90,
        impact_score=0.80,
        novelty_score=0.70,
        credibility_score=0.85,  # → mapped confidence_score 0.85 (>= 0.75 gate)
        spam_probability=0.02,
        directional_confidence=0.80,
        priority_score=10,
        tickers=["ETH"],
        tags=["bullish"],
    )


def _ledger(tmp_path: Path) -> Path:
    return tmp_path / "artifacts" / "shadow_candidate_ledger.jsonl"


@pytest.mark.asyncio
async def test_r3_real_analysis_through_real_loop_is_autonomous_and_no_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "disabled")
    monkeypatch.setenv("EXECUTION_SHADOW_DIAGNOSTICS", "true")
    monkeypatch.chdir(tmp_path)

    doc = _strong_real_doc()
    # The r3 provider produces the analysis — NOT a hand-written fixture. This is
    # exactly the object the shadow-real feed would inject into the loop.
    analysis = canonical_to_analysis_result(doc)
    assert analysis.document_id == str(doc.id)  # provenance preserved by the mapper

    loop = _loop(tmp_path)
    cycle = await loop.run_cycle(analysis, "ETH/USDT")

    # Finding 1 — No-Execution against the REAL loop (not a spy): blocked, no order.
    assert cycle.status == CycleStatus.ENTRY_MODE_BLOCKED
    assert cycle.order_created is False
    assert cycle.signal_generated is True  # pipeline really ran, candidate recorded

    exec_audit = tmp_path / "exec_audit.jsonl"
    if exec_audit.exists():
        events = [
            json.loads(x) for x in exec_audit.read_text(encoding="utf-8").splitlines() if x.strip()
        ]
        assert not any(e.get("event_type") == "order_filled" for e in events)
    # no position opened by the shadow cycle
    assert len(loop._exec.portfolio.positions) == 0

    # Finding 2 — the real loop attributes the r3-fed analysis as the REAL
    # generator, never the canary probe.
    ledger = _ledger(tmp_path)
    assert ledger.exists()
    rows = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(rows) == 1
    rec = rows[0]
    assert rec["source"] == "autonomous_generator"
    assert rec["signal_origin"] == "autonomous_generator"
    assert rec["is_canary"] is False
    assert rec["candidate_kind"] == "signal_candidate"
    assert rec["source_stage"] == "signal_generator"
    assert rec["document_id"] == str(doc.id)  # real doc-id, not loop_control_*
    assert rec["schema_version"] == "v2"
