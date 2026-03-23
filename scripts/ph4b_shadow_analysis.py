"""PH4B Shadow Analysis Execution Script.

Sprint: PH4B_TIER3_COVERAGE_EXPANSION
Contract: docs/contracts.md §68 (frozen 2026-03-22)
Decisions: D-55 (contract frozen), D-56 (execution authorized)

Goal: Route existing Tier-1 documents through Tier-3 (external LLM) analysis to create
paired records (same document analyzed by both Tier-1 and Tier-3).

Primary acceptance criterion: paired_count > 0 (I-408)

Non-goals enforced by script:
- No DB writes (shadow mode only — results written to artifacts/ph4b/)
- No new sources (I-406)
- No new providers beyond existing configured ones (I-406)
- No scoring/threshold changes (I-407)
- Fail-closed: if no Tier-3 provider available, writes blocked report and exits cleanly

PH4A baseline anchor:
- 74 records, tier-3 coverage 6.76%, paired_count 0 (artifacts/ph4a/)

Outputs written to artifacts/ph4b/:
1. ph4b_tier3_shadow.jsonl — Tier-3 analysis results (same format as PH4A
   teacher_external_llm.jsonl)
2. ph4b_metrics.json — updated tier coverage, paired_count, SNR
3. ph4b_benchmark.json — benchmark comparison (Tier-1 vs Tier-3 on same documents)
4. ph4b_operator_summary.md — operator-readable pass/fail vs PH4A baseline
5. execution_report.json — execution metadata and status

Usage:
    python scripts/ph4b_shadow_analysis.py [--limit N] [--provider openai|anthropic|gemini]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from datetime import (
    UTC,
    datetime,
)
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
ARTIFACTS_PH4A = PROJECT_ROOT / "artifacts" / "ph4a"
ARTIFACTS_PH4B = PROJECT_ROOT / "artifacts" / "ph4b"
RULE_DATASET = ARTIFACTS_PH4A / "candidate_rule.jsonl"
TEACHER_PH4A = ARTIFACTS_PH4A / "teacher_external_llm.jsonl"

# PH4A frozen baseline (from quality_metrics.json)
PH4A_BASELINE = {
    "records_in_scope": 74,
    "tier3_coverage": 0.06756756756756757,
    "paired_count": 0,
    "signal_to_noise": 0.0,
}

SIGNAL_THRESHOLD = 8  # priority_score >= 8 → actionable signal


# ── Helpers ────────────────────────────────────────────────────────────────

def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file into a list of dicts."""
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def extract_document_info(row: dict) -> tuple[str, str, str]:
    """Extract (document_id, title, content) from a JSONL row."""
    document_id = row.get("metadata", {}).get("document_id", "")
    messages = row.get("messages", [])
    title = ""
    content = ""
    for msg in messages:
        if msg.get("role") == "user":
            text = msg.get("content", "")
            # Extract title and content from the prompt format
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("Title:"):
                    title = line[6:].strip()
                if line.startswith("Content:"):
                    content_lines = lines[i + 1:]
                    content = "\n".join(content_lines).strip()
                    break
            break
    return document_id, title, content


