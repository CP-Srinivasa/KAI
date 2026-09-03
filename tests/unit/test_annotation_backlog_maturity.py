"""STAB-2026-09-01 §19 — the annotation backlog warning must be age-aware.

The block counted every directional alert without an outcome row, with no age
filter at all. The auto-annotator is contractually forbidden to touch an alert for
its first four hours (``auto_annotator._DEFAULT_MIN_AGE_HOURS``) and runs every six
(``kai-auto-annotate.timer``, ``OnUnitActiveSec=6h``), so freshly dispatched alerts
were counted as backlog the minute they arrived. The warning measured the ARRIVAL
RATE of directional alerts, not whether the annotator was keeping up.

Replayed against the real Pi ledgers, 1441 hourly samples over 60 days:

    warning (>20) fired in                          199 hours
    genuinely overdue in those hours                never above 5
    at the 2026-08-31T21:00Z warning                32 of 32 were NOT YET DUE

Every one of those 199 warning-hours was a false alarm, and each item was
annotated as soon as it crossed 4h. Raising the >20 gate would have been the wrong
fix — the overdue count never came near it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.auto_annotator import _DEFAULT_MIN_AGE_HOURS
from app.alerts.health_check import _ANNOTATOR_TICK_HOURS, run_health_check_report

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
DUE_AT_HOURS = _DEFAULT_MIN_AGE_HOURS
OVERDUE_AT_HOURS = _DEFAULT_MIN_AGE_HOURS + _ANNOTATOR_TICK_HOURS


def _audits(tmp_path: Path, ages_hours: list[float]) -> Path:
    adir = tmp_path / "artifacts"
    adir.mkdir(parents=True, exist_ok=True)
    with (adir / "alert_audit.jsonl").open("w", encoding="utf-8") as f:
        for i, age in enumerate(ages_hours):
            f.write(
                json.dumps(
                    {
                        "document_id": f"tv:doc_{i:03d}",
                        "channel": "telegram",
                        "directional_eligible": True,
                        "dispatched_at": (NOW - timedelta(hours=age)).isoformat(),
                        "asset": "ETH/USDT",
                    }
                )
                + "\n"
            )
    return adir


def _annotation_issue(adir: Path):
    report = run_health_check_report(artifacts_dir=adir, now=NOW)
    found = [i for i in report.issues if i.component == "annotations"]
    return found[0] if found else None


# --------------------------------------------------------------------------
# NEGATIVE CONTROL — the actual bug
# --------------------------------------------------------------------------
def test_thirty_two_fresh_alerts_do_not_warn(tmp_path: Path) -> None:
    """The exact Pi state at 2026-08-31T21:00Z: 32 items, all aged 3.00-3.99h."""
    adir = _audits(tmp_path, [3.0 + (i % 100) / 100.0 for i in range(32)])
    assert _annotation_issue(adir) is None


def test_an_alert_dispatched_one_minute_ago_is_not_backlog(tmp_path: Path) -> None:
    adir = _audits(tmp_path, [1 / 60] * 40)
    assert _annotation_issue(adir) is None


def test_items_inside_the_annotator_grace_window_do_not_warn(tmp_path: Path) -> None:
    """Due but not yet offered to a single annotator run: the annotator is not late."""
    adir = _audits(tmp_path, [DUE_AT_HOURS + 1.0] * 40)
    assert _annotation_issue(adir) is None


def test_sixty_day_worst_case_still_does_not_warn(tmp_path: Path) -> None:
    """Worst genuinely-overdue count measured over 60 days on the Pi was 5."""
    adir = _audits(tmp_path, [OVERDUE_AT_HOURS + 2.0] * 5 + [1.0] * 75)
    assert _annotation_issue(adir) is None


# --------------------------------------------------------------------------
# POSITIVE CONTROL — a real backlog still fires
# --------------------------------------------------------------------------
def test_a_genuine_overdue_backlog_still_warns(tmp_path: Path) -> None:
    adir = _audits(tmp_path, [OVERDUE_AT_HOURS + 5.0] * 25)
    issue = _annotation_issue(adir)
    assert issue is not None
    assert issue.severity == "warning"
    assert "25 directional alerts overdue" in issue.message


def test_the_message_separates_the_three_populations(tmp_path: Path) -> None:
    """UNANNOTATED_TOTAL / NOT_DUE / DUE_UNANNOTATED must be separately visible."""
    adir = _audits(
        tmp_path,
        [OVERDUE_AT_HOURS + 5.0] * 25 + [DUE_AT_HOURS + 1.0] * 3 + [1.0] * 7,
    )
    issue = _annotation_issue(adir)
    assert issue is not None
    assert "25 directional alerts overdue" in issue.message
    assert "3 in Karenz" in issue.message
    assert "7 noch nicht faellig" in issue.message
    assert "35 unannotiert gesamt" in issue.message


def test_an_unparseable_timestamp_fails_closed_as_overdue(tmp_path: Path) -> None:
    """An item whose age cannot be established is never silently excused.

    A record with no ``dispatched_at`` at all cannot reach this code — the audit
    loader treats the field as mandatory and drops the row — so the reachable
    failure is a malformed value.
    """
    adir = tmp_path / "artifacts"
    adir.mkdir(parents=True, exist_ok=True)
    with (adir / "alert_audit.jsonl").open("w", encoding="utf-8") as f:
        for i in range(25):
            f.write(
                json.dumps(
                    {
                        "document_id": f"tv:undated_{i}",
                        "channel": "telegram",
                        "directional_eligible": True,
                        "dispatched_at": "not-a-timestamp",
                    }
                )
                + "\n"
            )
    issue = _annotation_issue(adir)
    assert issue is not None
    assert "25 directional alerts overdue" in issue.message


def test_the_probe_reads_the_producers_own_maturity_constants() -> None:
    """Probe and annotator must not drift apart."""
    assert DUE_AT_HOURS == 4.0
    assert _ANNOTATOR_TICK_HOURS == 6.0
