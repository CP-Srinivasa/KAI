"""Canonical, FILL-INDEPENDENT outcome loader for shadow hypothesis evaluation.

The single most important lever of the ADR-0012 truth platform is that a
hypothesis is scored against realized OUTCOMES that accumulate on their own —
NOT against paper fills that only materialize when a real trade happens to land
on a target symbol (5-7/day). That coupling is exactly what starved the
Momentum-Universe program (n>=30 took months). The V5 funding/OI evaluator never
had that problem because it scores against the resolved shadow-candidate pool
(``artifacts/shadow_candidate_resolved.jsonl``, ~8k rows, fill-independent).

This module lifts V5's outcome-loading verbatim into ONE canonical place so every
evaluator — funding, OI, momentum, L2, and any future pre-registered hypothesis —
draws from the same fill-independent pool instead of a hand-produced outcomes file.

  * :func:`load_canonical_outcomes` — resolved shadow candidates -> time-ordered
    outcomes ``{symbol, side, entry_ts, fwd:{h:bps}}`` with side-adjusted forward
    returns (``fwd_{h}s_bps`` > 0 means the candidate's OWN direction paid).
  * :func:`to_feature_outcomes` — project one horizon into the flat
    ``{symbol, entry_ts, net_bps}`` shape the raw-feature evaluators
    (:func:`app.observability.l2_evidence_eval.pit_join` /
    ``evaluate_feature_direction``) consume — so momentum/L2 no longer REQUIRE a
    hand-supplied ``--outcomes`` file.

Read-only analysis; nothing here touches sizing or the execution path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Canonical horizons (seconds) carried by every resolved shadow candidate.
HORIZONS: tuple[int, ...] = (60, 300, 900, 3600)
_SIDES = ("long", "short")

DEFAULT_RESOLVED_PATH = Path("artifacts/shadow_candidate_resolved.jsonl")
DEFAULT_LEDGER_PATH = Path("artifacts/shadow_candidate_ledger.jsonl")
# A |fwd| at/above this is a delisted/no-data sentinel, not a real return.
DEFAULT_MAX_ABS_BPS = 5000.0


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Tolerant JSONL read: missing file -> []; a corrupt line is skipped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def parse_ts(ts: object) -> datetime | None:
    """Parse an ISO-8601 timestamp; naive values are assumed UTC. ``None`` on junk."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def load_entry_times(ledger: list[dict[str, Any]]) -> dict[str, datetime]:
    """``candidate_id -> entry ts`` from the shadow-candidate ledger."""
    out: dict[str, datetime] = {}
    for r in ledger:
        cid = r.get("candidate_id")
        ts = parse_ts(r.get("ts_utc"))
        if cid and ts is not None:
            out[str(cid)] = ts
    return out


def entry_ts_for(cand: dict[str, Any], entry_times: dict[str, datetime]) -> datetime | None:
    """Entry time of a candidate: ledger ``ts_utc``, else the ISO ts embedded in a
    ``tech-<SYM>-<iso>`` id (autonomous_generator ``cyc_*`` ids carry no time)."""
    cid = str(cand.get("candidate_id", ""))
    if cid in entry_times:
        return entry_times[cid]
    parts = cid.split("-", 2)
    if len(parts) == 3:
        return parse_ts(parts[2])
    return None


def build_outcomes(
    resolved: list[dict[str, Any]],
    entry_times: dict[str, datetime],
    *,
    max_abs_bps: float = DEFAULT_MAX_ABS_BPS,
) -> list[dict[str, Any]]:
    """Resolved shadow candidates -> time-ordered outcome records.

    Each outcome is ``{symbol, side, entry_ts (datetime), fwd:{h: bps|None}}`` with
    SIDE-ADJUSTED forward returns. Rows with a sentinel ``|fwd| >= max_abs_bps``
    (delisted/no-data), an unknown side, no entry time, or no usable horizon are
    dropped. The list is sorted by ``entry_ts`` so a downstream moving-block
    bootstrap preserves autocorrelation.
    """
    out: list[dict[str, Any]] = []
    for c in resolved:
        sym = c.get("symbol")
        side = c.get("side")
        ets = entry_ts_for(c, entry_times)
        if not sym or side not in _SIDES or ets is None:
            continue
        fwd: dict[int, float | None] = {}
        sentinel = False
        for h in HORIZONS:
            v = c.get(f"fwd_{h}s_bps")
            if v is None:
                fwd[h] = None
                continue
            fv = float(v)
            if abs(fv) >= max_abs_bps:  # delisted / no-data sentinel, not signal
                sentinel = True
                break
            fwd[h] = fv
        if sentinel or all(fwd.get(h) is None for h in HORIZONS):
            continue
        out.append({"symbol": str(sym), "side": str(side), "entry_ts": ets, "fwd": fwd})
    out.sort(key=lambda o: o["entry_ts"])  # time-ordered → autocorr-preserving bootstrap
    return out


def load_canonical_outcomes(
    *,
    resolved_path: Path = DEFAULT_RESOLVED_PATH,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    max_abs_bps: float = DEFAULT_MAX_ABS_BPS,
) -> list[dict[str, Any]]:
    """Convenience: read the resolved + ledger files and build the outcome pool."""
    resolved = read_jsonl(Path(resolved_path))
    entry_times = load_entry_times(read_jsonl(Path(ledger_path)))
    return build_outcomes(resolved, entry_times, max_abs_bps=max_abs_bps)


def to_feature_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    horizon: int = 3600,
) -> list[dict[str, Any]]:
    """Project canonical outcomes into the flat raw-feature-evaluator shape.

    ``{symbol, entry_ts (ISO str), net_bps}`` for the given horizon, dropping
    outcomes with no return at that horizon. This lets the momentum / L2 feature
    evaluators (which pit-join on ``entry_ts`` and read ``net_bps``) run against
    the fill-independent canonical pool instead of a hand-produced file.
    """
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}, got {horizon}")
    out: list[dict[str, Any]] = []
    for o in outcomes:
        v = o["fwd"].get(horizon)
        if v is None:
            continue
        ets = o["entry_ts"]
        out.append(
            {
                "symbol": o["symbol"],
                "entry_ts": ets.isoformat() if isinstance(ets, datetime) else str(ets),
                "net_bps": float(v),
            }
        )
    return out


__all__ = [
    "DEFAULT_LEDGER_PATH",
    "DEFAULT_MAX_ABS_BPS",
    "DEFAULT_RESOLVED_PATH",
    "HORIZONS",
    "build_outcomes",
    "entry_ts_for",
    "load_canonical_outcomes",
    "load_entry_times",
    "parse_ts",
    "read_jsonl",
    "to_feature_outcomes",
]
