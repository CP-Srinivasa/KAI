#!/usr/bin/env python3
"""Recompute source-confluence observations and write the audit stream.

Goal-pin 2026-05-16 V3 (shadow-only): for every directional dispatched alert
in ``artifacts/alert_audit.jsonl`` we record how many OTHER independent
sources reported the same (asset, direction) within a backward-looking
60-minute window. The result lands in
``artifacts/source_confluence_audit.jsonl`` as one line per (document, asset).

The SignalGenerator and the eligibility filter do NOT read this file —
this is a pure operator-facing observation stream. After 7+ days of data
we'll measure ``confluence_count``-vs-forward-outcome correlation and
THEN decide whether to wire it into signal generation.

Idempotent: re-runs OVERWRITE the audit stream (we re-compute from scratch).
First-run is operator-CLI; systemd timer wiring follows after the
score-vs-outcome curve is measured.

Exit codes:
- 0 success
- 1 input file missing or unreadable
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

from app.alerts.audit import load_alert_audits  # noqa: E402
from app.analysis.source_confluence import (  # noqa: E402
    DEFAULT_WINDOW_SECONDS,
    compute_confluence,
    summarize_confluence,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("source-confluence-recalc")


def main() -> int:
    audit_path = _REPO_ROOT / "artifacts" / "alert_audit.jsonl"
    if not audit_path.exists():
        logger.error("alert_audit.jsonl missing at %s", audit_path)
        return 1

    audits = load_alert_audits(audit_path)
    window_seconds = int(
        os.environ.get("KAI_CONFLUENCE_WINDOW_SECONDS", str(DEFAULT_WINDOW_SECONDS))
    )

    observations = compute_confluence(audits, window_seconds=window_seconds)
    summary = summarize_confluence(observations)

    out_path = _REPO_ROOT / "artifacts" / "source_confluence_audit.jsonl"
    tmp_path = out_path.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        for obs in observations:
            fh.write(json.dumps(obs.to_json_dict(), ensure_ascii=False) + "\n")
    tmp_path.replace(out_path)  # atomic on POSIX

    summary_path = _REPO_ROOT / "artifacts" / "source_confluence_summary.json"
    summary_payload = {
        "schema_version": "v1",
        "report_type": "source_confluence_summary",
        "window_seconds": window_seconds,
        **summary,
    }
    summary_path.write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    logger.info(
        "wrote %s n=%d distribution=%s",
        out_path,
        summary["n_observations"],
        summary["distribution"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
