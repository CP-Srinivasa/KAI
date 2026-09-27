from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.litellm_shadow_eval.models import GraduationPolicy

from tests.unit.litellm_shadow_eval.helpers import row, runtime_evidence, write_jsonl

ROOT = Path(__file__).resolve().parents[3]


def _run(
    tmp_path: Path, evidence: Path, *, runtime: dict[str, Any] | None = None
) -> subprocess.CompletedProcess[str]:
    policy = tmp_path / "policy.json"
    runtime_path = tmp_path / "runtime.json"
    policy.write_text(
        json.dumps(asdict(GraduationPolicy(minimum_sample_count=1))), encoding="utf-8"
    )
    if runtime is None:
        # Das CLI misst gegen die echte Uhr: die Nachweise sind deshalb relativ
        # zu JETZT datiert, sonst wuerde dieser Test mit dem Kalender rot.
        gestern = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        runtime = runtime_evidence(proven_at=gestern)
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
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
            str(runtime_path),
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
    evidence = write_jsonl(
        tmp_path / "replay.jsonl",
        [row("DIRECT", quality_score=0.8), row("SHADOW", quality_score=0.8)],
    )
    result = _run(tmp_path, evidence)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert report["decisions"]["standard"]["status"] == "READY"
    assert report["primary_ready_routes"] == ["standard"]
    assert report["schema_version"] == "litellm-shadow-eval-report/v2"
    assert report["runtime_evidence"]["rollback_proven"]["referenced"] is True
    assert report["runtime_evidence"]["rollback_proven"]["artifact_sha256"] in markdown
    assert "advisory evidence only" in markdown
    assert "ACTIVATE_PRIMARY" not in markdown


def test_cli_nackte_laufzeitbooleans_sind_not_ready_exit_5(tmp_path: Path) -> None:
    evidence = write_jsonl(
        tmp_path / "replay.jsonl",
        [row("DIRECT", quality_score=0.8), row("SHADOW", quality_score=0.8)],
    )
    result = _run(tmp_path, evidence, runtime=runtime_evidence(referenced=False))
    assert result.returncode == 5, result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["primary_ready_routes"] == []
    assert (
        "RUNTIME_PROOF_UNREFERENCED:off_mode_proven" in report["decisions"]["standard"]["reasons"]
    )


def test_cli_halbe_paare_ohne_grund_sind_not_ready_exit_5(tmp_path: Path) -> None:
    evidence = write_jsonl(
        tmp_path / "replay.jsonl",
        [row("DIRECT", quality_score=0.8), row("SHADOW", quality_score=0.8), row("DIRECT", 1)],
    )
    result = _run(tmp_path, evidence)
    assert result.returncode == 5, result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["decisions"]["standard"]["reasons"] == ["INCOMPLETE_PAIRS_UNEXPLAINED"]
    assert report["primary_ready_routes"] == []


def test_cli_kaputtes_nachweisobjekt_ist_ein_konfigurationsfehler_exit_2(tmp_path: Path) -> None:
    evidence = write_jsonl(tmp_path / "replay.jsonl", [row("DIRECT"), row("SHADOW")])
    runtime = runtime_evidence()
    runtime["rollback_proven"] = {"proven": True, "artifact": "x"}
    assert _run(tmp_path, evidence, runtime=runtime).returncode == 2


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
