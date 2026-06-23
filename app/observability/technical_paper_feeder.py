"""LONG-only Technical Paper Feeder (Proposal 2) — runs decoupled."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.domain.document import AnalysisResult
from app.core.enums import SentimentLabel
from app.core.settings import get_settings
from app.orchestrator.models import CycleStatus, LoopCycle
from app.orchestrator.trading_loop import run_trading_loop_once

logger = logging.getLogger(__name__)

DEFAULT_FED_PATH = Path("artifacts/technical_paper_fed.jsonl")
DEFAULT_LEDGER_PATH = Path("artifacts/shadow_candidate_ledger.jsonl")


def load_fed_ids(path: Path = DEFAULT_FED_PATH) -> set[str]:
    """Load successfully fed candidate IDs."""
    if not path.exists():
        return set()
    ids = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if isinstance(record, dict) and "candidate_id" in record:
                    ids.add(record["candidate_id"])
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        logger.warning("[tech-feeder] failed to read fed ledger: %s", exc)
    return ids


def record_fed_id(candidate_id: str, path: Path = DEFAULT_FED_PATH) -> bool:
    """Record fed candidate ID for idempotency."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            record = {"candidate_id": candidate_id, "fed_at": datetime.now(UTC).isoformat()}
            fh.write(json.dumps(record) + "\n")
        return True
    except OSError as exc:
        logger.warning("[tech-feeder] failed to write fed ID %s: %s", candidate_id, exc)
        return False


def load_shadow_candidates(path: Path = DEFAULT_LEDGER_PATH) -> list[dict[str, Any]]:
    """Load candidates from shadow ledger."""
    if not path.exists():
        return []
    candidates = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if isinstance(record, dict):
                    candidates.append(record)
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        logger.warning("[tech-feeder] failed to read shadow ledger: %s", exc)
    return candidates


def _as_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _cycle_was_fed(cycle: LoopCycle) -> bool:
    return cycle.status is CycleStatus.COMPLETED and cycle.fill_simulated


async def run_feeder(
    *,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    fed_path: Path = DEFAULT_FED_PATH,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Process shadow ledger technical candidates and feed them to the trading loop."""
    settings = get_settings()
    if not settings.technical_paper.enabled:
        logger.info("[tech-feeder] technical paper feeder is disabled in settings.")
        return {"enabled": False}

    now_utc = now or datetime.now(UTC)
    max_age_hours = settings.technical_paper.freshness_max_age_hours
    age_limit = now_utc - timedelta(hours=max_age_hours)

    candidates = load_shadow_candidates(ledger_path)
    fed_ids = load_fed_ids(fed_path)

    processed = 0
    fed_count = 0
    skipped_stale = 0
    skipped_rejected = 0
    skipped_short = 0
    skipped_already = 0
    skipped_weak = 0
    not_filled = 0
    failed = 0

    for c in candidates:
        if c.get("candidate_kind") != "technical":
            continue

        processed += 1
        cid = c.get("candidate_id")
        if not cid:
            continue

        if cid in fed_ids:
            skipped_already += 1
            continue

        # Filter 1: LONG-only
        side = str(c.get("side") or "").lower()
        if side != "long":
            skipped_short += 1
            continue

        # Filter 2: skip candidates rejected by screening/eligibility.
        if c.get("gate_would_reject") is True:
            skipped_rejected += 1
            continue

        # Filter 3: freshness check
        ts_str = c.get("ts_utc") or ""
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            skipped_stale += 1
            continue

        if ts < age_limit:
            skipped_stale += 1
            continue

        # Filter 4: min strength check
        raw_strength = _as_float(c.get("signal_confidence"))
        strength = raw_strength if raw_strength is not None else 0.0
        if strength < settings.technical_paper.min_strength:
            skipped_weak += 1
            continue

        # Build analysis result
        symbol = c["symbol"]
        analysis = AnalysisResult(
            document_id=f"technical_paper_{symbol.replace('/', '')}_{cid}",
            sentiment_label=SentimentLabel.BULLISH,
            sentiment_score=0.8,
            relevance_score=1.0,
            impact_score=1.0,
            confidence_score=raw_strength if raw_strength is not None else 0.8,
            novelty_score=0.0,
            explanation_short=f"Technical screener signal for {symbol}",
            explanation_long=(
                f"LONG-only technical paper feeder signal triggered for {symbol} "
                f"from candidate {cid}"
            ),
            actionable=True,
            recommended_priority=10,
            affected_assets=[symbol],
            event_type="technical_screener_signal",
            tags=["technical", "long"],
        )

        try:
            cycle = await run_trading_loop_once(
                symbol=symbol,
                mode="paper",
                analysis_result=analysis,
                analysis_source="technical_paper",
            )
            if _cycle_was_fed(cycle):
                record_fed_id(cid, fed_path)
                fed_count += 1
            else:
                not_filled += 1
            logger.info(
                "[tech-feeder] processed candidate %s for %s, status=%s",
                cid,
                symbol,
                cycle.status.value,
            )
        except Exception as exc:
            failed += 1
            logger.exception("[tech-feeder] failed to run cycle for candidate %s: %s", cid, exc)

    return {
        "enabled": True,
        "processed_candidates": processed,
        "fed": fed_count,
        "skipped_already": skipped_already,
        "skipped_short": skipped_short,
        "skipped_rejected": skipped_rejected,
        "skipped_stale": skipped_stale,
        "skipped_weak": skipped_weak,
        "not_filled": not_filled,
        "failed": failed,
    }
