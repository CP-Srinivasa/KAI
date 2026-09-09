"""Budget-Zustände und ihre Wirkung: Warnung warnt, Limit sperrt, kritisch läuft.

Die Zustandslogik ist rein (``app.ai.budget.evaluate_status``) und wird hier
ohne Uhr und ohne Datei geprüft. Die Durchsetzung am Aufruf hängt an
``app.ai.runtime.invoke`` und wird über einen echten Strom getestet.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ai.budget import (
    BUDGET_EXEMPT_ROUTES,
    BudgetExceeded,
    BudgetPolicy,
    BudgetState,
    evaluate_status,
)
from app.ai.runtime import invoke, reset_environment_settings
from app.ai.spend import reset_spend_cache
from app.core.ai_cost_settings import reset_ai_cost_settings

_LIMIT = BudgetPolicy(daily_limit_usd=10.0, monthly_limit_usd=100.0)


@pytest.fixture(autouse=True)
def _clean() -> None:
    """Alle drei Zwischenspeicher leeren — sonst traegt ein Test den naechsten."""
    reset_spend_cache()
    reset_ai_cost_settings()
    reset_environment_settings()


# ── Zustandslogik ──────────────────────────────────────────────────────────


def test_below_warn_threshold_is_ok() -> None:
    status = evaluate_status(
        daily=BudgetState(5.0, 10, 0), monthly=BudgetState(5.0, 10, 0), policy=_LIMIT
    )
    assert status.state == "OK"


def test_warning_at_eighty_percent_does_not_block() -> None:
    status = evaluate_status(
        daily=BudgetState(8.0, 10, 0), monthly=BudgetState(8.0, 10, 0), policy=_LIMIT
    )
    assert status.state == "WARNING"
    assert status.reason == "daily_warn_threshold"
    # Der Punkt der Warnung: sie ist der letzte Zustand, in dem noch alles laeuft.
    assert status.blocks_routine is False
    assert status.allows("standard") is True


def test_daily_limit_reached_blocks_routine_but_never_critical() -> None:
    status = evaluate_status(
        daily=BudgetState(10.0, 10, 0), monthly=BudgetState(10.0, 10, 0), policy=_LIMIT
    )
    assert status.state == "LIMIT_REACHED"
    assert status.blocks_routine is True
    assert status.allows("standard") is False
    assert status.allows("bulk") is False
    assert status.allows("critical") is True


def test_monthly_limit_reached_blocks_even_with_a_quiet_day() -> None:
    status = evaluate_status(
        daily=BudgetState(0.1, 1, 0), monthly=BudgetState(100.0, 500, 0), policy=_LIMIT
    )
    assert status.state == "LIMIT_REACHED"
    assert status.reason == "monthly_limit_reached"


def test_too_many_unknown_calls_fail_closed_as_cost_unknown() -> None:
    """Wer nicht weiss, was er ausgibt, hat kein gedecktes Budget."""
    status = evaluate_status(
        daily=BudgetState(0.0, 0, 51),
        monthly=BudgetState(0.0, 0, 51),
        policy=_LIMIT,
        unknown_max_calls_per_day=50,
    )
    assert status.state == "COST_UNKNOWN"
    assert status.blocks_routine is True
    assert status.allows("critical") is True
    assert "unknown_cost_calls_today=51" in status.reason


def test_unknown_volume_without_any_limit_is_visible_but_never_blocks() -> None:
    """Ohne Budget gibt es keine Deckung, die fehlen koennte.

    Regression: der fail-closed Zweig sperrte urspruenglich auch ohne gesetztes
    Limit — ein Betrieb, der nie ein Budget konfiguriert hat, waere ab dem 51.
    unbelegten Aufruf eines Tages stehen geblieben. Aus der Voreinstellung
    heraus, ausgeloest von einem Messproblem statt von Kosten. Gefangen von
    ``tests/unit/test_ai_call_scopes.py::test_voice_transcriber_records_stt_call``.
    """
    status = evaluate_status(
        daily=BudgetState(0.0, 0, 999),
        monthly=BudgetState(0.0, 0, 999),
        policy=BudgetPolicy(),
        unknown_max_calls_per_day=50,
    )
    # Sichtbar bleibt es -- Sichtbarkeit braucht keine Erlaubnis.
    assert status.state == "COST_UNKNOWN"
    assert status.limits_configured is False
    # Gesperrt wird nichts.
    assert status.blocks_routine is False
    assert status.allows("standard") is True


def test_unknown_volume_below_the_ceiling_still_runs() -> None:
    status = evaluate_status(
        daily=BudgetState(0.0, 0, 50),
        monthly=BudgetState(0.0, 0, 50),
        policy=BudgetPolicy(),
        unknown_max_calls_per_day=50,
    )
    assert status.state == "OK"


def test_proven_limit_beats_unknown_volume_in_the_reason() -> None:
    """Ein Beleg schlägt eine Vermutung — auch in der Begründung."""
    status = evaluate_status(
        daily=BudgetState(10.0, 5, 99), monthly=BudgetState(10.0, 5, 99), policy=_LIMIT
    )
    assert status.state == "LIMIT_REACHED"


def test_without_limits_nothing_warns_and_nothing_blocks() -> None:
    status = evaluate_status(
        daily=BudgetState(9_999.0, 5_000, 0),
        monthly=BudgetState(9_999.0, 5_000, 0),
        policy=BudgetPolicy(),
    )
    assert status.state == "OK"
    assert status.blocks_routine is False


def test_critical_is_the_only_exempt_route_and_not_configurable() -> None:
    assert BUDGET_EXEMPT_ROUTES == frozenset({"critical"})


# ── Durchsetzung am echten Aufruf ──────────────────────────────────────────


def _teures_fenster(sink: Path, *, usd: float) -> None:
    zeile = {
        "ts": datetime.now(UTC).isoformat(),
        "provider": "openai",
        "model": "gpt-4o",
        "actual_model": "gpt-4o",
        "ok": True,
        "chain_position": 0,
        "correlation_id": "spent",
        "purpose": "analysis",
        "use_case": "news_intelligence",
        "input_tokens": 10,
        "output_tokens": 10,
        "cost_usd": usd,
    }
    sink.write_text(json.dumps(zeile) + "\n", encoding="utf-8")
    reset_spend_cache()


async def _ruf(purpose: str, sink: Path) -> str:
    async def direct_call() -> str:
        return "geliefert"

    routed = await invoke(
        purpose=purpose,  # type: ignore[arg-type]
        direct_call=direct_call,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=None,
        telemetry_path=sink,
    )
    return routed.value


async def test_hard_limit_stops_the_paid_direct_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der bewusste Verhaltenswechsel: das Budget erreicht den Pfad, der Geld kostet.

    Bis D-CORE-007 galt "das Budget regiert die LiteLLM-Ausgabe, nicht den
    Altpfad" — und weil LiteLLM aus ist, hätte ein erschöpftes Budget genau
    nichts angehalten.
    """
    sink = tmp_path / "llm.jsonl"
    _teures_fenster(sink, usd=5.0)
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "1.0")
    reset_ai_cost_settings()

    with pytest.raises(BudgetExceeded) as fehler:
        await _ruf("analysis", sink)
    assert fehler.value.route == "standard"
    assert fehler.value.state == "LIMIT_REACHED"


