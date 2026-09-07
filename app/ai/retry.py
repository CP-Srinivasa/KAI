"""Bounded retry mechanics for transports governed by :mod:`app.ai`.

Der Defekt, gegen den diese Datei steht, hiess „unbounded retry" — und er kam
nicht daher, dass jemand eine Endlosschleife schrieb, sondern daher, dass ZWEI
Schichten unabhaengig voneinander wiederholten. Drei Versuche im Provider mal
drei im Router sind neun Aufrufe, neunmal Kosten und neun Zeilen Telemetrie fuer
einen einzigen logischen Aufruf. Niemand hatte das entschieden; es ergab sich.

Deshalb gibt es genau eine Wiederholungs-Politik, und sie steht hier. Die
ENTSCHEIDUNG, ob ein Fehler ueberhaupt eine Wiederholung vertraegt, steht
weiterhin in :func:`app.ai.audit.is_retryable_error_class` — eine zweite
Praedikat-Implementierung waere die naechste Wahrheit, die driftet. Diese Datei
besitzt nur die endliche Versuchszahl und die begrenzte Wartezeit.

Rein: keine Uhr, kein Schlaf, kein I/O. Der Backoff wird BERECHNET, nicht
gewartet — wer wartet, entscheidet der Aufrufer (``time.sleep`` im synchronen
Gateway, ``asyncio.sleep`` im asynchronen).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.ai.audit import is_retryable_error_class
from app.ai.models import AttemptTrace

#: Voreinstellung: ein Aufruf plus zwei Wiederholungen.
DEFAULT_MAX_ATTEMPTS: Final = 3

#: Harte Obergrenze fuer die Zahl physischer Versuche EINES logischen Aufrufs.
#: Getrennt benannt, obwohl der Wert heute mit der Voreinstellung uebereinstimmt:
#: eine Voreinstellung darf sich aendern, eine Obergrenze ist eine Zusage. Waeren
#: es dieselbe Konstante, wuerde ein Absenken der Voreinstellung stillschweigend
#: die Zusage mitverschieben.
MAX_ATTEMPTS_CEILING: Final = 3


@dataclass(frozen=True)
class RetryPolicy:
    """Wie oft und wie lange — beides endlich, beides vorab bezifferbar."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_backoff_s: float = 0.25
    max_backoff_s: float = 2.0
    max_jitter_s: float = 0.1

    def __post_init__(self) -> None:
        # Bewusst `raise` statt stillem Zurechtbiegen: eine Politik, die eine
        # unsinnige Zahl kommentarlos auf den Deckel klemmt, verbirgt einen
        # Konfigurationsfehler, statt ihn zu melden. Die Umgebungsschicht
        # (``InferenceSettings``) faengt den Fall ohnehin vorher ab, dies hier
        # ist die Zusage fuer alle uebrigen Aufrufer.
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS_CEILING:
            raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS_CEILING}")
        if self.base_backoff_s < 0 or self.max_backoff_s < 0 or self.max_jitter_s < 0:
            raise ValueError("retry delays must be non-negative")


def should_retry(attempt: AttemptTrace) -> bool:
    """Vertraegt dieser zurueckgegebene Versuch eine Wiederholung?

    LiteLLM-Transporte werfen nicht, sie geben einen :class:`AttemptTrace`
    zurueck. Die Taxonomie ist trotzdem dieselbe wie fuer Ausnahmen — sonst
    haetten Exception-Pfad und Trace-Pfad zwei Meinungen ueber 429 und 403.
    """
    roh = attempt.detail.get("status_code")
    # `True` ist in Python ein `int` -- als HTTP-Status waere es Unsinn.
    status = roh if isinstance(roh, int) and not isinstance(roh, bool) else None
    return is_retryable_error_class(attempt.error_class, status)


def retry_delay_s(
    failed_attempt_number: int,
    policy: RetryPolicy,
    *,
    jitter: Callable[[], float] = lambda: 0.0,
) -> float:
    """Wartezeit vor dem naechsten Versuch — begrenzt auch bei feindlichem Jitter."""
    exponential = float(policy.base_backoff_s * (2 ** max(0, failed_attempt_number - 1)))
    raw_jitter = jitter()
    jitter_value = float(raw_jitter) if isinstance(raw_jitter, (int, float)) else 0.0
    bounded_jitter = min(policy.max_jitter_s, max(0.0, jitter_value))
    return float(min(policy.max_backoff_s, exponential + bounded_jitter))


def worst_case_backoff_s(policy: RetryPolicy) -> float:
    """Summe aller Wartezeiten im schlimmsten Fall, in Sekunden.

    Ohne diese Zahl ist „begrenzt" eine Behauptung. Sie beziffert, wie lange ein
    logischer Aufruf hoechstens in der Wiederholungsschleife haengen kann —
    Netzwerkzeit nicht eingerechnet, die deckt das Transport-Timeout ab.
    """
    return sum(
        retry_delay_s(k, policy, jitter=lambda: policy.max_jitter_s)
        for k in range(1, policy.max_attempts)
    )


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "MAX_ATTEMPTS_CEILING",
    "RetryPolicy",
    "retry_delay_s",
    "should_retry",
    "worst_case_backoff_s",
]
