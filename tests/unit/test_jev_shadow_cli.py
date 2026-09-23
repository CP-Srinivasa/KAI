from __future__ import annotations

import json
from pathlib import Path

from scripts.jev_shadow_eval.cli import main


def _case(case_id: str, expected: bool, probability: float) -> dict[str, object]:
    return {
        "schema_version": "jev-shadow-case/v1",
        "case_id": case_id,
        "expected_relevant": expected,
        "baseline_relevant": not expected,
        "latency_ms": 10.0,
        "cost_usd": 0.001,
        "response": {
            "model": "typesafe/jev-test",
            "answers": {"relevant": {"type": "noul", "noul": probability}},
            "usage": {"input_tokens": 10, "output_tokens": 1},
        },
    }


def test_cli_writes_a_reproducible_advisory_report(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    evidence.write_text(
        "\n".join(
            json.dumps(_case(f"case-{index}", index % 2 == 0, 0.95 if index % 2 == 0 else 0.05))
            for index in range(4)
        )
        + "\n",
        encoding="utf-8",
    )
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "minimum_sample_count": 4,
                "negative_below": 0.2,
                "positive_at": 0.8,
                "minimum_coverage": 1.0,
                "maximum_false_negative_rate": 0.0,
                "maximum_brier_score": 0.01,
                "minimum_accuracy_gain": 0.0,
                "require_all_costs_known": True,
                "require_single_model_identity": True,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "reports" / "report.json"

    assert main(["--input", str(evidence), "--policy", str(policy), "--output", str(output)]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "READY_FOR_SHADOW_REVIEW"
    assert report["primary_ready"] is False
    assert report["input_sha256"]
    assert report["policy_sha256"]


def test_cli_fails_closed_for_invalid_input(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    evidence.write_text("not-json\n", encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"minimum_sample_count": 1}), encoding="utf-8")

    assert main(["--input", str(evidence), "--policy", str(policy)]) == 2
