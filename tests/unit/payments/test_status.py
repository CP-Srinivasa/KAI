"""State Machine des Payment Control Plane (ADR 0017 §4).

Die Matrix ist hier NICHT aus ``app.payments.status`` importiert, sondern
zweitgeschrieben. Ein Test, der die Tabelle des Prueflings gegen sich selbst
haelt, prueft nur, dass Python Mengen vergleichen kann.

Der Kern der Pruefung ist nicht die Tabelle, sondern die Beweislast:
``SETTLED`` und ``FAILED_FINAL`` aus einem Zustand, in dem ein Send bereits
draussen war, brauchen Node-Evidenz. Genau diese Beweislast fehlte im Bestand
— der 25k-Spend vom 07-02 stand als ``error`` im Journal, waehrend die
Kanalbilanzen ihn als bezahlt auswiesen.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.payments.enums import PaymentStatus, RailOutcome
from app.payments.models import Proof, ProofKind
from app.payments.status import (
    TERMINAL_STATES,
    TRANSITIONS,
    RailEvidence,
    TransitionEvidence,
    classify_rail_outcome,
    transition,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

#: ADR §4, unabhaengig abgeschrieben.
EXPECTED: dict[str, set[str]] = {
    "REQUESTED": {"DENIED", "AWAITING_APPROVAL", "AUTHORIZED"},
    "AWAITING_APPROVAL": {"AUTHORIZED", "CANCELLED", "EXPIRED"},
    "AUTHORIZED": {"SUBMITTED", "EXPIRED", "CANCELLED"},
    "SUBMITTED": {
        "IN_FLIGHT",
        "SETTLED",
        "SETTLED_REVERSIBLE",
        "FAILED_FINAL",
        "RECONCILIATION_REQUIRED",
    },
    "IN_FLIGHT": {
        "SETTLED",
        "SETTLED_REVERSIBLE",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "RECONCILIATION_REQUIRED",
    },
    "FAILED_RETRYABLE": {"AUTHORIZED", "FAILED_FINAL"},
    "RECONCILIATION_REQUIRED": {"SETTLED", "FAILED_FINAL", "RECONCILIATION_REQUIRED"},
    "SETTLED_REVERSIBLE": {"SETTLED", "REVERSED"},
    "DENIED": set(),
    "SETTLED": set(),
    "REVERSED": set(),
    "FAILED_FINAL": set(),
    "EXPIRED": set(),
    "CANCELLED": set(),
}

EXPECTED_TERMINAL = {"DENIED", "SETTLED", "REVERSED", "FAILED_FINAL", "EXPIRED", "CANCELLED"}

# SORTIERT, nicht in Set-Reihenfolge: pytest-xdist verteilt anhand der
# Reihenfolge der gesammelten Tests, und die Iteration ueber ein ``set`` haengt
# an PYTHONHASHSEED. Jeder Worker haette sonst eine andere Liste gesammelt und
# die Verteilung waere mit "Different tests were collected" abgebrochen.
ALL_PAIRS = sorted((a, b) for a in EXPECTED for b in EXPECTED)
ALLOWED_PAIRS = sorted((a, b) for a, targets in EXPECTED.items() for b in targets)
FORBIDDEN_PAIRS = [pair for pair in ALL_PAIRS if pair not in set(ALLOWED_PAIRS)]


def node_evidence(status: str = "SUCCEEDED") -> TransitionEvidence:
    return TransitionEvidence(
        actor="reconciler",
        reason="rail lookup",
        occurred_at=NOW,
        rail_evidence=RailEvidence(
            source="rail_lookup",
            rail="lightning",
            rail_dedup_key="a" * 64,
            observed_status=status,
            proof=Proof(kind=ProofKind.PREIMAGE, ref_hash="b" * 64),
            observed_at=NOW,
        ),
    )


def bare_evidence() -> TransitionEvidence:
    return TransitionEvidence(actor="operator", reason="manual", occurred_at=NOW)


# --------------------------------------------------------------------------- #
# Tabelle
# --------------------------------------------------------------------------- #


def test_transition_table_matches_the_adr() -> None:
    actual = {state.value: {t.value for t in targets} for state, targets in TRANSITIONS.items()}
    assert actual == EXPECTED


def test_every_status_appears_in_the_table() -> None:
    assert {s.value for s in PaymentStatus} == set(EXPECTED)


def test_terminal_states_match_the_adr() -> None:
    assert {s.value for s in TERMINAL_STATES} == EXPECTED_TERMINAL


def test_terminal_states_have_no_outgoing_edge() -> None:
    for state in TERMINAL_STATES:
        assert TRANSITIONS[state] == frozenset()


@pytest.mark.parametrize(("source", "target"), ALLOWED_PAIRS)
def test_allowed_transition_is_accepted(source: str, target: str) -> None:
    result = transition(PaymentStatus(source), PaymentStatus(target), evidence=node_evidence())
    assert result is PaymentStatus(target)


@pytest.mark.parametrize(("source", "target"), FORBIDDEN_PAIRS)
def test_forbidden_transition_is_refused(source: str, target: str) -> None:
    with pytest.raises(ValueError, match="illegal transition"):
        transition(PaymentStatus(source), PaymentStatus(target), evidence=node_evidence())


# --------------------------------------------------------------------------- #
# Beweislast
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("source", ["SUBMITTED", "IN_FLIGHT", "RECONCILIATION_REQUIRED"])
@pytest.mark.parametrize("target", ["SETTLED", "FAILED_FINAL"])
def test_terminal_money_verdict_requires_node_evidence(source: str, target: str) -> None:
    with pytest.raises(ValueError, match="rail evidence"):
        transition(PaymentStatus(source), PaymentStatus(target), evidence=bare_evidence())


@pytest.mark.parametrize("source", ["SUBMITTED", "IN_FLIGHT", "RECONCILIATION_REQUIRED"])
@pytest.mark.parametrize("target", ["SETTLED", "FAILED_FINAL"])
def test_terminal_money_verdict_passes_with_node_evidence(source: str, target: str) -> None:
    assert transition(
        PaymentStatus(source), PaymentStatus(target), evidence=node_evidence()
    ) is PaymentStatus(target)


def test_settled_reversible_also_requires_evidence() -> None:
    with pytest.raises(ValueError, match="rail evidence"):
        transition(
            PaymentStatus.SUBMITTED,
            PaymentStatus.SETTLED_REVERSIBLE,
            evidence=bare_evidence(),
        )


def test_failed_retryable_requires_evidence_that_nothing_moved() -> None:
    """ADR §4: Retry nur, wenn der Rail beweist "nichts bewegt"."""
    with pytest.raises(ValueError, match="rail evidence"):
        transition(
            PaymentStatus.IN_FLIGHT, PaymentStatus.FAILED_RETRYABLE, evidence=bare_evidence()
        )


def test_reconciliation_required_needs_no_evidence() -> None:
    """Der Weg ins Unbekannte ist immer erlaubt — sonst gibt es keinen sicheren Hafen."""
    assert (
        transition(
            PaymentStatus.SUBMITTED,
            PaymentStatus.RECONCILIATION_REQUIRED,
            evidence=bare_evidence(),
        )
        is PaymentStatus.RECONCILIATION_REQUIRED
    )


def test_reconciliation_may_stay_unresolved() -> None:
    assert (
        transition(
            PaymentStatus.RECONCILIATION_REQUIRED,
            PaymentStatus.RECONCILIATION_REQUIRED,
            evidence=bare_evidence(),
        )
        is PaymentStatus.RECONCILIATION_REQUIRED
    )


def test_pre_send_transitions_need_no_node_evidence() -> None:
    for source, target in (
        ("REQUESTED", "AUTHORIZED"),
        ("REQUESTED", "DENIED"),
        ("AWAITING_APPROVAL", "AUTHORIZED"),
        ("AUTHORIZED", "SUBMITTED"),
    ):
        assert transition(
            PaymentStatus(source), PaymentStatus(target), evidence=bare_evidence()
        ) is PaymentStatus(target)


def test_evidence_must_be_supplied() -> None:
    with pytest.raises(TypeError):
        transition(PaymentStatus.REQUESTED, PaymentStatus.AUTHORIZED)  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# classify_rail_outcome
# --------------------------------------------------------------------------- #


def test_settled_outcome_maps_to_settled() -> None:
    assert classify_rail_outcome(RailOutcome.SETTLED) is PaymentStatus.SETTLED


def test_settled_outcome_on_a_reversible_rail_stays_reversible() -> None:
    assert (
        classify_rail_outcome(RailOutcome.SETTLED, reversal_supported=True)
        is PaymentStatus.SETTLED_REVERSIBLE
    )


def test_failed_outcome_maps_to_failed_final() -> None:
    assert classify_rail_outcome(RailOutcome.FAILED) is PaymentStatus.FAILED_FINAL


def test_in_flight_outcome_maps_to_in_flight() -> None:
    assert classify_rail_outcome(RailOutcome.IN_FLIGHT) is PaymentStatus.IN_FLIGHT


@pytest.mark.parametrize(
    "outcome",
    [RailOutcome.UNKNOWN, "timeout", "transport_error", "", "voellig_neuer_zustand", None],
)
def test_anything_unproven_maps_to_reconciliation_required(outcome: object) -> None:
    """Timeout, Transportfehler und jeder unbekannte Wert sind KEIN Fehlschlag."""
    assert classify_rail_outcome(outcome) is PaymentStatus.RECONCILIATION_REQUIRED


def test_unknown_never_becomes_failed() -> None:
    for outcome in (RailOutcome.UNKNOWN, "timeout", "transport_error"):
        assert classify_rail_outcome(outcome) is not PaymentStatus.FAILED_FINAL
        assert classify_rail_outcome(outcome) is not PaymentStatus.FAILED_RETRYABLE


# --------------------------------------------------------------------------- #
# Einzige Vergabestelle
# --------------------------------------------------------------------------- #


def test_status_is_only_assigned_inside_status_module() -> None:
    """ADR §4: genau EINE Vergabestelle.

    Ein ``status=PaymentStatus.X`` irgendwo sonst im Paket waere ein zweiter
    Weg an der Beweislast vorbei — genau die Bauart, mit der der Bestand drei
    ueberlappende Zustandsvokabulare bekommen hat.
    """
    package = Path(__file__).resolve().parents[3] / "app" / "payments"
    offenders: list[str] = []
    for path in sorted(package.rglob("*.py")):
        if path.name in {"status.py", "models.py", "enums.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg != "status":
                continue
            value = node.value
            is_enum_literal = (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "PaymentStatus"
            )
            if is_enum_literal:
                offenders.append(f"{path.name}:{node.value.lineno}")
    assert not offenders, (
        "Status wird ausserhalb von status.py vergeben — jede Vergabe gehoert "
        f"durch transition(): {offenders}"
    )


def test_reversal_also_requires_evidence() -> None:
    """Auch eine Rueckbuchung ist eine Aussage ueber Geld — Lightning kennt sie
    nicht, ein spaeterer Rail mit ``reversal_supported`` schon."""
    with pytest.raises(ValueError, match="rail evidence"):
        transition(
            PaymentStatus.SETTLED_REVERSIBLE, PaymentStatus.REVERSED, evidence=bare_evidence()
        )
