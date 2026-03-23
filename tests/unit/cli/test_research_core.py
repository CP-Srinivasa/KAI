"""Tests for research core commands: watchlists, brief, dataset-export, signals."""
from __future__ import annotations

import json

import yaml  # type: ignore[import-untyped]
from typer.testing import CliRunner

from app.cli import main as cli_main
from app.cli.main import app
from app.core.domain.document import CanonicalDocument
from app.core.enums import AnalysisSource, SentimentLabel
from app.core.settings import AppSettings

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers (dataset rows)
# ---------------------------------------------------------------------------


def _make_dataset_row(
    *,
    document_id: str,
    analysis_source: str = "external_llm",
    provider: str = "openai",
    sentiment_label: str = "bullish",
    priority_score: int = 8,
    relevance_score: float = 0.9,
    impact_score: float = 0.6,
    tags: list[str] | None = None,
) -> dict[str, object]:
    target = {
        "affected_assets": ["BTC"],
        "impact_score": impact_score,
        "market_scope": "crypto",
        "novelty_score": 0.4,
        "priority_score": priority_score,
        "relevance_score": relevance_score,
        "sentiment_label": sentiment_label,
        "sentiment_score": 0.7,
        "spam_probability": 0.05,
        "summary": "Synthetic dataset row.",
        "tags": tags or ["btc"],
    }
    return {
        "messages": [
            {"role": "system", "content": "You are a highly precise financial AI analyst."},
            {"role": "user", "content": "Analyze..."},
            {"role": "assistant", "content": json.dumps(target, sort_keys=True)},
        ],
        "metadata": {
            "document_id": document_id,
            "provider": provider,
            "analysis_source": analysis_source,
        },
    }


def _write_jsonl_rows(path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------------------
# Sprint 15: research watchlists / brief
# ---------------------------------------------------------------------------


def test_research_watchlists_lists_requested_type(monkeypatch, tmp_path) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        yaml.safe_dump(
            {
                "persons": [
                    {
                        "name": "Gary Gensler",
                        "aliases": ["gensler"],
                        "tags": ["regulation"],
                    },
                    {
                        "name": "Elizabeth Warren",
                        "aliases": ["warren"],
                        "tags": ["regulation"],
                    },
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)

    result = runner.invoke(app, ["research", "watchlists", "--type", "persons", "regulation"])

    assert result.exit_code == 0
    assert "regulation" in result.output
    assert "Gary Gensler" in result.output
    assert "Elizabeth Warren" in result.output


def test_research_brief_filters_documents_by_watchlist_type(monkeypatch, tmp_path) -> None:
    from app.storage.repositories import document_repo

    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        yaml.safe_dump(
            {
                "persons": [
                    {
                        "name": "Gary Gensler",
                        "aliases": ["gensler"],
                        "tags": ["regulation"],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)

    class FakeSessionFactory:
        def begin(self):
            class FakeSessionContext:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return FakeSessionContext()

    async def fake_list(self, **kwargs):
        return [
            CanonicalDocument(
                url="https://example.com/gensler",
                title="Gensler warns on crypto regulation",
                is_analyzed=True,
                priority_score=8,
                summary="Regulatory pressure remains elevated.",
                people=["Gary Gensler"],
                entities=["Gary Gensler"],
                sentiment_label=SentimentLabel.BEARISH,
            ),
            CanonicalDocument(
                url="https://example.com/vitalik",
                title="Vitalik discusses scaling",
                is_analyzed=True,
                priority_score=7,
                summary="Ethereum roadmap update.",
                people=["Vitalik Buterin"],
                entities=["Vitalik Buterin"],
                sentiment_label=SentimentLabel.BULLISH,
            ),
        ]

    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_main, "build_session_factory", lambda _db: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)

    result = runner.invoke(
        app,
        ["research", "brief", "--watchlist", "regulation", "--type", "persons", "--format", "json"],
    )

    assert result.exit_code == 0
    assert '"title": "Research Brief: regulation"' in result.output
    assert "Gensler warns on crypto regulation" in result.output
    assert "Vitalik discusses scaling" not in result.output


# ---------------------------------------------------------------------------
# Sprint 17: research dataset-export
# ---------------------------------------------------------------------------


def test_research_dataset_export_teacher_only_uses_strict_export_flag(
    monkeypatch,
    tmp_path,
) -> None:
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo
    from tests.unit.factories import make_document

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)

    class FakeSessionFactory:
        def begin(self):
            class FakeSessionContext:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return FakeSessionContext()

    async def fake_list(self, **kwargs):
        return [
            make_document(
                raw_text="Teacher content.",
                is_analyzed=True,
                provider="openai",
                analysis_source=AnalysisSource.EXTERNAL_LLM,
            ),
            make_document(
                raw_text="Internal content.",
                is_analyzed=True,
                provider="companion",
                analysis_source=AnalysisSource.INTERNAL,
            ),
            make_document(
                raw_text="Rule content.",
                is_analyzed=True,
                provider=None,
                analysis_source=AnalysisSource.RULE,
            ),
        ]

    monkeypatch.setattr(cli_main, "get_settings", lambda: settings)
    monkeypatch.setattr(db_session, "build_session_factory", lambda _db: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)

    out_file = tmp_path / "teacher_only.jsonl"
    result = runner.invoke(
        app,
        [
            "research",
            "dataset-export",
            str(out_file),
            "--source-type",
            "all",
            "--teacher-only",
        ],
    )

    assert result.exit_code == 0
    assert "Successfully exported 1 documents" in result.output

    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["metadata"]["analysis_source"] == "external_llm"
    assert row["metadata"]["provider"] == "openai"


# ---------------------------------------------------------------------------
# Sprint 31: research signals
# ---------------------------------------------------------------------------


def test_research_signals_in_help() -> None:
    """signals must appear in research --help."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "signals" in result.output


def test_research_signals_no_candidates(monkeypatch) -> None:
    """signals exits 0 and reports no candidates when DB returns empty list."""
    from app.research import signals as signals_module
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo

    class FakeSessionFactory:
        def begin(self):
            class Ctx:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, *a):
                    return False

            return Ctx()

    async def fake_list(self, **kwargs):
        return []

    monkeypatch.setattr(db_session, "build_session_factory", lambda _: FakeSessionFactory())
    monkeypatch.setattr(document_repo.DocumentRepository, "list", fake_list)
    monkeypatch.setattr(signals_module, "extract_signal_candidates", lambda docs, **kwargs: [])

    result = runner.invoke(app, ["research", "signals"])
    assert result.exit_code == 0
    assert "No signal candidates" in result.output
