"""Premium-Trail-Summary für `/trail` ohne Argumente (Sprint S6, Lücke #8).

Verhalten: lesbare deutsche Zusammenfassung der letzten Premium-Signale aus
``bridge_pending_orders.jsonl`` (letzte Stage je envelope_id), Stage-Labels
statt Roh-Enums, Gesamt-Zählung, und der no-arg-`/trail` liefert die Premium-
Summary auch dann, wenn der TradingView-Ingress leer ist.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.messaging.signal_trail import (
    format_premium_trails_summary,
    format_signal_trail_message,
)


def _write_bridge(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path / "bridge_pending_orders.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _row(envelope_id: str, stage: str, ts: str, symbol: str = "BTC/USDT", **extra) -> dict:
    return {
        "envelope_id": envelope_id,
        "stage": stage,
        "timestamp_utc": ts,
        "symbol": symbol,
        "side": "buy",
        "source": "telegram_premium_channel_approved",
        **extra,
    }


def test_summary_uses_latest_stage_per_envelope(tmp_path: Path) -> None:
    _write_bridge(
        tmp_path,
        [
            _row("env-1", "pending", "2026-06-11T10:00:00+00:00"),
            _row("env-1", "filled", "2026-06-11T10:05:00+00:00"),
            _row("env-2", "rejected_entry_mode", "2026-06-11T11:00:00+00:00", symbol="ETH/USDT"),
        ],
    )
    msg = format_premium_trails_summary(tmp_path)
    assert "Premium-Trail" in msg
    # env-1 zeigt die LETZTE Stage (filled), nicht pending
    assert "✅ gefüllt" in msg
    assert "🚫 Kill-Switch" in msg
    # lesbar, keine Roh-Enums in den Zeilen für bekannte Stages
    assert "BTC/USDT" in msg and "ETH/USDT" in msg
    assert "Gesamt nach Stage:" in msg


def test_summary_ignores_non_premium_sources(tmp_path: Path) -> None:
    _write_bridge(
        tmp_path,
        [
            _row("env-tv", "filled", "2026-06-11T10:00:00+00:00") | {"source": "dashboard"},
        ],
    )
    msg = format_premium_trails_summary(tmp_path)
    assert "keine Premium-Signale" in msg


def test_summary_orders_latest_first_and_limits(tmp_path: Path) -> None:
    rows = [
        _row(f"env-{i}", "pending", f"2026-06-11T0{i}:00:00+00:00", symbol=f"A{i}/USDT")
        for i in range(10)
    ]
    _write_bridge(tmp_path, rows)
    msg = format_premium_trails_summary(tmp_path, limit=3)
    assert "A9/USDT" in msg  # neuestes zuerst
    assert "A0/USDT" not in msg  # über dem Limit
    # Gesamtzählung sieht trotzdem alle 10
    assert "wartet auf Entry-Range: 10" in msg


def test_noarg_trail_returns_premium_summary_without_tv_ingress(tmp_path: Path) -> None:
    _write_bridge(tmp_path, [_row("env-1", "filled", "2026-06-11T10:00:00+00:00")])
    msg = format_signal_trail_message("", tmp_path)
    assert "Premium-Trail" in msg
    assert "✅ gefüllt" in msg


def test_rejected_lines_carry_reason(tmp_path: Path) -> None:
    _write_bridge(
        tmp_path,
        [
            _row(
                "env-1",
                "rejected_entry_mode",
                "2026-06-11T10:00:00+00:00",
                reason="entry_mode_disabled",
            )
        ],
    )
    msg = format_premium_trails_summary(tmp_path)
    assert "— entry_mode_disabled" in msg