async def test_critical_intent_still_runs_when_the_budget_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Operator-Fernbedienung bleibt bedienbar — mit sichtbarer Eskalation."""
    sink = tmp_path / "llm.jsonl"
    _teures_fenster(sink, usd=5.0)
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "1.0")
    reset_ai_cost_settings()

    assert await _ruf("intent", sink) == "geliefert"


async def test_unknown_cost_volume_blocks_routine_at_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = tmp_path / "llm.jsonl"
    zeilen = [
        {
            "ts": datetime.now(UTC).isoformat(),
            "provider": "openai",
            "model": "mystery-model",
            "ok": True,
            "chain_position": 0,
            "correlation_id": f"c{i}",
            "purpose": "analysis",
            "cost_usd": None,
            # Die Messung LIEF und fand keinen Preis. Ohne dieses Feld waere
            # die Zeile eine Altzeile und zaehlte bewusst nicht mit
            # (app/ai/spend.py::is_unmetered_legacy_row).
            "cost_status": "COST_UNKNOWN",
        }
        for i in range(3)
    ]
    sink.write_text(
        "\n".join(json.dumps(z) for z in zeilen) + "\n",
        encoding="utf-8",
    )
    reset_spend_cache()
    monkeypatch.setenv("APP_AI_BUDGET_UNKNOWN_MAX_CALLS_PER_DAY", "2")
    # Fail-closed setzt ein Budget voraus, auf das es schliessen kann.
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "50.0")
    reset_ai_cost_settings()

    with pytest.raises(BudgetExceeded) as fehler:
        await _ruf("analysis", sink)
    assert fehler.value.state == "COST_UNKNOWN"
    # Kritisch bleibt auch hier frei.
    assert await _ruf("intent", sink) == "geliefert"


async def test_without_limits_the_call_runs_exactly_as_before(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _teures_fenster(sink, usd=9_999.0)
    reset_ai_cost_settings()
    assert await _ruf("analysis", sink) == "geliefert"


async def test_critical_override_is_visible_in_the_telemetry_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Ausnahme darf nicht gratis sein — sie muss in der Zeile stehen.

    Sonst liesse sich hinterher nicht sagen, welcher Teil der Rechnung
    entstanden ist, NACHDEM das Budget schon erschoepft war.
    """
    from app.ai.audit import llm_call_scope

    sink = tmp_path / "llm.jsonl"
    _teures_fenster(sink, usd=5.0)
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "1.0")
    reset_ai_cost_settings()

    async def direct_call() -> str:
        async with llm_call_scope(
            purpose="intent", provider="openai", model="gpt-4o", path=sink
        ) as scope:
            scope.set_tokens(50, 10)
        return "geliefert"

    await invoke(
        purpose="intent",
        direct_call=direct_call,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=None,
        telemetry_path=sink,
    )
    zeilen = [json.loads(z) for z in sink.read_text("utf-8").splitlines()]
    letzte = zeilen[-1]
    assert letzte["purpose"] == "intent"
    assert letzte["escalation_reason"] == "critical_override"
    assert letzte["use_case"] == "operator_manual"


