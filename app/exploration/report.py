"""Coverage report — the actual product of the exploration phase.

Reads the normalized JSONL captures and aggregates, per probe_id:
  - runs / successful runs / failed runs (a run = one distinct fetched_at)
  - total records captured
  - field coverage: union of record keys + per-field non-null rate
  - latency (median over runs)
  - distinct errors observed
  - a heuristic verdict: GO / CONDITIONAL / NO-GO

Output: coverage_report.json (machine) + coverage_report.md (operator).
This is read-only over artifacts — it mutates no source state.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _load_lines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        logger.warning("[exploration] could not read %s: %s", path, exc)
    return rows


def _verdict(
    *,
    runs: int,
    success_runs: int,
    record_count: int,
    field_count: int,
    error_count: int,
) -> tuple[str, str]:
    """Heuristic GO/CONDITIONAL/NO-GO with a one-line reason."""
    if runs == 0:
        return "NO-GO", "never ran"
    success_rate = success_runs / runs
    if success_runs == 0 or record_count == 0:
        return "NO-GO", "no successful run with usable records"
    if success_rate >= 0.8 and field_count >= 3 and error_count == 0:
        return "GO", f"stable ({success_rate:.0%} ok), {field_count} fields"
    return (
        "CONDITIONAL",
        f"usable but {success_rate:.0%} ok / {field_count} fields / {error_count} errors",
    )


def build_report(*, artifacts_dir: str) -> dict[str, Any]:
    """Aggregate all normalized captures into a structured report dict."""
    normalized_dir = Path(artifacts_dir) / "normalized"
    sources: dict[str, dict[str, Any]] = {}

    jsonl_files = sorted(normalized_dir.glob("*.jsonl")) if normalized_dir.exists() else []

    for path in jsonl_files:
        rows = _load_lines(path)
        if not rows:
            continue
        probe_id = rows[0].get("probe_id") or path.stem.replace("__", ":")

        runs_by_ts: dict[str, dict[str, Any]] = {}
        field_present: dict[str, int] = defaultdict(int)
        field_nonnull: dict[str, int] = defaultdict(int)
        record_count = 0
        errors: dict[str, int] = defaultdict(int)

        for row in rows:
            ts = row.get("fetched_at") or "?"
            run = runs_by_ts.setdefault(
                ts,
                {
                    "success": bool(row.get("success")),
                    "error": row.get("error"),
                    "latency_ms": (row.get("meta") or {}).get("latency_ms"),
                },
            )
            if row.get("error"):
                errors[str(row.get("error"))] += 1
            record = row.get("record")
            if isinstance(record, dict):
                record_count += 1
                for key, value in record.items():
                    field_present[key] += 1
                    if value is not None:
                        field_nonnull[key] += 1
            _ = run  # marker only

        runs = len(runs_by_ts)
        success_runs = sum(1 for r in runs_by_ts.values() if r["success"])
        latencies = [
            r["latency_ms"]
            for r in runs_by_ts.values()
            if isinstance(r["latency_ms"], (int, float))
        ]
        median_latency = round(statistics.median(latencies), 1) if latencies else None

        coverage = {
            key: {
                "present_in_records": field_present[key],
                "non_null": field_nonnull[key],
                "non_null_pct": (
                    round(field_nonnull[key] / record_count * 100, 1) if record_count else 0.0
                ),
            }
            for key in sorted(field_present)
        }

        verdict, reason = _verdict(
            runs=runs,
            success_runs=success_runs,
            record_count=record_count,
            field_count=len(coverage),
            error_count=len(errors),
        )

        sources[probe_id] = {
            "probe_id": probe_id,
            "runs": runs,
            "success_runs": success_runs,
            "failed_runs": runs - success_runs,
            "record_count": record_count,
            "field_count": len(coverage),
            "median_latency_ms": median_latency,
            "fields": coverage,
            "errors": dict(errors),
            "verdict": verdict,
            "verdict_reason": reason,
        }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "artifacts_dir": str(artifacts_dir),
        "probe_count": len(sources),
        "sources": sources,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Source-Intake Coverage Report")
    lines.append("")
    lines.append(f"_Generated: {report['generated_at']}_  ")
    lines.append(f"_Artifacts: `{report['artifacts_dir']}`  ·  probes: {report['probe_count']}_")
    lines.append("")
    if not report["sources"]:
        lines.append("No captures found yet. Run `python -m app.exploration.cli run` first.")
        return "\n".join(lines) + "\n"

    lines.append("## Summary")
    lines.append("")
    lines.append("| Probe | Verdict | Runs (ok) | Records | Fields | Median latency | Reason |")
    lines.append("|---|---|---|---|---|---|---|")
    for probe_id in sorted(report["sources"]):
        s = report["sources"][probe_id]
        lat = f"{s['median_latency_ms']}ms" if s["median_latency_ms"] is not None else "–"
        lines.append(
            f"| `{probe_id}` | **{s['verdict']}** | {s['runs']} ({s['success_runs']}) "
            f"| {s['record_count']} | {s['field_count']} | {lat} | {s['verdict_reason']} |"
        )
    lines.append("")

    lines.append("## Per-probe detail")
    for probe_id in sorted(report["sources"]):
        s = report["sources"][probe_id]
        lines.append("")
        lines.append(f"### `{probe_id}` — {s['verdict']}")
        lines.append("")
        if s["fields"]:
            lines.append("| Field | non-null % | non-null / present |")
            lines.append("|---|---|---|")
            for field_name in sorted(s["fields"]):
                f = s["fields"][field_name]
                lines.append(
                    f"| `{field_name}` | {f['non_null_pct']}% "
                    f"| {f['non_null']}/{f['present_in_records']} |"
                )
        else:
            lines.append("_No record fields captured._")
        if s["errors"]:
            lines.append("")
            lines.append("**Errors observed:**")
            for err, count in sorted(s["errors"].items(), key=lambda kv: -kv[1]):
                lines.append(f"- `{err}` ×{count}")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_report(*, artifacts_dir: str) -> tuple[Path, Path]:
    """Build + write coverage_report.json and coverage_report.md. Returns both paths."""
    report = build_report(artifacts_dir=artifacts_dir)
    out_dir = Path(artifacts_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "coverage_report.json"
    md_path = out_dir / "coverage_report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
