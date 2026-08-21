r"""Der Candidate wird JETZT geschlossen — die Uhr startet spaeter.

Der uebliche Fehler waere, heute eine Datei anzulegen und spaeter ``T0``
hineinzueditieren, um dann zu behaupten, es sei dieselbe Praeregistrierung.
Eine Datei, die sich noch aendert, ist nicht versiegelt. Deshalb zwei
unveraenderliche Artefakte, die eine Kette bilden: der Candidate traegt alle
Forschungsparameter und **kein Feld fuer T0**, das Activation-Record verweist
auf dessen Hash und setzt die Uhr.

Der zweite Gegenstand dieser Datei ist optional stopping. Es sieht harmlos aus::

    Tag 61:  n_valid = 104,  G = 53   ->  "reif! jetzt auswerten"

Wer auswertet, sobald es reif *und* huebsch ist, hat die
Irrtumswahrscheinlichkeit still erhoeht. T1 bleibt der erste
Entscheidungszeitpunkt, und nach einer Verlaengerung wird bis T2 nicht mehr
hingesehen — sonst waere die Verlaengerung nur eine zweite Gelegenheit.
"""

from __future__ import annotations

import pytest

from app.research.prereg_candidate import (
    PREREG_ECONOMIC_FLOOR_BPS,
    PREREG_ROUND_TRIP_COST_BPS,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    PreRegCandidate,
    activate,
    activation_sha256,
    build_rsi_reentry_volume_candidate,
    candidate_sha256,
    candidate_to_dict,
)
from app.research.prereg_window import (
    ACTION_CLOSED,
    ACTION_EVALUATE,
    ACTION_EXTEND_TO_T2,
    ACTION_INCONCLUSIVE,
    ACTION_WAIT,
    MaturityCounts,
    PrematureEvaluationError,
    assert_evaluable,
    decide_window_action,
)

_UNIVERSE_SHA = "d28e10d5ba2e11b1f541c9d2cd17e1219b92107c1b307441e5353ea05ac3f03e"
_START = "2026-09-01T00:00:00+00:00"
# Formatgueltige Platzhalter: ``activate`` verlangt einen 40-Hex-Commit und
# einen 64-Hex-Evaluator-Hash, damit im Record keine Schein-Beweiskette steht.
_CODE_SHA = "9d1502dc7c6f4f2b1a3e5c7d9b0f2a4c6e8d0b2f"
_EVALUATOR_SHA = "a" * 64
_T1 = "2026-11-30T00:00:00+00:00"  # +90d
_T2 = "2027-02-28T00:00:00+00:00"  # +180d


def _candidate() -> PreRegCandidate:
    return build_rsi_reentry_volume_candidate(_UNIVERSE_SHA, 34)


def _counts(n_valid: int, n_clusters: int) -> MaturityCounts:
    return MaturityCounts(n_valid=n_valid, n_clusters=n_clusters)


# ── Der Candidate ───────────────────────────────────────────────────────────


def test_candidate_carries_the_operator_approved_parameters() -> None:
    candidate = _candidate()

    assert candidate.n_valid_min == 100
    assert candidate.cluster_min == 50
    assert candidate.t1_offset_days == 90
    assert candidate.t2_offset_days == 180
    assert candidate.alpha == 0.05
    assert candidate.round_trip_cost_bps == 20.0
    assert candidate.economic_floor_bps == 5.0
    assert candidate.universe_sha256 == _UNIVERSE_SHA
    assert candidate.n_symbols == 34
    assert candidate.status == STATUS_CANDIDATE


def test_the_economic_requirement_is_gross_25_bps() -> None:
    """Kosten UND Huerde sind versiegelt — zusammen sind sie die eigentliche Latte."""
    assert PREREG_ROUND_TRIP_COST_BPS + PREREG_ECONOMIC_FLOOR_BPS == 25.0


def test_candidate_has_no_field_for_t0_at_all() -> None:
    """Was nicht existiert, kann nicht stillschweigend nachgetragen werden."""
    fields = set(PreRegCandidate.__dataclass_fields__)

    assert "t0" not in fields
    assert "t0_utc" not in fields
    assert not any(name.startswith("t0") for name in fields)


