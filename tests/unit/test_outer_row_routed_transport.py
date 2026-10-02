"""Die aeussere Kettenzeile nennt den Transport, der WIRKLICH lief (02.10.2026).

Seit ``standard`` primaer ueber LiteLLM/DeepSeek laeuft, schrieb die aeussere Zeile
(``chain_position=-1``) jeden DeepSeek-Aufruf als ``transport=direct``,
``model=gpt-4o`` und bepreiste ihn zum gpt-4o-Listenpreis -- 52 Zeilen ergaben
1,01 USD statt 0,108 USD. Budget und Routenbericht verwerfen die Huelle, jeder
andere Leser der Zeile sah aber Modell und Kosten falsch.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.ai.config import InferenceSettings
from app.ai.models import AttemptTrace
from app.analysis.ai_control_plane import ControlPlaneAnalysisProvider
from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.core.enums import MarketScope, SentimentLabel
from app.integrations.litellm.provider import LiteLLMResponse

DS_MODEL = "deepseek/deepseek-v4-flash"


def _output() -> LLMAnalysisOutput:
    return LLMAnalysisOutput(
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.6,
        relevance_score=0.9,
        impact_score=0.7,
        confidence_score=0.8,
        novelty_score=0.5,
        spam_probability=0.0,
        market_scope=MarketScope.CRYPTO,
    )


class _Direct(BaseAnalysisProvider):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return "gpt-4o"

    async def analyze(
        self, title: str, text: str, context: dict[str, object] | None = None
    ) -> LLMAnalysisOutput:
        self.calls += 1
        return _output()


def _settings(mode: str) -> InferenceSettings:
    return InferenceSettings(
        enabled=True, mode_ceiling="primary", route_modes={"standard": mode}, max_attempts=1
    )


def _litellm_ok(monkeypatch: pytest.MonkeyPatch, *, cost: float | None = 0.0012) -> None:
    trace = AttemptTrace(
        transport="litellm",
        requested_model="kai-standard",
        latency_ms=1.0,
        actual_provider="deepseek",
        actual_model=DS_MODEL,
        input_tokens=2117,
        output_tokens=720,
        cost_usd=cost,
    )
    body = {
        "model": DS_MODEL,
        "choices": [{"message": {"content": _output().model_dump_json()}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2117, "completion_tokens": 720},
    }
    monkeypatch.setattr(
        "app.ai.runtime.call_litellm_async",
        AsyncMock(return_value=LiteLLMResponse(trace=trace, body=body)),
    )


async def test_die_steuerebene_markiert_den_getragenen_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _litellm_ok(monkeypatch)
    ergebnis = await ControlPlaneAnalysisProvider(_Direct(), _settings("primary")).analyze(
        "Bitcoin", "Bitcoin " * 20
    )
    assert ergebnis.routed_transport == "litellm"
    assert ergebnis.routed_model == DS_MODEL
    assert ergebnis.routed_cost_usd == pytest.approx(0.0012)


async def test_der_direkte_pfad_bleibt_unmarkiert() -> None:
    direkt = _Direct()
    ergebnis = await ControlPlaneAnalysisProvider(direkt, _settings("off")).analyze(
        "Bitcoin", "Bitcoin " * 20
    )
    assert direkt.calls == 1
    assert ergebnis.routed_transport is None
    assert ergebnis.routed_model is None
    assert ergebnis.routed_cost_usd is None


def test_die_routing_felder_gehoeren_nicht_zum_antwortschema() -> None:
    # Das Schema geht als ``response_format`` an gpt-4o; Modell-JSON darf die
    # Felder auch nicht befuellen.
    eigenschaften = LLMAnalysisOutput.model_json_schema()["properties"]
    assert not [name for name in eigenschaften if "routed" in name]
    roh = json.loads(_output().model_dump_json())
    roh["routed_model"] = "gefaelscht"
    assert LLMAnalysisOutput.model_validate_json(json.dumps(roh)).routed_model is None


async def test_die_aeussere_zeile_traegt_transport_modell_und_upstream_preis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.analysis.pipeline import AnalysisPipeline
    from tests.unit.test_analysis_pipeline import _btc_engine, _make_doc

    sink = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    _litellm_ok(monkeypatch)
    provider = ControlPlaneAnalysisProvider(_Direct(), _settings("primary"))
    await AnalysisPipeline(keyword_engine=_btc_engine(), provider=provider).run(_make_doc())

    zeilen = [json.loads(z) for z in sink.read_text("utf-8").splitlines() if z.strip()]
    aussen = [z for z in zeilen if z.get("chain_position") == -1 and z.get("ok")]
    assert len(aussen) == 1
    assert aussen[0]["transport"] == "litellm"
    assert aussen[0]["model"] == DS_MODEL
    assert aussen[0]["provider"] == "deepseek"
    assert aussen[0]["cost_usd"] == pytest.approx(0.0012)
    assert aussen[0]["cost_source"] == "upstream"


async def test_ohne_upstream_preis_wird_nicht_zum_direktmodell_geschaetzt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.analysis.pipeline import AnalysisPipeline
    from tests.unit.test_analysis_pipeline import _btc_engine, _make_doc

    sink = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    _litellm_ok(monkeypatch, cost=None)
    provider = ControlPlaneAnalysisProvider(_Direct(), _settings("primary"))
    await AnalysisPipeline(keyword_engine=_btc_engine(), provider=provider).run(_make_doc())

    zeilen = [json.loads(z) for z in sink.read_text("utf-8").splitlines() if z.strip()]
    aussen = [z for z in zeilen if z.get("chain_position") == -1 and z.get("ok")]
    from app.ai.pricing import estimate_cost_usd

    assert aussen[0]["model"] == DS_MODEL
    assert aussen[0]["transport"] == "litellm"
    gpt4o = estimate_cost_usd("gpt-4o", 2117, 720).usd
    assert aussen[0]["cost_usd"] != pytest.approx(gpt4o)
