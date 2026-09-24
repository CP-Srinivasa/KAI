"""Validate and freeze holdout adjudications without changing source evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "jev-adjudication/v1"
CASE_FIELDS = {"case_id", "final_label", "rule_ref", "decided_by", "decided_at", "rationale"}


def load_adjudication(path: Path) -> tuple[dict[str, Any], str]:
    data = path.read_bytes()
    document = json.loads(data)
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unknown adjudication schema")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("adjudication cases must be a non-empty list")
    default_decided_at = document.get("decided_at_utc")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(cases, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"case {index}: expected object")
        case_id = raw.get("case_id")
        decided_at = raw.get("decided_at", default_decided_at)
        row = {
            "case_id": case_id,
            "final_label": raw.get("final_label"),
            "rule_ref": raw.get("rule_ref"),
            "decided_by": raw.get("decided_by"),
            "decided_at": decided_at,
            "rationale": raw.get("rationale"),
        }
        if set(row) != CASE_FIELDS:
            raise AssertionError("internal adjudication schema mismatch")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError(f"case {index}: invalid or duplicate case_id")
        seen.add(case_id)
        if not isinstance(row["final_label"], bool):
            raise ValueError(f"case {case_id}: final_label must be boolean")
        for field in ("rule_ref", "decided_by", "decided_at", "rationale"):
            value = row[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"case {case_id}: {field} is required")
            row[field] = value.strip()
        row["case_id"] = case_id.strip() if isinstance(case_id, str) else case_id
        normalized.append(row)
    labels_sha256 = document.get("labels_sha256")
    if labels_sha256 is not None and (
        not isinstance(labels_sha256, str) or len(labels_sha256) != 64
    ):
        raise ValueError("labels_sha256 must be a sha256 hex digest")
    return {
        "schema_version": SCHEMA_VERSION,
        "labels_sha256": labels_sha256,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "case_count": len(normalized),
        "cases": normalized,
    }, hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        frozen, _ = load_adjudication(args.input)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(frozen, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
        print(json.dumps({"status": "ADJUDICATION_FROZEN", "case_count": frozen["case_count"]}))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INVALID_ADJUDICATION", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
