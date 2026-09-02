"""Use Case A — reproducible AI-orchestration E2E without network.

One document walks the whole path: normalisation -> routing -> provider failure
-> classified fallback -> result, and the audit trail is asserted alongside the
state. Asserting only one of the two would not be an E2E test.

Network freedom is structural, not a matter of mocking discipline: the fakes
satisfy ``BaseAnalysisProvider.analyze`` and no HTTP client exists in the test
path. The httpx monkeypatch at the end turns that from a claim into a proof.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.ai.audit import correlation_scope
from app.analysis.base.interfaces import LLMAnalysisOutput
from app.analysis.ensemble.provider import EnsembleProvider
from app.analysis.internal_model.provider import InternalModelProvider
from app.analysis.keywords.engine import KeywordEngine
from app.analysis.keywords.watchlist import WatchlistEntry
from app.analysis.pipeline import AnalysisPipeline
from app.core.domain.document import CanonicalDocument
from app.core.enums import AnalysisSource, MarketScope, SentimentLabel
from app.observability.llm_telemetry import llm_telemetry_summary

_CORRELATION_ID = "req_e2e_0001"

_BODY = (
    "Bitcoin exchange-traded funds were approved by the regulator today, "
    "opening institutional access to BTC for the first time. Analysts expect "
    "significant inflows over the coming quarters as pension funds and asset "
    "managers gain a compliant vehicle for spot exposure. The approval follows "
    "a decade of rejected filings and is widely read as a structural shift in "
    "market access rather than a short-lived narrative."
)


class _HTTPStatusError(Exception):
    """Duck-typed SDK failure: classify_error() reads .status_code."""

    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.status_code = status


def _btc_engine() -> KeywordEngine:
    return KeywordEngine(
        keywords=frozenset({"halving", "etf"}),
        watchlist_entries=[
            WatchlistEntry(
                symbol="BTC",
                name="Bitcoin",
                aliases=frozenset({"bitcoin"}),
                tags=(),
                category="crypto",
            )
        ],
        entity_aliases=[],
    )


def _llm_output(provider: str) -> LLMAnalysisOutput:
    out = LLMAnalysisOutput(
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=0.9,
        impact_score=0.7,
        confidence_score=0.85,
        novelty_score=0.6,
        spam_probability=0.01,
        market_scope=MarketScope.CRYPTO,
        affected_assets=["BTC"],
        short_reasoning="Spot ETF approval widens institutional access.",
        recommended_priority=7,
        actionable=True,
    )
    out.provider_used = provider
    out.prompt_tokens = 512
    out.completion_tokens = 96
    return out


def _ok_provider(name: str, model: str) -> Any:
    provider = AsyncMock()
    provider.provider_name = name
    provider.model = model
    provider.analyze = AsyncMock(return_value=_llm_output(name))
    return provider


def _failing_provider(name: str, model: str, exc: BaseException) -> Any:
    provider = AsyncMock()
    provider.provider_name = name
    provider.model = model
    provider.analyze = AsyncMock(side_effect=exc)
    return provider


def _doc() -> CanonicalDocument:
    return CanonicalDocument(
        url="https://example.com/btc-etf-approved",
        title="Bitcoin ETF approved",
        raw_text=_BODY,
        cleaned_text=_BODY,
    )


@pytest.fixture
def telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sink = tmp_path / "llm_telemetry.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    return sink


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


async def _run(providers: list[Any]) -> Any:
    pipeline = AnalysisPipeline(
        keyword_engine=_btc_engine(),
        provider=EnsembleProvider(providers),
        run_llm=True,
        crypto_gate_mode="off",
    )
    with correlation_scope(_CORRELATION_ID):
        return await pipeline.run(_doc(), correlation_id=_CORRELATION_ID)


# ── Use Case A: 429 -> rate_limit -> fallback to gemini ──────────────────────


async def test_rate_limited_primary_falls_through_to_gemini(telemetry: Path) -> None:
    result = await _run(
        [
            _failing_provider("openai", "gpt-4o", _HTTPStatusError(429)),
            _ok_provider("gemini", "gemini-2.5-flash"),
        ]
    )

    # --- State ---
    assert result.error is None
    assert result.provider_name == "gemini"
    assert result.model_name == "gemini-2.5-flash"
    assert result.llm_output is not None
    assert result.llm_output.provider_used == "gemini"
    assert result.analysis_result is not None
    assert result.analysis_result.analysis_source is AnalysisSource.EXTERNAL_LLM

    # --- Audit ---
    rows = _rows(telemetry)
    attempts = sorted(
        [r for r in rows if r["chain_position"] >= 0], key=lambda r: r["chain_position"]
    )
    outer = [r for r in rows if r["chain_position"] == -1]

    assert {r["correlation_id"] for r in rows} == {_CORRELATION_ID}
    assert len({r["call_id"] for r in attempts}) == 2
    assert len(outer) == 1

    assert attempts[0]["chain_position"] == 0
    assert attempts[0]["provider"] == "openai"
    assert attempts[0]["model"] == "gpt-4o"
    assert attempts[0]["ok"] is False
    assert attempts[0]["error_class"] == "rate_limit"
    assert attempts[0]["http_status"] == 429
    assert attempts[0]["outcome"] == "fallthrough"

    assert attempts[1]["provider"] == "gemini"
    assert attempts[1]["ok"] is True
    assert attempts[1]["outcome"] == "success"
    assert attempts[1]["latency_ms"] >= 0.0
    assert attempts[1]["prompt_tokens"] == 512
    assert attempts[1]["completion_tokens"] == 96

    assert all(r["schema_version"] == "v2" and r["purpose"] == "analysis" for r in rows)

    # --- Secret-leak gate ---
    blob = json.dumps(rows)
    assert "api_key" not in blob
    assert "sk-" not in blob
    assert "Bearer" not in blob

    # --- v1 reader must not choke on v2 ---
    summary = llm_telemetry_summary(window_hours=1.0, path=telemetry)
    assert summary["n"] == len(rows)
    assert summary["failures"] == 1


@pytest.mark.parametrize(
    ("exc", "expected_class", "expected_status"),
    [
        (_HTTPStatusError(429), "rate_limit", 429),
        (_HTTPStatusError(503), "server", 503),
        (TimeoutError("upstream took too long"), "timeout", None),
        (_HTTPStatusError(401), "auth", 401),
    ],
)
async def test_failure_classes_are_recorded(
    telemetry: Path, exc: BaseException, expected_class: str, expected_status: int | None
) -> None:
    result = await _run(
        [
            _failing_provider("openai", "gpt-4o", exc),
            _ok_provider("gemini", "gemini-2.5-flash"),
        ]
    )

    assert result.provider_name == "gemini"
    first = sorted(
        [r for r in _rows(telemetry) if r["chain_position"] >= 0], key=lambda r: r["chain_position"]
    )[0]
    assert first["error_class"] == expected_class
    assert first["http_status"] == expected_status


# ── Variant: every external provider fails, internal model delivers ──────────


async def test_all_external_providers_fail_internal_model_delivers(telemetry: Path) -> None:
    result = await _run(
        [
            _failing_provider("openai", "gpt-4o", _HTTPStatusError(429)),
            _failing_provider("gemini", "gemini-2.5-flash", _HTTPStatusError(503)),
            InternalModelProvider(_btc_engine()),
        ]
    )

    assert result.error is None
    assert result.llm_output is not None
    assert result.llm_output.provider_used == "internal"
    assert result.provider_name == "internal"
    assert result.model_name == "rule-heuristic-v1"
    # The internal model is a rule heuristic, not an external LLM — the source
    # must say so, otherwise a rule result would masquerade as an LLM result.
    assert result.analysis_result is not None
    assert result.analysis_result.analysis_source is not AnalysisSource.EXTERNAL_LLM

    attempts = sorted(
        [r for r in _rows(telemetry) if r["chain_position"] >= 0],
        key=lambda r: r["chain_position"],
    )
    assert [r["provider"] for r in attempts] == ["openai", "gemini", "internal"]
    assert [r["outcome"] for r in attempts] == ["fallthrough", "fallthrough", "success"]
    assert [r["ok"] for r in attempts] == [False, False, True]
    assert attempts[0]["error_class"] == "rate_limit"
    assert attempts[1]["error_class"] == "server"
    assert attempts[2]["error_class"] is None


async def test_every_provider_fails_last_row_is_exhausted(telemetry: Path) -> None:
    result = await _run(
        [
            _failing_provider("openai", "gpt-4o", _HTTPStatusError(500)),
            _failing_provider("gemini", "gemini-2.5-flash", _HTTPStatusError(500)),
        ]
    )

    # Pipeline degrades to the rule-based fallback rather than raising.
    assert result.analysis_result is not None
    assert result.llm_output is None

    rows = _rows(telemetry)
    attempts = sorted(
        [r for r in rows if r["chain_position"] >= 0], key=lambda r: r["chain_position"]
    )
    assert [r["outcome"] for r in attempts] == ["fallthrough", "exhausted"]

    outer = [r for r in rows if r["chain_position"] == -1]
    assert len(outer) == 1
    assert outer[0]["ok"] is False
    # The ensemble wrapper's RuntimeError is not an HTTP failure; honest "unknown".
    assert outer[0]["error_type"] == "RuntimeError"
    assert outer[0]["error_class"] == "unknown"


# ── Network-freedom negative control ─────────────────────────────────────────


async def test_no_http_traffic_happens_anywhere_in_this_path(
    telemetry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If any of the above secretly used httpx, this test would fail."""

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access attempted in an offline E2E test")

    def _boom_sync(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access attempted in an offline E2E test")

    monkeypatch.setattr(httpx.AsyncClient, "send", _boom, raising=True)
    monkeypatch.setattr(httpx.Client, "send", _boom_sync, raising=True)

    result = await _run(
        [
            _failing_provider("openai", "gpt-4o", _HTTPStatusError(429)),
            _ok_provider("gemini", "gemini-2.5-flash"),
        ]
    )

    assert result.provider_name == "gemini"
    assert len(_rows(telemetry)) == 3
