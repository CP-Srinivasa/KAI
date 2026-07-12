"""Tests fuer scripts/portfolio_epoch_reset.py (Weg B+ attestierter Reset).

Abnahme: Archiv+Hash, genau EIN Event, Positions-Invalidierung ohne erfundene
Exits, Refuse ohne Freeze/Approval, Idempotenz beim zweiten Lauf.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts.portfolio_epoch_reset import NEW_EPOCH_ID, main

from app.execution.audit_replay import replay_paper_audit


def _write_legacy_book(path: Path) -> None:
    rows = [
        {
            "event_type": "order_filled",
            "timestamp_utc": "2026-07-01T00:00:00+00:00",
            "fill_id": "f1",
            "order_id": "o1",
            "symbol": "ETH/USDT",
            "side": "buy",
            "position_side": "long",
            "quantity": 1.0,
            "fill_price": 2000.0,
            "fee_usd": 2.0,
            "filled_at": "2026-07-01T00:00:00+00:00",
            "portfolio_cash": 7998.0,
            "realized_pnl_usd": 0.0,
        }
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


@pytest.fixture()
def book(tmp_path: Path) -> dict[str, Path]:
    audit = tmp_path / "audit.jsonl"
    _write_legacy_book(audit)
    report = tmp_path / "forensic.md"
    report.write_text("Befund: kontaminierte Legacy-Historie.\n", encoding="utf-8")
    return {"audit": audit, "report": report, "archive": tmp_path / "archive"}


def _args(book: dict[str, Path], *extra: str) -> list[str]:
    return [
        "--audit-path",
        str(book["audit"]),
        "--archive-dir",
        str(book["archive"]),
        "--forensic-report",
        str(book["report"]),
        "--code-sha",
        "testsha",
        *extra,
    ]


def test_dry_run_writes_nothing(book: dict[str, Path]) -> None:
    before = book["audit"].read_text(encoding="utf-8")
    assert main(_args(book)) == 0
    assert book["audit"].read_text(encoding="utf-8") == before
    assert not book["archive"].exists()


def test_apply_requires_freeze(book: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXECUTION_PAPER_FROZEN", raising=False)
    assert main(_args(book, "--operator-approved", "--apply")) == 4


def test_apply_requires_operator_approval(
    book: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXECUTION_PAPER_FROZEN", "true")
    assert main(_args(book, "--apply")) == 4


def test_apply_archives_hashes_and_seeds_new_epoch(
    book: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EXECUTION_PAPER_FROZEN", "true")
    legacy_bytes = book["audit"].read_bytes()
    legacy_sha = hashlib.sha256(legacy_bytes).hexdigest()

    assert main(_args(book, "--operator-approved", "--apply")) == 0
    attestation = json.loads(capsys.readouterr().out)
    assert attestation["applied"] is True
    assert attestation["verified"] is True
    event = attestation["event"]
    assert event["new_epoch_id"] == NEW_EPOCH_ID
    assert event["old_book_performance_valid"] is False
    assert event["new_starting_cash_usd"] == 10_000.0
    assert event["legacy_book_sha256"] == legacy_sha
    assert event["operator_approved"] is True
    # Offene Legacy-Position dokumentiert, aber NICHT geschlossen (kein Exit-Fill).
    assert event["invalidated_positions"] == [
        {
            "symbol": "ETH/USDT",
            "quantity": 1.0,
            "avg_entry_price": 2000.0,
            "position_side": "long",
            "source": "",
            "opened_at": "2026-07-01T00:00:00+00:00",
            "legacy_position_status": "invalidated_at_epoch_boundary",
            "performance_effect_new_epoch": 0.0,
        }
    ]

    # Archiv: unveraenderte Legacy-Bytes + Hash-Sidecar.
    archives = list(book["archive"].glob("*.jsonl"))
    assert len(archives) == 1
    assert archives[0].read_bytes() == legacy_bytes
    sidecar = archives[0].with_suffix(".jsonl.sha256")
    assert sidecar.read_text(encoding="utf-8").split()[0] == legacy_sha

    # Buch: Legacy-Zeilen unveraendert, Event ist die letzte Zeile, kein Exit-Fill.
    lines = [
        json.loads(line)
        for line in book["audit"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [r["event_type"] for r in lines] == ["order_filled", "portfolio_epoch_reset"]

    # Replay: neue Epoche bei 10k, keine uebernommenen Positionen.
    result = replay_paper_audit(book["audit"])
    assert result.epoch_id == NEW_EPOCH_ID
    assert result.cash_usd == pytest.approx(10_000.0)
    assert result.positions == {}
    assert result.realized_pnl_usd == pytest.approx(0.0)


def test_second_apply_is_refused(book: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_PAPER_FROZEN", "true")
    assert main(_args(book, "--operator-approved", "--apply")) == 0
    assert main(_args(book, "--operator-approved", "--apply")) == 3
