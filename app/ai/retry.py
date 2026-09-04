"""Eine Retry-Politik, an genau einer Stelle, mit hartem Deckel.

Der Defekt, gegen den diese Datei steht, hiess im ersten Anlauf „unbounded
retry" — und er kam nicht daher, dass jemand eine Endlosschleife schrieb,
sondern daher, dass ZWEI Schichten unabhaengig voneinander wiederholten. Drei
Versuche im Provider mal drei im Router sind neun Aufrufe, neunmal Kosten und
neun Zeilen Telemetrie fuer einen einzigen logischen Aufruf. Niemand hatte das
entschieden; es ergab sich.

Deshalb steht die Politik hier, und nur hier. `app.ai.audit` besitzt weiterhin
die Fehler-TAXONOMIE — welche Klassen es gibt und welche eine Wiederholung
ueberhaupt vertragen. Diese Datei schreibt sie nicht zweit, sondern importiert
sie: ``NON_RETRYABLE_CLASSES`` und ``RETRYABLE_CLIENT_STATUS`` kommen von dort.
Eine zweite Liste waere die naechste Wahrheit, die driftet.

**Jeder physische Versuch bleibt ein eigener AttemptTrace.** Ein Retry ist kein
Detail, das man zusammenfasst: er kostet Geld, Zeit und Upstream-Kontingent, und
wer ihn wegmittelt, sieht in der Auswertung eine Latenz, die es nie gab.

**UNKNOWN bleibt UNKNOWN.** Ein wiederholter Versuch ohne Kostenangabe wird
nicht dadurch guenstig, dass ein anderer Versuch bekannte Kosten hatte.

Rein: keine Uhr, kein Schlaf, kein I/O. Der Backoff wird BERECHNET, nicht
gewartet — wer wartet, entscheidet der Aufrufer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.ai.audit import NON_RETRYABLE_CLASSES, RETRYABLE_CLIENT_STATUS, ErrorClass
from app.ai.models import AttemptTrace

#: Harte Obergrenze fuer die Zahl physischer Versuche EINES logischen Aufrufs.
#: Nicht konfigurierbar nach oben: eine Politik, die sich beliebig hochstellen
#: laesst, ist keine Obergrenze, sondern eine Voreinstellung.
MAX_ATTEMPTS_CEILING: Final = 5

DEFAULT_MAX_ATTEMPTS: Final = 3
DEFAULT_BASE_DELAY_S: Final = 0.5
DEFAULT_MAX_DELAY_S: Final = 8.0


@dataclass(frozen=True)
class RetryPolicy:
    """Wie oft und wie lange — beides begrenzt."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay_s: float = DEFAULT_BASE_DELAY_S
    max_delay_s: float = DEFAULT_MAX_DELAY_S

    def __post_init__(self) -> None:
        # Kein `raise`: eine unsinnige Konfiguration darf den Aufruf nicht
        # sprengen, sie wird eingefangen. Aber sie wird auch nicht respektiert.
        object.__setattr__(
            self, "max_attempts", max(1, min(self.max_attempts, MAX_ATTEMPTS_CEILING))
        )
        object.__setattr__(self, "base_delay_s", max(0.0, self.base_delay_s))
        object.__setattr__(self, "max_delay_s", max(0.0, self.max_delay_s))


def is_retryable_class(error_class: ErrorClass | None, *, http_status: int | None = None) -> bool:
    """Vertraegt dieser Fehler eine Wiederholung?

    ``None`` heisst Erfolg — und Erfolg wird nicht wiederholt.

    Die Entscheidung faellt auf derselben Taxonomie wie
    :func:`app.ai.audit.is_retryable_error`, nur ueber die bereits
    klassifizierte Klasse statt ueber die Ausnahme: an dieser Stelle der Kette
    gibt es keine Exception mehr, sondern einen ``AttemptTrace``.
    """
    if error_class is None:
        return False
    if error_class in NON_RETRYABLE_CLASSES:
        return False
    if http_status is not None and 400 <= http_status < 500:
        return http_status in RETRYABLE_CLIENT_STATUS
    return True


def should_retry(attempts: Sequence[AttemptTrace], *, policy: RetryPolicy) -> bool:
    """Darf nach diesen Versuchen noch einer folgen?

    Zwei Bedingungen, beide notwendig: der Deckel ist nicht erreicht, und der
    letzte Fehler vertraegt ueberhaupt eine Wiederholung. Ohne die zweite haette
    ein Auth-Fehler dreimal dieselbe Ablehnung eingesammelt.
    """
    if not attempts:
        return True
    if len(attempts) >= policy.max_attempts:
        return False
    letzter = attempts[-1]
    return is_retryable_class(letzter.error_class, http_status=_status_of(letzter))


def delay_before_attempt(index: int, *, policy: RetryPolicy) -> float:
    """Wartezeit VOR dem Versuch mit Index *index* (0-basiert), in Sekunden.

    Exponentiell, aber gedeckelt. Vor dem ersten Versuch wird nicht gewartet —
    ein Backoff, der schon den Erstaufruf verzoegert, bestraft den Normalfall
    fuer einen Fehler, der noch gar nicht passiert ist.
    """
    if index <= 0:
        return 0.0
    exponentiell: float = policy.base_delay_s * float(2 ** (index - 1))
    return min(exponentiell, policy.max_delay_s)


def total_backoff_s(attempt_count: int, *, policy: RetryPolicy) -> float:
    """Summe aller Wartezeiten fuer *attempt_count* Versuche — die Obergrenze.

    Damit laesst sich vorab sagen, wie lange ein Aufruf im schlimmsten Fall
    haengt. Ohne diese Zahl ist „begrenzt" eine Behauptung.
    """
    return sum(delay_before_attempt(i, policy=policy) for i in range(attempt_count))


def _status_of(attempt: AttemptTrace) -> int | None:
    wert = attempt.detail.get("status_code")
    return wert if isinstance(wert, int) and not isinstance(wert, bool) else None


__all__ = [
    "DEFAULT_BASE_DELAY_S",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_DELAY_S",
    "MAX_ATTEMPTS_CEILING",
    "RetryPolicy",
    "delay_before_attempt",
    "is_retryable_class",
    "should_retry",
    "total_backoff_s",
]
