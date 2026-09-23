"""Capture the current raw-text relevance baseline for a sealed holdout."""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path
from typing import Any

from scripts.jev_shadow_eval.corpus import BASELINE_FILES, MONITOR_FILES, ROOT, digest

from app.analysis.crypto_relevance import crypto_relevance_verdict
from app.analysis.keywords.engine import KeywordEngine
from app.core.domain.document import CanonicalDocument


def _read_object(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_bytes())
    if not isinstance(parsed, dict):
        raise ValueError(f"{path.name} must contain an object")
    return parsed


def _content_hash(title: str, text: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", title + " " + text).casefold().split())
    return digest(normalized.encode("utf-8"))


def capture(review_path: Path, mapping_path: Path, monitor: Path) -> dict[str, Any]:
    review = _read_object(review_path)
    mapping = _read_object(mapping_path)
    if review.get("schema_version") != "jev-holdout-blind-review/v1":
        raise ValueError("unknown blind review schema")
    if mapping.get("schema_version") != "jev-holdout-private-map/v1":
        raise ValueError("unknown private mapping schema")
    pool_hash = review.get("pool_sha256")
    if not isinstance(pool_hash, str) or mapping.get("pool_sha256") != pool_hash:
        raise ValueError("pool hash mismatch")
    review_cases = review.get("cases")
    mapping_cases = mapping.get("cases")
    if not isinstance(review_cases, list) or not isinstance(mapping_cases, list):
        raise ValueError("cases must be lists")
    if review.get("case_count") != len(review_cases) or mapping.get("case_count") != len(
        mapping_cases
    ):
        raise ValueError("case count mismatch")
    private_by_id: dict[str, dict[str, Any]] = {}
    for case in mapping_cases:
        if not isinstance(case, dict):
            raise ValueError("invalid private mapping case")
        review_id = case.get("review_id")
        if not isinstance(review_id, str) or review_id in private_by_id:
            raise ValueError("invalid or duplicate private review_id")
        private_by_id[review_id] = case

    config_hashes = {name: digest((monitor / name).read_bytes()) for name in MONITOR_FILES}
    engine = KeywordEngine.from_monitor_dir(monitor)
    results = []
    seen: set[str] = set()
    required = {"review_id", "title", "text", "relevant", "disputed", "reason"}
    for case in review_cases:
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("invalid blind review case fields")
        review_id = case["review_id"]
        if not isinstance(review_id, str) or review_id in seen or review_id not in private_by_id:
            raise ValueError("unknown or duplicate review_id")
        seen.add(review_id)
        if any(case[field] is not None for field in ("relevant", "disputed", "reason")):
            raise ValueError("baseline requires the original unlabeled blind packet")
        title = case["title"]
        text = case["text"]
        if (
            not isinstance(title, str)
            or not isinstance(text, str)
            or not (title.strip() or text.strip())
        ):
            raise ValueError("invalid or empty review content")
        private = private_by_id[review_id]
        content_hash = _content_hash(title, text)
        if private.get("content_sha256") != content_hash:
            raise ValueError("content hash mismatch")
        combined = title + "\n" + text
        hits = engine.match(combined)
        doc = CanonicalDocument(
            url="https://example.invalid/jev-holdout",
            title=title,
            raw_text=text,
            tickers=engine.match_tickers(combined),
        )
        relevant, reason = crypto_relevance_verdict(doc, hits)
        results.append(
            {
                "review_id": review_id,
                "doc_id": private.get("doc_id"),
                "content_sha256": content_hash,
                "baseline_relevant": relevant,
                "baseline_reason": reason,
                "baseline_tickers": doc.tickers,
                "crypto_keyword_hits": [hit.canonical for hit in hits if hit.category == "crypto"],
            }
        )
    if seen != set(private_by_id):
        raise ValueError("review and mapping IDs differ")
    if config_hashes != {name: digest((monitor / name).read_bytes()) for name in MONITOR_FILES}:
        raise ValueError("monitor configuration changed during baseline run")
    return {
        "schema_version": "jev-holdout-baseline/v1",
        "status": "BASELINE_CAPTURED_NOT_SCORED",
        "baseline_scope": "raw-text KeywordEngine adapter + crypto_relevance_verdict; not full pipeline",
        "primary_ready": False,
        "jev_called": False,
        "pool_sha256": pool_hash,
        "case_count": len(results),
        "baseline_positive_count": sum(row["baseline_relevant"] for row in results),
        "baseline_negative_count": sum(not row["baseline_relevant"] for row in results),
        "monitor_sha256": config_hashes,
        "code_sha256": {name: digest((ROOT / name).read_bytes()) for name in BASELINE_FILES},
        "cases": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--monitor", type=Path, default=ROOT / "monitor")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = capture(args.review, args.mapping, args.monitor)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
        print(json.dumps({"status": report["status"], "case_count": report["case_count"]}))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INVALID_BASELINE_INPUT", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
