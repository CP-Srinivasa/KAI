#!/usr/bin/env python3
"""Size-based, tail-preserving audit-stream rotation (Sprint S5, 2026-06-11).

Archiving, never deleting: when an allowlisted ``artifacts/*.jsonl`` stream
exceeds its size threshold, the FULL file is moved to
``artifacts/archive/<stem>.<UTC-ts>.jsonl`` and a fresh live file is seeded
with the last ``keep_lines`` lines — so consumers that only need the recent
window (bridge TTL 24h, route limiter 1h/today, fillcheck tails) keep working
unchanged while history stays available in the archive.

HARD EXCLUSIONS (never rotate — documented in
docs/runbooks/repo_hygiene_policy.md):
- ``paper_execution_audit.jsonl`` — the PaperExecutionEngine REPLAYS this file
  to rebuild portfolio state on every one-shot; rotating it would wipe the
  paper book.
- ``blocked_outcomes.jsonl`` — D-227 reports aggregate the FULL history.
- ``shadow_candidate_ledger`` / bayes / alert outcome streams — learning and
  resolver state needs unresolved/backfill history.

Default-OFF activation: the systemd timer is installed but not enabled; the
script itself is also a no-op unless ``--apply`` is passed (dry-run prints the
plan), so an accidental manual invocation cannot rotate anything.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("audit-rotate")

_MB = 1024 * 1024


@dataclass(frozen=True)
class RotationRule:
    """One allowlisted stream: rotate above ``max_bytes``, keep ``keep_lines``."""

    filename: str
    max_bytes: int
    keep_lines: int
    rationale: str
    #: Zeitfenster statt fester Zeilenzahl (C5, 17.09.): bleiben alle Zeilen der
    #: letzten ``keep_hours`` im Live-File, hoechstens ``keep_lines``. Eine reine
    #: Zeilenzahl haengt an der Schreibrate — bei der /health-Flut (P0-2, ~44 000
    #: Zeilen/h) deckten 20 000 Zeilen nur 27 Minuten.
    keep_hours: float | None = None
    timestamp_key: str | None = None


# Conservative allowlist. A stream earns its place here ONLY when every known
# consumer needs at most a recent window (see module docstring for exclusions).
ROTATION_RULES: tuple[RotationRule, ...] = (
    RotationRule(
        filename="llm_telemetry.jsonl",
        max_bytes=20 * _MB,
        keep_lines=20_000,
        rationale="KAI CORE v1 (2026-09-02): both readers are windowed — "
        "llm_telemetry_summary (24h) and app/ai/health.py::ai_health_snapshot "
        "(window_hours); the per-attempt v2 rows multiply volume x2-3 "
        "(~220 calls/day on the Pi) — 20k lines is roughly a month",
    ),
    RotationRule(
        filename="bridge_pending_orders.jsonl",
        max_bytes=20 * _MB,
        keep_lines=5_000,
        rationale="bridge stage-lookup + trail join need the TTL window (24h); "
        "bridge rows average ~2.6KB (executable_intent payloads) and ~670 rows/day "
        "— 5k lines ≈ a week (first live run 2026-06-11: 20k > whole file)",
    ),
    RotationRule(
        filename="telegram_message_envelope.jsonl",
        max_bytes=20 * _MB,
        keep_lines=5_000,
        rationale="bridge pending-scan honours a 24h TTL; older envelopes are inert",
    ),
    RotationRule(
        filename="entry_watcher_audit.jsonl",
        max_bytes=20 * _MB,
        keep_lines=10_000,
        rationale="entry-range polling is forensic-recent; positions live in the engine",
    ),
    RotationRule(
        filename="api_request_audit.jsonl",
        max_bytes=20 * _MB,
        keep_lines=50_000,
        keep_hours=168,
        timestamp_key="timestamp_utc",
        rationale="HTTP request audit. Programmatic reader (corrected 2026-09-17): "
        "app/research/back_edge_evaluator.py reads the LIVE file (not archive/) "
        "for dashboard actions inside REACTION_WINDOW_HOURS=24 — so the live tail "
        "must cover a time window, not a line count: during the /health flood "
        "(P0-2, 16.09., ~44k lines/h) the old 20k-line tail covered 27 minutes. "
        "7 days (>= 7x the reaction window), capped at 50k lines so a new flood "
        "cannot turn the live file into the archive. Safe against the writer: "
        "open('a') per record, a rename is picked up on the next append",
    ),
)


@dataclass(frozen=True)
class RotationResult:
    filename: str
    rotated: bool
    reason: str
    size_bytes: int = 0
    archive_path: str | None = None
    kept_lines: int = 0


def rotate_stream(
    path: Path,
    *,
    max_bytes: int,
    keep_lines: int,
    archive_dir: Path,
    apply: bool,
    now: datetime | None = None,
    keep_hours: float | None = None,
    timestamp_key: str | None = None,
) -> RotationResult:
    """Rotate one stream if oversized. Archive-first, tail-preserving, atomic-ish:

    1. read the tail (last ``keep_lines`` lines),
    2. move the full file into the archive (rename — no data ever deleted),
    3. write the tail as the new live file.

    A crash between 2 and 3 leaves the full history safe in the archive and the
    live file absent — every consumer treats a missing audit file as empty and
    the producer recreates it on next append (fail-safe direction).
    """
    if not path.exists():
        return RotationResult(path.name, False, "missing")
    size = path.stat().st_size
    if size <= max_bytes:
        return RotationResult(path.name, False, "under_threshold", size_bytes=size)

    ts = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    archive_path = archive_dir / f"{path.stem}.{ts}{path.suffix}"
    if not apply:
        return RotationResult(
            path.name,
            False,
            f"dry_run_would_rotate_to:{archive_path.name}",
            size_bytes=size,
        )

    cutoff = None
    if keep_hours is not None and timestamp_key:
        cutoff = ((now or datetime.now(UTC)) - timedelta(hours=keep_hours)).isoformat()
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        tail: deque[str] = deque(maxlen=keep_lines)
        total_lines = 0
        for line in fh:
            total_lines += 1
            if cutoff is not None and _stamp_before(line, timestamp_key or "", cutoff):
                # Append-only und zeitsortiert: alles bisher Gesammelte ist
                # ebenfalls aelter als das Fenster.
                tail.clear()
                continue
            tail.append(line)

    # No-shrink guard (calibration finding, first live run 2026-06-11): when the
    # tail covers the WHOLE file, rotating would archive a full copy every run
    # without shrinking the live file — archive bloat instead of hygiene. Skip
    # and tell the operator to lower keep_lines for this stream.
    if len(tail) >= total_lines:
        return RotationResult(
            path.name,
            False,
            f"tail_covers_whole_file:lines={total_lines}<=keep_lines={keep_lines}"
            " — lower keep_lines/keep_hours for this stream",
            size_bytes=size,
        )

    archive_dir.mkdir(parents=True, exist_ok=True)
    path.rename(archive_path)
    with path.open("w", encoding="utf-8") as fh:
        fh.writelines(tail)
    return RotationResult(
        path.name,
        True,
        "rotated",
        size_bytes=size,
        archive_path=str(archive_path),
        kept_lines=len(tail),
    )


def _stamp_before(line: str, key: str, cutoff: str) -> bool:
    """True nur bei lesbarem Zeitstempel VOR ``cutoff`` — Unlesbares bleibt."""
    try:
        stamp = json.loads(line).get(key)
    except (ValueError, AttributeError):
        return False
    return isinstance(stamp, str) and stamp < cutoff


def run(artifacts_dir: Path, *, apply: bool) -> list[RotationResult]:
    archive_dir = artifacts_dir / "archive"
    results = []
    for rule in ROTATION_RULES:
        result = rotate_stream(
            artifacts_dir / rule.filename,
            max_bytes=rule.max_bytes,
            keep_lines=rule.keep_lines,
            archive_dir=archive_dir,
            apply=apply,
            keep_hours=rule.keep_hours,
            timestamp_key=rule.timestamp_key,
        )
        results.append(result)
        logger.info(
            "[audit-rotate] %s: %s (size=%.1fMB)%s",
            result.filename,
            result.reason,
            result.size_bytes / _MB,
            f" -> {result.archive_path} (kept {result.kept_lines} lines)" if result.rotated else "",
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Size-based audit-stream rotation (archiving)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually rotate; without this flag the run is a dry-run plan",
    )
    parser.add_argument("--artifacts-dir", default="artifacts")
    args = parser.parse_args(argv)
    try:
        results = run(Path(args.artifacts_dir), apply=args.apply)
    except Exception:  # noqa: BLE001 — entrypoint boundary
        logger.exception("[audit-rotate] unexpected error")
        return 1
    print(json.dumps([r.__dict__ for r in results], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
