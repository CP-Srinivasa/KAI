from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

import app.agents.mcp_server as mcp_server_module
from app.agents.mcp_server import (
    acknowledge_signal_handoff,
    activate_route_profile,
    append_review_journal_entry,
    create_inference_profile,
    deactivate_route_profile,
    get_action_queue_summary,
    get_active_route_status,
    get_artifact_inventory,
    get_artifact_retention_report,
    get_blocking_actions,
    get_blocking_summary,
    get_cleanup_eligibility_summary,
    get_daily_operator_summary,
    get_decision_pack_summary,
    get_distribution_classification_report,
    get_distribution_drift,
    get_escalation_summary,
    get_handoff_collector_summary,
    get_handoff_summary,
    get_inference_route_profile,
    get_mcp_capabilities,
    get_mcp_tool_inventory,
    get_narrative_clusters,
    get_operational_escalation_summary,
    get_operational_readiness_summary,
    get_operator_action_summary,
    get_operator_decision_pack,
    get_operator_runbook,
    get_prioritized_actions,
    get_protected_artifact_summary,
    get_protective_gate_summary,
    get_provider_health,
    get_remediation_recommendations,
    get_research_brief,
    get_resolution_summary,
    get_review_journal_summary,
    get_review_required_actions,
    get_review_required_summary,
    get_route_profile_report,
    get_signal_candidates,
    get_signals_for_execution,
    get_upgrade_cycle_status,
    get_watchlists,
    mcp,
)
from app.core.enums import AnalysisSource, SentimentLabel, SourceType
from app.research.abc_result import (
    ABCInferenceEnvelope,
    PathResultEnvelope,
    save_abc_inference_envelope_jsonl,
)
from app.research.execution_handoff import (
    HANDOFF_ACK_JSONL_FILENAME,
    append_handoff_acknowledgement_jsonl,
    create_handoff_acknowledgement,
    create_signal_handoff,
    load_signal_handoffs,
    save_signal_handoff_batch_jsonl,
)
from app.research.inference_profile import (
    InferenceRouteProfile,
    save_inference_route_profile,
)
from app.research.signals import extract_signal_candidates
from tests.unit.factories import make_document


def _patch_workspace_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(mcp_server_module, "_WORKSPACE_ROOT", root.resolve())


def _write_route_profile(
    root: Path,
    *,
    route_profile: str = "primary_with_shadow",
    shadow_paths: list[str] | None = None,
    control_path: str | None = None,
) -> Path:
    profile_path = root / "profiles" / "route_profile.json"
    save_inference_route_profile(
        InferenceRouteProfile(
            profile_name="mcp-route",
            route_profile=route_profile,
            active_primary_path="A.external_llm",
            enabled_shadow_paths=list(shadow_paths or ["B.companion"]),
            control_path=control_path,
        ),
        profile_path,
    )
    return profile_path


def _write_teacher_dataset(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")


def _write_signal_handoff_batch(path: Path) -> tuple[Path, dict[str, object]]:
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
    signal = extract_signal_candidates([document], min_priority=8)[0]
    handoff = create_signal_handoff(signal, document=document)
    save_signal_handoff_batch_jsonl(
        [handoff],
        path,
    )
    return path, handoff.to_json_dict()


def _write_abc_output(path: Path, *, document_id: str) -> Path:
    save_abc_inference_envelope_jsonl(
        [
            ABCInferenceEnvelope(
                document_id=document_id,
                route_profile="primary_with_shadow_and_control",
                primary_result=PathResultEnvelope(
                    path_id="A.external_llm",
                    provider="openai",
                    analysis_source="external_llm",
                    summary="Primary result",
                    scores={"recommended_priority": 9, "actionable": True},
                ),
                shadow_results=[
                    PathResultEnvelope(
                        path_id="B.companion",
                        provider="companion",
                        analysis_source="internal",
                        summary="Shadow audit result",
                        scores={"recommended_priority": 4},
                    )
                ],
                control_result=PathResultEnvelope(
                    path_id="C.rule",
                    provider="rule",
                    analysis_source="rule",
                    summary="Control comparison result",
                    scores={"recommended_priority": 5},
                ),
            )
        ],
        path,
    )
    return path


def test_mcp_server_initialization() -> None:
    assert isinstance(mcp, FastMCP)
    assert mcp.name == "KAI Analyst Trading Bot"


@pytest.mark.asyncio
async def test_mcp_server_tools_registered() -> None:
    tools = await mcp.list_tools()
    tool_names = {tool.name for tool in tools}

    assert {
        "get_watchlists",
        "get_research_brief",
        "get_signal_candidates",
        "get_signals_for_execution",
        "get_distribution_classification_report",
        "get_route_profile_report",
        "get_inference_route_profile",
        "get_active_route_status",
        "get_upgrade_cycle_status",
        "get_handoff_collector_summary",
        "get_operational_readiness_summary",
        "get_protective_gate_summary",
        "get_remediation_recommendations",
        "append_review_journal_entry",
        "get_review_journal_summary",
        "get_resolution_summary",
        "acknowledge_signal_handoff",
        "create_inference_profile",
        "activate_route_profile",
        "deactivate_route_profile",
        "get_mcp_capabilities",
    }.issubset(tool_names)


@pytest.mark.asyncio
@patch("app.agents.mcp_server.get_settings")
@patch("app.agents.mcp_server.WatchlistRegistry")
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
@patch("app.agents.mcp_server.build_session_factory")
@patch("app.agents.mcp_server.get_settings")
@patch("app.agents.mcp_server.WatchlistRegistry")
@patch("app.agents.mcp_server.ResearchBriefBuilder")
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

    with patch("app.agents.mcp_server.DocumentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.list = AsyncMock(return_value=[])

        result = await get_research_brief(
            watchlist="defi",
            watchlist_type="assets",
            limit=10,
        )

    assert result == "# Research Brief: defi\n"


@pytest.mark.asyncio
@patch("app.agents.mcp_server.extract_signal_candidates")
@patch("app.agents.mcp_server.build_session_factory")
@patch("app.agents.mcp_server.get_settings")
@patch("app.agents.mcp_server.WatchlistRegistry")
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

    with patch("app.agents.mcp_server.DocumentRepository") as mock_repo_cls:
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
@patch("app.agents.mcp_server.build_session_factory")
@patch("app.agents.mcp_server.get_settings")
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

    with patch("app.agents.mcp_server.DocumentRepository") as mock_repo_cls:
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
@patch("app.agents.mcp_server.build_session_factory")
@patch("app.agents.mcp_server.get_settings")
@patch("app.agents.mcp_server.WatchlistRegistry")
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

    with patch("app.agents.mcp_server.DocumentRepository") as mock_repo_cls:
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
@patch("app.agents.mcp_server.build_session_factory")
@patch("app.agents.mcp_server.get_settings")
@patch("app.agents.mcp_server.WatchlistRegistry")
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

    with patch("app.agents.mcp_server.DocumentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.list = AsyncMock(return_value=[openai_document, rule_document])

        payload = await get_signals_for_execution(limit=10, provider="openai")

    assert payload["signal_count"] == 1
    assert payload["signals"][0]["provider"] == "openai"


@pytest.mark.asyncio
@patch("app.agents.mcp_server.build_session_factory")
@patch("app.agents.mcp_server.get_settings")
@patch("app.agents.mcp_server.WatchlistRegistry")
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

    with patch("app.agents.mcp_server.DocumentRepository") as mock_repo_cls:
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
@patch("app.agents.mcp_server.build_route_profile", new_callable=AsyncMock)
@patch("app.agents.mcp_server.build_session_factory")
@patch("app.agents.mcp_server.get_settings")
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

    with patch("app.agents.mcp_server.DocumentRepository") as mock_repo_cls:
        payload = await get_route_profile_report(limit=25)

    assert payload["report_type"] == "route_profile"
    assert payload["total_analyzed"] == 12
    mock_build_route_profile.assert_awaited_once_with(mock_repo_cls.return_value, limit=25)


@pytest.mark.asyncio
async def test_get_inference_route_profile_reads_workspace_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)

    payload = await get_inference_route_profile(str(profile_path))

    assert payload["report_type"] == "inference_route_profile"
    assert payload["profile_name"] == "mcp-route"
    assert payload["path"] == str(profile_path.resolve())


@pytest.mark.asyncio
async def test_get_inference_route_profile_blocks_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside_path = tmp_path.parent / "outside_profile.json"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_inference_route_profile(str(outside_path))


@pytest.mark.asyncio
async def test_get_active_route_status_returns_inactive_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    payload = await get_active_route_status()

    assert payload["active"] is False
    assert payload["state_path"].endswith("artifacts\\active_route_profile.json") or payload[
        "state_path"
    ].endswith("artifacts/active_route_profile.json")


@pytest.mark.asyncio
async def test_get_upgrade_cycle_status_reads_existing_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    teacher_dataset = tmp_path / "artifacts" / "teacher.jsonl"
    _write_teacher_dataset(teacher_dataset)

    payload = await get_upgrade_cycle_status(str(teacher_dataset))

    assert payload["report_type"] == "upgrade_cycle_report"
    assert payload["status"] == "prepared"
    assert payload["teacher_dataset_path"] == str(teacher_dataset.resolve())


@pytest.mark.asyncio
async def test_create_inference_profile_writes_profile_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    payload = await create_inference_profile(
        profile_name="mcp-created",
        route_profile="primary_with_shadow",
        shadow_paths=["B.companion"],
        output_path="artifacts/routes/created_profile.json",
        notes=["operator-managed"],
    )

    saved_path = Path(payload["output_path"])
    assert saved_path.exists()
    data = json.loads(saved_path.read_text(encoding="utf-8"))
    assert data["profile_name"] == "mcp-created"
    assert data["route_profile"] == "primary_with_shadow"
    assert payload["profile"]["notes"] == ["operator-managed"]


@pytest.mark.asyncio
async def test_activate_route_profile_writes_guarded_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)

    payload = await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route.json",
        abc_envelope_output="artifacts/abc/envelopes.jsonl",
    )

    state_path = tmp_path / "artifacts" / "active_route.json"
    assert state_path.exists()
    assert payload["state_path"] == str(state_path.resolve())
    assert payload["state"]["route_profile"] == "primary_with_shadow"
    assert payload["state"]["abc_envelope_output"] == str(
        (tmp_path / "artifacts" / "abc" / "envelopes.jsonl").resolve()
    )


