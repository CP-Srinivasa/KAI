#!/usr/bin/env python3
"""Source-lifecycle recalc — rank sources, detect silence, flag rotation.

Phase 2 of the source-lifecycle system (operator goal 2026-06-23). This engine
DETECTS + RANKS + FLAGS; it does NOT yet autonomously rotate (that is Phase 3 /
PR5). It is deliberately DB-free: all state lives in append-only artifacts, so a
recalc run is idempotent and side-effect-free beyond two files:

- ``monitor/source_ranking.json``  — the deterministic Top-N ranking + per-source
  lifecycle flags (silent / pinned / rotation_flagged). A ``consecutive_top_runs``
  counter is carried forward across runs so pinning is evidence-backed over time,
  not a single lucky run.
- ``artifacts/source_lifecycle_audit.jsonl`` — one append-only line per logical
  status transition (active↔silent, →pinned). Every emitted (from → to) pair is
  routed through the lifecycle FSM, so an illegal direct jump (e.g. pinned→silent)
  is decomposed into its legal path (pinned→active→silent) rather than recording
  an impossible transition. This append IS the accountability behind "only logging".

Safety rails honoured (KAI §5 / ADR-0006):
- Rail 5 (fail-closed trust): provisional sources (n < validated floor) rank but
  never pin and never earn an eligibility boost.
- Rail 6 (no execution authority): writes ranking + audit only; touches no risk
  gate, entry mode, or eligibility modifier.
- Rail 7 (audit duty): every transition is appended before the run returns.
- Rail 8 (replace-only-when-ready): rotation is only FLAGGED here; the archival
  swap is Phase 3 and never happens without a graduated replacement.

Exit codes: 0 success · 1 input files missing/unreadable.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.alerts.audit import (  # noqa: E402
    AlertAuditRecord,
    load_alert_audits,
    load_outcome_annotations,
)
from app.core.enums import SourceStatus  # noqa: E402
from app.learning.source_lifecycle import can_transition  # noqa: E402
from app.learning.source_lifecycle_audit import (  # noqa: E402
    LifecycleEvent,
    append_lifecycle_event,
)
from app.learning.source_reliability import (  # noqa: E402
    _MIN_N_FOR_DEMOTE,
    _WILSON_HIGH_THRESHOLD,
    _parse_iso,
    _resolve_record_source,
    build_source_reliability_report,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("source-lifecycle-recalc")

# --- Engine policy (all conservative; the mechanism is present but stays inert
#     until enough data accrues — see test_source_lifecycle_recalc). ---

# A source with no directional dispatch within this window is "silent".
SILENT_AFTER_DAYS: int = 7
# Consecutive top-tier runs required before a source can be pinned.
PIN_MIN_CONSECUTIVE_RUNS: int = 3
# A source must rank within this position (and be non-silent) to accrue a pin run.
PIN_RANK_THRESHOLD: int = 10
# Reliability tiers that mark a source as a replacement candidate.
_ROTATION_TIERS: frozenset[str] = frozenset({"low", "watch"})


def _resolve_monitor_dir() -> Path:
    """Use settings.monitor_dir when available, else relative monitor/."""
    try:
        from app.core.settings import get_settings

        return Path(get_settings().monitor_dir)
    except Exception:  # noqa: BLE001 — settings not available (CI, fresh repo)
        return Path("monitor")


def _build_source_by_doc_from_audits(audits: list[AlertAuditRecord]) -> dict[str, str]:
    """doc_id → flat source_name from the audit stream (last-seen wins).

    Only the flat field is collected here; ``_resolve_record_source`` then falls
    back to ``provenance.source`` per row, so the provenance-attribution fix
    (PR1/PR2) carries through without re-walking the DB.
    """
    out: dict[str, str] = {}
    for rec in audits:
        if rec.source_name:
            out[rec.document_id] = rec.source_name
    return out


def _last_signal_by_source(
    audits: list[AlertAuditRecord], source_by_doc: dict[str, str]
) -> dict[str, datetime]:
    """Most recent directional dispatch timestamp per resolved source.

    Signal-level silence detection (operator request): a source is judged by when
    it last *acted* (dispatched a directional alert), not by raw ingestion. Digest
    rows are excluded — they are not source-attributable signals.
    """
    out: dict[str, datetime] = {}
    for rec in audits:
        if rec.is_digest:
            continue
        source = _resolve_record_source(rec, source_by_doc)
        if not source:
            continue
        dispatched = _parse_iso(rec.dispatched_at)
        if dispatched is None:
            continue
        prev = out.get(source)
        if prev is None or dispatched > prev:
            out[source] = dispatched
    return out


def _logical_status(*, silent: bool, pinned: bool) -> SourceStatus:
    """Map the engine's observed flags to a single logical lifecycle status.

    Precedence: pinned (a pinned source is by construction recently active) >
    silent > active. ``probation``/``archived`` are never produced here — those
    are Phase 3 status mutations, not observational flags.
    """
    if pinned:
        return SourceStatus.PINNED
    if silent:
        return SourceStatus.SILENT
    return SourceStatus.ACTIVE


def _legal_path(current: SourceStatus, target: SourceStatus) -> list[SourceStatus]:
    """FSM-legal sequence of statuses from ``current`` to ``target`` (≤2 hops).

    Returns the list of intermediate+final statuses to record. Direct transitions
    return ``[target]``; an illegal direct jump (e.g. pinned→silent) is decomposed
    into its single-hop bridge (pinned→active→silent). Returns ``[]`` if no path of
    length ≤2 exists (logged and skipped — never a fabricated transition).
    """
    if current == target:
        return []
    if can_transition(current, target):
        return [target]
    for mid in SourceStatus:
        if mid in (current, target):
            continue
        if can_transition(current, mid) and can_transition(mid, target):
            return [mid, target]
    return []


def _load_prior_ranking(path: Path) -> dict[str, dict[str, Any]]:
    """Prior run's per-source state, keyed by source_name (empty on first run)."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("prior source_ranking.json unreadable; treating as first run")
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in payload.get("ranked", []):
        name = entry.get("source_name")
        if isinstance(name, str):
            out[name] = entry
    return out


