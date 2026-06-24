"""L2 on-chain flow features (KAI Sprint 2, shadow-only, B-003 direction-agnostic).

Computes RAW percentile features of the current on-chain state (fee rate, mempool
depth) within a recent window of KAI's OWN L1 fee-shadow stream
(``artifacts/onchain_fee_shadow.jsonl``, written by the L1 fee-truth scheduler).

Source = KAI's own bitcoind series (no new provider, no new key — the plan's
"Quelle = KAIs eigene Serie"). DIRECTION-AGNOSTIC by construction: we record raw
features only; whether a high-fee/congested mempool is contrarian or pro-trend is
LEARNED later by ``scripts/evaluate_l2_evidence.py`` (B-003), never hardcoded here.

Read-only, fail-soft: a missing/corrupt stream yields no features; the shadow log
never raises into the signal path.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OnchainFlowFeatures:
    """Raw on-chain flow features at signal time. Percentiles are the rank of the
    current value within the recent window (0..1), ``None`` if the window is empty.
    """

    fee_sat_vb: float | None
    mempool_tx: int
    fee_percentile: float | None
    mempool_percentile: float | None
    window_n: int


def percentile_rank(value: float | None, window: Sequence[float]) -> float | None:
    """Fraction of ``window`` values <= ``value`` (0..1). ``None`` if the window is
    empty or ``value`` is ``None`` — honest "cannot rank", not a fabricated 0.5."""
    if value is None or not window:
        return None
    leq = sum(1 for w in window if w <= value)
    return leq / len(window)


def read_onchain_fee_shadow(path: Path | str, *, limit: int = 500) -> list[dict[str, Any]]:
    """Tolerant tail reader for the L1 fee-shadow stream. Missing file → ``[]``;
    blank/corrupt lines skipped; returns up to the last ``limit`` JSON objects."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out[-limit:] if limit > 0 else out


def compute_l2_features(
    history: Sequence[dict[str, Any]],
    *,
    fee_sat_vb: float | None,
    mempool_tx: int,
) -> OnchainFlowFeatures:
    """Compute percentile features of the current (fee, mempool) within ``history``.

    ``history`` is a window of prior fee-shadow records (each a dict with
    ``fee_sat_vb`` / ``mempool_tx``). A ``None`` fee in history is skipped from the
    fee window (the L1 estimate is best-effort). The fee window count drives
    ``window_n``.
    """
    fee_window = [
        float(r["fee_sat_vb"])
        for r in history
        if isinstance(r, dict) and r.get("fee_sat_vb") is not None
    ]
    mempool_window = [
        float(r["mempool_tx"])
        for r in history
        if isinstance(r, dict) and r.get("mempool_tx") is not None
    ]
    return OnchainFlowFeatures(
        fee_sat_vb=fee_sat_vb,
        mempool_tx=mempool_tx,
        fee_percentile=percentile_rank(fee_sat_vb, fee_window),
        mempool_percentile=percentile_rank(float(mempool_tx), mempool_window),
        window_n=len(fee_window),
    )


def append_l2_shadow_log(
    path: Path | str,
    *,
    symbol: str,
    direction: str,
    features: OnchainFlowFeatures,
    source_trust: float,
) -> None:
    """Append one RAW-feature measurement line (append-only JSONL).

    B-003: records ONLY raw features + the candidate signal context — NO
    pre-chosen direction-aligned strength. The contrarian/pro-trend direction is
    learned downstream from the joined outcomes. Fail-soft: a write error is
    logged and swallowed (the measurement must never kill the signal path).
    """
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "direction": direction,
        "fee_sat_vb": features.fee_sat_vb,
        "mempool_tx": features.mempool_tx,
        "fee_percentile": features.fee_percentile,
        "mempool_percentile": features.mempool_percentile,
        "window_n": features.window_n,
        "source_trust": source_trust,
    }
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — measurement must never kill the signal path
        logger.warning("[l2-shadow] append failed: %s", exc)


__all__ = [
    "OnchainFlowFeatures",
    "append_l2_shadow_log",
    "compute_l2_features",
    "percentile_rank",
    "read_onchain_fee_shadow",
]
