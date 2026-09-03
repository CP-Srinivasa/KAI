"""Circuit und Budget (ADR 0017, Sprint 2).

Beide Dateien stehen gegen je einen namentlich benannten Defekt des ersten
LiteLLM-Anlaufs. Die Tests, die genau diese Defekte ausschliessen, sagen das im
Docstring — sonst ist in sechs Monaten nicht mehr erkennbar, welche Zeile eine
Zusicherung ist und welche nur Gewohnheit.
"""

from __future__ import annotations

import pytest

from app.ai.budget import (
    BudgetEntry,
    BudgetPolicy,
    accumulate,
    decide,
    headroom_usd,
)
from app.ai.circuit import (
    CircuitBook,
    CircuitKey,
    CircuitPolicy,
    circuit_key,
)
from app.ai.models import AttemptTrace

POLICY = CircuitPolicy(failure_threshold=3, cooldown_s=60.0)


def _attempt(**over: object) -> AttemptTrace:
    base: dict[str, object] = {
        "transport": "litellm",
        "requested_model": "fast",
        "latency_ms": 10.0,
        "actual_provider": "openai",
        "actual_model": "gpt-4o-mini",
    }
    base.update(over)
    return AttemptTrace(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Circuit — der Schlüssel ist dreiteilig, nicht Alias-only.
# --------------------------------------------------------------------------


def test_der_schluessel_nimmt_den_bewiesenen_upstream() -> None:
    key = circuit_key("standard", "fast", _attempt())
    assert key.upstream == "openai/gpt-4o-mini"
    assert key.precise


def test_ohne_bewiesene_identitaet_wird_der_alias_gesperrt() -> None:
    """Gröber, aber ehrlich — und als grob erkennbar."""
    key = circuit_key("standard", "fast", _attempt(actual_provider="", actual_model=""))
    assert key.upstream == ""
    assert not key.precise
    assert circuit_key("standard", "fast") == key


def test_ein_defekter_upstream_sperrt_die_alternativen_nicht() -> None:
    """Exakt der Defekt „alias-only Circuit-Breaker".

    Zwei Anbieter bedienen denselben Alias. Fällt einer aus, muss der andere
    weiter erreichbar sein — sonst nimmt ein einzelner kaputter Upstream genau
    die Ausweichwege mit, die es in dem Moment braucht.
    """
    kaputt = circuit_key("standard", "fast", _attempt())
    heil = circuit_key("standard", "fast", _attempt(actual_provider="gemini", actual_model="flash"))
    assert kaputt != heil

    book = CircuitBook()
    for _ in range(POLICY.failure_threshold):
        book = book.on_failure(kaputt, now_s=0.0, policy=POLICY)

    assert not book.allows(kaputt, now_s=0.0, policy=POLICY)
    assert book.allows(heil, now_s=0.0, policy=POLICY)
    assert book.open_keys(now_s=0.0, policy=POLICY) == (kaputt,)


def test_dieselbe_route_mit_anderem_alias_bleibt_offen() -> None:
    a = CircuitKey("standard", "fast", "openai/gpt-4o-mini")
    b = CircuitKey("standard", "gruendlich", "openai/gpt-4o-mini")
    book = CircuitBook()
    for _ in range(POLICY.failure_threshold):
        book = book.on_failure(a, now_s=0.0, policy=POLICY)
    assert not book.allows(a, now_s=0.0, policy=POLICY)
    assert book.allows(b, now_s=0.0, policy=POLICY)


def test_unter_der_schwelle_bleibt_der_kreis_geschlossen() -> None:
    key = CircuitKey("bulk", "fast", "openai/x")
    book = CircuitBook()
    for _ in range(POLICY.failure_threshold - 1):
        book = book.on_failure(key, now_s=0.0, policy=POLICY)
    assert book.state(key, now_s=0.0, policy=POLICY) == "closed"
    assert book.allows(key, now_s=0.0, policy=POLICY)


def test_erfolg_loescht_den_zaehler_vollstaendig() -> None:
    """Kein Rest-Zähler, der beim nächsten Fehlschlag sofort wieder öffnet."""
    key = CircuitKey("bulk", "fast", "openai/x")
    book = CircuitBook()
    for _ in range(POLICY.failure_threshold - 1):
        book = book.on_failure(key, now_s=0.0, policy=POLICY)
    book = book.on_success(key)
    assert book.records == {}
    book = book.on_failure(key, now_s=0.0, policy=POLICY)
    assert book.state(key, now_s=0.0, policy=POLICY) == "closed"


def test_nach_dem_cooldown_wird_genau_ein_versuch_erlaubt() -> None:
    """Ohne diese Begrenzung stürmt die volle Last gegen einen Upstream,
    der sich gerade erst erholt — und öffnet ihn sofort wieder."""
    key = CircuitKey("standard", "fast", "openai/x")
    book = CircuitBook()
    for _ in range(POLICY.failure_threshold):
        book = book.on_failure(key, now_s=0.0, policy=POLICY)

    assert book.state(key, now_s=59.9, policy=POLICY) == "open"
    assert not book.allows(key, now_s=59.9, policy=POLICY)

    assert book.state(key, now_s=60.0, policy=POLICY) == "half_open"
    assert book.allows(key, now_s=60.0, policy=POLICY)

    book = book.on_attempt(key, now_s=60.0, policy=POLICY)
    assert not book.allows(key, now_s=60.0, policy=POLICY), "nur EINE Probe"


def test_die_probe_entscheidet_in_beide_richtungen() -> None:
    key = CircuitKey("standard", "fast", "openai/x")
    offen = CircuitBook()
    for _ in range(POLICY.failure_threshold):
        offen = offen.on_failure(key, now_s=0.0, policy=POLICY)
    probe = offen.on_attempt(key, now_s=60.0, policy=POLICY)

    geheilt = probe.on_success(key)
    assert geheilt.state(key, now_s=60.0, policy=POLICY) == "closed"

    wieder_zu = probe.on_failure(key, now_s=60.0, policy=POLICY)
    assert wieder_zu.state(key, now_s=60.0, policy=POLICY) == "open"
    assert not wieder_zu.allows(key, now_s=60.0, policy=POLICY)


def test_das_buch_ist_unveraenderlich() -> None:
    """Übergänge geben Neues zurück — sonst teilt sich Zustand über Aufrufe hinweg."""
    key = CircuitKey("bulk", "fast", "openai/x")
    leer = CircuitBook()
    danach = leer.on_failure(key, now_s=0.0, policy=POLICY)
    assert leer.records == {}
    assert danach is not leer


# --------------------------------------------------------------------------
# Budget — verbucht wird, was gemessen wurde.
# --------------------------------------------------------------------------


def test_unbekannte_kosten_werden_gezaehlt_nicht_genullt() -> None:
    """Der Defekt „Tagesbudget aus lauter Nullen", direkt adressiert."""
    state = accumulate(
        [
            BudgetEntry("standard", 0.01),
            BudgetEntry("standard", None),
            BudgetEntry("bulk", 0.02),
        ]
    )
    assert state.booked_usd == pytest.approx(0.03)
    assert state.known_calls == 2
    assert state.unknown_calls == 1
    assert not state.fully_accounted
    assert state.cost_known_rate == pytest.approx(2 / 3)


def test_ein_leeres_fenster_ist_nicht_vollstaendig_belegt() -> None:
    leer = accumulate([])
    assert leer.booked_usd == 0.0
    assert leer.cost_known_rate is None
    assert not leer.fully_accounted


def test_ohne_schaetzung_wird_nicht_hart_abgelehnt() -> None:
    """Wer ohne Zahlen ablehnt, lehnt nach Gefühl ab."""
    daily = accumulate([BudgetEntry("standard", 0.10)])
    entscheidung = decide(
        daily=daily,
        monthly=accumulate([]),
        policy=BudgetPolicy(daily_limit_usd=1.0),
        estimated_request_cost_usd=None,
    )
    assert entscheidung == "allow_unbudgeted"


def test_mit_schaetzung_ueber_dem_limit_wird_abgelehnt() -> None:
    daily = accumulate([BudgetEntry("standard", 0.95)])
    assert (
        decide(
            daily=daily,
            monthly=accumulate([]),
            policy=BudgetPolicy(daily_limit_usd=1.0),
            estimated_request_cost_usd=0.10,
        )
        == "reject"
    )


def test_ein_bereits_erreichtes_limit_lehnt_auch_ohne_schaetzung_ab() -> None:
    """Verbuchte Kosten allein reichen — hier ist der Beleg schon da."""
    daily = accumulate([BudgetEntry("standard", 1.0)])
    assert (
        decide(
            daily=daily,
            monthly=accumulate([]),
            policy=BudgetPolicy(daily_limit_usd=1.0),
            estimated_request_cost_usd=None,
        )
        == "reject"
    )


def test_das_monatslimit_greift_unabhaengig_vom_tag() -> None:
    assert (
        decide(
            daily=accumulate([BudgetEntry("bulk", 0.01)]),
            monthly=accumulate([BudgetEntry("bulk", 50.0)]),
            policy=BudgetPolicy(daily_limit_usd=10.0, monthly_limit_usd=50.0),
            estimated_request_cost_usd=0.01,
        )
        == "reject"
    )


def test_ohne_limit_gibt_es_nichts_zu_ueberschreiten() -> None:
    assert (
        decide(
            daily=accumulate([BudgetEntry("bulk", 999.0)]),
            monthly=accumulate([BudgetEntry("bulk", 999.0)]),
            policy=BudgetPolicy(),
            estimated_request_cost_usd=None,
        )
        == "allow"
    )


def test_unbelegte_aufrufe_machen_aus_allow_ein_allow_unbudgeted() -> None:
    """„Wir wissen es nicht" darf nicht als „alles in Ordnung" protokolliert werden."""
    daily = accumulate([BudgetEntry("standard", 0.01), BudgetEntry("standard", None)])
    assert (
        decide(
            daily=daily,
            monthly=accumulate([]),
            policy=BudgetPolicy(daily_limit_usd=10.0),
            estimated_request_cost_usd=0.01,
        )
        == "allow_unbudgeted"
    )


def test_vollstaendig_belegt_und_im_rahmen_ist_schlicht_allow() -> None:
    daily = accumulate([BudgetEntry("standard", 0.01)])
    assert (
        decide(
            daily=daily,
            monthly=accumulate([BudgetEntry("standard", 0.01)]),
            policy=BudgetPolicy(daily_limit_usd=10.0, monthly_limit_usd=100.0),
            estimated_request_cost_usd=0.01,
        )
        == "allow"
    )


def test_headroom_ist_none_sobald_etwas_unbelegt_ist() -> None:
    """Eine Restgrösse auszuweisen wäre eine Genauigkeit, die es nicht gibt."""
    assert headroom_usd(accumulate([BudgetEntry("bulk", 1.0)]), 10.0) == pytest.approx(9.0)
    assert headroom_usd(accumulate([BudgetEntry("bulk", None)]), 10.0) is None
    assert headroom_usd(accumulate([BudgetEntry("bulk", 1.0)]), None) is None
    assert headroom_usd(accumulate([BudgetEntry("bulk", 99.0)]), 10.0) == 0.0


def test_budget_eintrag_uebernimmt_unbekannt_aus_dem_versuch() -> None:
    unbekannt = BudgetEntry.from_attempt("bulk", _attempt(cost_usd=None))
    assert unbekannt.cost_usd is None
    bekannt = BudgetEntry.from_attempt("bulk", _attempt(cost_usd=0.5))
    assert bekannt.cost_usd == 0.5