def test_candidate_hash_is_deterministic() -> None:
    assert candidate_sha256(_candidate()) == candidate_sha256(_candidate())


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("n_valid_min", 101),
        ("cluster_min", 49),
        ("t1_offset_days", 91),
        ("t2_offset_days", 181),
        ("alpha", 0.10),
        ("round_trip_cost_bps", 25.0),
        ("economic_floor_bps", 0.0),
        ("universe_sha256", "deadbeef"),
        ("horizon", 5),
    ],
)
def test_every_parameter_is_inside_the_hash(field_name: str, value: object) -> None:
    """Eine geaenderte Zahl muss ein ANDERER Candidate sein — sonst ist der Seal Zierde."""
    from dataclasses import replace

    changed = replace(_candidate(), **{field_name: value})

    assert candidate_sha256(changed) != candidate_sha256(_candidate())


def test_the_sealed_policies_are_inside_the_hash_too() -> None:
    """Die DATA_UNAVAILABLE-Regel ist Kriterium, nicht Beiwerk."""
    from dataclasses import replace

    weakened = replace(
        _candidate(),
        data_unavailable_policy={"asset_substitution": "allowed"},
    )

    assert candidate_sha256(weakened) != candidate_sha256(_candidate())


def test_sealed_policy_forbids_substitution_and_zero_filling() -> None:
    policy = _candidate().data_unavailable_policy

    assert policy["asset_substitution"] == "forbidden"
    assert policy["zero_filling"] == "forbidden"
    assert "forbidden" in policy["unavailable_as_no_signal"]
    assert "NOT_VALID" in policy["fire_without_label"]
    assert "immutable" in policy["canonical_universe_membership"]


def test_rename_continuity_is_narrow_and_objective() -> None:
    """Identitaetsfortsetzung ist keine Substitution — aber nur unter engen Bedingungen."""
    rule = _candidate().data_unavailable_policy["canonical_rename_continuity"]

    assert "nicht mehr TRADING" in rule
    assert "1:1" in rule
    assert "Keine heuristische Alias-Suche" in rule
    assert "Keine Entscheidung nach Performance" in rule


def test_candidate_serialises_without_tuples_leaking() -> None:
    payload = candidate_to_dict(_candidate())

    assert isinstance(payload["sensitivity_cost_bps"], list)
    assert payload["sensitivity_cost_bps"] == [20.0, 25.0, 30.0]
    assert "result_without_largest_cluster" in payload["robustness_diagnostics"]
    assert "data_unavailable_count" in payload["mandatory_disclosure"]


# ── Die Aktivierung ─────────────────────────────────────────────────────────


def test_activation_derives_t1_and_t2_from_the_sealed_offsets() -> None:
    """T1/T2 werden abgeleitet, nicht eingegeben — sonst waeren sie verhandelbar."""
    activation = activate(
        _candidate(),
        t0_utc=_START,
        research_code_sha=_CODE_SHA,
        evaluator_sha256=_EVALUATOR_SHA,
        operator_approved=True,
    )

    assert activation.t0_utc.startswith("2026-09-01")
    assert activation.t1_utc.startswith("2026-11-30")
    assert activation.t2_utc.startswith("2027-02-28")
    assert activation.status == STATUS_ACTIVE


def test_activation_links_to_the_candidate_hash() -> None:
    """Die Kette: ohne diesen Verweis waere hinterher unklar, WAS aktiviert wurde."""
    candidate = _candidate()

    activation = activate(
        candidate,
        t0_utc=_START,
        research_code_sha=_CODE_SHA,
        evaluator_sha256=_EVALUATOR_SHA,
        operator_approved=True,
    )

    assert activation.candidate_sha256 == candidate_sha256(candidate)
    assert activation.universe_sha256 == candidate.universe_sha256
    assert len(activation_sha256(activation)) == 64


def test_activation_requires_explicit_operator_approval() -> None:
    """Eine Praeregistrierung aktiviert sich nicht selbst."""
    with pytest.raises(ValueError, match="operator approval"):
        activate(
            _candidate(),
            t0_utc=_START,
            research_code_sha="abc",
            evaluator_sha256="def",
            operator_approved=False,
        )


def test_activation_requires_both_shas() -> None:
    """Ohne Code- und Evaluator-SHA ist nicht feststellbar, WAS gemessen hat."""
    for code, evaluator in (("", "def"), ("abc", "")):
        with pytest.raises(ValueError, match="mandatory"):
            activate(
                _candidate(),
                t0_utc=_START,
                research_code_sha=code,
                evaluator_sha256=evaluator,
                operator_approved=True,
            )


