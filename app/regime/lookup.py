"""Historic regime lookup — find the regime classification effective at a given
timestamp.

R3-Shadow (2026-05-16): the trading-loop and the forward-outcome bucketing both
need to know "which regime was active when this cycle ran", not just "what is
the current regime". ``app/regime/storage.py`` already provides
``latest_regime_snapshot`` for the live case; this module fills the historic
case.

Lookup semantics:
- Snapshots are stored hour-truncated (one per asset per hour, see
  ``regime/models.py`` + ``regime/service.py``).
- For a target timestamp ``t``, we return the snapshot whose own timestamp is
  the largest value ``<= t`` — i.e. the regime that had just been committed
  when ``t`` happened. This avoids look-ahead bias: a snapshot timestamped
  09:00 UTC was committed at 09:00 UTC and is valid for ``[09:00, 10:00)``.
- A ``max_age_seconds`` parameter rejects stale lookups. Default 24h matches
  the operator-staleness threshold elsewhere in the codebase. Returning
  ``None`` on stale is intentional — the consumer must decide how to handle
  missing-regime (audit-with-null vs degrade-to-unknown).

Symbol→asset mapping:
- The R1 classifier covers BTC and ETH only (see ``regime/service.py``).
- For other symbols the consumer should fall back to BTC as a market-wide
  proxy (documented in ``trading_loop.py`` integration). This module does NOT
  auto-fallback — it answers "did we ever classify this asset?" honestly so
  the caller can log the proxy explicitly.

KAI-no-prediction-rule: nothing in this module predicts a future regime. It
strictly returns past observations.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.regime.models import RegimeSnapshot
from app.regime.storage import DEFAULT_REGIME_DIR, load_regime_snapshots, resolve_regime_path

# R1 classifier coverage — see regime/service.py. Symbols outside this set
# fall through symbol_to_regime_asset() to "BTC" as a proxy. Direct callers
# of get_regime_at() that bypass that mapping get "asset_unsupported".
SUPPORTED_REGIME_ASSETS = frozenset({"BTC", "ETH"})

# 24h matches the freshness-threshold pattern used in
# app/observability/premium_pipeline_health.py and the operator staleness
# tolerance in app/learning/calibration.py.
DEFAULT_MAX_AGE_SECONDS: float = 24 * 60 * 60.0


@dataclass(frozen=True)
class RegimeLookupResult:
    """Result of a historic regime lookup.

    ``snapshot`` is None when no eligible snapshot exists (asset never
    classified, file empty, all snapshots older than ``max_age_seconds``
    before the target timestamp, or all snapshots newer than the target).
    ``reason`` carries a short machine-readable code so audit-streams can
    distinguish "no data" from "stale" from "future-only".
    """

    snapshot: RegimeSnapshot | None
    asset: str
    target_timestamp_utc: str
    age_seconds: float | None
    reason: str
    # reason ∈ {
    #   "ok"                 — snapshot returned, age ≤ max_age_seconds
    #   "all_future"         — earliest snapshot is newer than target (no look-ahead)
    #   "stale"              — best snapshot exceeds max_age_seconds
    #   "no_snapshot_file"   — asset JSONL does not exist (storage/path drift,
    #                          regime-classifier never ran, or wrong CWD)
    #   "no_snapshots_data"  — file exists but no parseable snapshot inside
    #   "asset_unsupported"  — caller asked for asset outside R1 coverage
    #                          (R1 covers BTC + ETH; mapping handled by
    #                          symbol_to_regime_asset)
    #   "invalid_timestamp"  — target_timestamp_utc could not be parsed
    # }
    # NOTE: the legacy "asset_unknown" / "no_history" reasons were collapsed
    # into one bucket that the trading_loop audit could not act on. The
    # split surfaces *why* the lookup failed so operators can distinguish
    # "BTC file missing on the workstation" from "XRP not in R1 coverage".


def _parse_iso(timestamp_str: str) -> datetime | None:
    """Parse ISO-8601 timestamps written by ``RegimeSnapshot.to_json_dict``.

    The regime-service writes hour-truncated ISO strings without a timezone
    suffix ("2026-05-16T11:00:00Z"). Some legacy lines lack the trailing Z.
    Both forms are accepted.
    """
    if not timestamp_str:
        return None
    cleaned = timestamp_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def get_regime_at(
    asset: str,
    target_timestamp_utc: str,
    *,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    base_dir: str | Path = DEFAULT_REGIME_DIR,
) -> RegimeLookupResult:
    """Return the regime snapshot effective at ``target_timestamp_utc``.

    Effective = the snapshot whose ``timestamp`` is the largest value
    ``<= target_timestamp_utc`` AND not older than ``max_age_seconds``.

    Returns ``RegimeLookupResult`` with ``snapshot=None`` and a reason
    code when no eligible snapshot exists. The caller decides how to
    handle the absence (see trading_loop integration).
    """
    target_dt = _parse_iso(target_timestamp_utc)
    if target_dt is None:
        return RegimeLookupResult(
            snapshot=None,
            asset=asset,
            target_timestamp_utc=target_timestamp_utc,
            age_seconds=None,
            reason="invalid_timestamp",
        )

    if not asset or asset.upper() not in SUPPORTED_REGIME_ASSETS:
        return RegimeLookupResult(
            snapshot=None,
            asset=asset,
            target_timestamp_utc=target_timestamp_utc,
            age_seconds=None,
            reason="asset_unsupported",
        )

    # Differentiate "file missing" from "file present but empty" so the
    # trading-loop audit makes the regime-pipeline failure mode actionable.
    # Workstation 2026-05-26 had no artifacts/regime_state/ at all while
    # the Pi was happily writing hourly snapshots — pre-fix both legitimate
    # absences and a missing pipeline collapsed to "asset_unknown".
    if not resolve_regime_path(asset, base_dir).exists():
        return RegimeLookupResult(
            snapshot=None,
            asset=asset,
            target_timestamp_utc=target_timestamp_utc,
            age_seconds=None,
            reason="no_snapshot_file",
        )

    snapshots = load_regime_snapshots(asset, base_dir)
    if not snapshots:
        return RegimeLookupResult(
            snapshot=None,
            asset=asset,
            target_timestamp_utc=target_timestamp_utc,
            age_seconds=None,
            reason="no_snapshots_data",
        )

    # Build a parallel list of (datetime, snapshot) sorted by timestamp.
    # The on-disk file is append-only oldest-first but we tolerate same-hour
    # re-runs by honouring the *last* write per timestamp (the storage
    # contract): later snapshots replace earlier ones at the same hour.
    by_ts: dict[datetime, RegimeSnapshot] = {}
    for snap in snapshots:
        dt = _parse_iso(snap.timestamp)
        if dt is None:
            continue
        by_ts[dt] = snap  # later write at same ts overwrites earlier
    if not by_ts:
        # File had lines but none parsed as valid timestamps — same operator
        # signal as "data missing".
        return RegimeLookupResult(
            snapshot=None,
            asset=asset,
            target_timestamp_utc=target_timestamp_utc,
            age_seconds=None,
            reason="no_snapshots_data",
        )

    sorted_dts = sorted(by_ts.keys())

    # bisect_right returns the insertion point such that everything to the
    # left is <= target. So sorted_dts[idx-1] is the largest dt <= target.
    idx = bisect.bisect_right(sorted_dts, target_dt)
    if idx == 0:
        # Target predates the earliest snapshot — no look-ahead allowed.
        return RegimeLookupResult(
            snapshot=None,
            asset=asset,
            target_timestamp_utc=target_timestamp_utc,
            age_seconds=None,
            reason="all_future",
        )

    effective_dt = sorted_dts[idx - 1]
    snapshot = by_ts[effective_dt]
    age = (target_dt - effective_dt).total_seconds()

    if age > max_age_seconds:
        return RegimeLookupResult(
            snapshot=None,
            asset=asset,
            target_timestamp_utc=target_timestamp_utc,
            age_seconds=age,
            reason="stale",
        )

    return RegimeLookupResult(
        snapshot=snapshot,
        asset=asset,
        target_timestamp_utc=target_timestamp_utc,
        age_seconds=age,
        reason="ok",
    )


def symbol_to_regime_asset(symbol: str) -> str:
    """Map a trading symbol to the regime-asset used for lookup.

    R1 covers BTC and ETH directly. For everything else the trading-loop
    treats BTC as a market-wide proxy — documented in the trading_loop
    integration so the audit stream stamps both ``regime_symbol_asset``
    (BTC) and ``regime_symbol_is_proxy`` (True/False) for forensics.
    """
    if not symbol:
        return "BTC"
    head = symbol.upper().split("/", 1)[0].split("-", 1)[0]
    if head in {"BTC", "XBT"}:
        return "BTC"
    if head == "ETH":
        return "ETH"
    # Stablecoins-as-asset-base are nonsensical here; fall back to BTC.
    return "BTC"


def now_utc_iso() -> str:
    """Helper for trading_loop and tests — current UTC timestamp ISO-8601.

    Centralised so a future "freeze time for shadow replay" toggle has one
    place to swap.
    """
    return datetime.now(UTC).isoformat()


__all__ = [
    "DEFAULT_MAX_AGE_SECONDS",
    "SUPPORTED_REGIME_ASSETS",
    "RegimeLookupResult",
    "get_regime_at",
    "now_utc_iso",
    "symbol_to_regime_asset",
]
