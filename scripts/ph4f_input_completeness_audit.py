"""PH4F Rule Input Completeness Audit Execution Script.

Sprint: PH4F_RULE_INPUT_COMPLETENESS_AUDIT
Contract: docs/contracts.md §74
Decision: D-68

Goal:
- Diagnose missing/empty/default rule input fields on the frozen PH4E paired set.
- Keep execution strictly diagnostic-only (no runtime/scoring/rule changes).

Inputs:
- artifacts/ph4a/candidate_rule.jsonl
- artifacts/ph4b/ph4b_tier3_shadow.jsonl

Outputs:
- artifacts/ph4f/ph4f_input_completeness_metrics.json
- artifacts/ph4f/ph4f_per_document_input_gaps.json
- artifacts/ph4f/ph4f_operator_summary.md
- artifacts/ph4f/execution_report.json

Usage:
    python scripts/ph4f_input_completeness_audit.py
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
ARTIFACTS_PH4A = PROJECT_ROOT / "artifacts" / "ph4a"
ARTIFACTS_PH4B = PROJECT_ROOT / "artifacts" / "ph4b"
ARTIFACTS_PH4F = PROJECT_ROOT / "artifacts" / "ph4f"

RULE_DATASET_PATH = ARTIFACTS_PH4A / "candidate_rule.jsonl"
TIER3_DATASET_PATH = ARTIFACTS_PH4B / "ph4b_tier3_shadow.jsonl"

FIELD_ORDER = [
    "relevance_score",
    "impact_score",
    "novelty_score",
    "actionable",
    "sentiment_label",
    "sentiment_score",
    "market_scope",
    "affected_assets",
    "tags",
]

DEFAULT_VALUE_MAP: dict[str, set[object]] = {
    "relevance_score": {0.0},
    "impact_score": {0.0, 0.05},
    "novelty_score": {0.0, 0.6},
    "sentiment_label": {"neutral"},
    "sentiment_score": {0.0},
    "market_scope": {"unknown"},
}


@dataclass(frozen=True)
class FieldStats:
    field: str
    missing_count: int
    empty_count: int
    default_count: int
    informative_count: int
    top_values: list[dict[str, object]]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "missing_count": self.missing_count,
            "empty_count": self.empty_count,
            "default_count": self.default_count,
            "informative_count": self.informative_count,
            "top_values": self.top_values,
        }


def load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)


def is_empty_value(field: str, value: object) -> bool:
    if value is None:
        return True
    if field in {"affected_assets", "tags"}:
        return isinstance(value, list) and len(value) == 0
    if field == "market_scope":
        return value in {"", "unknown"}
    if isinstance(value, str):
        return value.strip() == ""
    return False


def classify_field_state(field: str, payload: dict[str, object]) -> tuple[str, object]:
    if field not in payload:
        return ("missing", None)
    value = payload[field]
    if is_empty_value(field, value):
        return ("empty", value)
    defaults = DEFAULT_VALUE_MAP.get(field, set())
    try:
        if value in defaults:
            return ("default", value)
    except TypeError:
        # Non-hashable values (for example lists) cannot be checked in set membership.
        pass
    return ("informative", value)


def extract_assistant_payload(row: dict[str, object]) -> dict[str, object]:
    messages = row.get("messages")
    if not isinstance(messages, list):
        return {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        return json.loads(content)
    return {}


def get_paired_document_ids(tier3_rows: list[dict[str, object]]) -> set[str]:
    ids: set[str] = set()
    for row in tier3_rows:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            continue
        document_id = metadata.get("document_id")
        if isinstance(document_id, str) and document_id:
            ids.add(document_id)
    return ids


def build_field_stats(
    classified_rows: list[dict[str, str]],
    value_counters: dict[str, Counter[str]],
) -> list[FieldStats]:
    results: list[FieldStats] = []
    total = len(classified_rows)
    for field in FIELD_ORDER:
        missing = sum(1 for row in classified_rows if row[field] == "missing")
        empty = sum(1 for row in classified_rows if row[field] == "empty")
        default = sum(1 for row in classified_rows if row[field] == "default")
        informative = total - missing - empty - default
        top_values = [
            {"value": value_repr, "count": count}
            for value_repr, count in value_counters[field].most_common(5)
        ]
        results.append(
            FieldStats(
                field=field,
                missing_count=missing,
                empty_count=empty,
                default_count=default,
                informative_count=informative,
                top_values=top_values,
            )
        )
    return results


def main() -> None:
    generated_at = datetime.now(UTC).isoformat()
    rule_rows = load_jsonl(RULE_DATASET_PATH)
    tier3_rows = load_jsonl(TIER3_DATASET_PATH)
    paired_ids = get_paired_document_ids(tier3_rows)

    if not rule_rows or not tier3_rows or not paired_ids:
        write_json(
            ARTIFACTS_PH4F / "execution_report.json",
            {
                "report_type": "ph4f_execution_report",
                "phase": "PHASE 4",
                "sprint": "PH4F_RULE_INPUT_COMPLETENESS_AUDIT",
                "generated_at": generated_at,
                "status": "blocked",
                "reason": "missing_or_empty_input_artifacts",
                "inputs": {
                    "rule_dataset": str(RULE_DATASET_PATH),
                    "tier3_dataset": str(TIER3_DATASET_PATH),
                },
                "observations": {
                    "rule_rows": len(rule_rows),
                    "tier3_rows": len(tier3_rows),
                    "paired_count": len(paired_ids),
                },
            },
        )
        print("[BLOCKED] Missing PH4A/PH4B input artifacts for PH4F execution.")
        return

    classified_rows: list[dict[str, str]] = []
    doc_gap_rows: list[dict[str, object]] = []
    value_counters: dict[str, Counter[str]] = {field: Counter() for field in FIELD_ORDER}
    paired_count = 0

    for row in rule_rows:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            continue
        document_id = metadata.get("document_id")
        if not isinstance(document_id, str) or document_id not in paired_ids:
            continue

        payload = extract_assistant_payload(row)
        if not payload:
            continue
        paired_count += 1

        class_map: dict[str, str] = {}
        missing_fields: list[str] = []
        empty_fields: list[str] = []
        default_fields: list[str] = []
        informative_fields: list[str] = []

        for field in FIELD_ORDER:
            state, value = classify_field_state(field, payload)
            class_map[field] = state
            if state == "missing":
                missing_fields.append(field)
            elif state == "empty":
                empty_fields.append(field)
            elif state == "default":
                default_fields.append(field)
            else:
                informative_fields.append(field)
            if state != "missing":
                value_counters[field][repr(value)] += 1

        gap_score = len(missing_fields) + len(empty_fields) + len(default_fields)
        doc_gap_rows.append(
            {
                "document_id": document_id,
                "gap_score": gap_score,
                "missing_fields": missing_fields,
                "empty_fields": empty_fields,
                "default_fields": default_fields,
                "informative_fields": informative_fields,
            }
        )
        classified_rows.append(class_map)

    field_stats = build_field_stats(classified_rows, value_counters)

    pathway_counts = {
        "actionable_not_populated": sum(
            1 for row in classified_rows if row["actionable"] == "missing"
        ),
        "context_unknown_and_assetless": sum(
            1
            for row in classified_rows
            if row["market_scope"] in {"empty", "default"}
            and row["affected_assets"] == "empty"
        ),
        "keywordless_relevance_path": sum(
            1
            for row in classified_rows
            if row["relevance_score"] in {"empty", "default"}
            and row["tags"] == "empty"
        ),
        "impact_novelty_default_pair": sum(
            1
            for row in classified_rows
            if row["impact_score"] == "default" and row["novelty_score"] == "default"
        ),
    }
    top_pathways = sorted(
        (
            {
                "pathway": name,
                "count": count,
                "ratio": round((count / paired_count), 4) if paired_count else 0.0,
            }
            for name, count in pathway_counts.items()
        ),
        key=lambda item: item["count"],
        reverse=True,
    )[:3]

    metrics_payload: dict[str, object] = {
        "report_type": "ph4f_input_completeness_metrics",
        "phase": "PHASE 4",
        "sprint": "PH4F_RULE_INPUT_COMPLETENESS_AUDIT",
        "generated_at": generated_at,
        "contract_ref": "docs/contracts.md §74",
        "inputs": {
            "rule_dataset": str(RULE_DATASET_PATH),
            "tier3_dataset": str(TIER3_DATASET_PATH),
        },
        "counts": {
            "paired_count": paired_count,
            "tier3_document_ids": len(paired_ids),
            "rule_rows_in_dataset": len(rule_rows),
        },
        "field_stats": [item.to_json_dict() for item in field_stats],
        "pathway_counts": pathway_counts,
        "top_3_pathways": top_pathways,
    }

    doc_gap_rows.sort(key=lambda row: int(row["gap_score"]), reverse=True)

    write_json(ARTIFACTS_PH4F / "ph4f_input_completeness_metrics.json", metrics_payload)
    write_json(ARTIFACTS_PH4F / "ph4f_per_document_input_gaps.json", {"rows": doc_gap_rows})
    write_json(
        ARTIFACTS_PH4F / "execution_report.json",
        {
            "report_type": "ph4f_execution_report",
            "phase": "PHASE 4",
            "sprint": "PH4F_RULE_INPUT_COMPLETENESS_AUDIT",
            "generated_at": generated_at,
            "status": "completed",
            "next_required_step": "PH4F_RESULTS_REVIEW",
            "paired_count": paired_count,
            "top_3_pathways": top_pathways,
        },
    )

    summary_path = ARTIFACTS_PH4F / "ph4f_operator_summary.md"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8") as file:
        file.write("# PH4F Operator Summary\n\n")
        file.write(f"- Generated at (UTC): {generated_at}\n")
        file.write("- Sprint: PH4F_RULE_INPUT_COMPLETENESS_AUDIT\n")
        file.write(f"- Paired documents analyzed: {paired_count}\n\n")
        file.write("## Top-3 Missing Input Pathways\n\n")
        for idx, pathway in enumerate(top_pathways, start=1):
            pct = pathway["ratio"] * 100
            file.write(
                f"{idx}. `{pathway['pathway']}` — {pathway['count']} docs ({pct:.1f}%)\n"
            )
        file.write("\n## Field Completeness Snapshot\n\n")
        file.write("| Field | Missing | Empty | Default | Informative |\n")
        file.write("|---|---:|---:|---:|---:|\n")
        for stat in field_stats:
            file.write(
                f"| {stat.field} | {stat.missing_count} | {stat.empty_count} | "
                f"{stat.default_count} | {stat.informative_count} |\n"
            )

    print("PH4F execution complete.")
    print(f"Paired documents analyzed: {paired_count}")
    print("Top pathways:")
    for pathway in top_pathways:
        print(f"  - {pathway['pathway']}: {pathway['count']}")


if __name__ == "__main__":
    main()
