"""Lightning node reputation telemetry (KAI observability, default-off).

Periodically snapshots the node's *reputation-relevant* health into an append-only
shadow stream so an uptime / connectivity / routing-income trend accumulates over
time. Read-only, fail-soft, NO capital path. Mirrors the L1 fee-shadow recorder
(:mod:`app.chain.fee_shadow`).

Difference from the fee shadow: a reachable-failure (``unavailable``) IS recorded —
downtime is itself a reputation signal (needed to compute uptime%). Only the
default-off ``disabled`` case is a no-op, so the stream is not spammed while the
feature is off. Routing income is best-effort: a ``feereport`` failure must never
drop the health record.

See KAI-mirror/kai_btc_ln_future_integration_20260616.md (node reputation as the
OTS-anchored track-record artefact inside the Truth Oracle).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.settings import LightningSettings
from app.lightning.adapter import get_fee_report, get_node_status
from app.lightning.jsonl_tail import read_recent_jsonl

logger = logging.getLogger(__name__)

_REP_PATH = Path("artifacts/ln_reputation.jsonl")


@dataclass(frozen=True)
class LnReputationRecord:
    """One node-reputation observation (shadow, read-only, no capital path).

    ``state`` is ``ok`` or ``unavailable`` (``disabled`` is never recorded). The
    ``routing_fee_*`` fields are ``None`` when the fee report could not be read
    (best-effort), and a real ``0`` only when the node confirmed no routing income.
    """

    ts: str  # ISO-8601 UTC
    state: str
    reachable: bool
    info_available: bool
    num_peers: int
    num_active_channels: int
    num_pending_channels: int
    synced_to_chain: bool
    synced_to_graph: bool
    channel_local_sat: int
    channel_remote_sat: int
    wallet_confirmed_sat: int
    wallet_total_sat: int
    routing_fee_day_sat: int | None
    routing_fee_week_sat: int | None
    routing_fee_month_sat: int | None
    alias: str
    identity_pubkey: str


async def record_ln_reputation(
    cfg: LightningSettings | None = None,
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> LnReputationRecord | None:
    """Append one reputation record unless the feature is ``disabled``.

    Returns the written record, or ``None`` when the feature is off (no-op) or when
    persistence failed (logged). Never raises — safe to call from a cron/timer.
    """
    status = await get_node_status(cfg)
    if status.state == "disabled":
        return None  # default-off → no-op (do not spam the stream while off)

    routing_day: int | None = None
    routing_week: int | None = None
    routing_month: int | None = None
    if status.state == "ok":
        try:
            fr = await get_fee_report(cfg)
            if fr.available:
                routing_day = fr.day_fee_sat
                routing_week = fr.week_fee_sat
                routing_month = fr.month_fee_sat
        except Exception as exc:  # noqa: BLE001 — routing income is best-effort
            logger.warning("[ln-reputation] fee report failed (best-effort): %s", exc)

    rec = LnReputationRecord(
        ts=(now or datetime.now(UTC)).isoformat(),
        state=status.state,
        reachable=status.reachable,
        info_available=status.info_available,
        num_peers=status.num_peers,
        num_active_channels=status.num_active_channels,
        num_pending_channels=status.num_pending_channels,
        synced_to_chain=status.synced_to_chain,
        synced_to_graph=status.synced_to_graph,
        channel_local_sat=status.channel_local_sat,
        channel_remote_sat=status.channel_remote_sat,
        wallet_confirmed_sat=status.wallet_confirmed_sat,
        wallet_total_sat=status.wallet_total_sat,
        routing_fee_day_sat=routing_day,
        routing_fee_week_sat=routing_week,
        routing_fee_month_sat=routing_month,
        alias=status.alias,
        identity_pubkey=status.identity_pubkey,
    )
    out = path or _REP_PATH
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec)) + "\n")
    except OSError as exc:
        logger.warning("[ln-reputation] persist failed: %s", exc)
        return None
    return rec


def read_recent_ln_reputation(
    path: Path | None = None, *, limit: int = 200
) -> list[dict[str, Any]]:
    """Read the most recent reputation records (newest last). Tolerant reader for
    the dashboard endpoint: a missing file → ``[]``, blank/corrupt lines skipped."""
    return read_recent_jsonl(path or _REP_PATH, limit=limit)
