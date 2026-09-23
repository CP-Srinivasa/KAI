"""Join frozen holdout labels with a frozen baseline; never call Jev."""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read(path: Path) -> tuple[dict[str, Any], str]:
    data = path.read_bytes()
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError(f"{path.name} must contain an object")
    return parsed, hashlib.sha256(data).hexdigest()


def _content_hash(title: str, text: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", title + " " + text).casefold().split())
    return hashlib.sha256(normalized.encode()).hexdigest()


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(row["baseline_relevant"] and row["reference_relevant"] for row in cases)
    tn = sum(not row["baseline_relevant"] and not row["reference_relevant"] for row in cases)
    fp = sum(row["baseline_relevant"] and not row["reference_relevant"] for row in cases)
    fn = sum(not row["baseline_relevant"] and row["reference_relevant"] for row in cases)
    return {
        "case_count": len(cases),
        "reference_positive_count": tp + fn,
        "reference_negative_count": tn + fp,
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "accuracy": _ratio(tp + tn, len(cases)),
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "specificity": _ratio(tn, tn + fp),
        "false_negative_rate": _ratio(fn, tp + fn),
    }


def score(review_path: Path, mapping_path: Path, baseline_path: Path) -> dict[str, Any]:
    review, review_hash = _read(review_path)
    mapping, mapping_hash = _read(mapping_path)
    baseline, baseline_hash = _read(baseline_path)
    if review.get("schema_version") != "jev-holdout-blind-review/v1":
        raise ValueError("unknown review schema")
    if mapping.get("schema_version") != "jev-holdout-private-map/v1":
        raise ValueError("unknown mapping schema")
    if baseline.get("schema_version") != "jev-holdout-baseline/v1":
        raise ValueError("unknown baseline schema")
    pool_hash = review.get("pool_sha256")
    if not isinstance(pool_hash, str) or any(
        document.get("pool_sha256") != pool_hash for document in (mapping, baseline)
    ):
        raise ValueError("pool hash mismatch")
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("reviewer is missing")
    review_cases = review.get("cases")
    mapping_cases = mapping.get("cases")
    baseline_cases = baseline.get("cases")
    if not isinstance(review_cases, list):
        raise ValueError("review cases must be a list")
    if not isinstance(mapping_cases, list):
        raise ValueError("mapping cases must be a list")
    if not isinstance(baseline_cases, list):
        raise ValueError("baseline cases must be a list")
    expected_count = review.get("case_count")
    if (
        not isinstance(expected_count, int)
        or expected_count != mapping.get("case_count")
        or expected_count != baseline.get("case_count")
    ):
        raise ValueError("case count mismatch")
    private_by_id = {row.get("review_id"): row for row in mapping_cases if isinstance(row, dict)}
    baseline_by_id = {row.get("review_id"): row for row in baseline_cases if isinstance(row, dict)}
    if len(private_by_id) != expected_count or len(baseline_by_id) != expected_count:
        raise ValueError("duplicate or missing mapping/baseline IDs")

    required = {"review_id", "title", "text", "relevant", "disputed", "reason"}
    joined = []
    seen: set[str] = set()
    for case in review_cases:
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("invalid review case fields")
        review_id = case["review_id"]
        if not isinstance(review_id, str) or review_id in seen:
            raise ValueError("invalid or duplicate review_id")
        seen.add(review_id)
        if review_id not in private_by_id or review_id not in baseline_by_id:
            raise ValueError("review ID missing from evidence")
        if not isinstance(case["relevant"], bool) or not isinstance(case["disputed"], bool):
            raise ValueError("review label and disputed must be boolean")
        if not isinstance(case["reason"], str) or not case["reason"].strip():
            raise ValueError("review reason is missing")
        if not isinstance(case["title"], str) or not isinstance(case["text"], str):
            raise ValueError("invalid review content")
        private = private_by_id[review_id]
        frozen = baseline_by_id[review_id]
        content_hash = _content_hash(case["title"], case["text"])
        if private.get("content_sha256") != content_hash:
            raise ValueError("review content does not match mapping")
        if frozen.get("content_sha256") != content_hash:
            raise ValueError("baseline content does not match review")
        baseline_relevant = frozen.get("baseline_relevant")
        if not isinstance(baseline_relevant, bool):
            raise ValueError("invalid baseline decision")
        joined.append(
            {
                "review_id": review_id,
                "doc_id": private.get("doc_id"),
                "stratum": private.get("stratum"),
                "content_sha256": content_hash,
                "reference_relevant": case["relevant"],
                "disputed": case["disputed"],
                "reason": case["reason"].strip(),
                "baseline_relevant": baseline_relevant,
                "baseline_reason": frozen.get("baseline_reason"),
            }
        )
    if seen != set(private_by_id) or seen != set(baseline_by_id):
        raise ValueError("evidence ID sets differ")
    undisputed = [row for row in joined if not row["disputed"]]
    by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in undisputed:
        stratum = row["stratum"]
        if not isinstance(stratum, str):
            raise ValueError("stratum is missing")
        by_stratum[stratum].append(row)
    disputed_count = len(joined) - len(undisputed)
    if disputed_count:
        status = "NEEDS_ADJUDICATION"
    elif len(undisputed) < 100:
        status = "INSUFFICIENT_REVIEW"
    else:
        status = "BASELINE_SCORED"
    return {
        "schema_version": "jev-holdout-baseline-score/v1",
        "status": status,
        "primary_ready": False,
        "jev_called": False,
        "independent_labels_verified": False,
        "reviewer": reviewer.strip(),
        "pool_sha256": pool_hash,
        "review_sha256": review_hash,
        "mapping_sha256": mapping_hash,
        "baseline_sha256": baseline_hash,
        "case_count": len(joined),
        "undisputed_count": len(undisputed),
        "disputed_count": disputed_count,
        "metrics": _metrics(undisputed),
        "metrics_by_stratum": {
            stratum: _metrics(rows) for stratum, rows in sorted(by_stratum.items())
        },
        "cases": joined,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = score(args.review, args.mapping, args.baseline)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
        print(json.dumps({"status": report["status"], "case_count": report["case_count"]}))
        return 0 if report["status"] == "BASELINE_SCORED" else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INVALID_SCORE_INPUT", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
