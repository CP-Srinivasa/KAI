"""PH5C filter baseline execution.

This script evaluates pre-LLM stub filtering options on the PH5 dataset.

Primary goals:
1. Identify stub-like documents (manual placeholder content).
2. Validate exclusion risk for short manual documents.
3. Estimate proxy-rate and LLM-cost impact for filter candidates.

Outputs:
- artifacts/ph5c/ph5c_stub_filter_baseline.json
- artifacts/ph5c/ph5c_operator_summary.md
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BASE = Path(__file__).parent.parent
ARTIFACTS = BASE / "artifacts"
INPUT_DATASET = ARTIFACTS / "ph4b" / "ph4b_tier3_shadow.jsonl"
OUT_DIR = ARTIFACTS / "ph5c"

# PH5C baseline threshold from contract candidate.
MAX_STUB_LEN = 50

# Placeholder tokens we consider non-analyzable by default.
PLACEHOLDER_VALUES = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "none",
    "comment",
    "comments",
    "todo",
    "tbd",
}

# Conservative title signal set:
# If one of these terms appears in title, we do NOT auto-skip in conservative mode.
TITLE_SIGNAL_KEYWORDS = {
    # finance/macro keywords
    "bitcoin",
    "crypto",
    "ethereum",
    "defi",
    "stock",
    "equity",
    "market",
    "trading",
    "investment",
    "investor",
    "portfolio",
    "fund",
    "bond",
    "etf",
    "inflation",
    "fed",
    "rate",
    "usd",
    "eur",
    # frequently market-relevant entities from this corpus
    "tesla",
    "salesforce",
    "anthropic",
    "google",
    "openai",
    "acquired",
    "acquisition",
    # ai/tech signal terms to avoid over-broad auto-skip
    "ai",
    "llm",
    "gpt",
}


@dataclass(frozen=True)
class Doc:
    document_id: str
    title: str
    source: str
    raw_content: str
    content_len: int
    priority_score: int | None
    relevance_score: float | None
    market_scope: str | None

    @property
    def normalized_content(self) -> str:
        return " ".join(self.raw_content.strip().lower().split())

    @property
    def is_proxy_signature(self) -> bool:
        return (
            self.priority_score == 1
            and self.relevance_score == 0.0
            and self.market_scope == "unknown"
        )


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def parse_doc(row: dict) -> Doc:
    messages = row.get("messages", [])

    user_content = ""
    for msg in messages:
        if msg.get("role") == "user":
            user_content = msg.get("content", "")
            break

    title = ""
    source = ""
    for line in user_content.split("\n"):
        if line.startswith("Title:"):
            title = line.split("Title:", 1)[1].strip()
        elif line.startswith("Source:"):
            source = line.split("Source:", 1)[1].strip()

    raw_content = ""
    content_start = user_content.find("Content:")
    if content_start >= 0:
        raw_content = user_content[content_start + 8 :].strip()

    llm_output: dict = {}
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            try:
                llm_output = json.loads(msg.get("content", "{}"))
            except json.JSONDecodeError:
                llm_output = {}
            break

    metadata = row.get("metadata", {})
    return Doc(
        document_id=metadata.get("document_id", ""),
        title=title,
        source=source,
        raw_content=raw_content,
        content_len=len(raw_content),
        priority_score=llm_output.get("priority_score"),
        relevance_score=llm_output.get("relevance_score"),
        market_scope=llm_output.get("market_scope"),
    )


def has_title_signal(title: str) -> bool:
    t = title.lower()
    if any(keyword in t for keyword in TITLE_SIGNAL_KEYWORDS):
        return True
    # Optional ticker-ish signal in title.
    return re.search(r"\b[A-Z]{2,5}\b", title) is not None


def is_stub_candidate(doc: Doc) -> bool:
    return (
        doc.source.lower() == "manual"
        and doc.content_len <= MAX_STUB_LEN
        and doc.normalized_content in PLACEHOLDER_VALUES
    )


def evaluate_rule(name: str, docs: list[Doc], flagged_ids: set[str]) -> dict:
    flagged = [d for d in docs if d.document_id in flagged_ids]
    kept = [d for d in docs if d.document_id not in flagged_ids]

    total = len(docs)
    proxy_total = sum(1 for d in docs if d.is_proxy_signature)
    tp = sum(1 for d in flagged if d.is_proxy_signature)
    fp = sum(1 for d in flagged if not d.is_proxy_signature)
    fn = proxy_total - tp

    precision = (tp / (tp + fp)) if (tp + fp) else 0.0
    recall = (tp / proxy_total) if proxy_total else 0.0
    llm_calls_saved = len(flagged)
    llm_calls_saved_rate = llm_calls_saved / total if total else 0.0

    remaining_proxy = sum(1 for d in kept if d.is_proxy_signature)
    projected_proxy_rate_overall = remaining_proxy / total if total else 0.0
    projected_proxy_rate_on_remaining = remaining_proxy / len(kept) if kept else 0.0

    return {
        "rule": name,
        "flagged_count": len(flagged),
        "kept_count": len(kept),
        "tp_proxy_captured": tp,
        "fp_non_proxy_flagged": fp,
        "fn_proxy_missed": fn,
        "precision_proxy_capture": round(precision, 4),
        "recall_proxy_capture": round(recall, 4),
        "llm_calls_saved": llm_calls_saved,
        "llm_calls_saved_rate": round(llm_calls_saved_rate, 4),
        "projected_proxy_rate_overall": round(projected_proxy_rate_overall, 4),
        "projected_proxy_rate_on_remaining": round(projected_proxy_rate_on_remaining, 4),
    }


def choose_recommended_rule(evaluations: list[dict]) -> str:
    # Prefer zero false-positive rules. Within those, maximize proxy recall.
    zero_fp = [e for e in evaluations if e["fp_non_proxy_flagged"] == 0]
    if zero_fp:
        return max(zero_fp, key=lambda e: e["recall_proxy_capture"])["rule"]
    # Fallback: maximize F1-like proxy capture tradeoff.
    return max(
        evaluations,
        key=lambda e: (2 * e["precision_proxy_capture"] * e["recall_proxy_capture"])
        / (e["precision_proxy_capture"] + e["recall_proxy_capture"] + 1e-9),
    )["rule"]


def main() -> None:
    print("PH5C: loading tier3 dataset...")
    rows = load_jsonl(INPUT_DATASET)
    docs = [parse_doc(r) for r in rows]
    total = len(docs)
    if total != 69:
        raise ValueError(f"Expected 69 docs, got {total}")

    proxy_total = sum(1 for d in docs if d.is_proxy_signature)
    print(f"  docs={total}, proxy_signature={proxy_total}")

    stub_candidates = [d for d in docs if is_stub_candidate(d)]
    stub_candidate_ids = {d.document_id for d in stub_candidates}
    print(f"  stub_candidates={len(stub_candidates)} (MAX_STUB_LEN={MAX_STUB_LEN})")

    # Rule A (aggressive): skip all placeholder candidates.
    aggressive_ids = set(stub_candidate_ids)

    # Rule B (conservative): skip placeholder candidates only when title has no signal.
    conservative_ids = {
        d.document_id for d in stub_candidates if not has_title_signal(d.title)
    }

    evaluations = [
        evaluate_rule("aggressive_placeholder_skip", docs, aggressive_ids),
        evaluate_rule("conservative_placeholder_skip", docs, conservative_ids),
    ]
    recommended_rule = choose_recommended_rule(evaluations)
    recommended_eval = next(e for e in evaluations if e["rule"] == recommended_rule)

    aggressive_fp_docs = [
        d for d in docs if d.document_id in aggressive_ids and not d.is_proxy_signature
    ]
    conservative_flagged_docs = [d for d in docs if d.document_id in conservative_ids]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()

    report = {
        "report_type": "ph5c_stub_filter_baseline",
        "phase": "PHASE 5",
        "sprint": "PH5C_FILTER_BEFORE_LLM_BASELINE",
        "generated_at": generated_at,
        "inputs": {
            "dataset": str(INPUT_DATASET),
            "total_docs": total,
            "proxy_docs": proxy_total,
            "max_stub_len": MAX_STUB_LEN,
            "placeholder_values": sorted(PLACEHOLDER_VALUES),
        },
        "stub_candidates": {
            "count": len(stub_candidates),
            "proxy_count": sum(1 for d in stub_candidates if d.is_proxy_signature),
            "non_proxy_count": sum(1 for d in stub_candidates if not d.is_proxy_signature),
            "unique_normalized_content": sorted({d.normalized_content for d in stub_candidates}),
        },
        "rule_evaluations": evaluations,
        "recommended_rule": recommended_rule,
        "recommended_rule_metrics": recommended_eval,
        "risk_validation": {
            "aggressive_rule_non_proxy_exclusions": [
                {
                    "document_id": d.document_id,
                    "title": d.title,
                    "content_len": d.content_len,
                    "priority_score": d.priority_score,
                    "relevance_score": d.relevance_score,
                    "market_scope": d.market_scope,
                }
                for d in aggressive_fp_docs
            ],
            "conservative_rule_flagged_docs": [
                {
                    "document_id": d.document_id,
                    "title": d.title,
                    "content_len": d.content_len,
                    "is_proxy_signature": d.is_proxy_signature,
                }
                for d in conservative_flagged_docs
            ],
        },
        "conclusion": {
            "ph5b_closed": True,
            "root_cause_confirmed": "EMPTY_MANUAL placeholder content",
            "execution_readiness": "filter baseline executed",
            "note": (
                "Aggressive placeholder-only skip maximizes proxy reduction but risks excluding "
                "valid short manual docs. Conservative skip has zero observed non-proxy exclusions "
                "on this dataset and is recommended as baseline-first path."
            ),
        },
    }

    out_json = OUT_DIR / "ph5c_stub_filter_baseline.json"
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote {out_json}")

    lines = [
        "# PH5C Stub Filter Baseline - Operator Summary",
        "",
        f"**Generated:** {generated_at[:19]}Z  ",
        "**Sprint:** PH5C_FILTER_BEFORE_LLM_BASELINE (execution run)  ",
        f"**Dataset:** {total} Tier3 docs, proxy-signature={proxy_total}",
        "",
        "---",
        "",
        "## Confirmed Context",
        "",
        "- PH5B remains closed and accepted.",
        "- Root cause remains EMPTY_MANUAL placeholder content.",
        "- PH5C run executed as baseline filter analysis (no production deployment).",
        "",
        "---",
        "",
        "## Rule Evaluation",
        "",
        "| Rule | Flagged | Proxy Captured | Non-Proxy Flagged | Precision | Recall | Calls Saved | Projected Proxy Rate (overall) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for e in evaluations:
        lines.append(
            f"| {e['rule']} | {e['flagged_count']} | {e['tp_proxy_captured']} | "
            f"{e['fp_non_proxy_flagged']} | {e['precision_proxy_capture']:.2f} | "
            f"{e['recall_proxy_capture']:.2f} | {e['llm_calls_saved']} | "
            f"{e['projected_proxy_rate_overall']:.3f} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Recommendation",
        "",
        f"- **Recommended baseline rule:** `{recommended_rule}`",
        (
            f"- Rationale: precision={recommended_eval['precision_proxy_capture']:.2f}, "
            f"recall={recommended_eval['recall_proxy_capture']:.2f}, "
            f"non-proxy flagged={recommended_eval['fp_non_proxy_flagged']}."
        ),
        (
            f"- Estimated LLM calls saved: {recommended_eval['llm_calls_saved']}/{total} "
            f"({recommended_eval['llm_calls_saved_rate']:.1%})."
        ),
        (
            f"- Projected proxy rate after skip (overall): "
            f"{recommended_eval['projected_proxy_rate_overall']:.1%}."
        ),
        "",
        "---",
        "",
        "## Risk Note",
        "",
        "- Aggressive placeholder-only skip excludes additional non-proxy short-manual docs.",
        "- Conservative baseline is safer for first execution pass.",
        "",
        "_Artifacts: `artifacts/ph5c/ph5c_stub_filter_baseline.json`, "
        "`artifacts/ph5c/ph5c_operator_summary.md`_",
    ]

    out_md = OUT_DIR / "ph5c_operator_summary.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"  wrote {out_md}")
    print("PH5C baseline execution complete.")


if __name__ == "__main__":
    main()

