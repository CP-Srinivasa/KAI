#!/usr/bin/env python3
"""Run one shadow-real feed tick (Issue #175 wiring, Sprint S4 2026-06-11).

Thin systemd/CLI entrypoint around
``app.observability.shadow_real_feed.run_shadow_real_feed`` — the NEO-P-002-r3
driver that replays REAL analyzed documents through the existing
``run_trading_loop_once(mode=SHADOW)`` seam so the real ``SignalGenerator``
finally produces measurable ``source=autonomous_generator`` shadow candidates
(the stream the eligibility probe GO-confirmed at ~5-6 directional signals/day).
The existing shadow resolver then resolves them → ``real_resolved`` starts
counting instead of staying an unexplained 0.

Fail-safe by construction:
- ``EXECUTION_SHADOW_REAL_GENERATOR=false`` (default) → the driver returns a
  ``flag_off`` no-op funnel WITHOUT touching the DB (the fetch callable below
  is only invoked when the flag is ON).
- The driver forces ``ExecutionMode.SHADOW`` — no order, no position, no fill;
  ``entry_mode`` is never read or changed by this path.
- Idempotent: the fed-ledger skips already-fed documents, so a 30-min timer
  cadence re-processes nothing.

Intended to run from ``kai-shadow-real-feed.timer`` (installed but NOT enabled
by default) and operator-triggerable for evaluation.

Exit codes: 0 = tick completed (flag-off no-op OR armed pass), 1 = unexpected
error during an armed pass.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.domain.document import CanonicalDocument  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.observability.shadow_real_feed import run_shadow_real_feed  # noqa: E402
from app.storage.db.session import build_session_factory  # noqa: E402
from app.storage.repositories.document_repo import DocumentRepository  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("shadow-real-feed")

# Mirror the real-analysis paper feeder's freshness horizon: stale analyses do
# not produce honest forward-shadow data.
_FRESHNESS_HOURS = 48
_MAX_DOC_FETCH = 20000
# Per-tick injection ceiling — keeps one tick bounded; the fed-ledger makes the
# next tick pick up where this one stopped.
_LIMIT_PER_TICK = 20


async def _fetch_recent_analyzed() -> list[CanonicalDocument]:
    """Fetch recent analyzed, non-duplicate documents (only called flag-ON)."""
    settings = get_settings()
    oldest = datetime.now(UTC) - timedelta(hours=_FRESHNESS_HOURS)
    factory = build_session_factory(settings.db)
    async with factory.begin() as session:
        repo = DocumentRepository(session)
        return await repo.list(
            is_analyzed=True,
            is_duplicate=False,
            published_after=oldest,
            limit=_MAX_DOC_FETCH,
        )


async def _main() -> int:
    funnel = await run_shadow_real_feed(
        fetch_recent_analyzed=_fetch_recent_analyzed,
        limit=_LIMIT_PER_TICK,
    )
    if not funnel.get("enabled"):
        logger.info("shadow-real-feed: flag OFF → no-op (%s)", funnel.get("reason"))
        return 0
    logger.info(
        "shadow-real-feed: armed tick done — seen=%s eligible=%s injected=%s "
        "shadow_recorded=%s run_errors=%s in_loop=%s",
        funnel.get("seen"),
        funnel.get("eligible"),
        funnel.get("injected"),
        funnel.get("shadow_recorded"),
        funnel.get("run_errors"),
        funnel.get("in_loop"),
    )
    return 0


def main() -> int:
    try:
        return asyncio.run(_main())
    except Exception:  # noqa: BLE001 — entrypoint boundary: log + non-zero exit
        logger.exception("shadow-real-feed: unexpected error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
