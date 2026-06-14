"""WP-C (2026-06-15): gated TV auto-promotion.

Verifies eligible buys auto-promote, bearish sells gate on allow_short, the
decision log makes it idempotent, unsupported events are rejected (not retried),
and the flag is a hard no-op when OFF.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.signals.tradingview_auto_promote import auto_promote_pending


def _write_pending(path: Path, rows: list[dict]) -> None:
    lines = []
    for i, r in enumerate(rows):
        lines.append(
            json.dumps(
                {
                    "event_id": r["event_id"],
                    "received_at": "2026-06-15T00:00:00+00:00",
                    "ticker": r["ticker"],
                    "action": r["action"],
                    "price": r.get("price", 100.0),
                    "note": None,
                    "strategy": None,
                    "source_request_id": f"req-{i}",
                    "source_payload_hash": f"hash-{i}",
                    "external_event_id": r["event_id"],
                    "provenance": {
                        "source": "tradingview_webhook",
                        "version": "tv",
                        "signal_path_id": None,
                    },
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _paths(tmp: Path) -> dict[str, Path]:
    return {
        "pending_path": tmp / "pending.jsonl",
        "decisions_path": tmp / "decisions.jsonl",
        "promoted_path": tmp / "promoted.jsonl",
    }


def test_eligible_buy_is_auto_promoted(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    _write_pending(p["pending_path"], [{"event_id": "e1", "ticker": "BTCUSDT", "action": "buy"}])
    summary = auto_promote_pending(**p, now_iso="2026-06-15T00:00:00+00:00")

    assert summary["promoted"] == 1
    assert summary["rejected"] == 0
    promoted = [
        json.loads(line) for line in p["promoted_path"].read_text(encoding="utf-8").splitlines()
    ]
    assert promoted[0]["symbol"] == "BTCUSDT"
    assert promoted[0]["direction"] == "long"
    decisions = [
        json.loads(line) for line in p["decisions_path"].read_text(encoding="utf-8").splitlines()
    ]
    assert decisions[0]["decision"] == "promoted"
    assert decisions[0]["operator_reason"] == "auto_promote"


def test_bearish_sell_gated_on_allow_short(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    _write_pending(p["pending_path"], [{"event_id": "e1", "ticker": "ADAUSDT", "action": "sell"}])

    # allow_short False → rejected as bearish_disabled, nothing promoted.
    off = auto_promote_pending(**p, allow_short=False, now_iso="2026-06-15T00:00:00+00:00")
    assert off["promoted"] == 0
    assert off["rejected"] == 1
    decisions = [
        json.loads(line) for line in p["decisions_path"].read_text(encoding="utf-8").splitlines()
    ]
    assert decisions[0]["decision"] == "rejected"
    assert "bearish_directional_disabled" in decisions[0]["operator_reason"]


def test_bearish_sell_promoted_with_allow_short(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    _write_pending(p["pending_path"], [{"event_id": "e1", "ticker": "ADAUSDT", "action": "sell"}])
    summary = auto_promote_pending(**p, allow_short=True, now_iso="2026-06-15T00:00:00+00:00")
    assert summary["promoted"] == 1
    promoted = [
        json.loads(line) for line in p["promoted_path"].read_text(encoding="utf-8").splitlines()
    ]
    assert promoted[0]["direction"] == "short"


def test_idempotent_second_run_promotes_nothing(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    _write_pending(p["pending_path"], [{"event_id": "e1", "ticker": "BTCUSDT", "action": "buy"}])
    first = auto_promote_pending(**p, now_iso="2026-06-15T00:00:00+00:00")
    second = auto_promote_pending(**p, now_iso="2026-06-15T00:01:00+00:00")
    assert first["promoted"] == 1
    assert second["open_events"] == 0
    assert second["promoted"] == 0
    # Only one promoted candidate written across both runs.
    promoted = p["promoted_path"].read_text(encoding="utf-8").strip().splitlines()
    assert len(promoted) == 1


def test_unsupported_action_rejected_not_retried(tmp_path: Path) -> None:
    p = _paths(tmp_path)
    _write_pending(p["pending_path"], [{"event_id": "e1", "ticker": "BTCUSDT", "action": "close"}])
    summary = auto_promote_pending(**p, now_iso="2026-06-15T00:00:00+00:00")
    assert summary["promoted"] == 0
    assert summary["rejected"] == 1
    decisions = [
        json.loads(line) for line in p["decisions_path"].read_text(encoding="utf-8").splitlines()
    ]
    assert "unsupported_event" in decisions[0]["operator_reason"]


def test_run_from_settings_disabled_is_noop() -> None:
    from app.signals.tradingview_auto_promote import run_from_settings

    summary = run_from_settings()
    assert summary["enabled"] is False
