from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.worktree_claims_report import (
    build_report,
    parse_claims,
    parse_worktrees,
    render_json,
)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def test_parse_worktrees_preserves_branch_and_detached_state() -> None:
    raw = """worktree C:/repo
HEAD aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
branch refs/heads/main

worktree C:/tmp/old
HEAD bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
detached
"""

    assert parse_worktrees(raw) == [
        {
            "path": "C:/repo",
            "head": "a" * 40,
            "branch": "main",
            "detached": False,
        },
        {
            "path": "C:/tmp/old",
            "head": "b" * 40,
            "branch": None,
            "detached": True,
        },
    ]


def test_claim_parser_handles_embedded_pipe_and_classifies_bad_leases(tmp_path: Path) -> None:
    claims = tmp_path / "ACTIVE_CLAIMS.md"
    claims.write_text(
        """| claim_id | owner_agent | worktree/branch | scope | created_at | expires_at | status |
|---|---|---|---|---|---|---|
| good | codex | wt | `a|b` and tests | 2026-08-25T10:00Z | 2026-08-25T13:00Z | active |
| overdue | codex | wt | scope | 2026-08-24T10:00Z | 2026-08-25T11:00Z | active (work) |
| missing | claude | wt | scope | 2026-07-11 | — | active |
| done | claude | wt | scope | 2026-08-20T10:00Z | 2026-08-21T10:00Z | closed (#1) |
""",
        encoding="utf-8",
    )

    parsed = parse_claims(claims, now=NOW)

    assert [item["lease_state"] for item in parsed] == [
        "active_valid",
        "active_expired",
        "active_missing_expiry",
        "closed",
    ]
    assert parsed[0]["scope"] == "`a|b` and tests"


def test_report_uses_pr_state_before_ancestry_and_never_changes_claims(tmp_path: Path) -> None:
    live = tmp_path / "live"
    old = tmp_path / "old"
    live.mkdir()
    old.mkdir()
    claims = tmp_path / "ACTIVE_CLAIMS.md"
    claims.write_text(
        "| c1 | codex | wt | scope | 2026-08-24T10:00Z | 2026-08-25T11:00Z | active |\n",
        encoding="utf-8",
    )
    before = claims.read_bytes()
    worktrees: list[dict[str, Any]] = [
        {"path": str(live), "head": "a" * 40, "branch": "feature/squashed", "detached": False},
        {"path": str(old), "head": "b" * 40, "branch": "feature/open", "detached": False},
        {"path": str(tmp_path / "missing"), "head": "c" * 40, "branch": None, "detached": True},
    ]

    def commit_time(head: str) -> int | None:
        return {
            "a" * 40: int(NOW.timestamp()) - 86_400,
            "b" * 40: int(NOW.timestamp()) - 40 * 86_400,
        }.get(head)

    report = build_report(
        worktrees=worktrees,
        claims=parse_claims(claims, now=NOW),
        now=NOW,
        commit_time=commit_time,
        ancestry_status=lambda _head: "unmerged",
        pr_index={
            "feature/squashed": {"number": 7, "state": "MERGED", "url": "https://x/7"},
            "feature/open": {"number": 8, "state": "OPEN", "url": "https://x/8"},
        },
    )

    items = report["worktrees"]["items"]
    assert items[0]["merge_status"] == "merged"
    assert items[0]["merge_status_source"] == "github_pr"
    assert items[1]["merge_status"] == "open"
    assert items[2]["path_exists"] is False
    assert report["worktrees"]["counts"] == {
        "total": 3,
        "older_than_14d": 1,
        "older_than_30d": 1,
        "missing_paths": 1,
        "merged": 1,
        "open": 1,
        "unmerged": 1,
        "closed_unmerged": 0,
        "unknown": 0,
    }
    assert report["claims"]["counts"]["active_expired"] == 1
    assert claims.read_bytes() == before


def test_module_source_has_no_destructive_filesystem_or_git_commands() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app" / "observability" / "worktree_claims_report.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("worktree remove", "worktree prune", "unlink(", "rmtree(", "Remove-Item"):
        assert forbidden not in source


def test_json_output_is_safe_for_legacy_windows_console_encoding() -> None:
    rendered = render_json({"scope": "old → new"})

    assert "\\u2192" in rendered
    assert rendered.encode("cp1252")
