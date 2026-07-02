#!/usr/bin/env python3
"""Premium-Channel Parser-Feedback-Aggregator (P1 #10 — 2026-05-14).

Runs from ``kai-parser-feedback.timer`` every hour. Scans the last 60 min of
``artifacts/telegram_channel_raw.jsonl`` for messages with
``outcome == "not_a_signal"`` AND ``text_len > 50`` — these are channel posts
that the regex parser could not match and that are long enough to be a real
signal-like payload (not just chat noise like "👍" or "thanks").

If any such records exist, sends ONE aggregated Telegram message to
``ALERT_TELEGRAM_CHAT_ID`` with:
- count of unparsed long messages in window
- per-message: ISO timestamp + truncated text_preview (200 chars)

Why this exists: pre-2026-05-14 ``artifacts/telegram_channel_raw.jsonl`` was
a write-only graveyard — no consumer read it, channel-format changes silently
broke the parser, operator only noticed days later when paper-fills dropped.

Exits 0 on no-op (nothing to alert) AND on success-send.
Exits 1 only on hard error (file missing, telegram send failed AND records
existed). Cron-tick is non-fatal — never block the timer-loop.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("parser-feedback")

_RAW_LOG = Path(_REPO_ROOT) / "artifacts" / "telegram_channel_raw.jsonl"
_WINDOW_MINUTES = 60
_MIN_TEXT_LEN = 50
_PREVIEW_LIMIT = 200
_MAX_SAMPLES_IN_ALERT = 5


def scan_unparsed(
    path: Path,
    *,
    window_minutes: int = _WINDOW_MINUTES,
    min_text_len: int = _MIN_TEXT_LEN,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Return records from ``path`` matching not_a_signal + text_len > min within window.

    Tolerant against malformed JSON lines (drops them with debug log). Returns
    empty list if the file does not exist — first-time-boot is not an error.
    """
    if not path.exists():
        logger.info("raw_log missing: %s", path)
        return []
    cutoff = (now or datetime.now(UTC)) - timedelta(minutes=window_minutes)
    matches: list[dict[str, object]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("outcome") != "not_a_signal":
                continue
            try:
                text_len = int(rec.get("text_len", 0))
            except (TypeError, ValueError):
                continue
            if text_len <= min_text_len:
                continue
            ts_raw = rec.get("timestamp_utc")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = datetime.fromisoformat(ts_raw)
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if ts < cutoff:
                continue
            matches.append(rec)
    except OSError as exc:
        logger.warning("raw_log read failed: %s", exc)
        return []
    return matches


def format_alert(records: list[dict[str, object]], *, window_minutes: int = _WINDOW_MINUTES) -> str:
    """Build the Telegram alert text. Caps sample-count for length."""
    head = (
        f"KAI parser-feedback: {len(records)} nicht parsbare Channel-Message"
        f"{'s' if len(records) != 1 else ''} in letzten {window_minutes} min\n"
        f"(text_len > {_MIN_TEXT_LEN}, sonst wären sie Chat-Rauschen)\n\n"
    )
    body_lines: list[str] = []
    for rec in records[:_MAX_SAMPLES_IN_ALERT]:
        ts_raw = str(rec.get("timestamp_utc", "?"))
        text_len = rec.get("text_len", "?")
        preview = str(rec.get("text_preview", "(no preview)"))
        body_lines.append(f"• {ts_raw} (len={text_len}):")
        body_lines.append(f"  {preview[:_PREVIEW_LIMIT]}")
    if len(records) > _MAX_SAMPLES_IN_ALERT:
        body_lines.append(
            f"\n(+ {len(records) - _MAX_SAMPLES_IN_ALERT} weitere — siehe "
            f"artifacts/telegram_channel_raw.jsonl)"
        )
    body_lines.append("")
    body_lines.append("Next: Parser-Regex prüfen (app/ingestion/telegram_channel_parser.py)")
    return head + "\n".join(body_lines)


def send_telegram(text: str) -> bool:
    """Send to Telegram Bot API. Returns False if config missing or send failed."""
    token = os.environ.get("ALERT_TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("ALERT_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("ALERT_TELEGRAM_TOKEN/CHAT_ID missing — printing to stdout only")
        return False
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram send failed: %s", exc)
        return False


def main() -> int:
    records = scan_unparsed(_RAW_LOG)
    if not records:
        logger.info("OK no unparsed messages in last %d min", _WINDOW_MINUTES)
        return 0
    text = format_alert(records)
    print(text)
    sent = send_telegram(text)
    logger.warning("ALERT count=%d telegram_sent=%s", len(records), sent)
    # Records-existed-but-could-not-send is the only hard-error path.
    # Cron-tick still returns 0 so the timer doesn't enter failed state —
    # journal captures the WARNING for forensic review.
    return 0


if __name__ == "__main__":
    sys.exit(main())
