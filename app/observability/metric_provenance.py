"""Provenance for dashboard metrics — WHAT a number counted, and against what.

STAB-2026-09-01 §29. Extracted from ``app/api/routers/dashboard.py``: the router
is a god-file under ratchet, and this is a self-contained concern with its own
tests. Nothing here touches request handling.

The contract already carried a value, a window and a source artifact, but not the
two things that make a percentage checkable: its numerator and its denominator.
Different cards therefore showed 72.41 %, 73.49 %, 84/116, "166 checked" and
"19890 annotations" beside one another with no way to tell whether the
denominators differed for a reason or by accident. Differing numbers are fine;
UNEXPLAINED differing numbers are not.

``population_id`` names the set a number was computed over, so two cards that
disagree can be seen to be measuring two different things. ``code_sha`` and
``config_sha256`` pin the code and configuration that produced it, and
``source_artifact_sha256`` pins the bytes it was read from — the same provenance
triple the truth ledger uses.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = [
    "artifact_sha256",
    "code_sha",
    "config_sha256",
    "metric_contract",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def code_sha() -> str:
    """The commit this process is running. Empty when git is unavailable."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=str(_repo_root()),
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - provenance must never break a payload
        return ""


@lru_cache(maxsize=1)
def config_sha256() -> str:
    """Stable digest over the config tree the numbers were computed against."""
    try:
        root = _repo_root() / "config"
        if not root.is_dir():
            return ""
        h = hashlib.sha256()
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            h.update(f.relative_to(root).as_posix().encode("utf-8"))
            h.update(f.read_bytes())
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def artifact_sha256(path: Path) -> str:
    """Digest of the artifact a number was read from. Empty when unreadable.

    Cached on (path, mtime, size) so a hot dashboard request does not re-hash a
    multi-megabyte append-only stream on every call.
    """
    try:
        st = path.stat()
    except OSError:
        return ""
    return _artifact_sha256_cached(str(path), st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=64)
def _artifact_sha256_cached(path_str: str, mtime_ns: int, size: int) -> str:
    try:
        h = hashlib.sha256()
        with Path(path_str).open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def metric_contract(
    *,
    value: object,
    unit: str,
    semantic_type: str,
    scope: str,
    source_artifact: Path,
    generated_at: str,
    artifact_updated_at: Callable[[Path], Any],
    artifact_stale_status: Callable[[Path], Any],
    window_hours: int | None = None,
    since: str | None = None,
    until: str | None = None,
    sample_size: int | None = None,
    is_decision_relevant: bool = False,
    is_read_only: bool = True,
    quality_status: str = "ok",
    warning: str | None = None,
    explanation: str | None = None,
    confidence_interval: dict[str, float | None] | None = None,
    population_id: str | None = None,
    numerator: float | int | None = None,
    denominator: float | int | None = None,
    status_reason: str | None = None,
) -> dict[str, Any]:
    """A metric plus everything needed to say WHAT it counted."""
    return {
        "value": value,
        "unit": unit,
        "semantic_type": semantic_type,
        "scope": scope,
        # §29: the population and its cardinality, not just a percentage.
        "population_id": population_id,
        "numerator": numerator,
        "denominator": denominator,
        "window_hours": window_hours,
        "since": since,
        "until": until,
        "window_start_utc": since,
        "window_end_utc": until,
        "generated_at": generated_at,
        "computed_at_utc": generated_at,
        "source_artifact": str(source_artifact),
        "source_artifact_sha256": artifact_sha256(source_artifact),
        "source_artifact_updated_at": artifact_updated_at(source_artifact),
        "code_sha": code_sha(),
        "config_sha256": config_sha256(),
        "stale_status": artifact_stale_status(source_artifact),
        "sample_size": sample_size,
        "confidence_interval": confidence_interval,
        "is_decision_relevant": is_decision_relevant,
        "is_read_only": is_read_only,
        "quality_status": quality_status,
        "status": quality_status,
        "status_reason": status_reason or warning,
        "warning": warning,
        "explanation": explanation,
    }
