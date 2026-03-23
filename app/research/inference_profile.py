"""Declarative inference route profiles for A/B/C path separation.

Production default: primary_only (single external LLM path, no shadow/control).
The active route profile file (artifacts/active_route_profile.json) controls
which mode is active at runtime. Absence of the file = primary_only behavior.

Multi-path modes (primary_with_shadow, primary_with_control,
primary_with_shadow_and_control) are EXPERIMENTAL and require:
- A deployed companion model at COMPANION_MODEL_ENDPOINT
- Explicit operator activation via route-activate MCP tool

Creating or saving a profile MUST NOT change APP_LLM_PROVIDER, DB state,
or CLI defaults (I-80, I-84, I-89).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DistributionTarget:
    """Explicit channel target for audit-safe signal distribution."""

    channel: str
    # research_brief | signal_candidates | shadow_audit_jsonl |
    # comparison_report_json | upgrade_cycle_report_json | promotion_audit_json
    include_paths: list[str]  # subset of ["A", "B", "C", "comparison"]
    mode: str  # primary_only | audit_only | comparison_only | audit_appendix
    artifact_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "include_paths": self.include_paths,
            "mode": self.mode,
            "artifact_path": self.artifact_path,
        }


@dataclass
class InferenceRouteProfile:
    """Declarative route profile defining allowed A/B/C path layout.

    This is a pure configuration artifact. It does not activate routing,
    change providers, or modify any DB state (I-89).
    """

    profile_name: str
    route_profile: str
    # primary_only | primary_with_shadow | primary_with_control |
    # primary_with_shadow_and_control
    active_primary_path: str  # e.g. "A.external_llm"
    enabled_shadow_paths: list[str]  # e.g. ["B.companion"] or []
    control_path: str | None = None  # "C.rule" or None
    distribution_targets: list[DistributionTarget] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "inference_route_profile",
            "profile_name": self.profile_name,
            "route_profile": self.route_profile,
            "active_primary_path": self.active_primary_path,
            "enabled_shadow_paths": self.enabled_shadow_paths,
            "control_path": self.control_path,
            "distribution_targets": [t.to_dict() for t in self.distribution_targets],
            "notes": self.notes,
        }


VALID_ROUTE_PROFILES: frozenset[str] = frozenset(
    {
        "primary_only",
        "primary_with_shadow",
        "primary_with_control",
        "primary_with_shadow_and_control",
    }
)


def _validate_route_profile(profile: InferenceRouteProfile) -> None:
    if profile.route_profile not in VALID_ROUTE_PROFILES:
        raise ValueError(
            f"Invalid route_profile: {profile.route_profile!r}. "
            f"Must be one of: {sorted(VALID_ROUTE_PROFILES)}"
        )

    if not profile.active_primary_path.startswith("A."):
        raise ValueError("active_primary_path must start with 'A.'.")

    if any(not path.startswith("B.") for path in profile.enabled_shadow_paths):
        raise ValueError("enabled_shadow_paths must contain only 'B.' paths.")

    if profile.control_path not in {None, "C.rule"}:
        raise ValueError("control_path must be 'C.rule' or None.")

    if profile.route_profile == "primary_only":
        if profile.enabled_shadow_paths or profile.control_path is not None:
            raise ValueError("primary_only profiles cannot define shadow or control paths.")
        return

    if profile.route_profile == "primary_with_shadow":
        if not profile.enabled_shadow_paths or profile.control_path is not None:
            raise ValueError(
                "primary_with_shadow requires shadow paths and forbids a control path."
            )
        return

    if profile.route_profile == "primary_with_control":
        if profile.enabled_shadow_paths or profile.control_path != "C.rule":
            raise ValueError(
                "primary_with_control requires control_path='C.rule' and no shadow paths."
            )
        return

    if not profile.enabled_shadow_paths or profile.control_path != "C.rule":
        raise ValueError(
            "primary_with_shadow_and_control requires shadow paths and control_path='C.rule'."
        )


def save_inference_route_profile(
    profile: InferenceRouteProfile,
    output_path: Path | str,
) -> Path:
    """Save a route profile to JSON. Does NOT change any routing state (I-80, I-89).

    Raises ValueError if route_profile is not a valid value.
    """
    _validate_route_profile(profile)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(profile.to_json_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return out


def load_inference_route_profile(path: Path | str) -> InferenceRouteProfile:
    """Load and reconstruct a saved route profile. Read-only, no side effects."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    profile = InferenceRouteProfile(
        profile_name=data["profile_name"],
        route_profile=data["route_profile"],
        active_primary_path=data["active_primary_path"],
        enabled_shadow_paths=data.get("enabled_shadow_paths", []),
        control_path=data.get("control_path"),
        distribution_targets=[
            DistributionTarget(
                channel=t["channel"],
                include_paths=t["include_paths"],
                mode=t["mode"],
                artifact_path=t.get("artifact_path"),
            )
            for t in data.get("distribution_targets", [])
        ],
        notes=data.get("notes", []),
    )
    _validate_route_profile(profile)
    return profile
