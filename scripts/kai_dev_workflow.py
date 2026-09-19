"""Local, recoverable workspaces and self-contained context for the KAI developer hub.

This module is an operator tool. It has no dependency on KAI's runtime or app/ai.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BASE_REF = "origin/claude/p7/reentry-ia-codex-cycle"
CONTEXT_FILES = (
    "AGENTS.md",
    "docs/AI_HANDOFF.md",
    "docs/adr/0020-developer-independence-reserve.md",
    "docs/adr/0017-ai-control-plane-and-litellm-transport.md",
    "ARCHITECTURE.md",
    "CLAUDE.md",
)
MAX_CONTEXT_FILE_CHARS = 28_000
MAX_UNTRACKED_FILE_BYTES = 5_000_000
MAX_SNAPSHOT_BYTES = 20_000_000
SECRET_NAME = re.compile(
    r"(^|[\\/])(?:\.env(?:\.|$)|.*(?:secret|credential|wallet|macaroon|\.pem$|\.key$))", re.I
)


class WorkflowError(RuntimeError):
    """Operator-visible failure with no secret content."""


def _git(repo: Path, *args: str, timeout: int = 40) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if result.returncode:
        raise WorkflowError(f"Git {args[0]} fehlgeschlagen (Exit {result.returncode}).")
    return result.stdout.strip()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _session_dir(state_root: Path) -> Path:
    root = state_root / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_sessions(state_root: Path) -> list[dict[str, Any]]:
    root = _session_dir(state_root)
    rows: list[dict[str, Any]] = []
    for item in root.glob("*.json"):
        try:
            row = json.loads(item.read_text(encoding="utf-8"))
            path = Path(row["worktree"]).resolve()
            if path.is_dir() and _git(path, "branch", "--show-current") == row["branch"]:
                rows.append(row)
        except (OSError, ValueError, KeyError, WorkflowError):
            continue
    return sorted(rows, key=lambda row: str(row["created_at"]), reverse=True)


def require_session(repo: Path, state_root: Path) -> dict[str, Any]:
    target = repo.resolve()
    for row in list_sessions(state_root):
        if Path(row["worktree"]).resolve() == target:
            return row
    raise WorkflowError(
        "Kein vom Hub verwalteter KAI-Arbeitsbereich. Zuerst 'Neue Aufgabe' wählen; "
        "der gemeinsame Checkout bleibt geschützt."
    )


def new_task(repo: Path, state_root: Path, task: str) -> dict[str, Any]:
    task = task.strip()
    if not task or len(task) > 300:
        raise WorkflowError("Aufgabe muss 1 bis 300 Zeichen enthalten.")
    root = Path(_git(repo, "rev-parse", "--show-toplevel")).resolve()
    # The first registered worktree is the canonical checkout. New sessions
    # always branch from its freshly fetched authoritative remote ref.
    worktrees = _git(root, "worktree", "list", "--porcelain")
    primary_line = next(
        (line for line in worktrees.splitlines() if line.startswith("worktree ")), ""
    )
    if not primary_line:
        raise WorkflowError("Git hat keinen kanonischen Checkout gemeldet.")
    primary = Path(primary_line.removeprefix("worktree ")).resolve()
    _git(
        primary, "fetch", "origin", "--no-tags", "--", BASE_REF.removeprefix("origin/"), timeout=90
    )
    base_sha = _git(primary, "rev-parse", BASE_REF)
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:35] or "task"
    suffix = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    branch = f"codex/dev-task-{slug}-{suffix}"
    path = primary.parent / f"{primary.name}-dev-task-{slug}-{suffix}"
    if path.exists():
        raise WorkflowError(f"Arbeitsbereich existiert bereits: {path}")
    _git(primary, "worktree", "add", str(path), "-b", branch, base_sha, timeout=90)
    row: dict[str, Any] = {
        "schema_version": 1,
        "session_id": uuid.uuid4().hex,
        "created_at": _now(),
        "task": task,
        "worktree": str(path),
        "branch": branch,
        "base_ref": BASE_REF,
        "base_sha": base_sha,
    }
    target = _session_dir(state_root) / f"{row['session_id']}.json"
    target.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return row


def snapshot(repo: Path, state_root: Path, handoff_id: str) -> dict[str, Any]:
    """Preserve a recoverable copy of tracked changes and safe untracked files."""
    require_session(repo, state_root)
    root = state_root / "snapshots" / handoff_id
    if root.exists():
        raise WorkflowError("Für diese Übergabe existiert bereits ein Snapshot.")
    tracked = _git(repo, "diff", "--binary", "HEAD")
    changed_tracked = _git(repo, "diff", "--name-only", "HEAD", "-z")
    if any(SECRET_NAME.search(name) for name in changed_tracked.split("\0") if name):
        raise WorkflowError("Geänderte Secret-Datei darf nicht im Snapshot gespeichert werden.")
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    names = [name for name in untracked.split("\0") if name]
    planned: list[tuple[Path, Path, int]] = []
    total = len(tracked.encode("utf-8"))
    for name in names:
        relative = Path(name)
        raw_source = repo / relative
        source = raw_source.resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not source.is_relative_to(repo.resolve())
            or SECRET_NAME.search(name)
            or not source.is_file()
            or raw_source.is_symlink()
        ):
            raise WorkflowError("Ungetrackte Datei ist für einen sicheren Snapshot ungeeignet.")
        size = source.stat().st_size
        total += size
        if size > MAX_UNTRACKED_FILE_BYTES or total > MAX_SNAPSHOT_BYTES:
            raise WorkflowError("Snapshot überschreitet das lokale Größenlimit (20 MB).")
        planned.append((source, relative, size))
    if total > MAX_SNAPSHOT_BYTES:
        raise WorkflowError("Snapshot überschreitet das lokale Größenlimit (20 MB).")
    root.mkdir(parents=True)
    patch = root / "tracked.patch"
    patch.write_text(tracked, encoding="utf-8")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at": _now(),
        "repository": str(repo.resolve()),
        "head": _git(repo, "rev-parse", "HEAD"),
        "tracked_patch_sha256": _sha256(patch.read_bytes()),
        "untracked": [],
    }
    for source, relative, size in planned:
        target = root / "untracked" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest["untracked"].append(
            {"path": relative.as_posix(), "bytes": size, "sha256": _sha256(target.read_bytes())}
        )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"path": str(root), "manifest_sha256": _sha256(manifest_path.read_bytes())}


def context_pack(
    repo: Path,
    state_root: Path,
    *,
    task: str,
    handoff: dict[str, Any] | None = None,
    compact: bool = False,
) -> Path:
    """Produce a portable document: no local path is required to understand it."""
    session = require_session(repo, state_root)
    head = _git(repo, "rev-parse", "HEAD")
    status = _git(repo, "status", "--short") or "clean"
    diff_stat = _git(repo, "diff", "--stat", "HEAD") or "none"
    lines = [
        "# KAI developer context pack",
        "",
        "This document is self-contained. Read the included rules before taking action.",
        "It contains no API key or .env content. External agents must not infer production authority.",
        "",
        f"Generated UTC: {_now()}",
        f"Session: {session['session_id']}",
        f"Profile: {'compact' if compact else 'full'}",
        f"Task: {task}",
        f"Branch: {session['branch']}",
        f"Base SHA: {session['base_sha']}",
        f"Current HEAD: {head}",
        f"Worktree status: {status}",
        f"Changed tracked files: {diff_stat}",
        "",
        "## Authority",
        "KAI's only runtime AI control plane is app/ai. LiteLLM is transport below it.",
        "Claude Code reviews and integrates. No agent may merge, deploy, change trading gates,",
        "activate PRIMARY, or read .env/secrets as part of this task.",
        "Use the worktree shown in the session record for local edits.",
    ]
    if handoff:
        lines.extend(
            [
                "",
                "## Current handoff",
                f"From: {handoff['from_agent']}",
                f"To: {handoff['to_agent']}",
                f"Receipt: {handoff['payload_sha256']}",
                f"Acknowledgement challenge: {handoff['ack_challenge']}",
                f"Completed: {handoff['completed'] or 'none recorded'}",
                f"Open items: {handoff['open_items'] or 'none recorded'}",
                f"Assumptions: {handoff['assumptions'] or 'none recorded'}",
                f"Next action: {handoff['next_action'] or 'none recorded'}",
                f"Tests: {handoff['tests'] or 'not recorded'}",
                f"Snapshot manifest SHA-256: {handoff['snapshot_manifest_sha256']}",
                "",
                "Before continuing, restate the open item and reply with the acknowledgement challenge.",
            ]
        )
    for name in CONTEXT_FILES:
        source = repo / name
        if not source.is_file():
            lines.extend(["", f"## {name}", "MISSING — request the current document."])
            continue
        data = source.read_bytes()
        body = data.decode("utf-8", errors="replace")
        if compact and name == "docs/AI_HANDOFF.md":
            start = body.find("## 3b. Rolle OpenCode")
            included = body[start : start + 3_000] if start >= 0 else body[:3_000]
        elif compact:
            limits = {
                "AGENTS.md": 4_500,
                "docs/adr/0020-developer-independence-reserve.md": 3_000,
                "docs/adr/0017-ai-control-plane-and-litellm-transport.md": 1_200,
                "ARCHITECTURE.md": 1_500,
                "CLAUDE.md": 1_200,
            }
            included = body[: limits.get(name, 1_200)]
        else:
            included = body[:MAX_CONTEXT_FILE_CHARS]
        lines.extend(
            [
                "",
                f"## Included source: {name}",
                f"SHA-256 of full source: {_sha256(data)}",
                f"Included characters: {len(included)} of {len(body)}",
                "```text",
                included,
                "```",
            ]
        )
        if len(included) < len(body):
            lines.append(
                "TRUNCATED: consult the versioned source before making changes in this area."
            )
    target_dir = state_root / "context" / str(session["session_id"])
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (
        target_dir
        / f"KAI_CONTEXT_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}.md"
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
