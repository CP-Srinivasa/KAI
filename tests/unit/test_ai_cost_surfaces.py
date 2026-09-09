"""Verschwendung, Zuordnung und Sichtbarkeit — die drei Nicht-Metering-Teile.

Abgedeckt: Shadow-Skip nach dem Relevanz-Gate (W2), der Schatten-Schalter
(Default AUS seit 2026-09-09), die Anbieter-vs-Quelle-Zuordnung in der Pipeline, die
Instrumentierung von ``app/intelligence`` und der Kostenblock in ``/health/ai``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.spend import reset_spend_cache
from app.analysis.factory import describe_shadow_chain
from app.core.ai_cost_settings import get_ai_cost_settings, reset_ai_cost_settings


@pytest.fixture(autouse=True)
def _clean() -> None:
    reset_spend_cache()
    reset_ai_cost_settings()


# ── W2: Schatten läuft nicht ohne Primäraufruf ─────────────────────────────


def _btc_engine() -> Any:
    """Dieselbe Keyword-Konfiguration wie in ``test_analysis_pipeline.py``."""
    from app.analysis.keywords.engine import KeywordEngine
    from app.analysis.keywords.watchlist import WatchlistEntry

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


def _shadow_doppel() -> Any:
    from unittest.mock import AsyncMock

    from app.analysis.base.interfaces import LLMAnalysisOutput
    from app.core.enums import MarketScope, SentimentLabel

    ausgabe = LLMAnalysisOutput(
        sentiment_label=SentimentLabel.NEUTRAL,
        sentiment_score=0.0,
        relevance_score=0.5,
        impact_score=0.5,
        confidence_score=0.5,
        novelty_score=0.5,
        spam_probability=0.01,
        market_scope=MarketScope.CRYPTO,
        short_reasoning="schatten",
    )
    doppel = AsyncMock()
    doppel.provider_name = "anthropic"
    doppel.model = "claude-sonnet-4-6"
    doppel.analyze = AsyncMock(return_value=ausgabe)
    return doppel


async def test_shadow_is_not_called_when_the_relevance_gate_declined_the_document() -> None:
    """Eine Schattenanalyse ohne Primäranalyse vergleicht nichts — und kostet."""
    from app.analysis.pipeline import AnalysisPipeline
    from app.core.domain.document import CanonicalDocument

    doppel = _shadow_doppel()
    primaer = _shadow_doppel()
    pipeline = AnalysisPipeline(
        keyword_engine=_btc_engine(),
        provider=primaer,
        shadow_provider=doppel,
        run_llm=True,
    )
    # Kein Ticker, kein Krypto-Asset, keine Keyword-Treffer -> Relevanz-Gate.
    doc = CanonicalDocument(
        url="https://example.com/irrelevant",
        title="Local bakery opens second branch downtown",
        raw_text="A bakery opened a new branch. " * 20,
    )
    ergebnis = await pipeline.run(doc)

    assert ergebnis.analysis_result is not None
    # Der Kern: WEDER Primaer NOCH Schatten wurden bezahlt.
    primaer.analyze.assert_not_awaited()
    doppel.analyze.assert_not_awaited()
    assert ergebnis.shadow_llm_output is None


async def test_shadow_still_runs_when_there_is_no_primary_provider_at_all() -> None:
    """Kein Primaerprovider ist keine Gate-Entscheidung — der Schatten bleibt der Analyst."""
    from app.analysis.pipeline import AnalysisPipeline
    from app.core.domain.document import CanonicalDocument

    doppel = _shadow_doppel()
    pipeline = AnalysisPipeline(
        keyword_engine=_btc_engine(),
        provider=None,
        shadow_provider=doppel,
        run_llm=False,
    )
    ergebnis = await pipeline.run(
        CanonicalDocument(
            url="https://example.com/2",
            title="Bitcoin halving approaches",
            raw_text="BTC outlook remains active. " * 20,
        )
    )
    doppel.analyze.assert_awaited()
    assert ergebnis.shadow_llm_output is not None


# ── Schatten-Schalter ──────────────────────────────────────────────────────


def _settings_mit_keys() -> Any:
    return SimpleNamespace(
        providers=SimpleNamespace(
            anthropic_api_key="sk-a",
            gemini_api_key="sk-g",
            openai_api_key="sk-o",
            xai_api_key="",
            xai_fallback_enabled=False,
        )
    )


def test_shadow_flag_defaults_to_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zweitmeinung AUS als Standard (Operator-Entscheidung 2026-09-09)."""
    monkeypatch.delenv("APP_ANALYSIS_SHADOW_ENABLED", raising=False)
    reset_ai_cost_settings()
    assert get_ai_cost_settings().shadow_enabled is False
    assert describe_shadow_chain(_settings_mit_keys()) == []


def test_shadow_flag_true_switches_the_chain_back_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Schalter ist ein Schalter: ``true`` stellt das alte Verhalten her."""
    monkeypatch.setenv("APP_ANALYSIS_SHADOW_ENABLED", "true")
    reset_ai_cost_settings()
    assert get_ai_cost_settings().shadow_enabled is True
    assert describe_shadow_chain(_settings_mit_keys()) == ["anthropic"]


def test_shadow_flag_false_empties_the_chain_for_factory_and_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EINE Quelle: was die Factory nicht baut, meldet /health/ai auch nicht."""
    monkeypatch.setenv("APP_ANALYSIS_SHADOW_ENABLED", "false")
    reset_ai_cost_settings()
    assert get_ai_cost_settings().shadow_enabled is False
    assert describe_shadow_chain(_settings_mit_keys()) == []


