"""Tests for app/research/abc_result.py — Sprint 14.4."""

from __future__ import annotations

import json

import pytest

from app.research.abc_result import (
    ABCInferenceEnvelope,
    DistributionMetadata,
    PathComparisonSummary,
    PathResultEnvelope,
    abc_inference_envelope_from_dict,
    load_abc_inference_envelopes,
    save_abc_inference_envelope,
    save_abc_inference_envelope_jsonl,
)


def _make_primary(path_id: str = "A.external_llm") -> PathResultEnvelope:
    return PathResultEnvelope(
        path_id=path_id,
        provider="openai",
        analysis_source="external_llm",
        summary="Primary result",
        scores={"priority_score": 7, "actionable": True},
    )


def _make_shadow() -> PathResultEnvelope:
    return PathResultEnvelope(
        path_id="B.companion",
        provider="companion",
        analysis_source="internal",
        result_ref="shadow_runs/run.jsonl",
        summary="Shadow result",
        scores={"priority_score": 6},
    )


def _make_control() -> PathResultEnvelope:
    return PathResultEnvelope(
        path_id="C.rule",
        provider="rule",
        analysis_source="rule",
        summary="Control result",
        scores={"priority_score": 5},
    )


def _make_envelope(route_profile: str = "primary_only") -> ABCInferenceEnvelope:
    return ABCInferenceEnvelope(
        document_id="doc-001",
        route_profile=route_profile,
        primary_result=_make_primary(),
    )


# ---------------------------------------------------------------------------
# to_json_dict structure
# ---------------------------------------------------------------------------


def test_abc_inference_envelope_to_json_dict_structure() -> None:
    env = _make_envelope()
    d = env.to_json_dict()
    assert "report_type" in d
    assert "document_id" in d
    assert "route_profile" in d
    assert "primary_result" in d
    assert "shadow_results" in d
    assert "control_result" in d
    assert "comparison_summary" in d
    assert "distribution_metadata" in d


def test_abc_inference_envelope_report_type_always_present() -> None:
    env = _make_envelope()
    assert env.to_json_dict()["report_type"] == "abc_inference_envelope"


def test_abc_inference_envelope_primary_only_route() -> None:
    env = _make_envelope(route_profile="primary_only")
    d = env.to_json_dict()
    assert d["route_profile"] == "primary_only"
    assert d["shadow_results"] == []
    assert d["control_result"] is None
    assert d["comparison_summary"] == []
    assert d["distribution_metadata"] is None


def test_abc_inference_envelope_with_shadow_result() -> None:
    env = ABCInferenceEnvelope(
        document_id="doc-002",
        route_profile="primary_with_shadow",
        primary_result=_make_primary(),
        shadow_results=[_make_shadow()],
    )
    d = env.to_json_dict()
    assert len(d["shadow_results"]) == 1
    assert d["shadow_results"][0]["path_id"] == "B.companion"
    assert d["shadow_results"][0]["analysis_source"] == "internal"


def test_abc_inference_envelope_with_control_result() -> None:
    env = ABCInferenceEnvelope(
        document_id="doc-003",
        route_profile="primary_with_control",
        primary_result=_make_primary(),
        control_result=_make_control(),
    )
    d = env.to_json_dict()
    assert d["control_result"] is not None
    assert d["control_result"]["path_id"] == "C.rule"
    assert d["control_result"]["analysis_source"] == "rule"


def test_abc_inference_envelope_with_comparison_summary() -> None:
    comp = PathComparisonSummary(
        compared_path="A_vs_B",
        sentiment_match=True,
        actionable_match=False,
        tag_overlap=0.75,
        deviations={"priority_delta": 1.0, "relevance_delta": -0.5},
        comparison_report_path="reports/comparison.json",
    )
    env = ABCInferenceEnvelope(
        document_id="doc-004",
        route_profile="primary_with_shadow",
        primary_result=_make_primary(),
        shadow_results=[_make_shadow()],
        comparison_summary=[comp],
    )
    d = env.to_json_dict()
    assert len(d["comparison_summary"]) == 1
    cs = d["comparison_summary"][0]
    assert cs["compared_path"] == "A_vs_B"
    assert cs["sentiment_match"] is True
    assert cs["deviations"]["priority_delta"] == 1.0
    assert cs["comparison_report_path"] == "reports/comparison.json"


