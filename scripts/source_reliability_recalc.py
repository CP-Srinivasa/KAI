#!/usr/bin/env python3
"""Recompute source-reliability scores and write monitor/source_reliability.json.

Goal-pin 2026-05-16 V1: closes the source-reliability feedback loop.

Loads:
- ``artifacts/alert_audit.jsonl`` (dispatched alerts with source_name + timestamps)
- ``artifacts/alert_outcomes.jsonl`` (operator outcome annotations)

Computes:
- Per-source Wilson Lower Bound 95% over a 90-day window via
  ``app.learning.source_reliability.build_source_reliability_report``.

Writes:
- ``monitor/source_reliability.json`` with tiers and priority modifiers.

Idempotent: re-runs overwrite the file. The eligibility filter reads it
with mtime-cache (see ``app.alerts.eligibility._load_source_reliability_modifiers``)
so no service restart is needed. Intended to run from a daily systemd
timer; first version is operator-triggerable from the CLI for evaluation.

Exit codes:
- 0 success
- 1 input files missing or unreadable
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.alerts.audit import (  # noqa: E402
    load_alert_audits,
    load_outcome_annotations,
)
from app.learning.source_reliability import build_source_reliability_report  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("source-reliability-recalc")


def _resolve_monitor_dir() -> Path:
    """Use settings.monitor_dir when available, else relative monitor/."""
    try:
        from app.core.settings import get_settings

        return Path(get_settings().monitor_dir)
    except Exception:  # noqa: BLE001 — settings not available (CI, fresh repo)
        return Path("monitor")


def _build_source_by_doc_from_audits(audit_records: list) -> dict[str, str]:
    """Extract source_name per document_id directly from audit records.

    The audit-stream is the canonical source-of-truth — DB lookups would
    duplicate this information and risk drift. Last-seen source wins per
    document_id (matches feature_analysis dedup semantics).
    """
    out: dict[str, str] = {}
    for rec in audit_records:
        if not rec.source_name:
            continue
        out[rec.document_id] = rec.source_name
    return out


def main() -> int:
    audit_path = _REPO_ROOT / "artifacts" / "alert_audit.jsonl"
    outcomes_path = _REPO_ROOT / "artifacts" / "alert_outcomes.jsonl"

    if not audit_path.exists():
        logger.error("alert_audit.jsonl missing at %s", audit_path)
        return 1
    if not outcomes_path.exists():
        logger.error("alert_outcomes.jsonl missing at %s", outcomes_path)
        return 1

    audits = load_alert_audits(audit_path)
    annotations = load_outcome_annotations(outcomes_path)
    source_by_doc = _build_source_by_doc_from_audits(audits)

    report = build_source_reliability_report(audits, annotations, source_by_doc)

    out_path = _resolve_monitor_dir() / "source_reliability.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(out_path)  # atomic on POSIX

    score_count = len(report.get("scores", {}))
    tier_counts: dict[str, int] = {}
    for entry in report.get("scores", {}).values():
        tier = entry.get("tier", "?")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    logger.info(
        "wrote %s sources=%d tiers=%s",
        out_path,
        score_count,
        tier_counts,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
