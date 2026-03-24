"""Unit tests for app/research/route_runner.py (Sprint 17)."""

from __future__ import annotations

import pytest

from app.analysis.base.interfaces import LLMAnalysisOutput
from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.research.abc_result import PathResultEnvelope
from app.research.active_route import ActiveRouteState
from app.research.route_runner import (
    build_abc_envelope,
    build_comparison_summaries,
    build_path_result_from_analysis_result,
    build_path_result_from_llm_output,
    map_path_to_provider_name,
)

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_llm_output(
    sentiment: SentimentLabel = SentimentLabel.NEUTRAL,
    relevance: float = 0.7,
    impact: float = 0.5,
    priority: int = 5,
    actionable: bool = False,
    reasoning: str | None = "short reason",
) -> LLMAnalysisOutput:
    return LLMAnalysisOutput(
        sentiment_label=sentiment,
        sentiment_score=0.0,
        relevance_score=relevance,
        impact_score=impact,
        confidence_score=0.8,
        novelty_score=0.5,
        spam_probability=0.1,
        recommended_priority=priority,
        actionable=actionable,
        short_reasoning=reasoning,
    )


def _make_analysis_result(
    doc_id: str = "doc-1",
    sentiment: SentimentLabel = SentimentLabel.BULLISH,
    relevance: float = 0.8,
    impact: float = 0.6,
    priority: int = 7,
    actionable: bool = True,
) -> AnalysisResult:
    return AnalysisResult(
        document_id=doc_id,
        sentiment_label=sentiment,
        sentiment_score=0.3,
        relevance_score=relevance,
        impact_score=impact,
        confidence_score=0.9,
        novelty_score=0.6,
        explanation_short="short explanation",
        explanation_long="long explanation",
        recommended_priority=priority,
        actionable=actionable,
    )


def _make_active_route(
    route_profile: str = "primary_with_shadow",
    active_primary_path: str = "A.external_llm",
    shadow_paths: list[str] | None = None,
    control_path: str | None = None,
    abc_output: str = "artifacts/abc_envelopes/envelopes.jsonl",
) -> ActiveRouteState:
    return ActiveRouteState(
        profile_path="/tmp/profile.json",
        profile_name="test-profile",
        route_profile=route_profile,
        active_primary_path=active_primary_path,
        enabled_shadow_paths=shadow_paths or ["B.companion"],
        control_path=control_path,
        abc_envelope_output=abc_output,
    )


# ── map_path_to_provider_name ──────────────────────────────────────────────────


def test_map_path_companion():
    assert map_path_to_provider_name("B.companion") == "companion"


def test_map_path_rule():
    assert map_path_to_provider_name("C.rule") == "rule"


def test_map_path_external_llm():
    assert map_path_to_provider_name("A.external_llm") == "external_llm"


def test_map_path_openai():
    assert map_path_to_provider_name("A.openai") == "openai"


def test_map_path_no_dot():
    assert map_path_to_provider_name("companion") == "companion"


def test_map_path_multiple_dots():
    # Only first dot matters — everything after first dot is the provider name
    assert map_path_to_provider_name("B.my.provider") == "my.provider"


# ── build_path_result_from_llm_output ─────────────────────────────────────────


def test_build_path_result_from_llm_output_shadow():
    output = _make_llm_output(sentiment=SentimentLabel.BULLISH, priority=8, actionable=True)
    result = build_path_result_from_llm_output("B.companion", "companion", output)

    assert result.path_id == "B.companion"
    assert result.provider == "companion"
    assert result.analysis_source == "internal"
    assert result.scores["recommended_priority"] == 8
    assert result.scores["sentiment_label"] == str(SentimentLabel.BULLISH)
    assert result.scores["actionable"] is True
    assert result.summary == "short reason"


def test_build_path_result_from_llm_output_control():
    output = _make_llm_output(sentiment=SentimentLabel.NEUTRAL, priority=3)
    result = build_path_result_from_llm_output("C.rule", "rule", output)

    assert result.analysis_source == "rule"
    assert result.scores["recommended_priority"] == 3


def test_build_path_result_from_llm_output_primary():
    output = _make_llm_output()
    result = build_path_result_from_llm_output("A.external_llm", "openai", output)

    assert result.analysis_source == "external_llm"


