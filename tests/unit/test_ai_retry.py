"""Bounded Retry (ADR 0017, Luecke A) — GENAU EINE Politik.

Der Defekt hiess „unbounded retry" und entstand nicht durch eine
Endlosschleife, sondern durch **zwei Schichten**, die unabhaengig voneinander
wiederholten: drei Versuche im Provider mal drei im Router sind neun Aufrufe
fuer einen logischen Call. Niemand hatte das entschieden.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.audit import is_retryable_error_class
from app.ai.models import AttemptTrace
from app.ai.retry import (
    DEFAULT_MAX_ATTEMPTS,
    MAX_ATTEMPTS_CEILING,
    RetryPolicy,
    retry_delay_s,
    should_retry,
    worst_case_backoff_s,
)


def _a(error_class: str | None = None, status: int | None = None) -> AttemptTrace:
    return AttemptTrace(
        transport="litellm",
        requested_model="kai-standard",
        latency_ms=1.0,
        error_class=error_class,  # type: ignore[arg-type]
        detail={"status_code": status} if status is not None else {},
    )


# ---------------------------------------------------------------------------
# Eine Taxonomie, nicht zwei.
# ---------------------------------------------------------------------------


def test_die_entscheidung_kommt_aus_audit_und_wird_nicht_zweitgeschrieben() -> None:
    """Zwei Praedikate waeren zwei Meinungen ueber 429 und 403."""
    import app.ai.retry as retry

    text = (retry.__file__ or "").replace("\\", "/")
    quelle = open(text, encoding="utf-8").read()  # noqa: SIM115, PTH123
    assert "from app.ai.audit import is_retryable_error_class" in quelle
    assert '"auth"' not in quelle, "die Klassenmenge gehoert nach audit.py"
    assert "408" not in quelle, "die Statusmenge gehoert nach audit.py"


@pytest.mark.parametrize("klasse", ["auth", "quota", "schema", "cancelled"])
def test_nicht_wiederholbare_klassen_werden_nicht_wiederholt(klasse: str) -> None:
    assert not should_retry(_a(klasse))


@pytest.mark.parametrize("klasse", ["timeout", "rate_limit", "transport", "server", "unknown"])
def test_wiederholbare_klassen_werden_wiederholt(klasse: str) -> None:
    assert should_retry(_a(klasse))


def test_erfolg_wird_nicht_wiederholt() -> None:
    assert not should_retry(_a(None))


@pytest.mark.parametrize("status", [408, 409, 425, 429])
def test_die_wiederholbaren_4xx_bleiben_wiederholbar(status: int) -> None:
    assert should_retry(_a("server", status=status))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_die_uebrigen_4xx_sind_endgueltig(status: int) -> None:
    """Ein Client-Fehler wird durch Wiederholung nicht zum Server-Fehler."""
    assert not should_retry(_a("server", status=status))


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_wird_wiederholt(status: int) -> None:
    assert should_retry(_a("server", status=status))


def test_ein_bool_ist_kein_statuscode() -> None:
    """``True`` ist in Python ein ``int`` — als HTTP-Status waere es Unsinn."""
    trace = AttemptTrace(
        transport="litellm",
        requested_model="kai-standard",
        latency_ms=1.0,
        error_class="server",
        detail={"status_code": True},
    )
    assert should_retry(trace), "kein 4xx-Ausschluss auf Basis eines Bools"


def test_dieselbe_taxonomie_wie_der_exception_pfad() -> None:
    for klasse in ("auth", "quota", "schema", "cancelled", "timeout", "server"):
        assert should_retry(_a(klasse)) is is_retryable_error_class(klasse, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Der Deckel ist ein Deckel.
# ---------------------------------------------------------------------------


def test_die_obergrenze_ist_eine_zusage_keine_voreinstellung() -> None:
    assert DEFAULT_MAX_ATTEMPTS <= MAX_ATTEMPTS_CEILING


@pytest.mark.parametrize("zahl", [0, -1, MAX_ATTEMPTS_CEILING + 1, 99])
def test_ausserhalb_der_obergrenze_wird_gemeldet_nicht_zurechtgebogen(zahl: int) -> None:
    """Stilles Klemmen wuerde einen Konfigurationsfehler verbergen."""
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=zahl)


@pytest.mark.parametrize("feld", ["base_backoff_s", "max_backoff_s", "max_jitter_s"])
def test_negative_wartezeiten_werden_gemeldet(feld: str) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        RetryPolicy(**{feld: -1.0})


# ---------------------------------------------------------------------------
# Backoff — berechnet, nicht gewartet, und nach oben begrenzt.
# ---------------------------------------------------------------------------


def test_der_backoff_waechst_exponentiell_und_deckelt() -> None:
    p = RetryPolicy(base_backoff_s=0.5, max_backoff_s=2.0, max_jitter_s=0.0)
    assert retry_delay_s(1, p) == pytest.approx(0.5)
    assert retry_delay_s(2, p) == pytest.approx(1.0)
    assert retry_delay_s(3, p) == pytest.approx(2.0)
    assert retry_delay_s(9, p) == pytest.approx(2.0), "gedeckelt"


def test_ein_feindlicher_jitter_sprengt_die_grenze_nicht() -> None:
    """Der Jitter ist eine Streuung, kein Kanal fuer beliebige Wartezeit."""
    p = RetryPolicy(base_backoff_s=0.5, max_backoff_s=2.0, max_jitter_s=0.1)
    assert retry_delay_s(1, p, jitter=lambda: 10_000.0) == pytest.approx(0.6)
    assert retry_delay_s(1, p, jitter=lambda: -10_000.0) == pytest.approx(0.5)
    assert retry_delay_s(1, p, jitter=lambda: float("nan")) >= 0.0


def test_ein_jitter_der_unsinn_liefert_wird_ignoriert() -> None:
    p = RetryPolicy(base_backoff_s=0.5, max_backoff_s=2.0)
    assert retry_delay_s(1, p, jitter=lambda: "spaet") == pytest.approx(0.5)  # type: ignore[arg-type,return-value]


def test_die_schlimmste_wartezeit_ist_vorab_bekannt() -> None:
    """Ohne diese Zahl ist „begrenzt" eine Behauptung."""
    p = RetryPolicy(max_attempts=3, base_backoff_s=0.25, max_backoff_s=2.0, max_jitter_s=0.1)
    # zwei Wartezeiten bei drei Versuchen: (0.25+0.1) + (0.5+0.1)
    assert worst_case_backoff_s(p) == pytest.approx(0.95)
    assert worst_case_backoff_s(RetryPolicy(max_attempts=1)) == 0.0


