"""momentum_universe_feeder — G2: feed the own-data universe into PAPER.

Turns the top-N Momentum-Universe symbols into LONG paper signals, gated and
capped, tagged ``analysis_source="momentum_universe"`` so ``edge-report`` can
isolate the cohort and measure it cost-netto. Closes the G1→G2 loop: a symbol
the rotation FSM flagged (``ROTATION_FLAGGED``) or rotated out (``ARCHIVED``) is
NOT fed. Default-off, paper only, NO capital. The trading-loop entrypoint is
injectable so this stays unit-testable without the real pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.core.settings import get_settings
from app.learning.asset_lifecycle import AssetStatus
from app.learning.asset_rotation_shadow import load_state
from app.observability.momentum_universe_ledger import read_latest
from app.orchestrator.trading_loop import run_trading_loop_once

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_PATH = Path("artifacts/momentum_universe_candidates.jsonl")
DEFAULT_STATE_PATH = Path("artifacts/asset_rotation_state.json")
DEFAULT_FED_PATH = Path("artifacts/momentum_universe_fed.jsonl")

COHORT = "momentum_universe"
# Statuses that are NOT fed — the G1 rotation FSM has flagged or rotated them out.
_SKIP_STATUSES = frozenset({AssetStatus.ROTATION_FLAGGED, AssetStatus.ARCHIVED})

RunCycle = Callable[..., Awaitable[Any]]


def _load_fed_ids(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {line.strip() for line in text.splitlines() if line.strip()}


def _record_fed_id(fid: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(fid + "\n")


def _build_analysis(symbol: str) -> AnalysisResult:
    asset = symbol.split("/")[0]
    return AnalysisResult(
        document_id=f"momentum_universe_{symbol.replace('/', '')}",
        sentiment_label=SentimentLabel.BULLISH,
        sentiment_score=0.8,
        relevance_score=1.0,
        impact_score=1.0,
        confidence_score=0.8,
        novelty_score=0.0,
        explanation_short=f"Momentum-Universe signal for {symbol}",
        explanation_long=(
            f"LONG-only momentum-universe paper signal for {symbol} "
            f"(own-data most-traded x best-performer rank)."
        ),
        actionable=True,
        recommended_priority=10,
        affected_assets=[asset],
        event_type="momentum_universe_signal",
        tags=[COHORT, "long"],
    )


async def run_momentum_feeder(
    *,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    fed_path: Path = DEFAULT_FED_PATH,
    now: datetime | None = None,
    run_cycle: RunCycle = run_trading_loop_once,
) -> dict[str, Any]:
    """Feed top-N universe symbols (minus rotated-out) into PAPER. Gated/capped/tagged."""
    settings = get_settings()
    cfg = settings.momentum_universe_feed
    if not cfg.enabled:
        return {"enabled": False}

    snapshot = read_latest(ledger_path)
    universe = snapshot.get("universe") if isinstance(snapshot, dict) else None
    if not isinstance(universe, list) or not universe:
        return {"enabled": True, "fed": 0, "reason": "no_universe"}
    snap_ts = str(snapshot.get("ts", "")) if isinstance(snapshot, dict) else ""

    rotation_state = load_state(state_path)
    fed_ids = _load_fed_ids(fed_path)

    fed = skipped_flagged = skipped_already = failed = 0
    for row in universe[: cfg.top_n]:
        symbol = row.get("symbol") if isinstance(row, dict) else None
        if not isinstance(symbol, str) or not symbol:
            continue
        state = rotation_state.get(symbol)
        if state is not None and state.status in _SKIP_STATUSES:
            skipped_flagged += 1
            continue
        fid = f"{snap_ts}:{symbol}"
        if fid in fed_ids:
            skipped_already += 1
            continue
        try:
            await run_cycle(
                symbol=symbol,
                mode="paper",
                analysis_result=_build_analysis(symbol),
                analysis_source=COHORT,
            )
            _record_fed_id(fid, fed_path)
            fed += 1
        except Exception:  # noqa: BLE001 — one bad symbol must not abort the tick
            failed += 1
            logger.exception("[momentum-feeder] cycle failed for %s", symbol)
            continue
        if cfg.max_per_run and fed >= cfg.max_per_run:
            break

    return {
        "enabled": True,
        "fed": fed,
        "skipped_flagged": skipped_flagged,
        "skipped_already": skipped_already,
        "failed": failed,
        "universe_size": len(universe),
    }