def _prior_status(prior_entry: dict[str, Any] | None) -> SourceStatus:
    """Logical status from a prior entry, defaulting to ACTIVE for new sources."""
    if not prior_entry:
        return SourceStatus.ACTIVE
    raw = prior_entry.get("logical_status")
    if not isinstance(raw, str):
        return SourceStatus.ACTIVE
    try:
        return SourceStatus(raw)
    except ValueError:
        return SourceStatus.ACTIVE


def build_lifecycle_ranking(
    ranked: list[dict[str, Any]],
    last_signal: dict[str, datetime],
    prior: dict[str, dict[str, Any]],
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int], list[LifecycleEvent]]:
    """Pure core: enrich the reliability ranking with lifecycle flags + events.

    Given the deterministic ``ranked`` list (from
    ``build_source_reliability_report``), each source's last directional signal
    time, and the prior run's per-source state, computes for every source:
    ``silent`` / ``pinned`` / ``rotation_flagged`` flags, a forward-carried
    ``consecutive_top_runs`` counter, and the resulting logical status — then
    emits one FSM-legal ``LifecycleEvent`` per status hop when the status changed.
    No I/O: deterministic in (inputs, now), so it is fully unit-testable.
    """
    silent_cutoff = now - timedelta(days=SILENT_AFTER_DAYS)
    out_ranked: list[dict[str, Any]] = []
    counts = {
        "ranked": 0,
        "provisional": 0,
        "validated": 0,
        "silent": 0,
        "pinned": 0,
        "rotation_flagged": 0,
    }
    events: list[LifecycleEvent] = []

    for entry in ranked:
        source = str(entry["source_name"])
        rank = int(entry["rank"])
        n = int(entry["n"])
        provisional = bool(entry["provisional"])
        wilson = entry.get("wilson_lower_95")
        reliability_tier = str(entry.get("reliability_tier", "insufficient"))

        last = last_signal.get(source)
        silent = last is None or last < silent_cutoff

        prior_entry = prior.get(source)
        prior_runs = int((prior_entry or {}).get("consecutive_top_runs", 0) or 0)
        is_top = (rank <= PIN_RANK_THRESHOLD) and not silent
        consecutive_top_runs = prior_runs + 1 if is_top else 0

        pinned = (
            consecutive_top_runs >= PIN_MIN_CONSECUTIVE_RUNS
            and not provisional
            and isinstance(wilson, (int, float))
            and float(wilson) >= _WILSON_HIGH_THRESHOLD
        )
        if pinned:
            silent = False  # a pinned source is recently active by construction

        rotation_flagged = (not pinned) and (
            silent or (n >= _MIN_N_FOR_DEMOTE and reliability_tier in _ROTATION_TIERS)
        )

        new_status = _logical_status(silent=silent, pinned=pinned)
        old_status = _prior_status(prior_entry)

        last_iso = last.astimezone(UTC).isoformat() if last is not None else None
        out_ranked.append(
            {
                **entry,
                "silent": silent,
                "pinned": pinned,
                "rotation_flagged": rotation_flagged,
                "consecutive_top_runs": consecutive_top_runs,
                "logical_status": new_status.value,
                "last_signal_at": last_iso,
            }
        )

        counts["ranked"] += 1
        counts["provisional"] += int(provisional)
        counts["validated"] += int(not provisional)
        counts["silent"] += int(silent)
        counts["pinned"] += int(pinned)
        counts["rotation_flagged"] += int(rotation_flagged)

        if new_status != old_status:
            path = _legal_path(old_status, new_status)
            if not path:
                logger.warning(
                    "no legal lifecycle path %s -> %s for %s; not recorded",
                    old_status.value,
                    new_status.value,
                    source,
                )
                continue
            frm = old_status
            for hop in path:
                events.append(
                    LifecycleEvent(
                        source=source,
                        from_status=frm.value,
                        to_status=hop.value,
                        reason="lifecycle_recalc",
                        recorded_at_utc=now.isoformat(),
                        evidence={
                            "rank": rank,
                            "n": n,
                            "wilson_lower_95": wilson,
                            "provisional": provisional,
                            "reliability_tier": reliability_tier,
                            "rotation_flagged": rotation_flagged,
                        },
                    )
                )
                frm = hop

    return out_ranked, counts, events


