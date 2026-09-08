"""Aggregation über den Telemetriestrom: keine Doppelzählung, keine Null-Summe."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.ai.budget import BudgetPolicy, BudgetState, evaluate_status
from app.ai.spend import (
    load_rows,
    reset_spend_cache,
    spend_window,
    window_bounds,
)


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    reset_spend_cache()


def _write(sink: Path, rows: list[dict]) -> None:
    sink.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8"
    )
    reset_spend_cache()


def _row(**kwargs: object) -> dict:
    basis = {
        "ts": datetime.now(UTC).isoformat(),
        "provider": "openai",
        "model": "gpt-4o",
        "actual_model": "gpt-4o",
        "ok": True,
        "chain_position": 0,
        "correlation_id": "c1",
        "purpose": "analysis",
        "use_case": "news_intelligence",
        "input_tokens": 1000,
        "output_tokens": 100,
        "cost_usd": 0.0035,
    }
    basis.update(kwargs)
    return basis


def test_empty_or_missing_stream_is_a_zero_state_not_a_crash(tmp_path: Path) -> None:
    fenster = spend_window("today", path=tmp_path / "does_not_exist.jsonl")
    assert fenster.calls == 0
    assert fenster.known_cost_usd == 0.0
    assert fenster.unknown_calls == 0
    # n=0 heisst NICHT "vollstaendig belegt".
    assert fenster.fully_accounted is False


def test_broken_lines_do_not_stop_the_reader(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    sink.write_text(
        json.dumps(_row()) + "\n{ kaputt \n" + json.dumps(_row(correlation_id="c2")) + "\n",
        encoding="utf-8",
    )
    reset_spend_cache()
    assert spend_window("today", path=sink).calls == 2


def test_outer_row_is_dropped_when_attempt_rows_exist(tmp_path: Path) -> None:
    """Die kanonische Zählebene: sonst zählt jede Ensemble-Analyse doppelt."""
    sink = tmp_path / "llm.jsonl"
    _write(
        sink,
        [
            _row(chain_position=-1, correlation_id="doc-1", cost_usd=0.01),
            _row(chain_position=0, correlation_id="doc-1", cost_usd=0.01),
        ],
    )
    fenster = spend_window("today", path=sink)
    assert fenster.calls == 1
    assert fenster.known_cost_usd == pytest.approx(0.01)


def test_outer_row_survives_without_attempt_counterpart(tmp_path: Path) -> None:
    """Ohne Ensemble gibt es nur die äussere Zeile — sie darf nicht verschwinden."""
    sink = tmp_path / "llm.jsonl"
    _write(sink, [_row(chain_position=-1, correlation_id="doc-2", cost_usd=0.02)])
    assert spend_window("today", path=sink).calls == 1


def test_source_names_in_the_provider_field_are_filtered_out(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _write(
        sink,
        [
            _row(),
            _row(provider="CNBC", correlation_id="c2", actual_provider="CNBC", purpose="rss"),
        ],
    )
    assert spend_window("today", path=sink).calls == 1


def test_unknown_cost_is_counted_and_never_summed_as_zero(tmp_path: Path) -> None:
    """UNKNOWN != 0: die Summe ist eine Untergrenze und sagt das über ``unknown_calls``."""
    sink = tmp_path / "llm.jsonl"
    _write(
        sink,
        [
            _row(cost_usd=0.10, correlation_id="c1"),
            _row(cost_usd=None, correlation_id="c2"),
            _row(cost_usd=None, correlation_id="c3"),
        ],
    )
    fenster = spend_window("today", path=sink)
    assert fenster.calls == 3
    assert fenster.known_cost_usd == pytest.approx(0.10)
    assert fenster.unknown_calls == 2
    assert fenster.known_calls == 1
    assert fenster.fully_accounted is False
    zustand = fenster.budget_state()
    assert zustand.unknown_calls == 2
    assert zustand.booked_usd == pytest.approx(0.10)


def test_breakdown_by_provider_model_and_use_case(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _write(
        sink,
        [
            _row(correlation_id="c1", cost_usd=0.10),
            _row(
                correlation_id="c2",
                provider="anthropic",
                model="claude-sonnet-4-6",
                actual_model="claude-sonnet-4-6",
                use_case="research",
                cost_usd=0.30,
            ),
        ],
    )
    fenster = spend_window("today", path=sink)
    assert fenster.by_provider["anthropic"].known_cost_usd == pytest.approx(0.30)
    assert fenster.by_model["gpt-4o"].calls == 1
    assert fenster.by_use_case["research"].known_cost_usd == pytest.approx(0.30)
    assert fenster.top_provider == "anthropic"
    assert fenster.top_use_case == "research"
    assert fenster.total_tokens == 2200


def test_top_entries_are_none_without_any_proven_cost(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _write(sink, [_row(cost_usd=None)])
    fenster = spend_window("today", path=sink)
    assert fenster.top_provider is None
    assert fenster.top_use_case is None


def test_today_window_starts_at_utc_midnight_not_24h_ago(tmp_path: Path) -> None:
    jetzt = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    since, until = window_bounds("today", jetzt)
    assert since == datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
    assert until == jetzt

    sink = tmp_path / "llm.jsonl"
    _write(
        sink,
        [
            _row(ts=(jetzt - timedelta(hours=2)).isoformat(), correlation_id="heute"),
            _row(ts=(jetzt - timedelta(hours=20)).isoformat(), correlation_id="gestern"),
        ],
    )
    assert spend_window("today", path=sink, now=jetzt).calls == 1
    assert spend_window("month", path=sink, now=jetzt).calls == 2


def test_month_window_starts_at_the_first_of_the_month(tmp_path: Path) -> None:
    jetzt = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    since, _ = window_bounds("month", jetzt)
    assert since == datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    sink = tmp_path / "llm.jsonl"
    _write(sink, [_row(ts=datetime(2026, 8, 31, 23, 0, tzinfo=UTC).isoformat())])
    assert spend_window("month", path=sink, now=jetzt).calls == 0


def test_mtime_cache_is_invalidated_when_a_line_is_appended(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _write(sink, [_row(correlation_id="c1")])
    assert len(load_rows(sink)) == 1
    with sink.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_row(correlation_id="c2")) + "\n")
    assert len(load_rows(sink)) == 2


def test_status_is_computed_even_without_limits() -> None:
    """Sichtbarkeit braucht keine Erlaubnis; Sperren schon."""
    status = evaluate_status(
        daily=BudgetState(5.0, 10, 0),
        monthly=BudgetState(5.0, 10, 0),
        policy=BudgetPolicy(),
    )
    assert status.state == "OK"
    assert status.blocks_routine is False
