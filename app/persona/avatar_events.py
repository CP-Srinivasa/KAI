"""KAI Avatar Event Interface — disabled-by-default stub.

Architecture contract:
- avatar_enabled must be explicitly True to activate
- Events are read-only notifications, no state mutation
- No external API calls until enabled
- execution_enabled is always False
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AvatarEvent:
    """Immutable avatar event for future visual persona."""

    event_type: str  # e.g. "mood_change", "alert", "status_update"
    payload: dict[str, object]
    timestamp_utc: str
    execution_enabled: bool = False
    write_back_allowed: bool = False


class AvatarEventInterface:
    """Abstract avatar event interface — stub only."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def emit(self, event: AvatarEvent) -> bool:
        """Emit an avatar event. Returns False if disabled."""
        if not self._enabled:
            return False
        # Future: delegate to avatar rendering engine
        return False
