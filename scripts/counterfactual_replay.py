"""Counterfactual Live∥Replay drift pass (ADR 0010 / #318 Phase 1).

Thin, flag-gated shell around the pure
``app/observability/counterfactual_replay_logger.run_counterfactual_pass``. For
each shadow candidate it compares the LIVE entry price KAI acted on against the
settled 1m Binance kline (replay view) of the same minute and appends a drift
record to ``artifacts/counterfactual_comparison.jsonl``.

READ-ONLY: no order, no fill, no paper_execution_audit write, no live/paper-path
mutation — strictly safer than entry_mode=paper. Kill-switch
``EXECUTION_DUAL_STREAM_DIAGNOSTICS`` (default OFF → no-op). Reuses the same
``binance_kline_fetcher`` as the shadow resolver (public REST, no auth).

Usage:
    python scripts/counterfactual_replay.py            # respects the flag
    python scripts/counterfactual_replay.py --force    # run even if flag off (test)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.settings import get_settings  # noqa: E402
from app.observability.counterfactual_replay_logger import (  # noqa: E402
    run_counterfactual_pass,
)
from app.observability.shadow_resolver import binance_kline_fetcher  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("counterfactual-replay")


def main(argv: list[str] | None = None) -> int:
    force = "--force" in (argv if argv is not None else sys.argv[1:])
    settings = get_settings()
    if not settings.execution.dual_stream_diagnostics and not force:
        logger.info("counterfactual-replay: EXECUTION_DUAL_STREAM_DIAGNOSTICS off — no-op")
        return 0
    counts = run_counterfactual_pass(
        fetch_klines=binance_kline_fetcher,
        threshold_bps=float(settings.execution.dual_stream_drift_bps),
    )
    logger.info("counterfactual-replay: %s", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
