"""Basic read-tool tests: watchlists, research brief, signals, narrative, distribution, route."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp_server import (
    get_distribution_classification_report,
    get_narrative_clusters,
    get_research_brief,
    get_route_profile_report,
    get_signal_candidates,
    get_signals_for_execution,
    get_watchlists,
)
from app.core.enums import AnalysisSource, SentimentLabel, SourceType
from tests.unit.factories import make_document
from tests.unit.mcp._helpers import (
    _patch_workspace_root,
    _write_abc_output,
)


@pytest.mark.asyncio
@patch("app.agents.tools.canonical_read.get_settings")
@patch("app.agents.tools.canonical_read.WatchlistRegistry")
async def test_get_watchlists(mock_registry_cls: MagicMock, mock_settings: MagicMock) -> None:
    mock_settings.return_value = SimpleNamespace(monitor_dir="monitor")
    mock_registry = mock_registry_cls.from_monitor_dir.return_value
    mock_registry.get_all_watchlists.return_value = {
        "defi": ["UNI", "AAVE"],
        "infra": ["LINK"],
    }

    result = await get_watchlists(watchlist_type="assets")

    assert result["defi"] == ["UNI", "AAVE"]
    assert result["infra"] == ["LINK"]


@pytest.mark.asyncio
@patch("app.agents.tools.canonical_read.build_session_factory")
@patch("app.agents.tools.canonical_read.get_settings")
@patch("app.agents.tools.canonical_read.WatchlistRegistry")
@patch("app.agents.tools.canonical_read.ResearchBriefBuilder")
async def test_get_research_brief(
    mock_builder_cls: MagicMock,
    mock_registry_cls: MagicMock,
    mock_settings: MagicMock,
    mock_session_factory: MagicMock,
) -> None:
    mock_settings.return_value = SimpleNamespace(monitor_dir="monitor", db=MagicMock())
    mock_registry = mock_registry_cls.from_monitor_dir.return_value
    mock_registry.get_watchlist.return_value = ["UNI", "AAVE"]
    mock_registry.filter_documents.return_value = []

    mock_builder = mock_builder_cls.return_value
    mock_brief = MagicMock()
    mock_brief.to_markdown.return_value = "# Research Brief: defi\n"
    mock_builder.build.return_value = mock_brief

    mock_session = AsyncMock()
    mock_session_factory.return_value.begin.return_value.__aenter__.return_value = (
        mock_session
    )

    with patch("app.agents.tools.canonical_read.DocumentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.list = AsyncMock(return_value=[])

        result = await get_research_brief(
            watchlist="defi",
            watchlist_type="assets",
            limit=10,
        )

    assert result == "# Research Brief: defi\n"


@pytest.mark.asyncio
@patch("app.agents.tools._helpers.extract_signal_candidates")
@patch("app.agents.tools._helpers.build_session_factory")
@patch("app.agents.tools._helpers.get_settings")
@patch("app.agents.tools._helpers.WatchlistRegistry")
async def test_get_signal_candidates(
    mock_registry_cls: MagicMock,
    mock_settings: MagicMock,
    mock_session_factory: MagicMock,
    mock_extract: MagicMock,
) -> None:
    mock_settings.return_value = SimpleNamespace(monitor_dir="monitor", db=MagicMock())
    mock_registry = mock_registry_cls.from_monitor_dir.return_value
    mock_registry.get_watchlist.return_value = ["UNI", "AAVE"]

    mock_candidate = MagicMock()
    mock_candidate.to_json_dict.return_value = {"symbol": "UNI", "signal": "BUY"}
    mock_extract.return_value = [mock_candidate]

    mock_session = AsyncMock()
    mock_session_factory.return_value.begin.return_value.__aenter__.return_value = (
        mock_session
    )

    with patch("app.agents.tools._helpers.DocumentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.list = AsyncMock(return_value=[])

        result = await get_signal_candidates(
            watchlist="defi",
            min_priority=8,
            limit=10,
        )

    assert "UNI" in result
    assert "BUY" in result


@pytest.mark.asyncio
@patch("app.agents.tools.canonical_read.build_session_factory")
@patch("app.agents.tools.canonical_read.get_settings")
async def test_get_narrative_clusters_returns_read_only_cluster_report(
    mock_settings: MagicMock,
    mock_session_factory: MagicMock,
) -> None:
    mock_settings.return_value = SimpleNamespace(db=MagicMock())
    mock_session = AsyncMock()
    mock_session_factory.return_value.begin.return_value.__aenter__.return_value = (
        mock_session
    )
    docs = [
        make_document(
            is_analyzed=True,
            priority_score=9,
            sentiment_label=SentimentLabel.BULLISH,
            tickers=["BTC"],
            crypto_assets=[],
            relevance_score=0.92,
            credibility_score=0.87,
            provider="openai",
            analysis_source=AnalysisSource.EXTERNAL_LLM,
            source_name="CoinDesk",
            source_type=SourceType.RSS_FEED,
            summary="ETF demand remains elevated.",
        ),
        make_document(
            is_analyzed=True,
            priority_score=8,
            sentiment_label=SentimentLabel.BULLISH,
            tickers=["BTC"],
            crypto_assets=[],
            relevance_score=0.88,
            credibility_score=0.84,
            provider="openai",
            analysis_source=AnalysisSource.EXTERNAL_LLM,
            source_name="The Block",
            source_type=SourceType.RSS_FEED,
            summary="BTC inflows continue across spot products.",
        ),
    ]

    with patch("app.agents.tools.canonical_read.DocumentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.list = AsyncMock(return_value=docs)

        result = await get_narrative_clusters(
            min_priority=8,
            limit=10,
            min_cluster_size=1,
        )

    assert result["report_type"] == "narrative_cluster_report"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert result["candidate_count"] == 2
    assert result["cluster_count"] == 1
    assert result["clusters"][0]["doc_count"] == 2
    assert "BTC" in result["clusters"][0]["assets"]


@pytest.mark.asyncio
@patch("app.agents.tools._helpers.build_session_factory")
@patch("app.agents.tools._helpers.get_settings")
@patch("app.agents.tools._helpers.WatchlistRegistry")
async def test_get_signals_for_execution_returns_read_only_handoff(
    mock_registry_cls: MagicMock,
    mock_settings: MagicMock,
    mock_session_factory: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    mock_settings.return_value = SimpleNamespace(monitor_dir="monitor", db=MagicMock())
    mock_registry = mock_registry_cls.from_monitor_dir.return_value
    mock_registry.get_watchlist.return_value = []

    document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        tickers=["BTC"],
        crypto_assets=[],
        relevance_score=0.92,
        credibility_score=0.87,
        provider="openai",
        analysis_source=AnalysisSource.EXTERNAL_LLM,
        source_name="CoinDesk",
        source_type=SourceType.RSS_FEED,
        summary="ETF demand remains elevated.",
    )

    mock_session = AsyncMock()
    mock_session_factory.return_value.begin.return_value.__aenter__.return_value = (
        mock_session
    )

    with patch("app.agents.tools._helpers.DocumentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.list = AsyncMock(return_value=[document])

        payload = await get_signals_for_execution(limit=10)

    assert payload["report_type"] == "execution_signal_handoff"
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert payload["signal_count"] == 1
    signal = payload["signals"][0]
    assert signal["signal_id"] == f"sig_{document.id}"
    assert signal["report_type"] == "signal_handoff"
    assert signal["provider"] == "openai"
    assert signal["analysis_source"] == "external_llm"
    assert signal["route_path"] == "A.external_llm"
    assert signal["path_type"] == "primary"
    assert signal["delivery_class"] == "productive_handoff"
    assert signal["consumer_visibility"] == "visible"
    assert signal["source_name"] == "CoinDesk"
    assert signal["source_url"] == document.url
    assert not (tmp_path / "artifacts" / "mcp_write_audit.jsonl").exists()


@pytest.mark.asyncio
@patch("app.agents.tools._helpers.build_session_factory")
@patch("app.agents.tools._helpers.get_settings")
@patch("app.agents.tools._helpers.WatchlistRegistry")
async def test_get_signals_for_execution_provider_filter_excludes_other_sources(
    mock_registry_cls: MagicMock,
    mock_settings: MagicMock,
    mock_session_factory: MagicMock,
) -> None:
    mock_settings.return_value = SimpleNamespace(monitor_dir="monitor", db=MagicMock())
    mock_registry = mock_registry_cls.from_monitor_dir.return_value
    mock_registry.get_watchlist.return_value = []

    openai_document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        tickers=["BTC"],
        crypto_assets=[],
        provider="openai",
        analysis_source=AnalysisSource.EXTERNAL_LLM,
    )
    rule_document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BEARISH,
        tickers=["ETH"],
        crypto_assets=[],
        provider="rule",
        analysis_source=AnalysisSource.RULE,
    )

    mock_session = AsyncMock()
    mock_session_factory.return_value.begin.return_value.__aenter__.return_value = (
        mock_session
    )

    with patch("app.agents.tools._helpers.DocumentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.list = AsyncMock(return_value=[openai_document, rule_document])

        payload = await get_signals_for_execution(limit=10, provider="openai")

    assert payload["signal_count"] == 1
    assert payload["signals"][0]["provider"] == "openai"


@pytest.mark.asyncio
@patch("app.agents.tools._helpers.build_session_factory")
@patch("app.agents.tools._helpers.get_settings")
@patch("app.agents.tools._helpers.WatchlistRegistry")
async def test_get_distribution_classification_report_returns_read_only_split(
    mock_registry_cls: MagicMock,
    mock_settings: MagicMock,
    mock_session_factory: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    mock_settings.return_value = SimpleNamespace(monitor_dir="monitor", db=MagicMock())
    mock_registry = mock_registry_cls.from_monitor_dir.return_value
    mock_registry.get_watchlist.return_value = []

    document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        tickers=["BTC"],
        crypto_assets=[],
        relevance_score=0.92,
        credibility_score=0.87,
        provider="openai",
        analysis_source=AnalysisSource.EXTERNAL_LLM,
        source_name="CoinDesk",
        source_type=SourceType.RSS_FEED,
        summary="ETF demand remains elevated.",
    )
    abc_output = _write_abc_output(
        tmp_path / "artifacts" / "routes" / "abc_output.jsonl",
        document_id=str(document.id),
    )

    mock_session = AsyncMock()
    mock_session_factory.return_value.begin.return_value.__aenter__.return_value = (
        mock_session
    )

    with patch("app.agents.tools._helpers.DocumentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.list = AsyncMock(return_value=[document])

        payload = await get_distribution_classification_report(
            abc_output_path=str(abc_output),
            limit=10,
        )

    assert payload["report_type"] == "distribution_classification_report"
    assert payload["abc_output_path"] == str(abc_output.resolve())
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert payload["primary_signal_count"] == 1
    assert payload["audit_output_count"] == 2
    assert payload["primary_handoff"]["signals"][0]["delivery_class"] == "productive_handoff"
    assert payload["audit_outputs"][0]["path_type"] == "shadow"
    assert payload["audit_outputs"][0]["consumer_visibility"] == "hidden"
    assert payload["audit_outputs"][1]["path_type"] == "control"


@pytest.mark.asyncio
@patch("app.agents.tools.canonical_read.build_route_profile", new_callable=AsyncMock)
@patch("app.agents.tools.canonical_read.build_session_factory")
@patch("app.agents.tools.canonical_read.get_settings")
async def test_get_route_profile_report(
    mock_settings: MagicMock,
    mock_session_factory: MagicMock,
    mock_build_route_profile: AsyncMock,
) -> None:
    mock_settings.return_value = SimpleNamespace(db=MagicMock())
    mock_session = AsyncMock()
    mock_session_factory.return_value.begin.return_value.__aenter__.return_value = (
        mock_session
    )
    mock_report = MagicMock()
    mock_report.to_json_dict.return_value = {
        "report_type": "route_profile",
        "total_analyzed": 12,
    }
    mock_build_route_profile.return_value = mock_report

    with patch("app.agents.tools.canonical_read.DocumentRepository") as mock_repo_cls:
        payload = await get_route_profile_report(limit=25)

    assert payload["report_type"] == "route_profile"
    assert payload["total_analyzed"] == 12
    mock_build_route_profile.assert_awaited_once_with(mock_repo_cls.return_value, limit=25)
