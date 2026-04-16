"""Persistent reply-keyboard for the operator surface.

Unlike inline keyboards (bound to one message, scroll away), a persistent
reply keyboard docks under the text input field and stays visible across
the whole chat session. Telegram only allows plain text labels on reply
keyboards — tapping a key sends the label as a regular message. Routing
to the matching slash-command is handled by `match_label_to_command`.
"""

from __future__ import annotations

from typing import Final

_KEYBOARD_ROWS: Final[list[list[dict[str, str]]]] = [
    [{"text": "Status"}, {"text": "Help"}],
    [{"text": "Portfolio"}, {"text": "Signals"}],
    [{"text": "Trades"}, {"text": "Alerts"}],
    [{"text": "Quality"}, {"text": "Daily"}],
]

PERSISTENT_KEYBOARD: Final[dict[str, object]] = {
    "keyboard": _KEYBOARD_ROWS,
    "is_persistent": True,
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "selective": False,
}

_LABEL_TO_COMMAND: Final[dict[str, str]] = {
    "status": "status",
    "help": "help",
    "portfolio": "positions",
    "signals": "signals",
    "trades": "signalstatus",
    "alerts": "alertstatus",
    "quality": "quality",
    "daily": "tagesbericht",
}


def match_label_to_command(text: str) -> str | None:
    """Return the slash-command a persistent-keyboard label maps to, or None."""
    return _LABEL_TO_COMMAND.get(text.strip().lower())