@pytest.mark.asyncio
async def test_deactivate_route_profile_removes_state_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    state_path = tmp_path / "artifacts" / "active_route.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{}", encoding="utf-8")

    payload = await deactivate_route_profile(str(state_path))

    assert payload["deactivated"] is True
    assert not state_path.exists()


@pytest.mark.asyncio
async def test_deactivate_route_profile_is_idempotent_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    state_path = tmp_path / "artifacts" / "missing_active_route.json"

    payload = await deactivate_route_profile(str(state_path))

    assert payload["deactivated"] is False
    assert payload["state_path"] == str(state_path.resolve())


@pytest.mark.asyncio
async def test_create_inference_profile_rejects_non_json_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="must use one of"):
        await create_inference_profile(
            profile_name="bad-output",
            route_profile="primary_only",
            output_path="artifacts/routes/not_allowed.txt",
        )


@pytest.mark.asyncio
async def test_activate_route_profile_blocks_outside_workspace_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)
    outside_state = tmp_path.parent / "active_route.json"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await activate_route_profile(str(profile_path), state_path=str(outside_state))


@pytest.mark.asyncio
async def test_activate_route_profile_missing_profile_raises_file_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)

    with pytest.raises(FileNotFoundError, match="Inference route profile not found"):
        await activate_route_profile(str(tmp_path / "missing_profile.json"))


@pytest.mark.asyncio
async def test_get_mcp_capabilities_reports_guardrails() -> None:
    payload = json.loads(await get_mcp_capabilities())

    assert payload["transport"] == "stdio_only"
    assert "get_signals_for_execution" in payload["read_tools"]
    assert "get_distribution_classification_report" in payload["read_tools"]
    assert "get_upgrade_cycle_status" in payload["read_tools"]
    assert "get_operational_readiness_summary" in payload["read_tools"]
    assert "get_protective_gate_summary" in payload["read_tools"]
    assert "get_remediation_recommendations" in payload["read_tools"]
    assert "get_escalation_summary" in payload["read_tools"]
    assert "get_blocking_summary" in payload["read_tools"]
    assert "get_operator_action_summary" in payload["read_tools"]
    assert "get_action_queue_summary" in payload["read_tools"]
    assert "get_blocking_actions" in payload["read_tools"]
    assert "get_prioritized_actions" in payload["read_tools"]
    assert "get_review_required_actions" in payload["read_tools"]
    assert "get_review_journal_summary" in payload["read_tools"]
    assert "get_resolution_summary" in payload["read_tools"]
    assert "get_daily_operator_summary" in payload["read_tools"]
    assert "get_artifact_inventory" in payload["read_tools"]
    assert "get_artifact_retention_report" in payload["read_tools"]
    assert "get_cleanup_eligibility_summary" in payload["read_tools"]
    assert "get_protected_artifact_summary" in payload["read_tools"]
    assert "get_review_required_summary" in payload["read_tools"]
    assert "get_policy_rationale_summary" not in payload["read_tools"]
    assert "get_governance_summary" not in payload["read_tools"]
    assert "activate_route_profile" in payload["write_tools"]
    assert payload["write_tools"] == payload["guarded_write_tools"]
    assert "acknowledge_signal_handoff" in payload["write_tools"]
    assert "append_review_journal_entry" in payload["write_tools"]
    assert (
        payload["aliases"]["get_handoff_summary"]["canonical_tool"]
        == "get_handoff_collector_summary"
    )
    assert (
        payload["aliases"]["get_operator_decision_pack"]["canonical_tool"]
        == "get_decision_pack_summary"
    )
    assert (
        payload["superseded_tools"]["get_operational_escalation_summary"][
            "replacement_tool"
        ]
        == "get_escalation_summary"
    )
    assert "No direct execution hook for signals" in payload["guardrails"]
    assert "audit-only" in " ".join(payload["guardrails"])
    assert "no auto-deletion" in " ".join(payload["guardrails"]).lower()
    assert "No auto-routing or auto-promotion" in payload["guardrails"]


