from __future__ import annotations

import json
from pathlib import Path

from app.execution.portfolio_read import compute_realized_by_asset


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _closed(
    symbol: str, pnl: float, source: str | None, reason: str | None = None
) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "v2",
        "event_type": "position_closed",
        "timestamp_utc": "2026-06-04T01:00:00+00:00",
        "symbol": symbol,
        "quantity": 1.0,
        "trade_pnl_usd": pnl,
        "fee_usd": 0.0,
    }
    if source is not None:
        row["signal_source"] = source
    if reason is not None:
        row["reason"] = reason
    return row


def test_premium_source_filter_excludes_autonomous_and_legacy(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            _closed("CYS/USDT", 7.0, "telegram_premium_channel_approved"),
            _closed("BTC/USDT", 99.0, "autonomous_generator"),
            _closed("ETH/USDT", 25.0, None),
        ],
    )

    premium = compute_realized_by_asset(audit, source_filter="telegram_premium")
    assert premium["source_filter"] == "telegram_premium"
    assert premium["totals"]["closed_trades"] == 1
    assert premium["totals"]["realized_pnl_usd"] == 7.0
    assert [row["symbol"] for row in premium["by_asset"]] == ["CYS/USDT"]


def test_unfiltered_view_preserves_full_paper_book(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            _closed("CYS/USDT", 7.0, "telegram_premium_channel_approved"),
            _closed("BTC/USDT", 99.0, "autonomous_generator"),
            _closed("ETH/USDT", 25.0, None),
        ],
    )

    full = compute_realized_by_asset(audit, source_filter="gesamt")
    assert full["source_filter"] == "gesamt"
    assert full["totals"]["closed_trades"] == 3
    assert full["totals"]["realized_pnl_usd"] == 131.0


def test_autonomous_filter_logic(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            _closed("CYS/USDT", 7.0, "telegram_premium_channel_approved"),
            _closed("BTC/USDT", 99.0, "autonomous_generator"),
            _closed("ETH/USDT", 25.0, None),
            _closed(
                "SOL/USDT",
                15.0,
                "autonomous_generator",
                reason="reconcile:touch_price_from_channel",
            ),
        ],
    )

    res = compute_realized_by_asset(audit, source_filter="autonomous")
    assert res["totals"]["closed_trades"] == 1
    assert res["totals"]["realized_pnl_usd"] == 99.0
    assert [row["symbol"] for row in res["by_asset"]] == ["BTC/USDT"]


def test_reconciled_filter_logic(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            _closed("CYS/USDT", 7.0, "telegram_premium_channel_approved"),
            _closed("BTC/USDT", 99.0, "autonomous_generator"),
            _closed("ETH/USDT", 25.0, None),
            _closed(
                "SOL/USDT",
                15.0,
                "telegram_premium_channel_approved",
                reason="reconcile:touch_price_from_channel",
            ),
        ],
    )

    res = compute_realized_by_asset(audit, source_filter="reconciled")
    assert res["totals"]["closed_trades"] == 1
    assert res["totals"]["realized_pnl_usd"] == 15.0
    assert [row["symbol"] for row in res["by_asset"]] == ["SOL/USDT"]


def test_legacy_unknown_filter_logic(tmp_path: Path) -> None:
    audit = tmp_path / "paper_execution_audit.jsonl"
    _write_jsonl(
        audit,
        [
            _closed("CYS/USDT", 7.0, "telegram_premium_channel_approved"),
            _closed("BTC/USDT", 99.0, "autonomous_generator"),
            _closed("ETH/USDT", 25.0, None),
            _closed("SOL/USDT", 15.0, None, reason="reconcile:touch_price_from_channel"),
        ],
    )

    res = compute_realized_by_asset(audit, source_filter="legacy_unknown")
    assert res["totals"]["closed_trades"] == 1
    assert res["totals"]["realized_pnl_usd"] == 25.0
    assert [row["symbol"] for row in res["by_asset"]] == ["ETH/USDT"]
