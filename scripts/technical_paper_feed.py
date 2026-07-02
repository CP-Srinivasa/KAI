#!/usr/bin/env python3
"""Run one pass of the LONG-only Technical Paper Feeder.

Fetches eligible LONG-only candidates from the shadow candidate ledger
and processes them through the trading loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.observability.technical_paper_feeder import run_feeder  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("technical-paper-feed")


async def _main() -> int:
    result = await run_feeder()
    if not result.get("enabled", False):
        logger.info("technical-paper-feed: disabled in settings (no-op)")
        return 0

    logger.info(
        "technical-paper-feed completed: processed=%d fed=%d skipped_already=%d "
        "skipped_short=%d skipped_rejected=%d skipped_stale=%d skipped_weak=%d failed=%d",
        result.get("processed_candidates", 0),
        result.get("fed", 0),
        result.get("skipped_already", 0),
        result.get("skipped_short", 0),
        result.get("skipped_rejected", 0),
        result.get("skipped_stale", 0),
        result.get("skipped_weak", 0),
        result.get("failed", 0),
    )
    return 0


def main() -> int:
    try:
        return asyncio.run(_main())
    except Exception:  # noqa: BLE001
        logger.exception("technical-paper-feed: unexpected error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
