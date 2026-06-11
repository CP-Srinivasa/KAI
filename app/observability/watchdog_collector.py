"""Watchdog collector — wire the passive scoring cores to real artifacts (#167).

PR #166 built two pure scoring cores deliberately WITHOUT wiring (real data did
not exist yet — wiring then would have produced canary-contaminated
scoreboards, the V1-shadow-degeneration lesson). The S4 wiring made the real
generator measurable; this module is the deferred collector:

  - **Source reputation**: ``monitor/source_reliability.json`` (realised Wilson
    lower bound per source) + the D-227 blocked-outcome report (realised
    precision per source) → :class:`SourceReputationInputs` →
    ``build_source_reputation_report``.
  - **Agent scoreboard**: the generator-edge report (#161, fed by the #170-B
    side-channel collector) → :class:`AgentMetricInputs` per agent cohort →
    ``build_agent_scoreboard``.

Hard gates (issue #167):
  - **Activation gate**: nothing is emitted while ``real_resolved == 0`` —
    the resolved shadow ledger must contain at least one REAL generator
    resolution before any scoreboard exists.
  - **Canary exclusion**: probe cohorts never enter the scoreboard inputs.
  - Advisory-only invariant stays untouched: the cores hard-code
    ``can_trigger_execution_alone=False`` / advisory ranking.

Dropbox emission follows the honest-by-design agent pattern: one JSON line per
run into ``artifacts/agents/watchdog/*.jsonl`` (fresh file == status ``live``).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.agent_scoreboard import AgentMetricInputs, build_agent_scoreboard
from app.observability.source_reputation import (
    SourceReputationInputs,
    build_source_reputation_report,
    reliability_tier_to_accuracy,
)

logger = logging.getLogger(__name__)

DEFAULT_RELIABILITY_PATH = Path("monitor/source_reliability.json")
DEFAULT_AUDIT_PATH = Path("artifacts/paper_execution_audit.jsonl")
DEFAULT_RESOLVED_PATH = Path("artifacts/shadow_candidate_resolved.jsonl")
WATCHDOG_DROPBOX = Path("artifacts/agents/watchdog")

# Cohorts that must NEVER enter the scoreboard (issue #167 canary gate).
_EXCLUDED_AGENT_COHORTS: tuple[str, ...] = ("canary_probe", "loop_control", "unknown")


# ── Source reputation ────────────────────────────────────────────────────────


def collect_source_reputation_inputs(
    reliability_path: Path = DEFAULT_RELIABILITY_PATH,
    *,
    d227_by_source: list[dict[str, Any]] | None = None,
) -> list[SourceReputationInputs]:
    """Build per-source inputs from realised artifacts (deterministic, read-only).

    - Wilson lower bound (n>0) → ``historical_accuracy`` (the conservative
      realised precision estimate the core expects).
    - D-227 per-source precision (resolved>0) → ``realized_signal_quality``.
    - Sources with zero evidence stay in the report with all-None dimensions —
      the core marks them ``low_confidence`` (honest insufficiency, n=0 path).
    """
    rows: dict[str, dict[str, Any]] = {}
    try:
        payload = json.loads(reliability_path.read_text(encoding="utf-8"))
        scores = payload.get("scores")
        if isinstance(scores, dict):
            for name, row in scores.items():
                if isinstance(row, dict):
                    rows[str(name)] = dict(row)
    except (OSError, ValueError) as exc:
        logger.warning("[watchdog-collector] reliability read failed: %s", exc)

    d227_quality: dict[str, tuple[float, int]] = {}
    for row in d227_by_source or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("source"))
        resolved = int(row.get("resolved") or 0)
        precision = row.get("precision_pct")
        if resolved > 0 and isinstance(precision, (int, float)):
            d227_quality[name.lower()] = (float(precision) / 100.0, resolved)

    inputs: list[SourceReputationInputs] = []
    seen_lower: set[str] = set()
    for name, row in sorted(rows.items()):
        n = int(row.get("n") or 0)
        wilson = row.get("wilson_lower_95") if n > 0 else None
        quality = d227_quality.get(name.lower())
        inputs.append(
            SourceReputationInputs(
                source_id=name,
                historical_accuracy=reliability_tier_to_accuracy(
                    wilson if isinstance(wilson, (int, float)) else None
                ),
                realized_signal_quality=quality[0] if quality else None,
                sample_size=max(n, quality[1] if quality else 0),
            )
        )
        seen_lower.add(name.lower())

    # D-227 sources without a reliability row still carry realised evidence.
    for name_lower, (precision, resolved) in sorted(d227_quality.items()):
        if name_lower in seen_lower or name_lower in ("none", "unknown", ""):
            continue
        inputs.append(
            SourceReputationInputs(
                source_id=name_lower,
                realized_signal_quality=precision,
                sample_size=resolved,
            )
        )
    return inputs


# ── Agent scoreboard ─────────────────────────────────────────────────────────


def _profile_to_agent_inputs(profile: Any) -> AgentMetricInputs:
    """Map one GeneratorEdgeProfile onto the scoreboard input contract."""
    dd_bps = profile.max_drawdown_bps
    max_drawdown = -abs(float(dd_bps)) / 10_000.0 if isinstance(dd_bps, (int, float)) else None
    ic = {
        h: float(v) for h, v in (profile.ic_by_horizon or {}).items() if isinstance(v, (int, float))
    }
    return AgentMetricInputs(
        agent_id=str(profile.cohort_key),
        ev_after_costs_bps=profile.expected_value_after_costs_bps,
        sharpe=profile.sharpe,
        sortino=profile.sortino,
        brier=profile.brier_score,
        calibration_error=profile.calibration_error,
        ic_by_horizon=ic or None,
        max_drawdown=max_drawdown,
        overtrading_penalty=profile.overtrading_score,
        hit_rate=profile.win_rate,
        n_trades=int(profile.resolved_count or 0),
        n_signals=int(profile.trade_count or 0),
    )


def collect_agent_metric_inputs(
    *,
    audit_path: Path = DEFAULT_AUDIT_PATH,
    resolved_path: Path = DEFAULT_RESOLVED_PATH,
) -> tuple[list[AgentMetricInputs], dict[str, Any]]:
    """Derive per-agent realised metrics via the generator-edge instrument.

    One calculation source: trades + side-channels flow through
    ``build_generator_edge_report`` (the same instrument the edge CLI uses),
    then each cohort profile maps onto the scoreboard input contract. Probe
    cohorts are excluded (issue #167 canary gate). Returns the inputs plus an
    audit dict (collector counters + excluded cohorts).
    """
    from app.observability.edge_report import (
        load_audit_events,
        parse_closed_trades_with_exclusions,
    )
    from app.observability.generator_edge import build_generator_edge_report
    from app.observability.generator_edge_collector import collect_edge_inputs_from_resolved

    events = load_audit_events(str(audit_path))
    trades, _exclusions = parse_closed_trades_with_exclusions(events)
    side = collect_edge_inputs_from_resolved(resolved_path)
    report = build_generator_edge_report(
        trades,
        ic_aligned_by_cohort=side.ic_aligned_by_cohort or None,
        outcome_pairs_by_cohort=side.outcome_pairs_by_cohort or None,
    )

    inputs: list[AgentMetricInputs] = []
    excluded: list[str] = []
    for profile in report.profiles:
        key = str(profile.cohort_key)
        if any(key.startswith(prefix) for prefix in _EXCLUDED_AGENT_COHORTS):
            excluded.append(key)
            continue
        inputs.append(_profile_to_agent_inputs(profile))

    audit: dict[str, Any] = {
        "real_resolved": side.resolved_real,
        "side_channel": side.to_audit_dict(),
        "excluded_cohorts": excluded,
        "n_agent_inputs": len(inputs),
    }
    return inputs, audit


# ── Dropbox emission ─────────────────────────────────────────────────────────


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def emit_watchdog_reports(
    *,
    reliability_path: Path = DEFAULT_RELIABILITY_PATH,
    audit_path: Path = DEFAULT_AUDIT_PATH,
    resolved_path: Path = DEFAULT_RESOLVED_PATH,
    d227_by_source: list[dict[str, Any]] | None = None,
    dropbox_dir: Path = WATCHDOG_DROPBOX,
    dry_run: bool = False,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Build both reports and append them to the watchdog dropbox.

    **Activation gate (issue #167):** when the resolved shadow ledger contains
    ZERO real generator resolutions, nothing is emitted — a scoreboard without
    real data would be the canary-contamination failure mode this issue exists
    to prevent. The gate result is always returned for the audit trail.
    """
    agent_inputs, agent_audit = collect_agent_metric_inputs(
        audit_path=audit_path, resolved_path=resolved_path
    )
    if int(agent_audit.get("real_resolved") or 0) <= 0:
        logger.warning("[watchdog-collector] gate held: real_resolved=0 — nothing emitted")
        return {
            "emitted": False,
            "reason": "activation_gate_real_resolved_zero",
            "gate": agent_audit,
        }

    now = now_utc or datetime.now(UTC)
    source_report = build_source_reputation_report(
        collect_source_reputation_inputs(reliability_path, d227_by_source=d227_by_source),
        now_utc=now,
    )
    agent_report = build_agent_scoreboard(agent_inputs, now_utc=now)
    agent_report["collector_audit"] = agent_audit

    if not dry_run:
        _append_jsonl(dropbox_dir / "source_reputation.jsonl", source_report)
        _append_jsonl(dropbox_dir / "agent_scoreboard.jsonl", agent_report)

    return {
        "emitted": not dry_run,
        "dry_run": dry_run,
        "gate": {"real_resolved": agent_audit["real_resolved"]},
        "source_reputation": {
            "n_sources": source_report.get("n_sources"),
            "path": str(dropbox_dir / "source_reputation.jsonl"),
        },
        "agent_scoreboard": {
            "n_agents": agent_report.get("n_agents"),
            "excluded_cohorts": agent_audit["excluded_cohorts"],
            "path": str(dropbox_dir / "agent_scoreboard.jsonl"),
        },
    }


__all__ = [
    "DEFAULT_AUDIT_PATH",
    "DEFAULT_RELIABILITY_PATH",
    "DEFAULT_RESOLVED_PATH",
    "WATCHDOG_DROPBOX",
    "collect_agent_metric_inputs",
    "collect_source_reputation_inputs",
    "emit_watchdog_reports",
]