def test_an_already_active_record_cannot_be_activated_again() -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="expected CANDIDATE"):
        activate(
            replace(_candidate(), status=STATUS_ACTIVE),
            t0_utc=_START,
            research_code_sha="abc",
            evaluator_sha256="def",
            operator_approved=True,
        )


def test_activation_does_not_mutate_the_candidate() -> None:
    candidate = _candidate()
    before = candidate_sha256(candidate)

    activate(
        candidate,
        t0_utc=_START,
        research_code_sha=_CODE_SHA,
        evaluator_sha256=_EVALUATOR_SHA,
        operator_approved=True,
    )

    assert candidate_sha256(candidate) == before


# ── Die Fensterregel ────────────────────────────────────────────────────────


def test_maturity_reached_early_still_waits_for_t1() -> None:
    """DER Optional-Stopping-Test: Tag 61, n=104, G=53 — und trotzdem WAIT.

    Wer hier auswertet, hat nicht frueher recht, sondern einen anderen Test
    gemacht.
    """
    decision = decide_window_action(
        now_utc="2026-11-01T00:00:00+00:00",  # ~Tag 61
        t1_utc=_T1,
        t2_utc=_T2,
        counts=_counts(104, 53),
        n_valid_min=100,
        cluster_min=50,
    )

    assert decision.action == ACTION_WAIT
    assert decision.mature is True
    assert not decision.may_evaluate
    assert "erste Entscheidungszeitpunkt" in " ".join(decision.reasons)


def test_mature_at_t1_evaluates_once() -> None:
    decision = decide_window_action(
        now_utc=_T1,
        t1_utc=_T1,
        t2_utc=_T2,
        counts=_counts(146, 75),
        n_valid_min=100,
        cluster_min=50,
    )

    assert decision.action == ACTION_EVALUATE
    assert decision.may_evaluate


def test_immature_at_t1_extends_without_looking_at_performance() -> None:
    decision = decide_window_action(
        now_utc=_T1,
        t1_utc=_T1,
        t2_utc=_T2,
        counts=_counts(80, 41),
        n_valid_min=100,
        cluster_min=50,
    )

    assert decision.action == ACTION_EXTEND_TO_T2
    assert not decision.may_evaluate
    assert "KEINE Performance" in " ".join(decision.reasons)


def test_only_one_of_the_two_thresholds_missing_is_enough_to_extend() -> None:
    """Beide Schranken sind bindend — sonst waere eine davon Dekoration."""
    decision = decide_window_action(
        now_utc=_T1,
        t1_utc=_T1,
        t2_utc=_T2,
        counts=_counts(146, 41),  # Signale reichen, Cluster nicht
        n_valid_min=100,
        cluster_min=50,
    )

    assert decision.action == ACTION_EXTEND_TO_T2
    assert any("cluster_min" in r for r in decision.reasons)


def test_after_an_extension_it_waits_until_t2_even_when_mature() -> None:
    """Sonst waere die Verlaengerung nur eine zweite Gelegenheit zum Hinsehen."""
    decision = decide_window_action(
        now_utc="2026-12-20T00:00:00+00:00",  # zwischen T1 und T2
        t1_utc=_T1,
        t2_utc=_T2,
        counts=_counts(200, 90),
        n_valid_min=100,
        cluster_min=50,
        t1_outcome=ACTION_EXTEND_TO_T2,
    )

    assert decision.action == ACTION_WAIT
    assert decision.mature is True
    assert not decision.may_evaluate


def test_mature_at_t2_evaluates() -> None:
    decision = decide_window_action(
        now_utc=_T2,
        t1_utc=_T1,
        t2_utc=_T2,
        counts=_counts(120, 62),
        n_valid_min=100,
        cluster_min=50,
        t1_outcome=ACTION_EXTEND_TO_T2,
    )

    assert decision.action == ACTION_EVALUATE


def test_immature_at_t2_is_inconclusive_never_not_met() -> None:
    """Die ND-v2-Lektion, hier am Fristende."""
    decision = decide_window_action(
        now_utc=_T2,
        t1_utc=_T1,
        t2_utc=_T2,
        counts=_counts(88, 44),
        n_valid_min=100,
        cluster_min=50,
        t1_outcome=ACTION_EXTEND_TO_T2,
    )

    assert decision.action == ACTION_INCONCLUSIVE
    assert "NOT_MET" in " ".join(decision.reasons)


