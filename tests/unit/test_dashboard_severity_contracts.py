"""STAB-2026-09-01 §8/§28/§31 — immaturity and negative evidence are not alarms.

Two of the brief's items turned out to be ALREADY CORRECT when measured against
the live system. They are pinned here so that stays true, because both are the
kind of thing that silently regresses:

  §28  an elapsed, unreplaced re-entry target (2026-05-16) is not an
       infrastructure alarm. REVIDIERT am 2026-09-09 (Operator-Entscheid): es ist
       aber auch kein neutraler "Konfiguration ausstehend"-Zustand. Es ist das
       Fehlen einer Freigabe, und es heisst jetzt so:
       ``no_current_authorization``. §28 bleibt gueltig in dem, was es meinte —
       kein Alarm, kein Systemdefekt. Was faellt, ist die Entwarnung: "kein
       Fehler" stand da, wo "keine Freigabe" hingehoert. Siehe
       tests/unit/test_reentry_no_current_authorization.py.
  §8   with zero trusted sources, no source is punished. All 12 scored sources
       carry ``priority_modifier`` 0 on the live Pi report, and the eligibility
       consumer receives an empty modifier map. Fail-closed means neutral here,
       not negative.

§31 is the rule both express: negative performance and immature evidence are not
infrastructure alarms.
"""

from __future__ import annotations

import pytest

from app.alerts.hold_metrics import GATE_IMMATURE_REASONS, GATE_LOW_WILSON, GATE_PASS


# --------------------------------------------------------------------------
# §28 — an archived target is INFO, not a warning
# --------------------------------------------------------------------------
def _reentry(target_date: str):
    from app.api.routers.dashboard import _reentry_status

    return _reentry_status(target_date=target_date)


def test_an_elapsed_target_is_not_an_infrastructure_error() -> None:
    """§28 in dem, was es meinte: kein Defekt, kein Alarm.

    Was 2026-09-09 faellt, ist nur die Entwarnung im Text — nicht die
    Einstufung als Nicht-Alarm.
    """
    state = _reentry("2026-05-16")
    assert state["days_delta"] < 0
    assert "expired" not in state["status"]
    assert "error" not in state["status"]


def test_an_elapsed_target_names_the_missing_authorization() -> None:
    """Und §28 deckt NICHT, den fehlenden Freigabezustand zu verschweigen."""
    state = _reentry("2026-05-16")
    assert state["status"] == "no_current_authorization"
    assert state["grants_execution_authorization"] is False
    warning = (state.get("warning") or "").lower()
    assert "kein fehler" not in warning, (
        "Die Entwarnung ist der Fehler: ein abgelaufenes Gate ohne Nachfolger "
        "ist keine ausstehende Konfiguration, sondern eine fehlende Freigabe."
    )


def test_a_future_target_is_a_running_target_not_a_clearance() -> None:
    """NEGATIVE CONTROL: der abgelaufene Zweig darf kein laufendes Ziel schlucken."""
    state = _reentry("2099-12-01")
    assert state["status"] == "active_target"
    assert state["days_delta"] > 0
    assert state["warning"] is None
    # Auch ein laufendes Sammelziel ist keine Ausfuehrungsfreigabe.
    assert state["grants_execution_authorization"] is False


def test_an_unparseable_target_does_not_pass_either() -> None:
    state = _reentry("not-a-date")
    assert state["status"] == "no_current_authorization"
    assert state["reason"] == "target_unparseable"
    assert state["grants_execution_authorization"] is False


# --------------------------------------------------------------------------
# §8 — a thin sample must never move a source in EITHER direction
# --------------------------------------------------------------------------
# Note what this does NOT claim: modifiers are not structurally always zero. A
# source with enough evidence and a genuinely low Wilson bound is demoted (-2),
# and that is correct — the brief asks for neutrality on IMMATURE evidence, not
# for the demotion machinery to be switched off. The live all-zero state on the
# Pi is a consequence of thin samples, not of a disabled mechanism.
@pytest.mark.parametrize(
    ("n", "wilson_lower"),
    [(0, None), (1, 0.0), (5, 0.23), (19, 0.05), (2570, 0.5723)],
)
def test_thin_or_neutral_evidence_yields_no_modifier(n: int, wilson_lower: float | None) -> None:
    """No bonus on thin evidence, and no blanket penalty for being young."""
    from app.learning.source_reliability import _classify_tier

    _tier, modifier = _classify_tier(n, wilson_lower)
    assert modifier == 0, f"n={n} wilson={wilson_lower} produced modifier {modifier}"


def test_a_matured_underperformer_is_still_demoted() -> None:
    """NEGATIVE CONTROL: neutrality on immaturity must not disable the demotion.

    Otherwise the previous test would pass just as well against a system that had
    stopped scoring sources entirely.
    """
    from app.learning.source_reliability import _classify_tier

    tier, modifier = _classify_tier(500, 0.05)
    assert modifier < 0
    assert tier == "low"


# --------------------------------------------------------------------------
# §31 — the severity classes are distinct
# --------------------------------------------------------------------------
def test_immature_evidence_is_not_the_same_class_as_measured_failure() -> None:
    """A source that has not been measured yet must not share a severity class
    with one that was measured and underperformed."""
    assert GATE_LOW_WILSON not in GATE_IMMATURE_REASONS
    assert GATE_PASS not in GATE_IMMATURE_REASONS
    assert GATE_IMMATURE_REASONS, "the immature class must not be empty"