def test_build_path_result_from_llm_output_error():
    result = build_path_result_from_llm_output("B.companion", "companion", None, error="timeout")

    assert result.scores == {}
    assert result.summary == "error: timeout"


def test_build_path_result_from_llm_output_none_no_error():
    result = build_path_result_from_llm_output("B.companion", "companion", None)

    assert result.scores == {}
    assert result.summary is None


# ── build_path_result_from_analysis_result ────────────────────────────────────


def test_build_path_result_from_analysis_result_basic():
    ar = _make_analysis_result(priority=7, actionable=True)
    result = build_path_result_from_analysis_result("A.external_llm", "openai", ar)

    assert result.path_id == "A.external_llm"
    assert result.provider == "openai"
    assert result.analysis_source == "external_llm"
    assert result.scores["recommended_priority"] == 7
    assert result.scores["actionable"] is True
    assert result.summary == "short explanation"


def test_build_path_result_from_analysis_result_primary_internal_path():
    ar = _make_analysis_result(priority=6, actionable=False)
    result = build_path_result_from_analysis_result("A.internal", "companion", ar)

    assert result.path_id == "A.internal"
    assert result.provider == "companion"
    assert result.analysis_source == "internal"


def test_build_path_result_from_analysis_result_primary_rule_path():
    ar = _make_analysis_result(priority=4, actionable=False)
    result = build_path_result_from_analysis_result("A.rule", "rule", ar)

    assert result.path_id == "A.rule"
    assert result.provider == "rule"
    assert result.analysis_source == "rule"


def test_build_path_result_from_analysis_result_none():
    result = build_path_result_from_analysis_result("A.external_llm", "openai", None)

    assert result.scores == {}
    assert result.summary is None


# ── build_comparison_summaries ────────────────────────────────────────────────


def _make_envelope(
    path_id: str,
    sentiment: str = "neutral",
    priority: float = 5,
    actionable: bool = False,
) -> PathResultEnvelope:
    return PathResultEnvelope(
        path_id=path_id,
        provider="test",
        analysis_source="test",
        scores={
            "sentiment_label": sentiment,
            "recommended_priority": priority,
            "relevance_score": 0.5,
            "impact_score": 0.4,
            "actionable": actionable,
        },
    )


def test_comparison_summaries_a_vs_b_match():
    primary = _make_envelope("A.external_llm", sentiment="bullish", priority=7, actionable=True)
    shadow = _make_envelope("B.companion", sentiment="bullish", priority=6, actionable=True)

    summaries = build_comparison_summaries(primary, [shadow], None)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.compared_path == "A_vs_B"
    assert s.sentiment_match is True
    assert s.actionable_match is True
    assert s.deviations["recommended_priority_delta"] == pytest.approx(1.0)


def test_comparison_summaries_a_vs_c():
    primary = _make_envelope("A.external_llm", sentiment="bullish", priority=7)
    control = _make_envelope("C.rule", sentiment="neutral", priority=3)

    summaries = build_comparison_summaries(primary, [], control)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.compared_path == "A_vs_C"
    assert s.sentiment_match is False
    assert s.deviations["recommended_priority_delta"] == pytest.approx(4.0)


def test_comparison_summaries_empty_shadows_no_control():
    primary = _make_envelope("A.external_llm")
    summaries = build_comparison_summaries(primary, [], None)
    assert summaries == []


def test_comparison_summaries_both_b_and_c():
    primary = _make_envelope("A.external_llm", sentiment="bullish", priority=8)
    shadow = _make_envelope("B.companion", sentiment="bullish", priority=7)
    control = _make_envelope("C.rule", sentiment="neutral", priority=4)

    summaries = build_comparison_summaries(primary, [shadow], control)
    labels = [s.compared_path for s in summaries]
    assert "A_vs_B" in labels
    assert "A_vs_C" in labels


# ── build_abc_envelope ────────────────────────────────────────────────────────


def test_build_abc_envelope_primary_only_no_shadows():
    route = _make_active_route(
        route_profile="primary_only",
        shadow_paths=[],
        control_path=None,
    )
    ar = _make_analysis_result()
    envelope = build_abc_envelope(
        document_id="doc-1",
        route_state=route,
        primary_provider_name="openai",
        primary_analysis_result=ar,
        shadow_outcomes=[],
        control_outcome=None,
    )

    assert envelope.document_id == "doc-1"
    assert envelope.route_profile == "primary_only"
    assert envelope.shadow_results == []
    assert envelope.control_result is None
    assert envelope.primary_result.provider == "openai"
    assert envelope.primary_result.path_id == "A.external_llm"


