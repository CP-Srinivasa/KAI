"""Shared JSONL reader with retry-on-truncate (NEO-P-002 extended).

D-194 / NEO-F-META-20260424-029. The original retry-on-truncate policy
(NEO-P-002 D / D-156h) was inlined only in :mod:`app.alerts.audit` as
``_read_jsonl_tolerant``. Other JSONL reader call sites — in
:mod:`app.agents.worker`, :mod:`app.api.routers.agents`, and
:mod:`app.execution.envelope_to_paper_bridge` — silently drop a partial
last line when the reader races with a writer mid-append.

Under normal 10-minute-cron + polling-API load on the laptop we never
observed the race in practice, but:

* the cron frequency rises after Pi-migration (systemd timer + possibly
  several per-minute reads from the dashboard polling hook), and
* append-only writes on Windows do not guarantee POSIX append-atomicity,

so the defensive single-retry policy should apply to every reader of
append-only JSONL files. This module centralises the policy so a future
flip to e.g. ``filelock`` does not require touching each call site.

Public API:

* :func:`read_jsonl_tolerant` — the canonical entry point.
* :func:`RETRY_SLEEP_SECONDS` — policy constant kept as module attribute
  so tests can monkey-patch it without touching import-order edge cases.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# 100 ms retry delay — chosen in NEO-P-002 D after empirical observation
# that a writer's fsync-plus-append takes < 50 ms on SSD; 100 ms keeps the
# latency impact on readers well below the 1 s cron-tick budget.
RETRY_SLEEP_SECONDS: float = 0.1


def read_jsonl_tolerant(
    path: Path,
    *,
    tail: int | None = None,
    dict_only: bool = True,
) -> list[dict[str, Any]]:
    """Read JSON objects from a JSONL file with one retry on partial tail.

    Policy
    ------
    * Missing file → empty list (callers always treat that as "no rows yet").
    * Mid-file :class:`json.JSONDecodeError` → silently skipped (legacy
      behaviour; mid-file corruption is rare with append-only writes).
    * Last non-empty line fails to decode → sleep
      ``RETRY_SLEEP_SECONDS`` and re-read the whole file once. On the
      second failure the line is dropped. Closes the reader-vs-writer race
      identified in NEO-P-002 D (D-156h).

    Parameters
    ----------
    path:
        File to read. The caller is responsible for resolving relative
        paths against the project root.
    tail:
        If set, return only the last *N* rows. Implemented after the
        parse to keep mid-file skip-semantics stable regardless of
        ``tail``.
    dict_only:
        When ``True`` (default), non-dict JSON values (arrays, strings,
        ``null``) are dropped. This preserves the legacy semantics of the
        three call sites migrated in D-194. Set to ``False`` only when a
        JSONL file is known to contain non-object records.

    Returns
    -------
    list[dict[str, Any]]
        Parsed records in file order.
    """

    if not path.exists():
        return []

    def _parse(text: str) -> tuple[list[dict[str, Any]], bool]:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return [], False
        records: list[dict[str, Any]] = []
        last_idx = len(lines) - 1
        last_failed = False
        for idx, line in enumerate(lines):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                if idx == last_idx:
                    last_failed = True
                continue
            if dict_only and not isinstance(obj, dict):
                continue
            records.append(obj)
        return records, last_failed

    records, last_failed = _parse(path.read_text(encoding="utf-8"))
    if last_failed:
        time.sleep(RETRY_SLEEP_SECONDS)
        records, _ = _parse(path.read_text(encoding="utf-8"))

    if tail is None:
        return records
    if tail <= 0:
        # ``tail=0`` means "last zero rows" — explicit empty slice,
        # matching caller expectations. ``records[-0:]`` would yield the
        # whole list because of Python's ``-0 == 0``.
        return []
    return records[-tail:]
