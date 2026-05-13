"""Read-only signal execution status from bridge, watcher and paper audits."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _cid(row: dict[str, Any]) -> str:
    value = row.get("correlation_id") or row.get("envelope_id") or ""
    return str(value)


def build_signal_execution_status(
    *,
    bridge_log_path: str | Path = "artifacts/bridge_pending_orders.jsonl",
    paper_audit_log_path: str | Path = "artifacts/paper_execution_audit.jsonl",
    entry_watcher_log_path: str | Path = "artifacts/entry_watcher_audit.jsonl",
    recent_limit: int = 8,
) -> dict[str, object]:
    bridge_rows = _load_jsonl(bridge_log_path)
    paper_rows = _load_jsonl(paper_audit_log_path)
    watcher_rows = _load_jsonl(entry_watcher_log_path)

    by_cid: dict[str, dict[str, Any]] = {}
    for row in bridge_rows:
        cid = _cid(row)
        if not cid:
            continue
        current = by_cid.setdefault(
            cid,
            {
                "correlation_id": cid,
                "envelope_id": row.get("envelope_id"),
                "symbol": row.get("symbol"),
                "bridge_stage": None,
                "lifecycle_state": None,
                "audit_reason": None,
                "last_update_utc": None,
            },
        )
        current["bridge_stage"] = row.get("stage")
        current["lifecycle_state"] = row.get("lifecycle_state")
        current["audit_reason"] = row.get("audit_reason") or row.get("reason")
        current["last_update_utc"] = row.get("timestamp_utc")
        if row.get("symbol"):
            current["symbol"] = row.get("symbol")

    for row in watcher_rows:
        cid = _cid(row)
        if not cid:
            continue
        current = by_cid.setdefault(
            cid,
            {
                "correlation_id": cid,
                "envelope_id": row.get("envelope_id"),
                "symbol": row.get("symbol"),
                "bridge_stage": None,
                "lifecycle_state": None,
                "audit_reason": None,
                "last_update_utc": None,
            },
        )
        current["watcher_decision"] = row.get("decision")
        current["watcher_reason"] = row.get("reason")
        current["watcher_price"] = row.get("price")
        current["lifecycle_state"] = row.get("lifecycle_state") or current.get("lifecycle_state")
        current["last_update_utc"] = row.get("timestamp_utc") or current.get("last_update_utc")
        if row.get("symbol"):
            current["symbol"] = row.get("symbol")

    lifecycle_counts: Counter[str] = Counter()
    for row in paper_rows:
        if row.get("event_type") != "lifecycle_transition":
            continue
        cid = _cid(row)
        if not cid:
            continue
        current = by_cid.setdefault(
            cid,
            {
                "correlation_id": cid,
                "envelope_id": None,
                "symbol": row.get("symbol"),
                "bridge_stage": None,
                "lifecycle_state": None,
                "audit_reason": None,
                "last_update_utc": None,
            },
        )
        state = row.get("to_state")
        if isinstance(state, str) and state:
            current["lifecycle_state"] = state
            lifecycle_counts[state] += 1
        current["audit_reason"] = row.get("reason") or current.get("audit_reason")
        current["last_update_utc"] = row.get("timestamp_utc") or current.get("last_update_utc")

    stage_counts = Counter(
        str(v.get("bridge_stage")) for v in by_cid.values() if v.get("bridge_stage") is not None
    )
    state_counts = Counter(
        str(v.get("lifecycle_state"))
        for v in by_cid.values()
        if v.get("lifecycle_state") is not None
    )
    recent = sorted(
        by_cid.values(),
        key=lambda row: str(row.get("last_update_utc") or ""),
        reverse=True,
    )[: max(1, recent_limit)]
    return {
        "report_type": "signal_execution_status",
        "total_correlations": len(by_cid),
        "bridge_stage_counts": dict(stage_counts),
        "lifecycle_state_counts": dict(state_counts),
        "paper_lifecycle_transition_counts": dict(lifecycle_counts),
        "waiting_for_entry": state_counts.get("WAITING_FOR_ENTRY", 0),
        "entry_triggered": state_counts.get("ENTRY_TRIGGERED", 0),
        "positions_open": state_counts.get("POSITION_OPEN", 0),
        "filled": stage_counts.get("filled", 0),
        "expired": stage_counts.get("expired", 0),
        "rejected": sum(
            count for stage, count in stage_counts.items() if stage.startswith("rejected")
        ),
        "recent": recent,
    }


__all__ = ["build_signal_execution_status"]
