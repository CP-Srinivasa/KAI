"""Das Gateway (ADR 0017, Sprint 3): wer entscheidet, wer läuft nur mit.

Die zentrale Zusicherung: in ``off`` und ``shadow`` erreicht ein
LiteLLM-Ergebnis den Aufrufer nicht — auch kein gutes. Erfolg ersetzt keine
Graduation.
"""

from __future__ import annotations

from app.ai.budget import BudgetPolicy, BudgetState
from app.ai.circuit import CircuitBook, CircuitKey, CircuitPolicy
from app.ai.gateway import (
    SKIP_BUDGET_REJECT,
    SKIP_CIRCUIT_OPEN,
    SKIP_MODE_OFF,
    execute,
)
from app.ai.models import AttemptTrace

POLICY = CircuitPolicy(failure_threshold=2, cooldown_s=30.0)


def _trace(transport: str, *, ok: bool = True, provider: str = "openai") -> AttemptTrace:
    return AttemptTrace(
        transport=transport,
        requested_model="fast",
        latency_ms=10.0,
        actual_provider=provider,
        actual_model="m1",
        cost_usd=0.001,
        error_class=None if ok else "server",
    )


def _call(transport: str, *, ok: bool = True, provider: str = "openai", seen: list | None = None):
    def run() -> AttemptTrace:
        if seen is not None:
            seen.append(transport)
        return _trace(transport, ok=ok, provider=provider)

    return run


# --------------------------------------------------------------------------
# Modus entscheidet, nicht der Ausgang.
# --------------------------------------------------------------------------


def test_off_ruft_litellm_gar_nicht_erst() -> None:
    gesehen: list[str] = []
    outcome = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct", seen=gesehen),
        litellm_call=_call("litellm", seen=gesehen),
    )
    assert gesehen == ["direct"]
    assert outcome.litellm is None
    assert SKIP_MODE_OFF in outcome.skipped
    assert outcome.authoritative is outcome.direct


def test_im_schatten_laufen_beide_aber_nur_direct_zaehlt() -> None:
    """Der Kern des Schattenbetriebs — und seine Sicherheitszusage."""
    gesehen: list[str] = []
    outcome = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct", seen=gesehen),
        litellm_call=_call("litellm", seen=gesehen),
        per_route={"standard": "shadow"},
        ceiling="shadow",
    )
    assert sorted(gesehen) == ["direct", "litellm"]
    assert outcome.mode == "shadow"
    assert outcome.litellm is not None and outcome.litellm.ok
    assert not outcome.litellm.execution_authority
    assert outcome.authoritative is outcome.direct
    assert outcome.shadow is outcome.litellm


def test_ein_geglueckter_schatten_wird_nicht_autoritativ() -> None:
    """Auch wenn der direkte Pfad scheitert und LiteLLM trägt."""
    outcome = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct", ok=False),
        litellm_call=_call("litellm", ok=True),
        per_route={"standard": "shadow"},
        ceiling="shadow",
    )
    assert outcome.litellm is not None and outcome.litellm.ok
    assert outcome.authoritative is outcome.direct
    assert outcome.authoritative is not None and not outcome.authoritative.ok


def test_primary_macht_litellm_autoritativ_und_spart_den_direkten_lauf() -> None:
    gesehen: list[str] = []
    outcome = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct", seen=gesehen),
        litellm_call=_call("litellm", seen=gesehen),
        per_route={"standard": "primary"},
        ceiling="primary",
    )
    assert gesehen == ["litellm"]
    assert outcome.authoritative is outcome.litellm
    assert outcome.direct is None
    assert not outcome.fell_back


def test_primary_faellt_kontrolliert_auf_direct_zurueck() -> None:
    """Scheitert der autoritative Transport, traegt der direkte Pfad."""
    gesehen: list[str] = []
    outcome = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct", seen=gesehen),
        litellm_call=_call("litellm", ok=False, seen=gesehen),
        per_route={"standard": "primary"},
        ceiling="primary",
    )
    assert gesehen == ["litellm", "direct"]
    assert outcome.fell_back
    assert outcome.authoritative is outcome.direct
    assert outcome.direct is not None and outcome.direct.fell_back_to_direct


def test_ein_globales_off_haelt_auch_eine_graduierte_route_zurueck() -> None:
    outcome = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct"),
        litellm_call=_call("litellm"),
        per_route={"standard": "primary"},
        ceiling="off",
    )
    assert outcome.mode == "off"
    assert outcome.litellm is None


# --------------------------------------------------------------------------
# Budget und Circuit stehen VOR dem Transport.
# --------------------------------------------------------------------------


