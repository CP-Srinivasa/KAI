"""Input-artifact pinning for verifiable, third-party-recomputable attestations (B5b).

An attestation over a *derived* report (e.g. the canonical-edge report) is only
recomputable by a third party if the EXACT input state that produced it is sealed
into the claim. This module pins each input artifact — an append-only audit JSONL
— as ``{role, path, sha256, lines}`` and verifies a pinned prefix against the
current file.

Append-only robustness
----------------------
The audit files only ever grow. A pin records the line count ``N`` and the
SHA-256 over the first ``N`` lines at attest time. On verification the file may be
LONGER (new appends) — only the first ``N`` pinned lines are re-hashed, so healthy
growth verifies clean. A file that SHRANK, or whose pinned prefix changed, is a
tampered/rewritten input and fails loud.

Line-ending normalisation: lines are split with ``str.splitlines`` and re-joined
with ``\\n`` before hashing, so a CRLF/LF difference between attest and verify
machines does not spuriously break a pin.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def read_lines(path: str | Path) -> list[str]:
    """Read a text file into a list of lines (missing file -> ``[]``).

    Uses ``splitlines`` so a trailing newline does not add an empty final line and
    CRLF/LF endings collapse to logical lines — the same normalisation
    :func:`hash_lines` relies on.
    """
    p = Path(path)
    if not p.exists():
        return []
    return p.read_text(encoding="utf-8").splitlines()


def hash_lines(lines: Sequence[str]) -> str:
    """Deterministic SHA-256 over ``lines`` joined with ``\\n`` (LF-normalised)."""
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _relative_path(path: str | Path, root: Path) -> str:
    """POSIX-normalised path relative to ``root`` (absolute fallback across drives).

    The path string is part of the hashed payload, so it must be deterministic on
    both the attesting and verifying machine. Verification resolves it back via
    ``root / stored_path`` — pathlib returns the absolute path unchanged when the
    stored value is itself absolute, so the cross-drive fallback stays resolvable.
    """
    p = Path(path)
    try:
        rel = os.path.relpath(p, root)
    except ValueError:
        # Different drive on Windows — relativisation is impossible; pin absolute.
        return p.as_posix()
    return Path(rel).as_posix()


def pin_input(
    role: str,
    path: str | Path,
    lines: Sequence[str],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Pin one already-read input artifact as ``{role, path, sha256, lines}``.

    ``lines`` is passed in (not re-read) so the pin hashes the EXACT content the
    report consumed — no time-of-check/time-of-use gap against a growing file.
    """
    base = root or Path.cwd()
    return {
        "role": role,
        "path": _relative_path(path, base),
        "sha256": hash_lines(lines),
        "lines": len(lines),
    }


def pin_inputs(
    specs: Sequence[tuple[str, str | Path, Sequence[str]]],
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Pin several inputs and return them DETERMINISTICALLY sorted by (role, path)."""
    pins = [pin_input(role, path, lines, root=root) for role, path, lines in specs]
    return sorted(pins, key=lambda pin: (str(pin["role"]), str(pin["path"])))


@dataclass(frozen=True)
class PinCheck:
    """Outcome of verifying one pin against the current file on disk.

    ``prefix_lines`` are the first ``N`` pinned lines (the exact attest-time
    content) so a caller can reconstruct the report from the verified prefix
    rather than the possibly-grown current file.
    """

    ok: bool
    reason: str
    sha256: str
    prefix_lines: list[str]


def verify_input_pin(pin: dict[str, Any], *, root: Path | None = None) -> PinCheck:
    """Verify a pinned input's first-``N``-line prefix against the current file.

    Fails loud (never raises) on: malformed pin, missing file, a file that shrank
    below the pinned line count, or a changed pinned prefix. Append-only growth
    (file longer than pinned) verifies OK — only the pinned prefix is hashed.
    """
    base = root or Path.cwd()
    rel = str(pin.get("path", ""))
    expected_sha = str(pin.get("sha256", ""))
    raw_lines = pin.get("lines")
    if not isinstance(raw_lines, int) or isinstance(raw_lines, bool):
        return PinCheck(False, f"malformed pin (lines={raw_lines!r})", "", [])
    expected_lines = raw_lines
    if expected_lines < 0:
        return PinCheck(False, f"malformed pin (negative lines={expected_lines})", "", [])

    target = base / rel
    if not target.exists():
        return PinCheck(False, f"input missing: {rel}", "", [])
    current = read_lines(target)
    if len(current) < expected_lines:
        return PinCheck(
            False,
            f"input shrank: {rel} ({len(current)} lines < {expected_lines} pinned)",
            "",
            [],
        )
    prefix = list(current[:expected_lines])
    actual_sha = hash_lines(prefix)
    if actual_sha != expected_sha:
        return PinCheck(False, f"pinned prefix changed: {rel}", actual_sha, prefix)
    return PinCheck(True, "ok", actual_sha, prefix)


__all__ = [
    "PinCheck",
    "hash_lines",
    "pin_input",
    "pin_inputs",
    "read_lines",
    "verify_input_pin",
]
