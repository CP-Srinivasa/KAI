"""Der Loop stellt den L2-Kandidatenkontext WAEHREND der Signalgenerierung bereit.

Der eigentliche Beweis der Verdrahtung: Der L2-Provider laeuft als
``ExtraEvidencesProvider`` innerhalb von ``SignalGenerator.generate``. Nur wenn
der Kontext GENAU DANN steht, kann seine Messzeile Kandidaten-ID,
Entscheidungszeit und Referenzpreiszeit tragen — sonst bleibt der Join ueber
``symbol`` + Zeitfenster die einzige Moeglichkeit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.core.l2_candidate_context import current_candidate
from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.orchestrator.trading_loop import TradingLoop, build_trading_loop
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.signals.generator import SignalGenerator


class _SpyGenerator(SignalGenerator):
    """Haelt fest, was der Kontext im Moment der Signalgenerierung sagte."""

    def __init__(self) -> None:
        super().__init__(
            min_confidence=0.75, min_confluence=2, stop_loss_pct=2.5, take_profit_pct=5.0
        )
        self.seen: list[object] = []

    def generate(self, analysis, market_data, symbol):  # type: ignore[no-untyped-def]
        self.seen.append(current_candidate())
        return super().generate(analysis, market_data, symbol)


def _loop(tmp_path: Path, generator: SignalGenerator) -> TradingLoop:
    limits = RiskLimits(
        initial_equity=10000.0,
        max_risk_per_trade_pct=0.25,
        max_daily_loss_pct=1.0,
        max_total_drawdown_pct=5.0,
        max_open_positions=3,
        max_leverage=1.0,
        require_stop_loss=True,
        allow_averaging_down=False,
        allow_martingale=False,
        kill_switch_enabled=True,
        min_signal_confidence=0.75,
        min_signal_confluence_count=2,
    )
    engine = PaperExecutionEngine(
        initial_equity=10000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "exec_audit.jsonl"),
    )
    return TradingLoop(
        risk_engine=RiskEngine(limits),
        execution_engine=engine,
        market_data_adapter=MockMarketDataAdapter(),
        signal_generator=generator,
        audit_log_path=str(tmp_path / "loop_audit.jsonl"),
    )


def _bullish(document_id: str = "doc_l2") -> AnalysisResult:
    """Analyse, die alle Signalfilter passiert (wie in ``test_trading_loop``)."""
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
        tags=["etf", "bullish", "adoption"],
        spam_probability=0.02,
        explanation_short="BTC ETF approval expected — strong bullish catalyst.",
        explanation_long="Detailed reasoning about ETF impact.",
    )


@pytest.mark.asyncio
async def test_kontext_steht_waehrend_der_signalgenerierung(tmp_path: Path) -> None:
    generator = _SpyGenerator()
    loop = _loop(tmp_path, generator)

    cycle = await loop.run_cycle(_bullish(), "BTC/USDT")

    assert generator.seen, "generate() wurde nicht aufgerufen"
    ctx = generator.seen[0]
    assert ctx is not None, "kein Kandidatenkontext waehrend der Messung"
    # Die ID ist DIESELBE, die der Zyklus spaeter im Ledger traegt — sie stand
    # also schon vor der Messung fest.
    assert ctx.candidate_id == cycle.cycle_id
    assert ctx.decision_ts == cycle.started_at
    # Der Referenzpreis stammt aus dem Marktdatenpunkt desselben Zyklus.
    assert ctx.reference_price_ts


@pytest.mark.asyncio
async def test_kontext_ist_nach_dem_zyklus_wieder_leer(tmp_path: Path) -> None:
    loop = _loop(tmp_path, _SpyGenerator())

    await loop.run_cycle(_bullish(), "BTC/USDT")

    assert current_candidate() is None


@pytest.mark.asyncio
async def test_jeder_zyklus_bringt_seine_eigene_id_mit(tmp_path: Path) -> None:
    generator = _SpyGenerator()
    loop = _loop(tmp_path, generator)

    first = await loop.run_cycle(_bullish("doc_a"), "BTC/USDT")
    second = await loop.run_cycle(_bullish("doc_b"), "ETH/USDT")

    ids = [c.candidate_id for c in generator.seen if c is not None]
    assert ids == [first.cycle_id, second.cycle_id]
    assert len(set(ids)) == 2


@pytest.mark.asyncio
async def test_kontext_wird_auch_bei_fehlgeschlagener_generierung_freigegeben(
    tmp_path: Path,
) -> None:
    class _Boom(SignalGenerator):
        def __init__(self) -> None:
            super().__init__(
                min_confidence=0.75, min_confluence=2, stop_loss_pct=2.5, take_profit_pct=5.0
            )

        def generate(self, analysis, market_data, symbol):  # type: ignore[no-untyped-def]
            raise RuntimeError("Generator kaputt")

    loop = _loop(tmp_path, _Boom())

    cycle = await loop.run_cycle(_bullish(), "BTC/USDT")

    # Der Loop faengt den Fehler wie bisher ab …
    assert any("signal_error" in note for note in cycle.notes)
    # … und der Kontext bleibt nicht stehen.
    assert current_candidate() is None


# ── Der schaerfste Ort: im Evidence-Provider selbst ────────────────────────
#
# Der L2-Provider laeuft als ``bayes_extra_evidences_provider`` tief in
# ``_evaluate_bayes``. Ein Spy an GENAU dieser Stelle prueft beides auf einmal:
# ob der Kontext dort ankommt und ob die Richtung, mit der gemessen wird,
# dieselbe ist, die der Kandidat traegt. Ohne das zweite Stueck wuerde die
# Kandidaten-ID eine Identitaet behaupten, die die Richtung nicht deckt.


class _EvidenceSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def __call__(self, analysis, market_data, direction):  # type: ignore[no-untyped-def]
        self.calls.append((current_candidate(), direction))
        return ()


@pytest.mark.asyncio
async def test_der_evidence_provider_misst_mit_der_richtung_des_kandidaten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_MARKET_DATA_PROVIDER", "mock")
    monkeypatch.setenv("RISK_BAYES_CONFIDENCE_ENABLED", "true")
    monkeypatch.setenv("RISK_BAYES_CONFIDENCE_SHADOW_ONLY", "true")

    loop = build_trading_loop(
        loop_audit_path=tmp_path / "loop_audit.jsonl",
        execution_audit_path=tmp_path / "exec_audit.jsonl",
        rehydrate_from_audit=False,
    )
    generator = loop._signals  # noqa: SLF001
    spy = _EvidenceSpy()
    generator._bayes_extra_evidences_provider = spy  # noqa: SLF001

    signale: list[object] = []
    original = generator.generate

    def _mitschnitt(analysis, market_data, symbol):  # type: ignore[no-untyped-def]
        signal = original(analysis, market_data, symbol)
        signale.append(signal)
        return signal

    generator.generate = _mitschnitt  # type: ignore[method-assign]

    cycle = await loop.run_cycle(_bullish(), "BTC/USDT")

    # Ehrlich bleiben: ohne erreichten Provider beweist der Test nichts.
    assert spy.calls, "Evidence-Provider wurde nicht erreicht — Test ohne Aussage"
    # GENAU ein Aufruf: eine Messung, eine Richtung, ein Kandidat.
    assert len(spy.calls) == 1

    ctx, gemessene_richtung = spy.calls[0]
    assert ctx is not None, "kein Kandidatenkontext im Provider"
    assert ctx.candidate_id == cycle.cycle_id
    assert ctx.decision_ts == cycle.started_at

    signal = signale[0]
    assert signal is not None, "kein Kandidat erzeugt — Richtung nicht vergleichbar"
    assert gemessene_richtung == signal.direction
