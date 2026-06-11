"""Real-analysis paper-learning FEEDER (Goal 2026-06-10).

The orchestration delta that finally makes the paper-learning stream carry
*usable* data: it pulls REAL analysed documents from the DocumentRepository,
selects the eligible directional ones (long AND short, via
``real_analysis_paper_selector``), and injects each into the loop as a PAPER
cycle tagged ``source=real_analysis``.

Fail-closed by construction:
- It is a NO-OP unless the operator armed the three-arm override
  (``real_analysis_paper.enabled`` ∧ ``allow_paper_while_entry_disabled`` ∧
  ack-sentinel). Without it every injected cycle is refused at the loop's
  entry-mode gate (ENTRY_MODE_BLOCKED) — the feeder still runs read-only but
  produces zero fills.
- It NEVER flips ``entry_mode`` and NEVER touches the premium/fastlane path.
- It runs ``ExecutionMode.PAPER`` only → live is unreachable (the loop's
  ``_run_once_guard`` forbids anything but paper/shadow).
- The synthetic autonomous loop is untouched: this feeder only injects real
  stored documents (never ``loop_control_*`` probes).

B-005: ``get_settings()`` is read EXACTLY ONCE per ``run_once`` invocation and
passed down; no per-candidate re-parse of ``.env`` (event-loop-wedge guard).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.enums import ExecutionMode
from app.core.settings import get_settings
from app.execution.entry_policy import EntryRoute, resolve_entry_policy
from app.execution.real_analysis_paper import REAL_ANALYSIS_SOURCE
from app.observability.real_analysis_paper_selector import (
    RealAnalysisCandidate,
    select_real_analysis_candidates,
)
from app.orchestrator.models import CycleStatus
from app.orchestrator.trading_loop import run_trading_loop_once
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository

logger = logging.getLogger(__name__)

_MAX_DOC_FETCH = 20000


@dataclass
class FeederResult:
    """Outcome of one feeder pass — pure reporting, no side effects of its own."""

    armed: bool
    refusal_code: str | None
    candidates_selected: int
    fills: int
    blocked: int
    errors: int
    funnel: dict[str, int] = field(default_factory=dict)
    fill_document_ids: list[str] = field(default_factory=list)


async def run_real_analysis_paper_feed_once(
    *,
    symbol_override: str | None = None,
) -> FeederResult:
    """Run one real-analysis paper-feed pass.

    Returns a :class:`FeederResult`. When the entry policy keeps the
    real-analysis route closed (legacy three-arm override not armed, or the
    active mode does not open the route) the pass short-circuits BEFORE any
    loop injection (``armed=False`` + the refusal code) so a disarmed feeder is
    a cheap, side-effect-free no-op.

    ``symbol_override`` forces every candidate onto one symbol (test seam); in
    normal operation each candidate keeps its document-derived symbol.
    """
    # B-005: ONE settings read for the whole pass.
    settings = get_settings()
    cfg = settings.real_analysis_paper

    # Sprint S3 (#181): arming is the entry-policy verdict for the
    # real-analysis route. Under ``disabled`` that delegates to the legacy
    # three-arm override (migration alias, byte-identical); under
    # ``paper_learning`` the mode itself opens the route (master enable still
    # required); ``paper_premium_limited`` keeps it closed.
    verdict = resolve_entry_policy(settings).verdict(EntryRoute.REAL_ANALYSIS_PAPER)
    armed, refusal = verdict.allowed, verdict.reason_code
    if not armed:
        logger.info("[real-analysis-feeder] disarmed → no-op (%s)", refusal)
        return FeederResult(
            armed=False,
            refusal_code=refusal,
            candidates_selected=0,
            fills=0,
            blocked=0,
            errors=0,
        )

    now = datetime.now(UTC)
    oldest = now - timedelta(hours=cfg.freshness_max_age_hours)
    factory = build_session_factory(settings.db)
    async with factory.begin() as session:
        repo = DocumentRepository(session)
        docs = await repo.list(
            is_analyzed=True,
            is_duplicate=False,
            published_after=oldest,
            limit=_MAX_DOC_FETCH,
        )

    candidates, funnel = select_real_analysis_candidates(
        docs,
        freshness_max_age_hours=cfg.freshness_max_age_hours,
        min_priority=cfg.min_priority,
        now=now,
    )

    fills = 0
    blocked = 0
    errors = 0
    fill_ids: list[str] = []
    for cand in candidates:
        try:
            cycle = await _inject(cand, symbol_override=symbol_override)
        except Exception as exc:  # noqa: BLE001 — one bad doc must not abort the pass
            errors += 1
            logger.warning("[real-analysis-feeder] inject failed %s: %s", cand.document_id, exc)
            continue
        # A fill = a COMPLETED cycle that actually simulated a fill. A COMPLETED
        # cycle without a fill (no_signal-equivalent downstream) or any reject
        # status counts as blocked, not a fill.
        if cycle.status == CycleStatus.COMPLETED and cycle.fill_simulated:
            fills += 1
            fill_ids.append(cand.document_id)
        else:
            blocked += 1
            logger.info(
                "[real-analysis-feeder] %s → %s (%s)",
                cand.document_id,
                cycle.status.value,
                cand.direction,
            )

    return FeederResult(
        armed=True,
        refusal_code=None,
        candidates_selected=len(candidates),
        fills=fills,
        blocked=blocked,
        errors=errors,
        funnel=funnel,
        fill_document_ids=fill_ids,
    )


async def _inject(cand: RealAnalysisCandidate, *, symbol_override: str | None):  # type: ignore[no-untyped-def]
    """Inject one real-analysis candidate as a PAPER cycle.

    Hard invariants at the injection seam:
      - ``mode=ExecutionMode.PAPER`` (never live; ``_run_once_guard`` enforces).
      - ``analysis_source=REAL_ANALYSIS_SOURCE`` so the loop's decoupling verdict
        can apply and the fill is attributed ``real_analysis`` (B-002).
    """
    symbol = symbol_override or cand.symbol
    return await run_trading_loop_once(
        symbol=symbol,
        mode=ExecutionMode.PAPER,
        analysis_result=cand.analysis,
        analysis_source=REAL_ANALYSIS_SOURCE,
    )


__all__ = [
    "FeederResult",
    "run_real_analysis_paper_feed_once",
]
