"""Unit tests für den Replay-SSOT-Status (#314) — pure derive + fail-soft load."""

from __future__ import annotations

from pathlib import Path

from app.execution.audit_replay import AuditReplayResult
from app.observability.replay_status import (
    derive_replay_status,
    load_replay_status,
)


def _result(
    *,
    available: bool = True,
    positions: int = 0,
    fills: int = 0,
    skipped: int = 0,
    lc_errors: int = 0,
    error: str | None = None,
) -> AuditReplayResult:
    return AuditReplayResult(
        positions={f"P{i}": object() for i in range(positions)},  # type: ignore[misc]
        cash_usd=0.0,
        realized_pnl_usd=0.0,
        available=available,
        error=error,
        filled_idempotency_keys=frozenset(str(i) for i in range(fills)),
        skipped_events=tuple((i, "race") for i in range(skipped)),
        lifecycle_replay_errors=tuple("err" for _ in range(lc_errors)),
    )


def test_clean_replay_is_ok() -> None:
    s = derive_replay_status(_result(available=True, positions=3, fills=170))
    assert s.state == "ok"
    assert s.available is True
    assert s.positions == 3 and s.fills_replayed == 170
    assert s.skipped_events == 0 and s.lifecycle_errors == 0
    assert s.reason == ""


def test_skips_degrade() -> None:
    s = derive_replay_status(_result(available=True, positions=2, skipped=4))
    assert s.state == "degraded" and s.skipped_events == 4


def test_lifecycle_errors_degrade() -> None:
    s = derive_replay_status(_result(available=True, lc_errors=2))
    assert s.state == "degraded" and s.lifecycle_errors == 2


def test_unavailable_carries_reason() -> None:
    s = derive_replay_status(_result(available=False, error="audit kaputt"))
    assert s.state == "unavailable" and s.available is False
    assert s.positions == 0 and s.fills_replayed == 0
    assert s.reason == "audit kaputt"


def test_unavailable_without_error_has_default_reason() -> None:
    s = derive_replay_status(_result(available=False, error=None))
    assert s.state == "unavailable" and s.reason  # nicht leer (honest hint)


def test_load_missing_file_is_ok_empty(tmp_path: Path) -> None:
    # replay_paper_audit behandelt eine fehlende Datei als available=True/leer
    # (frisches System = konsistenter leerer Replay), nicht als Fehler.
    s = load_replay_status(tmp_path / "does_not_exist.jsonl")
    assert s.state == "ok" and s.available is True
    assert s.positions == 0 and s.fills_replayed == 0


def test_load_malformed_json_is_unavailable(tmp_path: Path) -> None:
    # Kaputtes JSON ist ein harter Fehler (KEIN resilienter Skip — der gilt nur
    # für semantisch schlechte Zeilen) → unavailable mit Grund.
    p = tmp_path / "audit.jsonl"
    p.write_text("not json\n{bad\n", encoding="utf-8")
    s = load_replay_status(p)
    assert s.state == "unavailable" and s.available is False
    assert s.reason  # nicht leer (trägt die decode-Fehlerursache)
