"""Deterministic audit-digest computation (KAI L3).

Computes a single SHA256 over a set of audit files in a deterministic,
order-independent way (sorted by path; each file contributes its path + size +
content hash). The result is stable for identical inputs regardless of OS or
listing order — so the same audit state always yields the same digest, which is
what makes an on-chain anchor meaningful.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AuditDigest:
    digest: str  # hex SHA256 over the manifest
    files: dict[str, str] = field(default_factory=dict)  # path -> per-file sha256
    missing: list[str] = field(default_factory=list)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_audit_digest(paths: list[str]) -> AuditDigest:
    """Return a deterministic SHA256 digest over the given audit files.

    Missing files are recorded (not silently skipped) so a vanished SSOT file is
    visible in the manifest rather than producing a falsely-stable digest.
    """
    per_file: dict[str, str] = {}
    missing: list[str] = []
    for raw in sorted(set(paths)):
        p = Path(raw)
        if p.is_file():
            per_file[raw] = _sha256_file(p)
        else:
            missing.append(raw)

    manifest = hashlib.sha256()
    for path in sorted(per_file):
        size = Path(path).stat().st_size
        manifest.update(f"{path}\0{size}\0{per_file[path]}\n".encode())
    for path in sorted(missing):
        manifest.update(f"{path}\0MISSING\n".encode())

    return AuditDigest(digest=manifest.hexdigest(), files=per_file, missing=missing)
