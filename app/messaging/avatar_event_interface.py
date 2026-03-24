"""Disabled-by-default avatar event interface stub for future KAI channels."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AvatarEvent:
    event_type: str
    payload: dict[str, object] = field(default_factory=dict)
    correlation_id: str | None = None


@dataclass(frozen=True)
class AvatarPublishResult:
    enabled: bool = False
    status: str = "disabled"
    event_type: str | None = None
    reason: str = "Avatar interface is disabled by default."


class AvatarEventInterface:
    """Safe no-op avatar surface until an approved backend is connected."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def publish(self, event: AvatarEvent) -> AvatarPublishResult:
        if not self._enabled:
            return AvatarPublishResult(event_type=event.event_type)
        return AvatarPublishResult(
            enabled=True,
            status="noop",
            event_type=event.event_type,
            reason=(
                "Avatar backend is not connected. "
                f"Event {event.event_type!r} was accepted without side effects."
            ),
        )
