"""Alert-Blindstelle nach erschoepftem Budget (Operator-Auftrag 2026-09-14, D-273).

Der Regelpfad erreicht hoechstens Prioritaet 6, die Alert-Schwelle liegt bei 7.
Ist das Budget erschoepft, entsteht ohne Reserve strukturell kein Alert mehr
(Kettentests B und C vom 14.09.). Die Alert-Reserve aus #954 schliesst das
fuer alert-faehige Dokumente -- aber nur, wenn sie aktiv ist UND die Anzeige
ihre Wirkung auch meldet. Bis hierher sagte ``/health/ai`` "unreachable",
sobald der Normaltopf erschoepft war, auch wenn die Reserve noch Luft hatte.

Zwei Teile:

* Health stellt dieselbe Topfentscheidung wie die Runtime vor einem echten
  Aufruf: wuerde ein alert-faehiges Dokument JETZT noch bezahlt?
* Die Pipeline beweist den Weg durch die echte Budgetsperre: Reserve bezahlt
  die Analyse, der Alert entsteht; nicht alert-faehig und erschoepfte Reserve
  enden im Regelpfad ohne Alert.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.spend import reset_spend_cache
from app.core.ai_cost_settings import reset_ai_cost_settings


@pytest.fixture(autouse=True)
def _reserve(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "1.25")
    monkeypatch.setenv("APP_AI_BUDGET_MONTHLY_USD", "25.00")
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_RESERVE_USD", "0.15")
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_RESERVE_MAX_CALLS", "20")
    monkeypatch.delenv("APP_AI_BUDGET_VALIDATION_RESERVE_USD", raising=False)
    monkeypatch.delenv("APP_AI_BUDGET_VALIDATION_RESERVE_MAX_CALLS", raising=False)
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_MIN_RULE_PRIORITY", "3")
    reset_ai_cost_settings()
    reset_spend_cache()
    yield
    reset_ai_cost_settings()
    reset_spend_cache()


def _gebucht(sink: Path, *, usd: float, pot: str = "normal", nr: int = 0) -> None:
    zeile = {
        "ts": datetime.now(UTC).isoformat(),
        "provider": "openai",
        "model": "gpt-4o",
        "actual_model": "gpt-4o",
        "ok": True,
        "chain_position": 0,
        "correlation_id": f"llm_gebucht_{pot}_{nr}",
        "purpose": "analysis",
        "use_case": "news_intelligence",
        "input_tokens": 1000,
        "output_tokens": 100,
        "cost_usd": usd,
        "cost_status": "OK",
        "budget_pot": pot,
    }
    with sink.open("a", encoding="utf-8") as f:
        f.write(json.dumps(zeile) + "\n")
    reset_spend_cache()


def _lage(sink: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.ai.health import _budget_block, cost_block

    kosten = cost_block(path=sink)
    return kosten, _budget_block(kosten, [])


# ── Health meldet die Wirkung der Reserve ──────────────────────────────────


def test_offene_reserve_heisst_nicht_unerreichbar(tmp_path: Path) -> None:
    """Der eigentliche Defekt: Normaltopf leer, Reserve voll -> bisher "unreachable"."""
    sink = tmp_path / "llm.jsonl"
    _gebucht(sink, usd=1.12)

    kosten, budget = _lage(sink)

    assert budget["routine_calls_blocked"] is True
    assert budget["alert_capability_for_new_documents"] == "reserve"
    assert budget["alert_capability_reason"] == "normal_budget_exhausted_alert_reserve_open"
    assert kosten["alert_eligible_call_allowed"] is True
    assert kosten["alert_eligible_call_pot"] == "alert_reserve"


def test_erschoepfte_reserve_nennt_sich_beim_namen(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _gebucht(sink, usd=1.12)
    _gebucht(sink, usd=0.15, pot="alert_reserve")

    kosten, budget = _lage(sink)

    assert budget["alert_capability_for_new_documents"] == "unreachable"
    assert budget["alert_capability_reason"] == "alert_reserve_exhausted"
    assert kosten["alert_eligible_call_allowed"] is False


def test_aufrufgrenze_der_reserve_zaehlt_auch_ohne_erreichten_betrag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_RESERVE_MAX_CALLS", "2")
    reset_ai_cost_settings()
    sink = tmp_path / "llm.jsonl"
    _gebucht(sink, usd=1.12)
    _gebucht(sink, usd=0.01, pot="alert_reserve", nr=1)
    _gebucht(sink, usd=0.01, pot="alert_reserve", nr=2)

    _kosten, budget = _lage(sink)

    assert budget["alert_capability_for_new_documents"] == "unreachable"
    assert budget["alert_capability_reason"] == "alert_reserve_exhausted"


def test_monatslimit_sperrt_auch_die_reserve_und_sagt_es(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_AI_BUDGET_MONTHLY_USD", "1.20")
    reset_ai_cost_settings()
    sink = tmp_path / "llm.jsonl"
    _gebucht(sink, usd=1.22)

    _kosten, budget = _lage(sink)

    assert budget["alert_capability_for_new_documents"] == "unreachable"
    assert budget["alert_capability_reason"] == "monthly_limit_reached"


def test_ohne_reserve_bleibt_die_meldung_wie_bisher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_AI_BUDGET_ALERT_RESERVE_USD", raising=False)
    monkeypatch.delenv("APP_AI_BUDGET_ALERT_RESERVE_MAX_CALLS", raising=False)
    reset_ai_cost_settings()
    sink = tmp_path / "llm.jsonl"
    _gebucht(sink, usd=1.30)

    kosten, budget = _lage(sink)

    assert budget["alert_capability_for_new_documents"] == "unreachable"
    assert budget["alert_capability_reason"] == "budget_blocked_rule_path_below_alert_gate"
    assert kosten["alert_eligible_call_allowed"] is False


def test_offenes_budget_bleibt_ok(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _gebucht(sink, usd=0.20)

    kosten, budget = _lage(sink)

    assert budget["alert_capability_for_new_documents"] == "ok"
    assert budget["alert_capability_reason"] == ""
    assert kosten["alert_eligible_call_allowed"] is True
    assert kosten["alert_eligible_call_pot"] == "normal"


def test_endpunkt_liefert_reserve_zustand_und_neue_felder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nicht deklariert heisst bei ``response_model``: still weggeworfen (#957)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routers.health import router as health_router
    from app.core.settings import get_settings

    sink = tmp_path / "llm_telemetry.jsonl"
    _gebucht(sink, usd=1.12)
    for ziel in (
        "app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH",
        "app.ai.health.DEFAULT_TELEMETRY_PATH",
        "app.ai.spend.DEFAULT_TELEMETRY_PATH",
    ):
        monkeypatch.setattr(ziel, sink)

    app = FastAPI()
    app.include_router(health_router)
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        providers=SimpleNamespace(
            openai_api_key="sk-x",
            gemini_api_key="",
            anthropic_api_key="",
            xai_api_key="",
            xai_fallback_enabled=False,
        )
    )
    body = TestClient(app).get("/health/ai").json()

    assert body["budget"]["alert_capability_for_new_documents"] == "reserve"
    assert body["cost"]["alert_eligible_call_allowed"] is True
    assert body["cost"]["alert_eligible_call_pot"] == "alert_reserve"
    assert "alert_eligible_call_reason" in body["cost"]


# ── Die Pipeline durch die echte Budgetsperre ──────────────────────────────


def _pipeline_mit_sperre(sink: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, MagicMock]:
    from app.ai.config import InferenceSettings
    from app.ai.runtime import reset_environment_settings
    from app.analysis.ai_control_plane import ControlPlaneAnalysisProvider
    from app.analysis.ensemble.provider import EnsembleProvider
    from app.analysis.pipeline import AnalysisPipeline
    from tests.unit.test_analysis_pipeline import _btc_engine, _make_llm_output

    for ziel in (
        "app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH",
        "app.ai.spend.DEFAULT_TELEMETRY_PATH",
    ):
        monkeypatch.setattr(ziel, sink)
    reset_environment_settings()

    direkt = MagicMock()
    direkt.provider_name = "openai"
    direkt.model = "gpt-4o"
    direkt.analyze = AsyncMock(return_value=_make_llm_output())
    verdrahtet = ControlPlaneAnalysisProvider(EnsembleProvider([direkt]), InferenceSettings())
    return AnalysisPipeline(keyword_engine=_btc_engine(), provider=verdrahtet), direkt


def _zeilen(sink: Path) -> list[dict[str, Any]]:
    return [json.loads(z) for z in sink.read_text("utf-8").splitlines() if z.strip()]


async def test_reserve_bezahlt_die_analyse_und_der_alert_entsteht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.alerts.threshold import ThresholdEngine
    from app.core.enums import AnalysisSource
    from tests.unit.test_analysis_pipeline import _make_doc

    sink = tmp_path / "llm_telemetry.jsonl"
    _gebucht(sink, usd=1.12)
    pipeline, direkt = _pipeline_mit_sperre(sink, monkeypatch)

    ergebnis = await pipeline.run(_make_doc())
    ergebnis.apply_to_document()

    direkt.analyze.assert_awaited_once()
    assert ergebnis.document.analysis_source == AnalysisSource.EXTERNAL_LLM
    assert (ergebnis.document.priority_score or 0) >= 7
    assert ThresholdEngine(min_priority=7).should_alert(
        ergebnis.analysis_result, spam_probability=ergebnis.document.spam_probability or 0.0
    )
    toepfe = {z.get("budget_pot") for z in _zeilen(sink) if z.get("chain_position", -1) >= 0}
    assert "alert_reserve" in toepfe


async def test_nicht_alert_faehiges_dokument_bekommt_die_reserve_nicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.alerts.threshold import ThresholdEngine
    from app.core.enums import AnalysisSource
    from tests.unit.test_analysis_pipeline import _make_doc

    monkeypatch.setenv("APP_AI_BUDGET_ALERT_MIN_RULE_PRIORITY", "10")
    reset_ai_cost_settings()
    sink = tmp_path / "llm_telemetry.jsonl"
    _gebucht(sink, usd=1.12)
    pipeline, direkt = _pipeline_mit_sperre(sink, monkeypatch)

    ergebnis = await pipeline.run(_make_doc())
    ergebnis.apply_to_document()

    direkt.analyze.assert_not_awaited()
    assert ergebnis.document.analysis_source == AnalysisSource.RULE
    assert not ThresholdEngine(min_priority=7).should_alert(
        ergebnis.analysis_result, spam_probability=ergebnis.document.spam_probability or 0.0
    )


async def test_erschoepfte_reserve_endet_im_regelpfad_ohne_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.alerts.threshold import ThresholdEngine
    from app.core.enums import AnalysisSource
    from tests.unit.test_analysis_pipeline import _make_doc

    sink = tmp_path / "llm_telemetry.jsonl"
    _gebucht(sink, usd=1.12)
    _gebucht(sink, usd=0.15, pot="alert_reserve")
    pipeline, direkt = _pipeline_mit_sperre(sink, monkeypatch)

    ergebnis = await pipeline.run(_make_doc())
    ergebnis.apply_to_document()

    direkt.analyze.assert_not_awaited()
    assert ergebnis.document.analysis_source == AnalysisSource.RULE
    assert not ThresholdEngine(min_priority=7).should_alert(
        ergebnis.analysis_result, spam_probability=ergebnis.document.spam_probability or 0.0
    )


# ── Anzeige und Sperre sagen dasselbe ──────────────────────────────────────


async def _runtime_bezahlt(sink: Path, *, alert_eligible: bool) -> bool:
    from app.ai.audit import budget_intent_scope
    from app.ai.budget import BudgetExceeded
    from app.ai.runtime import invoke, reset_environment_settings

    reset_environment_settings()

    async def direkt() -> str:
        return "bezahlt"

    try:
        with budget_intent_scope(alert_eligible=alert_eligible):
            await invoke(
                purpose="analysis",
                direct_call=direkt,
                direct_provider="openai",
                direct_model="gpt-4o",
                litellm=None,
                telemetry_path=sink,
            )
    except BudgetExceeded:
        return False
    return True


@pytest.mark.parametrize(
    ("normal_usd", "reserve_usd"),
    [(0.20, 0.0), (1.12, 0.0), (1.30, 0.0), (1.12, 0.15)],
)
async def test_anzeige_meldet_reserve_genau_wenn_die_runtime_bezahlt(
    tmp_path: Path, normal_usd: float, reserve_usd: float
) -> None:
    """Die Aussage aus /health/ai und die Entscheidung am Aufruf duerfen nie auseinanderlaufen."""
    sink = tmp_path / "llm.jsonl"
    _gebucht(sink, usd=normal_usd)
    if reserve_usd:
        _gebucht(sink, usd=reserve_usd, pot="alert_reserve")

    kosten, budget = _lage(sink)
    bezahlt = await _runtime_bezahlt(sink, alert_eligible=True)

    assert kosten["alert_eligible_call_allowed"] is bezahlt
    zustand = budget["alert_capability_for_new_documents"]
    if not budget["routine_calls_blocked"]:
        assert zustand == "ok"
    else:
        assert (zustand == "reserve") is bezahlt
