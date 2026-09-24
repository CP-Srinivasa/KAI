"""Local, recoverable workspaces and self-contained context for the KAI developer hub.

This module is an operator tool. It has no dependency on KAI's runtime or app/ai.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
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
# Task-specific sources: an explicit, bounded selection, never the repository.
MAX_SOURCE_FILES = 12
SOURCE_FILE_CHARS = {"full": 24_000, "compact": 6_000}
SOURCE_BUDGET_CHARS = {"full": 90_000, "compact": 12_000}
# Assignment of a long literal to a key-like name. Complements the shared
# catalogue (which knows provider prefixes) for tokens without a known prefix.
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?key|secret|token|password|passwd|master[_-]?key|macaroon)"
    r"[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_\-+/=]{24,}[\"']"
)


class WorkflowError(RuntimeError):
    """Operator-visible failure with no secret content."""


class OfflineBaseRequired(WorkflowError):  # noqa: N818 - task-capsule API name
    """A remote refresh failed and only an explicitly accepted cached base remains."""

    def __init__(self, last_remote_sha: str | None, fetched_at: str | None) -> None:
        self.last_remote_sha = last_remote_sha
        self.fetched_at = fetched_at
        if last_remote_sha and fetched_at:
            message = (
                "Remote-Basis nicht erreichbar. Ein Offline-Start benötigt eine ausdrückliche "
                f"Bestätigung für {last_remote_sha[:8]} (zuletzt geholt {fetched_at})."
            )
        else:
            message = (
                "Remote-Basis nicht erreichbar und keine verifizierte Offline-Basis vorhanden."
            )
        super().__init__(message)


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


def _git_bytes(repo: Path, *args: str, timeout: int = 40) -> bytes:
    """Raw git output: no decoding, no strip, no newline translation (patches)."""
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],
        capture_output=True,
        timeout=timeout,
        check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if result.returncode:
        raise WorkflowError(f"Git {args[0]} fehlgeschlagen (Exit {result.returncode}).")
    return result.stdout


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _session_dir(state_root: Path) -> Path:
    root = state_root / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_status(row: dict[str, Any]) -> tuple[bool, str | None]:
    try:
        path = Path(row["worktree"]).resolve()
        branch = str(row["branch"])
    except (KeyError, TypeError, ValueError):
        return False, "Sitzungsdatei unvollständig"
    if not path.is_dir():
        return False, "Arbeitsbereich fehlt"
    try:
        actual = _git(path, "branch", "--show-current")
    except (WorkflowError, subprocess.TimeoutExpired):
        return False, "Arbeitsbereich ist kein lesbarer Git-Worktree"
    if actual != branch:
        return False, f"Branch abweichend ({actual or 'DETACHED'})"
    return True, None


def list_sessions(state_root: Path) -> list[dict[str, Any]]:
    root = _session_dir(state_root)
    rows: list[dict[str, Any]] = []
    for item in root.glob("*.json"):
        try:
            row = json.loads(item.read_text(encoding="utf-8"))
            valid, reason = _session_status(row)
            row["orphaned"] = not valid
            row["orphan_reason"] = reason
            row["session_file"] = str(item)
            rows.append(row)
        except (OSError, ValueError, TypeError):
            rows.append(
                {
                    "session_id": item.stem,
                    "created_at": "",
                    "task": "Unlesbare Sitzung",
                    "worktree": "",
                    "branch": "",
                    "orphaned": True,
                    "orphan_reason": "Sitzungsdatei ist kein gültiges JSON",
                    "session_file": str(item),
                }
            )
    return sorted(rows, key=lambda row: str(row["created_at"]), reverse=True)


def prune_stale_sessions(state_root: Path) -> list[str]:
    """Archive records whose worktree is gone, without invoking or mutating Git."""
    root = _session_dir(state_root)
    archive = root / "archive"
    moved: list[str] = []
    for item in root.glob("*.json"):
        stale = False
        try:
            row = json.loads(item.read_text(encoding="utf-8"))
            stale = not Path(row["worktree"]).resolve().is_dir()
        except (OSError, ValueError, KeyError, TypeError):
            stale = True
        if not stale:
            continue
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / item.name
        if target.exists():
            target = archive / f"{item.stem}-{uuid.uuid4().hex[:8]}.json"
        item.replace(target)
        moved.append(str(target))
    return moved


def require_session(repo: Path, state_root: Path) -> dict[str, Any]:
    target = repo.resolve()
    for row in list_sessions(state_root):
        if not row.get("orphaned") and Path(row["worktree"]).resolve() == target:
            return row
    raise WorkflowError(
        "Kein vom Hub verwalteter KAI-Arbeitsbereich. Zuerst 'Neue Aufgabe' wählen; "
        "der gemeinsame Checkout bleibt geschützt."
    )


def _base_cache_path(state_root: Path) -> Path:
    return state_root / "base-cache.json"


def _write_base_cache(state_root: Path, sha: str, fetched_at: str) -> None:
    state_root.mkdir(parents=True, exist_ok=True)
    _base_cache_path(state_root).write_text(
        json.dumps(
            {"schema_version": 1, "base_ref": BASE_REF, "sha": sha, "fetched_at": fetched_at},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _ref_reflog_time(primary: Path) -> str | None:
    try:
        selector = _git(
            primary, "reflog", "show", "-1", "--date=iso-strict", "--format=%gd", BASE_REF
        )
    except (WorkflowError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"@\{(.+)\}$", selector)
    return match.group(1) if match else None


def cached_remote_base(primary: Path, state_root: Path) -> dict[str, Any] | None:
    """Return a verified remote-tracking base; a local branch head never qualifies."""
    cache_path = _base_cache_path(state_root)
    cache: dict[str, Any] | None = None
    try:
        decoded = json.loads(cache_path.read_text(encoding="utf-8"))
        if decoded.get("base_ref") == BASE_REF:
            cache = decoded
    except (OSError, ValueError, TypeError):
        pass
    try:
        last_remote_sha = _git(primary, "rev-parse", BASE_REF)
        if cache is None:
            fetched_at = _ref_reflog_time(primary)
            if fetched_at is None:
                return None
            cache = {"sha": last_remote_sha, "fetched_at": fetched_at}
        sha = str(cache["sha"])
        fetched_at = str(cache["fetched_at"])
        _git(primary, "cat-file", "-e", f"{sha}^{{commit}}")
        _git(primary, "merge-base", "--is-ancestor", sha, last_remote_sha)
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            return None
    except (KeyError, ValueError, WorkflowError, subprocess.TimeoutExpired):
        return None
    age_s = max(0, int((datetime.now(UTC) - fetched.astimezone(UTC)).total_seconds()))
    return {"sha": sha, "fetched_at": fetched_at, "age_s": age_s}


def _primary_checkout(repo: Path) -> Path:
    root = Path(_git(repo, "rev-parse", "--show-toplevel")).resolve()
    worktrees = _git(root, "worktree", "list", "--porcelain")
    primary_line = next(
        (line for line in worktrees.splitlines() if line.startswith("worktree ")), ""
    )
    if not primary_line:
        raise WorkflowError("Git hat keinen kanonischen Checkout gemeldet.")
    return Path(primary_line.removeprefix("worktree ")).resolve()


def new_task(
    repo: Path, state_root: Path, task: str, *, allow_offline: bool = False
) -> dict[str, Any]:
    task = task.strip()
    if not task or len(task) > 300:
        raise WorkflowError("Aufgabe muss 1 bis 300 Zeichen enthalten.")
    primary = _primary_checkout(repo)
    base_mode = "remote"
    base_age_s = 0
    try:
        _git(
            primary,
            "fetch",
            "origin",
            "--no-tags",
            "--",
            BASE_REF.removeprefix("origin/"),
            timeout=90,
        )
        base_sha = _git(primary, "rev-parse", BASE_REF)
        fetched_at = _now()
        _write_base_cache(state_root, base_sha, fetched_at)
    except (WorkflowError, subprocess.TimeoutExpired) as exc:
        cached = cached_remote_base(primary, state_root)
        if not allow_offline or cached is None:
            raise OfflineBaseRequired(
                str(cached["sha"]) if cached else None,
                str(cached["fetched_at"]) if cached else None,
            ) from exc
        base_sha = str(cached["sha"])
        fetched_at = str(cached["fetched_at"])
        base_age_s = int(cached["age_s"])
        base_mode = "offline-cache"
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
        "base_mode": base_mode,
        "base_age_s": base_age_s,
        "base_fetched_at": fetched_at,
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
    # Byte-exact: a text-mode write turned LF into CRLF on Windows and _git's
    # strip() dropped the final newline -> "git apply: corrupt patch" (23.09.).
    tracked = _git_bytes(repo, "diff", "--binary", "HEAD")
    changed_tracked = _git(repo, "diff", "--name-only", "HEAD", "-z")
    if any(SECRET_NAME.search(name) for name in changed_tracked.split("\0") if name):
        raise WorkflowError("Geänderte Secret-Datei darf nicht im Snapshot gespeichert werden.")
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    names = [name for name in untracked.split("\0") if name]
    planned: list[tuple[Path, Path, int]] = []
    total = len(tracked)
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
    patch.write_bytes(tracked)
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


def _secret_scanner(repo: Path) -> Callable[[str, str], list[Any]]:
    """Load the repository's single secret catalogue (scripts/secret_guard.py).

    No second pattern list lives here. Without the catalogue, task sources are
    refused: an unscanned file must not leave the machine.
    """
    guard = repo / "scripts" / "secret_guard.py"
    catalogue = repo / "app" / "security" / "secret_patterns.py"
    if not guard.is_file() or not catalogue.is_file():
        raise WorkflowError(
            "Secret-Prüfung nicht verfügbar (scripts/secret_guard.py fehlt); "
            "Quelltext wird nicht übertragen."
        )
    name = "kai_dev_secret_guard_" + _sha256(str(guard.resolve()).encode())[:12]
    spec = importlib.util.spec_from_file_location(name, guard)
    if spec is None or spec.loader is None:
        raise WorkflowError("Secret-Prüfung nicht ladbar; Quelltext wird nicht übertragen.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module at class creation
    # Loading must not leave __pycache__ behind in the operator's worktree.
    previous_flag = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any import failure means: fail closed
        sys.modules.pop(name, None)
        raise WorkflowError(
            f"Secret-Prüfung nicht ladbar ({type(exc).__name__}); Quelltext wird nicht übertragen."
        ) from exc
    finally:
        sys.dont_write_bytecode = previous_flag
    scan: Callable[[str, str], list[Any]] = module.scan_text
    return scan


def validate_sources(repo: Path, sources: Sequence[str]) -> list[str]:
    """Normalise and check an explicit source selection before anything is written.

    Only versioned files inside the worktree qualify. Secret-named files and
    files whose content matches the shared secret catalogue are refused; the
    error names file and line, never the value.
    """
    cleaned: list[str] = []
    for raw in sources:
        text = str(raw).strip()
        if not text:
            continue
        relative = PureWindowsPath(text).as_posix() if "\\" in text else text
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts:
            raise WorkflowError(f"Quelldatei liegt außerhalb des Arbeitsbereichs: {text}")
        if SECRET_NAME.search(relative):
            raise WorkflowError(f"Secret-Datei ist als Quelltext ausgeschlossen: {relative}")
        if relative not in cleaned:
            cleaned.append(relative)
    if len(cleaned) > MAX_SOURCE_FILES:
        raise WorkflowError(f"Es sind höchstens {MAX_SOURCE_FILES} Quelldateien erlaubt.")
    if not cleaned:
        return []
    root = repo.resolve()
    scan = _secret_scanner(repo)
    for relative in cleaned:
        source = repo / relative
        if not source.resolve().is_relative_to(root) or source.is_symlink():
            raise WorkflowError(f"Quelldatei liegt außerhalb des Arbeitsbereichs: {relative}")
        if not _git(repo, "ls-files", "--", relative) or not source.is_file():
            raise WorkflowError(f"Quelldatei ist nicht versioniert oder fehlt: {relative}")
        data = source.read_bytes()
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkflowError(f"Quelldatei ist kein UTF-8-Text: {relative}") from exc
        if "\0" in body:
            raise WorkflowError(f"Quelldatei ist binär: {relative}")
        # Pseudo path: the guard's fixture allowlist must never apply here.
        findings = [f"{f.secret_type} · {relative}:{f.line}" for f in scan(body, "ctx:" + relative)]
        findings += [
            f"secret assignment · {relative}:{number}"
            for number, line in enumerate(body.splitlines(), start=1)
            if SECRET_ASSIGNMENT.search(line)
        ]
        if findings:
            raise WorkflowError("Quelldatei enthält mögliche Secrets: " + "; ".join(findings[:5]))
    return cleaned


def _fence(body: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    return "`" * max(3, longest + 1)


def _source_sections(repo: Path, sources: Sequence[str], *, compact: bool) -> list[str]:
    profile = "compact" if compact else "full"
    per_file = SOURCE_FILE_CHARS[profile]
    remaining = SOURCE_BUDGET_CHARS[profile]
    lines = [
        "",
        "## Task-specific sources",
        "Explicitly selected for this task; nothing else from the repository is included.",
        f"Selection: {len(sources)} file(s), budget {remaining} characters ({profile}).",
        "Base statements about code only on these sources and name the file you rely on.",
    ]
    for relative in sources:
        data = (repo / relative).read_bytes()
        body = data.decode("utf-8")
        try:
            blob = _git(repo, "rev-parse", f"HEAD:{relative}")
        except WorkflowError:
            blob = "not in HEAD (new file)"
        dirty = bool(_git(repo, "status", "--porcelain", "--", relative))
        included = body[: min(per_file, remaining)]
        remaining -= len(included)
        lines.extend(
            [
                "",
                f"## Task source: {relative}",
                f"Git blob at HEAD: {blob}",
                f"SHA-256 of working file: {_sha256(data)}",
                f"Working file differs from HEAD: {'yes' if dirty else 'no'}",
                f"Included characters: {len(included)} of {len(body)}",
            ]
        )
        if included:
            fence = _fence(included)
            lines.extend([fence + "text", included, fence])
        if len(included) < len(body):
            lines.append(
                "TRUNCATED: only the first characters are included"
                + (" (budget exhausted)" if remaining <= 0 else "")
                + "; request the rest by path and hash before relying on it."
            )
    return lines


def context_pack(
    repo: Path,
    state_root: Path,
    *,
    task: str,
    handoff: dict[str, Any] | None = None,
    compact: bool = False,
    sources: Sequence[str] | None = None,
) -> Path:
    """Produce a portable document: no local path is required to understand it."""
    session = require_session(repo, state_root)
    if sources is None:
        sources = list(handoff.get("context_sources", [])) if handoff else []
    selected = validate_sources(repo, sources)
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
    ]
    if handoff:
        lines.append(f"Handoff ID: {handoff['handoff_id']}")
    lines += [
        f"Session ID (workspace, not the handoff): {session['session_id']}",
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
                f"Handoff ID: {handoff['handoff_id']}",
                "The handoff ID identifies this transfer. The session ID above identifies",
                "the workspace and is a different value; do not confuse them.",
                f"From: {handoff['from_agent']}",
                f"To: {handoff['to_agent']}",
                f"Handoff receipt SHA-256: {handoff['payload_sha256']}",
                f"Previous receipt SHA-256: {handoff['previous_sha256']}",
                "Back-reference: entry with this handoff ID and receipt in the developer hub's",
                "append-only hash chain handoffs/ledger.jsonl.",
                f"Acknowledgement challenge: {handoff['ack_challenge']}",
                f"Completed: {handoff['completed'] or 'none recorded'}",
                f"Open items: {handoff['open_items'] or 'none recorded'}",
                f"Assumptions: {handoff['assumptions'] or 'none recorded'}",
                f"Next action: {handoff['next_action'] or 'none recorded'}",
                f"Tests: {handoff['tests'] or 'not recorded'}",
                f"Snapshot manifest SHA-256: {handoff['snapshot_manifest_sha256']}",
                "",
                "Before continuing, reply with the handoff ID and the acknowledgement challenge,",
                "restate the open item and name the included sources your statement is based on.",
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
    if selected:
        lines.extend(_source_sections(repo, selected, compact=compact))
    target_dir = state_root / "context" / str(session["session_id"])
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (
        target_dir
        / f"KAI_CONTEXT_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}.md"
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
