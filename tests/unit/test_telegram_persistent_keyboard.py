"""Unit tests for the persistent reply-keyboard module."""

from __future__ import annotations

from app.messaging.telegram_persistent_keyboard import (
    PERSISTENT_KEYBOARD,
    match_label_to_command,
)


def test_persistent_keyboard_is_persistent_and_resizes() -> None:
    assert PERSISTENT_KEYBOARD["is_persistent"] is True
    assert PERSISTENT_KEYBOARD["resize_keyboard"] is True
    assert PERSISTENT_KEYBOARD["one_time_keyboard"] is False


def test_keyboard_rows_carry_top_level_entries() -> None:
    rows = PERSISTENT_KEYBOARD["keyboard"]
    flat = [btn["text"] for row in rows for btn in row]
    assert set(flat) == {
        "Status", "Help", "Portfolio", "Signals",
        "Trades", "Alerts", "Quality", "Daily",
    }


def test_match_label_maps_to_canonical_commands() -> None:
    assert match_label_to_command("Status") == "status"
    assert match_label_to_command("Help") == "help"
    assert match_label_to_command("Portfolio") == "positions"
    assert match_label_to_command("Signals") == "signals"
    assert match_label_to_command("Trades") == "signalstatus"
    assert match_label_to_command("Alerts") == "alertstatus"
    assert match_label_to_command("Quality") == "quality"
    assert match_label_to_command("Daily") == "tagesbericht"


def test_match_label_is_case_insensitive_and_trims() -> None:
    assert match_label_to_command("  PORTFOLIO  ") == "positions"


def test_unknown_label_returns_none() -> None:
    assert match_label_to_command("random text") is None
    assert match_label_to_command("") is None
