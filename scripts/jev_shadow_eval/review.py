"""Reconcile a frozen blind review with its private mapping; never call Jev."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.jev_shadow_eval.corpus import load_corpus, prepare_blind_review


def _read_object(path: Path) -> tuple[dict[str, Any], str]:
    data = path.read_bytes()
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError(f"{path.name} must contain an object")
    return parsed, hashlib.sha256(data).hexdigest()


def reconcile(corpus_path: Path, review_path: Path, mapping_path: Path) -> dict[str, Any]:
    rows, corpus_hash = load_corpus(corpus_path)
    expected_review, expected_mapping = prepare_blind_review(rows, corpus_hash)
    review, review_hash = _read_object(review_path)
    mapping, mapping_hash = _read_object(mapping_path)

    if mapping != expected_mapping:
        raise ValueError("private mapping does not match corpus")
    if review.get("schema_version") != "jev-blind-review/v2":
        raise ValueError("unknown review schema")
    if review.get("corpus_sha256") != corpus_hash:
        raise ValueError("review corpus hash mismatch")
    if review.get("case_count") != len(rows):
        raise ValueError("review case count mismatch")
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("reviewer is missing")
    raw_cases = review.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("review cases must be a list")

    expected_by_id = {case["review_id"]: case for case in expected_review["cases"]}
    mapping_by_id = {case["review_id"]: case for case in mapping["cases"]}
    seen: set[str] = set()
    labels = []
    disputed_count = 0
    required = {"review_id", "title", "text", "relevant", "disputed", "reason"}
    for case in raw_cases:
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("invalid review case fields")
        review_id = case["review_id"]
        if not isinstance(review_id, str) or review_id in seen or review_id not in expected_by_id:
            raise ValueError("unknown or duplicate review_id")
        seen.add(review_id)
        if case["title"] != expected_by_id[review_id]["title"]:
            raise ValueError("review title changed")
        if case["text"] != expected_by_id[review_id]["text"]:
            raise ValueError("review text changed")
        if not isinstance(case["relevant"], bool):
            raise ValueError("review label must be boolean")
        if not isinstance(case["disputed"], bool):
            raise ValueError("disputed must be boolean")
        if not isinstance(case["reason"], str) or not case["reason"].strip():
            raise ValueError("review reason is missing")
        disputed_count += case["disputed"]
        private = mapping_by_id[review_id]
        labels.append(
            {
                "case_id": private["case_id"],
                "content_sha256": private["content_sha256"],
                "relevant": case["relevant"],
                "disputed": case["disputed"],
                "reason": case["reason"].strip(),
            }
        )
    if seen != set(expected_by_id):
        raise ValueError("review is missing cases")
    labels.sort(key=lambda item: item["case_id"])
    return {
        "schema_version": "jev-reviewed-labels/v1",
        "status": "NEEDS_ADJUDICATION" if disputed_count else "REVIEW_COMPLETE",
        "review_complete": disputed_count == 0,
        "independent_labels_verified": False,
        "primary_ready": False,
        "jev_called": False,
        "reviewer": reviewer.strip(),
        "corpus_sha256": corpus_hash,
        "review_sha256": review_hash,
        "mapping_sha256": mapping_hash,
        "case_count": len(labels),
        "disputed_count": disputed_count,
        "labels": labels,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = reconcile(args.corpus, args.review, args.mapping)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
        print(json.dumps({"status": report["status"], "case_count": report["case_count"]}))
        return 0 if report["status"] == "REVIEW_COMPLETE" else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INVALID_REVIEW", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
