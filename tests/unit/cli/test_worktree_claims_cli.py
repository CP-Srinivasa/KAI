"""CLI contract for STAB-12 worktree/claim hygiene reporting."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from typer.testing import CliRunner

from app.cli.commands import worktree_claims_cli
from app.cli.commands.audit import audit_app

runner = CliRunner()


def _fake_report() -> dict[str, Any]:
    return {
        "schema_version": "worktree_claims_hygiene/v1",
        "generated_at_utc": datetime(2026, 8, 27, tzinfo=UTC).isoformat(),
        "read_only": True,
        "worktrees": {
            "counts": {
                "total": 2,
                "older_than_14d": 1,
                "older_than_30d": 0,
                "missing_paths": 0,
                "merged": 1,
                "open": 1,
                "unmerged": 0,
                "closed_unmerged": 0,
                "unknown": 0,
            },
            "items": [],
        },
        "claims": {
            "counts": {
                "active_valid": 1,
                "active_expired": 1,
                "active_missing_expiry": 0,
                "closed": 2,
                "expired": 0,
                "other": 0,
            },
            "items": [],
        },
    }


def test_worktree_claims_report_json_is_registered_and_read_only(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    claims = tmp_path / "ACTIVE_CLAIMS.md"
    claims.write_text(
        "| claim_id | owner | worktree | scope | created_at | expires_at | status |\n"
    )
    observed: dict[str, object] = {}

    def fake_collect_report(
        repo_path: Path, claims_path: Path, *, base_ref: str, now: datetime
    ) -> dict[str, Any]:
        observed.update(
            {
                "repo": repo_path,
                "claims": claims_path,
                "base_ref": base_ref,
                "now_is_utc": now.tzinfo is UTC,
            }
        )
        return _fake_report()

    monkeypatch.setattr(worktree_claims_cli, "collect_report", fake_collect_report)

    result = runner.invoke(
        audit_app,
        [
            "worktree-claims-report",
            "--repo",
            str(repo),
            "--claims",
            str(claims),
            "--base-ref",
            "origin/main",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"read_only": true' in result.output
    assert '"schema_version": "worktree_claims_hygiene/v1"' in result.output
    assert observed["repo"] == repo.resolve()
    assert observed["claims"] == claims.resolve()
    assert observed["base_ref"] == "origin/main"
    assert observed["now_is_utc"] is True


def test_worktree_claims_report_summary_is_operator_scannable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    claims = tmp_path / "ACTIVE_CLAIMS.md"
    claims.write_text(
        "| claim_id | owner | worktree | scope | created_at | expires_at | status |\n"
    )
    monkeypatch.setattr(worktree_claims_cli, "collect_report", lambda *_, **__: _fake_report())

    result = runner.invoke(
        audit_app,
        ["worktree-claims-report", "--repo", str(repo), "--claims", str(claims)],
    )

    assert result.exit_code == 0, result.output
    assert "worktrees=2" in result.output
    assert "merged=1" in result.output
    assert "open=1" in result.output
    assert "claims_active_expired=1" in result.output


def test_worktree_claims_cli_source_has_no_destructive_verbs() -> None:
    source = Path(worktree_claims_cli.__file__).read_text(encoding="utf-8")

    forbidden = [
        "worktree remove",
        "worktree prune",
        "Remove-Item",
        "unlink(",
        "rmtree(",
        ".write_text(",
    ]
    for token in forbidden:
        assert token not in source