def test_ein_abgelehntes_budget_kostet_keinen_aufruf() -> None:
    gesehen: list[str] = []
    outcome = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct", seen=gesehen),
        litellm_call=_call("litellm", seen=gesehen),
        per_route={"standard": "primary"},
        ceiling="primary",
        budget_policy=BudgetPolicy(daily_limit_usd=1.0),
        daily=BudgetState(booked_usd=1.0, known_calls=1, unknown_calls=0),
    )
    assert gesehen == []
    assert outcome.budget == "reject"
    assert outcome.skipped == (SKIP_BUDGET_REJECT,)
    assert outcome.authoritative is None


def test_ein_offener_kreis_verhindert_den_litellm_versuch() -> None:
    gesehen: list[str] = []
    book = CircuitBook()
    grob = CircuitKey("standard", "fast")
    for _ in range(POLICY.failure_threshold):
        book = book.on_failure(grob, now_s=0.0, policy=POLICY)

    outcome = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct", seen=gesehen),
        litellm_call=_call("litellm", seen=gesehen),
        per_route={"standard": "shadow"},
        ceiling="shadow",
        circuit=book,
        circuit_policy=POLICY,
        now_s=1.0,
    )
    assert gesehen == ["direct"], "der gesperrte Pfad darf nicht laufen"
    assert SKIP_CIRCUIT_OPEN in outcome.skipped
    assert outcome.authoritative is outcome.direct


def test_der_kreis_bucht_auf_dem_tatsaechlichen_upstream() -> None:
    """Nicht auf dem Alias — sonst nimmt ein Anbieter die Alternativen mit."""
    outcome = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct"),
        litellm_call=_call("litellm", ok=False, provider="gemini"),
        per_route={"standard": "shadow"},
        ceiling="shadow",
        circuit_policy=POLICY,
    )
    fein = CircuitKey("standard", "fast", "gemini/m1")
    assert outcome.circuit.record_for(fein).consecutive_failures == 1
    assert outcome.circuit.record_for(CircuitKey("standard", "fast")).consecutive_failures == 0


def test_zwei_fehlschlaege_oeffnen_und_der_naechste_lauf_ueberspringt() -> None:
    book = CircuitBook()
    for _ in range(POLICY.failure_threshold):
        out = execute(
            purpose="analysis",
            alias="fast",
            direct_call=_call("direct"),
            litellm_call=_call("litellm", ok=False, provider="gemini"),
            per_route={"standard": "shadow"},
            ceiling="shadow",
            circuit=book,
            circuit_policy=POLICY,
        )
        book = out.circuit

    gesehen: list[str] = []
    danach = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct", seen=gesehen),
        litellm_call=_call("litellm", seen=gesehen, provider="gemini"),
        per_route={"standard": "shadow"},
        ceiling="shadow",
        circuit=book,
        circuit_policy=POLICY,
    )
    assert gesehen == ["litellm", "direct"], "grob offen? nein — nur der feine Schluessel ist zu"
    assert SKIP_CIRCUIT_OPEN in danach.skipped


# --------------------------------------------------------------------------
# Route und Purpose bleiben gekoppelt.
# --------------------------------------------------------------------------


def test_der_purpose_bestimmt_die_route() -> None:
    outcome = execute(purpose="intent", alias="fast", direct_call=_call("direct"))
    assert outcome.route == "critical"
    outcome = execute(purpose="stt", alias="whisper", direct_call=_call("direct"))
    assert outcome.route == "stt"


def test_stt_laeuft_durch_denselben_vertrag() -> None:
    """Kein Sondertransport neben dem Gateway (ADR 0017 § STT)."""
    gesehen: list[str] = []
    outcome = execute(
        purpose="stt",
        alias="whisper",
        direct_call=_call("direct", seen=gesehen),
        litellm_call=_call("litellm", seen=gesehen),
        per_route={"stt": "shadow"},
        ceiling="shadow",
    )
    assert sorted(gesehen) == ["direct", "litellm"]
    assert outcome.route == "stt"
    assert outcome.authoritative is outcome.direct


def test_ohne_direkten_pfad_bleibt_das_ergebnis_leer_statt_falsch() -> None:
    outcome = execute(purpose="analysis", alias="fast", direct_call=None)
    assert outcome.direct is None
    assert outcome.authoritative is None
    assert "no_transport" in outcome.skipped


def test_die_korrelations_id_wandert_in_beide_pfade() -> None:
    outcome = execute(
        purpose="analysis",
        alias="fast",
        direct_call=_call("direct"),
        litellm_call=_call("litellm"),
        per_route={"standard": "shadow"},
        ceiling="shadow",
        correlation_id="abc-123",
    )
    assert outcome.direct is not None and outcome.direct.correlation_id == "abc-123"
    assert outcome.litellm is not None and outcome.litellm.correlation_id == "abc-123"