def main() -> int:
    audit_path = _REPO_ROOT / "artifacts" / "alert_audit.jsonl"
    outcomes_path = _REPO_ROOT / "artifacts" / "alert_outcomes.jsonl"
    if not audit_path.exists():
        logger.error("alert_audit.jsonl missing at %s", audit_path)
        return 1
    if not outcomes_path.exists():
        logger.error("alert_outcomes.jsonl missing at %s", outcomes_path)
        return 1

    now = datetime.now(UTC)

    audits = load_alert_audits(audit_path)
    annotations = load_outcome_annotations(outcomes_path)
    source_by_doc = _build_source_by_doc_from_audits(audits)

    report = build_source_reliability_report(audits, annotations, source_by_doc, now_utc=now)
    raw_ranked = report.get("ranked", [])
    ranked: list[dict[str, Any]] = raw_ranked if isinstance(raw_ranked, list) else []
    last_signal = _last_signal_by_source(audits, source_by_doc)

    monitor_dir = _resolve_monitor_dir()
    ranking_path = monitor_dir / "source_ranking.json"
    prior = _load_prior_ranking(ranking_path)
    audit_dir = _REPO_ROOT / "artifacts"

    out_ranked, counts, events = build_lifecycle_ranking(ranked, last_signal, prior, now)
    for event in events:
        append_lifecycle_event(event, audit_dir)
    transitions = len(events)

    payload = {
        "report_type": "source_ranking",
        "generated_at": now.isoformat(),
        "silent_after_days": SILENT_AFTER_DAYS,
        "pin_min_consecutive_runs": PIN_MIN_CONSECUTIVE_RUNS,
        "pin_rank_threshold": PIN_RANK_THRESHOLD,
        "counts": counts,
        "ranked": out_ranked,
    }

    ranking_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = ranking_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(ranking_path)  # atomic on POSIX

    logger.info(
        "wrote %s counts=%s transitions=%d",
        ranking_path,
        counts,
        transitions,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
