"""Hold-Report-Snapshot Freshness Helper.

V-DB5 P2 Vorschlag 6 (2026-05-09): Failsafe gegen stale snapshots.
``artifacts/ph5_hold/ph5_hold_metrics_report.json`` wird vom
``kai-hold-report.timer`` (daily 05:00 UTC) geschrieben. Wenn der
Timer aussetzt, bleibt der Snapshot stale — Operator liest ihn aber
weiter (Telegram ``/quality``-Command, manueller ``cat``,
post-mortem).

Standalone-Modul, damit hold_metrics.py (im V-DB5-Backend-Stash
modifiziert) nicht angefasst werden muss. Pure Functions, einfach
testbar.

ENV-Variablen (optional override):
- APP_HOLD_REPORT_STALE_WARN_HOURS  (default 30) — soft warning
- APP_HOLD_REPORT_STALE_CRIT_HOURS  (default 168) — hard critical (1 Woche)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

_DEFAULT_WARN_HOURS = 30
_DEFAULT_CRIT_HOURS = 168  # 1 Woche

FreshnessLevel = Literal["fresh", "warn", "critical", "missing", "unparseable"]


@dataclass(frozen=True)
class FreshnessResult:
    """Pure-function output — caller decides UI/log/push."""

    level: FreshnessLevel
    age_hours: float | None
    generated_at: str | None
    warn_hours_threshold: int
    crit_hours_threshold: int
    message: str

    @property
    def is_stale(self) -> bool:
        return self.level in ("warn", "critical", "missing", "unparseable")


def load_thresholds_from_env(env: dict[str, str] | None = None) -> tuple[int, int]:
    """Returns ``(warn_hours, crit_hours)`` from ENV or defaults."""
    src = env if env is not None else os.environ
    warn = int(src.get("APP_HOLD_REPORT_STALE_WARN_HOURS", _DEFAULT_WARN_HOURS))
    crit = int(src.get("APP_HOLD_REPORT_STALE_CRIT_HOURS", _DEFAULT_CRIT_HOURS))
    return warn, crit


def evaluate_snapshot_freshness(
    *,
    generated_at: str | None,
    now: datetime,
    warn_hours: int = _DEFAULT_WARN_HOURS,
    crit_hours: int = _DEFAULT_CRIT_HOURS,
) -> FreshnessResult:
    """Pure-function freshness evaluation.

    - ``generated_at=None`` → ``missing`` (snapshot file existed but had
      no timestamp).
    - unparseable timestamp → ``unparseable``.
    - age < warn_hours → ``fresh``.
    - warn_hours ≤ age < crit_hours → ``warn``.
    - age ≥ crit_hours → ``critical``.
    """
    if generated_at is None:
        return FreshnessResult(
            level="missing",
            age_hours=None,
            generated_at=None,
            warn_hours_threshold=warn_hours,
            crit_hours_threshold=crit_hours,
            message="Snapshot ohne `generated_at` — Schreibpfad reparieren",
        )
    try:
        ts = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        return FreshnessResult(
            level="unparseable",
            age_hours=None,
            generated_at=generated_at,
            warn_hours_threshold=warn_hours,
            crit_hours_threshold=crit_hours,
            message=f"Snapshot-Timestamp nicht parsebar: {generated_at[:32]}",
        )

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    age_hours = (now - ts).total_seconds() / 3600.0
    if age_hours < warn_hours:
        return FreshnessResult(
            level="fresh",
            age_hours=age_hours,
            generated_at=generated_at,
            warn_hours_threshold=warn_hours,
            crit_hours_threshold=crit_hours,
            message="",
        )
    if age_hours < crit_hours:
        return FreshnessResult(
            level="warn",
            age_hours=age_hours,
            generated_at=generated_at,
            warn_hours_threshold=warn_hours,
            crit_hours_threshold=crit_hours,
            message=(
                f"Snapshot ist {age_hours:.0f}h alt (Schwelle {warn_hours}h) — Live-API empfohlen"
            ),
        )
    age_days = age_hours / 24.0
    return FreshnessResult(
        level="critical",
        age_hours=age_hours,
        generated_at=generated_at,
        warn_hours_threshold=warn_hours,
        crit_hours_threshold=crit_hours,
        message=(
            f"Snapshot ist {age_days:.0f} Tage alt "
            f"(>{crit_hours / 24:.0f}d) — kai-hold-report.timer prüfen"
        ),
    )


def freshness_for_report(
    report: dict[str, Any] | None, *, now: datetime | None = None
) -> FreshnessResult:
    """Convenience-wrapper: extract ``generated_at`` from a report dict."""
    moment = now if now is not None else datetime.now(UTC)
    warn_h, crit_h = load_thresholds_from_env()
    if report is None:
        return FreshnessResult(
            level="missing",
            age_hours=None,
            generated_at=None,
            warn_hours_threshold=warn_h,
            crit_hours_threshold=crit_h,
            message="Snapshot-Datei nicht vorhanden",
        )
    generated_at = report.get("generated_at") if isinstance(report, dict) else None
    return evaluate_snapshot_freshness(
        generated_at=generated_at if isinstance(generated_at, str) else None,
        now=moment,
        warn_hours=warn_h,
        crit_hours=crit_h,
    )


def annotate_report(report: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return a copy of *report* with `_freshness` field added.

    Used by API endpoints that want to surface staleness without
    breaking existing schema. Field ordering: `_freshness` lives at
    the top level, never inside business data.
    """
    if not isinstance(report, dict):
        return report
    moment = now if now is not None else datetime.now(UTC)
    result = freshness_for_report(report, now=moment)
    annotated = dict(report)
    annotated["_freshness"] = {
        "level": result.level,
        "age_hours": result.age_hours,
        "generated_at": result.generated_at,
        "warn_hours_threshold": result.warn_hours_threshold,
        "crit_hours_threshold": result.crit_hours_threshold,
        "message": result.message,
        "is_stale": result.is_stale,
    }
    return annotated


def telegram_warning_suffix(result: FreshnessResult) -> str:
    """Human-readable suffix for Telegram /quality command. Empty if fresh."""
    if result.level == "fresh":
        return ""
    icon = "⚠️" if result.level == "warn" else "🔴" if result.level == "critical" else "❓"
    return f"\n\n{icon} _{result.message}_"