def write_jsonl(path: Path, rows: list[dict]) -> None:
    """Write rows to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def write_json(path: Path, data: dict) -> None:
    """Write data to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def write_blocked_artifacts(reason: str, generated_at: str) -> None:
    """Write blocked execution artifacts (fail-closed output)."""
    ARTIFACTS_PH4B.mkdir(parents=True, exist_ok=True)

    execution_report = {
        "report_type": "ph4b_execution_report",
        "phase": "PHASE 4",
        "sprint": "PH4B_TIER3_COVERAGE_EXPANSION",
        "generated_at": generated_at,
        "status": "blocked",
        "reason": reason,
        "next_required_step": "PH4B_EXECUTION_START",
        "inputs": {
            "rule_dataset": str(RULE_DATASET),
            "teacher_dataset": str(TEACHER_PH4A),
        },
        "baseline_ref": PH4A_BASELINE,
        "observations": {
            "rule_rows": len(load_jsonl(RULE_DATASET)),
            "teacher_rows": len(load_jsonl(TEACHER_PH4A)),
            "generated_rows": 0,
            "paired_count": 0,
        },
    }
    write_json(ARTIFACTS_PH4B / "execution_report.json", execution_report)

    blocked_eval = {
        "report_type": "dataset_evaluation",
        "generated_at": generated_at,
        "dataset_type": "rule_baseline",
        "inputs": {
            "teacher_dataset": str(TEACHER_PH4A),
            "candidate_dataset": str(RULE_DATASET),
        },
        "baseline_count": len(load_jsonl(RULE_DATASET)),
        "teacher_count": len(load_jsonl(TEACHER_PH4A)),
        "paired_count": 0,
        "metrics": {
            "sample_count": 0,
            "missing_pairs": len(load_jsonl(RULE_DATASET)),
            "sentiment_agreement": 0.0,
            "actionable_accuracy": 0.0,
            "priority_mae": 0.0,
            "relevance_mae": 0.0,
            "impact_mae": 0.0,
            "tag_overlap_mean": 0.0,
            "false_actionable_rate": 0.0,
        },
        "notes": [],
    }
    write_json(ARTIFACTS_PH4B / "evaluation_ph4b_blocked.json", blocked_eval)

    benchmark_blocked = {
        "report_type": "ph4b_benchmark",
        "generated_at": generated_at,
        "status": "blocked",
        "reason": reason,
        "paired_count": 0,
        "ph4a_baseline_ref": PH4A_BASELINE,
        "ph4b_result": None,
    }
    write_json(ARTIFACTS_PH4B / "benchmark_ph4b_blocked.json", benchmark_blocked)

    summary_path = ARTIFACTS_PH4B / "operator_execution_summary.md"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write("# PH4B Execution Summary\n\n")
        f.write(f"- Started (UTC): {generated_at}\n")
        f.write("- Status: BLOCKED (fail-closed)\n")
        f.write(f"- Reason: {reason}\n")
        f.write(
            f"- PH4A baseline anchor: {PH4A_BASELINE['records_in_scope']} records, "
            f"tier-3 coverage {PH4A_BASELINE['tier3_coverage'] * 100:.2f}%, "
            f"paired_count {PH4A_BASELINE['paired_count']}\n"
        )
        f.write("- PH4B primary gate (`paired_count > 0`) not yet satisfied\n")

    print(f"[BLOCKED] {reason}")
    print("Blocked artifacts written to artifacts/ph4b/")


async def run_shadow_analysis(
    rule_records: list[dict],
    provider,
    limit: int,
    generated_at: str,
) -> list[dict]:
    """Run Tier-3 shadow analysis on Tier-1 documents. Returns JSONL rows."""
    results = []
    records_to_process = rule_records[:limit]

    print(f"Shadow-analyzing {len(records_to_process)} Tier-1 documents with Tier-3 provider...")

    for i, row in enumerate(records_to_process):
        document_id, title, content = extract_document_info(row)
        if not document_id:
            print(f"  [{i+1}] SKIP: no document_id")
            continue

        try:
            output = await provider.analyze(title=title, text=content)
            scores = {
                "affected_assets": [],
                "impact_score": output.impact_score,
                "market_scope": str(output.market_scope) if output.market_scope else "unknown",
                "novelty_score": output.novelty_score if hasattr(output, "novelty_score") else 0.0,
                "priority_score": output.recommended_priority,
                "relevance_score": output.relevance_score,
                "sentiment_label": str(output.sentiment_label),
                "sentiment_score": (
                    output.sentiment_score if hasattr(output, "sentiment_score") else 0.0
                ),
                "spam_probability": 0.0,
                "summary": output.short_reasoning if hasattr(output, "short_reasoning") else "",
                "tags": output.tags if hasattr(output, "tags") else [],
            }
            jsonl_row = {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a highly precise financial AI analyst.",
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Analyze the following financial document and determine "
                            f"sentiment, relevance, impact, novelty, and priority score (1-10).\n\n"
                            f"Title: {title}\nSource: Manual\n\nContent:\n{content}"
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": json.dumps(scores),
                    },
                ],
                "metadata": {
                    "document_id": document_id,
                    "provider": provider.provider_name,
                    "analysis_source": "external_llm",
                },
            }
            results.append(jsonl_row)
            prio = scores["priority_score"]
            print(f"  [{i+1}/{len(records_to_process)}] OK: {document_id[:8]}... priority={prio}")
        except Exception as e:
            print(f"  [{i+1}/{len(records_to_process)}] ERROR: {document_id[:8]}... {e}")

    return results