@pytest.mark.asyncio
async def test_get_mcp_tool_inventory_classifies_canonical_alias_and_superseded_tools() -> None:
    inventory = get_mcp_tool_inventory()

    assert "get_narrative_clusters" in inventory["canonical_read_tools"]
    assert "get_review_journal_summary" in inventory["canonical_read_tools"]
    assert "get_resolution_summary" in inventory["canonical_read_tools"]
    assert "get_daily_operator_summary" in inventory["canonical_read_tools"]
    assert "get_handoff_summary" not in inventory["canonical_read_tools"]
    assert "get_operator_decision_pack" not in inventory["canonical_read_tools"]
    assert "append_review_journal_entry" in inventory["guarded_write_tools"]
    assert (
        inventory["aliases"]["get_handoff_summary"]["canonical_tool"]
        == "get_handoff_collector_summary"
    )
    assert inventory["aliases"]["get_handoff_summary"]["tool_class"] == "read_only"
    assert (
        inventory["aliases"]["get_operator_decision_pack"]["canonical_tool"]
        == "get_decision_pack_summary"
    )
    assert (
        inventory["superseded_tools"]["get_operational_escalation_summary"][
            "replacement_tool"
        ]
        == "get_escalation_summary"
    )
    assert (
        inventory["superseded_tools"]["get_operational_escalation_summary"][
            "tool_class"
        ]
        == "read_only"
    )
    assert set(inventory["canonical_read_tools"]).isdisjoint(
        inventory["guarded_write_tools"]
    )


@pytest.mark.asyncio
async def test_mcp_tool_inventory_matches_registered_tools() -> None:
    inventory = get_mcp_tool_inventory()
    tools = await mcp.list_tools()
    registered = {tool.name for tool in tools}
    classified = (
        set(inventory["canonical_read_tools"])
        | set(inventory["guarded_write_tools"])
        | set(inventory["workflow_helpers"])
        | set(inventory["aliases"])
        | set(inventory["superseded_tools"])
    )

    assert registered == classified


@pytest.mark.asyncio
async def test_activate_route_profile_returns_app_llm_provider_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-97: activate_route_profile audit record MUST include app_llm_provider_unchanged."""
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)

    payload = await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route.json",
    )

    assert payload.get("app_llm_provider_unchanged") is True


# ---------------------------------------------------------------------------
# Sprint 18 â€” Write Guard (I-95) + Audit (I-94) tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_inference_profile_rejects_non_artifacts_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-95: output path outside artifacts/ must be rejected."""
    _patch_workspace_root(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="must be within workspace/artifacts/"):
        await create_inference_profile(
            profile_name="bad-location",
            route_profile="primary_only",
            output_path="inference_route_profile.json",  # workspace root â€” not artifacts/
        )


@pytest.mark.asyncio
async def test_activate_route_profile_rejects_non_artifacts_state_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-95: state_path outside artifacts/ must be rejected."""
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)

    with pytest.raises(ValueError, match="must be within workspace/artifacts/"):
        await activate_route_profile(
            str(profile_path),
            state_path="active_route.json",  # workspace root â€” not artifacts/
        )


@pytest.mark.asyncio
async def test_deactivate_route_profile_rejects_non_artifacts_state_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-95: state_path outside artifacts/ must be rejected."""
    _patch_workspace_root(monkeypatch, tmp_path)
    state_path = tmp_path / "active_route.json"  # workspace root â€” not artifacts/

    with pytest.raises(ValueError, match="must be within workspace/artifacts/"):
        await deactivate_route_profile(str(state_path))


@pytest.mark.asyncio
async def test_create_inference_profile_appends_write_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-94: create_inference_profile must append a JSONL audit entry."""
    _patch_workspace_root(monkeypatch, tmp_path)

    await create_inference_profile(
        profile_name="audit-test",
        route_profile="primary_with_shadow",
        shadow_paths=["B.companion"],
        output_path="artifacts/routes/audit_profile.json",
    )

    audit_path = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "create_inference_profile"
    assert entry["params"]["profile_name"] == "audit-test"
    assert "timestamp" in entry


@pytest.mark.asyncio
async def test_activate_route_profile_appends_write_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-94: activate_route_profile must append a JSONL audit entry."""
    _patch_workspace_root(monkeypatch, tmp_path)
    profile_path = _write_route_profile(tmp_path)

    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route.json",
    )

    audit_path = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    assert audit_path.exists()
    entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert entry["tool"] == "activate_route_profile"
    assert "state_path" in entry["params"]
    assert entry["params"]["abc_envelope_output"] is None


@pytest.mark.asyncio
async def test_deactivate_route_profile_appends_write_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-94: deactivate_route_profile must append audit even when nothing to remove."""
    _patch_workspace_root(monkeypatch, tmp_path)
    state_path = tmp_path / "artifacts" / "missing_active.json"

    await deactivate_route_profile(str(state_path))

    audit_path = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    assert audit_path.exists()
    entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert entry["tool"] == "deactivate_route_profile"
    assert "deactivated: False" in entry["result_summary"]


@pytest.mark.asyncio
async def test_mcp_write_audit_accumulates_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-94: multiple write calls produce multiple JSONL lines (append mode)."""
    _patch_workspace_root(monkeypatch, tmp_path)

    await create_inference_profile(
        profile_name="first",
        route_profile="primary_only",
        output_path="artifacts/routes/first.json",
    )
    state_path = tmp_path / "artifacts" / "missing.json"
    await deactivate_route_profile(str(state_path))

    audit_path = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    tools = [json.loads(line)["tool"] for line in lines]
    assert tools == ["create_inference_profile", "deactivate_route_profile"]


