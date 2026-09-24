"""Replay the production analysis gates offline with a recording stub provider."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput  # noqa: E402
from app.analysis.keywords.engine import KeywordEngine  # noqa: E402
from app.analysis.pipeline import AnalysisPipeline  # noqa: E402
from app.core.domain.document import CanonicalDocument  # noqa: E402
from app.core.enums import MarketScope, SentimentLabel  # noqa: E402

SCHEMA_VERSION = "jev-pipeline-replay/v1"
DB_COLUMNS = {
    "doc_id",
    "id",
    "external_id",
    "source_id",
    "source_name",
    "source_type",
    "document_type",
    "provider",
    "analysis_source",
    "url",
    "title",
    "author",
    "language",
    "market_scope",
    "published_at",
    "fetched_at",
    "raw_text",
    "cleaned_text",
    "summary",
    "content_hash",
    "sentiment_label",
    "sentiment_score",
    "directional_confidence",
    "relevance_score",
    "impact_score",
    "novelty_score",
    "credibility_score",
    "spam_probability",
    "priority_score",
    "status",
    "is_duplicate",
    "is_analyzed",
    "entity_mentions",
    "entities",
    "tickers",
    "crypto_assets",
    "people",
    "organizations",
    "tags",
    "topics",
    "categories",
    "youtube_meta",
    "podcast_meta",
    "metadata",
}


#: Columns written by ``DocumentRepository.update_analysis`` (result fields and
#: ``entity_columns``). They are read for schema validation but never replayed.
ANALYSIS_WRITEBACK = frozenset(
    {
        "tags",
        "tickers",
        "categories",
        "market_scope",
        "entities",
        "entity_mentions",
        "topics",
        "people",
        "organizations",
        "crypto_assets",
    }
)
ANALYSIS_METADATA_KEYS = ("explanation_short", "explanation_long")


class RecordingProvider(BaseAnalysisProvider):
    """Deterministic no-I/O provider proving whether the pipeline would call an LLM."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return "offline-replay-stub"

    @property
    def model(self) -> str:
        return "offline-sentinel"

    async def analyze(
        self, title: str, text: str, context: dict[str, Any] | None = None
    ) -> LLMAnalysisOutput:
        self.calls.append({"title": title, "text_chars": len(text), "context": context})
        return LLMAnalysisOutput(
            sentiment_label=SentimentLabel.NEUTRAL,
            sentiment_score=0.0,
            relevance_score=0.5,
            impact_score=0.0,
            confidence_score=1.0,
            novelty_score=0.0,
            spam_probability=0.0,
            market_scope=MarketScope.UNKNOWN,
            short_reasoning="offline replay sentinel",
            provider_used=self.provider_name,
        )


