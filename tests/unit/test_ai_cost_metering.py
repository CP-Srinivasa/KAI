"""Metering an EINER Stelle: Kosten, Zuordnung, Quelle, Retry-Zählung.

Alles hier prüft ``record_llm_call`` und die Scopes darum — nicht die
Preistabelle (``test_ai_pricing.py``) und nicht die Aggregation
(``test_ai_spend.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.audit import (
    attempt_counter_scope,
    escalation_scope,
    llm_call_scope,
    note_retry_attempt,
    resolve_use_case,
    use_case_scope,
)
from app.ai.pricing import PRICE_TABLE_VERSION
from app.observability.llm_telemetry import record_llm_call


def _rows(sink: Path) -> list[dict]:
    return [json.loads(line) for line in sink.read_text("utf-8").splitlines()]


def test_cost_is_computed_from_tokens_and_price_table(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    record_llm_call(
        provider="openai",
        model="gpt-4o",
        ok=True,
        latency_ms=12.0,
        path=sink,
        input_tokens=1_000_000,
        output_tokens=0,
    )
    row = _rows(sink)[0]
    assert row["cost_usd"] == pytest.approx(2.50)
    assert row["cost_known"] is True
    assert row["cost_status"] == "OK"
    assert row["cost_source"] == f"list_price:{PRICE_TABLE_VERSION}"
    assert row["total_tokens"] == 1_000_000


def test_upstream_cost_is_kept_and_labelled_as_billing(tmp_path: Path) -> None:
    """Eine genannte Abrechnung schlägt jede Schätzung — und sagt, dass sie das ist."""
    sink = tmp_path / "llm.jsonl"
    record_llm_call(
        provider="openai",
        model="gpt-4o",
        ok=True,
        latency_ms=1.0,
        path=sink,
        input_tokens=10,
        output_tokens=10,
        cost_usd=0.42,
    )
    row = _rows(sink)[0]
    assert row["cost_usd"] == pytest.approx(0.42)
    assert row["cost_source"] == "upstream"
    assert row["cost_status"] == "OK"


def test_missing_tokens_yield_cost_unknown_with_reason(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    record_llm_call(provider="openai", model="gpt-4o", ok=False, latency_ms=1.0, path=sink)
    row = _rows(sink)[0]
    assert row["cost_usd"] is None
    assert row["cost_known"] is False
    assert row["cost_status"] == "COST_UNKNOWN"
    assert row["cost_reason"] == "no_tokens"
    assert row["total_tokens"] is None


def test_unknown_model_yields_cost_unknown_with_reason(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    record_llm_call(
        provider="openai",
        model="some-unreleased-model",
        ok=True,
        latency_ms=1.0,
        path=sink,
        input_tokens=100,
        output_tokens=100,
    )
    row = _rows(sink)[0]
    assert row["cost_usd"] is None
    assert row["cost_reason"] == "unknown_model"


def test_actual_model_is_priced_over_the_route_alias(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    record_llm_call(
        provider="openai",
        model="kai-standard",
        ok=True,
        latency_ms=1.0,
        path=sink,
        requested_model_alias="kai-standard",
        actual_model="gpt-4o-mini",
        input_tokens=1_000_000,
        output_tokens=0,
    )
    assert _rows(sink)[0]["cost_usd"] == pytest.approx(0.15)


def test_source_has_its_own_field_and_never_lands_in_provider(tmp_path: Path) -> None:
    """Der Attributionsfehler: der Feedname gehört nicht ins Anbieterfeld."""
    sink = tmp_path / "llm.jsonl"
    record_llm_call(
        provider="anthropic",
        model="claude-sonnet-4-6",
        ok=True,
        latency_ms=1.0,
        path=sink,
        source="CNBC",
        input_tokens=10,
        output_tokens=10,
    )
    row = _rows(sink)[0]
    assert row["provider"] == "anthropic"
    assert row["source"] == "CNBC"


def test_use_case_defaults_to_the_purpose_derivation() -> None:
    assert resolve_use_case("analysis") == "news_intelligence"
    assert resolve_use_case("chat") == "operator_manual"
    assert resolve_use_case("intent") == "operator_manual"
    assert resolve_use_case("stt") == "operator_manual"
    assert resolve_use_case("consensus") == "trading_paper"


def test_unmapped_purpose_is_unknown_not_a_guess() -> None:
    assert resolve_use_case(None) == "unknown"
    assert resolve_use_case("something_new") == "unknown"


def test_use_case_scope_overrides_the_derivation_and_resets() -> None:
    with use_case_scope("premium_signals"):
        assert resolve_use_case("analysis") == "premium_signals"
    assert resolve_use_case("analysis") == "news_intelligence"


async def test_call_scope_writes_use_case_and_escalation(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    with use_case_scope("research"), escalation_scope("critical_override"):
        async with llm_call_scope(
            purpose="analysis", provider="anthropic", model="claude-sonnet-4-6", path=sink
        ) as scope:
            scope.set_tokens(1000, 500)
    row = _rows(sink)[0]
    assert row["use_case"] == "research"
    assert row["escalation_reason"] == "critical_override"
    assert row["cost_known"] is True


async def test_three_attempts_produce_one_row_with_retry_count_two(tmp_path: Path) -> None:
    """Die Lücke L2/W1: drei bezahlte Requests waren als ein Versuch gebucht."""
    sink = tmp_path / "llm.jsonl"
    async with llm_call_scope(
        purpose="analysis", provider="openai", model="gpt-4o", path=sink
    ) as scope:
        # Das tut der Tenacity-`before_sleep`-Hook: zwischen den Versuchen.
        note_retry_attempt()
        note_retry_attempt()
        scope.set_tokens(10, 10)
    rows = _rows(sink)
    assert len(rows) == 1
    assert rows[0]["retry_count"] == 2


async def test_retry_count_is_recorded_on_failure_too(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    with pytest.raises(ValueError):
        async with llm_call_scope(purpose="analysis", provider="openai", model="gpt-4o", path=sink):
            note_retry_attempt()
            note_retry_attempt()
            raise ValueError("boom")
    rows = _rows(sink)
    assert len(rows) == 1
    assert rows[0]["retry_count"] == 2
    assert rows[0]["ok"] is False


def test_retry_hook_outside_a_scope_is_harmless() -> None:
    """Telemetrie darf einen Aufruf nie mitreissen — auch nicht über den Zähler."""
    note_retry_attempt()  # kein laufender Scope
    with attempt_counter_scope() as counter:
        note_retry_attempt()
        assert counter.retries == 1


def test_providers_carry_the_counting_hook() -> None:
    """Ohne den Hook zählt niemand die bezahlten Wiederholungen."""
    import app.integrations.anthropic.provider as anthropic_provider
    import app.integrations.gemini.provider as gemini_provider
    import app.integrations.openai.provider as openai_provider
    import app.integrations.xai.provider as xai_provider

    for modul in (openai_provider, anthropic_provider, gemini_provider, xai_provider):
        quelle = Path(modul.__file__).read_text(encoding="utf-8")
        assert "before_sleep=note_retry_attempt" in quelle, modul.__name__
