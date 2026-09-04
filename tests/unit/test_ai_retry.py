"""Bounded Retry (ADR 0017, Lücke A).

Der Defekt hiess „unbounded retry" und entstand nicht durch eine
Endlosschleife, sondern durch **zwei Schichten**, die unabhängig voneinander
wiederholten: drei Versuche im Provider mal drei im Router sind neun Aufrufe für
einen logischen Call. Niemand hatte das entschieden.
"""

from __future__ import annotations

import pytest

from app.ai.audit import NON_RETRYABLE_CLASSES, RETRYABLE_CLIENT_STATUS
from app.ai.models import AttemptTrace
from app.ai.retry import (
    MAX_ATTEMPTS_CEILING,
    RetryPolicy,
    delay_before_attempt,
    is_retryable_class,
    should_retry,
    total_backoff_s,
)


def _a(error_class=None, status: int | None = None) -> AttemptTrace:
    return AttemptTrace(
        transport="litellm",
        requested_model="fast",
        latency_ms=1.0,
        error_class=error_class,
        detail={"status_code": status} if status is not None else {},
    )


# --------------------------------------------------------------------------
# Eine Taxonomie, nicht zwei.
# --------------------------------------------------------------------------


def test_die_klassen_kommen_aus_audit_und_werden_nicht_zweitgeschrieben() -> None:
    """Eine zweite Liste wäre die nächste Wahrheit, die driftet."""
    import app.ai.retry as retry

    quelle = (retry.__file__ or "").replace("\\", "/")
    text = open(quelle, encoding="utf-8").read()  # noqa: SIM115, PTH123
    assert "from app.ai.audit import" in text
    assert '"auth"' not in text, "die Klassenmenge gehoert nach audit.py"
    assert "408" not in text, "die Statusmenge gehoert nach audit.py"


@pytest.mark.parametrize("klasse", sorted(NON_RETRYABLE_CLASSES))
def test_nicht_wiederholbare_klassen_werden_nicht_wiederholt(klasse: str) -> None:
    assert not is_retryable_class(klasse)  # type: ignore[arg-type]


@pytest.mark.parametrize("klasse", ["timeout", "rate_limit", "transport", "server", "unknown"])
def test_wiederholbare_klassen_werden_wiederholt(klasse: str) -> None:
    assert is_retryable_class(klasse)  # type: ignore[arg-type]


def test_erfolg_wird_nicht_wiederholt() -> None:
    assert not is_retryable_class(None)


@pytest.mark.parametrize("status", sorted(RETRYABLE_CLIENT_STATUS))
def test_die_wiederholbaren_4xx_bleiben_wiederholbar(status: int) -> None:
    assert is_retryable_class("server", http_status=status)  # type: ignore[arg-type]


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_die_uebrigen_4xx_sind_endgueltig(status: int) -> None:
    """Ein Client-Fehler wird durch Wiederholung nicht zum Server-Fehler."""
    assert not is_retryable_class("server", http_status=status)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Der Deckel ist ein Deckel.
# --------------------------------------------------------------------------


def test_die_obergrenze_laesst_sich_nicht_hochstellen() -> None:
    """Eine Politik, die sich beliebig hochsetzen lässt, ist eine Voreinstellung."""
    assert RetryPolicy(max_attempts=99).max_attempts == MAX_ATTEMPTS_CEILING
    assert RetryPolicy(max_attempts=0).max_attempts == 1
    assert RetryPolicy(max_attempts=-5).max_attempts == 1


def test_negative_wartezeiten_werden_eingefangen() -> None:
    p = RetryPolicy(base_delay_s=-1.0, max_delay_s=-2.0)
    assert p.base_delay_s == 0.0
    assert p.max_delay_s == 0.0


def test_nach_dem_deckel_wird_nicht_mehr_wiederholt() -> None:
    p = RetryPolicy(max_attempts=3)
    assert should_retry([_a("timeout")], policy=p)
    assert should_retry([_a("timeout"), _a("timeout")], policy=p)
    assert not should_retry([_a("timeout")] * 3, policy=p)


def test_ohne_versuche_darf_der_erste_laufen() -> None:
    assert should_retry([], policy=RetryPolicy())


def test_ein_auth_fehler_wird_nicht_dreimal_abgelehnt() -> None:
    assert not should_retry([_a("auth")], policy=RetryPolicy(max_attempts=3))


def test_ein_erfolg_beendet_die_kette() -> None:
    assert not should_retry([_a("timeout"), _a()], policy=RetryPolicy(max_attempts=3))


def test_der_status_aus_dem_versuch_entscheidet_mit() -> None:
    """404 vertraegt keine Wiederholung, auch wenn die Klasse es täte."""
    p = RetryPolicy(max_attempts=3)
    assert not should_retry([_a("server", status=404)], policy=p)
    assert should_retry([_a("server", status=429)], policy=p)


# --------------------------------------------------------------------------
# Backoff — berechnet, nicht gewartet, und nach oben begrenzt.
# --------------------------------------------------------------------------


def test_vor_dem_ersten_versuch_wird_nicht_gewartet() -> None:
    """Sonst bestraft der Backoff den Normalfall für einen Fehler,
    der noch gar nicht passiert ist."""
    assert delay_before_attempt(0, policy=RetryPolicy()) == 0.0


def test_der_backoff_waechst_exponentiell_und_deckelt() -> None:
    p = RetryPolicy(base_delay_s=0.5, max_delay_s=2.0)
    assert delay_before_attempt(1, policy=p) == pytest.approx(0.5)
    assert delay_before_attempt(2, policy=p) == pytest.approx(1.0)
    assert delay_before_attempt(3, policy=p) == pytest.approx(2.0)
    assert delay_before_attempt(9, policy=p) == pytest.approx(2.0), "gedeckelt"


def test_die_gesamte_wartezeit_ist_vorab_bekannt() -> None:
    """Ohne diese Zahl ist „begrenzt" eine Behauptung."""
    p = RetryPolicy(max_attempts=3, base_delay_s=0.5, max_delay_s=8.0)
    assert total_backoff_s(3, policy=p) == pytest.approx(1.5)
    assert total_backoff_s(1, policy=p) == 0.0


def test_das_modul_wartet_nicht_selbst() -> None:
    """Rein: keine Uhr, kein Schlaf — sonst wäre es nicht testbar."""
    import app.ai.retry as retry

    text = open(retry.__file__ or "", encoding="utf-8").read()  # noqa: SIM115, PTH123
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text


def test_jeder_versuch_bleibt_ein_eigener_trace() -> None:
    """Ein Retry wird nicht weggemittelt — er kostet Geld, Zeit und Kontingent."""
    versuche = [_a("timeout"), _a("timeout"), _a()]
    assert len(versuche) == 3
    assert should_retry(versuche[:1], policy=RetryPolicy())
    assert not should_retry(versuche, policy=RetryPolicy())


def test_unbekannte_kosten_bleiben_ueber_wiederholungen_unbekannt() -> None:
    from app.ai.models import total_cost_usd

    versuche = [
        AttemptTrace("litellm", "fast", 1.0, cost_usd=0.001, error_class="timeout"),
        AttemptTrace("litellm", "fast", 1.0, cost_usd=None),
    ]
    assert total_cost_usd(versuche) is None
