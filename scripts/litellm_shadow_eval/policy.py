"""Pre-registered graduation policy and externally supplied proof flags."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.litellm_shadow_eval.models import (
    RUNTIME_GATE_FLAGS,
    RUNTIME_PROOF_FLAGS,
    GraduationPolicy,
    RuntimeEvidenceFlags,
    RuntimeProof,
    is_exclusion_code,
)

#: Quoten und Grenzen, die in [0,1] liegen muessen.
_RATE_FIELDS = (
    "minimum_success_rate",
    "minimum_schema_valid_rate",
    "minimum_quality_coverage",
    "maximum_quality_regression",
    "maximum_unexplained_incomplete_rate",
)
#: Ganzzahlen >= 1 oder `None` (= "folgt der Stichprobe" bzw. "keine Grenze").
_OPTIONAL_POSITIVE_INT_FIELDS = ("minimum_quality_sample_count", "maximum_runtime_proof_age_days")
_NON_BOOLEAN_FIELDS = frozenset(
    {
        "minimum_sample_count",
        *_RATE_FIELDS,
        *_OPTIONAL_POSITIVE_INT_FIELDS,
        "allowed_exclusion_reasons",
        "route_overrides",
    }
)
_PROOF_KEYS = frozenset({"proven", "artifact", "artifact_sha256", "proven_at", "version"})
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


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


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _exclusion_codes(value: object) -> tuple[str, ...]:
    """Sortiert und dedupliziert: die Reihenfolge ist keine Aussage, der Hash bleibt."""
    if not isinstance(value, (list, tuple)) or not all(is_exclusion_code(v) for v in value):
        raise PolicyError("allowed_exclusion_reasons must be a list of reason codes")
    return tuple(sorted(set(value)))


def policy_from_dict(raw: dict[str, Any]) -> GraduationPolicy:
    allowed = {item.name for item in fields(GraduationPolicy)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise PolicyError(f"unknown policy fields: {unknown}")
    values = dict(raw)
    if "allowed_exclusion_reasons" in values:
        values["allowed_exclusion_reasons"] = _exclusion_codes(values["allowed_exclusion_reasons"])
    try:
        policy = GraduationPolicy(**values)
    except TypeError as exc:
        raise PolicyError(str(exc)) from exc
    if not _is_positive_int(policy.minimum_sample_count):
        raise PolicyError("minimum_sample_count must be a positive integer")
    for name in _RATE_FIELDS:
        value = getattr(policy, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise PolicyError(f"{name} must be within [0,1]")
    for name in _OPTIONAL_POSITIVE_INT_FIELDS:
        value = getattr(policy, name)
        if value is not None and not _is_positive_int(value):
            raise PolicyError(f"{name} must be null or a positive integer")
    boolean_fields = [item.name for item in fields(GraduationPolicy)]
    if any(
        not isinstance(getattr(policy, name), bool)
        for name in boolean_fields
        if name not in _NON_BOOLEAN_FIELDS
    ):
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


def _proof_text(flag: str, key: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{flag}.{key} must be a non-empty string")
    return value.strip()


def _runtime_proof(flag: str, raw: dict[str, Any]) -> RuntimeProof:
    """Ein Nachweisobjekt -- vollstaendig oder gar nicht.

    Ein halbes Objekt ist kein schwacher Beleg, sondern eine kaputte Angabe:
    es wird abgelehnt (Exit 2), nicht stillschweigend zu "unbelegt" herabgestuft.
    """
    unknown = sorted(set(raw) - _PROOF_KEYS)
    if unknown:
        raise PolicyError(f"unknown proof fields for {flag}: {unknown}")
    proven = raw.get("proven")
    if not isinstance(proven, bool):
        raise PolicyError(f"{flag}.proven must be a boolean")
    present = {key: value for key, value in raw.items() if key != "proven" and value is not None}
    metadata: dict[str, Any] = {}
    for key in ("artifact", "version"):
        if key in present:
            metadata[key] = _proof_text(flag, key, present[key])
    if "artifact_sha256" in present:
        digest = _proof_text(flag, "artifact_sha256", present["artifact_sha256"]).lower()
        if not _SHA256_HEX.fullmatch(digest):
            raise PolicyError(f"{flag}.artifact_sha256 must be 64 hex characters")
        metadata["artifact_sha256"] = digest
    if "proven_at" in present:
        text = _proof_text(flag, "proven_at", present["proven_at"])
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise PolicyError(f"{flag}.proven_at must be ISO-8601") from exc
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise PolicyError(f"{flag}.proven_at must carry a timezone")
        metadata["proven_at"] = moment.astimezone(UTC).isoformat()
    if proven and set(metadata) != _PROOF_KEYS - {"proven"}:
        raise PolicyError(
            f"{flag}: proven=true requires artifact, artifact_sha256, proven_at and version"
        )
    return RuntimeProof(proven=proven, **metadata)


def runtime_flags_from_dict(raw: dict[str, Any]) -> RuntimeEvidenceFlags:
    """Laufzeitbelege lesen: ``*_proven`` als Boolean ODER als Nachweisobjekt.

    Objektform::

        {"proven": true, "artifact": "<pfad oder url>", "artifact_sha256": "<64 hex>",
         "proven_at": "<ISO-8601 mit Zeitzone>", "version": "<git sha oder release>"}

    Ein nacktes ``true`` wird weiter angenommen -- ob es als Beleg GILT,
    entscheidet die Politik (``require_referenced_runtime_evidence``), nicht
    der Parser. Die ``*_gate_changed``-Schalter bleiben reine Booleans.
    """
    allowed = set(RUNTIME_PROOF_FLAGS) | set(RUNTIME_GATE_FLAGS)
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise PolicyError(f"unknown runtime-evidence fields: {unknown}")
    flags: dict[str, Any] = {}
    proofs: dict[str, RuntimeProof] = {}
    for name, value in raw.items():
        if isinstance(value, bool):
            flags[name] = value
        elif isinstance(value, dict) and name in RUNTIME_PROOF_FLAGS:
            proof = _runtime_proof(name, value)
            flags[name] = proof.proven
            proofs[name] = proof
        else:
            raise PolicyError(
                "runtime-evidence flags must be booleans (or proof objects for *_proven)"
            )
    return RuntimeEvidenceFlags(**flags, proofs=proofs)


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
