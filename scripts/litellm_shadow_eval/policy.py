"""Pre-registered graduation policy and externally supplied proof flags."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from scripts.litellm_shadow_eval.models import GraduationPolicy, RuntimeEvidenceFlags


class PolicyError(ValueError):
    """Policy or runtime-evidence configuration is invalid."""


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read {path}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise PolicyError(f"{path} must contain a JSON object")
    return value


def policy_from_dict(raw: dict[str, Any]) -> GraduationPolicy:
    allowed = {item.name for item in fields(GraduationPolicy)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise PolicyError(f"unknown policy fields: {unknown}")
    try:
        policy = GraduationPolicy(**raw)
    except TypeError as exc:
        raise PolicyError(str(exc)) from exc
    if isinstance(policy.minimum_sample_count, bool) or policy.minimum_sample_count < 1:
        raise PolicyError("minimum_sample_count must be a positive integer")
    for name in ("minimum_success_rate", "minimum_schema_valid_rate"):
        value = getattr(policy, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise PolicyError(f"{name} must be within [0,1]")
    boolean_fields = [
        item.name
        for item in fields(GraduationPolicy)
        if item.name
        not in {
            "minimum_sample_count",
            "minimum_success_rate",
            "minimum_schema_valid_rate",
            "route_overrides",
        }
    ]
    if any(not isinstance(getattr(policy, name), bool) for name in boolean_fields):
        raise PolicyError("policy gates must be booleans")
    if not isinstance(policy.route_overrides, dict):
        raise PolicyError("route_overrides must be an object")
    override_allowed = allowed - {"route_overrides"}
    for route, override in policy.route_overrides.items():
        if not isinstance(route, str) or not route or not isinstance(override, dict):
            raise PolicyError("each route override must be a named object")
        if set(override) - override_allowed:
            raise PolicyError(f"unknown override fields for {route}")
        policy_from_dict({**asdict(policy), "route_overrides": {}, **override})
    return policy


def load_policy(path: Path) -> GraduationPolicy:
    return policy_from_dict(_json_object(path))


def effective_policy(policy: GraduationPolicy, route: str) -> GraduationPolicy:
    override = policy.route_overrides.get(route, {})
    values = asdict(policy)
    values.update(override)
    values["route_overrides"] = {}
    return policy_from_dict(values)


def policy_hash(policy: GraduationPolicy) -> str:
    canonical = json.dumps(asdict(policy), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def runtime_flags_from_dict(raw: dict[str, Any]) -> RuntimeEvidenceFlags:
    allowed = {item.name for item in fields(RuntimeEvidenceFlags)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise PolicyError(f"unknown runtime-evidence fields: {unknown}")
    if any(not isinstance(value, bool) for value in raw.values()):
        raise PolicyError("runtime-evidence flags must be booleans")
    return RuntimeEvidenceFlags(**raw)


def load_runtime_flags(path: Path) -> RuntimeEvidenceFlags:
    return runtime_flags_from_dict(_json_object(path))


__all__ = [
    "PolicyError",
    "effective_policy",
    "load_policy",
    "load_runtime_flags",
    "policy_from_dict",
    "policy_hash",
    "runtime_flags_from_dict",
]