def compute_metrics(
    rule_records: list[dict],
    tier3_records: list[dict],
) -> dict:
    """Compute PH4B metrics: paired_count, tier coverage, SNR, score comparison."""
    def _doc_id(r: dict) -> str:
        return r.get("metadata", {}).get("document_id", "")

    rule_ids = {_doc_id(r) for r in rule_records if _doc_id(r)}
    tier3_ids = {_doc_id(r) for r in tier3_records if _doc_id(r)}
    paired_ids = rule_ids & tier3_ids

    total_in_scope = len(rule_ids)
    paired_count = len(paired_ids)
    new_tier3_count = len(tier3_ids)
    tier3_coverage = new_tier3_count / total_in_scope if total_in_scope > 0 else 0.0

    # SNR: documents with priority_score >= SIGNAL_THRESHOLD
    all_records = rule_records + tier3_records
    actionable_ids: set[str] = set()
    for r in all_records:
        doc_id = r.get("metadata", {}).get("document_id", "")
        try:
            scores = json.loads(r["messages"][2]["content"])
            if scores.get("priority_score", 0) >= SIGNAL_THRESHOLD:
                actionable_ids.add(doc_id)
        except (KeyError, json.JSONDecodeError, IndexError):
            pass
    signal_to_noise = len(actionable_ids) / total_in_scope if total_in_scope > 0 else 0.0

    # Score comparison for paired documents
    tier1_by_id = {
        _doc_id(r): r for r in rule_records if _doc_id(r)
    }
    tier3_by_id = {
        _doc_id(r): r for r in tier3_records if _doc_id(r)
    }

    score_deltas: dict[str, list[float]] = {
        "relevance_delta": [],
        "priority_delta": [],
        "impact_delta": [],
    }
    for doc_id in paired_ids:
        try:
            t1_scores = json.loads(tier1_by_id[doc_id]["messages"][2]["content"])
            t3_scores = json.loads(tier3_by_id[doc_id]["messages"][2]["content"])
            score_deltas["relevance_delta"].append(
                abs(t3_scores.get("relevance_score", 0) - t1_scores.get("relevance_score", 0))
            )
            score_deltas["priority_delta"].append(
                abs(t3_scores.get("priority_score", 0) - t1_scores.get("priority_score", 0))
            )
            score_deltas["impact_delta"].append(
                abs(t3_scores.get("impact_score", 0) - t1_scores.get("impact_score", 0))
            )
        except (KeyError, json.JSONDecodeError, IndexError):
            pass

    def safe_mean(vals: list[float]) -> float:
        return round(statistics.mean(vals), 4) if vals else 0.0

    return {
        "total_in_scope": total_in_scope,
        "tier3_count": new_tier3_count,
        "tier3_coverage": round(tier3_coverage, 4),
        "paired_count": paired_count,
        "signal_to_noise": round(signal_to_noise, 4),
        "ph4a_baseline": PH4A_BASELINE,
        "delta_vs_baseline": {
            "tier3_coverage": round(tier3_coverage - PH4A_BASELINE["tier3_coverage"], 4),
            "paired_count": paired_count - PH4A_BASELINE["paired_count"],
        },
        "score_comparison_paired": {
            "relevance_mae": safe_mean(score_deltas["relevance_delta"]),
            "priority_mae": safe_mean(score_deltas["priority_delta"]),
            "impact_mae": safe_mean(score_deltas["impact_delta"]),
            "sample_count": paired_count,
        },
    }


