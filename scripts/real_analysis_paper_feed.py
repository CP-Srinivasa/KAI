#!/usr/bin/env python3
"""Run one real-analysis paper-feed pass (Goal 2026-06-10).

Thin systemd/CLI entrypoint around
``app.observability.real_analysis_paper_feeder.run_real_analysis_paper_feed_once``.

Fail-closed by construction: the feeder short-circuits to a side-effect-free
no-op unless the three-arm override is fully armed
(``REAL_ANALYSIS_PAPER_ENABLED`` + ``..._ALLOW_PAPER_WHILE_ENTRY_DISABLED`` +
``..._ENTRY_DISABLED_OVERRIDE_ACK`` == sentinel). So this script is safe to
install + schedule while disarmed: it does nothing until the operator sets all
three acks.

Intended to run from ``kai-real-analysis-paper-feed.timer`` (installed but NOT
enabled by default) and to be operator-triggerable from the CLI for evaluation.

Exit codes:
- 0 success (armed run completed, OR disarmed no-op)
- 1 unexpected error during an armed pass
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.observability.real_analysis_paper_feeder import (  # noqa: E402
    run_real_analysis_paper_feed_once,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("real-analysis-paper-feed")


async def _main() -> int:
    result = await run_real_analysis_paper_feed_once()
    if not result.armed:
        logger.info(
            "real-analysis-paper-feed: DISARMED no-op (refusal=%s)",
            result.refusal_code,
        )
        return 0
    logger.info(
        "real-analysis-paper-feed: armed pass done — selected=%d fills=%d "
        "blocked=%d errors=%d funnel=%s",
        result.candidates_selected,
        result.fills,
        result.blocked,
        result.errors,
        result.funnel,
    )
    return 0


def main() -> int:
    try:
        return asyncio.run(_main())
    except Exception:  # noqa: BLE001 — entrypoint boundary: log + non-zero exit
        logger.exception("real-analysis-paper-feed: unexpected error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
