"""Prepare reproducible, offline relevance baselines; never call Jev."""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from app.analysis.crypto_relevance import crypto_relevance_verdict
from app.analysis.keywords.engine import KeywordEngine
from app.core.domain.document import CanonicalDocument

ROOT = Path(__file__).resolve().parents[2]
MONITOR_FILES = ("keywords.txt", "watchlists.yml", "entity_aliases.yml")
BASELINE_FILES = (
    "app/analysis/crypto_relevance.py",
    "app/analysis/keywords/engine.py",
    "app/analysis/keywords/watchlist.py",
    "app/analysis/keywords/aliases.py",
    "app/core/domain/document.py",
    "scripts/jev_shadow_eval/corpus.py",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_corpus(path: Path) -> tuple[list[dict[str, Any]], str]:
    data = path.read_bytes()
    rows = json.loads(data)
    if not isinstance(rows, list) or not rows:
        raise ValueError("corpus must be a nonempty list")
    ids: set[str] = set()
    contents: set[str] = set()
    groups: dict[str, str] = {}
    required = {
        "case_id",
        "group_id",
        "split",
        "title",
        "text",
        "origin",
        "provenance",
        "proposed_relevant",
        "label_reason",
        "category",
    }
    normalized_rows = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("invalid corpus fields")
        for field in required - {"proposed_relevant", "title", "text"}:
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"missing or invalid {field}")
        if not all(isinstance(row[field], str) for field in ("title", "text")):
            raise ValueError("title and text must be strings")
        if not isinstance(row["proposed_relevant"], bool):
            raise ValueError("proposed_relevant must be boolean")
        if row["split"] not in {"development", "holdout"}:
            raise ValueError("unknown split")
        if row["origin"] not in {"synthetic", "public", "historical"}:
            raise ValueError("unknown origin")
        case_id = row["case_id"].strip()
        if case_id in ids:
            raise ValueError("duplicate case_id")
        ids.add(case_id)
        normalized = " ".join(
            unicodedata.normalize("NFKC", row["title"] + " " + row["text"]).casefold().split()
        )
        content_hash = digest(normalized.encode("utf-8"))
        if content_hash in contents:
            raise ValueError("duplicate normalized content")
        contents.add(content_hash)
        group = row["group_id"].strip()
        if group in groups and groups[group] != row["split"]:
            raise ValueError("related group crosses development/holdout boundary")
        groups[group] = row["split"]
        normalized_rows.append({**row, "case_id": case_id, "group_id": group})
    return normalized_rows, digest(data)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _provisional_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    results = [row for row in results if row["classification_eligible"]]
    tp = sum(row["baseline_relevant"] and row["proposed_relevant"] for row in results)
    tn = sum(not row["baseline_relevant"] and not row["proposed_relevant"] for row in results)
    fp = sum(row["baseline_relevant"] and not row["proposed_relevant"] for row in results)
    fn = sum(not row["baseline_relevant"] and row["proposed_relevant"] for row in results)
    return {
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "accuracy": _ratio(tp + tn, len(results)),
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "specificity": _ratio(tn, tn + fp),
    }


def prepare_blind_review(
    rows: list[dict[str, Any]], corpus_hash: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: digest(f"{corpus_hash}:{row['case_id']}".encode()),
    )
    cases = []
    mappings = []
    for index, row in enumerate(ordered, start=1):
        review_id = f"case-{index:04d}"
        content_hash = digest((row["title"] + "\n" + row["text"]).encode())
        cases.append(
            {
                "review_id": review_id,
                "title": row["title"],
                "text": row["text"],
                "relevant": None,
                "disputed": None,
                "reason": None,
            }
        )
        mappings.append(
            {
                "review_id": review_id,
                "case_id": row["case_id"],
                "content_sha256": content_hash,
            }
        )
    review = {
        "schema_version": "jev-blind-review/v2",
        "status": "AWAITING_INDEPENDENT_LABEL_REVIEW",
        "corpus_sha256": corpus_hash,
        "case_count": len(rows),
        "reviewer": None,
        "cases": cases,
    }
    mapping = {
        "schema_version": "jev-blind-review-map/v1",
        "corpus_sha256": corpus_hash,
        "case_count": len(rows),
        "handling": "DO_NOT_SHARE_WITH_REVIEWER_BEFORE_REVIEW_IS_FROZEN",
        "cases": mappings,
    }
    return review, mapping