@pytest.mark.asyncio
async def test_get_mcp_capabilities_reports_write_guard_guardrails() -> None:
    """I-94/I-95: capabilities surface must document write guard invariants."""
    payload = json.loads(await get_mcp_capabilities())

    guardrails = payload["guardrails"]
    assert any("I-95" in g for g in guardrails)
    assert any("I-94" in g for g in guardrails)


# ---------------------------------------------------------------------------
# Sprint 19 â€” blocked signal write-back (I-101â€“I-104)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledge_signal_handoff_writes_audit_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signal acknowledgement writes a canonical handoff audit record (I-116)."""
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )

    result = await acknowledge_signal_handoff(
        handoff_path=str(handoff_path),
        handoff_id=str(payload["handoff_id"]),
        consumer_agent_id="test-agent-001",
    )

    assert result["status"] == "acknowledged_in_audit_only"
    assert result["handoff_id"] == payload["handoff_id"]
    assert result["signal_id"] == payload["signal_id"]
    assert result["consumer_agent_id"] == "test-agent-001"
    assert result["handoff_path"] == str(handoff_path.resolve())

    ack_path = tmp_path / "artifacts" / HANDOFF_ACK_JSONL_FILENAME
    assert ack_path.exists()
    record = json.loads(ack_path.read_text(encoding="utf-8").strip())
    assert record["handoff_id"] == payload["handoff_id"]
    assert record["signal_id"] == payload["signal_id"]
    assert record["status"] == "acknowledged"
    assert record["consumer_visibility"] == "visible"


@pytest.mark.asyncio
async def test_acknowledge_signal_handoff_appends_mcp_write_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit acknowledgements must create an MCP write audit entry (I-94)."""
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )

    await acknowledge_signal_handoff(
        handoff_path=str(handoff_path),
        handoff_id=str(payload["handoff_id"]),
        consumer_agent_id="agent-002",
    )

    mcp_audit = tmp_path / "artifacts" / "mcp_write_audit.jsonl"
    assert mcp_audit.exists()
    record = json.loads(mcp_audit.read_text(encoding="utf-8").strip())
    assert record["tool"] == "acknowledge_signal_handoff"


@pytest.mark.asyncio
async def test_get_handoff_collector_summary_returns_pending_when_no_audit_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )

    result = await get_handoff_collector_summary(handoff_path=str(handoff_path))

    assert result["total_handoffs"] == 1
    assert result["acknowledged_count"] == 0
    assert result["pending_count"] == 1


@pytest.mark.asyncio
async def test_get_handoff_summary_reads_consumer_acknowledgements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_handoff_summary is a compatibility alias for the collector summary."""
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    handoff = load_signal_handoffs(handoff_path)[0]
    ack_path = tmp_path / "artifacts" / HANDOFF_ACK_JSONL_FILENAME
    ack_path.parent.mkdir(parents=True, exist_ok=True)

    append_handoff_acknowledgement_jsonl(
        create_handoff_acknowledgement(
            handoff,
            consumer_agent_id="agent-A",
        ),
        ack_path,
    )

    result = await get_handoff_summary(handoff_path=str(handoff_path))

    assert result["total_handoffs"] == 1
    assert result["acknowledged_count"] == 1
    assert result["pending_count"] == 0
    assert result["consumers"]["agent-A"] == 1
    assert result["acknowledged_handoffs"][0]["signal_id"] == payload["signal_id"]


@pytest.mark.asyncio
async def test_get_operational_readiness_summary_reports_canonical_backlog_and_route_issues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path / "artifacts",
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    missing_abc = tmp_path / "artifacts" / "routes" / "missing_abc.jsonl"

    await activate_route_profile(
        profile_path=str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output=str(missing_abc),
    )

    result = await get_operational_readiness_summary(
        handoff_path=str(handoff_path),
        acknowledgement_path=f"artifacts/{HANDOFF_ACK_JSONL_FILENAME}",
    )

    assert result["report_type"] == "operational_readiness"
    assert result["readiness_status"] == "critical"
    assert result["highest_severity"] == "critical"
    assert result["collector_summary"]["pending_count"] == 1
    assert result["route_summary"]["active"] is True
    assert result["route_summary"]["abc_output_available"] is False
    assert result["provider_health_summary"]["degraded_count"] == 0
    assert result["provider_health_summary"]["unavailable_count"] >= 2
    assert result["distribution_drift_summary"]["status"] == "warning"
    assert result["protective_gate_summary"]["gate_status"] == "blocking"
    assert result["protective_gate_summary"]["blocking_count"] >= 1
    categories = {issue["category"] for issue in result["issues"]}
    assert "handoff_backlog" in categories
    assert "artifact_state" in categories
    assert "provider_health" in categories
    assert "distribution_drift" in categories
    assert payload["signal_id"] == result["collector_summary"]["pending_handoffs"][0]["signal_id"]


@pytest.mark.asyncio
async def test_get_operational_readiness_summary_detects_distribution_drift_in_handoffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    tampered_payload = dict(payload)
    tampered_payload["path_type"] = "shadow"
    tampered_payload["delivery_class"] = "audit_only"
    tampered_payload["consumer_visibility"] = "hidden"
    handoff_path.write_text(json.dumps(tampered_payload) + "\n", encoding="utf-8")

    result = await get_operational_readiness_summary(
        handoff_path=str(handoff_path),
        acknowledgement_path=f"artifacts/{HANDOFF_ACK_JSONL_FILENAME}",
    )

    assert result["distribution_drift_summary"]["status"] == "critical"
    assert result["distribution_drift_summary"]["classification_mismatch_count"] == 1
    categories = {issue["category"] for issue in result["issues"]}
    assert "distribution_drift" in categories


