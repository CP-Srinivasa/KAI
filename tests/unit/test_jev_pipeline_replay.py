from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from scripts.jev_shadow_eval.corpus import ROOT, load_corpus
from scripts.jev_shadow_eval.pipeline_replay import DB_COLUMNS, load_rows, replay


def _db_row(*, title: str, text: str) -> dict[str, object]:
    row: dict[str, object] = dict.fromkeys(DB_COLUMNS)
    row.update(
        {
            "id": str(uuid4()),
            "doc_id": f"development-{uuid4()}",
            "source_name": "development-fixture",
            "source_type": "rss_feed",
            "document_type": "article",
            "url": f"https://example.invalid/{uuid4()}",
            "title": title,
            "language": "en",
            "market_scope": "unknown",
            "fetched_at": datetime.now(UTC).isoformat(),
            "raw_text": text,
            "status": "pending",
            "is_duplicate": False,
            "is_analyzed": False,
            "entity_mentions": [],
            "entities": [],
            "tickers": [],
            "crypto_assets": [],
            "people": [],
            "organizations": [],
            "tags": [],
            "topics": [],
            "categories": [],
            "metadata": {},
        }
    )
    return row


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.mark.asyncio
async def test_replay_runs_development_corpus_through_real_pipeline(tmp_path: Path) -> None:
    corpus, _ = load_corpus(ROOT / "tests/fixtures/jev/development_corpus.json")
    source = tmp_path / "rows.jsonl"
    _write(source, [_db_row(title=row["title"], text=row["text"]) for row in corpus])

    report = await replay(source, crypto_gate_mode="enforce", monitor=ROOT / "monitor")

    assert report["case_count"] == len(corpus)
    assert report["jev_called"] is False
    assert report["primary_ready"] is False
    assert "app/analysis/pipeline.py" in report["code_sha256"]
    assert all(
        case["skip_reason"] or case["would_call_llm"] or case["error"] for case in report["cases"]
    )


@pytest.mark.asyncio
async def test_footer_noise_is_not_sent_to_stub_after_gate_skip(tmp_path: Path) -> None:
    source = tmp_path / "footer.jsonl"
    text = (
        "Rates were left unchanged after the central bank meeting. " * 8
        + "Project collaboration appeared first on Crypto Briefing."
    )
    _write(source, [_db_row(title="Central bank decision", text=text)])

    report = await replay(source, crypto_gate_mode="enforce", monitor=ROOT / "monitor")

    case = report["cases"][0]
    assert case["would_call_llm"] is False
    assert report["provider_call_count"] == 0
    assert case["skip_reason"] is not None


def test_replay_requires_every_canonical_document_column(tmp_path: Path) -> None:
    row = _db_row(title="Bitcoin update", text="Bitcoin network update " * 10)
    del row["provider"]
    source = tmp_path / "missing.jsonl"
    _write(source, [row])
    with pytest.raises(ValueError, match="fields differ"):
        load_rows(source)


@pytest.mark.asyncio
async def test_persisted_analysis_output_does_not_leak_into_the_gate(tmp_path: Path) -> None:
    """Earlier analysis wrote tickers/crypto_assets back; the gate must not see them."""
    row = _db_row(
        title="Central bank keeps rates unchanged",
        text="The committee voted to hold the policy rate after the meeting. " * 8,
    )
    row.update(
        {
            "tickers": ["BTC"],
            "crypto_assets": ["BTC"],
            "categories": ["crypto"],
            "topics": ["Bitcoin"],
            "tags": ["bitcoin"],
            "market_scope": "crypto",
            "metadata": {"explanation_short": "from an earlier LLM run"},
        }
    )
    source = tmp_path / "leak.jsonl"
    _write(source, [row])

    report = await replay(source, crypto_gate_mode="enforce", monitor=ROOT / "monitor")

    case = report["cases"][0]
    assert case["would_call_llm"] is False
    assert case["skip_reason"] is not None
    assert "tickers" in report["analysis_writeback_reset"]