def prepare(path: Path, monitor: Path) -> dict[str, Any]:
    rows, corpus_hash = load_corpus(path)
    if any(row["split"] != "development" for row in rows):
        raise ValueError(
            "labeled baseline accepts development rows only; holdout labels must stay sealed"
        )
    # KeywordEngine silently tolerates missing monitor files; evidence must not.
    config_hashes = {name: digest((monitor / name).read_bytes()) for name in MONITOR_FILES}
    engine = KeywordEngine.from_monitor_dir(monitor)
    results = []
    for row in rows:
        text = row["title"] + "\n" + row["text"]
        if not text.strip():
            results.append(
                {
                    **row,
                    "content_sha256": digest(text.encode("utf-8")),
                    "classification_eligible": False,
                    "baseline_relevant": None,
                    "baseline_reason": "preclassification_empty",
                    "baseline_tickers": [],
                    "crypto_keyword_hits": [],
                    "agrees_with_proposed_label": None,
                }
            )
            continue
        hits = engine.match(text)
        # This is a named raw-text adapter, not a replay of the full pipeline.
        doc = CanonicalDocument(
            url="https://example.invalid/jev-corpus",
            title=row["title"],
            raw_text=row["text"],
            tickers=engine.match_tickers(text),
        )
        relevant, reason = crypto_relevance_verdict(doc, hits)
        results.append(
            {
                **row,
                "content_sha256": digest(text.encode("utf-8")),
                "classification_eligible": True,
                "baseline_relevant": relevant,
                "baseline_reason": reason,
                "baseline_tickers": doc.tickers,
                "crypto_keyword_hits": [h.canonical for h in hits if h.category == "crypto"],
                "agrees_with_proposed_label": relevant == row["proposed_relevant"],
            }
        )
    if config_hashes != {name: digest((monitor / name).read_bytes()) for name in MONITOR_FILES}:
        raise ValueError("monitor configuration changed during baseline run")
    return {
        "schema_version": "jev-corpus-baseline/v1",
        "status": "AWAITING_INDEPENDENT_LABEL_REVIEW",
        "primary_ready": False,
        "jev_called": False,
        "independent_labels_verified": False,
        "baseline_scope": "raw-text KeywordEngine adapter + crypto_relevance_verdict; not full pipeline",
        "corpus_sha256": corpus_hash,
        "monitor_sha256": config_hashes,
        "code_sha256": {name: digest((ROOT / name).read_bytes()) for name in BASELINE_FILES},
        "case_count": len(rows),
        "split_counts": dict(Counter(row["split"] for row in rows)),
        "category_counts": dict(Counter(row["category"] for row in rows)),
        "proposed_positive_count": sum(row["proposed_relevant"] for row in rows),
        "proposed_negative_count": sum(not row["proposed_relevant"] for row in rows),
        "classification_eligible_count": sum(row["classification_eligible"] for row in results),
        "preclassification_rejected_ids": [
            row["case_id"] for row in results if not row["classification_eligible"]
        ],
        "provisional_metrics": _provisional_metrics(results),
        "provisional_disagreement_ids": [
            row["case_id"]
            for row in results
            if row["classification_eligible"] and not row["agrees_with_proposed_label"]
        ],
        "cases": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--monitor", type=Path, default=ROOT / "monitor")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--blind-review",
        action="store_true",
        help="Export text without label proposals or baseline",
    )
    parser.add_argument(
        "--blind-map-output",
        type=Path,
        help="Separate secret ID mapping; required with --blind-review",
    )
    args = parser.parse_args(argv)
    try:
        if args.blind_review:
            if args.blind_map_output is None:
                raise ValueError("--blind-map-output is required with --blind-review")
            if args.output.resolve() == args.blind_map_output.resolve():
                raise ValueError("blind review and mapping outputs must differ")
            rows, corpus_hash = load_corpus(args.input)
            report, mapping = prepare_blind_review(rows, corpus_hash)
            args.blind_map_output.parent.mkdir(parents=True, exist_ok=True)
            if args.output.exists() or args.blind_map_output.exists():
                raise FileExistsError("review or mapping output already exists")
            with args.blind_map_output.open("x", encoding="utf-8") as stream:
                json.dump(mapping, stream, indent=2, sort_keys=True, ensure_ascii=False)
                stream.write("\n")
        elif args.blind_map_output is not None:
            raise ValueError("--blind-map-output requires --blind-review")
        else:
            report = prepare(args.input, args.monitor)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
        print(json.dumps({"status": report["status"], "case_count": report["case_count"]}))
        return 0  # Successful preparation, never evidence approval.
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "INVALID_CORPUS", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
