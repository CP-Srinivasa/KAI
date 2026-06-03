"""Read-only signal-detail aggregator (operator GET /operator/signals/{id}).

Joins the decision journal (the canonical per-signal record, keyed by
``decision_id``) with any linked paper-execution audit event. Pure + IO-thin:
``load_decision_journal`` does the file read and fail-closes on malformed rows.

Honesty contract (Goal 2026-06-03): missing fields are surfaced as ``null`` with
an explicit ``*_status: "not_available"`` — never a fabricated default (no 0.85
placeholder that could later be mistaken for real signal quality). No trading
side effects, no execution, never touches entry_mode/risk gates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.orchestrator.decision_journal import load_decision_journal

DEFAULT_JOURNAL_PATH = "artifacts/decision_journal.jsonl"
DEFAULT_AUDIT_PATH = "artifacts/paper_execution_audit.jsonl"


def _g(rec: dict[str, Any], key: str) -> Any:
    v = rec.get(key)
    return v if v not in ("", None) else None


def _find_linked_execution(audit_path: str | Path, decision_id: str) -> dict[str, Any] | None:
    """First paper-execution audit event whose decision_id matches. Read-only,
    fail-soft: a missing/malformed audit file never breaks the signal lookup."""
    path = Path(audit_path)
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("decision_id") == decision_id:
            return {
                "event_type": ev.get("event_type"),
                "order_id": ev.get("order_id"),
                "side": ev.get("side"),
                "symbol": ev.get("symbol"),
                "timestamp_utc": ev.get("timestamp_utc") or ev.get("timestamp"),
            }
    return None


def _resolve_side(rec: dict[str, Any], linked: dict[str, Any] | None) -> tuple[Any, str]:
    """Side is not a first-class decision-journal field. Prefer a linked
    execution's side, then an explicit record field; otherwise honestly unknown."""
    for source in (linked or {}, rec):
        for key in ("side", "direction", "order_side", "position_side"):
            val = source.get(key) if isinstance(source, dict) else None
            if isinstance(val, str) and val.strip():
                return val.strip().lower(), "available"
    return None, "not_available"


def build_signal_detail(
    decision_id: str,
    *,
    journal_path: str | Path = DEFAULT_JOURNAL_PATH,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
) -> dict[str, Any] | None:
    """Return the joined signal-detail dict, or ``None`` if no such decision_id
    exists (caller maps None -> 404). Raises only on a malformed journal (fail
    closed), which the caller maps to 503."""
    journal = [dict(r) for r in load_decision_journal(journal_path)]
    rec = next((r for r in journal if r.get("decision_id") == decision_id), None)
    if rec is None:
        return None

    linked = _find_linked_execution(audit_path, decision_id)
    side, side_status = _resolve_side(rec, linked)
    confidence = _g(rec, "confidence_score")
    conf_status = "available" if isinstance(confidence, (int, float)) else "not_available"
    gate_decision = _g(rec, "gate_decision")
    gate_status = "available" if gate_decision else "not_available"
    reason_codes_val = rec.get("reason_codes")
    reason_codes = reason_codes_val if isinstance(reason_codes_val, list) else []

    return {
        "report_type": "operator_signal_detail",
        "signal_id": decision_id,
        "source": _g(rec, "mode") or _g(rec, "venue"),
        "created_at": _g(rec, "timestamp_utc"),
        "symbol": _g(rec, "symbol"),
        "market": _g(rec, "market"),
        "side": side,
        "side_status": side_status,
        "status": _g(rec, "execution_state") or _g(rec, "approval_state"),
        "approval_state": _g(rec, "approval_state"),
        "execution_state": _g(rec, "execution_state"),
        "confidence": confidence,
        "confidence_status": conf_status,
        "market_regime": _g(rec, "market_regime"),
        "volatility_state": _g(rec, "volatility_state"),
        "liquidity_state": _g(rec, "liquidity_state"),
        "risk_geometry": {
            "stop_loss": _g(rec, "stop_loss"),
            "take_profit": _g(rec, "take_profit"),
            "max_loss_estimate": _g(rec, "max_loss_estimate"),
        },
        "risk_assessment": _g(rec, "risk_assessment"),
        "gate_decision": gate_decision,
        "gate_decision_status": gate_status,
        "reason_codes": reason_codes,
        "explain_summary": _g(rec, "thesis"),
        "linked_execution": linked,
        "raw_source_ref": _g(rec, "document_id") or _g(rec, "model_version"),
    }


def build_signal_explain(
    decision_id: str,
    *,
    journal_path: str | Path = DEFAULT_JOURNAL_PATH,
) -> dict[str, Any] | None:
    """Decision-path / explainability view for the same decision_id. ``None`` ->
    404. Read-only over the decision journal."""
    journal = [dict(r) for r in load_decision_journal(journal_path)]
    rec = next((r for r in journal if r.get("decision_id") == decision_id), None)
    if rec is None:
        return None

    caveats: list[str] = []
    if not isinstance(_g(rec, "confidence_score"), (int, float)):
        caveats.append("confidence_not_available")
    if not _g(rec, "gate_decision"):
        caveats.append("gate_decision_not_recorded")

    return {
        "report_type": "operator_signal_explain",
        "signal_id": decision_id,
        "thesis": _g(rec, "thesis"),
        "entry_logic": _g(rec, "entry_logic"),
        "exit_logic": _g(rec, "exit_logic"),
        "invalidation_condition": _g(rec, "invalidation_condition"),
        "supporting_factors": rec.get("supporting_factors")
        if isinstance(rec.get("supporting_factors"), list)
        else [],
        "contradictory_factors": rec.get("contradictory_factors")
        if isinstance(rec.get("contradictory_factors"), list)
        else [],
        "position_size_rationale": _g(rec, "position_size_rationale"),
        "risk_assessment": _g(rec, "risk_assessment"),
        "data_sources_used": rec.get("data_sources_used")
        if isinstance(rec.get("data_sources_used"), list)
        else [],
        "model_version": _g(rec, "model_version"),
        "prompt_version": _g(rec, "prompt_version"),
        "caveats": caveats,
    }
