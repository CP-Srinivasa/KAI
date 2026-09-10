"""One place where an LLM call is measured, classified and audited.

Why this exists (NEO-F-005/-007/-008/-010): the repo had provider *construction*
centralised in ``app.analysis.factory`` but the *call* nowhere. Timeout, error
classification, correlation-id, tokens and audit are call-time concerns, so
they live here.

Two rules that are not negotiable:

* **Telemetry never raises into the caller.** The underlying writer is
  best-effort (``record_llm_call``); this scope adds no failure mode of its own.
* **Exactly one telemetry row per scope** - on success and on failure alike.
  A failure is re-raised unchanged; the scope never swallows.

The error taxonomy is copied (not imported) from ``app.intelligence.core``,
which held the only closed failure vocabulary in the repo. That module stays
quarantined; duplicating ten string constants is cheaper than coupling to it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import ValidationError

from app.observability.llm_telemetry import record_llm_call

if TYPE_CHECKING:
    from app.ai.models import AttemptTrace

ErrorClass = Literal[
    "timeout",
    "rate_limit",
    "auth",
    "quota",
    "schema",
    "refusal",
    # 200, und trotzdem nichts Brauchbares. Am 2026-09-08 auf kai-pi5 gemessen:
    # Gemini 2.5 Flash verbraucht das Ausgabebudget zuerst fuer internes Denken.
    # Bei `max_tokens=20` gingen alle 17 Ausgabe-Token dorthin, `text_tokens=0`,
    # `finish_reason=length`, `content: null` -- und der Aufruf kam als Erfolg
    # zurueck. Ohne eigene Klasse waere das ein leeres Ergebnis, das aussieht wie
    # ein gueltiges, und der Unterschied zwischen "das Modell hat nichts gesagt"
    # und "das Modell wurde abgeschnitten" ginge im Log verloren.
    "empty",
    "transport",
    "server",
    "cancelled",
    "unknown",
]

Purpose = Literal["analysis", "chat", "intent", "stt", "consensus"]
Outcome = Literal["success", "fallthrough", "exhausted", "skipped"]

#: WOFÜR das Geld ausgegeben wurde — die Auftraggeber-Sicht.
#:
#: Abgrenzung zu :data:`Purpose`, damit hier kein zweites Routing-SSOT
#: entsteht: ``Purpose`` sagt, WELCHE OBERFLÄCHE ruft (Analyse, Chat, Intent,
#: STT, Consensus) und bestimmt über ``app.ai.routes`` die Route.
#: ``UseCase`` sagt, WELCHE ARBEIT bezahlt wird. Beide fallen heute meist
#: zusammen — aber genau in dem Moment, in dem ``analysis`` mehr als einen
#: Auftraggeber hat (News-Ingest UND Premium-Signale), ist die Grenze ohne
#: dieses Feld nicht mehr rekonstruierbar, und die Kostenzuordnung wäre für
#: immer verloren. Deshalb steht es jetzt da und nicht später.
UseCase = Literal[
    "research",
    "news_intelligence",
    "premium_signals",
    "trading_paper",
    "monitoring",
    "operator_manual",
    "unknown",
]

#: Rückfall-Zuordnung, wenn kein Aufrufer einen ``use_case`` gesetzt hat.
#: Sie ist eine ABLEITUNG aus dem Purpose, keine zweite Tabelle: solange
#: ``analysis`` genau einen Auftraggeber hat, IST der Purpose die Zuordnung.
#: Sobald das nicht mehr stimmt, überschreibt der Eintrittspunkt sie über
#: :func:`use_case_scope` — und der Rückfall wird nie stillschweigend falsch,
#: weil ein nicht zugeordneter Aufruf ``unknown`` trägt statt einer Vermutung.
_PURPOSE_USE_CASE: dict[str, UseCase] = {
    "analysis": "news_intelligence",
    "chat": "operator_manual",
    "intent": "operator_manual",
    "stt": "operator_manual",
    "consensus": "trading_paper",
}

# Classes for which a second attempt cannot possibly help. Everything else is
# retryable - deliberately a deny-list, so unclassified errors keep the
# pre-existing retry behaviour instead of silently losing it.
# `empty` gehoert dazu: derselbe Aufruf mit demselben Token-Budget liefert
# dieselbe abgeschnittene Antwort. Ein zweiter Versuch kostet Geld und
# Reasoning-Token und aendert nichts.
_NON_RETRYABLE: frozenset[str] = frozenset({"auth", "quota", "schema", "cancelled", "empty"})

# 4xx codes that DO warrant a retry (the rest of 4xx is a client-side defect).
_RETRYABLE_CLIENT_STATUS: frozenset[int] = frozenset({408, 409, 425, 429})

_TRANSPORT_MARKERS: tuple[str, ...] = (
    "connecterror",
    "connectionerror",
    "connecttimeout",
    "readerror",
    "remoteprotocolerror",
    "apiconnectionerror",
)


# ── correlation propagation ─────────────────────────────────────────────────
# NEO-F-008: request ids existed at the HTTP edge and died there. A ContextVar
# carries one across the await boundaries into providers that cannot take an
# extra argument (BaseAnalysisProvider.analyze is a fixed interface) WITHOUT
# smuggling it through the prompt ``context`` dict, which would change the
# prompt text itself.

_CORRELATION_ID: ContextVar[str | None] = ContextVar("kai_llm_correlation_id", default=None)


def current_correlation_id() -> str | None:
    """Correlation id bound to the current async context, if any."""
    return _CORRELATION_ID.get()


@contextmanager
def correlation_scope(correlation_id: str | None) -> Iterator[str]:
    """Bind *correlation_id* to every LLM call made inside the block.

    Generates one when ``None`` so a chain is never anonymous. Always resets on
    exit, so a pipeline awaited inline cannot leak its id into its caller.
    """
    resolved = correlation_id or f"llm_{uuid4().hex[:12]}"
    token = _CORRELATION_ID.set(resolved)
    try:
        yield resolved
    finally:
        _CORRELATION_ID.reset(token)


#: Die Zuordnung EINER logischen Auswertung. Sie ist der einzige Schluessel,
#: mit dem sich hinterher sagen laesst, welche DIRECT-Zeile und welche
#: SHADOW-Zeile denselben Aufruf beschreiben.
#:
#: WARUM NICHT ``call_id``: die wird pro ZEILE vergeben, also fuer jeden
#: physischen Versuch und fuer jede Seite eine eigene. Als Paarungsschluessel
#: waere sie das Gegenteil dessen, was gebraucht wird.
#:
#: WARUM NICHT ``correlation_id`` allein: die haelt eine ganze Kette zusammen.
#: Ein Aufrufer, der sie durchreicht (``text_intent`` tut das), haette darunter
#: mehrere Auswertungen -- und zwei Auswertungen mit je einer DIRECT- und einer
#: SHADOW-Seite saehen aus wie eine Auswertung mit zwei Duplikaten.
_EVALUATION_ID: ContextVar[str | None] = ContextVar("kai_llm_evaluation_id", default=None)

#: Route und Modus der laufenden Auswertung. Der Direktpfad wird von den
#: Aufrufern instrumentiert, lange bevor es eine Control-Plane gab; er KENNT
#: seine Route nicht. Ohne diese beiden Werte traegt seine Telemetriezeile
#: keine Zuordnung, und die Auswertung wirft sie weg.
_LOGICAL_ROUTE: ContextVar[str | None] = ContextVar("kai_llm_logical_route", default=None)
_MODE: ContextVar[str | None] = ContextVar("kai_llm_mode", default=None)


#: Der Auftraggeber der laufenden Arbeit. Er steht NEBEN Route und Modus im
#: SELBEN Scope-Mechanismus -- bewusst kein eigener Korrelationsbegriff und
#: kein zweiter Strom: wer eine dritte Art von Zuordnung einführt, hat in drei
#: Monaten drei Wahrheiten darüber, wer wofür bezahlt hat.
_USE_CASE: ContextVar[str | None] = ContextVar("kai_llm_use_case", default=None)

#: Provenienz des Analyse-SYSTEM-Prompts fuer den Transportpfad.
#:
#: Der direkte Analysepfad kann die Werte an der Schreibstelle mitgeben; der
#: Weg ueber den Gateway nicht, weil `record_attempt_trace` tief in
#: `app/ai/runtime.py` gerufen wird und den Prompt nicht sieht.
#:
#: Ein Rueckschluss ueber `purpose == "analysis"` waere geraten, nicht gemessen:
#: er behauptete, jeder Analyse-Aufruf benutze diesen Prompt, und waere still
#: falsch, sobald ein anderer Aufrufer dieselbe Absicht meldet. Wer den Prompt
#: setzt, setzt hier auch die Provenienz.
_PROMPT_VERSION: ContextVar[str | None] = ContextVar("kai_analysis_prompt_version", default=None)
_PROMPT_HASH: ContextVar[str | None] = ContextVar("kai_analysis_prompt_hash", default=None)

#: Warum dieser Aufruf teurer laufen darf als die günstigste Stufe. LEER ist
#: der Normalfall und heisst: keine Eskalation. Ein leerer String statt
#: ``None``, weil "nicht eskaliert" eine AUSSAGE ist und kein fehlender Wert.
_ESCALATION_REASON: ContextVar[str] = ContextVar("kai_llm_escalation_reason", default="")

#: Aus welchem Topf der laufende Aufruf bezahlt wird (Budget-Policy v2).
#: Gesetzt von der Stelle, die ENTSCHEIDET (``app.ai.runtime``), nicht von der,
#: die schreibt: der Topf ist das Ergebnis einer Abwaegung und keine
#: Eigenschaft des Aufrufs, die sich hier nachtraeglich erraten liesse.
_BUDGET_POT: ContextVar[str | None] = ContextVar("kai_llm_budget_pot", default=None)

#: Was der AUFRUFER ueber seine eigene Arbeit weiss, bevor bezahlt wird.
#:
#: ``alert_eligible`` heisst NICHT "dieses Dokument erzeugt einen Alert" --
#: das weiss erst die Analyse. Es heisst: nach den Signalen, die ohne Kosten
#: schon vorliegen, ist es nicht ausgeschlossen. Nur solche Aufrufe duerfen die
#: Alert-Reserve anfassen.
#:
#: ``validation`` markiert kontrollierte SHADOW-/Validierungsarbeit. Getrennt
#: von ``role="shadow"``: die Rolle beschreibt, WIE gefahren wird, dieses Feld,
#: WOFUER bezahlt wird. Ein Schattenlauf im Regelbetrieb ist keine Validierung.
_ALERT_ELIGIBLE: ContextVar[bool] = ContextVar("kai_llm_alert_eligible", default=False)
_VALIDATION: ContextVar[bool] = ContextVar("kai_llm_validation", default=False)


class _AttemptCounter:
    """Physische Versuche EINES logischen Aufrufs — veränderlich mit Absicht.

    Ein ``ContextVar[int]`` würde hier nicht tragen: der Zähler wird TIEF im
    Tenacity-Dekorator hochgezählt und weit AUSSEN gelesen. Ein ``set()`` im
    Inneren wäre für den äusseren Leser je nach Task-Grenze unsichtbar. Ein
    veränderliches Objekt, das der äussere Scope anlegt, wird von innen
    beschrieben und von aussen gelesen — genau die Richtung, die gebraucht wird.
    """

    __slots__ = ("retries",)

    def __init__(self) -> None:
        self.retries = 0


_ATTEMPT_COUNTER: ContextVar[_AttemptCounter | None] = ContextVar(
    "kai_llm_attempt_counter", default=None
)


def current_evaluation_id() -> str | None:
    """Auswertungs-Id des aktuellen Kontexts, falls einer laeuft."""
    return _EVALUATION_ID.get()


def current_use_case() -> str | None:
    """Der gesetzte Auftraggeber, oder ``None``, wenn keiner gesetzt wurde."""
    return _USE_CASE.get()


def resolve_use_case(purpose: str | None) -> UseCase:
    """Der Auftraggeber dieser Zeile: gesetzter Scope, sonst Ableitung, sonst ``unknown``.

    Nie ein Rateversuch: was weder gesetzt noch aus einem bekannten Purpose
    ableitbar ist, heisst ``unknown`` und ist damit in der Auswertung
    auffindbar, statt einem beliebigen Topf zugeschlagen zu werden.
    """
    gesetzt = _USE_CASE.get()
    if gesetzt:
        return gesetzt  # type: ignore[return-value]
    if purpose:
        abgeleitet = _PURPOSE_USE_CASE.get(str(purpose))
        if abgeleitet is not None:
            return abgeleitet
    return "unknown"


@contextmanager
def use_case_scope(use_case: UseCase) -> Iterator[UseCase]:
    """Bindet den Auftraggeber an alles, was in diesem Block telemetriert wird.

    Am Eintrittspunkt gesetzt, nicht am Provider: der Provider weiss, WOMIT er
    fährt, aber nie, FÜR WEN. Wird beim Verlassen immer zurückgesetzt.
    """
    token = _USE_CASE.set(use_case)
    try:
        yield use_case
    finally:
        _USE_CASE.reset(token)


def current_escalation_reason() -> str:
    """Warum dieser Aufruf über der günstigsten Stufe läuft. Leer = gar nicht."""
    return _ESCALATION_REASON.get()


@contextmanager
def escalation_scope(reason: str) -> Iterator[str]:
    """Markiert einen Block als bewusst teurer — z. B. ``critical_override``."""
    token = _ESCALATION_REASON.set(reason or "")
    try:
        yield reason
    finally:
        _ESCALATION_REASON.reset(token)


@contextmanager
def budget_intent_scope(
    *, alert_eligible: bool = False, validation: bool = False
) -> Iterator[None]:
    """Die Absicht des Aufrufers binden, BEVOR das Budget entscheidet.

    Wird dort gesetzt, wo der Aufrufer seine eigene Arbeit kennt -- in der
    Analyse-Pipeline also nach der regelbasierten Vorabbewertung und vor dem
    bezahlten Aufruf. Die Runtime raet nicht: ohne diesen Block ist ein Aufruf
    weder alert-faehig noch Validierung, und beides ist die sichere Seite.
    """
    a = _ALERT_ELIGIBLE.set(alert_eligible)
    v = _VALIDATION.set(validation)
    try:
        yield
    finally:
        _ALERT_ELIGIBLE.reset(a)
        _VALIDATION.reset(v)


def budget_intent() -> tuple[bool, bool]:
    """``(alert_eligible, validation)`` des laufenden Aufrufs."""
    return _ALERT_ELIGIBLE.get(), _VALIDATION.get()


@contextmanager
def budget_pot_scope(pot: str) -> Iterator[None]:
    """Den entschiedenen Topf an die Telemetriezeilen dieses Aufrufs binden."""
    token = _BUDGET_POT.set(pot or None)
    try:
        yield
    finally:
        _BUDGET_POT.reset(token)


@contextmanager
def attempt_counter_scope() -> Iterator[_AttemptCounter]:
    """Zählt die Wiederholungen, die INNERHALB dieses Blocks stattfinden.

    Der Defekt, den das schliesst: die vier Direktprovider tragen je einen
    ``@retry(stop=stop_after_attempt(3))``, und die Messung liegt AUSSERHALB
    dieses Dekorators. Bis zu drei bezahlte Requests ergaben genau eine
    Telemetriezeile mit ``retry_count=0`` — die einzige strukturelle Quelle,
    die eine Rechnung über die Telemetrie treiben kann.

    Dieser Zähler ÄNDERT NICHTS an der Wiederholung selbst. Die Entscheidung,
    ob wiederholt wird, bleibt vollständig in ``app.ai.retry`` bzw. im
    Tenacity-Prädikat :func:`is_retryable_error`. Hier wird nur gezählt.
    """
    counter = _AttemptCounter()
    token = _ATTEMPT_COUNTER.set(counter)
    try:
        yield counter
    finally:
        _ATTEMPT_COUNTER.reset(token)


def note_retry_attempt(retry_state: object = None) -> None:
    """Tenacity-``before_sleep``-Hook: eine Wiederholung ist beschlossen.

    ``before_sleep`` feuert ZWISCHEN Versuchen, nicht vor dem ersten. Drei
    Versuche ergeben deshalb zwei Aufrufe und ``retry_count == 2`` — dieselbe
    Zählweise wie ``record_attempt_trace`` (``attempt_number - 1``).

    Ohne laufenden :func:`attempt_counter_scope` passiert nichts. Telemetrie
    darf den Aufruf nie mitreissen, auch nicht über einen Zähler.
    """
    counter = _ATTEMPT_COUNTER.get()
    if counter is not None:
        counter.retries += 1


def current_retry_count() -> int:
    """Wiederholungen im laufenden Zähl-Scope; ``0`` ohne Scope."""
    counter = _ATTEMPT_COUNTER.get()
    return counter.retries if counter is not None else 0


@contextmanager
def evaluation_scope(
    *, logical_route: str, mode: str, evaluation_id: str | None = None
) -> Iterator[str]:
    """Bindet EINE Auswertung an alles, was in diesem Block telemetriert wird.

    Damit traegt auch die Zeile des Direktpfads Route, Modus und Auswertungs-Id,
    ohne dass ein einziger Aufrufer geaendert werden muesste: sie stehen im
    Kontext, und ``llm_call_scope`` liest sie dort ab.

    Wie ``correlation_scope`` wird beim Verlassen immer zurueckgesetzt -- eine
    inline erwartete Pipeline darf ihre Zuordnung nicht an den Aufrufer
    weitervererben.
    """
    resolved = evaluation_id or f"eval_{uuid4().hex[:12]}"
    marken = (
        _EVALUATION_ID.set(resolved),
        _LOGICAL_ROUTE.set(logical_route),
        _MODE.set(mode),
    )
    try:
        yield resolved
    finally:
        _MODE.reset(marken[2])
        _LOGICAL_ROUTE.reset(marken[1])
        _EVALUATION_ID.reset(marken[0])


def http_status(exc: BaseException) -> int | None:
    """Best-effort HTTP status of *exc*, duck-typed across SDKs. Never raises.

    Covers the OpenAI SDK (``.status_code``), httpx (``.response.status_code``)
    and the Anthropic equivalents without importing any of them.
    """
    try:
        direct = getattr(exc, "status_code", None)
        if isinstance(direct, int) and 100 <= direct < 600:
            return direct
        response = getattr(exc, "response", None)
        if response is not None:
            nested = getattr(response, "status_code", None)
            if isinstance(nested, int) and 100 <= nested < 600:
                return nested
        status = getattr(exc, "status", None)
        if isinstance(status, int) and 100 <= status < 600:
            return status
    except Exception:  # noqa: BLE001 - classification must never raise
        return None
    return None


def classify_error(exc: BaseException) -> ErrorClass:
    """Map *exc* onto the closed taxonomy. Falls back to ``unknown``, never raises."""
    try:
        if isinstance(exc, asyncio.CancelledError):
            return "cancelled"
        # asyncio.TimeoutError is an alias of builtins.TimeoutError since 3.11.
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, ValidationError):
            return "schema"

        status = http_status(exc)
        if status is not None:
            if status in (401, 403):
                return "auth"
            if status == 402:
                return "quota"
            if status == 429:
                return "rate_limit"
            if status >= 500:
                return "server"

        name = type(exc).__name__.lower()
        if "timeout" in name:
            return "timeout"
        if "ratelimit" in name:
            return "rate_limit"
        if "authentication" in name or "permissiondenied" in name:
            return "auth"
        if any(marker in name for marker in _TRANSPORT_MARKERS):
            return "transport"
    except Exception:  # noqa: BLE001 - classification must never raise
        return "unknown"
    return "unknown"


def is_retryable_error_class(error_class: ErrorClass | None, status: int | None = None) -> bool:
    """Canonical retry decision for both exceptions and recorded attempts.

    LiteLLM transports return :class:`~app.ai.models.AttemptTrace` instead of
    raising.  Keeping this decision here prevents an exception policy and a
    trace policy from drifting apart.
    """
    if error_class is None or error_class in _NON_RETRYABLE:
        return False
    if status is not None and 400 <= status < 500 and status not in _RETRYABLE_CLIENT_STATUS:
        return False
    return True


def is_retryable_error(exc: BaseException) -> bool:
    """Retry predicate for the provider-level ``tenacity`` decorators.

    ``False`` for auth (401/403), quota, schema violations and any other 4xx a
    retry cannot fix - those previously cost three attempts plus up to 15 s of
    backoff for nothing (NEO-F-006).
    """
    return is_retryable_error_class(classify_error(exc), http_status(exc))


def _ganzzahl(wert: object) -> int | None:
    """Nur echte Zahlen, kein `bool`, keine Zeichenketten-Raterei.

    `transport_retries` kommt als Header-Zeichenkette an, `reasoning_tokens` als
    `int`. Beides soll als Zahl in der Zeile stehen -- oder gar nicht.
    """
    if isinstance(wert, bool):
        return None
    if isinstance(wert, int):
        return wert
    if isinstance(wert, str):
        try:
            return int(wert)
        except ValueError:
            return None
    return None


def _gleitkomma(wert: object) -> float | None:
    if isinstance(wert, bool):
        return None
    if isinstance(wert, int | float):
        return float(wert)
    return None


def _text_oder_none(wert: object) -> str | None:
    return wert if isinstance(wert, str) and wert else None


@contextmanager
def analysis_prompt_scope(*, version: str, prompt_hash: str) -> Iterator[None]:
    """Die Provenienz des gerade gesendeten Analyse-System-Prompts binden.

    Wird dort gesetzt, wo der Prompt in die Nutzlast geht -- damit die Angabe
    aus derselben Stelle stammt wie der Text und nicht aus einer Vermutung
    ueber den Zweck des Aufrufs.
    """
    v = _PROMPT_VERSION.set(version)
    h = _PROMPT_HASH.set(prompt_hash)
    try:
        yield
    finally:
        _PROMPT_VERSION.reset(v)
        _PROMPT_HASH.reset(h)


def record_attempt_trace(
    attempt_trace: AttemptTrace,
    *,
    correlation_id: str,
    evaluation_id: str | None = None,
    purpose: Purpose,
    logical_route: str,
    mode: str,
    role: str,
    attempt_number: int,
    budget_decision: str,
    circuit_state: str,
    execution_authority: bool,
    schema_status: str | None,
    outcome: Outcome,
    fallback_from: str | None = None,
    fallback_to: str | None = None,
    path: Path | None = None,
) -> None:
    """Append one physical returned attempt to the canonical telemetry stream."""
    raw_status = attempt_trace.detail.get("status_code")
    status = raw_status if isinstance(raw_status, int) else None
    # `detail` traegt, was der Transport ueber den Aufruf weiss. Bis zum
    # 2026-09-09 endete es hier: der Schreiber pickte Felder einzeln heraus und
    # liess den Rest fallen. Die Zeile trug damit Kosten und Modell, aber nicht
    # die Aufschluesselung, aus der man sie versteht -- und `error_class="empty"`
    # ohne den Grund, obwohl die Unterscheidung zwischen "abgeschnitten" und
    # "verstummt" der ganze Zweck dieser Klasse ist.
    detail = attempt_trace.detail
    record_llm_call(
        provider=attempt_trace.actual_provider,
        model=attempt_trace.actual_model,
        ok=attempt_trace.ok,
        latency_ms=attempt_trace.latency_ms,
        role=role,
        error_type=str(attempt_trace.detail.get("exception") or "") or None,
        path=path,
        correlation_id=correlation_id,
        # Pro ZEILE neu -- deshalb taugt sie nicht als Paarungsschluessel.
        call_id=f"llmc_{uuid4().hex[:8]}",
        evaluation_id=evaluation_id if evaluation_id is not None else _EVALUATION_ID.get(),
        purpose=purpose,
        attempt=attempt_number,
        error_class=attempt_trace.error_class,
        http_status=status,
        prompt_tokens=attempt_trace.input_tokens or 0,
        completion_tokens=attempt_trace.output_tokens or 0,
        outcome=outcome,
        logical_route=logical_route,
        mode=mode,
        transport=attempt_trace.transport,
        requested_model_alias=attempt_trace.requested_model,
        actual_provider=attempt_trace.actual_provider or None,
        actual_model=attempt_trace.actual_model or None,
        identity_proven=attempt_trace.identity_proven,
        retry_count=max(0, attempt_number - 1),
        fallback_from=fallback_from,
        fallback_to=fallback_to,
        input_tokens=attempt_trace.input_tokens,
        output_tokens=attempt_trace.output_tokens,
        cost_usd=attempt_trace.cost_usd,
        use_case=resolve_use_case(purpose),
        escalation_reason=_ESCALATION_REASON.get(),
        schema_status=schema_status,
        reasoning_tokens=_ganzzahl(detail.get("reasoning_tokens")),
        cost_reasoning_usd=_gleitkomma(detail.get("cost_reasoning_usd")),
        finish_reason=_text_oder_none(detail.get("finish_reason")),
        empty_reason=_text_oder_none(detail.get("empty_reason")),
        transport_retries=_ganzzahl(detail.get("transport_retries")),
        truncated=attempt_trace.truncated,
        max_tokens=_ganzzahl(detail.get("max_tokens")),
        analysis_system_prompt_version=_PROMPT_VERSION.get(),
        analysis_system_prompt_hash=_PROMPT_HASH.get(),
        budget_pot=_BUDGET_POT.get(),
        budget_decision=budget_decision,
        circuit_state=circuit_state,
        execution_authority=execution_authority,
        upstream_request_id=attempt_trace.request_id or None,
    )


@dataclass
class CallScope:
    """Mutable handle handed to the caller inside :func:`llm_call_scope`."""

    correlation_id: str
    call_id: str
    provider: str
    model: str
    purpose: str
    role: str
    chain_position: int
    attempt: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failure_outcome: str = "exhausted"
    success_outcome: str = field(default="success", repr=False)

    def set_tokens(self, prompt: int | None, completion: int | None) -> None:
        """Record token usage. Tolerates ``None``/garbage from SDK responses."""
        try:
            self.prompt_tokens = int(prompt or 0)
        except (TypeError, ValueError):
            self.prompt_tokens = 0
        try:
            self.completion_tokens = int(completion or 0)
        except (TypeError, ValueError):
            self.completion_tokens = 0

    def set_model(self, model: str | None) -> None:
        """Override the model once the response reveals which one actually ran."""
        if model:
            self.model = model

    def set_outcome(self, outcome: Outcome) -> None:
        """Override the outcome recorded on *successful* exit."""
        self.success_outcome = outcome


@asynccontextmanager
async def llm_call_scope(
    *,
    purpose: Purpose,
    provider: str,
    model: str | None,
    role: str = "primary",
    correlation_id: str | None = None,
    call_id: str | None = None,
    chain_position: int = 0,
    attempt: int = 1,
    failure_outcome: Outcome = "exhausted",
    path: Path | None = None,
) -> AsyncIterator[CallScope]:
    """Measure exactly one LLM call and append exactly one v2 telemetry row.

    Args:
        purpose: which surface the call serves (analysis/chat/intent/stt/consensus).
        provider: provider name, e.g. ``"openai"``.
        model: model name; ``None`` becomes ``""`` (never invented).
        role: ``primary`` | ``shadow`` | ``validator``.
        correlation_id: request-scoped id; falls back to the ambient
            :func:`correlation_scope` and finally to a generated one, so a row
            is never anonymous.
        call_id: per-attempt id; auto-generated when absent.
        chain_position: index within a fallback chain; ``-1`` marks an outer
            wrapper row (kept for the v1 dashboard reader).
        attempt: 1-based retry counter.
        failure_outcome: what to record when the body raises - ``fallthrough``
            when a further provider will be tried, ``exhausted`` otherwise.
        path: telemetry sink; ``None`` uses the default at call time.

    Yields:
        CallScope - call ``set_tokens`` / ``set_outcome`` / ``set_model`` on it.

    Raises:
        Whatever the body raises, unchanged.
    """
    scope = CallScope(
        correlation_id=correlation_id or current_correlation_id() or f"llm_{uuid4().hex[:12]}",
        call_id=call_id or f"llmc_{uuid4().hex[:8]}",
        provider=provider,
        model=model or "",
        purpose=purpose,
        role=role,
        chain_position=chain_position,
        attempt=attempt,
        failure_outcome=failure_outcome,
    )
    started = monotonic()
    # Die Wiederholungen des Providers passieren INNERHALB dieses Blocks
    # (Tenacity sitzt auf ``analyze``, die Messung darum herum). Ohne diesen
    # Zähler tragen bis zu drei bezahlte Requests eine Zeile mit ``retry_count=0``.
    counter = _AttemptCounter()
    counter_token = _ATTEMPT_COUNTER.set(counter)
    try:
        yield scope
    except BaseException as exc:
        record_llm_call(
            provider=scope.provider,
            model=scope.model,
            ok=False,
            latency_ms=(monotonic() - started) * 1000.0,
            role=scope.role,
            error_type=type(exc).__name__,
            path=path,
            correlation_id=scope.correlation_id,
            call_id=scope.call_id,
            purpose=scope.purpose,
            chain_position=scope.chain_position,
            attempt=scope.attempt,
            error_class=classify_error(exc),
            http_status=http_status(exc),
            prompt_tokens=scope.prompt_tokens,
            completion_tokens=scope.completion_tokens,
            outcome=scope.failure_outcome,
            # Aus dem Kontext, nicht vom Aufrufer: der Direktpfad wurde
            # instrumentiert, als es noch keine Routen gab.
            evaluation_id=_EVALUATION_ID.get(),
            logical_route=_LOGICAL_ROUTE.get(),
            mode=_MODE.get(),
            use_case=resolve_use_case(scope.purpose),
            escalation_reason=_ESCALATION_REASON.get(),
            budget_pot=_BUDGET_POT.get(),
            retry_count=counter.retries,
        )
        raise
    finally:
        # Nach der Zeile, nicht davor: die Zeile liest den Zähler noch.
        _ATTEMPT_COUNTER.reset(counter_token)
    record_llm_call(
        provider=scope.provider,
        model=scope.model,
        ok=True,
        latency_ms=(monotonic() - started) * 1000.0,
        role=scope.role,
        path=path,
        correlation_id=scope.correlation_id,
        call_id=scope.call_id,
        purpose=scope.purpose,
        chain_position=scope.chain_position,
        attempt=scope.attempt,
        prompt_tokens=scope.prompt_tokens,
        completion_tokens=scope.completion_tokens,
        outcome=scope.success_outcome,
        evaluation_id=_EVALUATION_ID.get(),
        logical_route=_LOGICAL_ROUTE.get(),
        mode=_MODE.get(),
        use_case=resolve_use_case(scope.purpose),
        escalation_reason=_ESCALATION_REASON.get(),
        budget_pot=_BUDGET_POT.get(),
        retry_count=counter.retries,
    )