def test_a_verdict_at_t1_closes_the_experiment() -> None:
    """Genau ein konfirmatorischer Lauf. Kein zweiter bei T2."""
    decision = decide_window_action(
        now_utc=_T2,
        t1_utc=_T1,
        t2_utc=_T2,
        counts=_counts(300, 150),
        n_valid_min=100,
        cluster_min=50,
        t1_outcome=ACTION_EVALUATE,
    )

    assert decision.action == ACTION_CLOSED
    assert not decision.may_evaluate


@pytest.mark.parametrize(
    "action", [ACTION_WAIT, ACTION_EXTEND_TO_T2, ACTION_INCONCLUSIVE, ACTION_CLOSED]
)
def test_the_gate_refuses_every_non_evaluate_action(action: str) -> None:
    """Fail-closed: ein p-Wert, den niemand sehen durfte, laesst sich nicht zurueckziehen."""
    from app.research.prereg_window import WindowDecision

    decision = WindowDecision(action=action, checkpoint="X", mature=True, counts=_counts(200, 100))

    with pytest.raises(PrematureEvaluationError):
        assert_evaluable(decision)


def test_maturity_counts_cannot_carry_performance() -> None:
    """Strukturell, nicht nur als Vorsatz: hier gibt es kein Feld dafuer."""
    fields = set(MaturityCounts.__dataclass_fields__)

    for forbidden in ("mean_bps", "p_value", "hit_rate", "mean", "edge"):
        assert forbidden not in fields


# ── Das committete Candidate-Artefakt ───────────────────────────────────────

_CANDIDATE_PATH = "docs/research/prereg_rsi_reentry_volume_v1_candidate.json"
_UNIVERSE_PATH = "docs/research/universe_rsi_reentry_v1.json"


def _repo_root():
    from pathlib import Path as _Path

    return _Path(__file__).resolve().parents[2]


def _load(relative: str) -> dict:
    import json

    return json.loads((_repo_root() / relative).read_text(encoding="utf-8"))


def test_committed_candidate_matches_its_own_hash() -> None:
    """Wer eine Zahl editiert, muss den Hash mitziehen — und wird dabei gesehen."""
    payload = _load(_CANDIDATE_PATH)

    rebuilt = build_rsi_reentry_volume_candidate(payload["universe_sha256"], payload["n_symbols"])

    assert payload["candidate_sha256"] == candidate_sha256(rebuilt)


def test_committed_candidate_matches_the_code_that_built_it() -> None:
    payload = _load(_CANDIDATE_PATH)
    expected = candidate_to_dict(
        build_rsi_reentry_volume_candidate(payload["universe_sha256"], payload["n_symbols"])
    )

    for key, value in expected.items():
        assert payload[key] == value, key


def test_committed_candidate_is_locked_and_has_no_clock() -> None:
    payload = _load(_CANDIDATE_PATH)

    assert payload["status"] == STATUS_CANDIDATE
    assert payload["t0_utc"] == "NOT_SET"


def test_the_chain_to_the_sealed_universe_holds() -> None:
    """Der Candidate muss auf GENAU das versiegelte Universum zeigen.

    Ohne diese Verkettung koennte das Universum spaeter neu erzeugt werden und
    niemand saehe, dass die Praeregistrierung auf ein anderes zeigt.
    """
    candidate = _load(_CANDIDATE_PATH)
    universe = _load(_UNIVERSE_PATH)

    assert candidate["universe_sha256"] == universe["universe_sha256"]
    assert candidate["n_symbols"] == universe["n_symbols"] == 34


def test_activation_preconditions_are_operational_not_statistical() -> None:
    """Die Uhr startet erst, wenn die Betriebslage sauber und beweisbar ist.

    Das ist ausdruecklich KEIN statistischer Vorbehalt — die Forschungsparameter
    sind bereits geschlossen, bevor irgendein Outcome gesehen wurde.
    """
    preconditions = " ".join(_load(_CANDIDATE_PATH)["activation_preconditions"]).lower()

    assert "broker" in preconditions
    assert "unit drift" in preconditions
    assert "hold" in preconditions