def test_die_schlimmste_wartezeit_bleibt_unter_dem_produkt_der_deckel() -> None:
    p = RetryPolicy(max_attempts=MAX_ATTEMPTS_CEILING, base_backoff_s=99.0, max_backoff_s=2.0)
    assert worst_case_backoff_s(p) <= p.max_backoff_s * (MAX_ATTEMPTS_CEILING - 1)


def test_das_modul_wartet_nicht_selbst() -> None:
    """Rein: keine Uhr, kein Schlaf — sonst waere es nicht testbar.

    Geprueft wird der SYNTAXBAUM, nicht der Dateitext: ein Kommentar, der
    ``time.sleep`` erwaehnt, um zu erklaeren WARUM hier nicht geschlafen wird,
    ist kein Schlaf. Ein Test, der Prosa mitliest, misst die Dokumentation.
    """
    import ast

    import app.ai.retry as retry

    baum = ast.parse(Path(retry.__file__ or "").read_text(encoding="utf-8"))
    importiert = {
        alias.name.split(".")[0]
        for knoten in ast.walk(baum)
        if isinstance(knoten, (ast.Import, ast.ImportFrom))
        for alias in knoten.names
        if isinstance(knoten, ast.Import)
    } | {
        knoten.module.split(".")[0]
        for knoten in ast.walk(baum)
        if isinstance(knoten, ast.ImportFrom) and knoten.module
    }
    assert "time" not in importiert, "keine Uhr"
    assert "asyncio" not in importiert, "kein Schlaf"

    aufrufe = {
        ast.unparse(knoten.func) for knoten in ast.walk(baum) if isinstance(knoten, ast.Call)
    }
    assert not {name for name in aufrufe if name.endswith("sleep")}, aufrufe


def test_unbekannte_kosten_bleiben_ueber_wiederholungen_unbekannt() -> None:
    from app.ai.models import total_cost_usd

    versuche = [
        AttemptTrace("litellm", "kai-standard", 1.0, cost_usd=0.001, error_class="timeout"),
        AttemptTrace("litellm", "kai-standard", 1.0, cost_usd=None),
    ]
    assert total_cost_usd(versuche) is None, "ein bekannter Versuch macht den anderen nicht bekannt"
