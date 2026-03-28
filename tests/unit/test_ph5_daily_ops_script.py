from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _powershell_executable() -> str | None:
    return shutil.which("powershell")


def _run_daily_ops(
    *,
    artifacts_dir: Path,
    sources_config_path: Path,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    pwsh = _powershell_executable()
    if pwsh is None:
        pytest.skip("powershell executable not available")

    script_path = _repo_root() / "scripts" / "ph5_daily_ops.ps1"
    args = [
        pwsh,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-DryOps",
        "-SkipExchangeRelay",
        "-ArtifactsDir",
        str(artifacts_dir),
        "-SourcesConfigPath",
        str(sources_config_path),
    ]
    if extra_args:
        args.extend(extra_args)

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        args,
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell script test is Windows-only")
def test_ph5_daily_ops_uses_multi_source_plan_and_creates_retention_snapshot(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "alert_audit.jsonl").write_text('{"document_id":"a"}\n', encoding="utf-8")
    (artifacts / "alert_outcomes.jsonl").write_text(
        '{"document_id":"a","outcome":"hit"}\n',
        encoding="utf-8",
    )

    sources_json = tmp_path / "sources.json"
    sources_json.write_text(
        (
            '{"sources":['
            '{"url":"https://example.com/feed-a.xml","source_id":"feed_a","source_name":"Feed A"},'
            '{"url":"https://example.com/feed-b.xml","source_id":"feed_b","source_name":"Feed B"}'
            "]}"
        ),
        encoding="utf-8",
    )

    result = _run_daily_ops(artifacts_dir=artifacts, sources_config_path=sources_json)

    assert result.returncode == 0, result.stderr
    assert "Auto-check mode: apply" in result.stdout
    assert "Source plan: 2 source(s)" in result.stdout
    assert "feed_a" in result.stdout
    assert "feed_b" in result.stdout

    backups = artifacts / "retention_backups"
    assert backups.exists()
    snapshots = [p for p in backups.iterdir() if p.is_dir() and p.name.startswith("snapshot_")]
    assert snapshots
    newest = sorted(snapshots)[-1]
    assert (newest / "alert_audit.jsonl").exists()
    assert (newest / "alert_outcomes.jsonl").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell script test is Windows-only")
def test_ph5_daily_ops_supports_explicit_dry_run_auto_check_mode(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    sources_json = tmp_path / "sources.json"
    sources_json.write_text(
        (
            '{"sources":['
            '{"url":"https://example.com/feed.xml","source_id":"feed_1","source_name":"Feed 1"}'
            "]}"
        ),
        encoding="utf-8",
    )

    result = _run_daily_ops(
        artifacts_dir=artifacts,
        sources_config_path=sources_json,
        extra_args=["-DryRunAutoCheck"],
    )

    assert result.returncode == 0, result.stderr
    assert "Auto-check mode: dry-run" in result.stdout
