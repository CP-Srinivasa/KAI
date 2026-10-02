"""Einstiegs-Fills tragen denselben Kursbeleg wie Monitor-Closes.

Anlass (02.10.2026): Die MATIC/USDT-Short vom 23.09. wurde zu 0.40875 gefuellt,
dem eingefrorenen Kurs eines abgewickelten BitMEX-Kontrakts. Im Audit stand der
Einstieg mit ``price_source: ""``, ``market_data_is_stale: null`` und ohne
Beobachtungszeit — die Quelle liess sich nur ueber den Zyklus-Audit
rekonstruieren. Nur der Positionsmonitor gab bisher einen ``PriceEvidence`` mit;
beide Einstiegsaufrufe der Schleife fuellten ohne.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.execution.paper_engine import PaperExecutionEngine
from app.execution.price_evidence import quote_evidence
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.market_data.models import MarketDataPoint
from app.orchestrator.models import CycleStatus
from app.orchestrator.trading_loop import TradingLoop
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.signals.generator import SignalGenerator
from app.signals.tv_consumer import load_pending_promoted

_OBSERVED_AT = "2026-10-02T07:00:00+00:00"


def _point(**overrides: object) -> MarketDataPoint:
    base = MarketDataPoint(
        symbol="BTC/USDT",
        timestamp_utc=_OBSERVED_AT,
        price=65000.0,
        volume_24h=1.0e9,
        change_pct_24h=0.5,
        source="bybit",
        is_stale=False,
        freshness_seconds=1.5,
        corroborated_by="okx",
        corroborating_price=65010.0,
    )
    return replace(base, **overrides)


# ── Baustein ────────────────────────────────────────────────────────────────


def test_quote_evidence_traegt_quelle_zeit_alter_und_bestaetigung() -> None:
    ev = quote_evidence(_point())

    assert ev.source == "bybit"
    assert ev.observed_at_utc == _OBSERVED_AT
    assert ev.observed_price == pytest.approx(65000.0)
    assert ev.age_ms == pytest.approx(1500.0)
    assert ev.is_stale is False
    assert ev.corroborated_by == "okx"
    assert ev.corroborating_price == pytest.approx(65010.0)


def test_quote_evidence_raet_nichts() -> None:
    @dataclass
    class _Duenn:
        source: str = "coingecko"
        timestamp_utc: str = ""
        price: float = 0.1
        is_stale: bool = False

    ev = quote_evidence(_Duenn())

    assert ev.age_ms is None  # kein freshness_seconds -> unbekannt, nicht 0
    assert ev.observed_at_utc == ""
    assert ev.corroborated_by == ""
    assert ev.corroborating_price is None
    assert quote_evidence(_point(corroborating_price=float("nan"))).corroborating_price is None
    assert quote_evidence(_point(corroborating_price=0.0)).corroborating_price is None


# ── Einstiege der Schleife ──────────────────────────────────────────────────


class _QuotedMock(MockMarketDataAdapter):
    """Mock-Kette, deren Quote wie ein echter Anbieter ausgewiesen ist."""

    def __init__(self, price: float | None = None) -> None:
        super().__init__()
        self._price = price

    async def get_market_data_point(self, symbol: str) -> MarketDataPoint | None:
        point = await super().get_market_data_point(symbol)
        if point is None:
            return None
        return replace(
            point,
            price=self._price if self._price is not None else point.price,
            timestamp_utc=_OBSERVED_AT,
            source="bybit",
            freshness_seconds=2.0,
            is_stale=False,
        )


def _loop(tmp_path: Path, adapter: MockMarketDataAdapter) -> TradingLoop:
    limits = RiskLimits(
        initial_equity=100000.0,
        max_risk_per_trade_pct=0.25,
        max_daily_loss_pct=100.0,
        max_total_drawdown_pct=100.0,
        max_open_positions=50,
        max_leverage=1.0,
        require_stop_loss=True,
        allow_averaging_down=False,
        allow_martingale=False,
        kill_switch_enabled=True,
        min_signal_confidence=0.75,
        min_signal_confluence_count=2,
    )
    return TradingLoop(
        risk_engine=RiskEngine(limits),
        execution_engine=PaperExecutionEngine(
            initial_equity=100000.0,
            fee_pct=0.1,
            slippage_pct=0.05,
            live_enabled=False,
            audit_log_path=str(tmp_path / "exec_audit.jsonl"),
        ),
        market_data_adapter=adapter,
        signal_generator=SignalGenerator(
            min_confidence=0.75, min_confluence=2, stop_loss_pct=2.5, take_profit_pct=5.0
        ),
        audit_log_path=str(tmp_path / "loop_audit.jsonl"),
    )


def _entry_fills(tmp_path: Path) -> list[dict]:
    path = tmp_path / "exec_audit.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r.get("event_type") == "order_filled"]


def _assert_beleg(fill: dict) -> None:
    assert fill["price_source"] == "bybit"
    assert fill["price_observed_at_utc"] == _OBSERVED_AT
    assert fill["market_data_is_stale"] is False
    assert fill["market_data_age_ms_at_collection"] == pytest.approx(2000.0)
    assert fill["observed_market_price"] == pytest.approx(fill["raw_market_price"])


@pytest.mark.asyncio
async def test_autonomer_einstieg_traegt_kursbeleg(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "paper")
    analysis = AnalysisResult(
        document_id="doc_beleg",
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
    cycle = await _loop(tmp_path, _QuotedMock()).run_cycle(analysis, "BTC/USDT")

    assert cycle.status == CycleStatus.COMPLETED
    fills = _entry_fills(tmp_path)
    assert len(fills) == 1
    _assert_beleg(fills[0])


@pytest.mark.asyncio
async def test_promoted_einstieg_traegt_kursbeleg(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_ENTRY_MODE", "paper")
    promoted = tmp_path / "promoted.jsonl"
    promoted.write_text(
        '{"decision_id":"dec_beleg","timestamp_utc":"2026-10-02T07:00:00+00:00",'
        '"symbol":"ETHUSDT","market":"crypto","venue":"paper","mode":"paper",'
        '"direction":"long","entry_price":2000.0,"stop_loss_price":1950.0,'
        '"take_profit_price":2100.0,"confidence_score":0.8,"confluence_count":2,'
        '"thesis":"promoted","source_document_id":"req_beleg","execution_state":"pending"}\n',
        encoding="utf-8",
    )
    candidate = load_pending_promoted(promoted)[0]
    cycle = await _loop(tmp_path, _QuotedMock(price=2000.0)).run_promoted_signal(candidate)

    assert cycle.status == CycleStatus.COMPLETED, cycle.notes
    fills = _entry_fills(tmp_path)
    assert len(fills) == 1
    _assert_beleg(fills[0])