@pytest.mark.asyncio
async def test_acknowledge_signal_handoff_rejects_hidden_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shadow/control handoffs stay audit-only and cannot be externally acknowledged."""
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    hidden_payload = dict(payload)
    hidden_payload["route_path"] = "B.companion"
    hidden_payload["path_type"] = "shadow"
    hidden_payload["delivery_class"] = "audit_only"
    hidden_payload["consumer_visibility"] = "hidden"
    handoff_path.write_text(json.dumps(hidden_payload) + "\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="consumer-visible"):
        await acknowledge_signal_handoff(
            handoff_path=str(handoff_path),
            handoff_id=str(payload["handoff_id"]),
            consumer_agent_id="safety-check",
        )


@pytest.mark.asyncio
async def test_acknowledge_signal_handoff_no_db_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """acknowledge_signal_handoff MUST NOT touch the KAI-Core DB (I-118)."""
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )

    called = []

    def _fail_if_called(*_a: object, **_kw: object) -> None:
        called.append(True)

    monkeypatch.setattr("app.agents.mcp_server.build_session_factory", _fail_if_called)

    await acknowledge_signal_handoff(
        handoff_path=str(handoff_path),
        handoff_id=str(payload["handoff_id"]),
        consumer_agent_id="safety-check",
    )

    assert not called, "acknowledge_signal_handoff must not call build_session_factory"


# ---------------------------------------------------------------------------
# Sprint 22 â€” readiness-derived provider health and distribution drift views
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_provider_health_returns_readiness_derived_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    abc_path = _write_abc_output(
        tmp_path / "artifacts" / "abc" / "envelopes.jsonl",
        document_id=str(payload["document_id"]),
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output=str(abc_path),
    )

    result = await get_provider_health(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "provider_health_summary"
    assert result["derived_from"] == "operational_readiness"
    assert result["healthy_count"] == 3
    assert result["degraded_count"] == 0
    assert result["unavailable_count"] == 0
    assert result["issues"] == []
    by_path = {entry["path_id"]: entry["status"] for entry in result["entries"]}
    assert by_path == {
        "A.external_llm": "healthy",
        "B.companion": "healthy",
        "C.rule": "healthy",
    }


@pytest.mark.asyncio
async def test_get_provider_health_flags_unavailable_expected_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )

    result = await get_provider_health(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["healthy_count"] == 1
    assert result["degraded_count"] == 0
    assert result["unavailable_count"] == 2
    assert any(issue["category"] == "provider_health" for issue in result["issues"])


@pytest.mark.asyncio
async def test_get_provider_health_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = tmp_path.parent / "evil_handoffs.jsonl"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_provider_health(handoff_path=str(outside))


@pytest.mark.asyncio
async def test_get_distribution_drift_returns_readiness_derived_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    abc_path = _write_abc_output(
        tmp_path / "artifacts" / "abc" / "envelopes.jsonl",
        document_id=str(payload["document_id"]),
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output=str(abc_path),
    )

    result = await get_distribution_drift(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "distribution_drift_summary"
    assert result["derived_from"] == "operational_readiness"
    assert result["status"] == "nominal"
    assert result["production_handoff_count"] == 1
    assert result["shadow_audit_result_count"] == 1
    assert result["control_comparison_result_count"] == 1
    assert result["issues"] == []


@pytest.mark.asyncio
async def test_get_distribution_drift_detects_classification_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    rows = [
        json.loads(line)
        for line in handoff_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows[0]["path_type"] = "shadow"
    rows[0]["delivery_class"] = "audit_only"
    rows[0]["consumer_visibility"] = "hidden"
    handoff_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    result = await get_distribution_drift(handoff_path=str(handoff_path))

    assert result["status"] == "critical"
    assert result["classification_mismatch_count"] == 1
    assert result["visibility_mismatch_count"] == 1
    assert any(
        issue["category"] == "distribution_drift"
        for issue in result["issues"]
    )


@pytest.mark.asyncio
async def test_get_distribution_drift_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = tmp_path.parent / "evil_handoffs.jsonl"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_distribution_drift(handoff_path=str(outside))


@pytest.mark.asyncio
async def test_get_protective_gate_summary_returns_readiness_derived_gate_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )

    result = await get_protective_gate_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "protective_gate_summary"
    assert result["derived_from"] == "operational_readiness"
    assert result["gate_status"] == "blocking"
    assert result["blocking_count"] >= 1
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert any(item["subsystem"] == "providers" for item in result["items"])


@pytest.mark.asyncio
async def test_get_protective_gate_summary_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = tmp_path.parent / "evil_handoffs.jsonl"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_protective_gate_summary(handoff_path=str(outside))


@pytest.mark.asyncio
async def test_get_remediation_recommendations_returns_read_only_recommendations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )

    result = await get_remediation_recommendations(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "remediation_recommendation_report"
    assert result["derived_from"] == "protective_gate_summary"
    assert result["gate_status"] == "blocking"
    assert result["recommendation_count"] >= 1
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    recommendation = result["recommendations"][0]
    assert isinstance(recommendation["recommended_actions"], list)
    assert recommendation["recommended_actions"]
    assert isinstance(recommendation["evidence_refs"], list)


@pytest.mark.asyncio
async def test_get_escalation_summary_returns_read_only_review_aware_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_escalation_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "operational_escalation_summary"
    assert result["escalation_status"] == "blocking"
    assert result["blocking"] is True
    assert result["blocking_count"] >= 1
    assert result["review_required_count"] == 1
    assert result["operator_action_count"] >= 2
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert any(item["category"] == "review_required" for item in result["items"])


@pytest.mark.asyncio
async def test_get_operational_escalation_summary_alias_matches_canonical_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    canonical = await get_escalation_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
        artifacts_dir="artifacts",
    )
    alias = await get_operational_escalation_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert alias["report_type"] == canonical["report_type"]
    assert alias["escalation_status"] == canonical["escalation_status"]
    assert alias["blocking_count"] == canonical["blocking_count"]
    assert alias["review_required_count"] == canonical["review_required_count"]
    assert alias["operator_action_count"] == canonical["operator_action_count"]
    assert alias["items"] == canonical["items"]


@pytest.mark.asyncio
async def test_get_blocking_summary_filters_blocking_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )

    result = await get_blocking_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "blocking_summary"
    assert result["blocking"] is True
    assert result["blocking_count"] >= 1
    assert result["severity"] == "critical"
    assert result["items"]
    assert all(item["blocking"] is True for item in result["items"])


@pytest.mark.asyncio
async def test_get_operator_action_summary_includes_review_required_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_operator_action_summary(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "operator_action_summary"
    assert result["blocking"] is False
    assert result["operator_action_count"] >= 1
    assert result["review_required_count"] == 1
    assert any(item["category"] == "review_required" for item in result["items"])


@pytest.mark.asyncio
async def test_get_action_queue_summary_returns_prioritized_read_only_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_action_queue_summary(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "action_queue_summary"
    assert result["queue_status"] == "blocking"
    assert result["blocking_count"] >= 1
    assert result["review_required_count"] == 1
    assert result["highest_priority"] == "p1"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert result["items"]
    assert result["items"][0]["priority"] == "p1"
    assert result["items"][0]["action_id"].startswith("act_")


@pytest.mark.asyncio
async def test_get_blocking_actions_filters_only_blocking_queue_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path,
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    await activate_route_profile(
        str(profile_path),
        state_path="artifacts/active_route_profile.json",
        abc_envelope_output="artifacts/abc/missing_envelopes.jsonl",
    )

    result = await get_blocking_actions(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
    )

    assert result["report_type"] == "blocking_actions_summary"
    assert result["queue_status"] == "blocking"
    assert result["blocking_count"] >= 1
    assert result["highest_priority"] == "p1"
    assert result["items"]
    assert all(item["queue_status"] == "blocking" for item in result["items"])


@pytest.mark.asyncio
async def test_get_prioritized_actions_returns_priority_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_prioritized_actions(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "prioritized_actions_summary"
    assert result["action_count"] >= 1
    assert result["highest_priority"] == "p2"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    priorities = [item["priority"] for item in result["items"]]
    assert priorities == sorted(priorities)


@pytest.mark.asyncio
async def test_get_review_required_actions_filters_review_required_queue_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_review_required_actions(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "review_required_actions_summary"
    assert result["queue_status"] == "review_required"
    assert result["review_required_count"] == 1
    assert result["highest_priority"] == "p2"
    assert len(result["items"]) == 1
    assert result["items"][0]["queue_status"] == "review_required"
    assert result["items"][0]["blocking"] is False


@pytest.mark.asyncio
async def test_get_decision_pack_summary_returns_canonical_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path / "artifacts",
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )
    from app.research.active_route import activate_route_profile

    activate_route_profile(
        profile_path=profile_path,
        state_path=tmp_path / "artifacts" / "active_route_profile.json",
        abc_envelope_output=tmp_path / "artifacts" / "routes" / "missing_abc.jsonl",
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_decision_pack_summary(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )

    assert result["report_type"] == "operator_decision_pack"
    assert result["overall_status"] == "blocking"
    assert result["blocking_count"] >= 1
    assert result["review_required_count"] == 1
    assert result["action_queue_count"] >= 2
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert result["readiness_summary"]["report_type"] == "operational_readiness"
    assert result["blocking_summary"]["report_type"] == "blocking_summary"
    assert result["action_queue_summary"]["report_type"] == "action_queue_summary"
    assert (
        result["review_required_summary"]["report_type"]
        == "review_required_artifact_summary"
    )


@pytest.mark.asyncio
async def test_get_operator_decision_pack_alias_matches_canonical_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    canonical = await get_decision_pack_summary(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )
    alias = await get_operator_decision_pack(
        handoff_path=str(handoff_path),
        artifacts_dir="artifacts",
    )

    assert alias["report_type"] == "operator_decision_pack"
    assert alias["overall_status"] == canonical["overall_status"]
    assert alias["blocking_count"] == canonical["blocking_count"]
    assert alias["review_required_count"] == canonical["review_required_count"]
    assert alias["action_queue_count"] == canonical["action_queue_count"]


@pytest.mark.asyncio
async def test_get_daily_operator_summary_aggregates_canonical_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC).isoformat()

    async def fake_readiness(**_kwargs: object) -> dict[str, object]:
        return {"readiness_status": "warning"}

    async def fake_recent_cycles(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "recent_trading_cycles_summary",
            "recent_cycles": [
                {
                    "status": "no_signal",
                    "symbol": "BTC/USDT",
                    "completed_at": now,
                }
            ],
        }

    async def fake_portfolio(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "paper_portfolio_snapshot",
            "position_count": 2,
            "total_equity_usd": 10_000.0,
        }

    async def fake_exposure(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "paper_exposure_summary",
            "gross_exposure_usd": 2_500.0,
            "mark_to_market_status": "ok",
        }

    async def fake_decision_pack(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "operator_decision_pack",
            "overall_status": "warning",
        }

    async def fake_review_journal(**_kwargs: object) -> dict[str, object]:
        return {
            "report_type": "review_journal_summary",
            "open_count": 3,
        }

    monkeypatch.setattr(
        mcp_server_module,
        "get_operational_readiness_summary",
        fake_readiness,
    )
    monkeypatch.setattr(mcp_server_module, "get_recent_trading_cycles", fake_recent_cycles)
    monkeypatch.setattr(mcp_server_module, "get_paper_portfolio_snapshot", fake_portfolio)
    monkeypatch.setattr(mcp_server_module, "get_paper_exposure_summary", fake_exposure)
    monkeypatch.setattr(mcp_server_module, "get_decision_pack_summary", fake_decision_pack)
    monkeypatch.setattr(mcp_server_module, "get_review_journal_summary", fake_review_journal)

    payload = await get_daily_operator_summary()

    assert payload["report_type"] == "daily_operator_summary"
    assert payload["readiness_status"] == "warning"
    assert payload["cycle_count_today"] == 1
    assert payload["last_cycle_status"] == "no_signal"
    assert payload["last_cycle_symbol"] == "BTC/USDT"
    assert payload["position_count"] == 2
    assert payload["total_exposure_pct"] == 25.0
    assert payload["mark_to_market_status"] == "ok"
    assert payload["decision_pack_status"] == "warning"
    assert payload["open_incidents"] == 3
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False
    assert set(payload["sources"]) == {
        "readiness_summary",
        "recent_cycles",
        "portfolio_snapshot",
        "exposure_summary",
        "decision_pack_summary",
        "review_journal_summary",
    }


@pytest.mark.asyncio
async def test_get_daily_operator_summary_degrades_fail_closed_on_surface_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC).isoformat()

    async def failing_readiness(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("readiness unavailable")

    async def fake_recent_cycles(**_kwargs: object) -> dict[str, object]:
        return {"recent_cycles": [{"status": "no_signal", "completed_at": now}]}

    async def fake_portfolio(**_kwargs: object) -> dict[str, object]:
        return {"position_count": 0, "total_equity_usd": 0.0}

    async def fake_exposure(**_kwargs: object) -> dict[str, object]:
        return {"gross_exposure_usd": 0.0, "mark_to_market_status": "unknown"}

    async def fake_decision_pack(**_kwargs: object) -> dict[str, object]:
        return {"overall_status": "clear"}

    async def fake_review_journal(**_kwargs: object) -> dict[str, object]:
        return {"open_count": 0}

    monkeypatch.setattr(
        mcp_server_module,
        "get_operational_readiness_summary",
        failing_readiness,
    )
    monkeypatch.setattr(mcp_server_module, "get_recent_trading_cycles", fake_recent_cycles)
    monkeypatch.setattr(mcp_server_module, "get_paper_portfolio_snapshot", fake_portfolio)
    monkeypatch.setattr(mcp_server_module, "get_paper_exposure_summary", fake_exposure)
    monkeypatch.setattr(mcp_server_module, "get_decision_pack_summary", fake_decision_pack)
    monkeypatch.setattr(mcp_server_module, "get_review_journal_summary", fake_review_journal)

    payload = await get_daily_operator_summary()

    assert payload["report_type"] == "daily_operator_summary"
    assert payload["readiness_status"] == "unknown"
    assert payload["decision_pack_status"] == "clear"
    assert "readiness_summary" not in payload["sources"]
    assert payload["execution_enabled"] is False
    assert payload["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_mcp_and_cli_command_inventory_stay_consistent_for_locked_surfaces() -> None:
    from app.cli.main import get_research_command_inventory

    payload = json.loads(await get_mcp_capabilities())
    inventory = get_research_command_inventory()

    assert "get_handoff_collector_summary" in payload["read_tools"]
    assert "get_decision_pack_summary" in payload["read_tools"]
    assert "get_daily_operator_summary" in payload["read_tools"]
    assert "get_operator_runbook" in payload["read_tools"]
    assert (
        payload["aliases"]["get_handoff_summary"]["canonical_tool"]
        == "get_handoff_collector_summary"
    )
    assert (
        payload["aliases"]["get_operator_decision_pack"]["canonical_tool"]
        == "get_decision_pack_summary"
    )
    assert (
        payload["superseded_tools"]["get_operational_escalation_summary"][
            "replacement_tool"
        ]
        == "get_escalation_summary"
    )

    assert inventory["aliases"]["handoff-summary"] == "handoff-collector-summary"
    assert inventory["aliases"]["consumer-ack"] == "handoff-acknowledge"
    assert inventory["aliases"]["operator-decision-pack"] == "decision-pack-summary"
    assert "governance-summary" in inventory["superseded_commands"]


@pytest.mark.asyncio
async def test_get_operator_runbook_returns_validated_read_only_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.cli.main import get_registered_research_command_names
    from app.research.active_route import activate_route_profile

    _patch_workspace_root(monkeypatch, tmp_path)
    handoff_path, _payload = _write_signal_handoff_batch(
        tmp_path / "artifacts" / "handoffs.jsonl"
    )
    profile_path = _write_route_profile(
        tmp_path / "artifacts",
        route_profile="primary_with_shadow_and_control",
        shadow_paths=["B.companion"],
        control_path="C.rule",
    )

    activate_route_profile(
        profile_path=profile_path,
        state_path=tmp_path / "artifacts" / "active_route_profile.json",
        abc_envelope_output=tmp_path / "artifacts" / "routes" / "missing_abc.jsonl",
    )
    (tmp_path / "artifacts" / "manual_review_blob.json").write_text(
        "{}",
        encoding="utf-8",
    )

    result = await get_operator_runbook(
        handoff_path=str(handoff_path),
        state_path="artifacts/active_route_profile.json",
        artifacts_dir="artifacts",
    )

    registered = get_registered_research_command_names()
    assert result["report_type"] == "operator_runbook_summary"
    assert result["overall_status"] == "blocking"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert result["steps"]
    assert result["next_steps"]
    assert result["next_steps"] == result["steps"][: len(result["next_steps"])]
    assert "research governance-summary" not in result["command_refs"]
    assert "research operator-runbook" not in result["command_refs"]

    for ref in result["command_refs"]:
        parts = ref.split()
        assert len(parts) == 2
        assert parts[0] == "research"
        assert parts[1] in registered


@pytest.mark.asyncio
async def test_get_operator_runbook_fails_closed_on_invalid_command_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.cli import research as cli_research

    _patch_workspace_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli_research,
        "extract_runbook_command_refs",
        lambda _payload: ["research governance-summary"],
    )
    monkeypatch.setattr(
        cli_research,
        "get_invalid_research_command_refs",
        lambda refs: list(refs),
    )

    with pytest.raises(ValueError, match="invalid research command references"):
        await get_operator_runbook(artifacts_dir="artifacts")


@pytest.mark.asyncio
async def test_get_escalation_summary_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = tmp_path.parent / "evil_handoffs.jsonl"

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_escalation_summary(handoff_path=str(outside))


# ---------------------------------------------------------------------------
# Sprint 25: Retention policy MCP tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_artifact_inventory_reports_current_and_stale_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    current = arts_dir / "report.json"
    stale = arts_dir / "benchmark.json"
    current.write_text("{}", encoding="utf-8")
    stale.write_text("{}", encoding="utf-8")
    old_mtime = stale.stat().st_mtime - 40 * 86400
    import os

    os.utime(stale, (old_mtime, old_mtime))

    result = await get_artifact_inventory(
        artifacts_dir="artifacts",
        stale_after_days=30.0,
    )

    assert result["report_type"] == "artifact_inventory"
    assert result["current_count"] == 1
    assert result["stale_count"] == 1


@pytest.mark.asyncio
async def test_get_artifact_retention_report_read_only_invariants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retention report must carry execution_enabled=False, write_back_allowed=False,
    delete_eligible_count=0 (I-154, I-161)."""
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    (arts_dir / "mcp_write_audit.jsonl").write_text("{}", encoding="utf-8")
    (arts_dir / "benchmark.json").write_text("{}", encoding="utf-8")

    result = await get_artifact_retention_report(artifacts_dir="artifacts")

    assert result["report_type"] == "artifact_retention_report"
    assert result["execution_enabled"] is False        # I-161
    assert result["write_back_allowed"] is False       # I-161
    assert result["delete_eligible_count"] == 0        # I-154
    assert result["total_count"] == 2


