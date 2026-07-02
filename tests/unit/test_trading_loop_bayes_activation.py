"""TradingLoop integriert die Bayes-Engine über RiskSettings.

Verifiziert das Aktivierungsverhalten von ``build_trading_loop`` auf der
SignalGenerator-Konfigurationsebene — kein voller Cycle-Run nötig, der
Vertrag ist: Flag-on → engine + audit gesetzt, Flag-off → beide None.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.trading_loop import build_trading_loop


def test_build_trading_loop_off_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_MARKET_DATA_PROVIDER", "mock")
    monkeypatch.setenv("RISK_BAYES_CONFIDENCE_ENABLED", "false")

    loop = build_trading_loop(
        loop_audit_path=tmp_path / "loop_audit.jsonl",
        execution_audit_path=tmp_path / "exec_audit.jsonl",
        rehydrate_from_audit=False,
    )
    gen = loop._signals  # noqa: SLF001
    assert gen._bayes_engine is None  # noqa: SLF001
    assert gen._bayes_audit_path is None  # noqa: SLF001


def test_build_trading_loop_on_via_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_MARKET_DATA_PROVIDER", "mock")
    monkeypatch.setenv("RISK_BAYES_CONFIDENCE_ENABLED", "true")
    monkeypatch.setenv("RISK_BAYES_CONFIDENCE_SHADOW_ONLY", "true")

    from app.signals.bayesian_confidence import BayesianConfidenceEngine

    loop = build_trading_loop(
        loop_audit_path=tmp_path / "loop_audit.jsonl",
        execution_audit_path=tmp_path / "exec_audit.jsonl",
        rehydrate_from_audit=False,
    )
    gen = loop._signals  # noqa: SLF001
    assert isinstance(gen._bayes_engine, BayesianConfidenceEngine)  # noqa: SLF001
    assert gen._bayes_audit_path is not None  # noqa: SLF001
    assert gen._bayes_shadow_only is True  # noqa: SLF001


def test_build_trading_loop_hard_gate_thresholds_passed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_MARKET_DATA_PROVIDER", "mock")
    monkeypatch.setenv("RISK_BAYES_CONFIDENCE_ENABLED", "true")
    monkeypatch.setenv("RISK_BAYES_CONFIDENCE_SHADOW_ONLY", "false")
    monkeypatch.setenv("RISK_MIN_BAYES_CONFIDENCE", "0.42")
    monkeypatch.setenv("RISK_MAX_BAYES_UNCERTAINTY", "0.66")

    # get_settings() is uncached — env vars take effect on next call.

    loop = build_trading_loop(
        loop_audit_path=tmp_path / "loop_audit.jsonl",
        execution_audit_path=tmp_path / "exec_audit.jsonl",
        rehydrate_from_audit=False,
    )
    gen = loop._signals  # noqa: SLF001
    assert gen._bayes_shadow_only is False  # noqa: SLF001
    assert gen._min_bayes_confidence == pytest.approx(0.42)  # noqa: SLF001
    assert gen._max_bayes_uncertainty == pytest.approx(0.66)  # noqa: SLF001
