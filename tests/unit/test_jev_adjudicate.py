from __future__ import annotations

import json
from pathlib import Path

from scripts.jev_shadow_eval.adjudicate import load_adjudication, main


def test_reads_claude_v2_shape_with_top_level_decision_time(tmp_path: Path) -> None:
    source = tmp_path / "v2.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "jev-adjudication/v1",
                "decided_at_utc": "2026-09-24T08:11:02+00:00",
                "cases": [
                    {
                        "case_id": "holdout-0026",
                        "final_label": True,
                        "rule_ref": "R-FIRMA",
                        "decided_by": "operator",
                        "rationale": "operator confirmed",
                        "claude_blind_label": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    document, digest = load_adjudication(source)
    assert len(digest) == 64
    assert document["cases"][0]["decided_at"] == "2026-09-24T08:11:02+00:00"


def test_cli_creates_once_and_never_overwrites(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "jev-adjudication/v1",
                "cases": [
                    {
                        "case_id": "case-1",
                        "final_label": False,
                        "rule_ref": "R",
                        "decided_by": "operator",
                        "decided_at": "2026-09-24T08:11:02+00:00",
                        "rationale": "reason",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "frozen.json"
    argv = ["--input", str(source), "--output", str(output)]
    assert main(argv) == 0
    original = output.read_bytes()
    assert main(argv) == 2
    assert output.read_bytes() == original