async def test_routine_below_the_limit_carries_no_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ai.audit import llm_call_scope

    sink = tmp_path / "llm.jsonl"
    _teures_fenster(sink, usd=0.01)
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "100.0")
    reset_ai_cost_settings()

    async def direct_call() -> str:
        async with llm_call_scope(
            purpose="analysis", provider="openai", model="gpt-4o", path=sink
        ) as scope:
            scope.set_tokens(50, 10)
        return "geliefert"

    await invoke(
        purpose="analysis",
        direct_call=direct_call,
        direct_provider="openai",
        direct_model="gpt-4o",
        litellm=None,
        telemetry_path=sink,
    )
    letzte = [json.loads(z) for z in sink.read_text("utf-8").splitlines()][-1]
    assert letzte["escalation_reason"] == ""


def test_only_genuinely_costlier_routes_count_as_escalation() -> None:
    """Ein Feld, das immer gesetzt ist, sagt nichts mehr."""
    from app.ai.routes import escalation_reason_for

    assert escalation_reason_for("standard") == ""
    assert escalation_reason_for("bulk") == ""
    # stt ist eine andere Modalitaet, keine hoehere Stufe.
    assert escalation_reason_for("stt") == ""
    assert escalation_reason_for("reasoning") == "route_reasoning"
    assert escalation_reason_for("critical") == "route_critical"
    # Der Budget-Bypass schlaegt jede Routen-Einordnung.
    assert escalation_reason_for("critical", budget_bypassed=True) == "critical_override"
