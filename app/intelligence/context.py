"""ContextBuilder — allowlisted, redacted read-only context (ADR 0015 §2.7).

Guards mirror ``app/agents/tools/_helpers.py``: resolve() + is_relative_to
against an operator-configured allowlist, plus a HARD-CODED denylist that no
setting can widen. Every prompt is passed through the canonical secret
sanitizer before it may leave the process.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.audit.sanitization import sanitize_string

# Not configurable on purpose: even a mis-set allowlist must never expose these.
_DENY_SUBSTRINGS = (
    ".env",
    "config/",
    "credentials",
    "macaroon",
    ".session",
    "id_rsa",
    ".pem",
    ".key",
)

_MAX_FILE_CHARS = 40_000


class ContextRefusedError(ValueError):
    """Path outside the allowlist / on the denylist — fail-closed, no partial read."""


@dataclass(frozen=True)
class BuiltContext:
    text: str
    input_refs: tuple[str, ...]
    redaction_count: int


class ContextBuilder:
    def __init__(self, workspace_root: Path, allowlist: tuple[str, ...]) -> None:
        self._root = workspace_root.resolve()
        self._allowed = tuple((self._root / entry).resolve() for entry in allowlist)

    def _check(self, candidate: Path) -> Path:
        resolved = candidate.resolve()
        lowered = str(resolved).replace("\\", "/").lower()
        if any(marker in lowered for marker in _DENY_SUBSTRINGS):
            raise ContextRefusedError(f"denylisted path: {candidate}")
        if not any(resolved == base or resolved.is_relative_to(base) for base in self._allowed):
            raise ContextRefusedError(f"path outside context allowlist: {candidate}")
        return resolved

    def build(self, paths: list[str]) -> BuiltContext:
        blocks: list[str] = []
        refs: list[str] = []
        redactions = 0
        for raw_path in paths:
            resolved = self._check(self._root / raw_path)
            content = resolved.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_CHARS]
            sanitized = sanitize_string(content)
            redactions += sanitized.count("[REDACTED:")
            rel = resolved.relative_to(self._root).as_posix()
            refs.append(rel)
            # Documents are DATA, framed as such — part of the injection posture:
            # instructions inside them are quoted content, not commands.
            blocks.append(f"<dokument pfad={rel!r}>\n{sanitized}\n</dokument>")
        return BuiltContext(
            text="\n\n".join(blocks), input_refs=tuple(refs), redaction_count=redactions
        )
