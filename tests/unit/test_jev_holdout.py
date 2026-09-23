from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from scripts.jev_shadow_eval.holdout import (
    DEFAULT_TARGETS,
    build_packages,
    load_candidates,
    main,
    select_holdout,
)


def _candidate(index: int, stratum: str, source: str) -> dict[str, object]:
    analysis_source: str | None = "rule"
    skipped = False
    if stratum == "gate_skipped":
        skipped = True
    elif stratum == "external_llm":
        analysis_source = "external_llm"
    elif stratum == "unassigned":
        analysis_source = None
    return {
        "doc_id": f"doc-{index}",
        "source_name": source,
        "source_type": "rss_feed",
        "language": None,
        "url": f"https://example.test/{index}",
        "title": f"Title {index}",
        "text_excerpt": f"Text {index} for independent review.",
        "text_chars": 100,
        "fetched_at": "2026-09-20 10:00:00",
        "published_at": None,
        "is_duplicate": False,
        "analysis_source": analysis_source,
        "crypto_gate_skipped": skipped,
    }


def _pool(tmp_path: Path) -> Path:
    rows = []
    index = 0
    for stratum, target in DEFAULT_TARGETS.items():
        for offset in range(target + 4):
            rows.append(_candidate(index, stratum, f"source-{offset % 10}"))
            index += 1
    path = tmp_path / "pool.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_holdout_selection_is_reproducible_balanced_and_blind(tmp_path: Path) -> None:
    path = _pool(tmp_path)
    rows, pool_hash, duplicate_count = load_candidates(
        path, before=datetime.fromisoformat("2026-09-23T10:58:41")
    )
    first, source_counts = select_holdout(
        rows, pool_hash=pool_hash, targets=DEFAULT_TARGETS, max_per_source=30
    )
    second, _ = select_holdout(
        rows, pool_hash=pool_hash, targets=DEFAULT_TARGETS, max_per_source=30
    )
    assert first == second
    assert len(first) == 150
    assert max(source_counts.values()) <= 30
    review, mapping, manifest = build_packages(
        first,
        pool_hash=pool_hash,
        cutoff="2026-09-23T10:58:41",
        targets=DEFAULT_TARGETS,
        max_per_source=30,
        duplicate_content_count=duplicate_count,
        source_counts=source_counts,
    )
    assert review["case_count"] == mapping["case_count"] == 150
    assert manifest["targets"] == DEFAULT_TARGETS
    assert len(manifest["selection_code_sha256"]) == 64
    assert not ({"doc_id", "url", "source_name", "stratum"} & set(review["cases"][0]))
    assert mapping["handling"] == "DO_NOT_SHARE_WITH_REVIEWER_BEFORE_REVIEW_IS_FROZEN"


def test_cli_writes_three_new_outputs_and_never_overwrites(tmp_path: Path) -> None:
    path = _pool(tmp_path)
    outputs = [tmp_path / name for name in ("review.json", "mapping.json", "manifest.json")]
    argv = [
        "--input",
        str(path),
        "--before",
        "2026-09-23T10:58:41",
        "--review-output",
        str(outputs[0]),
        "--mapping-output",
        str(outputs[1]),
        "--manifest-output",
        str(outputs[2]),
    ]
    assert main(argv) == 0
    originals = [path.read_bytes() for path in outputs]
    assert main(argv) == 2
    assert [path.read_bytes() for path in outputs] == originals


@pytest.mark.parametrize("defect", ["cutoff", "duplicate", "marked", "empty"])
def test_invalid_candidate_pool_is_rejected(tmp_path: Path, defect: str) -> None:
    row = _candidate(1, "gate_skipped", "source")
    rows = [row]
    if defect == "cutoff":
        row["fetched_at"] = "2026-09-23 11:00:00"
    elif defect == "duplicate":
        rows.append(dict(row))
    elif defect == "marked":
        row["is_duplicate"] = True
    else:
        row["title"] = ""
        row["text_excerpt"] = ""
    path = tmp_path / "bad.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in rows), encoding="utf-8")
    with pytest.raises(ValueError):
        load_candidates(path, before=datetime.fromisoformat("2026-09-23T10:58:41"))


def test_source_cap_fails_closed_when_target_cannot_be_met(tmp_path: Path) -> None:
    path = _pool(tmp_path)
    rows, pool_hash, _ = load_candidates(path, before=datetime.fromisoformat("2026-09-23T10:58:41"))
    with pytest.raises(ValueError, match="insufficient candidates"):
        select_holdout(rows, pool_hash=pool_hash, targets=DEFAULT_TARGETS, max_per_source=1)
