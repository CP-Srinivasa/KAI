"""SENTR Security Hardening & Model/Prompt Governance Gates.

Implements Model Registry Gate, Prompt Registry Gate, Agent Permission Boundary,
and Audit validation for productive decisions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.execution.models import DecisionRecord

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_REGISTRY_PATH = REPO_ROOT / "config" / "model_registry.json"
PROMPT_REGISTRY_PATH = REPO_ROOT / "config" / "prompt_registry.json"


# ─── Governance Exceptions ───────────────────────────────────────────────────

class GovernanceError(ValueError):
    """Base exception for all model/prompt registry gate errors."""


class ModelRegistryGateError(GovernanceError):
    """Raised when a model fails to meet the registry requirements."""


class PromptRegistryGateError(GovernanceError):
    """Raised when a prompt fails to meet the registry requirements."""


class PermissionBoundaryViolation(PermissionError):
    """Raised when an agent attempts a forbidden action or accesses a restricted resource."""


class PromptInjectionEscalation(GovernanceError):
    """Raised when a prompt input is flagged for privilege escalation/tool bypass attempt."""


# ─── Registry Loader & Hash Verification ─────────────────────────────────────

def get_registry_hash() -> str:
    """Calculate combined SHA256 signature of model and prompt registry files."""
    h = hashlib.sha256()
    for path in (MODEL_REGISTRY_PATH, PROMPT_REGISTRY_PATH):
        if path.exists():
            h.update(path.read_bytes())
        else:
            h.update(b"")
    return h.hexdigest()


def load_model_registry() -> list[dict[str, Any]]:
    """Load model registry from JSON file."""
    if not MODEL_REGISTRY_PATH.exists():
        logger.warning("[GOVERNANCE] Model registry file missing at %s", MODEL_REGISTRY_PATH)
        return []
    try:
        return json.loads(MODEL_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("[GOVERNANCE] Failed to load model registry: %s", exc)
        return []


def load_prompt_registry() -> list[dict[str, Any]]:
    """Load prompt registry from JSON file."""
    if not PROMPT_REGISTRY_PATH.exists():
        logger.warning("[GOVERNANCE] Prompt registry file missing at %s", PROMPT_REGISTRY_PATH)
        return []
    try:
        return json.loads(PROMPT_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("[GOVERNANCE] Failed to load prompt registry: %s", exc)
        return []


# ─── Validation Gates ────────────────────────────────────────────────────────

def validate_model_gate(model_id: str | None, version: str | None) -> dict[str, Any]:
    """Verify model parameters against registry requirements.

    Requirements:
    - model_id and version present
    - eval_suite_id present
    - approval_status in ["shadow_approved", "production_approved"]
    - risk_rating present
    - owner present
    - last_validation_at present
    """
    if not model_id:
        raise ModelRegistryGateError("model_id is missing")
    if not version:
        raise ModelRegistryGateError("version is missing")

    registry = load_model_registry()
    matched_entry = None
    for entry in registry:
        if entry.get("model_id") == model_id and entry.get("version") == version:
            matched_entry = entry
            break

    if not matched_entry:
        raise ModelRegistryGateError(
            f"Model not registered: model_id={model_id}, version={version}"
        )

    required_fields = [
        "eval_suite_id",
        "approval_status",
        "risk_rating",
        "owner",
        "last_validation_at",
    ]
    for field in required_fields:
        if not matched_entry.get(field):
            raise ModelRegistryGateError(f"Model registry entry is missing mandatory field: {field}")

    approval_status = matched_entry["approval_status"]
    if approval_status not in ("shadow_approved", "production_approved"):
        raise ModelRegistryGateError(
            f"Model approval_status '{approval_status}' is invalid (must be shadow_approved or production_approved)"
        )

    return matched_entry


def validate_prompt_gate(prompt_id: str | None, prompt_version: str | None) -> dict[str, Any]:
    """Verify prompt parameters against registry requirements.

    Requirements:
    - prompt_id and prompt_version present
    - owner_agent present
    - allowed_tools defined
    - forbidden_tools defined
    - output_contract defined
    - prompt_injection_eval_status present
    - approval_status freigegeben (i.e. equals "freigegeben")
    """
    if not prompt_id:
        raise PromptRegistryGateError("prompt_id is missing")
    if not prompt_version:
        raise PromptRegistryGateError("prompt_version is missing")

    registry = load_prompt_registry()
    matched_entry = None
    for entry in registry:
        if entry.get("prompt_id") == prompt_id and entry.get("prompt_version") == prompt_version:
            matched_entry = entry
            break

    if not matched_entry:
        raise PromptRegistryGateError(
            f"Prompt not registered: prompt_id={prompt_id}, prompt_version={prompt_version}"
        )

    required_fields = [
        "owner_agent",
        "output_contract",
        "prompt_injection_eval_status",
        "approval_status",
    ]
    for field in required_fields:
        if not matched_entry.get(field):
            raise PromptRegistryGateError(f"Prompt registry entry is missing mandatory field: {field}")

    # allowed_tools and forbidden_tools must be defined (can be empty lists)
    for tool_field in ("allowed_tools", "forbidden_tools"):
        if tool_field not in matched_entry or not isinstance(matched_entry[tool_field], list):
            raise PromptRegistryGateError(f"Prompt registry entry is missing list field: {tool_field}")

    approval_status = matched_entry["approval_status"]
    if approval_status != "freigegeben":
        raise PromptRegistryGateError(
            f"Prompt approval_status '{approval_status}' is invalid (must be 'freigegeben')"
        )

    return matched_entry


def validate_productive_decision(decision: DecisionRecord) -> None:
    """Enforce model and prompt registry alignment on productive decision records.

    Raises ValueError (or subclasses) on failure.
    """
    # 1. Verify registry references in audit payload
    if not decision.model_id:
        raise GovernanceError("Productive decision missing model_id registry reference")
    if not decision.prompt_id:
        raise GovernanceError("Productive decision missing prompt_id registry reference")
    if not decision.registry_hash:
        raise GovernanceError("Productive decision missing registry_hash reference")

    # 2. Verify registry_hash is fresh/matches the active registries
    expected_hash = get_registry_hash()
    if decision.registry_hash != expected_hash:
        raise GovernanceError(
            f"Audit registry_hash mismatch. Provided: {decision.registry_hash}, Current: {expected_hash}"
        )

    # 3. Verify against Model and Prompt gates
    validate_model_gate(decision.model_id, decision.model_version)
    validate_prompt_gate(decision.prompt_id, decision.prompt_version)


# ─── Agent Permission Boundary ───────────────────────────────────────────────

ALLOWED_AGENT_ACTIONS = {
    "analysieren",
    "warnen",
    "Cancel Signal anfordern",
    "Risk Escalation erzeugen",
}

FORBIDDEN_AGENT_ACTIONS = {
    "Live Keys lesen",
    "Live Orders direkt platzieren",
    "eigene Tools freischalten",
    "Registry-Status ändern",
    "Audit deaktivieren",
}


def check_agent_action(agent_id: str | None, action: str, resource: str | None = None) -> None:
    """Verify that the agent action/resource access conforms to the permission boundary."""
    agent_label = agent_id or "UnknownAgent"

    # Action boundary checks
    if action in FORBIDDEN_AGENT_ACTIONS:
        raise PermissionBoundaryViolation(
            f"Agent {agent_label} attempted forbidden action: {action}"
        )

    if action not in ALLOWED_AGENT_ACTIONS:
        raise PermissionBoundaryViolation(
            f"Agent {agent_label} attempted action outside of permission boundaries: {action}"
        )

    # Resource checks (e.g. Live Keys read attempt)
    if resource:
        resource_lower = resource.strip().lower()
        # Any attempt to read Live keys (Exchange credentials, etc.)
        if "api_key" in resource_lower or "secret" in resource_lower or "private_key" in resource_lower:
            raise PermissionBoundaryViolation(
                f"Agent {agent_label} blocked from reading live secret/key: {resource}"
            )


# ─── Prompt Injection / Escalation Scanner ───────────────────────────────────

_ESCALATION_KEYWORDS = [
    r"unlock\s+tool",
    r"enable\s+tool",
    r"bypass\s+audit",
    r"disable\s+audit",
    r"read\s+live\s+key",
    r"read\s+api\s+key",
    r"change\s+registry",
    r"update\s+registry",
    r"place\s+live\s+order",
]


def scan_for_prompt_injection(text: str) -> None:
    """Scan input text for patterns attempting tool/governance escalation."""
    if not text:
        return

    for pattern in _ESCALATION_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            raise PromptInjectionEscalation(
                f"Prompt injection detected attempting tool escalation/governance bypass (matched pattern: {pattern})"
            )