def _json_column(value: Any, *, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        decoded = json.loads(value)
        return default if decoded is None else decoded
    return value


def _document_from_row(row: dict[str, Any], line_number: int) -> CanonicalDocument:
    if set(row) != DB_COLUMNS:
        missing = sorted(DB_COLUMNS - set(row))
        extra = sorted(set(row) - DB_COLUMNS)
        raise ValueError(
            f"line {line_number}: canonical_documents fields differ; missing={missing}, extra={extra}"
        )
    # Every field the earlier analysis wrote back is an OUTPUT and must not enter
    # the gate again: ``update_analysis`` stores tags/tickers/categories from the
    # result plus the enriched entity columns (``entity_columns``) and the market
    # scope. Fed back in, ``tickers`` alone makes the crypto gate pass with
    # ``has_tickers`` -- the replay would measure its own previous answer.
    # Ingestion-time values of these fields are overwritten and cannot be
    # recovered; the replay therefore starts them empty, as for RSS/YouTube.
    metadata = dict(_json_column(row["metadata"], default={}))
    for key in ANALYSIS_METADATA_KEYS:
        metadata.pop(key, None)
    return CanonicalDocument.model_validate(
        {
            "id": row["id"],
            "external_id": row["external_id"],
            "source_id": row["source_id"],
            "source_name": row["source_name"],
            "source_type": row["source_type"],
            "document_type": row["document_type"],
            "url": row["url"],
            "title": row["title"],
            "author": row["author"],
            "published_at": row["published_at"],
            "fetched_at": row["fetched_at"],
            "language": row["language"],
            "raw_text": row["raw_text"],
            "cleaned_text": row["cleaned_text"],
            "summary": row["summary"],
            "youtube_meta": _json_column(row["youtube_meta"], default=None),
            "podcast_meta": _json_column(row["podcast_meta"], default=None),
            "metadata": metadata,
        }
    )


def load_rows(path: Path) -> tuple[list[tuple[str, CanonicalDocument]], str]:
    data = path.read_bytes()
    documents: list[tuple[str, CanonicalDocument]] = []
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"line {line_number}: expected object")
        case_id = row["doc_id"]
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"line {line_number}: invalid doc_id")
        documents.append((case_id.strip(), _document_from_row(row, line_number)))
    if not documents:
        raise ValueError("replay input is empty")
    return documents, hashlib.sha256(data).hexdigest()


def _code_hashes() -> dict[str, str]:
    files: set[Path] = {Path(__file__).resolve()}
    app_root = (ROOT / "app").resolve()
    for module in tuple(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if not name:
            continue
        path = Path(name).resolve()
        if path.suffix == ".py" and (path == app_root or app_root in path.parents):
            files.add(path)
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }


async def replay(path: Path, *, crypto_gate_mode: str, monitor: Path) -> dict[str, Any]:
    if crypto_gate_mode not in {"off", "shadow", "enforce"}:
        raise ValueError("crypto gate mode must be off, shadow, or enforce")
    documents, input_hash = load_rows(path)
    provider = RecordingProvider()
    pipeline = AnalysisPipeline(
        KeywordEngine.from_monitor_dir(monitor),
        provider=provider,
        run_llm=True,
        crypto_gate_mode=crypto_gate_mode,
    )
    # Replay evidence must not append operational telemetry. This replaces only the
    # side-effect sink; AnalysisPipeline and its gate ordering remain untouched.
    from app.observability import llm_telemetry

    original_record = llm_telemetry.record_llm_call
    llm_telemetry.record_llm_call = lambda **_kwargs: None
    try:
        cases = []
        # Operational log rendering is irrelevant evidence and can fail on a
        # narrow Windows console encoding for multilingual documents.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for case_id, document in documents:
                calls_before = len(provider.calls)
                result = await pipeline.run(document, correlation_id=f"jev-replay-{document.id}")
                calls_after = len(provider.calls)
                cases.append(
                    {
                        "case_id": case_id,
                        "skip_reason": result.skip_reason,
                        "would_call_llm": calls_after == calls_before + 1,
                        "pipeline_llm_called": result.llm_called,
                        "error": result.error,
                    }
                )
    finally:
        llm_telemetry.record_llm_call = original_record
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "OFFLINE_REPLAY_COMPLETE",
        "primary_ready": False,
        "jev_called": False,
        "input_sha256": input_hash,
        "git_sha": git_sha,
        "crypto_gate_mode": crypto_gate_mode,
        "analysis_writeback_reset": sorted(ANALYSIS_WRITEBACK),
        "case_count": len(cases),
        "provider_call_count": len(provider.calls),
        "code_sha256": _code_hashes(),
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--monitor", type=Path, default=ROOT / "monitor")
    parser.add_argument("--crypto-gate-mode", choices=("off", "shadow", "enforce"), required=True)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(
            replay(args.input, crypto_gate_mode=args.crypto_gate_mode, monitor=args.monitor)
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
        print(json.dumps({"status": report["status"], "case_count": report["case_count"]}))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "INVALID_REPLAY", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
