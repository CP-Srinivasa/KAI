"""Die Reserve muss am ECHTEN Aufruf tragen, nicht nur in der reinen Policy.

``tests/unit/test_ai_budget_pots.py`` prüft die Entscheidung. Diese Datei prüft
den Weg dorthin: dass die Absicht des Aufrufers ankommt, dass beide sperrenden
Stellen dieselbe Antwort geben, und dass der entschiedene Topf in der Zeile
landet. Eine Policy, die richtig rechnet und deren Ergebnis unterwegs verloren
geht, ist genau so wirkungslos wie eine falsche — und schwerer zu finden.

Der Fall, der hier durchgespielt wird, ist der vom 2026-09-10: das Tagesbudget
ist erreicht, der Zufluss läuft weiter. Vor v2 endete das bei **0 von 373**
Alerts. Danach muss ein alert-fähiges Dokument noch durchkommen und ein
gewöhnliches nicht.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ai.audit import budget_intent_scope, budget_pot_scope, record_attempt_trace
from app.ai.budget import BudgetExceeded
from app.ai.models import AttemptTrace
from app.ai.runtime import invoke
from app.ai.spend import reset_spend_cache
from app.core.ai_cost_settings import reset_ai_cost_settings

#: 1,25 − 0,15 − 0,05 = 1,05. Ein Verbrauch von 1,10 USD liegt darüber, aber
#: unter dem Tagesbudget: genau die Lage, in der die Reserve etwas bewirken
#: soll und ein einzelnes Limit nichts mehr unterscheidet.
VERBRAUCHT_USD = 1.10


@pytest.fixture(autouse=True)
def _reserven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_AI_BUDGET_DAILY_USD", "1.25")
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_RESERVE_USD", "0.15")
    monkeypatch.setenv("APP_AI_BUDGET_ALERT_RESERVE_MAX_CALLS", "40")
    monkeypatch.setenv("APP_AI_BUDGET_VALIDATION_RESERVE_USD", "0.05")
    monkeypatch.setenv("APP_AI_BUDGET_VALIDATION_RESERVE_MAX_CALLS", "10")
    reset_ai_cost_settings()
    yield
    reset_ai_cost_settings()


def _verbraucht(sink: Path, *, usd: float, pot: str = "normal") -> None:
    zeile = {
        "ts": datetime.now(UTC).isoformat(),
        "provider": "openai",
        "model": "gpt-4o",
        "actual_model": "gpt-4o",
        "ok": True,
        "chain_position": 0,
        "purpose": "analysis",
        "use_case": "news_intelligence",
        "cost_usd": usd,
        "cost_status": "known",
        "budget_pot": pot,
    }
    with sink.open("a", encoding="utf-8") as f:
        f.write(json.dumps(zeile) + "\n")
    reset_spend_cache()


async def _ruf(sink: Path, *, alert_eligible: bool = False, validation: bool = False) -> str:
    async def direct_call() -> str:
        return "analysiert"

    with budget_intent_scope(alert_eligible=alert_eligible, validation=validation):
        routed = await invoke(
            purpose="analysis",
            direct_call=direct_call,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=None,
            telemetry_path=sink,
        )
    return routed.value


# ---------------------------------------------------------------------------
# Der Produktionsfall.
# ---------------------------------------------------------------------------


async def test_ein_alert_faehiges_dokument_kommt_nach_dem_limit_noch_durch(
    tmp_path: Path,
) -> None:
    """Die Regression zum Befund vom 2026-09-10.

    Vor v2 galt ab dem erreichten Tageslimit für JEDES Dokument dasselbe: kein
    bezahlter Aufruf, Regelpfad, maximal Priorität 6,0 — und damit strukturell
    kein Alert mehr. 0 von 373.
    """
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=VERBRAUCHT_USD)

    assert await _ruf(sink, alert_eligible=True) == "analysiert"


async def test_ein_gewoehnliches_dokument_kommt_nach_dem_limit_nicht_durch(
    tmp_path: Path,
) -> None:
    """Die Gegenprobe, ohne die der Test darüber nur belegen würde, dass die
    Bremse kaputt ist."""
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=VERBRAUCHT_USD)

    with pytest.raises(BudgetExceeded) as fehler:
        await _ruf(sink)

    assert fehler.value.reason == "normal_budget_exhausted"


async def test_ohne_gebundene_absicht_gilt_ein_aufruf_als_gewoehnlich(
    tmp_path: Path,
) -> None:
    """Die Runtime rät nicht.

    Ein Aufrufer, der nichts über seine Arbeit sagt, bekommt die Reserve nicht
    — sonst wäre die Reserve nach dem ersten Aufrufer offen, der den Block
    vergisst, und niemand würde es merken.
    """
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=VERBRAUCHT_USD)

    async def direct_call() -> str:
        return "analysiert"

    with pytest.raises(BudgetExceeded):
        await invoke(
            purpose="analysis",
            direct_call=direct_call,
            direct_provider="openai",
            direct_model="gpt-4o",
            litellm=None,
            telemetry_path=sink,
        )


async def test_die_erschoepfte_reserve_haelt_auch_alert_faehige_dokumente_an(
    tmp_path: Path,
) -> None:
    """Eine Reserve ohne Boden wäre eine Budgeterhöhung mit anderem Namen."""
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=VERBRAUCHT_USD)
    _verbraucht(sink, usd=0.15, pot="alert_reserve")

    with pytest.raises(BudgetExceeded) as fehler:
        await _ruf(sink, alert_eligible=True)

    assert fehler.value.reason == "alert_reserve_exhausted"


async def test_validierung_laeuft_wenn_die_produktion_steht(tmp_path: Path) -> None:
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=VERBRAUCHT_USD)

    assert await _ruf(sink, validation=True) == "analysiert"


async def test_validierung_steht_wenn_ihr_eigener_topf_leer_ist(tmp_path: Path) -> None:
    """Und zwar auch dann, wenn anderswo noch Geld liegt."""
    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=0.05, pot="validation")

    with pytest.raises(BudgetExceeded) as fehler:
        await _ruf(sink, validation=True)

    assert fehler.value.reason == "validation_reserve_exhausted"


# ---------------------------------------------------------------------------
# Ohne gesetzte Reserven bleibt das alte Verhalten Wort für Wort.
# ---------------------------------------------------------------------------


async def test_ohne_reserven_sperrt_das_tageslimit_wie_vor_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Einspielen ohne Konfiguration darf nichts ändern — auch nicht die
    Begründung, auf die Auswertungen und Meldungen sehen."""
    monkeypatch.delenv("APP_AI_BUDGET_ALERT_RESERVE_USD", raising=False)
    monkeypatch.delenv("APP_AI_BUDGET_ALERT_RESERVE_MAX_CALLS", raising=False)
    monkeypatch.delenv("APP_AI_BUDGET_VALIDATION_RESERVE_USD", raising=False)
    monkeypatch.delenv("APP_AI_BUDGET_VALIDATION_RESERVE_MAX_CALLS", raising=False)
    reset_ai_cost_settings()

    sink = tmp_path / "llm.jsonl"
    _verbraucht(sink, usd=1.30)

    with pytest.raises(BudgetExceeded) as fehler:
        await _ruf(sink, alert_eligible=True)

    assert fehler.value.reason == "daily_limit_reached"
    assert fehler.value.state == "LIMIT_REACHED"


