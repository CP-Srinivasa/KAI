"""KAI Persona Service — disabled-by-default stub.

This module defines the interface contract for KAI's persona layer.
Voice, avatar, and visual identity features are architecturally prepared
here but MUST remain disabled until explicitly enabled by operator.

Security invariants:
- persona_enabled defaults to False
- No external API calls until enabled
- No state mutation
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PersonaConfig:
    """Persona configuration — all features disabled by default."""

    persona_enabled: bool = False
    voice_enabled: bool = False
    avatar_enabled: bool = False
    display_name: str = "KAI"
    persona_version: str = "0.1.0"


@dataclass(frozen=True)
class PersonaState:
    """Read-only persona state snapshot."""

    config: PersonaConfig = field(default_factory=PersonaConfig)
    active: bool = False
    execution_enabled: bool = False
    write_back_allowed: bool = False

    def to_json_dict(self) -> dict[str, object]:
        return {
            "persona_enabled": self.config.persona_enabled,
            "voice_enabled": self.config.voice_enabled,
            "avatar_enabled": self.config.avatar_enabled,
            "display_name": self.config.display_name,
            "persona_version": self.config.persona_version,
            "active": self.active,
            "execution_enabled": self.execution_enabled,
            "write_back_allowed": self.write_back_allowed,
        }


def get_persona_state(config: PersonaConfig | None = None) -> PersonaState:
    """Return the current persona state. Always returns disabled if no config."""
    cfg = config or PersonaConfig()
    return PersonaState(config=cfg, active=cfg.persona_enabled)
