"""Exploration runner.

Runs eligible probes, captures their output, and returns the results. The runner
is the single defensive boundary: even though probes are contractually forbidden
from raising, the runner wraps each call so one misbehaving probe cannot abort a
whole pass.

A politeness throttle (``min_request_interval_seconds``) is applied *between*
probe runs so a full pass never hammers any host.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from app.exploration.base import ExplorationProbe, ExplorationResult, ProbeMeta
from app.exploration.capture import append_normalized, write_raw
from app.exploration.settings import ExplorationSettings, get_exploration_settings
from app.exploration.sources import build_registry

logger = logging.getLogger(__name__)


async def _run_one(probe: ExplorationProbe, *, timeout: int) -> ExplorationResult:
    """Run one probe defensively: never raises, always returns a result."""
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(probe.probe(), timeout=timeout)
    except TimeoutError:
        return ExplorationResult(
            source_name=probe.source_name,
            access_mode=probe.access_mode,
            fetched_at=datetime.now(UTC),
            success=False,
            error=f"timeout_after_{timeout}s",
            meta=ProbeMeta(latency_ms=round((time.monotonic() - start) * 1000, 1)),
        )
    except Exception as exc:  # noqa: BLE001 — probe contract violation; contain it
        logger.warning(
            "[exploration] probe %s raised (contract violation): %s", probe.probe_id, exc
        )
        return ExplorationResult(
            source_name=probe.source_name,
            access_mode=probe.access_mode,
            fetched_at=datetime.now(UTC),
            success=False,
            error=f"probe_raised:{type(exc).__name__}:{exc}",
            meta=ProbeMeta(latency_ms=round((time.monotonic() - start) * 1000, 1)),
        )

    # Stamp latency if the probe did not.
    if result.meta.latency_ms is None:
        result.meta.latency_ms = round((time.monotonic() - start) * 1000, 1)
    return result


async def run_probes(
    *,
    settings: ExplorationSettings | None = None,
    only: list[str] | None = None,
    capture: bool = True,
) -> list[ExplorationResult]:
    """Run eligible probes and (optionally) capture their output.

    Args:
        settings: sandbox settings; defaults to ``get_exploration_settings()``.
        only:     optional list of probe_ids or source_names to restrict the run.
        capture:  when True, write raw + normalized artifacts.

    Returns:
        One ExplorationResult per probe that ran (registry order).
    """
    cfg = settings or get_exploration_settings()
    registry = build_registry(cfg)

    selected = _select(registry, only)
    if not selected:
        logger.info("[exploration] no eligible probes (only=%s)", only)
        return []

    results: list[ExplorationResult] = []
    for index, probe in enumerate(selected):
        if index > 0 and cfg.min_request_interval_seconds > 0:
            await asyncio.sleep(cfg.min_request_interval_seconds)

        result = await _run_one(probe, timeout=cfg.timeout_seconds)
        results.append(result)

        if capture:
            write_raw(result, artifacts_dir=cfg.artifacts_dir)
            append_normalized(result, artifacts_dir=cfg.artifacts_dir)

        status = "ok" if result.success else f"FAIL({result.error})"
        logger.info(
            "[exploration] %s -> %s records=%d",
            result.probe_id,
            status,
            result.record_count,
        )

    return results


def _select(
    registry: dict[str, ExplorationProbe],
    only: list[str] | None,
) -> list[ExplorationProbe]:
    if not only:
        return list(registry.values())
    wanted = {item.strip().lower() for item in only if item.strip()}
    out: list[ExplorationProbe] = []
    for probe_id, probe in registry.items():
        if probe_id.lower() in wanted or probe.source_name.lower() in wanted:
            out.append(probe)
    return out
