"""Tests für Crash-Recovery (Aufgabenpaket 8 + Test-Cases #12 + #13).

Spec: docs/architecture/signal_to_execution_gap_analysis_20260510.md
Operator-Auftrag (2026-05-10) — Test-Cases:
    #12 Crash während WAITING_FOR_ENTRY → Recovery funktioniert.
    #13 Crash nach ORDER_SUBMITTED → keine doppelte Order.

Testkategorien
--------------
A) collect_idempotency_keys_from_paper_audit
B) has_idempotency_collision
C) recover_pending_signals (Test #12)
D) detect_orphaned_submitted (Test #13)
E) run_recovery_sweep (Top-level)
F) Tolerant-Read-Edge-Cases (malformed/empty/missing files)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.recovery import (
    RecoverableEnvelope,
    RecoveryResult,
    collect_idempotency_keys_from_paper_audit,
    detect_orphaned_submitted,
    has_idempotency_collision,
    recover_pending_signals,
    run_recovery_sweep,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_logs(tmp_path: Path) -> dict[str, Path]:
    """Three temp paths simulating the three audit JSONL streams."""
    return {
        "bridge": tmp_path / "bridge_pending_orders.jsonl",
        "envelope": tmp_path / "telegram_message_envelope.jsonl",
        "paper": tmp_path / "paper_execution_audit.jsonl",
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _envelope_record(
    *,
    envelope_id: str = "env-1",
    correlation_id: str = "SIG-TGCH-20260510120000-BTCUSDT",
    payload: dict | None = None,
) -> dict:
    return {
        "envelope_id": envelope_id,
        "correlation_id": correlation_id,
        "stage": "accepted",
        "status": "ok",
        "message_type": "signal",
        "source": "telegram_premium_channel_approved",
        "payload": payload
        or {
            "symbol": "BTCUSDT",
            "side": "buy",
            "direction": "long",
            "entry_type": "range",
            "entry_min": 65000.0,
            "entry_max": 65500.0,
            "stop_loss": 64200.0,
            "targets": [66000.0, 67000.0, 68500.0],
            "leverage": 10,
            "margin_pct": 5.0,
        },
    }


def _bridge_record(
    *,
    envelope_id: str = "env-1",
    correlation_id: str = "SIG-TGCH-20260510120000-BTCUSDT",
    stage: str = "pending",
    reason: str = "price_outside_tolerance",
    idempotency_key: str = "opbridge:env-1",
    timestamp_utc: str = "2026-05-10T12:00:00+00:00",
    lifecycle_state: str | None = None,
) -> dict:
    rec = {
        "envelope_id": envelope_id,
        "correlation_id": correlation_id,
        "stage": stage,
        "reason": reason,
        "idempotency_key": idempotency_key,
        "timestamp_utc": timestamp_utc,
    }
    if lifecycle_state:
        rec["lifecycle_state"] = lifecycle_state
    return rec


def _paper_filled_record(
    *,
    order_id: str = "ord_123",
    fill_id: str = "fill_123",
    idempotency_key: str = "opbridge:env-1",
    symbol: str = "BTCUSDT",
) -> dict:
    return {
        "schema_version": "v2",
        "event_type": "order_filled",
        "order_id": order_id,
        "fill_id": fill_id,
        "idempotency_key": idempotency_key,
        "symbol": symbol,
        "side": "buy",
        "quantity": 0.01,
        "fill_price": 65250.0,
        "fee_usd": 0.05,
        "filled_at": "2026-05-10T12:00:01+00:00",
        "slippage_pct": 0.05,
        "pnl_usd": 0.0,
        "position_side": "long",
    }


# ─────────────────────────────────────────────────────────────────────────────
# A) collect_idempotency_keys_from_paper_audit
# ─────────────────────────────────────────────────────────────────────────────


def test_collect_idempotency_keys_empty_file(tmp_logs: dict[str, Path]) -> None:
    keys = collect_idempotency_keys_from_paper_audit(audit_path=tmp_logs["paper"])
    assert keys == set()


def test_collect_idempotency_keys_single_fill(tmp_logs: dict[str, Path]) -> None:
    _write_jsonl(
        tmp_logs["paper"],
        [_paper_filled_record(idempotency_key="opbridge:env-A")],
    )
    keys = collect_idempotency_keys_from_paper_audit(audit_path=tmp_logs["paper"])
    assert keys == {"opbridge:env-A"}


def test_collect_idempotency_keys_skips_non_fill_events(
    tmp_logs: dict[str, Path],
) -> None:
    _write_jsonl(
        tmp_logs["paper"],
        [
            {"event_type": "order_created", "idempotency_key": "opbridge:env-X"},
            _paper_filled_record(idempotency_key="opbridge:env-A"),
            {"event_type": "position_closed", "idempotency_key": "opbridge:env-Y"},
        ],
    )
    keys = collect_idempotency_keys_from_paper_audit(audit_path=tmp_logs["paper"])
    # Nur order_filled-Events werden gesammelt
    assert keys == {"opbridge:env-A"}


def test_collect_idempotency_keys_handles_nested_order_payload(
    tmp_logs: dict[str, Path],
) -> None:
    """Manche Schema-Versionen schreiben order-info nested unter 'order'."""
    _write_jsonl(
        tmp_logs["paper"],
        [
            {
                "event_type": "order_filled",
                "order": {"idempotency_key": "opbridge:env-NESTED"},
                "filled_at": "2026-05-10T12:00:00+00:00",
            },
        ],
    )
    keys = collect_idempotency_keys_from_paper_audit(audit_path=tmp_logs["paper"])
    assert keys == {"opbridge:env-NESTED"}


def test_collect_idempotency_keys_multiple_fills(tmp_logs: dict[str, Path]) -> None:
    _write_jsonl(
        tmp_logs["paper"],
        [
            _paper_filled_record(order_id="ord_1", idempotency_key="opbridge:env-A"),
            _paper_filled_record(order_id="ord_2", idempotency_key="opbridge:env-B"),
            _paper_filled_record(order_id="ord_3", idempotency_key="opbridge:env-C"),
        ],
    )
    keys = collect_idempotency_keys_from_paper_audit(audit_path=tmp_logs["paper"])
    assert keys == {"opbridge:env-A", "opbridge:env-B", "opbridge:env-C"}


# ─────────────────────────────────────────────────────────────────────────────
# B) has_idempotency_collision
# ─────────────────────────────────────────────────────────────────────────────


def test_has_idempotency_collision_true_after_fill(
    tmp_logs: dict[str, Path],
) -> None:
    _write_jsonl(
        tmp_logs["paper"],
        [_paper_filled_record(idempotency_key="opbridge:env-DUP")],
    )
    assert has_idempotency_collision(
        "opbridge:env-DUP", audit_path=tmp_logs["paper"]
    )


def test_has_idempotency_collision_false_for_new_key(
    tmp_logs: dict[str, Path],
) -> None:
    _write_jsonl(
        tmp_logs["paper"],
        [_paper_filled_record(idempotency_key="opbridge:env-OLD")],
    )
    assert not has_idempotency_collision(
        "opbridge:env-NEW", audit_path=tmp_logs["paper"]
    )


def test_has_idempotency_collision_empty_key_is_false(
    tmp_logs: dict[str, Path],
) -> None:
    """Leerer key ist kein collision-trigger (defensive)."""
    _write_jsonl(tmp_logs["paper"], [_paper_filled_record()])
    assert not has_idempotency_collision("", audit_path=tmp_logs["paper"])


# ─────────────────────────────────────────────────────────────────────────────
# C) recover_pending_signals (Test-Case #12)
# ─────────────────────────────────────────────────────────────────────────────


def test_recover_pending_signals_picks_up_pending_envelope(
    tmp_logs: dict[str, Path],
) -> None:
    """Test-Case #12: WAITING_FOR_ENTRY-Recovery nach Crash.

    Setup: Envelope wurde accepted, Bridge hat es als 'pending' markiert
    (price_outside_tolerance), dann Crash. Recovery muss das Envelope
    zurückgeben damit EntryWatcher es weiter beobachtet.
    """
    _write_jsonl(tmp_logs["envelope"], [_envelope_record(envelope_id="env-WFE")])
    _write_jsonl(
        tmp_logs["bridge"],
        [_bridge_record(envelope_id="env-WFE", stage="pending")],
    )

    recovered = recover_pending_signals(
        bridge_log=tmp_logs["bridge"], envelope_log=tmp_logs["envelope"]
    )
    assert len(recovered) == 1
    assert isinstance(recovered[0], RecoverableEnvelope)
    assert recovered[0].envelope_id == "env-WFE"
    assert recovered[0].correlation_id == "SIG-TGCH-20260510120000-BTCUSDT"
    assert recovered[0].last_stage == "pending"
    assert recovered[0].payload["symbol"] == "BTCUSDT"
    assert recovered[0].payload["leverage"] == 10


def test_recover_pending_signals_skips_terminal_stages(
    tmp_logs: dict[str, Path],
) -> None:
    """filled/expired/rejected_* sind terminal — recovery ignoriert sie."""
    _write_jsonl(
        tmp_logs["envelope"],
        [
            _envelope_record(envelope_id="env-FILLED"),
            _envelope_record(
                envelope_id="env-EXPIRED",
                correlation_id="SIG-TGCH-20260510120100-ETHUSDT",
            ),
            _envelope_record(
                envelope_id="env-PENDING",
                correlation_id="SIG-TGCH-20260510120200-SOLUSDT",
            ),
        ],
    )
    _write_jsonl(
        tmp_logs["bridge"],
        [
            _bridge_record(envelope_id="env-FILLED", stage="filled"),
            _bridge_record(envelope_id="env-EXPIRED", stage="expired"),
            _bridge_record(envelope_id="env-PENDING", stage="pending"),
        ],
    )
    recovered = recover_pending_signals(
        bridge_log=tmp_logs["bridge"], envelope_log=tmp_logs["envelope"]
    )
    assert len(recovered) == 1
    assert recovered[0].envelope_id == "env-PENDING"


def test_recover_pending_signals_uses_latest_stage_per_envelope(
    tmp_logs: dict[str, Path],
) -> None:
    """Append-only log: erste pending, dann später filled — recovery
    nimmt das LATEST (filled → terminal → skip)."""
    _write_jsonl(tmp_logs["envelope"], [_envelope_record(envelope_id="env-X")])
    _write_jsonl(
        tmp_logs["bridge"],
        [
            _bridge_record(envelope_id="env-X", stage="pending"),
            _bridge_record(envelope_id="env-X", stage="filled"),  # later → wins
        ],
    )
    recovered = recover_pending_signals(
        bridge_log=tmp_logs["bridge"], envelope_log=tmp_logs["envelope"]
    )
    assert len(recovered) == 0  # filled is terminal, skipped


def test_recover_pending_signals_empty_logs(tmp_logs: dict[str, Path]) -> None:
    """Boot ohne vorhandene Audit-Logs: empty result, kein crash."""
    recovered = recover_pending_signals(
        bridge_log=tmp_logs["bridge"], envelope_log=tmp_logs["envelope"]
    )
    assert recovered == []


def test_recover_pending_signals_orphan_bridge_record_no_envelope(
    tmp_logs: dict[str, Path],
) -> None:
    """Bridge-Record ohne korrespondierendes Envelope: empty payload defensive."""
    _write_jsonl(tmp_logs["envelope"], [])  # empty
    _write_jsonl(
        tmp_logs["bridge"],
        [_bridge_record(envelope_id="env-ORPHAN", stage="pending")],
    )
    recovered = recover_pending_signals(
        bridge_log=tmp_logs["bridge"], envelope_log=tmp_logs["envelope"]
    )
    assert len(recovered) == 1
    assert recovered[0].payload == {}  # missing envelope → empty payload


# ─────────────────────────────────────────────────────────────────────────────
# D) detect_orphaned_submitted (Test-Case #13)
# ─────────────────────────────────────────────────────────────────────────────


def test_detect_orphaned_submitted_returns_keys_without_fill(
    tmp_logs: dict[str, Path],
) -> None:
    """Test-Case #13 Setup: ORDER_SUBMITTED in Bridge, kein order_filled
    im paper-audit → orphaned. Recovery muss diese Keys zurückgeben damit
    Caller idempotency-check macht."""
    _write_jsonl(
        tmp_logs["bridge"],
        [
            _bridge_record(
                envelope_id="env-SUBM",
                idempotency_key="opbridge:env-SUBM",
                lifecycle_state="ORDER_SUBMITTED",
            ),
        ],
    )
    # paper-audit ist leer (Crash zwischen submit + fill-write)
    orphaned = detect_orphaned_submitted(
        bridge_log=tmp_logs["bridge"], audit_path=tmp_logs["paper"]
    )
    assert orphaned == ["opbridge:env-SUBM"]


def test_detect_orphaned_submitted_excludes_keys_already_filled(
    tmp_logs: dict[str, Path],
) -> None:
    """Test-Case #13 Akzeptanz: idempotency-key der schon filled wurde,
    ist NICHT orphaned — Re-Submit wäre Doppel-Order."""
    _write_jsonl(
        tmp_logs["bridge"],
        [
            _bridge_record(
                envelope_id="env-A",
                idempotency_key="opbridge:env-A",
                lifecycle_state="ORDER_SUBMITTED",
            ),
            _bridge_record(
                envelope_id="env-B",
                idempotency_key="opbridge:env-B",
                lifecycle_state="ORDER_SUBMITTED",
            ),
        ],
    )
    # paper-audit zeigt: env-A war schon filled (Crash zwischen audit-write
    # und bridge-stage-update) — nur env-B ist echt orphaned
    _write_jsonl(
        tmp_logs["paper"], [_paper_filled_record(idempotency_key="opbridge:env-A")]
    )
    orphaned = detect_orphaned_submitted(
        bridge_log=tmp_logs["bridge"], audit_path=tmp_logs["paper"]
    )
    assert orphaned == ["opbridge:env-B"]


def test_detect_orphaned_submitted_no_submitted_records(
    tmp_logs: dict[str, Path],
) -> None:
    """Bridge ohne SUBMITTED-records → kein orphaned."""
    _write_jsonl(
        tmp_logs["bridge"], [_bridge_record(envelope_id="env-X", stage="pending")]
    )
    orphaned = detect_orphaned_submitted(
        bridge_log=tmp_logs["bridge"], audit_path=tmp_logs["paper"]
    )
    assert orphaned == []


def test_no_double_order_after_crash_at_audit_write_boundary(
    tmp_logs: dict[str, Path],
) -> None:
    """Test-Case #13 ENDE-zu-ENDE Akzeptanz:

    Sequenz:
    1. Bridge submitted Order → bridge-audit ORDER_SUBMITTED + idempotency-key
    2. Paper-Engine fillt Order → paper-audit order_filled + gleiche key
    3. Crash BEVOR Bridge stage='filled' schreibt
    4. Recovery: muss erkennen dass key bereits filled → KEINE Re-Submit

    Akzeptanz: has_idempotency_collision() returns True für dieses key.
    """
    submitted_key = "opbridge:env-CRASH"

    _write_jsonl(
        tmp_logs["bridge"],
        [
            _bridge_record(
                envelope_id="env-CRASH",
                idempotency_key=submitted_key,
                lifecycle_state="ORDER_SUBMITTED",
            ),
        ],
    )
    _write_jsonl(
        tmp_logs["paper"], [_paper_filled_record(idempotency_key=submitted_key)]
    )

    # detect_orphaned_submitted findet keinen orphan (key war filled)
    orphaned = detect_orphaned_submitted(
        bridge_log=tmp_logs["bridge"], audit_path=tmp_logs["paper"]
    )
    assert submitted_key not in orphaned

    # has_idempotency_collision detektiert die collision
    assert has_idempotency_collision(submitted_key, audit_path=tmp_logs["paper"])


# ─────────────────────────────────────────────────────────────────────────────
# E) run_recovery_sweep (Top-level)
# ─────────────────────────────────────────────────────────────────────────────


def test_recovery_sweep_combines_all_results(tmp_logs: dict[str, Path]) -> None:
    """End-to-end: 1 pending, 1 orphaned-submitted, 1 already-filled."""
    _write_jsonl(
        tmp_logs["envelope"],
        [
            _envelope_record(envelope_id="env-PEND"),
            _envelope_record(
                envelope_id="env-ORPH", correlation_id="SIG-X-20260510120000-X"
            ),
        ],
    )
    _write_jsonl(
        tmp_logs["bridge"],
        [
            _bridge_record(envelope_id="env-PEND", stage="pending"),
            _bridge_record(
                envelope_id="env-ORPH",
                idempotency_key="opbridge:env-ORPH",
                lifecycle_state="ORDER_SUBMITTED",
            ),
        ],
    )
    _write_jsonl(
        tmp_logs["paper"],
        [_paper_filled_record(idempotency_key="opbridge:env-CACHED")],
    )

    result = run_recovery_sweep(
        bridge_log=tmp_logs["bridge"],
        envelope_log=tmp_logs["envelope"],
        audit_path=tmp_logs["paper"],
        candidate_idempotency_keys=["opbridge:env-CACHED", "opbridge:env-NEW"],
    )

    assert isinstance(result, RecoveryResult)
    # Beide envelope (env-PEND stage=pending, env-ORPH stage=pending +
    # lifecycle_state=ORDER_SUBMITTED) sind nicht-terminal → beide recovered.
    # Der ORPH-Subset wird zusätzlich in orphaned_idempotency_keys aufgeführt.
    assert result.pending_signals_recovered == 2
    assert result.orphaned_submitted_count == 1
    assert "env-PEND" in result.pending_envelope_ids
    assert "env-ORPH" in result.pending_envelope_ids
    assert "opbridge:env-ORPH" in result.orphaned_idempotency_keys
    # opbridge:env-CACHED is in collisions, env-NEW is not
    assert result.idempotency_collisions == 1


def test_recovery_sweep_empty_repo_returns_zero_counts(
    tmp_logs: dict[str, Path],
) -> None:
    """Frischer Boot ohne Audit-Daten."""
    result = run_recovery_sweep(
        bridge_log=tmp_logs["bridge"],
        envelope_log=tmp_logs["envelope"],
        audit_path=tmp_logs["paper"],
    )
    assert result.pending_signals_recovered == 0
    assert result.orphaned_submitted_count == 0
    assert result.idempotency_collisions == 0


# ─────────────────────────────────────────────────────────────────────────────
# F) Tolerant-Read Edge-Cases
# ─────────────────────────────────────────────────────────────────────────────


def test_recovery_tolerates_malformed_jsonl_lines(
    tmp_logs: dict[str, Path],
) -> None:
    """Crash kann halben JSONL-Eintrag schreiben — recovery muss durchlaufen,
    valid records werden geliefert, malformed übersprungen."""
    # Erste Zeile valid, zweite Zeile broken (truncated), dritte Zeile valid
    content = (
        json.dumps(_paper_filled_record(idempotency_key="opbridge:env-A")) + "\n"
        + '{"event_type": "order_filled", "idempo'  # truncated, no newline
        + "\n"
        + json.dumps(_paper_filled_record(
            order_id="ord_2", idempotency_key="opbridge:env-B"
        ))
        + "\n"
    )
    tmp_logs["paper"].write_text(content, encoding="utf-8")
    keys = collect_idempotency_keys_from_paper_audit(audit_path=tmp_logs["paper"])
    # Both valid records collected, malformed skipped
    assert keys == {"opbridge:env-A", "opbridge:env-B"}


def test_recovery_handles_missing_files(tmp_path: Path) -> None:
    """Komplett fehlende Files (frischer Boot) ist kein crash."""
    nonexistent = tmp_path / "does_not_exist.jsonl"
    keys = collect_idempotency_keys_from_paper_audit(audit_path=nonexistent)
    assert keys == set()
    orphans = detect_orphaned_submitted(
        bridge_log=nonexistent, audit_path=nonexistent
    )
    assert orphans == []
    pending = recover_pending_signals(
        bridge_log=nonexistent, envelope_log=nonexistent
    )
    assert pending == []


def test_recovery_skips_records_without_envelope_id(
    tmp_logs: dict[str, Path],
) -> None:
    """Defensive: bridge records ohne envelope_id werden ignoriert."""
    _write_jsonl(
        tmp_logs["bridge"],
        [
            {"stage": "pending", "reason": "missing_id"},  # no envelope_id
            _bridge_record(envelope_id="env-OK", stage="pending"),
        ],
    )
    _write_jsonl(tmp_logs["envelope"], [_envelope_record(envelope_id="env-OK")])
    recovered = recover_pending_signals(
        bridge_log=tmp_logs["bridge"], envelope_log=tmp_logs["envelope"]
    )
    assert len(recovered) == 1
    assert recovered[0].envelope_id == "env-OK"
