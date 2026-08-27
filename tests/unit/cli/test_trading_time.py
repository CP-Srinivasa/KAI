from __future__ import annotations

from datetime import UTC, datetime

import pytest
import typer

from app.cli.commands.trading_time import parse_until_utc


def test_parse_until_utc_accepts_z_suffix() -> None:
    assert parse_until_utc("2026-07-01T00:00:00Z") == datetime(
        2026,
        7,
        1,
        tzinfo=UTC,
    )


def test_parse_until_utc_preserves_previous_naive_as_utc_behavior() -> None:
    assert parse_until_utc("2026-07-01T00:00:00") == datetime(
        2026,
        7,
        1,
        tzinfo=UTC,
    )


def test_parse_until_utc_raises_cli_parameter_error_for_bad_input() -> None:
    with pytest.raises(typer.BadParameter, match="--until"):
        parse_until_utc("not-a-date")
