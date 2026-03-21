"""Tests for app/research/distribution.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import AnalysisSource, SentimentLabel, SourceType
from app.research.abc_result import (
    ABCInferenceEnvelope,
    DistributionMetadata,
    PathComparisonSummary,
    PathResultEnvelope,
)
from app.research.distribution import (
    DistributionClassificationReport,
    ExecutionHandoffReport,
    RouteProfileReport,
    TierProfile,
    build_distribution_classification_report,
    build_execution_handoff_report,
    build_handoff_collector_summary,
    build_route_profile,
    save_distribution_classification_report,
    save_execution_handoff_report,
    save_handoff_collector_summary,
    save_route_profile,
)
from app.research.execution_handoff import (
    create_handoff_acknowledgement,
    create_signal_handoff,
)
from app.research.signals import extract_signal_candidates
from tests.unit.factories import make_document


def _make_doc(
    analysis_source: AnalysisSource | None = AnalysisSource.EXTERNAL_LLM,
    priority_score: int | None = 5,
    spam_probability: float | None = 0.1,
    actionable: bool = False,
    shadow_analysis: bool = False,
) -> MagicMock:
    doc = MagicMock()
    doc.analysis_source = analysis_source
    doc.priority_score = priority_score
    doc.spam_probability = spam_probability
    meta: dict[str, object] = {}
    if actionable:
        meta["actionable"] = True
    if shadow_analysis:
        meta["shadow_analysis"] = True
    doc.metadata = meta
    return doc


def _make_repo(docs: list[object]) -> MagicMock:
    repo = MagicMock()
    repo.get_recent_analyzed = AsyncMock(return_value=docs)
    return repo


def _make_primary_handoff():
    document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        tickers=["BTC"],
        crypto_assets=[],
        provider="openai",
        analysis_source=AnalysisSource.EXTERNAL_LLM,
        source_name="CoinDesk",
        source_type=SourceType.RSS_FEED,
        summary="ETF demand remains elevated.",
    )
    signal = extract_signal_candidates([document], min_priority=8)[0]
    return create_signal_handoff(signal, document=document)


def test_tier_profile_to_json_dict_structure() -> None:
    profile = TierProfile(
        document_count=10,
        signal_count=3,
        spam_count=1,
        avg_priority=6.5,
        actionable_count=4,
    )
    payload = profile.to_json_dict()
    assert payload["document_count"] == 10
    assert payload["signal_count"] == 3
    assert payload["spam_count"] == 1
    assert payload["avg_priority"] == 6.5
    assert payload["actionable_count"] == 4


def test_tier_profile_avg_priority_rounded() -> None:
    profile = TierProfile(avg_priority=6.123456789)
    payload = profile.to_json_dict()
    assert len(str(payload["avg_priority"]).split(".")[-1]) <= 4


def test_route_profile_report_to_json_dict_structure() -> None:
    report = RouteProfileReport(
        total_analyzed=5,
        primary_tier_metrics={"external_llm": TierProfile(document_count=5)},
        shadow_in_metadata=1,
    )
    payload = report.to_json_dict()
    assert payload["report_type"] == "route_profile"
    assert payload["total_analyzed"] == 5
    assert "primary_distribution" in payload
    assert payload["shadow_executions_tracked"] == 1
    assert "generated_at" in payload


def test_execution_handoff_report_to_json_dict_structure() -> None:
    document = make_document(
        raw_text="ETF demand remains elevated.",
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        crypto_assets=["BTC"],
        analysis_source=AnalysisSource.EXTERNAL_LLM,
        provider="openai",
        source_name="CoinDesk",
        source_type=SourceType.RSS_FEED,
        url="https://example.com/doc-1",
    )
    signal = extract_signal_candidates([document], min_priority=8)[0]
    report = ExecutionHandoffReport(
        signal_count=1,
        signals=[create_signal_handoff(signal, document=document)],
    )

    payload = report.to_json_dict()
    assert payload["report_type"] == "execution_signal_handoff"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert payload["signal_count"] == 1
    signal_payload = payload["signals"][0]
    assert signal_payload["provider"] == "openai"
    assert signal_payload["route_path"] == "A.external_llm"
    assert signal_payload["path_type"] == "primary"
    assert signal_payload["delivery_class"] == "productive_handoff"
    assert signal_payload["consumer_visibility"] == "visible"
    assert signal_payload["source_name"] == "CoinDesk"
    assert "recommended_next_step" not in signal_payload


@pytest.mark.asyncio
async def test_build_route_profile_empty_documents() -> None:
    repo = _make_repo([])
    report = await build_route_profile(repo)
    assert report.total_analyzed == 0
    assert report.primary_tier_metrics == {}
    assert report.shadow_in_metadata == 0


@pytest.mark.asyncio
async def test_build_route_profile_counts_documents_by_source() -> None:
    docs = [
        _make_doc(analysis_source=AnalysisSource.EXTERNAL_LLM),
        _make_doc(analysis_source=AnalysisSource.EXTERNAL_LLM),
        _make_doc(analysis_source=AnalysisSource.INTERNAL),
    ]
    report = await build_route_profile(_make_repo(docs))
    assert report.total_analyzed == 3
    assert report.primary_tier_metrics["external_llm"].document_count == 2
    assert report.primary_tier_metrics["internal"].document_count == 1


@pytest.mark.asyncio
async def test_build_route_profile_signals_high_priority() -> None:
    docs = [_make_doc(priority_score=9), _make_doc(priority_score=8), _make_doc(priority_score=5)]
    report = await build_route_profile(_make_repo(docs))
    assert report.primary_tier_metrics["external_llm"].signal_count == 2


@pytest.mark.asyncio
async def test_build_route_profile_spam_count() -> None:
    docs = [
        _make_doc(spam_probability=0.9),
        _make_doc(spam_probability=0.85),
        _make_doc(spam_probability=0.5),
    ]
    report = await build_route_profile(_make_repo(docs))
    assert report.primary_tier_metrics["external_llm"].spam_count == 2


@pytest.mark.asyncio
async def test_build_route_profile_actionable_via_metadata() -> None:
    docs = [_make_doc(actionable=True), _make_doc(actionable=True), _make_doc(actionable=False)]
    report = await build_route_profile(_make_repo(docs))
    assert report.primary_tier_metrics["external_llm"].actionable_count == 2


@pytest.mark.asyncio
async def test_build_route_profile_shadow_count() -> None:
    docs = [
        _make_doc(shadow_analysis=True),
        _make_doc(shadow_analysis=True),
        _make_doc(shadow_analysis=False),
    ]
    report = await build_route_profile(_make_repo(docs))
    assert report.shadow_in_metadata == 2


def test_build_execution_handoff_report_includes_full_metadata() -> None:
    document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        tickers=["BTC"],
        crypto_assets=[],
        relevance_score=0.93,
        credibility_score=0.88,
        provider="openai",
        analysis_source=AnalysisSource.EXTERNAL_LLM,
        source_name="CoinDesk",
        source_type=SourceType.RSS_FEED,
        summary="ETF demand remains elevated.",
    )
    signals = extract_signal_candidates([document], min_priority=8)

    report = build_execution_handoff_report(signals, [document])

    assert report.signal_count == 1
    payload = report.to_json_dict()["signals"][0]
    assert payload["signal_id"] == f"sig_{document.id}"
    assert payload["direction_hint"] == "bullish"
    assert payload["priority"] == 9
    assert payload["score"] == pytest.approx(0.93)
    assert payload["provider"] == "openai"
    assert payload["analysis_source"] == "external_llm"
    assert payload["route_path"] == "A.external_llm"
    assert payload["path_type"] == "primary"
    assert payload["delivery_class"] == "productive_handoff"
    assert payload["consumer_visibility"] == "visible"
    assert payload["source_name"] == "CoinDesk"
    assert payload["source_type"] == SourceType.RSS_FEED.value
    assert "recommended_next_step" not in payload


def test_build_execution_handoff_report_derives_rule_route_when_provider_missing() -> None:
    document = make_document(
        is_analyzed=True,
        priority_score=8,
        sentiment_label=SentimentLabel.NEUTRAL,
        tickers=[],
        crypto_assets=[],
        relevance_score=0.5,
        provider=None,
        analysis_source=None,
    )
    signals = extract_signal_candidates([document], min_priority=8)

    report = build_execution_handoff_report(signals, [document])

    payload = report.to_json_dict()["signals"][0]
    assert payload["provider"] == "rule"
    assert payload["analysis_source"] == "rule"
    assert payload["route_path"] == "A.rule"
    assert payload["delivery_class"] == "productive_handoff"
    assert payload["consumer_visibility"] == "visible"


def test_build_execution_handoff_report_raises_on_missing_source_document() -> None:
    document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        tickers=["BTC"],
        crypto_assets=[],
    )
    signals = extract_signal_candidates([document], min_priority=8)

    with pytest.raises(ValueError, match="requires the source document"):
        build_execution_handoff_report(signals, [])


def test_build_distribution_classification_report_separates_primary_and_audit_outputs() -> None:
    document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        tickers=["BTC"],
        crypto_assets=[],
        relevance_score=0.93,
        credibility_score=0.88,
        provider="openai",
        analysis_source=AnalysisSource.EXTERNAL_LLM,
        source_name="CoinDesk",
        source_type=SourceType.RSS_FEED,
        summary="ETF demand remains elevated.",
    )
    signals = extract_signal_candidates([document], min_priority=8)
    envelopes = [
        ABCInferenceEnvelope(
            document_id=str(document.id),
            route_profile="primary_with_shadow_and_control",
            primary_result=PathResultEnvelope(
                path_id="A.external_llm",
                provider="openai",
                analysis_source="external_llm",
                summary="Primary result",
                scores={"recommended_priority": 9, "actionable": True},
            ),
            shadow_results=[
                PathResultEnvelope(
                    path_id="B.companion",
                    provider="companion",
                    analysis_source="internal",
                    summary="Shadow audit result",
                    scores={"recommended_priority": 4},
                )
            ],
            control_result=PathResultEnvelope(
                path_id="C.rule",
                provider="rule",
                analysis_source="rule",
                summary="Control comparison result",
                scores={"recommended_priority": 5},
            ),
            comparison_summary=[
                PathComparisonSummary(compared_path="A_vs_B"),
                PathComparisonSummary(compared_path="A_vs_C"),
            ],
            distribution_metadata=DistributionMetadata(
                route_profile="primary_with_shadow_and_control",
                active_primary_path="A.external_llm",
                distribution_targets=["execution_handoff", "abc_audit_jsonl"],
                activation_state="active",
            ),
        )
    ]

    report = build_distribution_classification_report(signals, [document], envelopes)
    payload = report.to_json_dict()

    assert payload["report_type"] == "distribution_classification_report"
    assert payload["primary_signal_count"] == 1
    assert payload["audit_output_count"] == 2
    assert payload["shadow_output_count"] == 1
    assert payload["control_output_count"] == 1
    assert payload["primary_handoff"]["signals"][0]["delivery_class"] == "productive_handoff"
    assert payload["primary_handoff"]["signals"][0]["consumer_visibility"] == "visible"
    assert payload["route_profiles"] == ["primary_with_shadow_and_control"]
    assert payload["active_primary_paths"] == ["A.external_llm"]
    audit_outputs = payload["audit_outputs"]
    assert audit_outputs[0]["path_type"] == "shadow"
    assert audit_outputs[0]["delivery_class"] == "audit_only"
    assert audit_outputs[0]["consumer_visibility"] == "hidden"
    assert audit_outputs[0]["comparison_labels"] == ["A_vs_B"]
    assert audit_outputs[1]["path_type"] == "control"
    assert audit_outputs[1]["delivery_class"] == "comparison_only"
    assert audit_outputs[1]["consumer_visibility"] == "hidden"
    assert audit_outputs[1]["comparison_labels"] == ["A_vs_C"]


def test_distribution_classification_report_to_json_dict_structure() -> None:
    report = DistributionClassificationReport(
        primary_handoff=ExecutionHandoffReport(signal_count=0, signals=[]),
        audit_outputs=[],
        route_profiles=["primary_only"],
        active_primary_paths=["A.external_llm"],
    )

    payload = report.to_json_dict()

    assert payload["report_type"] == "distribution_classification_report"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert payload["route_profiles"] == ["primary_only"]
    assert payload["active_primary_paths"] == ["A.external_llm"]
    assert payload["primary_signal_count"] == 0
    assert payload["audit_output_count"] == 0


def test_save_route_profile_creates_file(tmp_path) -> None:
    report = RouteProfileReport(
        total_analyzed=2,
        primary_tier_metrics={"external_llm": TierProfile(document_count=2)},
    )
    path = save_route_profile(report, tmp_path / "route_profile.json")
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["report_type"] == "route_profile"
    assert data["total_analyzed"] == 2


def test_save_execution_handoff_report_creates_file(tmp_path) -> None:
    document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        tickers=["BTC"],
        crypto_assets=[],
        provider="openai",
        analysis_source=AnalysisSource.EXTERNAL_LLM,
    )
    signals = extract_signal_candidates([document], min_priority=8)
    report = build_execution_handoff_report(signals, [document])

    path = save_execution_handoff_report(report, tmp_path / "handoff.json")

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["report_type"] == "execution_signal_handoff"
    assert data["signal_count"] == 1


def test_save_distribution_classification_report_creates_file(tmp_path) -> None:
    report = DistributionClassificationReport(
        primary_handoff=ExecutionHandoffReport(signal_count=0, signals=[]),
        audit_outputs=[],
        route_profiles=["primary_only"],
        active_primary_paths=["A.external_llm"],
    )

    path = save_distribution_classification_report(
        report, tmp_path / "distribution_classification.json"
    )

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["report_type"] == "distribution_classification_report"
    assert data["primary_signal_count"] == 0


def test_save_handoff_collector_summary_creates_file(tmp_path) -> None:
    handoff = _make_primary_handoff()
    acknowledgement = create_handoff_acknowledgement(
        handoff,
        consumer_agent_id="agent-alpha",
    )
    report = build_handoff_collector_summary([handoff], [acknowledgement])

    path = save_handoff_collector_summary(report, tmp_path / "collector_summary.json")

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["report_type"] == "handoff_collector_summary"
    assert data["acknowledged_count"] == 1
    assert data["pending_count"] == 0


def test_build_handoff_collector_summary_tracks_pending_and_acknowledged() -> None:
    handoff = _make_primary_handoff()
    acknowledgement = create_handoff_acknowledgement(
        handoff,
        consumer_agent_id="agent-alpha",
    )

    report = build_handoff_collector_summary([handoff], [acknowledgement])

    assert report.total_handoffs == 1
    assert report.acknowledged_count == 1
    assert report.pending_count == 0
    assert report.consumers == {"agent-alpha": 1}
    assert report.acknowledged_handoffs[0].handoff_id == handoff.handoff_id


def test_build_handoff_collector_summary_counts_orphaned_acknowledgements() -> None:
    handoff = _make_primary_handoff()
    acknowledgement = create_handoff_acknowledgement(
        handoff,
        consumer_agent_id="agent-alpha",
    )

    rogue_ack = acknowledgement.__class__(
        handoff_id="missing-handoff",
        signal_id=acknowledgement.signal_id,
        consumer_agent_id=acknowledgement.consumer_agent_id,
        acknowledged_at=acknowledgement.acknowledged_at,
        path_type=acknowledgement.path_type,
        delivery_class=acknowledgement.delivery_class,
        consumer_visibility=acknowledgement.consumer_visibility,
        audit_visibility=acknowledgement.audit_visibility,
        notes=acknowledgement.notes,
        status=acknowledgement.status,
    )

    report = build_handoff_collector_summary([handoff], [rogue_ack])

    assert report.total_handoffs == 1
    assert report.acknowledged_count == 0
    assert report.pending_count == 1
    assert report.orphaned_ack_count == 1
