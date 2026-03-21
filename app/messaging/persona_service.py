"""Disabled-by-default persona surface for future multichannel KAI outputs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaSnapshot:
    """Read-only persona metadata used by presentation layers only."""

    persona_name: str = "KAI"
    channel: str = "text"
    enabled: bool = False
    style: str = "calm_precise_security_first"
    identity_statement: str = (
        "KAI remains calm, precise, disciplined, and security-first."
    )


class PersonaService:
    """Disabled-by-default persona service with no behavioral side effects."""

    def __init__(self, *, enabled: bool = False, persona_name: str = "KAI") -> None:
        self._enabled = enabled
        self._persona_name = persona_name

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def build_snapshot(self, *, channel: str = "text") -> PersonaSnapshot:
        return PersonaSnapshot(
            persona_name=self._persona_name,
            channel=channel,
            enabled=self._enabled,
        )
