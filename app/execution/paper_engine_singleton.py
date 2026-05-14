"""Per-process singleton factory for PaperExecutionEngine (P1 #7 — 2026-05-14).

Why: pre-2026-05-14 six call-sites instantiated their own
PaperExecutionEngine and called rehydrate_from_audit() every cycle.
That created three independent in-memory snapshots inside the FastAPI
process (Bridge tick, PositionMonitor tick, /premium_signals POST) plus
hard-coded ``initial_equity=10000.0`` regressions in the target-completion
reconciler and the /premium_signals adjust endpoint.

After this module, all six sites resolve to one process-local instance.
Cross-process consistency (kai-server vs. kai-paper-trading.service vs.
kai-tg-listener) still flows through ``rehydrate_from_audit()`` against
the shared JSONL audit log — that is the canonical sync point.

Semantics:
- ``get_paper_engine()`` is idempotent within a process (lru_cache).
- Callers MUST still invoke ``rehydrate_from_audit()`` before each
  read/mutation cycle — singleton spares the constructor cost but cannot
  see writes from sibling processes without re-reading the audit log.
- ``reset_paper_engine_cache()`` is the test-only escape hatch. Real code
  paths should never need to drop the cached instance.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.settings import get_settings
from app.execution.paper_engine import PaperExecutionEngine


@lru_cache(maxsize=1)
def get_paper_engine() -> PaperExecutionEngine:
    """Return the process-local PaperExecutionEngine singleton.

    Constructor parameters are pulled from settings.execution so the same
    engine instance carries the correct paper-initial-equity / fee /
    slippage values regardless of which call-site asks for it. This
    eliminates the previous regression where target_completion_reconciler
    and /premium_signals adjust hard-coded ``initial_equity=10000.0`` and
    silently diverged from settings.execution.paper_initial_equity.
    """
    s = get_settings().execution
    return PaperExecutionEngine(
        initial_equity=s.paper_initial_equity,
        fee_pct=s.paper_fee_pct,
        slippage_pct=s.paper_slippage_pct,
        live_enabled=False,
    )


def reset_paper_engine_cache() -> None:
    """Drop the cached singleton — for tests only.

    Production code should never call this. Tests that need an isolated
    engine instance (e.g. with a custom audit-log path) should clear the
    cache in an autouse fixture so a stale singleton from a prior test
    cannot leak portfolio state into the next test.
    """
    get_paper_engine.cache_clear()


__all__ = ["get_paper_engine", "reset_paper_engine_cache"]
