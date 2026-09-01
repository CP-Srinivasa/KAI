"""Positivkontrolle und Negativkontrollen des Praereg-Evaluators ``operator_back_edge_v1``.

Ein Evaluator ohne Positivkontrolle ist wertlos: faellt er, weiss niemand, ob
die Hypothese widerlegt oder das Skript kaputt ist (Lehre vom 01.07.). Deshalb
steht hier zuerst der Beweis, dass er MET **erzeugen kann** — und danach die
Kontrollen, dass er es nicht zu billig tut.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.observability.operator_feedback import (
    OPERATOR_ACTION_STREAM,
    new_trigger_id,
    record_operator_action,
    record_trigger_emitted,
)
from app.research.back_edge_evaluator import MIN_ACTED, MIN_EMITTED, evaluate_back_edge

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
WINDOW_END = T0 + timedelta(days=14)


def _emit(adir: Path, n: int, *, start: datetime = T0) -> list[str]:
    triggers: list[str] = []
    for i in range(n):
        trigger = new_trigger_id(seed=f"finding-{i}", now=start + timedelta(hours=i))
        record_trigger_emitted(
            adir / OPERATOR_ACTION_STREAM,
            now=start + timedelta(hours=i),
            trigger_id=trigger,
            channel="telegram",
            finding_count=2,
        )
        triggers.append(trigger)
    return triggers


def _act(adir: Path, trigger: str, *, at: datetime, channel: str = "dashboard") -> None:
    record_operator_action(
        adir / OPERATOR_ACTION_STREAM,
        now=at,
        channel=channel,
        action="open_dashboard",
        trigger_id=trigger,
    )


def _run(adir: Path) -> object:
    return evaluate_back_edge(
        adir, window_start=T0, window_end=WINDOW_END, now=WINDOW_END + timedelta(hours=1)
    )


# ---------------------------------------------------------------------------
# Positivkontrolle: der Evaluator KANN MET erzeugen
# ---------------------------------------------------------------------------


def test_positive_control_met_is_reachable(tmp_path: Path) -> None:
    triggers = _emit(tmp_path, MIN_EMITTED)
    _act(tmp_path, triggers[0], at=T0 + timedelta(minutes=7))
    result = _run(tmp_path)
    assert result.verdict == "MET"
    assert result.acted == MIN_ACTED
    assert result.emitted == MIN_EMITTED
    assert result.median_latency_minutes == 7.0
    assert result.by_channel == (("dashboard", 1),)


def test_the_measured_baseline_produces_not_met(tmp_path: Path) -> None:
    """Die Ausgangslage: Befunde gehen raus, niemand handelt (30 d = 0 Handlungen)."""
    _emit(tmp_path, MIN_EMITTED + 3)
    result = _run(tmp_path)
    assert result.verdict == "NOT_MET"
    assert result.acted == 0
    assert result.action_rate == 0.0
    assert len(result.unanswered) == MIN_EMITTED + 3


# ---------------------------------------------------------------------------
# Negativkontrollen: MET darf nicht zu billig sein
# ---------------------------------------------------------------------------


def test_action_without_trigger_id_does_not_count(tmp_path: Path) -> None:
    _emit(tmp_path, MIN_EMITTED)
    record_operator_action(
        tmp_path / OPERATOR_ACTION_STREAM,
        now=T0 + timedelta(minutes=5),
        channel="dashboard",
        action="open_dashboard",
    )
    assert _run(tmp_path).verdict == "NOT_MET"


def test_action_before_the_finding_does_not_count(tmp_path: Path) -> None:
    triggers = _emit(tmp_path, MIN_EMITTED)
    _act(tmp_path, triggers[2], at=T0)  # Befund 2 ging erst nach 2 h raus
    assert _run(tmp_path).verdict == "NOT_MET"


def test_action_after_the_reaction_window_does_not_count(tmp_path: Path) -> None:
    triggers = _emit(tmp_path, MIN_EMITTED)
    _act(tmp_path, triggers[0], at=T0 + timedelta(hours=30))
    assert _run(tmp_path).verdict == "NOT_MET"


def test_findings_outside_the_window_are_not_population(tmp_path: Path) -> None:
    """Kein Optional Stopping ueber die Hintertuer: was vor T0 lag, zaehlt nicht."""
    old = _emit(tmp_path, 4, start=T0 - timedelta(days=10))
    _act(tmp_path, old[0], at=T0 - timedelta(days=10) + timedelta(minutes=5))
    result = _run(tmp_path)
    assert result.emitted == 0
    assert result.verdict == "INVALID"


# ---------------------------------------------------------------------------
# INVALID ist ein moegliches Ergebnis — eine Messung, die nicht scheitern kann,
# misst nichts
# ---------------------------------------------------------------------------


def test_too_few_findings_is_invalid_not_not_met(tmp_path: Path) -> None:
    triggers = _emit(tmp_path, MIN_EMITTED - 1)
    _act(tmp_path, triggers[0], at=T0 + timedelta(minutes=3))
    result = _run(tmp_path)
    assert result.verdict == "INVALID"
    assert "neu angesetzt" in result.reason


def test_empty_stream_is_invalid(tmp_path: Path) -> None:
    assert _run(tmp_path).verdict == "INVALID"


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------


def test_verdict_is_machine_readable_json(tmp_path: Path) -> None:
    """Verdikte werden ausschliesslich aus --json zitiert, nie aus Fliesstext."""
    triggers = _emit(tmp_path, MIN_EMITTED)
    _act(tmp_path, triggers[1], at=T0 + timedelta(hours=1, minutes=30))
    payload = json.loads(_run(tmp_path).to_json())
    assert payload["prereg"] == "operator_back_edge_v1"
    assert payload["verdict"] == "MET"
    assert payload["window_start_utc"].startswith("2026-09-01")
    assert payload["measured_at_utc"]


def test_request_audit_clicks_count_as_actions(tmp_path: Path) -> None:
    """Der Dashboard-Klick landet im Request-Audit, nicht im Operator-Strom."""
    triggers = _emit(tmp_path, MIN_EMITTED)
    with (tmp_path / "api_request_audit.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp_utc": (T0 + timedelta(minutes=12)).isoformat(),
                    "request_id": "req_x",
                    "method": "GET",
                    "path": "/dashboard/",
                    "status_code": 200,
                    "trigger_id": triggers[0],
                }
            )
            + "\n"
        )
    result = _run(tmp_path)
    assert result.verdict == "MET"
    assert result.by_channel == (("dashboard", 1),)


@pytest.mark.parametrize("bad", ["", "trg_", "nope", "trg_ZZZZZZZZZZZZ"])
def test_malformed_trigger_in_an_action_is_ignored(tmp_path: Path, bad: str) -> None:
    _emit(tmp_path, MIN_EMITTED)
    with (tmp_path / OPERATOR_ACTION_STREAM).open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp_utc": (T0 + timedelta(minutes=5)).isoformat(),
                    "record_type": "operator_action",
                    "channel": "cli",
                    "action": "x",
                    "trigger_id": bad,
                }
            )
            + "\n"
        )
    assert _run(tmp_path).verdict == "NOT_MET"
