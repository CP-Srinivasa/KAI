"""Productive async entry point for KAI callers.

Callers describe their existing direct operation and, optionally, how to parse
an OpenAI-compatible LiteLLM response. Mode, retry, circuit, budget and
authority remain inside ``app.ai``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Any, Final

import httpx

from app.ai.audit import (
    Purpose,
    classify_error,
    correlation_scope,
    escalation_scope,
    evaluation_scope,
)
from app.ai.budget import (
    BUDGET_EXEMPT_ROUTES,
    BudgetExceeded,
    BudgetPolicy,
    BudgetState,
    BudgetStatus,
)
from app.ai.config import InferenceSettings
from app.ai.gateway import AsyncGatewayOutcome, execute_async
from app.ai.models import AttemptResult, AttemptTrace
from app.ai.modes import resolve_mode, unknown_route_keys
from app.ai.retry import RetryPolicy
from app.ai.routes import escalation_reason_for, route_for
from app.core.logging import get_logger
from app.integrations.litellm.provider import LiteLLMConfig, call_litellm_async

logger = get_logger(__name__)


class LiteLLMCallError(RuntimeError):
    """A typed LiteLLM result was unavailable and no direct fallback existed."""


@dataclass(frozen=True)
class LiteLLMRequest[T]:
    """Transport input plus caller-owned response interpretation."""

    parser: Callable[[dict[str, Any]], T]
    payload: dict[str, Any] | None = None
    endpoint: str = "/v1/chat/completions"
    files: Any = None
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class RoutedValue[T]:
    value: T
    transport: str
    outcome: AsyncGatewayOutcome[T] | None = None


#: Hart abgeschaltete Rueckfallebene. Wird benutzt, wenn die Umgebung eine
#: unbrauchbare ``KAI_INFERENCE_*``-Variable enthaelt: ein Tippfehler in einer
#: Env-Variable darf den Direktpfad von Chat, Intent, STT und Consensus nicht
#: mitreissen. OFF ist der Rollback, nicht ein Fehlerzustand.
_HARD_OFF: Final = InferenceSettings.model_construct(
    enabled=False,
    mode_ceiling="off",
    route_modes={},
    route_aliases={},
    litellm_base_url="http://127.0.0.1:4000",
    litellm_api_key="",
    timeout_seconds=30.0,
    max_attempts=1,
    backoff_base_seconds=0.0,
    backoff_max_seconds=0.0,
    jitter_max_seconds=0.0,
)


@lru_cache(maxsize=1)
def environment_settings() -> InferenceSettings:
    """Die Umgebungsfassung — EINMAL gelesen, nicht pro Aufruf.

    ``BaseSettings()`` liest ``.env`` von der Platte. Das pro Chat-, Intent- und
    STT-Aufruf zu tun, waere blockierendes Datei-I/O im Event-Loop — genau die
    Klasse Fehler, gegen die Luecke B antritt. Zwischenspeicher statt Neubau;
    :func:`reset_environment_settings` macht ihn fuer Tests wieder auf.
    """
    try:
        return InferenceSettings()
    except Exception as exc:  # noqa: BLE001 - eine kaputte Env darf nicht werfen
        logger.error("ai_gateway_settings_invalid_falling_back_to_off", error=str(exc))
        return _HARD_OFF


def reset_environment_settings() -> None:
    """Zwischenspeicher leeren (Tests, Neustart nach Env-Wechsel)."""
    environment_settings.cache_clear()


def inference_settings(source: Any | None = None) -> InferenceSettings:
    """Resolve settings without making legacy caller test doubles grow fields."""
    if isinstance(source, InferenceSettings):
        return source
    candidate = getattr(source, "ai_gateway", None) if source is not None else None
    if isinstance(candidate, InferenceSettings):
        return candidate
    return environment_settings()


def _transportfehler(exc: Exception, *, alias: str, woher: str) -> AttemptResult[Any]:
    """Eine geworfene Ausnahme des Transports als SPUR statt als Abbruch.

    Ein Fehler ohne Zeile ist ein Fehler, den niemand zaehlt -- und im Schatten
    zusaetzlich einer, der den Betrieb mitreisst, obwohl er ihn nicht einmal
    beeinflussen darf.
    """
    return AttemptResult(
        trace=AttemptTrace(
            transport="litellm",
            requested_model=alias,
            latency_ms=0.0,
            error_class=classify_error(exc),
            detail={"exception": type(exc).__name__, "raised": woher},
        ),
        error=exc,
    )


def _direct_trace(
    provider: str, model: str, latency_ms: float, exc: Exception | None
) -> AttemptTrace:
    return AttemptTrace(
        transport="direct",
        requested_model=model,
        latency_ms=latency_ms,
        actual_provider=provider if exc is None else "",
        actual_model=model if exc is None else "",
        error_class=classify_error(exc) if exc is not None else None,
        detail={"exception": type(exc).__name__} if exc is not None else {},
    )


def _budget_lage(telemetry_path: Path | None) -> BudgetStatus:
    """Der Budgetzustand vor diesem Aufruf — fail-soft, nie eine Ausnahme.

    Fehlschlaege beim Lesen des Stroms oder der Konfiguration duerfen den
    Aufruf nicht sperren: eine Kostenbremse, die aus einem Lesefehler heraus
    zuschlaegt, ist ein Ausfall mit Kostenbegruendung. Der Rueckfall ist der
    unbegrenzte Zustand — also das Verhalten von vor D-CORE-007.
    """
    try:
        from app.ai.spend import current_budget_status

        status, _heute, _monat = current_budget_status(path=telemetry_path)
    except Exception as exc:  # noqa: BLE001 - siehe Docstring
        logger.warning("ai_budget_state_unavailable", error=str(exc))
        leer = BudgetState(0.0, 0, 0)
        return BudgetStatus(state="OK", daily=leer, monthly=leer, policy=BudgetPolicy())
    return status


def _eskalation(route: str, lage: BudgetStatus) -> str:
    """Der Eskalationsgrund dieses Aufrufs — leer heisst: keine Eskalation."""
    return escalation_reason_for(
        route, budget_bypassed=lage.blocks_routine and route in BUDGET_EXEMPT_ROUTES
    )


def _budget_gate(route: str, lage: BudgetStatus) -> None:
    """Sperrt Routinearbeit bei erreichtem Limit — ``critical`` nie.

    Eine typisierte Ausnahme, kein leeres Ergebnis: der Aufrufer soll das
    Dokument VERSCHIEBEN oder die Antwort ERSETZEN und das vermerken. Ein
    stilles ``None`` waere von einem Anbieterausfall nicht zu unterscheiden.
    """
    if lage.allows(route):
        return
    logger.warning(
        "ai_budget_blocked_call",
        route=route,
        state=lage.state,
        reason=lage.reason,
        booked_usd_today=round(lage.daily.booked_usd, 4),
        unknown_calls_today=lage.daily.unknown_calls,
    )
    raise BudgetExceeded(route=route, state=lage.state, reason=lage.reason)


def _mit_denkbudget(
    payload: dict[str, Any] | None, configured: InferenceSettings, route: str
) -> dict[str, Any] | None:
    """Das Denkbudget der Route in die Nutzlast legen -- oder nichts tun.

    Der teuerste Posten eines Aufrufs ist bei denkenden Modellen nicht die
    Antwort, sondern der Weg dorthin: am 2026-09-08 gingen 96 Prozent der Kosten
    eines `gemini/gemini-2.5-flash`-Aufrufs auf `reasoning`. Das ist ein Regler,
    und ohne Eintrag wird er nicht angefasst -- der Aufrufer bestimmt seine
    Nutzlast, diese Funktion ergaenzt nur, was die Route vorgibt.

    `0` ist ein GUELTIGER Wert. Eine Pruefung auf Wahrheitswert statt auf `None`
    haette ausgerechnet die Einstellung verschluckt, die auf `gemini-2.5-flash`
    den Faktor 9 brachte.

    ACHTUNG: `thinking` ist die Anthropic-Schreibweise. LiteLLM 1.99.0
    uebersetzt sie fuer Gemini NICHT -- auf `gemini/gemini-3.6-flash` bleibt
    dieser Regler wirkungslos (2026-09-09 auf kai-pi5 gemessen: 867 statt 876
    Denk-Token bei Budget 0). Fuer diesen Weg ist `_mit_denkaufwand`
    zustaendig. Der Regler hier bleibt fuer Transporte, die `thinking` nativ
    tragen.

    Eine bereits gesetzte Angabe des Aufrufers bleibt stehen. Er weiss mehr
    ueber seinen Fall als eine Routen-Vorgabe, und ein stilles Ueberschreiben
    waere eine zweite Autoritaet ueber dieselbe Zahl.
    """
    budget = configured.route_reasoning_budget.get(route)
    if budget is None:
        return payload
    if payload is not None and "thinking" in payload:
        return payload
    ergaenzt = dict(payload or {})
    ergaenzt["thinking"] = {"type": "enabled", "budget_tokens": max(0, budget)}
    return ergaenzt


def _mit_denkaufwand(
    payload: dict[str, Any] | None, configured: InferenceSettings, route: str
) -> dict[str, Any] | None:
    """Die Denkstufe der Route in die Nutzlast legen -- oder nichts tun.

    Derselbe Regler wie :func:`_mit_denkbudget`, anderer Dialekt.
    `reasoning_effort` ist die Form, die LiteLLM fuer Gemini uebersetzt; am
    2026-09-09 auf kai-pi5 ueber den laufenden Proxy gemessen:

        ohne Parameter             876 Denk-Token, 8353 ms
        thinking.budget_tokens=0   867 Denk-Token, 6526 ms  <- wirkungslos
        reasoning_effort="low"     499 Denk-Token, 6366 ms
        reasoning_effort="minimal"   0 Denk-Token, 1301 ms

    Ohne Eintrag wird nichts angefasst, und eine bereits gesetzte Angabe des
    Aufrufers bleibt stehen: er weiss mehr ueber seinen Fall als eine
    Routen-Vorgabe, und ein stilles Ueberschreiben waere eine zweite Autoritaet
    ueber dieselbe Entscheidung.
    """
    stufe = configured.route_reasoning_effort.get(route)
    if stufe is None:
        return payload
    if payload is not None and "reasoning_effort" in payload:
        return payload
    ergaenzt = dict(payload or {})
    ergaenzt["reasoning_effort"] = stufe
    return ergaenzt


async def invoke[T](
    *,
    purpose: Purpose,
    direct_call: Callable[[], Awaitable[T]],
    direct_provider: str,
    direct_model: str,
    litellm: LiteLLMRequest[T] | None,
    settings: InferenceSettings | Any | None = None,
    correlation_id: str | None = None,
    telemetry_path: Path | None = None,
    client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    clock: Callable[[], float] = monotonic,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
    jitter: Callable[[], float] = lambda: 0.0,
) -> RoutedValue[T]:
    """Run one logical call; OFF is an exact direct-path fast return."""
    configured = inference_settings(settings)
    route = route_for(purpose)
    invalid_routes = unknown_route_keys(configured.route_modes)
    if invalid_routes:
        logger.warning("ai_gateway_unknown_route_keys", route_keys=invalid_routes)

    ceiling = configured.mode_ceiling if configured.enabled else "off"
    mode = resolve_mode(route, per_route=configured.route_modes, ceiling=ceiling)
    if purpose == "consensus" and mode == "primary":
        mode = "shadow"

    lage = _budget_lage(telemetry_path)

    # This branch deliberately adds no network client, task or retry around the
    # legacy path. It is the hard rollback invariant, not merely a mode label.
    if mode == "off":
        # HIER liegt heute das Geld. OFF ist im Betrieb der Normalfall
        # (`KAI_INFERENCE_ENABLED=false`), und dieser Zweig kehrt zurueck, ohne
        # das Gateway je zu betreten. Ein Budget, das nur im Gateway greift,
        # waere in genau dem Modus wirkungslos, in dem KAI laeuft -- also
        # ueberall. Die Reihenfolge (erst Budget, dann Aufruf) ist dieselbe wie
        # im Gateway; die Ausnahme ist dieselbe; `critical` bleibt dieselbe
        # Ausnahme von der Ausnahme.
        #
        # Was dieser Zweig NICHT tut: einen Client bauen, eine Task starten,
        # einen Retry legen. Die harte Rollback-Zusage bleibt unberuehrt.
        _budget_gate(route, lage)
        with correlation_scope(correlation_id) as _, escalation_scope(_eskalation(route, lage)):
            return RoutedValue(value=await direct_call(), transport="direct")

    with (
        correlation_scope(correlation_id) as active_correlation,
        # Ab hier gehoert alles Telemetrierte zu EINER Auswertung -- auch die
        # Zeile, die der Altpfad ueber `llm_call_scope` selbst schreibt. Ohne
        # das traegt sie weder Route noch Zuordnung, und die Auswertung findet
        # spaeter eine SHADOW-Seite ohne Gegenstueck.
        evaluation_scope(logical_route=route, mode=mode) as active_evaluation,
    ):

        async def run_direct() -> AttemptResult[T]:
            started = clock()
            try:
                value = await direct_call()
            except Exception as exc:  # preserve and re-raise after policy selection
                return AttemptResult(
                    trace=_direct_trace(
                        direct_provider,
                        direct_model,
                        (clock() - started) * 1000.0,
                        exc,
                    ),
                    error=exc,
                )
            return AttemptResult(
                trace=_direct_trace(
                    direct_provider,
                    direct_model,
                    (clock() - started) * 1000.0,
                    None,
                ),
                value=value,
            )

        async def boundary_failure() -> AttemptResult[T]:
            exc = ValueError("LiteLLM base_url is outside the localhost boundary")
            return AttemptResult(
                trace=AttemptTrace(
                    transport="litellm",
                    requested_model=configured.route_aliases.get(route, route),
                    latency_ms=0.0,
                    error_class="schema",
                    detail={"exception": type(exc).__name__, "boundary": "non_local"},
                ),
                error=exc,
            )

        async def unavailable() -> AttemptResult[T]:
            exc = LiteLLMCallError("LiteLLM request is not defined for this caller")
            return AttemptResult(
                trace=AttemptTrace(
                    transport="litellm",
                    requested_model=configured.route_aliases.get(route, route),
                    latency_ms=0.0,
                    error_class="schema",
                    detail={"exception": type(exc).__name__},
                ),
                error=exc,
            )

        lite_config = LiteLLMConfig(
            base_url=configured.litellm_base_url,
            timeout_s=configured.timeout_seconds,
            api_key=configured.litellm_api_key,
        )
        # Auch der Client-AUFBAU gehoert in den Schatten. Wuerde er hier
        # werfen, kaeme `execute_async` nie zum Zug -- und damit auch der
        # Altpfad nicht, der noch gar nicht gelaufen ist. Der Aufruf schluege
        # fehl, weil ein HTTP-Client nicht entstehen konnte, den er fuer die
        # Antwort ueberhaupt nicht braucht. Der Stack deckt Konstruktion UND
        # Eintritt ab; beides ist Transport, nicht Politik.
        async with AsyncExitStack() as stack:
            client: httpx.AsyncClient | None = None
            aufbau_fehler: Exception | None = None
            try:
                client = await stack.enter_async_context(
                    client_factory(timeout=configured.timeout_seconds)
                )
            except Exception as exc:  # noqa: BLE001 - siehe oben
                aufbau_fehler = exc

            async def run_litellm() -> AttemptResult[T]:
                if aufbau_fehler is not None:
                    return _transportfehler(
                        aufbau_fehler,
                        alias=configured.route_aliases.get(route, route),
                        woher="client_factory",
                    )
                try:
                    return await _run_litellm_unsafe()
                except Exception as exc:  # noqa: BLE001 -- siehe unten
                    # SHADOW heisst: der Transport laeuft MIT, er entscheidet
                    # nichts. Eine Ausnahme, die hier durchginge, wuerde den
                    # gesamten Aufruf sprengen -- also auch den Altpfad, der
                    # gerade nebenher laeuft und die eigentliche Antwort
                    # traegt. Der Schattenpfad haette dann maximalen Einfluss
                    # statt gar keinem.
                    #
                    # `call_litellm_async` SAGT ZU, nicht zu werfen. Eine
                    # Zusage ist aber kein Zwang: der Client-Aufbau liegt
                    # ausserhalb, und ein spaeterer Transport koennte sich
                    # anders verhalten. Hier wird der Vertrag erzwungen statt
                    # geglaubt -- der Fehler wird zu einer Spur, wie jeder
                    # andere Fehlversuch auch, und bleibt damit zaehlbar.
                    return _transportfehler(
                        exc,
                        alias=configured.route_aliases.get(route, route),
                        woher="transport",
                    )

            async def _run_litellm_unsafe() -> AttemptResult[T]:
                if litellm is None:
                    return await unavailable()
                if not lite_config.is_local:
                    return await boundary_failure()
                assert client is not None  # nur erreichbar, wenn der Aufbau gelang
                response = await call_litellm_async(
                    config=lite_config,
                    model=configured.route_aliases.get(route, route),
                    client=client,
                    monotonic=clock,
                    correlation_id=active_correlation,
                    endpoint=litellm.endpoint,
                    payload=_mit_denkaufwand(
                        _mit_denkbudget(litellm.payload, configured, route),
                        configured,
                        route,
                    ),
                    files=litellm.files,
                    data=litellm.data,
                )
                if not response.trace.ok:
                    return AttemptResult(
                        trace=response.trace,
                        error=LiteLLMCallError(
                            f"LiteLLM attempt failed: {response.trace.error_class}"
                        ),
                    )
                # NACH dem `not ok`-Zweig und VOR dem Parser: ein abgeschnittenes
                # Ergebnis ist kein Formfehler des Modells, sondern ein zu
                # kleines Budget. Beides endet ohne Analyse, aber nur eines
                # davon behebt der Operator an der richtigen Stelle -- und der
                # Parser meldete sonst `Invalid JSON: EOF while parsing a
                # string` ueber eine Antwort, die das Modell korrekt begonnen
                # hatte.
                #
                # `is True` und nicht Wahrheitswert: `truncated` ist dreiwertig,
                # `None` heisst "kein finish_reason gemeldet". Eine Pruefung auf
                # Wahrheitswert behandelte "unbekannt" wie "vollstaendig" --
                # dieselbe Falle wie eine unbekannte Kostenangabe als 0.
                # KEINE eigene `error_class`: die wuerde `ok` auf False setzen, und
                # `ok` gehoert dem Transport -- der war erfolgreich. Das Urteil
                # steht im `error` des Versuchs, die Diagnose in `truncated` und
                # `max_tokens` der Zeile. Ohne Klasse ist der Versuch ausserdem
                # NICHT wiederholbar (`is_retryable_error_class`: `None` -> False),
                # und das ist richtig: ein zweiter Lauf traefe denselben Deckel.
                if response.trace.truncated is True:
                    deckel = response.trace.detail.get("max_tokens")
                    return AttemptResult(
                        trace=response.trace,
                        error=LiteLLMCallError(
                            "LiteLLM response was truncated (finish_reason=length) — "
                            f"max_tokens={deckel} reicht nicht; bei denkenden Modellen "
                            "zaehlt der Denkaufwand gegen dasselbe Budget"
                        ),
                    )
                try:
                    value = litellm.parser(response.body)
                except Exception as exc:
                    detail = {**response.trace.detail, "exception": type(exc).__name__}
                    return AttemptResult(
                        trace=replace(response.trace, error_class="schema", detail=detail),
                        error=exc,
                    )
                return AttemptResult(trace=response.trace, value=value)

            kwargs: dict[str, Any] = {}
            if sleeper is not None:
                kwargs["sleeper"] = sleeper
            outcome = await execute_async(
                purpose=purpose,
                alias=configured.route_aliases.get(route, route),
                evaluation_id=active_evaluation,
                direct_call=run_direct,
                litellm_call=run_litellm,
                per_route=configured.route_modes,
                ceiling=ceiling,
                # Das Budget kommt jetzt AN. Bis 2026-09-08 uebergab diese
                # Stelle weder Politik noch Zustand -- `execute_async` fiel auf
                # `BudgetPolicy()` ohne Limits zurueck, und `decide()` antwortete
                # ausnahmslos `allow`. Ein Budget ohne Aufrufer ist keine Bremse.
                budget_policy=lage.policy,
                daily=lage.daily,
                monthly=lage.monthly,
                budget_blocked=lage.reason if lage.blocks_routine else "",
                retry_policy=RetryPolicy(
                    max_attempts=configured.max_attempts,
                    base_backoff_s=configured.backoff_base_seconds,
                    max_backoff_s=configured.backoff_max_seconds,
                    max_jitter_s=configured.jitter_max_seconds,
                ),
                jitter=jitter,
                clock=clock,
                correlation_id=active_correlation,
                telemetry_path=telemetry_path,
                **kwargs,
            )

    selected = outcome.authoritative_attempt
    if selected is None:
        raise LiteLLMCallError("AI control plane produced no authoritative result")
    if selected.error is not None:
        raise selected.error
    if selected.value is None:
        raise LiteLLMCallError("AI control plane produced an empty authoritative result")
    transport = "litellm" if selected in outcome.litellm_attempts else "direct"
    return RoutedValue(value=selected.value, transport=transport, outcome=outcome)


__all__ = [
    "LiteLLMCallError",
    "LiteLLMRequest",
    "RoutedValue",
    "environment_settings",
    "inference_settings",
    "invoke",
    "reset_environment_settings",
]