# ---------------------------------------------------------------------------
# Der Topf muss in der Zeile stehen.
# ---------------------------------------------------------------------------


def test_der_entschiedene_topf_landet_in_der_telemetriezeile(tmp_path: Path) -> None:
    """Getrennte Zähler ohne dieses Feld wären nicht rekonstruierbar.

    Der Verbrauch eines Topfes lässt sich aus einer Zeile, die ihn nicht nennt,
    hinterher nicht mehr ableiten — und eine Reserve, deren Stand man rät, ist
    keine.
    """
    sink = tmp_path / "llm.jsonl"
    trace = AttemptTrace(
        transport="direct",
        requested_model="gpt-4o",
        latency_ms=1.0,
        actual_provider="openai",
        actual_model="gpt-4o",
    )

    with budget_pot_scope("alert_reserve"):
        record_attempt_trace(
            trace,
            correlation_id="req_1",
            purpose="analysis",
            logical_route="standard",
            mode="primary",
            role="primary",
            attempt_number=1,
            budget_decision="allow",
            circuit_state="closed",
            execution_authority=False,
            schema_status=None,
            outcome="success",
            path=sink,
        )

    zeile = json.loads(sink.read_text(encoding="utf-8").strip())

    assert zeile["budget_pot"] == "alert_reserve"
    assert zeile["schema_version"] == "v8"


def test_ohne_entscheidung_bleibt_der_topf_none_und_wird_nicht_normal(
    tmp_path: Path,
) -> None:
    """``None`` heisst NICHT ZUGEORDNET.

    Ein Schreiber, der ohne Budgetentscheidung ``normal`` behauptet, macht aus
    einer fehlenden Angabe eine falsche — und die Zuordnung von Altzeilen wäre
    dann nicht mehr an EINER Stelle sichtbar, sondern über die Schreiber
    verstreut.
    """
    sink = tmp_path / "llm.jsonl"
    trace = AttemptTrace(
        transport="direct",
        requested_model="gpt-4o",
        latency_ms=1.0,
        actual_provider="openai",
        actual_model="gpt-4o",
    )

    record_attempt_trace(
        trace,
        correlation_id="req_1",
        purpose="analysis",
        logical_route="standard",
        mode="primary",
        role="primary",
        attempt_number=1,
        budget_decision="allow",
        circuit_state="closed",
        execution_authority=False,
        schema_status=None,
        outcome="success",
        path=sink,
    )

    assert json.loads(sink.read_text(encoding="utf-8").strip())["budget_pot"] is None
