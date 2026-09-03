"""STAB-2026-09-01 §7 — a resolved finding must be announced exactly once.

There WAS a recovery message, but only when the finding set became entirely
empty. Any finding that cleared while another remained simply vanished from the
channel without a word.

That is not hypothetical. On 2026-09-01 the CRITICAL finding ``privilege_broker``
cleared at 10:21 when the #838 deploy installed the matching binary. The set went
from {privilege_broker, youtube_transcript_coverage} to
{youtube_transcript_coverage} — non-empty, so the code took the "set changed" path
and sent a fresh alert. The operator was never told the CRITICAL had been
resolved; they had to infer it from its absence. An alarm that stops without
saying so trains people to ignore absence.

The transition ACTIVE -> CLEAR now emits one message per finding, and exactly one.
"""

from __future__ import annotations

from typing import Any

from app.alerts.health_notify import (
    build_recovery_text,
    finding_id_for,
    resolve_recoveries,
)

T0 = 1_756_000_000.0
HOUR = 3600.0


class _Issue:
    def __init__(self, severity: str, component: str, message: str = "x") -> None:
        self.severity = severity
        self.component = component
        self.message = message


def _ids(recovered: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [fid for fid, _ in recovered]


# --------------------------------------------------------------------------
# THE case this exists for
# --------------------------------------------------------------------------
def test_a_finding_clearing_beside_a_surviving_one_is_announced() -> None:
    """The live 2026-09-01 case, which produced no message at all."""
    broker = _Issue("critical", "privilege_broker")
    youtube = _Issue("warning", "youtube_transcript_coverage")

    _rec, state = resolve_recoveries([broker, youtube], {}, now_ts=T0)
    assert set(state) == {"privilege_broker", "youtube_transcript_coverage"}

    # The broker clears; YouTube does not. The set is still non-empty.
    recovered, state = resolve_recoveries([youtube], state, now_ts=T0 + 2 * HOUR)
    assert _ids(recovered) == ["privilege_broker"]
    assert "privilege_broker" not in state
    assert state["youtube_transcript_coverage"]["last_state"] == "ACTIVE"


def test_the_transition_fires_exactly_once() -> None:
    """ACTIVE -> CLEAR -> CLEAR -> CLEAR must yield ONE recovery."""
    issue = _Issue("warning", "tradingview_ingress_freshness")
    _rec, state = resolve_recoveries([issue], {}, now_ts=T0)

    first, state = resolve_recoveries([], state, now_ts=T0 + HOUR)
    assert _ids(first) == ["tradingview_ingress_freshness"]

    second, state = resolve_recoveries([], state, now_ts=T0 + 2 * HOUR)
    assert second == []

    third, state = resolve_recoveries([], state, now_ts=T0 + 3 * HOUR)
    assert third == []


def test_a_returning_finding_starts_a_fresh_episode() -> None:
    """It IS a new episode — the duration must not span the healthy gap."""
    issue = _Issue("warning", "tradingview_ingress_freshness")
    _rec, state = resolve_recoveries([issue], {}, now_ts=T0)
    _rec, state = resolve_recoveries([], state, now_ts=T0 + HOUR)
    _rec, state = resolve_recoveries([issue], state, now_ts=T0 + 10 * HOUR)
    assert state["tradingview_ingress_freshness"]["first_seen_ts"] == T0 + 10 * HOUR


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS
# --------------------------------------------------------------------------
def test_an_escalation_is_not_a_recovery() -> None:
    """warning -> critical on the SAME component is deterioration, not resolution.

    Keying identity on ``severity:component`` would announce "✅ RECOVERED" for the
    warning in the very moment the situation got worse.
    """
    warn = _Issue("warning", "tradingview_ingress_freshness")
    crit = _Issue("critical", "tradingview_ingress_freshness")

    _rec, state = resolve_recoveries([warn], {}, now_ts=T0)
    first_seen = state["tradingview_ingress_freshness"]["first_seen_ts"]

    recovered, state = resolve_recoveries([crit], state, now_ts=T0 + HOUR)
    assert recovered == []
    entry = state["tradingview_ingress_freshness"]
    assert entry["last_severity"] == "critical"
    # The episode continues, so the duration keeps counting from the warning.
    assert entry["first_seen_ts"] == first_seen


def test_a_still_active_finding_is_never_recovered() -> None:
    issue = _Issue("critical", "privilege_broker")
    _rec, state = resolve_recoveries([issue], {}, now_ts=T0)
    for i in range(1, 5):
        recovered, state = resolve_recoveries([issue], state, now_ts=T0 + i * HOUR)
        assert recovered == []


def test_a_healthy_system_that_was_never_unhealthy_says_nothing() -> None:
    recovered, state = resolve_recoveries([], {}, now_ts=T0)
    assert recovered == []
    assert state == {}


def test_identity_ignores_the_message_text() -> None:
    """The text carries ages and counters; a text key would flap every run."""
    a = _Issue("warning", "timer_health", "mtime is 22521min old")
    b = _Issue("warning", "timer_health", "mtime is 22526min old")
    assert finding_id_for(a) == finding_id_for(b)

    _rec, state = resolve_recoveries([a], {}, now_ts=T0)
    recovered, state = resolve_recoveries([b], state, now_ts=T0 + HOUR)
    assert recovered == []


# --------------------------------------------------------------------------
# The message itself
# --------------------------------------------------------------------------
def test_the_recovery_message_carries_the_required_fields() -> None:
    issue = _Issue("critical", "privilege_broker")
    _rec, state = resolve_recoveries([issue], {}, now_ts=T0)
    recovered, _state = resolve_recoveries([], state, now_ts=T0 + 2 * HOUR)
    fid, entry = recovered[0]

    text = build_recovery_text(fid, entry, recovered_at=T0 + 2 * HOUR)
    assert "RECOVERED" in text
    assert "privilege_broker" in text
    assert "first_seen=" in text
    assert "recovered_at=" in text
    assert "duration=2.0h" in text
    assert len(text.splitlines()) == 4
