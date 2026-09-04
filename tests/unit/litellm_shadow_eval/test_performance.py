from __future__ import annotations

import json
from pathlib import Path

from scripts.litellm_shadow_eval.engine import evaluate
from scripts.litellm_shadow_eval.models import GraduationPolicy, GraduationStatus

from tests.unit.litellm_shadow_eval.helpers import proven_flags


def _compact(side: str, number: int) -> dict[str, object]:
    return {
        "schema_version": "litellm-shadow-eval/v1",
        "evaluation_id": f"e{number}",
        "logical_route": "standard",
        "purpose": "analysis",
        "side": side,
        "mode": "off" if side == "DIRECT" else "shadow",
        "actual_provider": "p",
        "actual_model": "m",
        "identity_proven": True,
        "success": True,
        "schema_valid": True,
        "fallback_used": False,
        "retry_count": 0,
        "attempt_count": 1,
        "latency_ms": 1,
        "cost_known": False,
        "timestamp": "2026-09-04T00:00:00Z",
        "execution_authority": side == "DIRECT",
    }


def test_100k_records_streaming_smoke(tmp_path: Path) -> None:
    path = tmp_path / "100k.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for number in range(50_000):
            for side in ("DIRECT", "SHADOW"):
                handle.write(json.dumps(_compact(side, number), separators=(",", ":")) + "\n")
    report = evaluate(
        [path],
        GraduationPolicy(minimum_sample_count=50_000),
        proven_flags(),
    )
    metrics = report.metrics["standard"]
    assert report.record_count == 100_000
    assert metrics.complete_pair_count == 50_000
    assert metrics.invalid_record_count == 0
    assert report.decisions["standard"].status is GraduationStatus.READY
