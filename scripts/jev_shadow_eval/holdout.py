"""Create a deterministic blind holdout from a read-only candidate export."""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "jev-holdout-selection/v1"
DEFAULT_TARGETS = {
    "gate_skipped": 75,
    "external_llm": 40,
    "other_rule": 30,
    "unassigned": 5,
}
REQUIRED_FIELDS = {
    "doc_id",
    "source_name",
    "source_type",
    "language",
    "url",
    "title",
    "text_excerpt",
    "text_chars",
    "fetched_at",
    "published_at",
    "is_duplicate",
    "analysis_source",
    "crypto_gate_skipped",
}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalized_content(row: dict[str, Any]) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", row["title"] + " " + row["text_excerpt"]).casefold().split()
    )


def _stratum(row: dict[str, Any]) -> str:
    if row["crypto_gate_skipped"]:
        return "gate_skipped"
    if row["analysis_source"] == "external_llm":
        return "external_llm"
    if row["analysis_source"] == "rule":
        return "other_rule"
    return "unassigned"


def load_candidates(
    path: Path, *, before: datetime, after: datetime | None = None
) -> tuple[list[dict[str, Any]], str, int]:
    data = path.read_bytes()
    rows: list[dict[str, Any]] = []
    doc_ids: set[str] = set()
    urls: set[str] = set()
    contents: set[str] = set()
    duplicate_content_count = 0
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict) or set(parsed) != REQUIRED_FIELDS:
            raise ValueError(f"line {line_number}: invalid candidate fields")
        for field in ("doc_id", "source_name", "source_type", "url", "fetched_at"):
            if not isinstance(parsed[field], str) or not parsed[field].strip():
                raise ValueError(f"line {line_number}: invalid {field}")
        for field in ("title", "text_excerpt"):
            if not isinstance(parsed[field], str):
                raise ValueError(f"line {line_number}: invalid {field}")
        if not (parsed["title"].strip() or parsed["text_excerpt"].strip()):
            raise ValueError(f"line {line_number}: empty content")
        if parsed["language"] is not None and not isinstance(parsed["language"], str):
            raise ValueError(f"line {line_number}: invalid language")
        if parsed["published_at"] is not None and not isinstance(parsed["published_at"], str):
            raise ValueError(f"line {line_number}: invalid published_at")
        if parsed["analysis_source"] not in {None, "rule", "external_llm"}:
            raise ValueError(f"line {line_number}: invalid analysis_source")
        if not isinstance(parsed["crypto_gate_skipped"], bool):
            raise ValueError(f"line {line_number}: invalid crypto_gate_skipped")
        if not isinstance(parsed["is_duplicate"], bool) or parsed["is_duplicate"]:
            raise ValueError(f"line {line_number}: duplicate-marked candidate")
        if isinstance(parsed["text_chars"], bool) or not isinstance(parsed["text_chars"], int):
            raise ValueError(f"line {line_number}: invalid text_chars")
        if parsed["text_chars"] < len(parsed["text_excerpt"]):
            raise ValueError(f"line {line_number}: text_chars shorter than excerpt")
        fetched_at = datetime.fromisoformat(parsed["fetched_at"])
        if fetched_at >= before:
            raise ValueError(f"line {line_number}: candidate crosses cutoff")
        if after is not None and fetched_at <= after:
            raise ValueError(f"line {line_number}: candidate predates selection window")
        doc_id = parsed["doc_id"].strip()
        url = parsed["url"].strip()
        if doc_id in doc_ids or url in urls:
            raise ValueError(f"line {line_number}: duplicate doc_id or URL")
        doc_ids.add(doc_id)
        urls.add(url)
        normalized = _normalized_content(parsed)
        content_hash = _digest(normalized.encode("utf-8"))
        if content_hash in contents:
            duplicate_content_count += 1
            continue
        contents.add(content_hash)
        rows.append({**parsed, "doc_id": doc_id, "url": url, "content_sha256": content_hash})
    if not rows:
        raise ValueError("candidate pool is empty")
    return rows, _digest(data), duplicate_content_count


