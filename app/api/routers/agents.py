"""Agent control surface — read-only inventory of the agent roster (SSOT).

``_AGENTS`` is the single source of truth for the roster (imported by the
worker and the telegram menu). It lists all seven agents — three
``wiring="autonomous"`` (SENTR / Watchdog / Architect, backed by
``app/agents/worker.py`` HANDLERS) and four ``wiring="interactive"`` (DALI /
Neo / Satoshi / KAI-Finder, Claude-Code-only, no worker handler). The ``wiring``
field keeps the dashboard from implying autonomous execution an interactive
agent never performs (F-06/KAI-05).

Status is derived honestly from a JSONL dropbox under
`artifacts/agents/{slug}/` — no fake heartbeats, no mocks:
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
    # F-06/KAI-05: how the agent is actually driven, so the dashboard never
    # implies a capability that is never executed.
    #   "autonomous"  — backed by an app/agents/worker.py HANDLER; runs on the
    #                   cron/systemd queue without a human in the loop.
    #   "interactive" — a Claude-Code-only subagent; has NO worker handler, so an
    #                   enqueued command is not auto-executed (it is run
    #                   interactively). The contract test in
    #                   tests/unit/test_agents_roster_contract.py pins this:
    #                   every worker HANDLER agent must be "autonomous".
    wiring: Literal["autonomous", "interactive"]


_AGENTS: dict[str, AgentDefinition] = {
    "sentr": AgentDefinition(
        slug="sentr",
        name="SENTR",
        wiring="autonomous",
        agent_id="a708ac129e9cf2569",
        role="Security & Inspection — prüft Code, Configs, Secrets, Auditierbarkeit",
        modes=["inspect", "report"],
        permissions=["read", "report"],
    ),
    "watchdog": AgentDefinition(
        slug="watchdog",
        name="Watchdog",
        wiring="autonomous",
        agent_id=None,
        role="Health & Drift Monitor — Pipeline-Outputs, Quality-Bar, Regressionen",
        modes=["check", "report"],
        permissions=["read", "report"],
    ),
    "architect": AgentDefinition(
        slug="architect",
        name="Architect",
        wiring="autonomous",
        agent_id="a14a2b53ba50ebadd",
        role="Architektur-Review & Propose — Module, Abhängigkeiten, Refactor-Vorschläge",
        modes=["review", "propose"],
        permissions=["read", "report"],
    ),
    "dali": AgentDefinition(
        slug="dali",
        name="DALI",
        wiring="interactive",
        agent_id=None,
        role=(
            "Design-Audit & UI-Propose — Dashboard, Telegram-UI, Visual System, "
            "Microcopy, Informationsarchitektur"
        ),
        modes=["audit", "propose", "implement"],
        permissions=["read", "report"],
    ),
    "neo": AgentDefinition(
        slug="neo",
        name="Neo",
        wiring="interactive",
        agent_id=None,
        role=(
            "Code-Level Root-Cause & Refactor — Debugging, Concurrency/Races, "
            "Datenfluss-Analyse, Performance-Hotspots"
        ),
        modes=["analyze", "fix"],
        permissions=["read", "report"],
    ),
    "satoshi": AgentDefinition(
        slug="satoshi",
        name="Satoshi",
        wiring="interactive",
        agent_id=None,
        role=(
            "Krypto & Custody — Signaturen/HMAC/Webhooks, Wallet/Seed, "
            "Smart-Contracts, Tokenomics, On-Chain-Provenance"
        ),
        modes=["review", "verify"],
        permissions=["read", "report"],
    ),
    "kai-finder": AgentDefinition(
        slug="kai-finder",
        name="KAI-Finder",
        wiring="interactive",
        agent_id=None,
        role=(
            "Quellen- & Daten-Discovery — neue Feeds/APIs recherchieren, "
            "bewerten, vorschlagen (Legal/Stabilität/Kosten)"
        ),
        modes=["search", "propose"],
        permissions=["read", "report"],
    ),
}


def _agent_dir(slug: str) -> Path:
    return _AGENTS_ROOT / slug


def _read_jsonl(path: Path, tail: int = 50) -> list[dict[str, Any]]:
    """Read JSONL with mid-file tolerance and reader-vs-writer retry on the
    last line. ``tail`` preserved for backward-compat callers. Delegates to
    :func:`app.storage.jsonl_io.read_jsonl_tolerant` since D-194
    (NEO-F-META-20260424-029)."""
    from app.storage.jsonl_io import read_jsonl_tolerant

    return read_jsonl_tolerant(path, tail=tail if tail else None)


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
    mode: Literal["check", "report", "inspect", "review", "propose", "audit", "implement"]
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
