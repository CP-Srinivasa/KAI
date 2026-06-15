"""Capture writers for the exploration sandbox.

Two artifacts per probe run:
  - raw:        artifacts/exploration/raw/<probe_id>/<timestamp>.json
                The unmodified payload (audit / later re-analysis). One file per run.
  - normalized: artifacts/exploration/normalized/<probe_id>.jsonl
                One JSONL line per record (or a single envelope line on failure /
                empty success), so report.py can aggregate field coverage uniformly.

probe_id ("coinglass:api") is filesystem-sanitised to "coinglass__api".
All writes are best-effort and never raise into the runner.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.exploration.base import ExplorationResult

logger = logging.getLogger(__name__)


def _safe_probe_dir(probe_id: str) -> str:
    return probe_id.replace(":", "__").replace("/", "_")


def _json_default(obj: Any) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def write_raw(result: ExplorationResult, *, artifacts_dir: str) -> Path | None:
    """Write the raw payload for one run. Returns the path or None on error."""
    try:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")
        out_dir = Path(artifacts_dir) / "raw" / _safe_probe_dir(result.probe_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ts}.json"
        payload = {
            "envelope": result.to_envelope(),
            "raw": result.raw,
        }
        out_path.write_text(
            json.dumps(payload, indent=2, default=_json_default, ensure_ascii=False),
            encoding="utf-8",
        )
        return out_path
    except Exception as exc:  # noqa: BLE001 — capture must never crash the runner
        logger.warning("[exploration] raw capture failed for %s: %s", result.probe_id, exc)
        return None


def append_normalized(result: ExplorationResult, *, artifacts_dir: str) -> Path | None:
    """Append normalized JSONL lines for one run. Returns the path or None on error.

    On success with records: one line per record, each wrapped with the envelope.
    On success-but-empty or failure: a single envelope line with record=None — so
    the report can still count the run and its outcome.
    """
    try:
        out_dir = Path(artifacts_dir) / "normalized"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{_safe_probe_dir(result.probe_id)}.jsonl"
        envelope = result.to_envelope()
        lines: list[str] = []
        if result.records:
            for rec in result.records:
                row = {**envelope, "record": rec}
                lines.append(json.dumps(row, default=_json_default, ensure_ascii=False))
        else:
            row = {**envelope, "record": None}
            lines.append(json.dumps(row, default=_json_default, ensure_ascii=False))
        with out_path.open("a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
        return out_path
    except Exception as exc:  # noqa: BLE001 — capture must never crash the runner
        logger.warning("[exploration] normalized capture failed for %s: %s", result.probe_id, exc)
        return None
