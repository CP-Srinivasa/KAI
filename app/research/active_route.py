"""Active route profile state management for runtime A/B/C routing.

Sprint 14C - provides the explicit operator-driven runtime hook (I-84) that makes a
declarative InferenceRouteProfile take effect during analyze-pending runs.

Invariants (I-90-I-93):
  I-90: ActiveRouteStore writes to a dedicated state file only.
        NEVER writes to .env, settings.py, or APP_LLM_PROVIDER (I-80, I-91).
  I-91: route-activate does NOT change APP_LLM_PROVIDER.
        Primary path selection remains the operator's responsibility.
  I-92: analyze-pending with an active shadow route writes primary results to DB only.
        Shadow and control outputs go to audit JSONL only (I-51, I-82).
  I-93: ABCInferenceEnvelope is written per-document to audit JSONL only - no DB writes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_ACTIVE_ROUTE_PATH: Path = Path("artifacts") / "active_route_profile.json"


@dataclass
class ActiveRouteState:
    """Persisted runtime state: which route profile is currently active.

    Written by route-activate, read by analyze-pending.
    Deactivated by route-deactivate (file deletion).
    """

    profile_path: str
    profile_name: str
    route_profile: str
    # primary_only | primary_with_shadow | primary_with_control |
    # primary_with_shadow_and_control
    active_primary_path: str
    enabled_shadow_paths: list[str]
    control_path: str | None
    activated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    abc_envelope_output: str = "artifacts/abc_envelopes/envelopes.jsonl"

    @property
    def has_shadow(self) -> bool:
        """True if any shadow path is enabled."""
        return bool(self.enabled_shadow_paths)

    @property
    def has_control(self) -> bool:
        """True if a control path is configured."""
        return self.control_path is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_path": self.profile_path,
            "profile_name": self.profile_name,
            "route_profile": self.route_profile,
            "active_primary_path": self.active_primary_path,
            "enabled_shadow_paths": self.enabled_shadow_paths,
            "control_path": self.control_path,
            "activated_at": self.activated_at,
            "abc_envelope_output": self.abc_envelope_output,
        }


def activate_route_profile(
    profile_path: Path | str,
    state_path: Path | str = DEFAULT_ACTIVE_ROUTE_PATH,
    abc_envelope_output: Path | str | None = None,
) -> ActiveRouteState:
    """Load an InferenceRouteProfile and persist it as the active route state.

    Does NOT change APP_LLM_PROVIDER (I-90, I-91).
    Raises FileNotFoundError if profile_path does not exist.
    Raises ValueError if the profile has an invalid route_profile value.
    """
    from app.research.inference_profile import (
        VALID_ROUTE_PROFILES,
        load_inference_route_profile,
    )

    p = Path(profile_path)
    if not p.exists():
        raise FileNotFoundError(f"Route profile not found: {p}")

    profile = load_inference_route_profile(p)
    if profile.route_profile not in VALID_ROUTE_PROFILES:
        raise ValueError(
            f"Invalid route_profile: {profile.route_profile!r}. "
            f"Must be one of: {sorted(VALID_ROUTE_PROFILES)}"
        )

    state = ActiveRouteState(
        profile_path=str(p.resolve()),
        profile_name=profile.profile_name,
        route_profile=profile.route_profile,
        active_primary_path=profile.active_primary_path,
        enabled_shadow_paths=list(profile.enabled_shadow_paths),
        control_path=profile.control_path,
        abc_envelope_output=(
            str(abc_envelope_output)
            if abc_envelope_output
            else "artifacts/abc_envelopes/envelopes.jsonl"
        ),
    )

    state_file = Path(state_path)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return state


def load_active_route_state(
    state_path: Path | str = DEFAULT_ACTIVE_ROUTE_PATH,
) -> ActiveRouteState | None:
    """Load the current active route state. Returns None if no profile is active."""
    p = Path(state_path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return ActiveRouteState(
        profile_path=data["profile_path"],
        profile_name=data["profile_name"],
        route_profile=data["route_profile"],
        active_primary_path=data["active_primary_path"],
        enabled_shadow_paths=data.get("enabled_shadow_paths", []),
        control_path=data.get("control_path"),
        activated_at=data.get("activated_at", ""),
        abc_envelope_output=data.get(
            "abc_envelope_output", "artifacts/abc_envelopes/envelopes.jsonl"
        ),
    )


def deactivate_route_profile(
    state_path: Path | str = DEFAULT_ACTIVE_ROUTE_PATH,
) -> bool:
    """Remove the active route state file. Returns True if it existed.

    After deactivation, analyze-pending returns to primary_only behavior.
    """
    p = Path(state_path)
    if p.exists():
        p.unlink()
        return True
    return False
