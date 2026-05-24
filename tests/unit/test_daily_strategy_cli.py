"""Tests for `trading-bot daily-strategy` CLI commands.

Covers check / bootstrap / reminder. Reminder is V2 from 2026-04-24:
nudges the operator when today's review is missing or stub-only, and is
idempotent per day via a marker file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli.commands.daily_strategy import (
    _STUB_MARKERS,
    _blocked_alerts_summary,
    _format_dispatch_health_section,
    daily_strategy_app,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def repo_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run each CLI invocation in an isolated tmp working dir."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts").mkdir()
    return tmp_path


def _today_path(repo: Path) -> Path:
    today = datetime.now(UTC).date()
    return repo / "artifacts" / "daily_strategy" / f"{today.isoformat()}.md"


def _marker_path(repo: Path) -> Path:
    today = datetime.now(UTC).date()
    return (
        repo / "artifacts" / "daily_strategy" / f".reminder_sent_{today.isoformat()}.tmp"
    )  # placeholder, overwritten below


def _real_marker_path(repo: Path) -> Path:
    today = datetime.now(UTC).date()
    return repo / "artifacts" / "daily_strategy" / f".reminder_sent_{today.isoformat()}"


# --- check ----------------------------------------------------------------


def test_check_exits_1_when_missing(runner: CliRunner, repo_cwd: Path) -> None:
    result = runner.invoke(daily_strategy_app, ["check"])
    assert result.exit_code == 1
    assert "missing" in result.stdout


def test_check_exits_0_when_present(runner: CliRunner, repo_cwd: Path) -> None:
    today_path = _today_path(repo_cwd)
    today_path.parent.mkdir(parents=True, exist_ok=True)
    today_path.write_text("# stub", encoding="utf-8")
    result = runner.invoke(daily_strategy_app, ["check"])
    assert result.exit_code == 0
    assert "present" in result.stdout


# --- bootstrap ------------------------------------------------------------


def test_bootstrap_writes_skeleton_with_stub_markers(runner: CliRunner, repo_cwd: Path) -> None:
    result = runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify"])
    assert result.exit_code == 0
    today_path = _today_path(repo_cwd)
    assert today_path.exists()
    text = today_path.read_text(encoding="utf-8")
    # All stub markers from the template must be present.
    for marker in _STUB_MARKERS:
        assert marker in text


def test_bootstrap_is_idempotent(runner: CliRunner, repo_cwd: Path) -> None:
    today_path = _today_path(repo_cwd)
    today_path.parent.mkdir(parents=True, exist_ok=True)
    custom = "# Already filled by Claude\nLagebild content."
    today_path.write_text(custom, encoding="utf-8")
    result = runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify"])
    assert result.exit_code == 0
    assert "already present" in result.stdout
    # File untouched.
    assert today_path.read_text(encoding="utf-8") == custom


def test_bootstrap_force_overwrites(runner: CliRunner, repo_cwd: Path) -> None:
    today_path = _today_path(repo_cwd)
    today_path.parent.mkdir(parents=True, exist_ok=True)
    today_path.write_text("# old", encoding="utf-8")
    result = runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify", "--force"])
    assert result.exit_code == 0
    text = today_path.read_text(encoding="utf-8")
    assert text != "# old"
    assert _STUB_MARKERS[0] in text


# --- F4 Dispatch-Health Section ---------------------------------------------


def _write_blocked_jsonl(repo: Path, records: list[dict]) -> Path:
    """Helper: write a blocked_alerts.jsonl in the repo with the given records."""
    path = repo / "artifacts" / "blocked_alerts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return path


def test_blocked_alerts_summary_empty_when_file_missing(repo_cwd: Path) -> None:
    today = datetime.now(UTC).date()
    summary = _blocked_alerts_summary(today)
    assert summary["total"] == 0
    assert summary["top_reasons"] == []
    assert summary["top_blocked"] == []


def test_blocked_alerts_summary_counts_reasons_within_window(repo_cwd: Path) -> None:
    today = datetime.now(UTC).date()
    now = datetime.now(UTC)
    recent = (now - timedelta(hours=2)).isoformat()
    stale = (now - timedelta(hours=48)).isoformat()
    _write_blocked_jsonl(
        repo_cwd,
        [
            {
                "document_id": "d1",
                "block_reason": "reactive_price_narrative",
                "blocked_at": recent,
                "priority": 10,
                "sentiment_label": "bullish",
                "source_name": "cryptobriefing",
                "normalized_title": "iran us mou as btc rallies past 82k",
            },
            {
                "document_id": "d2",
                "block_reason": "reactive_price_narrative",
                "blocked_at": recent,
                "priority": 9,
                "sentiment_label": "bullish",
                "source_name": "coindesk",
                "normalized_title": "bitcoin heads higher on peace deal",
            },
            {
                "document_id": "d3",
                "block_reason": "bearish_directional_disabled",
                "blocked_at": recent,
                "priority": 10,
                "sentiment_label": "bearish",
                "source_name": "cryptoslate",
                "normalized_title": "bitcoin hard money thesis colliding with 5pct yields",
            },
            {
                "document_id": "d_old",
                "block_reason": "reactive_price_narrative",
                "blocked_at": stale,  # outside window — must not count
                "priority": 10,
                "sentiment_label": "bullish",
                "source_name": "x",
                "normalized_title": "old",
            },
        ],
    )

    summary = _blocked_alerts_summary(today)
    assert summary["total"] == 3
    reasons = dict(summary["top_reasons"])
    assert reasons["reactive_price_narrative"] == 2
    assert reasons["bearish_directional_disabled"] == 1


def test_blocked_alerts_summary_top_blocked_sorted_by_priority(repo_cwd: Path) -> None:
    today = datetime.now(UTC).date()
    now = datetime.now(UTC)
    recent = (now - timedelta(hours=1)).isoformat()
    _write_blocked_jsonl(
        repo_cwd,
        [
            {"document_id": f"d{i}", "block_reason": "x", "blocked_at": recent, "priority": p}
            for i, p in enumerate([3, 10, 7, 9, 5])
        ],
    )
    summary = _blocked_alerts_summary(today)
    assert summary["total"] == 5
    top = summary["top_blocked"]
    assert [r["priority"] for r in top] == [10, 9, 7]  # top-3 by priority desc


def test_format_dispatch_health_section_empty() -> None:
    section = _format_dispatch_health_section(
        {"total": 0, "top_reasons": [], "top_blocked": [], "window_start": "x", "window_end": "y"}
    )
    assert "Keine geblockten" in section
    assert "## Dispatch-Health 24h" in section


def test_format_dispatch_health_section_with_records() -> None:
    section = _format_dispatch_health_section(
        {
            "total": 3,
            "top_reasons": [("reactive_price_narrative", 2), ("bearish_directional_disabled", 1)],
            "top_blocked": [
                {
                    "priority": 10,
                    "sentiment_label": "bullish",
                    "source_name": "cryptobriefing",
                    "normalized_title": "iran us mou as btc rallies past 82k",
                    "block_reason": "reactive_price_narrative",
                }
            ],
            "window_start": "2026-05-23T00:00:00+00:00",
            "window_end": "2026-05-24T23:59:59+00:00",
        }
    )
    assert "## Dispatch-Health 24h" in section
    assert "**3** direktionale Alerts geblockt" in section
    assert "reactive_price_narrative" in section
    assert "cryptobriefing" in section
    assert "iran us mou as btc rallies past 82k" in section
    assert "kai-dispatch-filter-root-befund-20260524" in section


def test_bootstrap_skeleton_includes_dispatch_health(
    runner: CliRunner, repo_cwd: Path
) -> None:
    """F4: ensure the bootstrap-rendered skeleton has the Dispatch-Health section."""
    now = datetime.now(UTC)
    _write_blocked_jsonl(
        repo_cwd,
        [
            {
                "document_id": "d1",
                "block_reason": "weak_directional_signal",
                "blocked_at": (now - timedelta(hours=2)).isoformat(),
                "priority": 9,
                "sentiment_label": "bullish",
                "source_name": "beincrypto",
                "normalized_title": "sample headline for f4 test",
            }
        ],
    )
    result = runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify"])
    assert result.exit_code == 0
    text = _today_path(repo_cwd).read_text(encoding="utf-8")
    assert "## Dispatch-Health 24h" in text
    assert "weak_directional_signal" in text
    assert "sample headline for f4 test" in text


# --- reminder -------------------------------------------------------------


def test_reminder_exit2_when_review_missing(runner: CliRunner, repo_cwd: Path) -> None:
    result = runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    assert result.exit_code == 2
    assert "Skeleton fehlt komplett" in result.stdout
    # Marker file written so a second run dedup-skips.
    assert _real_marker_path(repo_cwd).exists()


def test_reminder_exit1_when_skeleton_unfilled(runner: CliRunner, repo_cwd: Path) -> None:
    # Bootstrap first to get the canonical stub-marker layout.
    runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify"])
    result = runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    assert result.exit_code == 1
    assert "Sektion(en) leer" in result.stdout
    marker = _real_marker_path(repo_cwd)
    assert marker.exists()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["review_exists"] is True
    assert "Sektion" in payload["kind"]


def test_reminder_exit0_when_review_filled(runner: CliRunner, repo_cwd: Path) -> None:
    today_path = _today_path(repo_cwd)
    today_path.parent.mkdir(parents=True, exist_ok=True)
    # No stub markers — counts as filled.
    today_path.write_text(
        "# KAI Daily Review\n## 1. Lagebild\nSituation: stable.\n",
        encoding="utf-8",
    )
    result = runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    assert result.exit_code == 0
    assert "review filled" in result.stdout
    # No marker — no reminder was needed, second-run still does the same check.
    assert not _real_marker_path(repo_cwd).exists()


def test_reminder_dedup_skip_when_marker_exists(runner: CliRunner, repo_cwd: Path) -> None:
    runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify"])
    first = runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    assert first.exit_code == 1
    second = runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    # Second run dedups — exit 0, no fresh reminder.
    assert second.exit_code == 0
    assert "already sent" in second.stdout


def test_reminder_force_bypasses_marker(runner: CliRunner, repo_cwd: Path) -> None:
    runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify"])
    runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    forced = runner.invoke(daily_strategy_app, ["reminder", "--no-notify", "--force"])
    # --force re-evaluates and re-triggers — exit-code reflects current state.
    assert forced.exit_code == 1


def test_reminder_partial_fill_still_flags_remaining_stubs(
    runner: CliRunner, repo_cwd: Path
) -> None:
    runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify"])
    today_path = _today_path(repo_cwd)
    text = today_path.read_text(encoding="utf-8")
    # Operator filled exactly one section, others remain stubs.
    text = text.replace(
        "_Bitte Claude füllen — aktuelle Stärken/Schwächen, "
        "offene Baustellen, was Potenzial verschenkt._",
        "Filled by operator: today is fine.",
        1,
    )
    today_path.write_text(text, encoding="utf-8")
    result = runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    assert result.exit_code == 1
    # The kind should reflect the remaining stub count.
    assert "Sektion" in result.stdout
