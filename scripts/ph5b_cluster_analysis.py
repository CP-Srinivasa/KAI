"""PH5B Low Signal Cluster Analysis.

Classifies the 19 LLM-error-proxy documents identified in PH5A.
Proxy signature: priority_score=1, relevance_score=0, market_scope=unknown.

Research questions:
1. What is the root cause of the proxy signature?
2. Are there sub-clusters within the 19 docs?
3. What remediation strategy is appropriate?

Generates:
- artifacts/ph5b_cluster_analysis.json
- artifacts/ph5b_operator_summary.md
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

BASE = Path(__file__).parent.parent
ARTIFACTS = BASE / "artifacts"

# Clustering thresholds
EMPTY_CONTENT_THRESHOLD = 20  # bytes — content shorter than this is considered empty


def load_jsonl(path: Path) -> list[dict]:
    docs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def extract_doc_metadata(doc: dict) -> dict:
    """Extract title, source, content and LLM output from tier3 shadow format."""
    messages = doc.get("messages", [])
    user_content = ""
    for m in messages:
        if m.get("role") == "user":
            user_content = m.get("content", "")
            break

    title = ""
    source = ""
    raw_content = ""
    for line in user_content.split("\n"):
        if line.startswith("Title:"):
            title = line.replace("Title:", "").strip()
        elif line.startswith("Source:"):
            source = line.replace("Source:", "").strip()

    content_start = user_content.find("Content:")
    if content_start >= 0:
        raw_content = user_content[content_start + 8:].strip()

    llm_output: dict | None = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            try:
                llm_output = json.loads(msg["content"])
            except (json.JSONDecodeError, KeyError):
                llm_output = None
            break

    metadata = doc.get("metadata", {})

    return {
        "document_id": metadata.get("document_id", ""),
        "provider": metadata.get("provider", "unknown"),
        "title": title,
        "source": source,
        "raw_content": raw_content,
        "content_len": len(raw_content),
        "llm_output": llm_output or {},
    }


def classify_cluster(doc_meta: dict) -> str:
    """Assign a cluster label to a proxy document.

    Cluster taxonomy:
    - EMPTY_MANUAL: source=Manual with placeholder/empty content
    - EMPTY_FEED: non-Manual source with near-empty content
    - OFF_TOPIC: content present but clearly non-finance/non-crypto topic
    - PARSE_DEGRADED: LLM output structurally present but all fields zeroed/unknown
    - UNKNOWN: does not fit other clusters
    """
    content = doc_meta["raw_content"]
    source = doc_meta["source"]
    content_len = doc_meta["content_len"]

    # Primary cluster: empty Manual source
    if source == "Manual" and content_len <= EMPTY_CONTENT_THRESHOLD:
        return "EMPTY_MANUAL"

    # Empty non-Manual source
    if content_len <= EMPTY_CONTENT_THRESHOLD:
        return "EMPTY_FEED"

    # Content present — check topic signal
    finance_keywords = {
        "bitcoin", "crypto", "ethereum", "defi", "token", "blockchain",
        "stock", "equity", "market", "trading", "invest", "fund",
        "usd", "eur", "currency", "rate", "inflation", "fed",
        "portfolio", "asset", "yield", "bond", "derivative",
    }
    content_lower = (content + " " + doc_meta["title"]).lower()
    if not any(kw in content_lower for kw in finance_keywords):
        return "OFF_TOPIC"

    return "PARSE_DEGRADED"


def main() -> None:
    print("PH5B: Loading datasets...")

    tier3_docs = load_jsonl(ARTIFACTS / "ph4b" / "ph4b_tier3_shadow.jsonl")
    n = len(tier3_docs)
    assert n == 69, f"Expected 69 tier3 docs, got {n}"  # noqa: S101
    print(f"  Loaded {n} tier3 documents.")

    # ── Identify proxy docs ────────────────────────────────────────────────────
    all_parsed = []
    for doc in tier3_docs:
        meta = extract_doc_metadata(doc)
        all_parsed.append(meta)

    proxy_docs = [
        m for m in all_parsed
        if (
            m["llm_output"].get("priority_score") == 1
            and m["llm_output"].get("relevance_score", 1.0) == 0.0
            and m["llm_output"].get("market_scope") == "unknown"
        )
    ]
    non_proxy_docs = [m for m in all_parsed if m not in proxy_docs]

    n_proxy = len(proxy_docs)
    print(f"\n  Proxy docs (priority=1, relevance=0, scope=unknown): {n_proxy}/{n}")

    # ── Cluster proxy docs ─────────────────────────────────────────────────────
    for meta in proxy_docs:
        meta["cluster"] = classify_cluster(meta)

    cluster_counter = Counter(m["cluster"] for m in proxy_docs)
    print("\n  Cluster distribution:")
    for cluster, count in sorted(cluster_counter.items()):
        print(f"    {cluster}: {count}")

    # ── Source distribution ────────────────────────────────────────────────────
    proxy_sources = Counter(m["source"] for m in proxy_docs)
    all_sources = Counter(m["source"] for m in all_parsed)
    print(f"\n  Proxy sources: {dict(proxy_sources)}")
    print(f"  All sources:   {dict(all_sources)}")

    # ── Content length analysis ────────────────────────────────────────────────
    proxy_lengths = [m["content_len"] for m in proxy_docs]
    non_proxy_lengths = [m["content_len"] for m in non_proxy_docs]

    def _stats(vals: list[int]) -> dict:
        if not vals:
            return {}
        return {
            "min": min(vals),
            "max": max(vals),
            "mean": round(sum(vals) / len(vals), 1),
            "empty_count": sum(1 for v in vals if v <= EMPTY_CONTENT_THRESHOLD),
        }

    proxy_len_stats = _stats(proxy_lengths)
    non_proxy_len_stats = _stats(non_proxy_lengths)
    print(f"\n  Proxy content lengths: {proxy_len_stats}")
    print(f"  Non-proxy content lengths: {non_proxy_len_stats}")

    # ── Root cause summary ─────────────────────────────────────────────────────
    dominant_cluster = cluster_counter.most_common(1)[0][0]
    dominant_count = cluster_counter.most_common(1)[0][1]
    empty_fraction = proxy_len_stats.get("empty_count", 0) / n_proxy if n_proxy else 0

    print(f"\n  Dominant cluster: {dominant_cluster} ({dominant_count}/{n_proxy})")
    print(f"  Empty content fraction: {empty_fraction:.1%}")

    root_cause = (
        "Empty Manual sources: all 19 proxy docs are source=Manual with placeholder content "
        "('Comments' / <20 bytes). LLM receives title-only documents with no analyzable body. "
        "Correct LLM response: low priority/relevance/scope — not a model failure."
        if dominant_cluster == "EMPTY_MANUAL" and dominant_count == n_proxy
        else "Mixed root causes — see cluster breakdown."
    )
    print(f"\n  Root cause: {root_cause}")

    # ── Remediation candidates ─────────────────────────────────────────────────
    recommendations = []
    if cluster_counter.get("EMPTY_MANUAL", 0) > 0:
        recommendations.append({
            "cluster": "EMPTY_MANUAL",
            "count": cluster_counter["EMPTY_MANUAL"],
            "action": "FILTER_BEFORE_LLM",
            "description": (
                "Skip LLM analysis for Manual-source docs with content_len < threshold. "
                "Tag as 'stub_document'. Reduces LLM cost; does not change actionable rate."
            ),
            "priority": "high",
        })
    if cluster_counter.get("EMPTY_FEED", 0) > 0:
        recommendations.append({
            "cluster": "EMPTY_FEED",
            "count": cluster_counter["EMPTY_FEED"],
            "action": "IMPROVE_INGESTION",
            "description": "Improve ingestion to ensure feed documents have body content before analysis.",
            "priority": "medium",
        })
    if cluster_counter.get("OFF_TOPIC", 0) > 0:
        recommendations.append({
            "cluster": "OFF_TOPIC",
            "count": cluster_counter["OFF_TOPIC"],
            "action": "TOPIC_FILTER",
            "description": "Add pre-filter to skip non-finance documents before LLM analysis.",
            "priority": "medium",
        })

    # ── Assemble JSON output ───────────────────────────────────────────────────
    generated_at = datetime.now(UTC).isoformat()
    output = {
        "report_type": "ph5b_cluster_analysis",
        "phase": "PHASE 5",
        "sprint": "PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS",
        "generated_at": generated_at,
        "inputs": {
            "total_docs": n,
            "proxy_docs": n_proxy,
            "non_proxy_docs": len(non_proxy_docs),
            "tier3_dataset": str(ARTIFACTS / "ph4b" / "ph4b_tier3_shadow.jsonl"),
        },
        "proxy_signature": {
            "priority_score": 1,
            "relevance_score": 0.0,
            "market_scope": "unknown",
        },
        "cluster_distribution": dict(cluster_counter),
        "source_distribution": {
            "proxy": dict(proxy_sources),
            "all": dict(all_sources),
        },
        "content_length_analysis": {
            "proxy": proxy_len_stats,
            "non_proxy": non_proxy_len_stats,
            "empty_threshold_bytes": EMPTY_CONTENT_THRESHOLD,
        },
        "root_cause": root_cause,
        "recommendations": recommendations,
        "proxy_documents": [
            {
                "document_id": m["document_id"],
                "title": m["title"],
                "source": m["source"],
                "content_len": m["content_len"],
                "cluster": m["cluster"],
            }
            for m in proxy_docs
        ],
    }

    out_dir = ARTIFACTS / "ph5b"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "ph5b_cluster_analysis.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote: {out_path}")

    # ── Operator summary ───────────────────────────────────────────────────────
    lines = [
        "# PH5B Low Signal Cluster Analysis — Operator Summary",
        "",
        f"**Generated:** {generated_at[:19]}Z  ",
        "**Sprint:** PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS  ",
        f"**Dataset:** {n} tier3 documents — {n_proxy} proxy docs analysed",
        "",
        "---",
        "",
        "## Root Cause",
        "",
        f"> {root_cause}",
        "",
        "---",
        "",
        "## Cluster Breakdown",
        "",
        "| Cluster | Count | % of Proxy | Description |",
        "|---|---|---|---|",
    ]
    cluster_descriptions = {
        "EMPTY_MANUAL": "Manual source, placeholder content only ('Comments' / <20 bytes)",
        "EMPTY_FEED": "Feed source, body content missing at ingestion time",
        "OFF_TOPIC": "Content present but no finance/crypto keyword signal",
        "PARSE_DEGRADED": "Content present, but LLM produced all-zero output",
        "UNKNOWN": "Does not fit any defined cluster",
    }
    for cluster, count in sorted(cluster_counter.items()):
        pct = count / n_proxy * 100
        desc = cluster_descriptions.get(cluster, "")
        lines.append(f"| {cluster} | {count} | {pct:.1f}% | {desc} |")

    lines += [
        "",
        "---",
        "",
        "## Content Length Analysis",
        "",
        "| Group | Min | Max | Mean | Empty (≤20B) |",
        "|---|---|---|---|---|",
        (
            f"| Proxy ({n_proxy} docs) | {proxy_len_stats.get('min',0)} | "
            f"{proxy_len_stats.get('max',0)} | {proxy_len_stats.get('mean',0)} | "
            f"{proxy_len_stats.get('empty_count',0)} |"
        ),
        (
            f"| Non-proxy ({len(non_proxy_docs)} docs) | {non_proxy_len_stats.get('min',0)} | "
            f"{non_proxy_len_stats.get('max',0)} | {non_proxy_len_stats.get('mean',0)} | "
            f"{non_proxy_len_stats.get('empty_count',0)} |"
        ),
        "",
        "---",
        "",
        "## Recommendations",
        "",
    ]
    for rec in recommendations:
        lines += [
            f"### {rec['action']} (cluster: {rec['cluster']}, {rec['count']} docs, priority: {rec['priority']})",
            "",
            rec["description"],
            "",
        ]

    lines += [
        "---",
        "",
        "## Proxy Document Index",
        "",
        "| # | Title | Source | Content Len | Cluster |",
        "|---|---|---|---|---|",
    ]
    for i, m in enumerate(proxy_docs, 1):
        lines.append(
            f"| {i} | {m['title'][:55]} | {m['source']} | {m['content_len']} | {m['cluster']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Constraints",
        "",
        "- Diagnostic only — no production code changes in PH5B",
        "- I-13 invariant unchanged",
        "- Recommendations are candidates for PH5C implementation",
        "",
        "_Artifact: `artifacts/ph5b/ph5b_cluster_analysis.json`_",
    ]

    summary_path = out_dir / "ph5b_operator_summary.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote: {summary_path}")
    print("\nPH5B diagnostic complete.")


if __name__ == "__main__":
    main()
