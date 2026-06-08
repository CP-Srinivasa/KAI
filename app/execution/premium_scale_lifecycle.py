"""Scale-lifecycle persistence (BUG-3) + terminal stabilization (V-1).

Pure helpers so the high-risk bridge edits stay minimal and unit-testable.

BUG-3 — once the bridge resolves an integer-tick scale against a valid spot,
the resolved geometry must be written back onto the envelope so the UI/analytics
show the real entry ($0.248), not the raw channel value (24800), and so the
stale receive-time ``scale_unknown=True`` flag is cleared.

V-1 — a signal in WAITING_FOR_ENTRY must not be terminally rejected by a SINGLE
bad tick (garbage spot / transient scale failure). Only after N consecutive bad
ticks do we terminate; before that the tick is ignored and the signal stays
pending (``pending_entry_with_bad_tick_ignored``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

# A market-/scale-plausibility reject on a single tick should not terminate a
# previously-healthy pending entry. Terminate only after this many CONSECUTIVE
# bad ticks. Env/caller tunable.
DEFAULT_BAD_TICK_TERMINATION_THRESHOLD = 3

PENDING_BAD_TICK_STAGE = "pending_entry_with_bad_tick_ignored"


def build_scale_resolution_patch(
    *,
    scale_factor: float,
    scaled_entry: float,
    scaled_stop_loss: float | None,
    scaled_targets: list[float],
    scale_source: str = "bridge_market_data",
    resolved_at: str | None = None,
) -> dict[str, object]:
    """Return the envelope-payload patch to persist a resolved scale (BUG-3).

    No-op patch (``{}``) when ``scale_factor`` is 1.0 — nothing was rescaled, so
    there is nothing to persist and ``scale_unknown`` must not be flipped on the
    basis of an unscaled signal.
    """
    if scale_factor == 1.0:
        return {}
    return {
        "scale_unknown": False,
        "scale_resolved_at_emit": True,
        "scale_factor": float(scale_factor),
        "scaled_entry": float(scaled_entry),
        "scaled_stop_loss": (float(scaled_stop_loss) if scaled_stop_loss is not None else None),
        "scaled_targets": [float(t) for t in scaled_targets],
        "scale_resolved_at": resolved_at or datetime.now(UTC).isoformat(),
        "scale_source": scale_source,
    }


@dataclass(frozen=True)
class TerminalDecision:
    """Outcome of the V-1 terminal-stabilization check.

    ``action`` is ``"ignore"`` (keep pending, count the bad tick) or
    ``"terminate"`` (emit the terminal reject). ``consecutive_bad`` is the run
    length INCLUDING the current tick.
    """

    action: str
    consecutive_bad: int


def decide_terminal_or_ignore(
    *,
    prior_consecutive_bad: int,
    had_prior_valid_pending: bool,
    threshold: int = DEFAULT_BAD_TICK_TERMINATION_THRESHOLD,
) -> TerminalDecision:
    """Decide whether the current bad tick terminates the signal (V-1).

    ``prior_consecutive_bad`` is how many immediately-preceding ticks were bad.
    ``had_prior_valid_pending`` is True when the signal was ever a healthy
    pending entry (scale resolved, waiting for entry) — only then do we protect
    it from a single garbage tick. A signal that has NEVER been valid pending
    (e.g. structurally broken from the first tick) terminates immediately.
    """
    consecutive = prior_consecutive_bad + 1
    if not had_prior_valid_pending:
        return TerminalDecision(action="terminate", consecutive_bad=consecutive)
    if consecutive >= threshold:
        return TerminalDecision(action="terminate", consecutive_bad=consecutive)
    return TerminalDecision(action="ignore", consecutive_bad=consecutive)


# Bridge stages that count as a "valid pending entry" for V-1 protection.
_VALID_PENDING_STAGES = frozenset({"pending"})
# Bridge stages / reasons that count as a "bad tick".
_BAD_TICK_STAGES = frozenset({"rejected_scale_review"})


def analyze_bridge_history(
    history: list[dict[str, object]],
) -> tuple[int, bool]:
    """Return ``(prior_consecutive_bad, had_prior_valid_pending)`` from the
    per-envelope bridge history (oldest→newest), EXCLUDING the current tick.

    A "valid pending" tick is a ``pending`` stage whose reason is NOT
    ``no_market_data`` (i.e. the scale resolved and the signal was genuinely
    waiting for entry). ``no_market_data`` ticks are neutral — they neither
    confirm health nor count as bad.
    """
    had_valid_pending = False
    consecutive_bad = 0
    for rec in history:
        stage = str(rec.get("stage") or "")
        reason = str(rec.get("reason") or rec.get("audit_reason") or "")
        if stage in _VALID_PENDING_STAGES and reason != "no_market_data":
            had_valid_pending = True
            consecutive_bad = 0
        elif stage in _BAD_TICK_STAGES or stage == PENDING_BAD_TICK_STAGE:
            consecutive_bad += 1
        # no_market_data / other neutral stages don't reset or increment
    return consecutive_bad, had_valid_pending


__all__ = [
    "DEFAULT_BAD_TICK_TERMINATION_THRESHOLD",
    "PENDING_BAD_TICK_STAGE",
    "TerminalDecision",
    "analyze_bridge_history",
    "build_scale_resolution_patch",
    "decide_terminal_or_ignore",
]
