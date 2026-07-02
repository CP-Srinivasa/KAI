#!/usr/bin/env python3
"""Recompute ph5_feature_analysis.json from alert_audit + alert_outcomes.

Sprint 2026-05-24: closes the recalc-cycle loop. Until now feature-analysis
was only available via `python -m app.cli.main alerts analyze-resolved
--json-out artifacts/ph5_feature_analysis.json`, which required operator
manual trigger. This wrapper makes the call timer-friendly and writes the
full report with `--by all` + `--include-source` so the produced JSON has
all bucket dimensions including by_regime.

Loads:
- ``artifacts/alert_audit.jsonl``
- ``artifacts/alert_outcomes.jsonl``
- DB canonical_documents (via _load_doc_metadata) for source/title join

Writes:
- ``artifacts/ph5_feature_analysis.json`` with totals, buckets per
  dimension (sentiment, priority, priority-group, asset, source, regime).

Read-only on the source data. Idempotent (overwrites output).

Exit codes:
- 0 success
- 1 input files missing or unreadable
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("ph5_feature_analysis_recalc")


def main() -> int:
    from app.alerts.audit import load_alert_audits, load_outcome_annotations
    from app.alerts.feature_analysis import build_feature_analysis

    artifacts_dir = Path("artifacts")
    out_path = artifacts_dir / "ph5_feature_analysis.json"

    try:
        audits = load_alert_audits(artifacts_dir)
        annotations = load_outcome_annotations(artifacts_dir)
    except FileNotFoundError as exc:
        log.error("Input file missing: %s", exc)
        return 1
    except Exception as exc:
        log.exception("Failed to load inputs: %s", exc)
        return 1

    log.info("Loaded %d audits, %d annotations", len(audits), len(annotations))

    source_by_doc: dict[str, str] | None = None
    title_by_doc: dict[str, str] | None = None
    try:
        from app.cli.main import _load_doc_metadata

        source_by_doc, title_by_doc = _load_doc_metadata(audits)
        source_by_doc = source_by_doc or None
        title_by_doc = title_by_doc or None
    except Exception as exc:
        # DB-join is optional — feature_analysis works without source-map.
        log.warning("DB metadata join skipped: %s", exc)

    report = build_feature_analysis(
        audits=audits,
        annotations=annotations,
        source_by_doc=source_by_doc,
        title_by_doc=title_by_doc,
        min_bucket_size=3,
    )

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(out_path)

    totals = report.get("totals", {})
    log.info(
        "Wrote %s: resolved=%s hits=%s miss=%s precision=%s%%",
        out_path,
        totals.get("resolved"),
        totals.get("hits"),
        totals.get("miss"),
        totals.get("precision_pct"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
