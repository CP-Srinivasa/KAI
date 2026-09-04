from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from scripts.litellm_shadow_eval.models import GraduationPolicy

from tests.unit.litellm_shadow_eval.helpers import proven_flags, row, write_jsonl

ROOT = Path(__file__).resolve().parents[3]


def _run(tmp_path: Path, evidence: Path) -> subprocess.CompletedProcess[str]:
    policy = tmp_path / "policy.json"
    runtime = tmp_path / "runtime.json"
    policy.write_text(
        json.dumps(asdict(GraduationPolicy(minimum_sample_count=1))), encoding="utf-8"
    )
    runtime.write_text(json.dumps(asdict(proven_flags())), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.litellm_shadow_eval.cli",
            "--replay",
            str(evidence),
            "--policy",
            str(policy),
            "--runtime-evidence",
            str(runtime),
            "--json-out",
            str(tmp_path / "report.json"),
            "--md-out",
            str(tmp_path / "report.md"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_writes_canonical_json_and_markdown_without_network(tmp_path: Path) -> None:
    evidence = write_jsonl(tmp_path / "replay.jsonl", [row("DIRECT"), row("SHADOW")])
    result = _run(tmp_path, evidence)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert report["decisions"]["standard"]["status"] == "READY"
    assert report["schema_version"] == "litellm-shadow-eval-report/v1"
    assert "advisory evidence only" in markdown
    assert "ACTIVATE_PRIMARY" not in markdown


def test_cli_invalid_evidence_has_stable_exit_code_3(tmp_path: Path) -> None:
    evidence = tmp_path / "bad.jsonl"
    evidence.write_text("{broken\n", encoding="utf-8")
    result = _run(tmp_path, evidence)
    assert result.returncode == 3
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["invalid_record_count"] == 1


def test_cli_insufficient_evidence_has_stable_exit_code_4(tmp_path: Path) -> None:
    evidence = write_jsonl(tmp_path / "empty.jsonl", [])
    assert _run(tmp_path, evidence).returncode == 4