@pytest.mark.asyncio
async def test_get_artifact_retention_report_protected_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit trail and promotion record must be classified as protected."""
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    (arts_dir / "mcp_write_audit.jsonl").write_text("{}", encoding="utf-8")
    (arts_dir / "promotion_record.json").write_text("{}", encoding="utf-8")

    result = await get_artifact_retention_report(artifacts_dir="artifacts")

    assert result["protected_count"] == 2
    assert result["rotatable_count"] == 0
    for entry in result["entries"]:
        assert entry["protected"] is True
        assert entry["delete_eligible"] is False  # I-154


@pytest.mark.asyncio
async def test_get_cleanup_eligibility_summary_returns_rotatable_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    stale_report = arts_dir / "readiness_report.json"
    stale_report.write_text("{}", encoding="utf-8")
    old_mtime = stale_report.stat().st_mtime - 45 * 86400
    import os

    os.utime(stale_report, (old_mtime, old_mtime))

    result = await get_cleanup_eligibility_summary(artifacts_dir="artifacts")

    assert result["report_type"] == "cleanup_eligibility_summary"
    assert result["cleanup_eligible_count"] == 1
    assert result["dry_run_default"] is True
    assert result["delete_eligible_count"] == 0
    assert result["candidates"][0]["path"] == "readiness_report.json"


@pytest.mark.asyncio
async def test_get_protected_artifact_summary_returns_protected_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    (arts_dir / "mcp_write_audit.jsonl").write_text("{}", encoding="utf-8")
    (arts_dir / "promotion_record.json").write_text("{}", encoding="utf-8")

    result = await get_protected_artifact_summary(artifacts_dir="artifacts")

    assert result["report_type"] == "protected_artifact_summary"
    assert result["protected_count"] == 2
    protected_paths = {entry["path"] for entry in result["entries"]}
    assert protected_paths == {"mcp_write_audit.jsonl", "promotion_record.json"}


@pytest.mark.asyncio
async def test_get_review_required_summary_returns_review_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    arts_dir = tmp_path / "artifacts"
    arts_dir.mkdir(parents=True)
    (arts_dir / "fresh.json").write_text("{}", encoding="utf-8")

    result = await get_review_required_summary(artifacts_dir="artifacts")

    assert result["report_type"] == "review_required_artifact_summary"
    assert result["review_required_count"] == 1
    assert result["entries"][0]["path"] == "fresh.json"
    assert result["entries"][0]["retention_rationale"]
    assert result["entries"][0]["operator_guidance"]


@pytest.mark.asyncio
async def test_get_artifact_retention_report_blocks_path_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = str(tmp_path.parent / "evil_artifacts")

    with pytest.raises(ValueError, match="must stay within workspace"):
        await get_artifact_retention_report(artifacts_dir=outside)


# ---------------------------------------------------------------------------
# Sprint 32: Coverage Completion — get_narrative_clusters + get_operational_escalation_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.agents.mcp_server.build_session_factory")
@patch("app.agents.mcp_server.get_settings")
async def test_get_narrative_clusters_returns_read_only_report(
    mock_settings: MagicMock,
    mock_session_factory: MagicMock,
) -> None:
    """get_narrative_clusters stays read-only and execution-disabled."""
    mock_settings.return_value = SimpleNamespace(db=MagicMock())

    mock_session = AsyncMock()
    mock_session_factory.return_value.begin.return_value.__aenter__.return_value = mock_session

    with patch("app.agents.mcp_server.DocumentRepository") as mock_repo_cls:
        mock_repo = mock_repo_cls.return_value
        mock_repo.list = AsyncMock(return_value=[])

        result = await get_narrative_clusters(min_priority=8, limit=10)

    assert result["report_type"] == "narrative_cluster_report"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert result["cluster_count"] == 0
    assert result["candidate_count"] == 0
    assert isinstance(result["clusters"], list)


@pytest.mark.asyncio
async def test_get_operational_escalation_summary_excluded_from_read_tools() -> None:
    """Superseded escalation alias stays out of read_tools."""
    caps = json.loads(await get_mcp_capabilities())
    assert "get_operational_escalation_summary" not in caps["read_tools"], (
        "Superseded tool must not appear in read_tools (I-204, I-212)"
    )
    assert "get_escalation_summary" in caps["read_tools"], (
        "Canonical escalation tool must remain in read_tools"
    )


@pytest.mark.asyncio
async def test_get_operational_escalation_summary_returns_valid_structure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Superseded escalation alias still returns a read-only payload."""
    _patch_workspace_root(monkeypatch, tmp_path)

    result = await get_operational_escalation_summary()

    assert result["report_type"] == "operational_escalation_summary"
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_mcp_capabilities_sprint32_contract(
) -> None:
    """Sprint 32 keeps canonical reads and excludes superseded reads."""
    caps = json.loads(await get_mcp_capabilities())

    assert "get_narrative_clusters" in caps["read_tools"], (
        "get_narrative_clusters must be in read_tools (I-205, I-214)"
    )
    assert "get_operational_escalation_summary" not in caps["read_tools"], (
        "Superseded get_operational_escalation_summary must not be in read_tools (I-204, I-215)"
    )


