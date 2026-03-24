"""Shared helper functions for MCP unit tests.

These are plain functions (not fixtures) so they can be imported directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import app.agents.mcp_server as mcp_server_module
from app.core.enums import AnalysisSource, SentimentLabel, SourceType
from app.execution.portfolio_read import ExposureSummary, PortfolioSnapshot, PositionSummary
from app.research.abc_result import (
    ABCInferenceEnvelope,
    PathResultEnvelope,
    save_abc_inference_envelope_jsonl,
)
from app.research.execution_handoff import (
    create_signal_handoff,
    save_signal_handoff_batch_jsonl,
)
from app.research.inference_profile import (
    InferenceRouteProfile,
    save_inference_route_profile,
)
from app.research.signals import extract_signal_candidates
from tests.unit.factories import make_document


def _patch_workspace_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    import app.agents.tools._helpers as _helpers_module

    resolved = root.resolve()
    monkeypatch.setattr(mcp_server_module, "_WORKSPACE_ROOT", resolved)
    monkeypatch.setattr(_helpers_module, "WORKSPACE_ROOT", resolved)


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


def _snapshot(*, available: bool = True, error: str | None = None) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        generated_at_utc="2026-03-21T12:00:00+00:00",
        source="paper_execution_audit_replay",
        audit_path="artifacts/paper_execution_audit.jsonl",
        cash_usd=5800.0,
        realized_pnl_usd=0.0,
        total_market_value_usd=12000.0,
        total_equity_usd=17800.0,
        position_count=1,
        positions=(
            PositionSummary(
                symbol="BTC/USDT",
                quantity=0.2,
                avg_entry_price=50000.0,
                stop_loss=48000.0,
                take_profit=70000.0,
                market_price=60000.0,
                market_value_usd=12000.0,
                unrealized_pnl_usd=2000.0,
                provider="coingecko",
                market_data_retrieved_at_utc="2026-03-21T12:00:00+00:00",
                market_data_source_timestamp_utc="2026-03-21T11:59:00+00:00",
                market_data_is_stale=False,
                market_data_freshness_seconds=60.0,
                market_data_available=True,
                market_data_error=None,
            ),
        ),
        exposure_summary=ExposureSummary(
            priced_position_count=1,
            stale_position_count=0,
            unavailable_price_count=0,
            gross_exposure_usd=12000.0,
            net_exposure_usd=12000.0,
            largest_position_symbol="BTC/USDT",
            largest_position_weight_pct=100.0,
            mark_to_market_status="ok",
        ),
        available=available,
        error=error,
    )
