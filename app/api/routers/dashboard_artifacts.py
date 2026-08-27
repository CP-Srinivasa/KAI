"""Pure artifact timestamp helpers for the operator dashboard.

Kept outside ``dashboard.py`` so the dashboard godfile can shrink without
changing endpoint semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ARTIFACT_STALE_WARNING_HOURS = 3.0
ARTIFACT_STALE_CRITICAL_HOURS = 24.0


def parse_iso_utc(value: object) -> datetime | None:
    """Parse an ISO-8601 value as timezone-aware UTC, returning ``None`` on bad input."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def artifact_updated_at(path: Path) -> str | None:
    """Return the path modification time as ISO UTC, or ``None`` when unavailable."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    except OSError:
        return None


def artifact_stale_status(path: Path, *, now: datetime | None = None) -> str:
    """Classify a dashboard artifact's freshness from its filesystem mtime."""
    updated_at = artifact_updated_at(path)
    if updated_at is None:
        return "unverified"
    updated_dt = parse_iso_utc(updated_at)
    if updated_dt is None:
        return "unverified"
    age_hours = ((now or datetime.now(UTC)) - updated_dt).total_seconds() / 3600.0
    return stale_status_for_age_hours(age_hours)


def stale_status_for_age_hours(age_hours: float | None) -> str:
    """Classify an already-computed age in hours with dashboard freshness thresholds."""
    if age_hours is None:
        return "unverified"
    if age_hours >= ARTIFACT_STALE_CRITICAL_HOURS:
        return "stale"
    if age_hours >= ARTIFACT_STALE_WARNING_HOURS:
        return "warning"
    return "ok"


def first_present_ts(row: dict[str, Any], keys: tuple[str, ...]) -> datetime | None:
    """Return the first parseable UTC timestamp from ``row`` for the given keys."""
    for key in keys:
        parsed = parse_iso_utc(row.get(key))
        if parsed is not None:
            return parsed
    return None