def select_holdout(
    rows: list[dict[str, Any]],
    *,
    pool_hash: str,
    targets: dict[str, int],
    max_per_source: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if set(targets) != set(DEFAULT_TARGETS) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in targets.values()
    ):
        raise ValueError("invalid stratum targets")
    if max_per_source < 1:
        raise ValueError("max_per_source must be positive")
    source_counts: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []
    selected_counts: Counter[str] = Counter()
    for stratum, target in targets.items():
        candidates = sorted(
            (row for row in rows if _stratum(row) == stratum),
            key=lambda row: _digest(f"{pool_hash}:{row['doc_id']}".encode()),
        )
        for row in candidates:
            if selected_counts[stratum] >= target:
                break
            if source_counts[row["source_name"]] >= max_per_source:
                continue
            selected.append({**row, "stratum": stratum})
            selected_counts[stratum] += 1
            source_counts[row["source_name"]] += 1
        if selected_counts[stratum] != target:
            raise ValueError(f"insufficient candidates for {stratum} under source cap")
    selected.sort(key=lambda row: _digest(f"review:{pool_hash}:{row['doc_id']}".encode()))
    return selected, dict(source_counts)


def build_packages(
    selected: list[dict[str, Any]],
    *,
    pool_hash: str,
    cutoff: str,
    after: str | None = None,
    targets: dict[str, int],
    max_per_source: int,
    duplicate_content_count: int,
    source_counts: dict[str, int],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    review_cases = []
    mapping_cases = []
    for index, row in enumerate(selected, start=1):
        review_id = f"holdout-{index:04d}"
        review_cases.append(
            {
                "review_id": review_id,
                "title": row["title"],
                "text": row["text_excerpt"],
                "relevant": None,
                "disputed": None,
                "reason": None,
            }
        )
        mapping_cases.append(
            {
                "review_id": review_id,
                "doc_id": row["doc_id"],
                "content_sha256": row["content_sha256"],
                "stratum": row["stratum"],
                "source_name": row["source_name"],
                "source_type": row["source_type"],
                "language": row["language"],
                "url": row["url"],
                "fetched_at": row["fetched_at"],
                "published_at": row["published_at"],
                "analysis_source": row["analysis_source"],
                "crypto_gate_skipped": row["crypto_gate_skipped"],
            }
        )
    review = {
        "schema_version": "jev-holdout-blind-review/v1",
        "status": "AWAITING_INDEPENDENT_LABEL_REVIEW",
        "pool_sha256": pool_hash,
        "case_count": len(review_cases),
        "reviewer": None,
        "cases": review_cases,
    }
    mapping = {
        "schema_version": "jev-holdout-private-map/v1",
        "handling": "DO_NOT_SHARE_WITH_REVIEWER_BEFORE_REVIEW_IS_FROZEN",
        "pool_sha256": pool_hash,
        "case_count": len(mapping_cases),
        "cases": mapping_cases,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "SELECTED_NOT_LABELED",
        "sealed": True,
        "primary_ready": False,
        "jev_called": False,
        "pool_sha256": pool_hash,
        "cutoff_exclusive": cutoff,
        "after_exclusive": after,
        "selection_algorithm": "sha256-order/v1",
        "selection_code_sha256": _digest(Path(__file__).read_bytes()),
        "targets": targets,
        "selected_count": len(selected),
        "max_per_source": max_per_source,
        "source_counts": dict(sorted(source_counts.items())),
        "duplicate_normalized_content_excluded": duplicate_content_count,
    }
    return review, mapping, manifest


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after")
    parser.add_argument("--review-output", type=Path, required=True)
    parser.add_argument("--mapping-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--max-per-source", type=int, default=30)
    args = parser.parse_args(argv)
    outputs = [args.review_output, args.mapping_output, args.manifest_output]
    try:
        if len({path.resolve() for path in outputs}) != len(outputs):
            raise ValueError("output paths must differ")
        if any(path.exists() for path in outputs):
            raise FileExistsError("an output already exists")
        cutoff = datetime.fromisoformat(args.before)
        after = datetime.fromisoformat(args.after) if args.after else None
        if after is not None and after >= cutoff:
            raise ValueError("--after must be earlier than --before")
        rows, pool_hash, duplicate_count = load_candidates(args.input, before=cutoff, after=after)
        selected, source_counts = select_holdout(
            rows,
            pool_hash=pool_hash,
            targets=DEFAULT_TARGETS,
            max_per_source=args.max_per_source,
        )
        review, mapping, manifest = build_packages(
            selected,
            pool_hash=pool_hash,
            cutoff=args.before,
            after=args.after,
            targets=DEFAULT_TARGETS,
            max_per_source=args.max_per_source,
            duplicate_content_count=duplicate_count,
            source_counts=source_counts,
        )
        for path, payload in zip(outputs, (review, mapping, manifest), strict=True):
            _write_new(path, payload)
        print(json.dumps({"status": manifest["status"], "selected_count": len(selected)}))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INVALID_SELECTION", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
