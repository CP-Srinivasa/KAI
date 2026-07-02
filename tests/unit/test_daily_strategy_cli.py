"""Tests for `trading-bot daily-strategy` CLI commands.

Covers check / bootstrap / reminder. Reminder is V2 from 2026-04-24:
nudges the operator when today's review is missing or stub-only, and is
idempotent per day via a marker file.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli.commands.daily_strategy import (
    _STUB_MARKERS,
    _blocked_alerts_summary,
    _format_dispatch_health_section,
    _last_filled_review_date,
    _staleness_line,
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
    result = runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify", "--no-sync"])
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
    result = runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify", "--no-sync"])
    assert result.exit_code == 0
    assert "already present" in result.stdout
    # File untouched.
    assert today_path.read_text(encoding="utf-8") == custom


def test_bootstrap_force_overwrites(runner: CliRunner, repo_cwd: Path) -> None:
    today_path = _today_path(repo_cwd)
    today_path.parent.mkdir(parents=True, exist_ok=True)
    today_path.write_text("# old", encoding="utf-8")
    result = runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify", "--no-sync", "--force"])
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


def test_blocked_alerts_summary_window_rolls_from_run_time_not_end_of_day(
    repo_cwd: Path,
) -> None:
    """Regression 2026-05-26: ``_blocked_alerts_summary`` anchored its window
    at today's *end-of-day UTC*. A bootstrap run at 08:34 UTC produced a
    window of [yesterday 23:59:59, today 23:59:59] — so a real block at
    today 06:00 UTC AND a real block at yesterday 16:00 UTC both fell
    outside the slice. The CLI then reported "no blocks" while six
    `weak_directional_signal` rejects sat in `blocked_alerts.jsonl`.

    With the rolling-now-anchored window, the same run sees:
    [yesterday 08:34, today 08:34] — i.e. the real last 24h.
    """
    fixed_now = datetime(2026, 5, 26, 8, 34, 0, tzinfo=UTC)
    today = fixed_now.date()
    yesterday_morning = fixed_now - timedelta(hours=22)  # 10:34 yesterday
    yesterday_evening = fixed_now - timedelta(hours=12)  # 20:34 yesterday
    today_morning = fixed_now - timedelta(hours=1)  # 07:34 today
    too_old = fixed_now - timedelta(hours=30)  # outside 24h, must drop
    future = fixed_now + timedelta(hours=2)  # future blocks must drop
    _write_blocked_jsonl(
        repo_cwd,
        [
            {
                "document_id": "yest_morn",
                "block_reason": "weak_directional_signal",
                "blocked_at": yesterday_morning.isoformat(),
                "priority": 10,
                "sentiment_label": "bullish",
                "source_name": "x",
                "normalized_title": "captured by rolling window",
            },
            {
                "document_id": "yest_eve",
                "block_reason": "weak_directional_signal",
                "blocked_at": yesterday_evening.isoformat(),
                "priority": 9,
                "sentiment_label": "bullish",
                "source_name": "x",
                "normalized_title": "captured by rolling window",
            },
            {
                "document_id": "today_morn",
                "block_reason": "low_directional_confidence",
                "blocked_at": today_morning.isoformat(),
                "priority": 8,
                "sentiment_label": "bearish",
                "source_name": "x",
                "normalized_title": "captured by rolling window",
            },
            {
                "document_id": "too_old",
                "block_reason": "x",
                "blocked_at": too_old.isoformat(),
                "priority": 7,
                "sentiment_label": "bullish",
                "source_name": "x",
                "normalized_title": "outside window",
            },
            {
                "document_id": "future",
                "block_reason": "x",
                "blocked_at": future.isoformat(),
                "priority": 7,
                "sentiment_label": "bullish",
                "source_name": "x",
                "normalized_title": "future block",
            },
        ],
    )

    summary = _blocked_alerts_summary(today, now_utc=fixed_now)
    assert summary["total"] == 3, (
        "rolling-now window must include yest_morn + yest_eve + today_morn "
        "(would be 0 with the old end-of-day anchor)"
    )
    reasons = dict(summary["top_reasons"])
    assert reasons.get("weak_directional_signal") == 2
    assert reasons.get("low_directional_confidence") == 1


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


def test_bootstrap_skeleton_includes_dispatch_health(runner: CliRunner, repo_cwd: Path) -> None:
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
    result = runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify", "--no-sync"])
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
    runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify", "--no-sync"])
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
    runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify", "--no-sync"])
    first = runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    assert first.exit_code == 1
    second = runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    # Second run dedups — exit 0, no fresh reminder.
    assert second.exit_code == 0
    assert "already sent" in second.stdout


def test_reminder_force_bypasses_marker(runner: CliRunner, repo_cwd: Path) -> None:
    runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify", "--no-sync"])
    runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    forced = runner.invoke(daily_strategy_app, ["reminder", "--no-notify", "--force"])
    # --force re-evaluates and re-triggers — exit-code reflects current state.
    assert forced.exit_code == 1


def test_reminder_partial_fill_still_flags_remaining_stubs(
    runner: CliRunner, repo_cwd: Path
) -> None:
    runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify", "--no-sync"])
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


# --- staleness surfacing (V3 2026-06-22) ----------------------------------


def test_last_filled_review_date_picks_newest_filled_before_cutoff(
    repo_cwd: Path,
) -> None:
    d = repo_cwd / "artifacts" / "daily_strategy"
    d.mkdir(parents=True, exist_ok=True)
    # An old filled review, a newer stub-only review, plus noise files.
    (d / "2026-01-01.md").write_text("## 1. Lagebild\nDone.\n", encoding="utf-8")
    (d / "2026-02-01.md").write_text(
        "## 1. Lagebild\n" + _STUB_MARKERS[0] + "_\n", encoding="utf-8"
    )
    (d / ".reminder_sent_2026-02-01").write_text("{}", encoding="utf-8")
    # Newest FILLED before cutoff is 2026-01-01 (the 02-01 file is stub-only).
    assert _last_filled_review_date(before=date(2026, 3, 1)) == date(2026, 1, 1)
    # No filled review at all -> None.
    (d / "2026-01-01.md").write_text(_STUB_MARKERS[0] + "_\n", encoding="utf-8")
    assert _last_filled_review_date(before=date(2026, 3, 1)) is None


def test_staleness_line_reports_days_since_last_filled(repo_cwd: Path) -> None:
    d = repo_cwd / "artifacts" / "daily_strategy"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-01-01.md").write_text("## 1. Lagebild\nDone.\n", encoding="utf-8")
    line = _staleness_line(date(2026, 1, 11))
    assert "2026-01-01" in line
    assert "vor 10 Tag(en)" in line


def test_staleness_line_reports_never_when_no_filled(repo_cwd: Path) -> None:
    assert "noch keiner" in _staleness_line(date(2026, 6, 22))


def test_reminder_surfaces_staleness_in_output(runner: CliRunner, repo_cwd: Path) -> None:
    d = repo_cwd / "artifacts" / "daily_strategy"
    d.mkdir(parents=True, exist_ok=True)
    # A long-ago filled review, then today's stub skeleton.
    (d / "2025-01-01.md").write_text("## 1. Lagebild\nDone.\n", encoding="utf-8")
    runner.invoke(daily_strategy_app, ["bootstrap", "--no-notify", "--no-sync"])
    result = runner.invoke(daily_strategy_app, ["reminder", "--no-notify"])
    assert result.exit_code == 1
    assert "Letzter ausgefüllter Review: 2025-01-01" in result.stdout
    assert "vor" in result.stdout