async def main(provider_name: str, limit: int) -> None:
    generated_at = datetime.now(UTC).isoformat()

    # Load PH4A Tier-1 records
    rule_records = load_jsonl(RULE_DATASET)
    if not rule_records:
        write_blocked_artifacts("rule_dataset_missing", generated_at)
        return

    # Initialize Tier-3 provider (fail-closed)
    try:
        from app.analysis.factory import create_provider
        from app.core.settings import get_settings

        settings = get_settings()
        provider = create_provider(provider_name, settings)
    except Exception as e:
        write_blocked_artifacts(f"provider_init_error: {e}", generated_at)
        return

    if provider is None:
        write_blocked_artifacts("no_external_tier3_provider_available", generated_at)
        return

    print(f"Provider ready: {provider.provider_name} ({getattr(provider, 'model', 'unknown')})")
    print(f"Input: {len(rule_records)} Tier-1 records from {RULE_DATASET}")

    # Run shadow analysis
    ARTIFACTS_PH4B.mkdir(parents=True, exist_ok=True)
    tier3_results = await run_shadow_analysis(rule_records, provider, limit, generated_at)

    if not tier3_results:
        write_blocked_artifacts("shadow_analysis_produced_no_results", generated_at)
        return

    # Write shadow results JSONL
    shadow_path = ARTIFACTS_PH4B / "ph4b_tier3_shadow.jsonl"
    write_jsonl(shadow_path, tier3_results)
    print(f"Shadow results written: {len(tier3_results)} rows -> {shadow_path}")

    # Compute metrics
    metrics = compute_metrics(rule_records, tier3_results)
    paired_count = metrics["paired_count"]
    tier3_coverage = metrics["tier3_coverage"]

    # Write metrics
    write_json(ARTIFACTS_PH4B / "ph4b_metrics.json", {
        "report_type": "ph4b_quality_metrics",
        "phase": "PHASE 4",
        "sprint": "PH4B_TIER3_COVERAGE_EXPANSION",
        "generated_at": generated_at,
        "contract_ref": "docs/contracts.md §68",
        "ph4a_baseline_ref": "docs/contracts.md §67",
        "metrics": metrics,
    })

    # Write benchmark
    benchmark = {
        "report_type": "ph4b_benchmark",
        "generated_at": generated_at,
        "paired_count": paired_count,
        "status": "data" if paired_count > 0 else "needs_more_data",
        "ph4a_baseline": PH4A_BASELINE,
        "ph4b_result": {
            "tier3_coverage": tier3_coverage,
            "paired_count": paired_count,
            "signal_to_noise": metrics["signal_to_noise"],
        },
        "delta": metrics["delta_vs_baseline"],
        "score_comparison": metrics["score_comparison_paired"],
    }
    write_json(ARTIFACTS_PH4B / "ph4b_benchmark.json", benchmark)

    # Determine pass/fail
    ph4b_pass = paired_count > 0
    status_label = "PASS" if ph4b_pass else "FAIL"
    coverage_pct = tier3_coverage * 100

    # Write operator summary
    summary_path = ARTIFACTS_PH4B / "ph4b_operator_summary.md"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write("# PH4B Execution Summary\n\n")
        f.write("- Sprint: PH4B_TIER3_COVERAGE_EXPANSION\n")
        f.write("- Contract: docs/contracts.md §68\n")
        f.write(f"- Executed (UTC): {generated_at}\n")
        f.write(f"- Provider: {provider.provider_name}\n\n")
        f.write("## Pass/Fail Statement\n\n")
        f.write(f"**PH4B execution status: {status_label}**\n\n")
        if ph4b_pass:
            f.write(f"Primary gate satisfied: `paired_count = {paired_count} > 0`\n\n")
        else:
            f.write(
                "Primary gate NOT satisfied: `paired_count = 0`"
                " (coverage improvement without paired records)\n\n"
            )
        f.write("## Core Metrics\n\n")
        t3_cnt = metrics["tier3_count"]
        t_scope = metrics["total_in_scope"]
        f.write(f"- Tier-3 coverage: {coverage_pct:.2f}% ({t3_cnt}/{t_scope})\n")
        f.write(f"- Paired count (Tier-1 ∩ Tier-3): {paired_count}\n")
        snr = metrics["signal_to_noise"] * 100
        f.write(f"- Signal-to-noise (priority >= {SIGNAL_THRESHOLD}): {snr:.2f}%\n\n")
        f.write("## Delta vs PH4A Baseline\n\n")
        f.write("| Metric | PH4A | PH4B | Delta |\n")
        f.write("|---|---|---|---|\n")
        ph4a_t3 = PH4A_BASELINE["tier3_coverage"] * 100
        delta_t3 = metrics["delta_vs_baseline"]["tier3_coverage"] * 100
        f.write(f"| Tier-3 coverage | {ph4a_t3:.2f}% | {coverage_pct:.2f}% | {delta_t3:+.2f}% |\n")
        ph4a_pc = PH4A_BASELINE["paired_count"]
        delta_pc = metrics["delta_vs_baseline"]["paired_count"]
        f.write(f"| Paired count | {ph4a_pc} | {paired_count} | {delta_pc:+d} |\n")
        f.write(f"| Signal-to-noise | 0.00% | {snr:.2f}% | {snr:+.2f}% |\n")

    # Write execution report
    write_json(ARTIFACTS_PH4B / "execution_report.json", {
        "report_type": "ph4b_execution_report",
        "phase": "PHASE 4",
        "sprint": "PH4B_TIER3_COVERAGE_EXPANSION",
        "generated_at": generated_at,
        "status": "complete",
        "ph4b_pass": ph4b_pass,
        "provider": provider.provider_name,
        "inputs": {"rule_dataset": str(RULE_DATASET)},
        "baseline_ref": PH4A_BASELINE,
        "observations": {
            "rule_rows": len(rule_records),
            "generated_rows": len(tier3_results),
            "paired_count": paired_count,
            "tier3_coverage": tier3_coverage,
        },
        "next_required_step": "PH4B_RESULTS_REVIEW" if ph4b_pass else "PH4B_EXECUTION_START",
    })

    print(f"\n{'='*60}")
    print(f"PH4B Execution {status_label}")
    print(f"paired_count={paired_count}, tier3_coverage={coverage_pct:.1f}%")
    print(f"Artifacts: {ARTIFACTS_PH4B}")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PH4B Shadow Analysis")
    parser.add_argument("--provider", default="openai", choices=["openai", "anthropic", "gemini"],
                        help="Tier-3 provider to use")
    parser.add_argument("--limit", type=int, default=69,
                        help="Max Tier-1 documents to process (default: all 69)")
    args = parser.parse_args()

    asyncio.run(main(provider_name=args.provider, limit=args.limit))