# ── Zuordnung: Anbieter ist nicht die Quelle ───────────────────────────────


def test_source_name_is_pushed_out_of_the_provider_field() -> None:
    from app.analysis.pipeline import _telemetry_provider

    anbieter = SimpleNamespace(provider_name="anthropic")
    # Der Defektfall: der aufgeloeste Name IST der Feedname.
    assert _telemetry_provider("CNBC", anbieter, "CNBC") == "anthropic"
    # Der Normalfall bleibt unberuehrt.
    assert _telemetry_provider("openai", anbieter, "CNBC") == "openai"
    # Ohne Quelle wird nichts umgeschrieben.
    assert _telemetry_provider("openai", anbieter, None) == "openai"


def test_pipeline_passes_the_document_source_into_its_own_field() -> None:
    quelle = Path("app/analysis/pipeline.py").read_text(encoding="utf-8")
    assert quelle.count("source=doc.source_name") >= 3


# ── app/intelligence: die zweite LLM-Schicht wird sichtbar ─────────────────


def test_paid_intelligence_call_writes_one_row_with_use_case_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = tmp_path / "llm.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    from app.intelligence.providers import _ok

    _ok("claude", "claude-sonnet-4-6", 0.0, {"confidence": 0.5})
    row = json.loads(sink.read_text("utf-8").splitlines()[0])
    # Der SEAM heisst "claude", der bezahlte Anbieter heisst "anthropic".
    assert row["provider"] == "anthropic"
    assert row["use_case"] == "research"
    assert row["source"] == "intelligence:claude"
    assert row["purpose"] == "analysis"


def test_free_seams_are_deliberately_not_instrumented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama und Mock kosten nichts — sie dürfen die Unbekannt-Quote nicht verderben."""
    sink = tmp_path / "llm.jsonl"
    monkeypatch.setattr("app.observability.llm_telemetry.DEFAULT_TELEMETRY_PATH", sink)
    from app.intelligence.providers import _fail, _ok

    _ok("ollama", "llama3", 0.0, {})
    _fail("mock", "mock-fixture", 0.0, "unavailable")
    assert not sink.exists()


def test_noop_provider_writes_nothing() -> None:
    from app.intelligence.providers import _PAID_SEAM_PROVIDERS, NoOpProvider

    assert NoOpProvider.name not in _PAID_SEAM_PROVIDERS


# ── /health/ai: der Kostenblock ────────────────────────────────────────────


def test_health_cost_block_names_its_own_uncertainty(tmp_path: Path) -> None:
    from app.ai.health import cost_block

    block = cost_block(path=tmp_path / "leer.jsonl")
    assert block["note"] == "estimates from list prices; billing amounts are separate"
    assert block["status"] == "OK"
    assert block["price_table_version"]
    for schluessel in (
        "today_usd_known",
        "month_usd_known",
        "unknown_cost_calls_today",
        "unknown_cost_calls_month",
        "daily_limit_usd",
        "monthly_limit_usd",
        "top_provider",
        "top_use_case",
    ):
        assert schluessel in block


def test_health_cost_block_reports_the_split_between_known_and_unknown(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from app.ai.health import cost_block

    sink = tmp_path / "llm.jsonl"
    jetzt = datetime.now(UTC).isoformat()
    zeilen = [
        {
            "ts": jetzt,
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "actual_model": "claude-sonnet-4-6",
            "ok": True,
            "chain_position": 0,
            "correlation_id": "a",
            "purpose": "analysis",
            "use_case": "news_intelligence",
            "cost_usd": 0.25,
            "cost_status": "OK",
        },
        {
            "ts": jetzt,
            "provider": "openai",
            "model": "mystery",
            "ok": True,
            "chain_position": 0,
            "correlation_id": "b",
            "purpose": "analysis",
            "use_case": "news_intelligence",
            "cost_usd": None,
            "cost_status": "COST_UNKNOWN",
        },
    ]
    sink.write_text("\n".join(json.dumps(z) for z in zeilen) + "\n", encoding="utf-8")
    reset_spend_cache()

    block = cost_block(path=sink)
    assert block["today_usd_known"] == pytest.approx(0.25)
    assert block["unknown_cost_calls_today"] == 1
    assert block["calls_today"] == 2
    # Die Summe ist eine Untergrenze, und der Block sagt das.
    assert block["fully_accounted_today"] is False
    assert block["top_provider"] == "anthropic"


def test_health_response_model_accepts_the_additive_cost_key() -> None:
    from app.api.routers.health import AICostBlock, AIHealthResponse

    antwort = AIHealthResponse(
        chain={"primary": [], "shadow": [], "source": "x"},  # type: ignore[arg-type]
        window_hours=24.0,
        providers=[],
    )
    # Additiv heisst: ein Leser ohne Kenntnis des Schluessels bleibt gueltig.
    assert antwort.cost is None
    assert AICostBlock(status="OK", price_table_version="v", note="n").status == "OK"


# ── Twitter-Schalter ───────────────────────────────────────────────────────


def test_twitter_ingest_switch_defaults_to_enabled() -> None:
    quelle = Path("scripts/paper_trading_cron.sh").read_text(encoding="utf-8")
    assert 'TWITTER_INGEST_ENABLED="${KAI_TWITTER_INGEST_ENABLED:-true}"' in quelle
    assert "twitter skipped" in quelle
