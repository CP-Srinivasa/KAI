"""Behavioural KYT analyzers over the order/fill history.

These are the risk signals KAI can actually compute today (no external data):
structuring, round-tripping, frequency spikes, amount anomalies and profile
deviation. Round-tripping in particular is the signature of the 2026-05-28 MATIC
incident (the loop re-opened+closed the same symbol every cycle), so this layer
would have flagged it independently of the price-source fix.

All functions are pure: they take an already-parsed history (list of dicts with
``timestamp_utc``, ``symbol``, ``side``, ``notional_usd``) plus the current
context, and return flags. Robust to missing/garbage fields — never raises.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime, timedelta

from app.security.kyt.models import KytFlag, KytReasonCode, KytRiskLevel, TransactionContext
from app.security.kyt.rules import KytRules, _norm_asset


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def analyze_behavioral(
    context: TransactionContext,
    history: Sequence[dict[str, object]],
    rules: KytRules,
) -> list[KytFlag]:
    """Return behavioural flags for ``context`` given recent ``history``.

    ``history`` should be recent transactions for the same system/portfolio,
    newest-last is not required. Empty history → a single INSUFFICIENT_DATA
    unknown flag (we do not pretend a clean profile from no data).
    """
    flags: list[KytFlag] = []
    now = _parse_ts(context.timestamp_utc) or datetime.now().astimezone()

    parsed: list[tuple[datetime, str, str, float | None]] = []
    for row in history:
        ts = _parse_ts(row.get("timestamp_utc") or row.get("filled_at") or row.get("created_at"))
        sym = row.get("symbol")
        if ts is None or not isinstance(sym, str):
            continue
        notional = _coerce_float(row.get("notional_usd"))
        if notional is None:
            q = _coerce_float(row.get("quantity"))
            p = _coerce_float(row.get("fill_price") or row.get("entry_price"))
            notional = q * p if (q is not None and p is not None) else None
        side = row.get("side")
        parsed.append((ts, sym, side if isinstance(side, str) else "", notional))

    if not parsed:
        flags.append(
            KytFlag(
                code=KytReasonCode.INSUFFICIENT_DATA,
                level=KytRiskLevel.UNKNOWN,
                detail="No transaction history available for behavioural analysis.",
                source="behavioral",
                data_available=False,
            )
        )
        return flags

    cur_asset = _norm_asset(context.symbol) if context.symbol else None

    # --- round-tripping: repeated full cycles on the same asset in a window ---
    if cur_asset is not None:
        window = now - timedelta(minutes=rules.round_trip_window_minutes)
        same_asset = [p for p in parsed if _norm_asset(p[1]) == cur_asset and p[0] >= window]
        # a "cycle" proxy = a sell/close on the asset within the window
        cycles = sum(1 for _, _, side, _ in same_asset if side in ("sell", "close"))
        if cycles >= rules.round_trip_min_cycles:
            flags.append(
                KytFlag(
                    code=KytReasonCode.ROUND_TRIPPING,
                    level=KytRiskLevel.HIGH,
                    detail=(
                        f"{cycles} close cycles on {cur_asset} within "
                        f"{rules.round_trip_window_minutes}min (>= {rules.round_trip_min_cycles})."
                    ),
                    source="behavioral",
                )
            )

    # --- frequency spike: total tx rate in the last hour ---
    hour_ago = now - timedelta(hours=1)
    last_hour = [p for p in parsed if p[0] >= hour_ago]
    if len(last_hour) > rules.frequency_spike_per_hour:
        flags.append(
            KytFlag(
                code=KytReasonCode.FREQUENCY_SPIKE,
                level=KytRiskLevel.MEDIUM,
                detail=f"{len(last_hour)} tx in last hour (> {rules.frequency_spike_per_hour}).",
                source="behavioral",
            )
        )

    # --- structuring: many sub-threshold trades on the same asset in a window ---
    if cur_asset is not None:
        s_window = now - timedelta(minutes=rules.structuring_window_minutes)
        small = [
            p
            for p in parsed
            if _norm_asset(p[1]) == cur_asset
            and p[0] >= s_window
            and p[3] is not None
            and 0 < p[3] < rules.structuring_max_notional_usd
        ]
        if len(small) >= rules.structuring_min_count:
            flags.append(
                KytFlag(
                    code=KytReasonCode.STRUCTURING,
                    level=KytRiskLevel.MEDIUM,
                    detail=(
                        f"{len(small)} sub-${rules.structuring_max_notional_usd:.0f} {cur_asset} "
                        f"trades within {rules.structuring_window_minutes}min."
                    ),
                    source="behavioral",
                )
            )

    # --- amount anomaly: current notional vs history distribution ---
    cur_notional = context.notional_usd
    if cur_notional is None and context.quantity is not None and context.entry_price is not None:
        cur_notional = context.quantity * context.entry_price
    notionals = [p[3] for p in parsed if p[3] is not None and p[3] > 0]
    if cur_notional is not None and cur_notional > 0 and len(notionals) >= 5:
        mean = statistics.fmean(notionals)
        stdev = statistics.pstdev(notionals)
        if stdev > 0:
            z = (cur_notional - mean) / stdev
            if z >= rules.amount_anomaly_z:
                flags.append(
                    KytFlag(
                        code=KytReasonCode.AMOUNT_ANOMALY,
                        level=KytRiskLevel.HIGH
                        if z >= rules.amount_anomaly_z * 1.5
                        else KytRiskLevel.MEDIUM,
                        detail=f"Notional ${cur_notional:.2f} = {z:.1f}σ above mean ${mean:.2f}.",
                        source="behavioral",
                    )
                )

    # --- profile deviation: asset never seen before in history ---
    if cur_asset is not None:
        seen_assets = {_norm_asset(p[1]) for p in parsed}
        if cur_asset not in seen_assets:
            flags.append(
                KytFlag(
                    code=KytReasonCode.PROFILE_DEVIATION,
                    level=KytRiskLevel.LOW,
                    detail=f"{cur_asset} not present in recent transaction profile.",
                    source="behavioral",
                )
            )

    return flags