# ---------------------------------------------------------------------------
# Sprint 33: append-only review journal / resolution tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_review_journal_entry_appends_audit_only_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.research.operational_readiness import load_review_journal_entries

    _patch_workspace_root(monkeypatch, tmp_path)

    result = await append_review_journal_entry(
        source_ref="rbk_123",
        operator_id="ops-1",
        review_action="note",
        review_note="Operator reviewed the blocking step.",
        evidence_refs=["artifacts/decision_pack.json"],
    )

    journal_path = tmp_path / "artifacts" / "operator_review_journal.jsonl"
    entries = load_review_journal_entries(journal_path)

    assert result["status"] == "review_journal_appended"
    assert result["core_state_unchanged"] is True
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False
    assert len(entries) == 1
    assert entries[0].source_ref == "rbk_123"
    assert entries[0].journal_status == "open"


@pytest.mark.asyncio
async def test_append_review_journal_entry_blocks_path_outside_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    outside = str(tmp_path / "review_journal.jsonl")

    with pytest.raises(ValueError, match="must be within workspace/artifacts/"):
        await append_review_journal_entry(
            source_ref="rbk_123",
            operator_id="ops-1",
            review_action="note",
            review_note="Should fail closed.",
            journal_output_path=outside,
        )


@pytest.mark.asyncio
async def test_get_review_journal_summary_returns_read_only_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    await append_review_journal_entry(
        source_ref="rbk_123",
        operator_id="ops-1",
        review_action="note",
        review_note="Still open.",
    )
    await append_review_journal_entry(
        source_ref="act_456",
        operator_id="ops-2",
        review_action="resolve",
        review_note="Resolved after operator review.",
    )

    result = await get_review_journal_summary()

    assert result["report_type"] == "review_journal_summary"
    assert result["journal_status"] == "open"
    assert result["total_count"] == 2
    assert result["open_count"] == 1
    assert result["resolved_count"] == 1
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False


@pytest.mark.asyncio
async def test_get_resolution_summary_returns_latest_source_resolution_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_workspace_root(monkeypatch, tmp_path)
    await append_review_journal_entry(
        source_ref="rbk_123",
        operator_id="ops-1",
        review_action="note",
        review_note="Initial note.",
    )
    await append_review_journal_entry(
        source_ref="rbk_123",
        operator_id="ops-1",
        review_action="resolve",
        review_note="Now resolved.",
    )
    await append_review_journal_entry(
        source_ref="act_456",
        operator_id="ops-2",
        review_action="defer",
        review_note="Still open.",
    )

    result = await get_resolution_summary()

    assert result["report_type"] == "review_resolution_summary"
    assert result["journal_status"] == "open"
    assert result["open_count"] == 1
    assert result["resolved_count"] == 1
    assert result["open_source_refs"] == ["act_456"]
    assert result["resolved_source_refs"] == ["rbk_123"]
    assert result["execution_enabled"] is False
    assert result["write_back_allowed"] is False

