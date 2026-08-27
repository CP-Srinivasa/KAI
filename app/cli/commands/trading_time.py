"""Pure time parsing helpers for the trading CLI."""

from __future__ import annotations

from datetime import UTC, datetime

import typer


def parse_until_utc(value: str) -> datetime:
    """Parse an ISO-8601 ``--until`` value into a tz-aware UTC datetime.

    Naive values are interpreted as UTC to preserve the previous CLI behavior.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter(f"--until: not an ISO-8601 datetime: {value!r}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
