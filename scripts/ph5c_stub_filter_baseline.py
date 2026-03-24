"""PH5C Stub Document Pre-Filter Baseline.

Validates a content-length threshold for pre-LLM stub detection.
Research questions:
1. How many docs fall below threshold=50 bytes?
2. Are any below-threshold docs non-stub (valid short documents)?
3. What is the projected proxy-rate reduction after filtering?
4. What is the recommended threshold?

Generates:
- artifacts/ph5c/ph5c_stub_filter_baseline.json
- artifacts/ph5c/ph5c_operator_summary.md
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

BASE = Path(__file__).parent.parent
ARTIFACTS = BASE / "artifacts"

PROPOSED_THRESHOLD = 50  # bytes
PROXY_SIGNATURE = {"priority_score": 1, "relevance_score": 0.0, "market_scope": "unknown"}

# Finance/crypto topic keywords — used to detect valid short docs
FINANCE_KEYWORDS = {
    "bitcoin", "btc", "crypto", "ethereum", "eth", "defi", "token", "blockchain",
    "stock", "equity", "market", "trading", "invest", "fund", "usd", "eur",
    "currency", "rate", "inflation", "fed", "portfolio", "asset", "yield",
    "bond", "derivative", "futures", "options", "volatility", "liquidity",
    "nasdaq", "s&p", "sp500", "dow", "index", "etf", "reit", "forex",
}


def load_jsonl(path: Path) -> list[dict]:
    docs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def extract_meta(doc: dict) -> dict:
    messages = doc.get("messages", [])
    user_content = ""
    for m in messages:
        if m.get("role") == "user":
            user_content = m.get("content", "")
            break

    title = source = raw_content = ""
    for line in user_content.split("\n"):
        if line.startswith("Title:"):
            title = line[6:].strip()
        elif line.startswith("Source:"):
            source = line[7:].strip()

    ci = user_content.find("Content:")
    if ci >= 0:
        raw_content = user_content[ci + 8:].strip()

    llm_out: dict = {}
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            try:
                llm_out = json.loads(msg["content"])
            except (json.JSONDecodeError, KeyError):
                pass
            break

    return {
        "document_id": doc.get("metadata", {}).get("document_id", ""),
        "title": title,
        "source": source,
        "raw_content": raw_content,
        "content_len": len(raw_content),
        "llm_out": llm_out,
    }


def is_proxy(m: dict) -> bool:
    o = m["llm_out"]
    return (
        o.get("priority_score") == 1
        and float(o.get("relevance_score", 1.0)) == 0.0
        and o.get("market_scope") == "unknown"
    )


def has_finance_signal(m: dict) -> bool:
    text = (m["title"] + " " + m["raw_content"]).lower()
    return any(kw in text for kw in FINANCE_KEYWORDS)


def stub_classification(m: dict, threshold: int) -> str:
    """Classify a below-threshold document."""
    if m["content_len"] > threshold:
        return "above_threshold"
    if has_finance_signal(m):
        return "short_finance"   # short but potentially valid
    return "stub"                # no finance signal — safe to filter


def main() -> None:
    print("PH5C: Loading dataset...")
    tier3_docs = load_jsonl(ARTIFACTS / "ph4b" / "ph4b_tier3_shadow.jsonl")
    n = len(tier3_docs)
    assert n == 69, f"Expected 69 docs, got {n}"  # noqa: S101
    print(f"  Loaded {n} documents.")

    docs = [extract_meta(d) for d in tier3_docs]
    proxy_docs = [d for d in docs if is_proxy(d)]
    n_proxy = len(proxy_docs)
    print(f"  Proxy docs (LLM error signature): {n_proxy}/{n}")

    # ── Threshold analysis ─────────────────────────────────────────────────────
    thresholds = [10, 20, 30, 50, 100, 200]
    threshold_analysis = {}

    print("\n  Threshold scan:")
    print(f"  {'Threshold':>10}  {'Below':>6}  {'Stub':>6}  {'ShortFin':>9}  {'ProxyCaught':>12}  {'FalsePos':>9}")
    for thr in thresholds:
        below = [d for d in docs if d["content_len"] <= thr]
        stubs = [d for d in below if stub_classification(d, thr) == "stub"]
        short_fin = [d for d in below if stub_classification(d, thr) == "short_finance"]
        proxy_caught = [d for d in below if is_proxy(d)]
        false_pos = [d for d in below if not is_proxy(d) and stub_classification(d, thr) != "short_finance"]
        threshold_analysis[thr] = {
            "below_threshold": len(below),
            "stub_count": len(stubs),
            "short_finance_count": len(short_fin),
            "proxy_caught": len(proxy_caught),
            "false_positives": len(false_pos),
            "proxy_catch_rate": round(len(proxy_caught) / n_proxy, 4) if n_proxy else 0.0,
            "precision": round(len(proxy_caught) / len(below), 4) if below else 0.0,
        }
        print(f"  {thr:>10}  {len(below):>6}  {len(stubs):>6}  {len(short_fin):>9}  {len(proxy_caught):>12}  {len(false_pos):>9}")

    # ── Recommended threshold ──────────────────────────────────────────────────
    # Prefer the lowest threshold with 100% proxy catch rate and 0 false positives
    recommended_thr = PROPOSED_THRESHOLD
    for thr in sorted(thresholds):
        ta = threshold_analysis[thr]
        if ta["proxy_caught"] == n_proxy and ta["false_positives"] == 0:
            recommended_thr = thr
            break

    rec = threshold_analysis[recommended_thr]
    projected_proxy_rate = round((n_proxy - rec["proxy_caught"]) / n, 4)
    print(f"\n  Recommended threshold: {recommended_thr} bytes")
    print(f"  Proxy catch rate: {rec['proxy_catch_rate']:.1%} ({rec['proxy_caught']}/{n_proxy})")
    print(f"  False positives: {rec['false_positives']}")
    print(f"  Projected proxy rate after filter: {projected_proxy_rate:.1%}")

    # ── Per-doc stub classification at recommended threshold ───────────────────
    stub_docs = []
    for d in docs:
        cls = stub_classification(d, recommended_thr)
        if cls in ("stub", "short_finance"):
            stub_docs.append({
                "document_id": d["document_id"],
                "title": d["title"],
                "source": d["source"],
                "content_len": d["content_len"],
                "classification": cls,
                "is_proxy": is_proxy(d),
            })

    # ── Source breakdown ───────────────────────────────────────────────────────
    below_thr = [d for d in docs if d["content_len"] <= recommended_thr]
    src_counter = Counter(d["source"] for d in below_thr)
    print(f"\n  Below-threshold source breakdown: {dict(src_counter)}")

    # ── Assemble output ────────────────────────────────────────────────────────
    generated_at = datetime.now(UTC).isoformat()
    output = {
        "report_type": "ph5c_stub_filter_baseline",
        "phase": "PHASE 5",
        "sprint": "PH5C_FILTER_BEFORE_LLM_BASELINE",
        "generated_at": generated_at,
        "inputs": {
            "total_docs": n,
            "proxy_docs": n_proxy,
            "tier3_dataset": str(ARTIFACTS / "ph4b" / "ph4b_tier3_shadow.jsonl"),
        },
        "proxy_signature": PROXY_SIGNATURE,
        "threshold_analysis": threshold_analysis,
        "recommendation": {
            "threshold_bytes": recommended_thr,
            "below_threshold_docs": rec["below_threshold"],
            "stub_docs": rec["stub_count"],
            "short_finance_docs": rec["short_finance_count"],
            "proxy_catch_rate": rec["proxy_catch_rate"],
            "false_positives": rec["false_positives"],
            "projected_proxy_rate_after_filter": projected_proxy_rate,
            "projected_proxy_rate_reduction": round(n_proxy / n - projected_proxy_rate, 4),
        },
        "stub_documents": stub_docs,
    }

    out_dir = ARTIFACTS / "ph5c"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "ph5c_stub_filter_baseline.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote: {out_path}")

    # ── Operator summary ───────────────────────────────────────────────────────
    rec_ta = threshold_analysis[recommended_thr]
    lines = [
        "# PH5C Stub Filter Baseline — Operator Summary",
        "",
        f"**Generated:** {generated_at[:19]}Z  ",
        "**Sprint:** PH5C_FILTER_BEFORE_LLM_BASELINE  ",
        f"**Dataset:** {n} tier3 documents — {n_proxy} proxy docs",
        "",
        "---",
        "",
        "## Recommendation",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Recommended threshold | **{recommended_thr} bytes** |",
        f"| Below-threshold docs | {rec_ta['below_threshold']}/{n} |",
        f"| Stub docs (safe to filter) | {rec_ta['stub_count']} |",
        f"| Short finance docs (keep) | {rec_ta['short_finance_count']} |",
        f"| Proxy catch rate | {rec_ta['proxy_catch_rate']:.1%} ({rec_ta['proxy_caught']}/{n_proxy}) |",
        f"| False positives | {rec_ta['false_positives']} |",
        f"| Projected proxy rate after filter | **{projected_proxy_rate:.1%}** (was 27.5%) |",
        f"| Projected reduction | {round(n_proxy/n - projected_proxy_rate, 4):.1%} |",
        "",
        "---",
        "",
        "## Threshold Scan",
        "",
        "| Threshold (bytes) | Below | Stubs | Short-Finance | Proxy Caught | False Pos | Precision |",
        "|---|---|---|---|---|---|---|",
    ]
    for thr in thresholds:
        ta = threshold_analysis[thr]
        marker = " ← recommended" if thr == recommended_thr else ""
        lines.append(
            f"| {thr}{marker} | {ta['below_threshold']} | {ta['stub_count']} | "
            f"{ta['short_finance_count']} | {ta['proxy_caught']}/{n_proxy} | "
            f"{ta['false_positives']} | {ta['precision']:.0%} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Stub Document Index",
        "",
        f"Documents at threshold={recommended_thr} bytes:",
        "",
        "| # | Title | Source | Len | Class | Proxy? |",
        "|---|---|---|---|---|---|",
    ]
    for i, d in enumerate(stub_docs, 1):
        lines.append(
            f"| {i} | {d['title'][:50]} | {d['source']} | "
            f"{d['content_len']} | {d['classification']} | {'yes' if d['is_proxy'] else 'no'} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Constraints",
        "",
        "- Diagnostic only — no production code changes in PH5C",
        "- I-13 invariant unchanged",
        "- Threshold recommendation is a candidate for PH5D implementation",
        "",
        "_Artifact: `artifacts/ph5c/ph5c_stub_filter_baseline.json`_",
    ]

    summary_path = out_dir / "ph5c_operator_summary.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote: {summary_path}")
    print("\nPH5C diagnostic complete.")


if __name__ == "__main__":
    main()
