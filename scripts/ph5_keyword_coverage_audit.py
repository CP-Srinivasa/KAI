"""Audit keyword coverage and suggest additions for zero-hit documents."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from app.analysis.keywords.engine import KeywordEngine
from app.core.settings import get_settings
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

OUT_JSON = "ph5_keyword_coverage_report.json"
OUT_MD = "ph5_keyword_coverage_report.md"
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{3,}")
STOPWORDS = {
    "this",
    "that",
    "with",
    "from",
    "will",
    "have",
    "after",
    "before",
    "under",
    "over",
    "between",
    "their",
    "about",
    "amid",
    "into",
    "onto",
    "been",
    "more",
    "less",
    "than",
    "what",
    "when",
    "where",
    "which",
    "while",
    "also",
    "says",
    "said",
    "news",
    "market",
    "markets",
    "crypto",
    "stock",
    "stocks",
    "price",
    "preise",
    "aber",
    "oder",
    "dass",
    "eine",
    "einer",
    "einem",
    "wird",
    "sind",
    "nach",
    "über",
    "mehr",
    "wenn",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=300, help="Max analyzed docs to audit")
    parser.add_argument(
        "--target-coverage",
        type=float,
        default=80.0,
        help="Target coverage percentage",
    )
    parser.add_argument(
        "--suggestions",
        type=int,
        default=30,
        help="Max keyword suggestions",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/ph5_keyword_coverage",
        help="Output directory for report files",
    )
    return parser.parse_args()


def _load_existing_keywords(path: Path) -> set[str]:
    if not path.exists():
        return set()
    terms: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        token = line.strip().lower()
        if not token or token.startswith("#"):
            continue
        terms.add(token)
    return terms


def _tokenize_for_suggestions(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


async def _build_report(args: argparse.Namespace) -> dict[str, object]:
    generated_at = datetime.now(UTC).isoformat()
    settings = get_settings()
    monitor_dir = Path(settings.monitor_dir)
    existing_keywords = _load_existing_keywords(monitor_dir / "keywords.txt")
    keyword_engine = KeywordEngine.from_monitor_dir(monitor_dir)

    try:
        session_factory = build_session_factory(settings.db)
        async with session_factory.begin() as session:
            repo = DocumentRepository(session)
            docs = await repo.list(is_analyzed=True, limit=args.limit)
    except Exception as exc:  # noqa: BLE001
        return {
            "report_type": "ph5_keyword_coverage_report",
            "generated_at": generated_at,
            "status": "db_unavailable",
            "error": str(exc),
            "document_limit": args.limit,
        }

    zero_hit_docs: list[dict[str, object]] = []
    domain_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    suggestion_counts: Counter[str] = Counter()
    covered = 0

    for doc in docs:
        full_text = f"{doc.title or ''} {doc.cleaned_text or doc.raw_text or ''}".strip()
        hits = keyword_engine.match(full_text)
        if hits:
            covered += 1
            continue

        host = urlparse(doc.url).hostname or "unknown"
        domain_counts[host] += 1
        for topic in (doc.topics or []) + (doc.categories or []):
            t = str(topic).strip().lower()
            if t:
                topic_counts[t] += 1

        seed_text = " ".join(
            [
                doc.title or "",
                doc.summary or "",
                " ".join(doc.tags or []),
                " ".join(doc.topics or []),
                " ".join(doc.categories or []),
            ]
        )
        for token in _tokenize_for_suggestions(seed_text):
            if token in STOPWORDS or token in existing_keywords:
                continue
            suggestion_counts[token] += 1

        zero_hit_docs.append(
            {
                "document_id": str(doc.id),
                "title": doc.title,
                "url": doc.url,
                "published_at": doc.published_at.isoformat() if doc.published_at else None,
                "source_name": doc.source_name,
                "topics": doc.topics,
                "categories": doc.categories,
            }
        )

    total = len(docs)
    coverage_pct = round((covered / total * 100.0), 2) if total else None
    gap_to_target = (
        round(max(0.0, float(args.target_coverage) - coverage_pct), 2)
        if coverage_pct is not None
        else None
    )
    report = {
        "report_type": "ph5_keyword_coverage_report",
        "generated_at": generated_at,
        "status": "ok",
        "document_limit": args.limit,
        "audited_documents": total,
        "covered_documents": covered,
        "zero_hit_documents": len(zero_hit_docs),
        "coverage_pct": coverage_pct,
        "target_coverage_pct": float(args.target_coverage),
        "gap_to_target_pct_points": gap_to_target,
        "top_zero_hit_domains": domain_counts.most_common(15),
        "top_zero_hit_topics": topic_counts.most_common(20),
        "keyword_suggestions": [
            {"keyword": kw, "count": count}
            for kw, count in suggestion_counts.most_common(args.suggestions)
        ],
        "zero_hit_sample": zero_hit_docs[:50],
    }
    return report


def _write_report(report: dict[str, object], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_out = output_dir / OUT_JSON
    md_out = output_dir / OUT_MD
    json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# PH5 Keyword Coverage Audit",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Status: `{report.get('status')}`",
        "",
        "## Coverage",
        "",
        f"- audited_documents: {report.get('audited_documents')}",
        f"- covered_documents: {report.get('covered_documents')}",
        f"- zero_hit_documents: {report.get('zero_hit_documents')}",
        f"- coverage_pct: {report.get('coverage_pct')}",
        f"- target_coverage_pct: {report.get('target_coverage_pct')}",
        f"- gap_to_target_pct_points: {report.get('gap_to_target_pct_points')}",
        "",
        "## Top Suggested Keywords",
        "",
    ]
    suggestions = report.get("keyword_suggestions")
    if isinstance(suggestions, list) and suggestions:
        for row in suggestions[:20]:
            if not isinstance(row, dict):
                continue
            lines.append(f"- {row.get('keyword')}: {row.get('count')}")
    else:
        lines.append("- none")

    md_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_out, md_out


async def _run(args: argparse.Namespace) -> int:
    report = await _build_report(args)
    json_out, md_out = _write_report(report, Path(args.output_dir))
    print("PH5 keyword coverage report written:")
    print(f"  {json_out}")
    print(f"  {md_out}")
    print(
        "Status: "
        f"{report.get('status')} "
        f"(coverage={report.get('coverage_pct')}, gap={report.get('gap_to_target_pct_points')})"
    )
    return 0


def main() -> int:
    args = _parse_args()
    import asyncio

    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
