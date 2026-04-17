"""Agent control surface — read-only inventory of Claude-Code-only agents.

Lists SENTR / Watchdog / Architect with honest status derived from a JSONL
dropbox under `artifacts/agents/{slug}/`. No fake heartbeats, no mocks:
- `live`        — at least one finding/run JSONL within last 24h
- `prepared`    — directory exists but no recent activity
- `unavailable` — directory does not exist

Commands (Watchdog `check`/`report`, etc.) are written into a per-agent
command queue file `artifacts/agents/{slug}/commands.jsonl` which the agent
process consumes out-of-band. The HTTP layer never executes anything itself —
this is the read+enqueue boundary, write-side guardrail intentionally thin.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/operator/agents", tags=["agents"])

_AGENTS_ROOT = Path("artifacts/agents")
_LIVE_WINDOW = timedelta(hours=24)
_CONVERSATION_TAIL_DEFAULT = 50
_CONVERSATION_TAIL_MAX = 500


class AgentDefinition(BaseModel):
    slug: str
    name: str
    agent_id: str | None
    role: str
    modes: list[str]
    permissions: list[str]


_AGENTS: dict[str, AgentDefinition] = {
    "sentr": AgentDefinition(
        slug="sentr",
        name="SENTR",
        agent_id="a708ac129e9cf2569",
        role="Security & Inspection — prüft Code, Configs, Secrets, Auditierbarkeit",
        modes=["inspect", "report"],
        permissions=["read", "report"],
    ),
    "watchdog": AgentDefinition(
        slug="watchdog",
        name="Watchdog",
        agent_id=None,
        role="Health & Drift Monitor — Pipeline-Outputs, Quality-Bar, Regressionen",
        modes=["check", "report"],
        permissions=["read", "report"],
    ),
    "architect": AgentDefinition(
        slug="architect",
        name="Architect",
        agent_id="a14a2b53ba50ebadd",
        role="Architektur-Review & Propose — Module, Abhängigkeiten, Refactor-Vorschläge",
        modes=["review", "propose"],
        permissions=["read", "report"],
    ),
}


def _agent_dir(slug: str) -> Path:
    return _AGENTS_ROOT / slug


def _read_jsonl(path: Path, tail: int = 50) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except json.JSONDecodeError:
            continue
    return rows[-tail:] if tail else rows


def _agent_status(slug: str) -> dict[str, Any]:
    d = _agent_dir(slug)
    if not d.is_dir():
        return {
            "status": "unavailable",
            "last_seen": None,
            "findings_count": 0,
            "runs_count": 0,
        }

    findings = _read_jsonl(d / "findings.jsonl")
    runs = _read_jsonl(d / "runs.jsonl")
    last_ts: datetime | None = None
    for src in (findings, runs):
        for row in src:
            ts = row.get("ts") or row.get("timestamp")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                if last_ts is None or dt > last_ts:
                    last_ts = dt
            except ValueError:
                continue

    now = datetime.now(UTC)
    if last_ts is not None and (now - last_ts) <= _LIVE_WINDOW:
        status = "live"
    else:
        status = "prepared"

    return {
        "status": status,
        "last_seen": last_ts.isoformat() if last_ts else None,
        "findings_count": len(findings),
        "runs_count": len(runs),
    }


def _serialize_agent(defn: AgentDefinition, *, with_findings: bool = False) -> dict[str, Any]:
    base = defn.model_dump()
    base.update(_agent_status(defn.slug))
    if with_findings:
        d = _agent_dir(defn.slug)
        base["recent_findings"] = _read_jsonl(d / "findings.jsonl", tail=20)
        base["recent_runs"] = _read_jsonl(d / "runs.jsonl", tail=20)
    return base


@router.get("")
async def list_agents() -> dict[str, Any]:
    return {
        "agents": [_serialize_agent(a) for a in _AGENTS.values()],
        "generated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/{slug}")
async def get_agent(slug: str) -> dict[str, Any]:
    defn = _AGENTS.get(slug)
    if defn is None:
        raise HTTPException(status_code=404, detail="agent_not_found")
    return _serialize_agent(defn, with_findings=True)


class CommandRequest(BaseModel):
    mode: Literal["check", "report", "inspect", "review", "propose"]
    note: str | None = Field(default=None, max_length=500)


@router.post("/{slug}/commands")
async def enqueue_command(slug: str, payload: CommandRequest) -> dict[str, Any]:
    defn = _AGENTS.get(slug)
    if defn is None:
        raise HTTPException(status_code=404, detail="agent_not_found")
    if payload.mode not in defn.modes:
        raise HTTPException(status_code=400, detail=f"mode_not_supported:{payload.mode}")

    d = _agent_dir(defn.slug)
    d.mkdir(parents=True, exist_ok=True)
    queue = d / "commands.jsonl"
    cmd_id = uuid4().hex
    entry = {
        "id": cmd_id,
        "ts": datetime.now(UTC).isoformat(),
        "agent": defn.slug,
        "mode": payload.mode,
        "note": payload.note,
        "status": "queued",
    }
    with queue.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Der Command wird zusätzlich als System-Event in den Conversation-Thread
    # geschrieben, damit Dashboard + Telegram denselben Verlauf sehen.
    append_conversation_event(
        defn.slug,
        source="dashboard",
        role="operator",
        content=f"[mode:{payload.mode}] {payload.note or ''}".strip(),
        kind="command",
        meta={"command_id": cmd_id, "mode": payload.mode},
    )
    return entry


# ---------------------------------------------------------------------------
# Conversation — single-source-of-truth für Dashboard + Telegram + Agent-Replies
# ---------------------------------------------------------------------------

class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    source: Literal["dashboard", "telegram", "agent"] = "dashboard"


def append_conversation_event(
    slug: str,
    *,
    source: Literal["dashboard", "telegram", "agent"],
    role: Literal["operator", "agent"],
    content: str,
    kind: str = "message",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Appendiert ein Event in artifacts/agents/{slug}/conversation.jsonl.

    Öffentlich importierbar, damit Telegram-Bot und CLI denselben Eventpfad
    nutzen können. Kein Lock — Appends sind auf allen ernsthaften FS atomar
    bis 4KB. Wenn je > 4KB geschrieben wird, eine echte Lock-Strategie ergänzen.
    """
    if slug not in _AGENTS:
        raise ValueError(f"unknown_agent:{slug}")
    d = _agent_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    event = {
        "id": uuid4().hex,
        "ts": datetime.now(UTC).isoformat(),
        "agent": slug,
        "source": source,
        "role": role,
        "kind": kind,
        "content": content,
        "meta": meta or {},
    }
    with (d / "conversation.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def _load_conversation(slug: str, tail: int, since: str | None) -> list[dict[str, Any]]:
    p = _agent_dir(slug) / "conversation.jsonl"
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            if since:
                ts = str(obj.get("ts", ""))
                if ts <= since:
                    continue
            rows.append(obj)
    return rows[-tail:] if tail else rows


@router.post("/{slug}/messages")
async def post_message(slug: str, payload: MessageRequest) -> dict[str, Any]:
    if slug not in _AGENTS:
        raise HTTPException(status_code=404, detail="agent_not_found")
    event = append_conversation_event(
        slug,
        source=payload.source,
        role="operator",
        content=payload.content,
    )
    return event


@router.get("/{slug}/messages")
async def get_messages(
    slug: str,
    since: str | None = Query(default=None, description="ISO-Timestamp — nur Events danach"),
    tail: int = Query(default=_CONVERSATION_TAIL_DEFAULT, ge=1, le=_CONVERSATION_TAIL_MAX),
) -> dict[str, Any]:
    if slug not in _AGENTS:
        raise HTTPException(status_code=404, detail="agent_not_found")
    events = _load_conversation(slug, tail, since)
    return {
        "agent": slug,
        "events": events,
        "count": len(events),
        "generated_at": datetime.now(UTC).isoformat(),
    }
