from __future__ import annotations

import json
from pathlib import Path

from app.inference.evaluation import (
    inspect_shadow_comparison_stream,
    shadow_evaluation_summary,
)


def test_shadow_summary_reports_divergence_latency_tokens_and_honest_cost(
    tmp_path: Path,
) -> None:
    path = tmp_path / "shadow.jsonl"
    rows = [
        {
            "current": {},
            "candidate": {
                "schema_pass": True,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "latency_ms": 100,
                "estimated_cost_usd": 0.002,
            },
            "divergence": {
                "direction_disagreement": True,
                "critical_field_disagreement": False,
            },
        },
        {
            "current": {},
            "candidate": {
                "schema_pass": False,
                "prompt_tokens": 20,
                "completion_tokens": 7,
                "latency_ms": 300,
                "estimated_cost_usd": None,
            },
            "divergence": {
                "direction_disagreement": False,
                "critical_field_disagreement": True,
            },
        },
        {
            "kind": "signal_consensus",
            "divergence": {"agreement_disagreement": True},
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    summary = shadow_evaluation_summary(path)
    assert summary["analysis_comparisons"] == 2
    assert summary["candidate_schema_success_rate_pct"] == 50.0
    assert summary["direction_disagreement_rate_pct"] == 100.0
    assert summary["critical_field_disagreement_rate_pct"] == 0.0
    assert summary["candidate_latency_p95_ms"] == 300.0
    assert summary["candidate_tokens"] == 42
    assert summary["candidate_known_cost_usd"] == 0.002
    assert summary["candidate_unknown_cost_calls"] == 1
    assert summary["consensus_disagreement_rate_pct"] == 100.0
    assert summary["primary_recommendation"] == "NOT_PROVEN"


def test_shadow_stream_inspector_rejects_corrupt_and_authoritative_candidate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inference_shadow.jsonl"
    path.write_text(
        "not-json\n"
        + json.dumps({"authoritative": "candidate", "influences_execution": True})
        + "\n",
        encoding="utf-8",
    )
    assert inspect_shadow_comparison_stream(path) == [
        "line_1:invalid_json",
        "line_2:authority_contract_invalid",
    ]