def test_abc_inference_envelope_with_distribution_metadata() -> None:
    meta = DistributionMetadata(
        route_profile="primary_with_shadow",
        active_primary_path="A.external_llm",
        distribution_targets=["research_brief", "shadow_audit_jsonl"],
    )
    env = ABCInferenceEnvelope(
        document_id="doc-005",
        route_profile="primary_with_shadow",
        primary_result=_make_primary(),
        distribution_metadata=meta,
    )
    d = env.to_json_dict()
    dm = d["distribution_metadata"]
    assert dm is not None
    assert dm["decision_owner"] == "operator"
    assert dm["activation_state"] == "audit_only"
    assert "shadow_audit_jsonl" in dm["distribution_targets"]


def test_abc_inference_envelope_from_dict_round_trips_distribution_metadata() -> None:
    original = ABCInferenceEnvelope(
        document_id="doc-006",
        route_profile="primary_with_shadow_and_control",
        primary_result=_make_primary(),
        shadow_results=[_make_shadow()],
        control_result=_make_control(),
        distribution_metadata=DistributionMetadata(
            route_profile="primary_with_shadow_and_control",
            active_primary_path="A.external_llm",
            distribution_targets=["execution_handoff", "abc_audit_jsonl"],
            activation_state="active",
        ),
    )

    restored = abc_inference_envelope_from_dict(original.to_json_dict())

    assert restored.document_id == "doc-006"
    assert restored.primary_result.path_id == "A.external_llm"
    assert restored.shadow_results[0].path_id == "B.companion"
    assert restored.control_result is not None
    assert restored.control_result.path_id == "C.rule"
    assert restored.distribution_metadata is not None
    assert restored.distribution_metadata.activation_state == "active"
    assert "execution_handoff" in restored.distribution_metadata.distribution_targets


def test_distribution_metadata_decision_owner_default() -> None:
    meta = DistributionMetadata(
        route_profile="primary_only",
        active_primary_path="A.external_llm",
    )
    assert meta.decision_owner == "operator"
    assert meta.activation_state == "audit_only"


# ---------------------------------------------------------------------------
# save_abc_inference_envelope
# ---------------------------------------------------------------------------


def test_save_abc_inference_envelope_creates_file(tmp_path) -> None:
    env = _make_envelope()
    out = save_abc_inference_envelope(env, tmp_path / "envelope.json")
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["report_type"] == "abc_inference_envelope"
    assert data["document_id"] == "doc-001"


# ---------------------------------------------------------------------------
# save_abc_inference_envelope_jsonl
# ---------------------------------------------------------------------------


def test_save_abc_inference_envelope_jsonl_appends(tmp_path) -> None:
    path = tmp_path / "envelopes.jsonl"

    first = _make_envelope()
    second = ABCInferenceEnvelope(
        document_id="doc-999",
        route_profile="primary_only",
        primary_result=_make_primary(),
    )

    save_abc_inference_envelope_jsonl([first], path)
    save_abc_inference_envelope_jsonl([second], path)

    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    ids = [json.loads(ln)["document_id"] for ln in lines]
    assert "doc-001" in ids
    assert "doc-999" in ids


def test_save_abc_inference_envelope_jsonl_each_line_valid_json(tmp_path) -> None:
    envelopes = [_make_envelope(), _make_envelope()]
    path = save_abc_inference_envelope_jsonl(envelopes, tmp_path / "out.jsonl")
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    for line in lines:
        data = json.loads(line)
        assert data["report_type"] == "abc_inference_envelope"


def test_load_abc_inference_envelopes_reads_jsonl(tmp_path) -> None:
    path = tmp_path / "envelopes.jsonl"
    save_abc_inference_envelope_jsonl(
        [
            _make_envelope(),
            ABCInferenceEnvelope(
                document_id="doc-777",
                route_profile="primary_with_shadow",
                primary_result=_make_primary(),
                shadow_results=[_make_shadow()],
            ),
        ],
        path,
    )

    envelopes = load_abc_inference_envelopes(path)

    assert [envelope.document_id for envelope in envelopes] == ["doc-001", "doc-777"]
    assert envelopes[1].shadow_results[0].path_id == "B.companion"


def test_load_abc_inference_envelopes_rejects_invalid_row(tmp_path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text("{\"report_type\": \"abc_inference_envelope\"}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_abc_inference_envelopes(path)
