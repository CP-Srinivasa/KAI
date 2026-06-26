"""Momentum-Universe evidence features + shadow log (G3, read-only).

Reads the warm G0 universe snapshot (Disk-Read, no network) and the per-symbol
momentum percentiles, and appends a measure-first shadow record. No sizing
effect — the evidence is inert (``direction_aligned=0``) until
``scripts/evaluate_momentum_evidence.py`` learns a sign.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from app.observability.momentum_universe_ledger import read_latest

_SCORE_KEYS = ("momentum_score", "volume_score", "universe_score", "rank")


def read_universe_scores(ledger_path: Path) -> tuple[str | None, dict[str, dict[str, float]]]:
    """Return ``(snapshot_ts, {symbol: {momentum_score, volume_score, universe_score, rank}})``.

    ``(None, {})`` when no snapshot exists. Non-numeric / missing fields default to 0.0.
    """
    snapshot = read_latest(ledger_path)
    if not isinstance(snapshot, dict):
        return None, {}
    ts = snapshot.get("ts")
    universe = snapshot.get("universe")
    out: dict[str, dict[str, float]] = {}
    if isinstance(universe, list):
        for row in universe:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                continue
            scores: dict[str, float] = {}
            for key in _SCORE_KEYS:
                try:
                    scores[key] = float(row.get(key, 0.0))
                except (TypeError, ValueError):
                    scores[key] = 0.0
            out[symbol] = scores
    return (ts if isinstance(ts, str) else None), out


def append_momentum_shadow_log(
    path: Path,
    *,
    symbol: str,
    direction: str,
    scores: Mapping[str, float],
    source_trust: float,
    now: datetime | None = None,
) -> None:
    """Append one read-only momentum-evidence shadow record (measure-first)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": (now or datetime.now(UTC)).isoformat(),
        "symbol": symbol,
        "direction": direction,
        "source_trust": source_trust,
        "evidence_direction_aligned": 0,
        "momentum_score": float(scores.get("momentum_score", 0.0)),
        "volume_score": float(scores.get("volume_score", 0.0)),
        "universe_score": float(scores.get("universe_score", 0.0)),
        "rank": float(scores.get("rank", 0.0)),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


__all__ = ["append_momentum_shadow_log", "read_universe_scores"]