def test_build_abc_envelope_with_shadow():
    route = _make_active_route(route_profile="primary_with_shadow")
    ar = _make_analysis_result()
    sh_out = _make_llm_output(sentiment=SentimentLabel.BEARISH, priority=3)

    envelope = build_abc_envelope(
        document_id="doc-2",
        route_state=route,
        primary_provider_name="openai",
        primary_analysis_result=ar,
        shadow_outcomes=[("B.companion", "companion", sh_out, None)],
        control_outcome=None,
    )

    assert len(envelope.shadow_results) == 1
    assert envelope.shadow_results[0].path_id == "B.companion"
    assert envelope.shadow_results[0].scores["recommended_priority"] == 3
    assert envelope.control_result is None


def test_build_abc_envelope_with_control():
    route = _make_active_route(
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    ar = _make_analysis_result()
    sh_out = _make_llm_output(priority=6)
    ctrl_out = _make_llm_output(priority=3, sentiment=SentimentLabel.NEUTRAL)

    envelope = build_abc_envelope(
        document_id="doc-3",
        route_state=route,
        primary_provider_name="openai",
        primary_analysis_result=ar,
        shadow_outcomes=[("B.companion", "companion", sh_out, None)],
        control_outcome=("C.rule", "rule", ctrl_out, None),
    )

    assert envelope.control_result is not None
    assert envelope.control_result.path_id == "C.rule"
    assert envelope.control_result.analysis_source == "rule"
    assert len(envelope.comparison_summary) == 2


def test_build_abc_envelope_shadow_error():
    route = _make_active_route(route_profile="primary_with_shadow")
    ar = _make_analysis_result()

    envelope = build_abc_envelope(
        document_id="doc-4",
        route_state=route,
        primary_provider_name="openai",
        primary_analysis_result=ar,
        shadow_outcomes=[("B.companion", "companion", None, "connection refused")],
        control_outcome=None,
    )

    assert len(envelope.shadow_results) == 1
    sr = envelope.shadow_results[0]
    assert sr.summary == "error: connection refused"
    assert sr.scores == {}


def test_build_abc_envelope_distribution_metadata():
    route = _make_active_route(route_profile="primary_with_shadow")
    ar = _make_analysis_result()

    envelope = build_abc_envelope(
        document_id="doc-5",
        route_state=route,
        primary_provider_name="openai",
        primary_analysis_result=ar,
        shadow_outcomes=[],
        control_outcome=None,
    )

    assert envelope.distribution_metadata is not None
    assert envelope.distribution_metadata.route_profile == "primary_with_shadow"
    assert envelope.distribution_metadata.activation_state == "active"
    assert envelope.distribution_metadata.decision_owner == "operator"


def test_build_abc_envelope_serializable():
    """to_json_dict() must not raise (I-93: written to JSONL)."""
    route = _make_active_route(route_profile="primary_with_shadow")
    ar = _make_analysis_result()
    sh_out = _make_llm_output()

    envelope = build_abc_envelope(
        document_id="doc-6",
        route_state=route,
        primary_provider_name="openai",
        primary_analysis_result=ar,
        shadow_outcomes=[("B.companion", "companion", sh_out, None)],
        control_outcome=None,
    )

    d = envelope.to_json_dict()
    assert d["report_type"] == "abc_inference_envelope"
    assert d["document_id"] == "doc-6"


# ── run_route_provider (async) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_route_provider_success():
    from unittest.mock import AsyncMock

    from app.research.route_runner import run_route_provider

    provider = AsyncMock()
    provider.analyze = AsyncMock(return_value=_make_llm_output())
    doc = AsyncMock()
    doc.title = "Test"
    doc.content = "Content"

    output, error = await run_route_provider(provider, doc)

    assert output is not None
    assert error is None


@pytest.mark.asyncio
async def test_run_route_provider_exception_captured():
    from unittest.mock import AsyncMock

    from app.research.route_runner import run_route_provider

    provider = AsyncMock()
    provider.analyze = AsyncMock(side_effect=RuntimeError("network error"))
    doc = AsyncMock()
    doc.title = "Test"
    doc.content = "Content"

    output, error = await run_route_provider(provider, doc)

    assert output is None
    assert "network error" in error
