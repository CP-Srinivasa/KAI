"""NEO-P-002-r3 — Shadow-real feed driver (read-only, flag-gated).

Replays REAL analyzed documents into the existing shadow path so the real
``SignalGenerator`` produces ``source=autonomous_generator`` candidates that the
existing edge-measurement infra (shadow_candidate_ledger / edge_report) can
resolve. Uses the existing ``run_trading_loop_once(analysis_result=...)`` seam —
no orchestrator rewrite, no new edge infra.

Hard safety contract (owned + enforced by the loop, asserted by tests here):
- Default OFF: ``EXECUTION_SHADOW_REAL_GENERATOR=false`` → no-op (status quo).
- Mode is forced to SHADOW; never live. ``entry_mode`` is never changed.
- The loop runs its existing entry_mode-disabled shadow path → records a
  hypothetical candidate, NO order / NO position / NO fill.

Funnel (the ledger must explain real_resolved=0): seen → already_fed →
no_symbol/non_directional → eligible → injected → cycle-status buckets. Written
to ``artifacts/shadow_real_feed_funnel.jsonl`` (append-only, auditable).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.settings import get_settings
from app.observability.real_analysis_provider import (
    FED_LEDGER_PATH,
    FeedCandidate,
    mark_fed,
    select_pending,
)
from app.observability.shadow_inloop_funnel import build_inloop_funnel

logger = logging.getLogger(__name__)

FUNNEL_PATH = Path("artifacts/shadow_real_feed_funnel.jsonl")

# Cycle-status strings (str(LoopCycle.status)) we count as "shadow candidate
# actually recorded" vs other terminal buckets. Kept as substrings so we don't
# import the enum (decoupled, and robust to "CycleStatus.ENTRY_MODE_BLOCKED" vs
# "entry_mode_blocked" string forms).
_SHADOW_RECORDED_HINTS = ("ENTRY_MODE_BLOCKED", "entry_mode_blocked")


# A run-once callable: (symbol, analysis_result) -> LoopCycle-ish object with a
# ``.status`` and a ``.notes`` list. Injected in tests; defaults to the real
# run_trading_loop_once in shadow mode.
RunOnce = Callable[..., Awaitable[Any]]


async def _default_run_once(*, symbol: str, analysis_result: Any) -> Any:
    from app.core.enums import ExecutionMode
    from app.execution.real_analysis_paper import REAL_ANALYSIS_SOURCE
    from app.orchestrator.trading_loop import run_trading_loop_once

    # mode=SHADOW is a hard floor: the feed never drives paper/live execution.
    # The decoupling verdict additionally refuses any non-PAPER cycle, so the
    # ``real_analysis`` tag below can NOT turn this into a fill.
    #
    # V2 2026-06-16: tag analysis_source=real_analysis so the D-182 priority gate
    # uses the operator-configured real_analysis_paper.min_priority (5) instead of
    # the global paper_min_priority (10). Without the tag the shadow funnel was
    # silently gated at 10 — rejecting ~88% of eligible directional analyses
    # (priority 5–9) and starving the closed-trade / edge-resolution funnel.
    return await run_trading_loop_once(
        symbol=symbol,
        mode=ExecutionMode.SHADOW,
        analysis_result=analysis_result,
        analysis_source=REAL_ANALYSIS_SOURCE,
    )


def _append_funnel(record: dict[str, Any], *, path: Path = FUNNEL_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:  # noqa: BLE001
        logger.warning("[r3-feed] funnel write failed: %s", exc)


async def run_shadow_real_feed(
    *,
    fetch_recent_analyzed: Callable[[], Awaitable[list[Any]]],
    run_once: RunOnce | None = None,
    limit: int = 20,
    min_directional_confidence: float = 0.0,
    fed_ledger_path: Path = FED_LEDGER_PATH,
    funnel_path: Path = FUNNEL_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One shadow-real feed tick. Returns the funnel dict (also persisted).

    Fail-safe: when ``EXECUTION_SHADOW_REAL_GENERATOR`` is OFF, returns a
    ``flag_off`` no-op funnel WITHOUT fetching or injecting anything. ``run_once``
    defaults to the real shadow-mode loop; tests inject a spy.
    ``fetch_recent_analyzed`` is injected (the DocumentRepository call) so this
    module stays DB-agnostic and offline-testable.
    """
    ts = (now or datetime.now(UTC)).isoformat()
    if not get_settings().execution.shadow_real_generator:
        funnel: dict[str, object] = {"timestamp_utc": ts, "enabled": False, "reason": "flag_off"}
        _append_funnel(funnel, path=funnel_path)
        return funnel

    runner = run_once or _default_run_once
    docs = await fetch_recent_analyzed()
    candidates, counts = select_pending(
        docs,
        fed_ledger_path=fed_ledger_path,
        min_directional_confidence=min_directional_confidence,
    )
    injected = 0
    run_errors = 0
    status_buckets: dict[str, int] = {}
    # Per-cycle (status, notes) for the in-loop funnel (#175): explains WHERE
    # inside the loop/generator each injected candidate died.
    cycle_outcomes: list[tuple[str, list[str]]] = []

    for cand in candidates[: max(0, limit)]:
        assert isinstance(cand, FeedCandidate)
        try:
            cycle = await runner(symbol=cand.symbol, analysis_result=cand.analysis)
        except Exception as exc:  # noqa: BLE001 — one bad cycle must not kill the feed
            run_errors += 1
            logger.warning("[r3-feed] run_once failed sym=%s: %s", cand.symbol, exc)
            continue
        injected += 1
        status = str(getattr(cycle, "status", "unknown"))
        status_buckets[status] = status_buckets.get(status, 0) + 1
        raw_notes = getattr(cycle, "notes", None)
        notes = [str(n) for n in raw_notes] if isinstance(raw_notes, (list, tuple)) else []
        cycle_outcomes.append((status, notes))
        # Idempotency: mark fed only after a successful (no-exception) run.
        mark_fed(cand.analysis.document_id, path=fed_ledger_path)

    # In-loop funnel axes (#175) — distinct from the feeder-level ``counts`` above.
    in_loop = build_inloop_funnel(cycle_outcomes)

    funnel = {
        **counts,
        "injected": injected,
        "run_errors": run_errors,
        "shadow_recorded": sum(
            n for s, n in status_buckets.items() if any(h in s for h in _SHADOW_RECORDED_HINTS)
        ),
        "by_cycle_status": status_buckets,
        "in_loop": in_loop,
        "timestamp_utc": ts,
        "enabled": True,
    }
    _append_funnel(funnel, path=funnel_path)
    return funnel


__all__ = ["FUNNEL_PATH", "run_shadow_real_feed"]
