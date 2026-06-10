"""Real-analysis paper-learning decoupling override (Goal 2026-06-10).

This is the ORTHOGONAL twin of the premium Pfad-3 override
(``premium_paper_entry_disabled_override`` in ``premium_fastlane.py``, #208).
It owns the fail-closed three-arm acknowledgement that lets the *real-analysis*
paper feeder open PAPER fills while the global ``entry_mode=disabled`` kill-switch
stays set — and ONLY that route. It deliberately lives in its own module so the
parallel premium/fastlane work is never touched.

Hard scope (invariants):
- Decouples ONLY ``source=real_analysis`` (stored LLM-analysed news documents).
- Synthetic control-plane probes (``loop_control_*`` document_ids) are NOT
  real-analysis and are hard-excluded by the caller; this override never re-arms
  the degenerate autonomous loop.
- Paper-only: the feeder runs ``ExecutionMode.PAPER`` and the loop's
  ``_run_once_guard`` allows only paper/shadow → live is never reachable.
- All three arms default off/empty → without an explicit operator ack the
  override returns ``(False, ...)`` and the kill-switch holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.settings import REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL

if TYPE_CHECKING:
    from app.core.settings import AppSettings

# Hard marker for the decoupled feeder path. ``run_cycle`` only honours the
# real-analysis decoupling when the analysis source equals this exact string
# AND the analysis document_id is not a synthetic ``loop_control_*`` probe.
REAL_ANALYSIS_SOURCE = "real_analysis"

# Synthetic control-plane probes carry document_ids with this prefix
# (``build_loop_trigger_analysis``). They must NEVER be treated as real-analysis.
_SYNTHETIC_DOC_PREFIX = "loop_control_"


def is_real_analysis_source(analysis_source: str | None) -> bool:
    """True iff the caller explicitly tagged this cycle as the real-analysis feed.

    A missing/empty/other source is NOT real-analysis → the decoupled paper path
    is never taken for it (fail-closed default: the normal autonomous loop and
    every other source keep honouring ``entry_mode``).
    """
    return analysis_source == REAL_ANALYSIS_SOURCE


def is_synthetic_probe_document(document_id: str | None) -> bool:
    """True for synthetic control-plane probe documents (``loop_control_*``).

    Defense-in-depth: even if a caller mis-tags a synthetic probe as
    real-analysis, the decoupled path is refused for these document_ids so the
    degenerate canary loop can never reach a fill via the real-analysis route.
    """
    return isinstance(document_id, str) and document_id.startswith(_SYNTHETIC_DOC_PREFIX)


def real_analysis_paper_entry_disabled_override(
    settings: AppSettings,
) -> tuple[bool, str | None]:
    """Fail-closed preflight: may the REAL-ANALYSIS feeder open a PAPER position
    while ``entry_mode=disabled``?

    Returns ``(allowed, refusal_code)``. ``allowed`` is True ONLY when ALL hold:
      - ``real_analysis_paper.enabled`` (the feeder master opt-in), AND
      - ``real_analysis_paper.allow_paper_while_entry_disabled`` (per-bypass opt-in), AND
      - ``real_analysis_paper.entry_disabled_override_ack`` == the ack sentinel
        (an independent human-typed acknowledgement that the global kill-switch is
        being un-gated for the real-analysis paper route).

    Any missing → the kill-switch holds (fail-closed) with a refusal code for the
    audit trail. This is the controlled alternative to flipping the GLOBAL
    ``entry_mode`` (which would also re-arm the synthetic autonomous loop). It is
    orthogonal to the premium override (#208) and never touches live: the feeder
    runs ``ExecutionMode.PAPER`` only.
    """
    cfg = settings.real_analysis_paper
    if not cfg.enabled:
        return False, "real_analysis_paper_disabled"
    if not cfg.allow_paper_while_entry_disabled:
        return False, "real_analysis_paper_while_entry_disabled_off"
    if cfg.entry_disabled_override_ack != REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL:
        return False, "real_analysis_paper_entry_disabled_override_not_armed"
    return True, None


__all__ = [
    "REAL_ANALYSIS_SOURCE",
    "is_real_analysis_source",
    "is_synthetic_probe_document",
    "real_analysis_paper_entry_disabled_override",
]
