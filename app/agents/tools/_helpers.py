"""Internal helper functions shared by MCP tool modules.

This module provides path-resolution, write-audit, and report-building
helpers used by canonical_read and guarded_write tool implementations.

Design rules:
- Never import from app.agents.mcp_server (circular-import guard).
- No FastMCP imports -- helpers are framework-agnostic.
- All path helpers enforce workspace / artifacts/ invariants (I-94, I-95).
- Companion-ML subsystem removed: all companion-ML payloads return stubs.
"""

from __future__ import annotations

import datetime
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.core.domain.document import CanonicalDocument
from app.core.settings import get_settings
from app.core.signals import SignalCandidate, extract_signal_candidates
from app.core.watchlists import WatchlistRegistry
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workspace constants
# ---------------------------------------------------------------------------

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS_SUBDIR = "artifacts"
JSON_SUFFIXES = frozenset({".json"})
ARTIFACT_SUFFIXES = frozenset({".json", ".jsonl"})
HANDOFF_ACK_DEFAULT_PATH = "artifacts/handoff_acknowledgements.jsonl"
ALERT_AUDIT_DEFAULT_DIR = ARTIFACTS_SUBDIR
REVIEW_JOURNAL_DEFAULT_PATH = "artifacts/operator_review_journal.jsonl"
PAPER_EXECUTION_AUDIT_DEFAULT_PATH = "artifacts/paper_execution_audit.jsonl"
DECISION_JOURNAL_DEFAULT_PATH = "artifacts/decision_journal.jsonl"
LOOP_AUDIT_DEFAULT_PATH = "artifacts/trading_loop_audit.jsonl"

_COMPANION_ML_STUB: dict[str, object] = {
    "status": "companion_ml_removed",
    "execution_enabled": False,
    "write_back_allowed": False,
    "available": False,
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def resolve_workspace_path(
    path_value: str | Path,
    *,
    label: str,
    must_exist: bool = False,
    allowed_suffixes: frozenset[str] = ARTIFACT_SUFFIXES,
) -> Path:
    candidate = Path(path_value)
    resolved = (candidate if candidate.is_absolute() else WORKSPACE_ROOT / candidate).resolve()
    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as err:
        raise ValueError(f"{label} must stay within workspace: {path_value}") from err

    if resolved.suffix.lower() not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ValueError(f"{label} must use one of: {allowed}")

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"{label} not found: {resolved}")

    return resolved


def require_artifacts_subpath(resolved: Path, *, label: str) -> Path:
    """Ensure resolved path is inside workspace/artifacts/ (I-95: write guard)."""
    artifacts_root = WORKSPACE_ROOT / ARTIFACTS_SUBDIR
    try:
        resolved.relative_to(artifacts_root)
    except ValueError as err:
        raise ValueError(f"{label} must be within workspace/artifacts/: {resolved}") from err
    return resolved


def resolve_workspace_dir(
    path_value: str | Path,
    *,
    label: str,
    must_exist: bool = False,
) -> Path:
    candidate = Path(path_value)
    resolved = (candidate if candidate.is_absolute() else WORKSPACE_ROOT / candidate).resolve()
    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as err:
        raise ValueError(f"{label} must stay within workspace: {path_value}") from err

    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"{label} must be a directory: {resolved}")

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"{label} not found: {resolved}")

    return resolved


# ---------------------------------------------------------------------------
# Write audit (I-94)
# ---------------------------------------------------------------------------


def append_mcp_write_audit(
    *,
    tool: str,
    params: dict[str, object],
    result_summary: str,
) -> None:
    """Append a write audit entry to artifacts/mcp_write_audit.jsonl (I-94).

    Never raises -- a failing audit must not suppress the original result.
    """
    audit_path = WORKSPACE_ROOT / ARTIFACTS_SUBDIR / "mcp_write_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.datetime.now(tz=datetime.UTC).isoformat(),
        "tool": tool,
        "params": params,
        "result_summary": result_summary,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Companion-ML stubs (subsystem removed)
# ---------------------------------------------------------------------------


def build_handoff_collector_report(
    *,
    handoff_path: str | Path,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
) -> tuple[dict[str, object], Path, Path]:
    """Stub: companion-ML subsystem removed."""
    resolved_handoff = resolve_workspace_path(handoff_path, label="Signal handoff input")
    resolved_ack = resolve_workspace_path(
        acknowledgement_path,
        label="Handoff acknowledgement audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    payload: dict[str, object] = {
        **_COMPANION_ML_STUB,
        "report_type": "handoff_collector_summary",
        "handoff_path": str(resolved_handoff),
        "acknowledgement_path": str(resolved_ack),
        "total_handoffs": 0,
        "pending": 0,
        "acknowledged": 0,
        "handoffs": [],
    }
    return payload, resolved_handoff, resolved_ack


def build_operational_readiness_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {
        **_COMPANION_ML_STUB,
        "report_type": "operational_readiness_summary",
        "readiness_status": "companion_ml_removed",
        "issues": [],
    }


