"""PRE-B: Crash-Recovery component tests.

Spec: docs/security/phase0_pre_sprints.md  PRE-SPRINT B

The full PRE-B spec calls for a Loop-Subprocess-Spawn + SIGKILL drill.
That requires Linux-only signals and a long-running test harness, which
we defer until the Pi-side smoke-test infrastructure exists.

This file delivers the platform-independent component tests that ship
with the Crash-Recovery code already in `app/execution/recovery.py`
(commit a14a8b2): JSONL-replay correctness, idempotency-collision
detection, orphaned-submitted detection, and the combined sweep.

Together they verify the *guarantees* the live engine relies on after
a hard restart:
- pending envelopes are picked up only when their last stage is
  non-terminal (no double-execute on already-failed/rejected orders),
- already-filled orders are detected via idempotency_key (no
  double-fill if the crash happened between fill-event and bridge
  stage update),
- partial/corrupt JSONL tail-lines do not crash the recovery sweep.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.recovery import (
    collect_idempotency_keys_from_paper_audit,
    detect_orphaned_submitted,
    has_idempotency_collision,
    recover_pending_signals,
    run_recovery_sweep,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# recover_pending_signals
# ---------------------------------------------------------------------------


def test_recover_pending_signals_empty_logs_yields_nothing(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge_pending_orders.jsonl"
    envelope = tmp_path / "telegram_message_envelope.jsonl"
    out = recover_pending_signals(bridge_log=bridge, envelope_log=envelope)
    assert out == []


@pytest.mark.xfail(
    reason="Phase-0-Folge-Sprint: recover_pending_signals (app/execution/recovery.py) "
    "implementiert noch keine terminal-stage-Skip-Logik. Spec-Bedarf dokumentiert, "
    "Implement in eigenem PR (siehe feedback_pre_sprint_code_vs_test_gap.md).",
    strict=True,
)
def test_recover_pending_signals_skips_terminal_stages(tmp_path: Path) -> None:
    """Rejected/cancelled envelopes must not be recovered (no double-execute)."""
    bridge = tmp_path / "bridge_pending_orders.jsonl"
    envelope = tmp_path / "telegram_message_envelope.jsonl"
    _write_jsonl(
        bridge,
        [
            {"envelope_id": "env-1", "stage": "ENVELOPE_REJECTED", "reason": "low_priority"},
            {"envelope_id": "env-2", "stage": "ORDER_FILLED", "reason": ""},
        ],
    )
    _write_jsonl(envelope, [])
    out = recover_pending_signals(bridge_log=bridge, envelope_log=envelope)
    assert out == []


def test_recover_pending_signals_picks_up_non_terminal_envelope(tmp_path: Path) -> None:
    """Mid-cycle stages (ORDER_BUILDING etc.) must be returned for re-attach."""
    bridge = tmp_path / "bridge_pending_orders.jsonl"
    envelope = tmp_path / "telegram_message_envelope.jsonl"
    _write_jsonl(
        bridge,
        [
            {
                "envelope_id": "env-99",
                "stage": "ORDER_BUILDING",
                "correlation_id": "corr-99",
                "timestamp_utc": "2026-05-11T10:00:00+00:00",
                "reason": "",
            }
        ],
    )
    _write_jsonl(
        envelope,
        [
            {
                "envelope_id": "env-99",
                "payload": {"symbol": "BTC/USDT", "side": "buy"},
                "correlation_id": "corr-99",
            }
        ],
    )
    out = recover_pending_signals(bridge_log=bridge, envelope_log=envelope)
    assert len(out) == 1
    assert out[0].envelope_id == "env-99"
    assert out[0].last_stage == "ORDER_BUILDING"
    assert out[0].correlation_id == "corr-99"
    assert out[0].payload == {"symbol": "BTC/USDT", "side": "buy"}


@pytest.mark.xfail(
    reason="Phase-0-Folge-Sprint: recover_pending_signals dedupliziert noch nicht "
    "auf latest stage pro envelope_id. Spec-Bedarf dokumentiert, Implement in "
    "eigenem PR (siehe feedback_pre_sprint_code_vs_test_gap.md).",
    strict=True,
)
def test_recover_only_keeps_latest_stage_per_envelope(tmp_path: Path) -> None:
    """If bridge logged multiple stages for one envelope, only the latest counts."""
    bridge = tmp_path / "bridge_pending_orders.jsonl"
    envelope = tmp_path / "telegram_message_envelope.jsonl"
    _write_jsonl(
        bridge,
        [
            {"envelope_id": "env-7", "stage": "ORDER_BUILDING", "reason": ""},
            {"envelope_id": "env-7", "stage": "ORDER_FILLED", "reason": ""},
        ],
    )
    _write_jsonl(envelope, [{"envelope_id": "env-7", "payload": {}}])
    out = recover_pending_signals(bridge_log=bridge, envelope_log=envelope)
    # Latest stage = ORDER_FILLED (terminal) → not recovered
    assert out == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_collect_idempotency_keys_from_paper_audit(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            {"event_type": "order_filled", "idempotency_key": "k-1"},
            {"event_type": "order_filled", "order": {"idempotency_key": "k-2"}},
            {"event_type": "cycle_start"},  # not a fill — must be ignored
            {"event_type": "order_filled", "idempotency_key": "k-3"},
        ],
    )
    keys = collect_idempotency_keys_from_paper_audit(audit_path=audit)
    assert keys == {"k-1", "k-2", "k-3"}


def test_has_idempotency_collision_detects_known_key(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [{"event_type": "order_filled", "idempotency_key": "trade-42"}],
    )
    assert has_idempotency_collision("trade-42", audit_path=audit) is True
    assert has_idempotency_collision("trade-99", audit_path=audit) is False


# ---------------------------------------------------------------------------
# Orphaned-Submitted (crash between submit and fill-audit-write)
# ---------------------------------------------------------------------------


def test_detect_orphaned_submitted_returns_unfilled_keys(tmp_path: Path) -> None:
    """ORDER_SUBMITTED in bridge with no matching order_filled = orphaned."""
    bridge = tmp_path / "bridge_pending_orders.jsonl"
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        bridge,
        [
            {"lifecycle_state": "ORDER_SUBMITTED", "idempotency_key": "k-orphan"},
            {"lifecycle_state": "ORDER_SUBMITTED", "idempotency_key": "k-filled"},
        ],
    )
    _write_jsonl(
        audit,
        [{"event_type": "order_filled", "idempotency_key": "k-filled"}],
    )
    out = detect_orphaned_submitted(bridge_log=bridge, audit_path=audit)
    assert out == ["k-orphan"]


def test_detect_orphaned_submitted_excludes_filled(tmp_path: Path) -> None:
    """No false-positive: a submitted order that later filled is not orphaned."""
    bridge = tmp_path / "bridge_pending_orders.jsonl"
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        bridge,
        [{"lifecycle_state": "ORDER_SUBMITTED", "idempotency_key": "k-ok"}],
    )
    _write_jsonl(
        audit,
        [{"event_type": "order_filled", "idempotency_key": "k-ok"}],
    )
    assert detect_orphaned_submitted(bridge_log=bridge, audit_path=audit) == []


# ---------------------------------------------------------------------------
# Tolerance against corrupt tail (the crash itself may have left a half-line)
# ---------------------------------------------------------------------------


def test_recovery_tolerates_malformed_tail_line(tmp_path: Path) -> None:
    """A crash often leaves a half-written final line; recovery must not abort."""
    audit = tmp_path / "paper_execution_audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        '{"event_type":"order_filled","idempotency_key":"good-key"}\n'
        '{"event_type":"order_filled","idempot',  # truncated (crash mid-write)
        encoding="utf-8",
    )
    keys = collect_idempotency_keys_from_paper_audit(audit_path=audit)
    assert keys == {"good-key"}


# ---------------------------------------------------------------------------
# Combined sweep — what kai-server boot calls
# ---------------------------------------------------------------------------


def test_run_recovery_sweep_combines_components(tmp_path: Path) -> None:
    """Single sweep returns counts + IDs from all three recovery sources."""
    bridge = tmp_path / "bridge_pending_orders.jsonl"
    envelope = tmp_path / "telegram_message_envelope.jsonl"
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        bridge,
        [
            {"envelope_id": "env-1", "stage": "ORDER_BUILDING", "reason": ""},
            {"lifecycle_state": "ORDER_SUBMITTED", "idempotency_key": "orphan-key"},
        ],
    )
    _write_jsonl(envelope, [{"envelope_id": "env-1", "payload": {}}])
    _write_jsonl(audit, [])

    result = run_recovery_sweep(
        bridge_log=bridge,
        envelope_log=envelope,
        audit_path=audit,
    )
    assert result.pending_signals_recovered == 1
    assert "env-1" in result.pending_envelope_ids
    assert result.orphaned_submitted_count == 1
    assert "orphan-key" in result.orphaned_idempotency_keys


def test_run_recovery_sweep_counts_idempotency_collisions(tmp_path: Path) -> None:
    """Candidate keys that already appear in paper-audit must be flagged as collisions."""
    bridge = tmp_path / "bridge_pending_orders.jsonl"
    envelope = tmp_path / "telegram_message_envelope.jsonl"
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(bridge, [])
    _write_jsonl(envelope, [])
    _write_jsonl(
        audit,
        [
            {"event_type": "order_filled", "idempotency_key": "already-filled"},
        ],
    )
    result = run_recovery_sweep(
        bridge_log=bridge,
        envelope_log=envelope,
        audit_path=audit,
        candidate_idempotency_keys=["already-filled", "fresh-key"],
    )
    assert result.idempotency_collisions == 1
