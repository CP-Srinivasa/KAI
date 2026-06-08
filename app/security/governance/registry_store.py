"""Governance registry persistence — model/prompt registries + decision audit.

Issue #165: the gate primitives from PR #164 are pure/standalone. To wire them
into the productive decision path they need a *persisted* registry to resolve a
decision's model/prompt identity into a full :class:`ModelRegistryEntry` /
:class:`PromptRegistryEntry`, plus a place to persist the resulting
:class:`DecisionRegistryReference`.

Storage: append-only JSONL under ``artifacts/governance/``.

Non-negotiable invariants (Issue #165)
--------------------------------------
- **Agents get no mutation right.** The ``save_*`` writers here are
  operator/CLI-only. They are deliberately NOT imported by any agent tool, and
  the ``mutate_registry`` capability stays in the forbidden set
  (:data:`~app.security.governance.models.FORBIDDEN_AGENT_CAPABILITIES`).
- **Read paths are fail-closed.** A malformed/partial row is skipped with a
  warning; a missing registry yields an empty mapping (→ unknown model/prompt →
  the gate refuses, never silently approves).
- This module performs **no** secret access and does not touch ``entry_mode``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.core.file_lock import append_lock
from app.security.governance.models import (
    DecisionRegistryReference,
    ModelRegistryEntry,
    PromptRegistryEntry,
)

logger = logging.getLogger(__name__)

GOVERNANCE_DIR = Path("artifacts/governance")
MODEL_REGISTRY_PATH = GOVERNANCE_DIR / "model_registry.jsonl"
PROMPT_REGISTRY_PATH = GOVERNANCE_DIR / "prompt_registry.jsonl"
DECISION_GOVERNANCE_AUDIT_PATH = GOVERNANCE_DIR / "decision_governance_audit.jsonl"


# --------------------------------------------------------------------------- #
# (de)serialisation
# --------------------------------------------------------------------------- #


def model_entry_to_dict(entry: ModelRegistryEntry) -> dict[str, Any]:
    return {
        "model_id": entry.model_id,
        "version": entry.version,
        "eval_suite_id": entry.eval_suite_id,
        "approval_status": entry.approval_status,
        "risk_rating": entry.risk_rating,
        "owner": entry.owner,
        "last_validation_at": entry.last_validation_at,
    }


def model_entry_from_dict(payload: dict[str, Any]) -> ModelRegistryEntry:
    return ModelRegistryEntry(
        model_id=payload.get("model_id"),
        version=payload.get("version"),
        eval_suite_id=payload.get("eval_suite_id"),
        approval_status=payload.get("approval_status"),
        risk_rating=payload.get("risk_rating"),
        owner=payload.get("owner"),
        last_validation_at=payload.get("last_validation_at"),
    )


def prompt_entry_to_dict(entry: PromptRegistryEntry) -> dict[str, Any]:
    return {
        "prompt_id": entry.prompt_id,
        "prompt_version": entry.prompt_version,
        "owner_agent": entry.owner_agent,
        "allowed_tools": list(entry.allowed_tools) if entry.allowed_tools is not None else None,
        "forbidden_tools": (
            list(entry.forbidden_tools) if entry.forbidden_tools is not None else None
        ),
        "output_contract": entry.output_contract,
        "prompt_injection_eval_status": entry.prompt_injection_eval_status,
        "approval_status": entry.approval_status,
    }


def prompt_entry_from_dict(payload: dict[str, Any]) -> PromptRegistryEntry:
    allowed = payload.get("allowed_tools")
    forbidden = payload.get("forbidden_tools")
    return PromptRegistryEntry(
        prompt_id=payload.get("prompt_id"),
        prompt_version=payload.get("prompt_version"),
        owner_agent=payload.get("owner_agent"),
        allowed_tools=tuple(allowed) if isinstance(allowed, list) else None,
        forbidden_tools=tuple(forbidden) if isinstance(forbidden, list) else None,
        output_contract=payload.get("output_contract"),
        prompt_injection_eval_status=payload.get("prompt_injection_eval_status"),
        approval_status=payload.get("approval_status"),
    )


# --------------------------------------------------------------------------- #
# loaders (fail-closed; last-write-wins per key so an entry can be re-approved)
# --------------------------------------------------------------------------- #


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning(
                "governance_registry_malformed_row: path=%s line=%s %s", path, line_no, exc
            )
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def load_model_registry(
    path: Path | str = MODEL_REGISTRY_PATH,
) -> dict[tuple[str, str], ModelRegistryEntry]:
    """Load the model registry keyed by ``(model_id, version)``. Later rows
    override earlier ones (append-only re-approval). Rows missing an id/version
    are skipped — an unkeyable entry can never resolve a decision."""
    out: dict[tuple[str, str], ModelRegistryEntry] = {}
    for row in _read_jsonl(Path(path)):
        entry = model_entry_from_dict(row)
        if entry.model_id and entry.version:
            out[(entry.model_id, entry.version)] = entry
    return out


def load_prompt_registry(
    path: Path | str = PROMPT_REGISTRY_PATH,
) -> dict[tuple[str, str], PromptRegistryEntry]:
    """Load the prompt registry keyed by ``(prompt_id, prompt_version)``."""
    out: dict[tuple[str, str], PromptRegistryEntry] = {}
    for row in _read_jsonl(Path(path)):
        entry = prompt_entry_from_dict(row)
        if entry.prompt_id and entry.prompt_version:
            out[(entry.prompt_id, entry.prompt_version)] = entry
    return out


def lookup_model(
    registry: dict[tuple[str, str], ModelRegistryEntry], model_id: str, version: str
) -> ModelRegistryEntry | None:
    return registry.get((model_id, version))


def lookup_prompt(
    registry: dict[tuple[str, str], PromptRegistryEntry], prompt_id: str, version: str
) -> PromptRegistryEntry | None:
    return registry.get((prompt_id, version))


# --------------------------------------------------------------------------- #
# writers — OPERATOR/CLI ONLY. Never import these from an agent tool.
# --------------------------------------------------------------------------- #


def save_model_registry_entry(
    entry: ModelRegistryEntry, path: Path | str = MODEL_REGISTRY_PATH
) -> Path:
    """Append a model registry entry. Operator/CLI only — agents are forbidden
    the ``mutate_registry`` capability and have no import path to this writer."""
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with append_lock(resolved):
        with resolved.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(model_entry_to_dict(entry)) + "\n")
    return resolved


def save_prompt_registry_entry(
    entry: PromptRegistryEntry, path: Path | str = PROMPT_REGISTRY_PATH
) -> Path:
    """Append a prompt registry entry. Operator/CLI only (see above)."""
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with append_lock(resolved):
        with resolved.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(prompt_entry_to_dict(entry)) + "\n")
    return resolved


# --------------------------------------------------------------------------- #
# decision governance audit sidecar
# --------------------------------------------------------------------------- #


def append_decision_governance_audit(
    *,
    decision_id: str,
    reference: DecisionRegistryReference,
    authorized: bool,
    blocker_codes: list[str],
    timestamp_utc: str,
    path: Path | str = DECISION_GOVERNANCE_AUDIT_PATH,
) -> Path:
    """Persist the registry reference for a governed decision alongside the
    journal (keyed by ``decision_id``). Additive — does not mutate the canonical
    decision record schema."""
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "decision_id": decision_id,
        "authorized": authorized,
        "blocker_codes": sorted(blocker_codes),
        "registry_reference": reference.to_dict(),
        "timestamp_utc": timestamp_utc,
    }
    with append_lock(resolved):
        with resolved.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    return resolved


def load_decision_governance_audit(
    path: Path | str = DECISION_GOVERNANCE_AUDIT_PATH,
) -> list[dict[str, Any]]:
    """Load the governance audit sidecar (fail-closed on malformed rows)."""
    return _read_jsonl(Path(path))


__all__ = [
    "DECISION_GOVERNANCE_AUDIT_PATH",
    "GOVERNANCE_DIR",
    "MODEL_REGISTRY_PATH",
    "PROMPT_REGISTRY_PATH",
    "append_decision_governance_audit",
    "load_decision_governance_audit",
    "load_model_registry",
    "load_prompt_registry",
    "lookup_model",
    "lookup_prompt",
    "model_entry_from_dict",
    "model_entry_to_dict",
    "prompt_entry_from_dict",
    "prompt_entry_to_dict",
    "save_model_registry_entry",
    "save_prompt_registry_entry",
]