def build_provider_health_payload(
    *,
    handoff_path: str | Path | None = None,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "provider_health_summary"}


def build_distribution_drift_payload(
    *,
    handoff_path: str | Path | None = None,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "distribution_drift_summary"}


def build_protective_gate_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "protective_gate_summary", "items": []}


def build_remediation_recommendation_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {
        **_COMPANION_ML_STUB,
        "report_type": "remediation_recommendation_report",
        "recommendations": [],
    }


def build_escalation_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {
        **_COMPANION_ML_STUB,
        "report_type": "operational_escalation_summary",
        "escalation_status": "nominal",
        "items": [],
    }


def build_blocking_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "blocking_summary", "blocking_items": []}


def build_operator_action_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "operator_action_summary", "actions": []}


def build_action_queue_summary_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "action_queue_summary", "actions": []}


def build_blocking_actions_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "blocking_actions", "actions": []}


def build_prioritized_actions_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "prioritized_actions", "actions": []}


def build_review_required_actions_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "review_required_actions", "actions": []}


def build_operator_decision_pack_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "operator_decision_pack"}


def build_operator_runbook_payload(
    *,
    handoff_path: str | Path | None = None,
    acknowledgement_path: str | Path = HANDOFF_ACK_DEFAULT_PATH,
    state_path: str | Path = "artifacts/active_route_state.json",
    abc_output_path: str | Path | None = None,
    alert_audit_dir: str | Path = ALERT_AUDIT_DEFAULT_DIR,
    stale_after_hours: int = 24,
    artifacts_dir: str | Path = ARTIFACTS_SUBDIR,
    retention_stale_after_days: float = 30.0,
) -> dict[str, object]:
    """Stub: companion-ML subsystem removed."""
    return {**_COMPANION_ML_STUB, "report_type": "operator_runbook", "commands": []}


def build_review_journal_summary_payload(
    *,
    journal_path: str | Path = REVIEW_JOURNAL_DEFAULT_PATH,
) -> tuple[dict[str, object], Path]:
    """Stub: companion-ML subsystem removed."""
    resolved = resolve_workspace_path(
        journal_path,
        label="Review journal path",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    payload: dict[str, object] = {
        **_COMPANION_ML_STUB,
        "report_type": "review_journal_summary",
        "journal_path": str(resolved),
        "entries": [],
    }
    return payload, resolved


# ---------------------------------------------------------------------------
# Signal candidates loader (shared by several read tools)
# ---------------------------------------------------------------------------


async def load_signal_candidates_and_documents(
    *,
    watchlist: str | None,
    min_priority: int,
    limit: int,
    provider: str | None = None,
) -> tuple[list[SignalCandidate], list[CanonicalDocument]]:
    settings = get_settings()
    registry = WatchlistRegistry.from_monitor_dir(Path(settings.monitor_dir))

    watchlist_boosts = None
    if watchlist:
        items = registry.get_watchlist(watchlist, item_type="assets")
        if items:
            watchlist_boosts = dict.fromkeys(items, 1)

    session_factory = build_session_factory(settings.db)
    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        docs = await repo.list(is_analyzed=True, limit=limit * 5)

    if provider:
        normalized_provider = provider.strip().lower()
        docs = [
            document
            for document in docs
            if (document.provider or "").strip().lower() == normalized_provider
        ]

    candidates = extract_signal_candidates(
        docs,
        min_priority=min_priority,
        watchlist_boosts=watchlist_boosts,
    )
    return candidates[:limit], docs


# ---------------------------------------------------------------------------
# Paper portfolio helper
# ---------------------------------------------------------------------------


async def build_paper_portfolio_snapshot_helper(
    *,
    audit_path: str = PAPER_EXECUTION_AUDIT_DEFAULT_PATH,
    provider: str = "coingecko",
    freshness_threshold_seconds: float = 120.0,
    timeout_seconds: int = 10,
) -> Any:
    from app.execution.portfolio_read import build_portfolio_snapshot

    resolved = resolve_workspace_path(
        audit_path,
        label="Paper execution audit",
        allowed_suffixes=frozenset({".jsonl"}),
    )
    return await build_portfolio_snapshot(
        audit_path=resolved,
        provider=provider,
        freshness_threshold_seconds=freshness_threshold_seconds,
        timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# Daily operator summary helper
# ---------------------------------------------------------------------------


async def safe_daily_surface_load(
    *,
    source_name: str,
    loader: Callable[[], Awaitable[dict[str, object]]],
) -> dict[str, object] | None:
    try:
        payload = await loader()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "daily_operator_summary degraded: %s unavailable (%s)",
            source_name,
            exc.__class__.__name__,
        )
        return None
    if not isinstance(payload, dict):
        logger.warning(
            "daily_operator_summary degraded: %s returned non-dict payload",
            source_name,
        )
        return None
    return payload
