"""Intake-reject tombstones with cooldown (pure, file-based).

When the discovery scheduler's fail-closed intake gate rejects a candidate
(login/paywall/captcha/SSRF/malformed), nothing was persisted — so every scout
run re-proposed the same dead URLs and re-ran them through the gate. A tombstone
records each rejection with a cooldown window; while the cooldown is active the
candidate is skipped instead of re-gated. Expired tombstones are ignored, so a
site that later changes (e.g. drops its paywall) can be reconsidered.

Pure + file-based: append one JSONL line per rejection, read back the set of
URLs/domains still within cooldown. No network, no DB; the scheduler/scout own
the wiring. Reuses ``normalize_url`` so a tombstone keys on the same canonical
form the intake gate de-duplicates on.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from app.learning.source_intake_gate import normalize_url
from app.storage.jsonl_io import read_jsonl_tolerant

REJECTED_TOMBSTONE_FILENAME = "source_rejected_candidates.jsonl"
DEFAULT_COOLDOWN_DAYS = 30


def _resolve(path: Path) -> Path:
    return path / REJECTED_TOMBSTONE_FILENAME if path.is_dir() else path


def rejection_domain(normalized_url: str) -> str:
    """Bare lower-cased host of an already-normalized URL ("" if unparseable)."""
    try:
        return urlsplit(normalized_url).netloc.lower()
    except ValueError:
        return ""


def append_rejection_tombstone(
    path: Path,
    *,
    url: str,
    reason: str,
    now: datetime,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
) -> bool:
    """Append one rejection tombstone as a JSONL line.

    Returns ``False`` without writing for a malformed/empty URL (nothing to
    tombstone). Records both the normalized URL and its domain plus the cooldown
    window so a reader can decide skip-by-url or skip-by-domain.
    """
    normalized = normalize_url(url)
    if not normalized:
        return False
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "url": normalized,
        "domain": rejection_domain(normalized),
        "reason": reason,
        "rejected_at_utc": now.isoformat(),
        "cooldown_until_utc": (now + timedelta(days=cooldown_days)).isoformat(),
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return True


def load_active_rejections(path: Path, now: datetime) -> set[str]:
    """Return normalized URLs + domains whose cooldown is still active (``> now``).

    Expired tombstones are ignored (a changed site can be reconsidered). Corrupt /
    missing-field / unparseable-date lines are skipped — this never raises. Both
    the exact normalized URL and its domain are returned so a caller can skip
    either an exact re-proposal or any URL on a tombstoned domain.
    """
    target = _resolve(path)
    if not target.exists():
        return set()
    active: set[str] = set()
    for row in read_jsonl_tolerant(target):
        raw = row.get("cooldown_until_utc")
        if not isinstance(raw, str):
            continue
        try:
            until = datetime.fromisoformat(raw)
        except ValueError:
            continue
        try:
            still_active = until > now
        except TypeError:
            continue  # naive/aware mismatch — skip defensively
        if not still_active:
            continue
        url = row.get("url")
        domain = row.get("domain")
        if isinstance(url, str) and url:
            active.add(url)
        if isinstance(domain, str) and domain:
            active.add(domain)
    return active


def is_tombstoned(url: str, active: set[str]) -> bool:
    """True if ``url``'s normalized form OR its domain is in the active set."""
    normalized = normalize_url(url)
    if not normalized:
        return False
    return normalized in active or rejection_domain(normalized) in active


__all__ = [
    "DEFAULT_COOLDOWN_DAYS",
    "REJECTED_TOMBSTONE_FILENAME",
    "append_rejection_tombstone",
    "is_tombstoned",
    "load_active_rejections",
    "rejection_domain",
]
