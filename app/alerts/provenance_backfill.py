"""Backfill persisted provenance into legacy alert_audit + alert_outcomes JSONL.

D-125 / SAT-C-PROV-20260422-001 — pre-backfill, both files carry rows
without the ``provenance`` nested dict because the writers only started
populating it on 2026-04-22. This module rewrites each file in-place with
each row augmented by a best-effort ``SignalProvenance``:

- TV-bridge rows (``document_id`` startswith ``tv:``) get
  ``source="tradingview_webhook"``, ``version="tv-3"`` (legacy default),
  ``signal_path_id`` from the matching pending-signal row when available,
  ``ingest_event_id`` from the doc_id suffix. ``auth_method`` is left None
  (the pending file did not propagate it pre-pivot — see V8 follow-up).

- RSS rows get ``source`` from the originating CanonicalDocumentModel via
  the same DB join that ``provenance_metrics`` used to do at analysis
  time, ``version="rss-1"`` (pre-pivot tag), ``auth_method="n/a"``,
  ``ingest_event_id=document_id``. Rows whose document is purged from the
  DB (D-139 legacy/test batches) get ``source="unknown"`` so the source
  gate keeps blocking them — same policy as ``_load_doc_metadata``.

- Rows that already carry ``provenance`` are untouched (idempotent).

A timestamped ``.bak`` copy is taken before rewrite. The rewrite is
write-temp-then-rename for atomicity. mtime is checked pre/post rewrite
on each source file as a cheap concurrent-write guard — if the file
changed during backfill, the rewrite aborts and the original is kept.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from app.signals.models import SignalProvenance

log = structlog.get_logger(__name__)

_TV_DOC_PREFIX = "tv:"
_TV_LEGACY_VERSION = "tv-3"
_RSS_LEGACY_VERSION = "rss-1"
_TV_SOURCE = "tradingview_webhook"
_RSS_AUTH_METHOD = "n/a"


def _load_tv_signal_path_map(tv_pending_path: Path) -> dict[str, str]:
    """Return {event_id: signal_path_id} from the TV pending-signals file."""
    out: dict[str, str] = {}
    if not tv_pending_path.exists():
        return out
    for raw in tv_pending_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_id = event.get("event_id")
        prov = event.get("provenance") or {}
        spid = prov.get("signal_path_id") if isinstance(prov, dict) else None
        if isinstance(event_id, str) and isinstance(spid, str):
            out[event_id] = spid
    return out


def _build_provenance_for_doc(
    doc_id: str,
    *,
    source_by_doc: dict[str, str],
    tv_signal_path_by_event: dict[str, str],
    secret: str,
) -> SignalProvenance | None:
    """Resolve a best-effort provenance for one row. Returns None if no source."""
    if doc_id.startswith(_TV_DOC_PREFIX):
        event_id = doc_id[len(_TV_DOC_PREFIX) :]
        return SignalProvenance(
            source=_TV_SOURCE,
            version=_TV_LEGACY_VERSION,
            signal_path_id=tv_signal_path_by_event.get(event_id),
            auth_method=None,
            ingest_event_id=event_id,
        ).with_hash(secret)

    source = source_by_doc.get(doc_id)
    if not source:
        return None
    return SignalProvenance(
        source=source,
        version=_RSS_LEGACY_VERSION,
        signal_path_id=None,
        auth_method=_RSS_AUTH_METHOD,
        ingest_event_id=doc_id,
    ).with_hash(secret)


def _rewrite_jsonl_with_provenance(
    *,
    path: Path,
    source_by_doc: dict[str, str],
    tv_signal_path_by_event: dict[str, str],
    secret: str,
    dry_run: bool,
) -> dict[str, int]:
    """Augment each row of a JSONL with provenance. Returns counts."""
    counts = {"total": 0, "augmented": 0, "already_tagged": 0, "no_source": 0}
    if not path.exists():
        return counts

    pre_mtime = path.stat().st_mtime
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append(rec)
        counts["total"] += 1

    augmented_rows: list[dict[str, Any]] = []
    for rec in rows:
        if "provenance" in rec and isinstance(rec["provenance"], dict):
            counts["already_tagged"] += 1
            augmented_rows.append(rec)
            continue
        doc_id = rec.get("document_id")
        if not isinstance(doc_id, str):
            augmented_rows.append(rec)
            counts["no_source"] += 1
            continue
        prov = _build_provenance_for_doc(
            doc_id,
            source_by_doc=source_by_doc,
            tv_signal_path_by_event=tv_signal_path_by_event,
            secret=secret,
        )
        if prov is None:
            augmented_rows.append(rec)
            counts["no_source"] += 1
            continue
        rec["provenance"] = prov.to_dict()
        augmented_rows.append(rec)
        counts["augmented"] += 1

    if dry_run:
        return counts

    if path.stat().st_mtime != pre_mtime:
        log.warning(
            "provenance_backfill.aborted_concurrent_write",
            path=str(path),
        )
        counts["aborted_concurrent_write"] = 1
        return counts

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak.{timestamp}")
    shutil.copy2(path, backup)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for rec in augmented_rows:
            fh.write(json.dumps(rec) + "\n")
    tmp.replace(path)

    log.info(
        "provenance_backfill.written",
        path=str(path),
        backup=str(backup),
        **counts,
    )
    return counts


def backfill_provenance(
    *,
    artifacts_dir: Path,
    secret: str,
    source_by_doc: dict[str, str],
    dry_run: bool = False,
) -> dict[str, dict[str, int]]:
    """Backfill provenance into both alert_audit.jsonl and alert_outcomes.jsonl.

    The DB-resolved ``source_by_doc`` map must come from a one-time call
    to ``_load_doc_metadata`` (or equivalent) so this module stays
    independent of SQLAlchemy.

    Returns ``{file: counts}``. ``counts`` per file:
      total, augmented, already_tagged, no_source,
      aborted_concurrent_write (only on abort).
    """
    audit_path = artifacts_dir / "alert_audit.jsonl"
    outcomes_path = artifacts_dir / "alert_outcomes.jsonl"
    tv_pending_path = artifacts_dir / "tradingview_pending_signals.jsonl"

    tv_map = _load_tv_signal_path_map(tv_pending_path)

    return {
        "alert_audit.jsonl": _rewrite_jsonl_with_provenance(
            path=audit_path,
            source_by_doc=source_by_doc,
            tv_signal_path_by_event=tv_map,
            secret=secret,
            dry_run=dry_run,
        ),
        "alert_outcomes.jsonl": _rewrite_jsonl_with_provenance(
            path=outcomes_path,
            source_by_doc=source_by_doc,
            tv_signal_path_by_event=tv_map,
            secret=secret,
            dry_run=dry_run,
        ),
    }
