"""KAI Persona — YAML loader with schema validation.

Spec: docs/kai_persona/technical_ui_pack_v3_2.md §13 (JSON-Schema)
       docs/kai_persona/final_execution_prompt_v3_4.md §7

Frontend consumes the validated snapshot via /api/kai/persona. Backend
(this module + KaiAuditService) is the single source of truth for runtime
behaviour: state machine, phrases, templates, motto.

Fail-closed: an invalid YAML raises KaiPersonaConfigError so callers can
emit a KAI_CONFIG_VALIDATION_FAILED audit event and degrade to ERROR/OFFLINE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "kai_persona.yaml"

VALID_STATES = ("IDLE", "ANALYSIS", "SIGNAL", "WARNING", "SECURITY", "ERROR", "OFFLINE")
REQUIRED_MOTTO = "Persona non grata"


class KaiPersonaConfigError(ValueError):
    """Raised when the persona config violates the schema."""


@dataclass(frozen=True)
class KaiStateConfig:
    state: str
    priority: int
    color: str
    icon: str
    animation: str
    ui_behavior: str
    severity: str
    phrases_de: tuple[str, ...]
    phrases_en: tuple[str, ...]


@dataclass(frozen=True)
class KaiPersona:
    """Parsed and validated persona snapshot."""

    name: str
    full_name: str
    motto: str
    version: str
    language_default: str
    languages_supported: tuple[str, ...]
    state_machine_default: str
    state_machine_priority_order: tuple[str, ...]
    states: dict[str, KaiStateConfig] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def state_config(self, state: str) -> KaiStateConfig:
        cfg = self.states.get(state)
        if cfg is None:
            raise KaiPersonaConfigError(f"unknown state: {state}")
        return cfg

    def to_snapshot_dict(self) -> dict[str, Any]:
        """Snapshot exposed to the frontend via /api/kai/persona.

        Strips only-runtime fields; keeps everything the SPA needs for typed
        rendering (states, phrases, dashboard/telegram metadata).
        """
        kai = self.raw.get("kai", {})
        return {
            "id": "kai",
            "name": self.name,
            "full_name": self.full_name,
            "motto": self.motto,
            "version": self.version,
            "language_default": self.language_default,
            "languages_supported": list(self.languages_supported),
            "state_machine": kai.get("state_machine", {}),
            "dashboard": kai.get("dashboard", {}),
            "telegram": kai.get("telegram", {}),
            "snapshot_generated_at": datetime.now(UTC).isoformat(),
        }


def _validate(parsed: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(parsed, dict) or "kai" not in parsed:
        raise KaiPersonaConfigError("config root must be a mapping with key 'kai'")
    kai = parsed["kai"]
    if not isinstance(kai, dict):
        raise KaiPersonaConfigError("'kai' must be a mapping")

    motto = kai.get("motto")
    if motto != REQUIRED_MOTTO:
        raise KaiPersonaConfigError(
            f"motto must be '{REQUIRED_MOTTO}', got {motto!r}",
        )

    sm = kai.get("state_machine")
    if not isinstance(sm, dict):
        raise KaiPersonaConfigError("state_machine missing")
    if "states" not in sm or not isinstance(sm["states"], dict):
        raise KaiPersonaConfigError("state_machine.states missing")

    for state in VALID_STATES:
        if state not in sm["states"]:
            raise KaiPersonaConfigError(f"state_machine.states.{state} missing")
        node = sm["states"][state]
        for required_field in (
            "priority",
            "color",
            "icon",
            "animation",
            "ui_behavior",
            "severity",
            "phrases_de",
            "phrases_en",
        ):
            if required_field not in node:
                raise KaiPersonaConfigError(
                    f"state_machine.states.{state}.{required_field} missing",
                )
        if not isinstance(node["phrases_de"], list) or not node["phrases_de"]:
            raise KaiPersonaConfigError(
                f"state_machine.states.{state}.phrases_de must be a non-empty list",
            )
        if not isinstance(node["phrases_en"], list) or not node["phrases_en"]:
            raise KaiPersonaConfigError(
                f"state_machine.states.{state}.phrases_en must be a non-empty list",
            )
    return parsed


def _build_persona(parsed: dict[str, Any]) -> KaiPersona:
    kai = parsed["kai"]
    sm = kai["state_machine"]
    states: dict[str, KaiStateConfig] = {}
    for state in VALID_STATES:
        node = sm["states"][state]
        states[state] = KaiStateConfig(
            state=state,
            priority=int(node["priority"]),
            color=str(node["color"]),
            icon=str(node["icon"]),
            animation=str(node["animation"]),
            ui_behavior=str(node["ui_behavior"]),
            severity=str(node["severity"]),
            phrases_de=tuple(node["phrases_de"]),
            phrases_en=tuple(node["phrases_en"]),
        )

    return KaiPersona(
        name=str(kai.get("name", "KAI")),
        full_name=str(kai.get("full_name", "KAI — Kinetic Artificial Intelligence")),
        motto=str(kai["motto"]),
        version=str(kai.get("version", "0.0.0")),
        language_default=str(kai.get("language_default", "de")),
        languages_supported=tuple(kai.get("languages_supported", ["de", "en"])),
        state_machine_default=str(sm.get("default_state", "IDLE")),
        state_machine_priority_order=tuple(sm.get("priority_order", [])),
        states=states,
        raw=parsed,
    )


@lru_cache(maxsize=1)
def load_kai_persona(path: Path = _DEFAULT_CONFIG_PATH) -> KaiPersona:
    """Load and validate the persona config. Cached per process."""
    if not path.exists():
        raise KaiPersonaConfigError(f"config not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise KaiPersonaConfigError(f"failed to load {path}: {exc}") from exc

    _validate(parsed)
    persona = _build_persona(parsed)
    logger.info(
        "[kai-persona] loaded version=%s default_lang=%s states=%d",
        persona.version,
        persona.language_default,
        len(persona.states),
    )
    return persona


def reset_persona_cache() -> None:
    """Drop the cached persona — primarily for tests."""
    load_kai_persona.cache_clear()
