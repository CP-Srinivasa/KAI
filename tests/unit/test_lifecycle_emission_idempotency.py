"""Idempotency der lifecycle_transition-Emission (#314).

Befund 2026-06-18 (Replay-SSOT-KPI): eine SKYAI/USDT-Envelope hatte ihre
Order-Open-Sequenz doppelt im Audit → 3 „discontinuous"-Replay-Fehler. Diese
Tests sichern den Emit-Guard: eine Open-Phase-Stufe wird nie erneut emittiert,
sobald die correlation_id sie bereits erreicht/überschritten hat — wiederholte
post-open PARTIAL_TP_HIT-Tiers bleiben dagegen erlaubt.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.execution.models import OrderLifecycleState as S
from app.execution.paper_engine import (
    PaperExecutionEngine,
    _is_redundant_open_transition,
)


def test_predicate_blocks_backward_and_repeat_open_steps() -> None:
    # Schon POSITION_OPEN → jede Open-Phase-Stufe (inkl. POSITION_OPEN selbst) ist redundant.
    assert _is_redundant_open_transition(S.POSITION_OPEN, S.ORDER_BUILDING) is True
    assert _is_redundant_open_transition(S.POSITION_OPEN, S.ORDER_SUBMITTED) is True
    assert _is_redundant_open_transition(S.POSITION_OPEN, S.ORDER_ACCEPTED) is True
    assert _is_redundant_open_transition(S.POSITION_OPEN, S.POSITION_OPEN) is True
    # Bei ORDER_ACCEPTED ist ein erneutes ORDER_SUBMITTED redundant, POSITION_OPEN aber Fortschritt.
    assert _is_redundant_open_transition(S.ORDER_ACCEPTED, S.ORDER_SUBMITTED) is True
    assert _is_redundant_open_transition(S.ORDER_ACCEPTED, S.POSITION_OPEN) is False


def test_predicate_allows_forward_progress() -> None:
    assert _is_redundant_open_transition(S.ORDER_BUILDING, S.ORDER_SUBMITTED) is False
    assert _is_redundant_open_transition(S.ORDER_SUBMITTED, S.ORDER_ACCEPTED) is False


def test_predicate_never_guards_post_open_repeats() -> None:
    # PARTIAL_TP_HIT hat keinen Open-Phase-Rang → Wiederholung nie geblockt.
    assert _is_redundant_open_transition(S.POSITION_OPEN, S.PARTIAL_TP_HIT) is False
    assert _is_redundant_open_transition(S.PARTIAL_TP_HIT, S.PARTIAL_TP_HIT) is False
    assert _is_redundant_open_transition(S.PARTIAL_TP_HIT, S.TP_HIT) is False
    # Aber zurück in die Open-Phase nach einem post-open State ist redundant.
    assert _is_redundant_open_transition(S.PARTIAL_TP_HIT, S.ORDER_SUBMITTED) is True


def _count_lifecycle_rows(audit_path: Path, correlation_id: str) -> int:
    n = 0
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if (
            row.get("event_type") == "lifecycle_transition"
            and row.get("correlation_id") == correlation_id
        ):
            n += 1
    return n


def test_emit_is_idempotent_when_already_open(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    engine = PaperExecutionEngine(audit_log_path=str(audit))
    cid = "ENV-TEST-0001"

    # Saubere Vorwärts-Sequenz emittiert je eine Zeile.
    assert engine._emit_lifecycle_transition(
        correlation_id=cid,
        default_from_state=S.ORDER_BUILDING,
        to_state=S.ORDER_SUBMITTED,
        reason="paper_order_created",
    )
    assert engine._emit_lifecycle_transition(
        correlation_id=cid,
        default_from_state=S.ORDER_SUBMITTED,
        to_state=S.ORDER_ACCEPTED,
        reason="paper_order_accepted",
    )
    assert engine._emit_lifecycle_transition(
        correlation_id=cid,
        default_from_state=S.ORDER_ACCEPTED,
        to_state=S.POSITION_OPEN,
        reason="paper_position_opened",
    )
    assert _count_lifecycle_rows(audit, cid) == 3

    # Re-Emission derselben Open-Sequenz (z.B. Reprocess): alle drei No-ops.
    assert (
        engine._emit_lifecycle_transition(
            correlation_id=cid,
            default_from_state=S.ORDER_BUILDING,
            to_state=S.ORDER_SUBMITTED,
            reason="paper_order_created",
        )
        is False
    )
    assert (
        engine._emit_lifecycle_transition(
            correlation_id=cid,
            default_from_state=S.ORDER_ACCEPTED,
            to_state=S.POSITION_OPEN,
            reason="paper_position_opened",
        )
        is False
    )
    # Keine zusätzlichen Zeilen → keine Doppel-Emission mehr.
    assert _count_lifecycle_rows(audit, cid) == 3


def test_emit_still_records_post_open_tiers(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    engine = PaperExecutionEngine(audit_log_path=str(audit))
    cid = "ENV-TEST-0002"
    engine._lifecycle_state_by_correlation_id[cid] = S.POSITION_OPEN

    assert engine._emit_lifecycle_transition(
        correlation_id=cid,
        default_from_state=S.POSITION_OPEN,
        to_state=S.PARTIAL_TP_HIT,
        reason="paper_tier_closed",
    )
    assert engine._emit_lifecycle_transition(
        correlation_id=cid,
        default_from_state=S.PARTIAL_TP_HIT,
        to_state=S.PARTIAL_TP_HIT,
        reason="paper_tier_closed",
    )
    assert _count_lifecycle_rows(audit, cid) == 2
