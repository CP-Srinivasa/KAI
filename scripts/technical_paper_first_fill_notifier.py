"""First technical_paper paper-fill Telegram notifier (Pi ops, fire-once).

Watches ``artifacts/paper_execution_audit.jsonl`` for the FIRST
``paper_trade_label`` with ``feed_source="technical_paper"`` and pushes ONE
Telegram message to the operator, then disarms via a marker file so the timer
stays quiet afterwards. The technical_paper feeder was activated 2026-06-23 but
its fills queue behind the global max_open cap — this tells the operator the
moment a slot frees and the first technical_paper evidence actually lands.

READ-ONLY + fail-soft: it only reads the audit; it never writes or changes any
trading/risk/execution/cap/sizing parameter, and a send/read error never raises.

Run modes:
    python scripts/technical_paper_first_fill_notifier.py             # check + notify once
    python scripts/technical_paper_first_fill_notifier.py --dry-run   # compose + print, no send/marker
    python scripts/technical_paper_first_fill_notifier.py --self-test # pure-logic smoke, no net/fs
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path.home() / "ai_analyst_trading_bot"
AUDIT = REPO / "artifacts" / "paper_execution_audit.jsonl"
MARKER = REPO / "artifacts" / "technical_paper_first_fill_notified.flag"

_FEED = "technical_paper"


def find_technical_paper_fills(lines: list[str]) -> tuple[int, dict[str, Any] | None]:
    """Pure: scan audit JSONL lines for technical_paper paper-trade labels.

    Returns ``(count, earliest)`` where ``earliest`` is the technical_paper
    ``paper_trade_label`` with the smallest ``timestamp_utc`` (the first fill),
    or ``(0, None)`` if none. Corrupt lines are skipped.
    """
    fills: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:  # noqa: BLE001 — skip corrupt audit rows
            continue
        if d.get("event_type") == "paper_trade_label" and d.get("feed_source") == _FEED:
            fills.append(d)
    if not fills:
        return 0, None
    earliest = min(fills, key=lambda d: str(d.get("timestamp_utc", "")))
    return len(fills), earliest


def compose_message(count: int, first: dict[str, Any]) -> str:
    """Pure: human-readable Telegram body for the first technical_paper fill."""
    return (
        "✅ KAI — erster technical_paper-Fill ist da\n"
        f"Symbol: {first.get('symbol')} ({first.get('direction')})\n"
        f"Zeit (UTC): {first.get('timestamp_utc')}\n"
        f"technical_paper-Fills bisher: {count}\n"
        "Der Feeder konvertiert jetzt Screener-Kandidaten zu Paper-Fills — "
        "Evidenz läuft (Outcome↔signal_confidence demnächst auswertbar)."
    )


def _send_telegram(token: str, chat: str, text: str) -> None:
    import httpx

    httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text},
        timeout=15,
    )


def main() -> None:
    if "--self-test" in sys.argv:
        _self_test()
        return
    dry = "--dry-run" in sys.argv

    if MARKER.exists() and not dry:
        print("notify: already fired (marker present) — skipping")
        return

    try:
        lines = AUDIT.read_text(encoding="utf-8").splitlines() if AUDIT.exists() else []
    except OSError as exc:
        print("notify: audit read failed", exc)
        return

    count, first = find_technical_paper_fills(lines)
    if first is None:
        print("notify: no technical_paper fill yet")
        return

    msg = compose_message(count, first)
    if dry:
        print(msg)
        return

    token = (
        os.environ.get("ALERT_TELEGRAM_TOKEN")
        or os.environ.get("OPERATOR_TELEGRAM_BOT_TOKEN")
        or ""
    )
    chat = (
        os.environ.get("ALERT_TELEGRAM_CHAT_ID")
        or os.environ.get("OPERATOR_ADMIN_CHAT_IDS", "").split(",")[0].strip()
    )
    if not token or not chat:
        print("notify: no telegram creds")
        return

    try:
        _send_telegram(token, chat, msg)
    except Exception as exc:  # noqa: BLE001 — send failure must not crash the timer
        print("notify: send failed", exc)
        return

    try:
        MARKER.write_text(str(first.get("timestamp_utc", "")), encoding="utf-8")
    except OSError as exc:
        print("notify: sent but marker write failed (may re-notify next tick):", exc)
        return
    print("notify: telegram sent + marker written ->", msg.replace(chr(10), " | "))


def _self_test() -> None:
    assert find_technical_paper_fills([]) == (0, None)
    rows = [
        # wrong event_type — must be ignored even with the right feed_source
        json.dumps({"event_type": "order_filled", "feed_source": "technical_paper"}),
        # different feed_source — ignored
        json.dumps(
            {
                "event_type": "paper_trade_label",
                "feed_source": "autonomous_loop",
                "timestamp_utc": "2026-06-23T10:00:00Z",
                "symbol": "X/USDT",
            }
        ),
        # technical_paper later
        json.dumps(
            {
                "event_type": "paper_trade_label",
                "feed_source": "technical_paper",
                "timestamp_utc": "2026-06-23T12:00:00Z",
                "symbol": "SOL/USDT",
                "direction": "long",
            }
        ),
        # technical_paper earlier (the true first)
        json.dumps(
            {
                "event_type": "paper_trade_label",
                "feed_source": "technical_paper",
                "timestamp_utc": "2026-06-23T11:00:00Z",
                "symbol": "ETH/USDT",
                "direction": "long",
            }
        ),
        "{corrupt json",  # skipped
    ]
    count, first = find_technical_paper_fills(rows)
    assert count == 2, count
    assert first is not None and first["symbol"] == "ETH/USDT", first
    msg = compose_message(count, first)
    assert "ETH/USDT" in msg
    assert "technical_paper-Fills bisher: 2" in msg
    print("SELF-TEST OK")


if __name__ == "__main__":
    main()
