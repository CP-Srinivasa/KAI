"""Acceptance-Tests fuer Adaptive-Learning Schritt 4.

Verifiziert dass SignalGenerator das neue ``audit_adapter``-kwarg akzeptiert,
explizit-uebergebene Adapter gegenueber den Legacy-kwargs (reasoning_journal,
bayes_audit_path) gewinnen, und dass die internen Audit-Pfade tatsaechlich
ueber den Adapter laufen statt direkt gegen ``ReasoningJournal`` /
``append_bayes_report``.

Schritt 3 (bayes_activation.py) reicht ``audit_adapter`` als kwarg an
``SignalGenerator(**kwargs)`` — Schritt 4 muss das hier sauber empfangen.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.audit.structured_reasoning import ReasoningJournal
from app.signals.audit_adapter import SignalAuditAdapter
from app.signals.bayesian_confidence import BayesianConfidenceEngine
from app.signals.generator import SignalGenerator


def test_audit_adapter_kwarg_accepted():
    """Schritt 4 Kern-Acceptance: Konstruktor nimmt audit_adapter ohne TypeError."""
    adapter = SignalAuditAdapter(reasoning_journal=None, bayes_audit_path=None)
    gen = SignalGenerator(audit_adapter=adapter)
    assert gen._audit_adapter is adapter  # noqa: SLF001


def test_no_audit_kwargs_construct_silent_adapter():
    """Default-Pfad: ohne kwargs ist der intern gebaute Adapter no-op."""
    gen = SignalGenerator()
    assert gen._audit_adapter is not None  # noqa: SLF001
    assert gen._audit_adapter.is_journaling is False  # noqa: SLF001


def test_legacy_kwargs_construct_internal_adapter(tmp_path: Path):
    """Backward-Compat: alte kwargs erzeugen einen aequivalenten Adapter."""
    journal_path = tmp_path / "rj.jsonl"
    bayes_path = tmp_path / "bayes.jsonl"
    journal = ReasoningJournal(path=journal_path)

    gen = SignalGenerator(
        reasoning_journal=journal,
        bayes_audit_path=bayes_path,
    )

    assert gen._audit_adapter is not None  # noqa: SLF001
    assert gen._audit_adapter.is_journaling is True  # noqa: SLF001


def test_audit_adapter_wins_over_legacy_kwargs(tmp_path: Path):
    """Wenn beide gegeben sind, hat audit_adapter Vorrang."""
    explicit_adapter = SignalAuditAdapter(
        reasoning_journal=None,
        bayes_audit_path=tmp_path / "explicit_bayes.jsonl",
    )
    legacy_journal = ReasoningJournal(path=tmp_path / "legacy_rj.jsonl")

    gen = SignalGenerator(
        audit_adapter=explicit_adapter,
        reasoning_journal=legacy_journal,
        bayes_audit_path=tmp_path / "legacy_bayes.jsonl",
    )

    assert gen._audit_adapter is explicit_adapter  # noqa: SLF001
    # Legacy-Attribute werden zwar gespeichert (fuer test-Inspektoren),
    # aber der Adapter bestimmt das tatsaechliche Audit-Verhalten.
    assert gen._reasoning_journal is legacy_journal  # noqa: SLF001


def _build_market_payload():
    """Minimal-Setup fuer einen Bayes-Pfad-Cycle.

    Reuses the same field-shape as test_generator_active_calibrator.
    """
    from app.core.domain.document import AnalysisResult
    from app.core.enums import SentimentLabel
    from app.market_data.models import MarketDataPoint

    analysis = AnalysisResult(
        document_id="doc_test_step4",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=0.85,
        impact_score=0.85,
        confidence_score=0.85,
        novelty_score=0.7,
        actionable=True,
        affected_assets=["BTC", "BTC/USDT"],
        tags=["etf", "bullish"],
        spam_probability=0.05,
        explanation_short="BTC ETF approved.",
        explanation_long="Detailed.",
    )
    market = MarketDataPoint(
        symbol="BTC/USDT",
        timestamp_utc="2026-05-16T08:00:00+00:00",
        price=80000.0,
        volume_24h=2_000_000.0,
        change_pct_24h=3.5,
        source="mock",
    )
    return analysis, market


def test_record_raw_bayes_report_called_via_adapter(tmp_path: Path):
    """Bayes-Pfad: Raw-Report-Write laeuft ueber adapter.record_raw_bayes_report."""
    bayes_path = tmp_path / "bayes_audit.jsonl"
    spy_adapter = SignalAuditAdapter(
        reasoning_journal=None,
        bayes_audit_path=bayes_path,
    )
    # Spy auf die Adapter-Method, ohne dass das tatsaechliche Schreiben kaputtgeht.
    real_record = spy_adapter.record_raw_bayes_report
    calls: list[dict] = []

    def _spy(**kwargs):
        calls.append(kwargs)
        real_record(**kwargs)

    spy_adapter.record_raw_bayes_report = _spy  # type: ignore[method-assign]

    gen = SignalGenerator(
        bayes_engine=BayesianConfidenceEngine(),
        audit_adapter=spy_adapter,
    )

    analysis, market = _build_market_payload()
    gen.generate(analysis, market, symbol="BTC/USDT")

    # Adapter-Method wurde aufgerufen (bei min. einem Bayes-Pfad-Hit).
    assert len(calls) >= 1
    assert calls[0]["symbol"] == "BTC/USDT"
    assert calls[0]["direction"] == "long"
    assert "decision_id" in calls[0]

    # Und das File wurde tatsaechlich beschrieben (E2E-Check).
    assert bayes_path.exists()
    lines = bayes_path.read_text().strip().splitlines()
    assert len(lines) >= 1
    payload = json.loads(lines[0])
    assert payload["symbol"] == "BTC/USDT"


def test_legacy_path_still_writes_bayes_audit(tmp_path: Path):
    """Backward-Compat E2E: bayes_audit_path-kwarg schreibt via internem Adapter."""
    bayes_path = tmp_path / "bayes_legacy.jsonl"

    gen = SignalGenerator(
        bayes_engine=BayesianConfidenceEngine(),
        bayes_audit_path=bayes_path,  # alter Pfad — kein audit_adapter
    )

    analysis, market = _build_market_payload()
    gen.generate(analysis, market, symbol="BTC/USDT")

    # File muss vorhanden sein, auch ohne explicit audit_adapter.
    assert bayes_path.exists()
    payload = json.loads(bayes_path.read_text().strip().splitlines()[0])
    assert payload["symbol"] == "BTC/USDT"


def test_adapter_replaces_direct_journal_writes(tmp_path: Path):
    """Wenn audit_adapter gegeben + Legacy reasoning_journal als Spy:
    Calibrator-Apply und Gate-Reject duerfen NICHT direkt am Legacy-Journal landen.
    Adapter wins.
    """
    legacy_journal_spy = MagicMock(spec=ReasoningJournal)
    explicit_adapter = SignalAuditAdapter(
        reasoning_journal=None,  # adapter schreibt nichts in journal-richtung
        bayes_audit_path=tmp_path / "bayes.jsonl",
    )

    gen = SignalGenerator(
        bayes_engine=BayesianConfidenceEngine(),
        audit_adapter=explicit_adapter,
        reasoning_journal=legacy_journal_spy,  # darf nicht angesprochen werden
    )

    analysis, market = _build_market_payload()
    gen.generate(analysis, market, symbol="BTC/USDT")

    # Der Generator routet ueber den explicit Adapter, nicht ueber das Legacy-Journal.
    legacy_journal_spy.log_step.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
