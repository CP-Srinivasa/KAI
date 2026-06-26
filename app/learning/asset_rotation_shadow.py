"""asset_rotation_shadow — G1 shadow evaluator (measurement-only, no feed).

Wires the pure rotation core (verdict + FSM + policy) to real paper data:
``paper_quality_snapshot.by_symbol`` → per-asset verdict → rotation decision,
guarded by the FSM, with the hysteresis counter persisted across runs. Writes a
shadow log of decisions and the carried state. **NOTHING acts on the decisions**
— no feed, no sizing, no capital. This is the "rotiert nachvollziehbar"-Sicht;
promoting it to actually gate the feeder is a later, edge-gated step.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.learning.asset_lifecycle import AssetStatus, can_transition
from app.learning.asset_performance_score import AssetWindowStats, evaluate_asset
from app.learning.asset_rotation_policy import decide_asset_rotation

_DEFAULT_MIN_CLOSES = 5
_DEFAULT_WILSON_FLOOR = 0.5


@dataclass(frozen=True)
class AssetRotationState:
    status: AssetStatus
    flagged_runs: int


def evaluate_rotations(
    by_symbol: Mapping[str, Mapping[str, float]],
    prior_state: Mapping[str, AssetRotationState],
    *,
    min_closes: int = _DEFAULT_MIN_CLOSES,
    wilson_floor: float = _DEFAULT_WILSON_FLOOR,
) -> tuple[list[dict[str, object]], dict[str, AssetRotationState]]:
    """Pure: map paper stats → verdict → policy → FSM-guarded next state.

    Returns ``(decisions, new_state)``. An illegal proposed transition is dropped
    (status unchanged) — the policy proposes, the FSM disposes.
    """
    decisions: list[dict[str, object]] = []
    new_state: dict[str, AssetRotationState] = dict(prior_state)
    for symbol, stats in by_symbol.items():
        closes = int(stats.get("count", 0.0))
        wins = int(stats.get("wins", 0.0))
        net = float(stats.get("sum_pnl_usd", 0.0))
        verdict = evaluate_asset(
            AssetWindowStats(symbol=symbol, net_pnl_usd=net, closes=closes, wins=wins),
            min_closes=min_closes,
            wilson_floor=wilson_floor,
        )
        prior = prior_state.get(symbol, AssetRotationState(AssetStatus.PROBATION, 0))
        decision = decide_asset_rotation(
            prior.status,
            verdict,
            pinned=(prior.status == AssetStatus.PINNED),
            prior_flagged_runs=prior.flagged_runs,
        )
        if decision.target is not None and can_transition(prior.status, decision.target):
            next_status = decision.target
        else:
            next_status = prior.status
        new_state[symbol] = AssetRotationState(next_status, decision.flagged_runs)
        wilson_lb = round(verdict.wilson_lb, 4) if verdict.wilson_lb is not None else None
        verdict_label = "healthy" if verdict.healthy else "weak" if verdict.weak else "insufficient"
        decisions.append(
            {
                "symbol": symbol,
                "from": prior.status.value,
                "to": next_status.value,
                "changed": next_status != prior.status,
                "reason": decision.reason,
                "verdict": verdict_label,
                "net_pnl_usd": round(net, 4),
                "closes": closes,
                "wins": wins,
                "wilson_lb": wilson_lb,
                "flagged_runs": decision.flagged_runs,
            }
        )
    return decisions, new_state


def load_state(path: Path) -> dict[str, AssetRotationState]:
    """Load the persisted per-symbol rotation state; missing/corrupt → ``{}``."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, AssetRotationState] = {}
    for symbol, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        status_raw = entry.get("status")
        if not isinstance(status_raw, str):
            continue
        try:
            status = AssetStatus(status_raw)
        except ValueError:
            continue
        try:
            flagged = int(entry.get("flagged_runs", 0))
        except (TypeError, ValueError):
            flagged = 0
        out[str(symbol)] = AssetRotationState(status, max(0, flagged))
    return out


def save_state(path: Path, state: Mapping[str, AssetRotationState]) -> None:
    """Persist the per-symbol rotation state atomically-ish (write then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        symbol: {"status": st.status.value, "flagged_runs": st.flagged_runs}
        for symbol, st in state.items()
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def run_rotation_shadow(
    *,
    audit_path: Path,
    state_path: Path,
    shadow_log_path: Path,
    last_n: int,
    now: datetime,
) -> dict[str, object]:
    """Read paper closes → evaluate → append a shadow record + persist state.

    Returns the shadow record. Side effects only: never affects trading/sizing.
    """
    from app.observability.paper_quality_snapshot import build_paper_quality_snapshot

    snapshot = build_paper_quality_snapshot(audit_path=str(audit_path), last_n=last_n)
    prior = load_state(state_path)
    decisions, new_state = evaluate_rotations(snapshot.by_symbol, prior)
    record: dict[str, object] = {
        "ts": now.isoformat(),
        "evaluated": len(decisions),
        "changes": sum(1 for d in decisions if d["changed"]),
        "decisions": decisions,
    }
    shadow_log_path.parent.mkdir(parents=True, exist_ok=True)
    with shadow_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    save_state(state_path, new_state)
    return record
