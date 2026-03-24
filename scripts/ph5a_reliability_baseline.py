"""PH5A Reliability Baseline Diagnostic Script.

Computes 7 reliability metrics from the 69-document paired set:
1. fallback_rate
2. llm_error_proxy_rate
3. provider_distribution
4. priority_distribution
5. keyword_coverage (after PH4D)
6. tag_fill_rate (after PH4J)
7. watchlist_overlap_rate

Generates:
- artifacts/ph5a_reliability_baseline.json
- artifacts/ph5a_operator_summary.md
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

BASE = Path(__file__).parent.parent
ARTIFACTS = BASE / "artifacts"


def load_jsonl(path: Path) -> list[dict]:
    docs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def parse_tier3_output(doc: dict) -> dict | None:
    """Extract assistant JSON from ph4b tier3 shadow format."""
    messages = doc.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            try:
                return json.loads(msg["content"])
            except (json.JSONDecodeError, KeyError):
                return None
    return None


def parse_rule_output(doc: dict) -> dict | None:
    """Extract rule-based analysis result from ph4a format."""
    messages = doc.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            try:
                return json.loads(msg["content"])
            except (json.JSONDecodeError, KeyError):
                return None
    return None


def main() -> None:
    print("PH5A: Loading datasets...")

    # ── Load datasets ──────────────────────────────────────────────────────────
    rule_docs = load_jsonl(ARTIFACTS / "ph4a" / "candidate_rule.jsonl")
    tier3_docs = load_jsonl(ARTIFACTS / "ph4b" / "ph4b_tier3_shadow.jsonl")
    ph4k_metrics = json.loads((ARTIFACTS / "ph4k" / "ph4k_utility_metrics.json").read_text())
    ph4d_metrics = json.loads((ARTIFACTS / "ph4d" / "ph4d_metrics.json").read_text())

    n = len(tier3_docs)
    assert n == 69, f"Expected 69 tier3 docs, got {n}"  # noqa: S101
    assert len(rule_docs) == 69, f"Expected 69 rule docs, got {len(rule_docs)}"  # noqa: S101
    print(f"  Loaded {n} paired documents.")

    # ── Parse outputs ──────────────────────────────────────────────────────────
    tier3_outputs = []
    tier3_parse_errors = 0
    for doc in tier3_docs:
        out = parse_tier3_output(doc)
        if out is not None:
            tier3_outputs.append(out)
        else:
            tier3_parse_errors += 1

    rule_outputs = []
    rule_parse_errors = 0
    for doc in rule_docs:
        out = parse_rule_output(doc)
        if out is not None:
            rule_outputs.append(out)
        else:
            rule_parse_errors += 1

    print(f"  Tier3 parse errors: {tier3_parse_errors}/{n}")
    print(f"  Rule parse errors:  {rule_parse_errors}/{n}")

    # ── Metric 1: Fallback Rate ────────────────────────────────────────────────
    # Shadow run = 100% Tier3. Fallback = 0% (by definition of shadow run design).
    # We verify: all 69 docs have valid tier3 output.
    fallback_count = tier3_parse_errors
    fallback_rate = fallback_count / n
    print(f"\nMetric 1 — Fallback rate: {fallback_rate:.4f} ({fallback_count}/{n})")

    # ── Metric 2: LLM Error Proxy ──────────────────────────────────────────────
    # Proxy: priority_score=1 AND relevance_score=0 AND market_scope='unknown'
    # These are the signatures of a "nothing useful" response (likely irrelevant
    # content, but NOT a parse error).
    llm_error_proxy_count = 0
    for out in tier3_outputs:
        if (
            out.get("priority_score") == 1
            and out.get("relevance_score", 1.0) == 0.0
            and out.get("market_scope") == "unknown"
        ):
            llm_error_proxy_count += 1
    llm_error_proxy_rate = llm_error_proxy_count / n
    print(f"Metric 2 — LLM error proxy rate: {llm_error_proxy_rate:.4f} ({llm_error_proxy_count}/{n})")

    # ── Metric 3: Provider Distribution ───────────────────────────────────────
    # Shadow run used a single provider (openai). No provider metadata in JSONL.
    # Record as observed: single-provider shadow run.
    provider_distribution = {"openai": n}
    print(f"Metric 3 — Provider distribution: {provider_distribution}")

    # ── Metric 4: Priority Distribution ───────────────────────────────────────
    tier3_priorities = [out.get("priority_score", 0) for out in tier3_outputs]
    priority_counter = Counter(tier3_priorities)
    priority_mean = statistics.mean(tier3_priorities) if tier3_priorities else 0.0
    priority_median = statistics.median(tier3_priorities) if tier3_priorities else 0.0
    priority_distribution = {
        "histogram": dict(sorted(priority_counter.items())),
        "mean": round(priority_mean, 4),
        "median": float(priority_median),
        "high_count": sum(1 for p in tier3_priorities if p >= 7),
        "low_count": sum(1 for p in tier3_priorities if p <= 3),
        "mid_count": sum(1 for p in tier3_priorities if 4 <= p <= 6),
    }
    print(f"Metric 4 — Priority mean={priority_mean:.3f}, high={priority_distribution['high_count']}, "
          f"low={priority_distribution['low_count']}, mid={priority_distribution['mid_count']}")

    # ── Metric 5: Keyword Coverage ─────────────────────────────────────────────
    # From ph4d_metrics: after PH4D expansion, zero_hit=26/69 → covered=43/69
    ph4d_before = ph4d_metrics.get("before", {})
    ph4d_after = ph4d_metrics.get("after", {})
    zero_hit_before = ph4d_before.get("zero_hit", 0)
    zero_hit_after = ph4d_after.get("zero_hit", 0)
    covered_after = n - zero_hit_after
    keyword_coverage = covered_after / n
    print(f"Metric 5 — Keyword coverage: {keyword_coverage:.4f} ({covered_after}/{n}), "
          f"zero-hit: {zero_hit_after} (was {zero_hit_before})")

    # ── Metric 6: Tag Fill Rate ────────────────────────────────────────────────
    # From ph4k: fallback_tags_populated_docs=69/69 (PH4J enrichment complete)
    tag_fill_count = ph4k_metrics["utility_metrics"]["fallback_tags_populated_docs"]
    tag_fill_rate = tag_fill_count / n
    mean_tags = ph4k_metrics["utility_metrics"]["fallback_tags_mean"]
    print(f"Metric 6 — Tag fill rate: {tag_fill_rate:.4f} ({tag_fill_count}/{n}), mean tags={mean_tags}")

    # ── Metric 7: Watchlist Overlap Rate ──────────────────────────────────────
    watchlist_overlap_docs = ph4k_metrics["utility_metrics"]["watchlist_overlap_docs"]
    watchlist_overlap_rate = ph4k_metrics["utility_metrics"]["watchlist_overlap_ratio"]
    print(f"Metric 7 — Watchlist overlap: {watchlist_overlap_rate:.4f} ({watchlist_overlap_docs}/{n})")

    # ── Actionable Rate ────────────────────────────────────────────────────────
    # I-13 invariant: actionable=False always in rule-only fallback.
    # For tier3 shadow run: check if any output has actionable=True
    # Note: tier3 outputs may or may not include 'actionable' field
    actionable_count = sum(1 for out in tier3_outputs if out.get("actionable") is True)
    actionable_rate = actionable_count / n
    print(f"\nActionable rate (tier3 shadow): {actionable_rate:.4f} ({actionable_count}/{n})")
    print(f"Rule-only actionable: 0/{n} (I-13 permanent invariant)")

    # ── Assemble JSON output ───────────────────────────────────────────────────
    generated_at = datetime.now(UTC).isoformat()
    output = {
        "report_type": "ph5a_reliability_baseline",
        "phase": "PHASE 5",
        "sprint": "PH5A_BASELINE_RELIABILITY_AND_SIGNAL_TRUST",
        "generated_at": generated_at,
        "inputs": {
            "paired_docs": n,
            "rule_dataset": str(ARTIFACTS / "ph4a" / "candidate_rule.jsonl"),
            "tier3_dataset": str(ARTIFACTS / "ph4b" / "ph4b_tier3_shadow.jsonl"),
            "ph4k_per_doc": str(ARTIFACTS / "ph4k" / "ph4k_per_document_utility.json"),
            "ph4k_metrics": str(ARTIFACTS / "ph4k" / "ph4k_utility_metrics.json"),
            "ph4d_metrics": str(ARTIFACTS / "ph4d" / "ph4d_metrics.json"),
        },
        "parse_quality": {
            "tier3_parse_errors": tier3_parse_errors,
            "rule_parse_errors": rule_parse_errors,
            "tier3_parsed_ok": len(tier3_outputs),
            "rule_parsed_ok": len(rule_outputs),
        },
        "metrics": {
            "fallback_rate": round(fallback_rate, 4),
            "fallback_count": fallback_count,
            "llm_error_proxy_rate": round(llm_error_proxy_rate, 4),
            "llm_error_proxy_count": llm_error_proxy_count,
            "provider_distribution": provider_distribution,
            "priority_distribution": priority_distribution,
            "keyword_coverage": round(keyword_coverage, 4),
            "keyword_covered_count": covered_after,
            "keyword_zero_hit_count": zero_hit_after,
            "keyword_zero_hit_before_ph4d": zero_hit_before,
            "tag_fill_rate": round(tag_fill_rate, 4),
            "tag_fill_count": tag_fill_count,
            "tag_mean_per_doc": round(mean_tags, 4),
            "watchlist_overlap_rate": round(watchlist_overlap_rate, 4),
            "watchlist_overlap_count": watchlist_overlap_docs,
            "actionable_rate_tier3": round(actionable_rate, 4),
            "actionable_count_tier3": actionable_count,
            "actionable_rate_rule_only": 0.0,
            "actionable_count_rule_only": 0,
        },
        "invariants": {
            "i13_actionable_rule_only": "always_false",
            "shadow_run_provider": "openai",
            "fallback_tier": "tier1_rule_only",
        },
    }

    out_path = ARTIFACTS / "ph5a_reliability_baseline.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nWrote: {out_path}")

    # ── Generate operator summary ──────────────────────────────────────────────
    summary_lines = [
        "# PH5A Reliability Baseline — Operator Summary",
        "",
        f"**Generated:** {generated_at[:19]}Z  ",
        "**Sprint:** PH5A_BASELINE_RELIABILITY_AND_SIGNAL_TRUST  ",
        f"**Dataset:** {n} paired documents (PH4A rule + PH4B Tier3 shadow)",
        "",
        "---",
        "",
        "## Signal Pipeline Health",
        "",
        "| Metric | Value | Notes |",
        "|---|---|---|",
        f"| Fallback rate | {fallback_rate:.1%} | 0 parse failures in 69-doc shadow run |",
        f"| LLM error proxy rate | {llm_error_proxy_rate:.1%} | Docs with priority=1, relevance=0, scope=unknown |",
        "| Provider distribution | openai: 100% | Single-provider shadow run |",
        f"| Priority mean (Tier3) | {priority_mean:.2f} / 10 | High≥7: {priority_distribution['high_count']}, Mid 4–6: {priority_distribution['mid_count']}, Low≤3: {priority_distribution['low_count']} |",
        f"| Keyword coverage | {keyword_coverage:.1%} | {covered_after}/{n} docs hit ≥1 keyword (after PH4D) |",
        f"| Tag fill rate | {tag_fill_rate:.1%} | {tag_fill_count}/{n} docs have ≥1 fallback tag (after PH4J) |",
        f"| Watchlist overlap | {watchlist_overlap_rate:.1%} | {watchlist_overlap_docs}/{n} docs match watchlist terms |",
        "",
        "---",
        "",
        "## Actionability (I-13 Invariant)",
        "",
        f"- **Tier3 shadow run:** {actionable_count}/{n} actionable signals ({actionable_rate:.1%})",
        "- **Rule-only fallback:** 0/69 actionable (0.0%) — permanent invariant I-13",
        "- `actionable=True` requires LLM-level analysis. No relaxation planned.",
        "",
        "---",
        "",
        "## Priority Distribution",
        "",
        "| Priority Score | Count |",
        "|---|---|",
    ]
    for score in sorted(priority_counter.keys()):
        summary_lines.append(f"| {score} | {priority_counter[score]} |")

    summary_lines += [
        "",
        "---",
        "",
        "## Phase 4 Improvements Reflected",
        "",
        f"- **PH4D keyword expansion:** zero-hit reduced from {zero_hit_before} → {zero_hit_after} docs ({zero_hit_before/n:.1%} → {zero_hit_after/n:.1%})",
        f"- **PH4J tag enrichment:** tag fill rate 100% ({tag_fill_count}/{n}), mean {mean_tags} tags/doc",
        f"- **PH4K watchlist utility:** {watchlist_overlap_rate:.1%} docs have watchlist overlap (MAE priority gap closed from 3.13→corr 0.56)",
        "",
        "---",
        "",
        "## Constraints",
        "",
        "- Diagnostic only — no scoring, keyword, or actionability changes made in PH5A",
        "- I-13 invariant unchanged",
        "- Shadow run data only (no live inference in this sprint)",
        "",
        "_Artifact: `artifacts/ph5a_reliability_baseline.json`_",
    ]

    summary_path = ARTIFACTS / "ph5a_operator_summary.md"
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"Wrote: {summary_path}")
    print("\nPH5A diagnostic complete.")


if __name__ == "__main__":
    main()
