"""Approval-Mode for auto-ingested signals (Vorschlag B, B-6).

Flow:
    worker parses message
      → emit_parsed_signal (shadow envelope, source="telegram_premium_channel")
      → send approval request (this module → Telegram bot)

    operator clicks [✅ Fill]
      → _handle_callback_query dispatches to handle_signal_approval
      → re-emit with source="telegram_premium_channel_approved"
      → bridge allowlist matches → paper-fill

    operator clicks [❌ Ignore] OR TTL expires
      → audit-record only, no re-emit

Design invariants:
- Pure helpers (format_message, build_keyboard, parse_callback, is_ttl_expired,
  build_approval_record) are io-free → unit-tested without any Telethon/bot.
- Re-emit path mutates ``payload["source"]`` so the canonical idempotency_key
  genuinely differs — Bridge dedup sees it as a NEW envelope, not the shadow
  record already marked as skipped_source.
- Double-click dedup: handle_signal_approval refuses a second re-emit for the
  same ``origin_envelope_id`` regardless of source (audit-record still written).
- Fail-safe TTL: expired click → refused, no fill. CLAUDE.md safety rule.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APPROVED_SUFFIX = "_approved"

# Callback-data prefixes — kept short (Telegram limit 64 bytes total).
# Format: "sig:{action}:{envelope_id}" → max ~40 chars with ENV-ids.
CB_PREFIX = "sig"
CB_FILL = "f"
CB_IGNORE = "i"


# ── Pure helpers (io-free, test-friendly) ───────────────────────────────────


def _fmt_price(value: float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, int) or value == int(value):
        return f"{int(value)}"
    return f"{value:g}"


def format_approval_message(record: dict[str, Any], *, ttl_minutes: int) -> str:
    """Human-readable approval prompt. Callable independent of bot transport.

    ``record`` is the envelope JSONL entry (output of build_envelope_record).
    Returned string is Markdown-safe for Telegram — no escape helpers required
    since we only emit digits / ticker symbols / short labels.
    """
    p: dict[str, Any] = dict(record.get("payload", {}))
    source = str(record.get("source", "?"))
    symbol = p.get("display_symbol") or p.get("symbol") or "?"
    direction = str(p.get("direction", "?")).upper()
    leverage = p.get("leverage")
    entry = p.get("entry_value")
    entry_min = p.get("entry_min")
    entry_max = p.get("entry_max")
    sl = p.get("stop_loss")
    targets = list(p.get("targets", []) or [])

    entry_line = _fmt_price(entry)
    if entry_min is not None and entry_max is not None:
        entry_line = f"{_fmt_price(entry_min)}–{_fmt_price(entry_max)}"

    sl_pct = ""
    if isinstance(entry, (int, float)) and isinstance(sl, (int, float)) and entry:
        pct = (sl - entry) / entry * 100.0
        sl_pct = f" ({pct:+.2f}%)"

    lev_str = f"{int(leverage)}x" if isinstance(leverage, (int, float)) and leverage else "—"
    t_str = " / ".join(_fmt_price(t) for t in targets) if targets else "—"

    lines = [
        f"📡 *Signal* — `{source}`",
        f"*{symbol}* · *{direction}* · {lev_str}",
        f"Entry: `{entry_line}`",
        f"SL: `{_fmt_price(sl)}`{sl_pct}",
        f"Targets: `{t_str}`",
        "",
        f"_TTL: {ttl_minutes} Min_ · _env: `{record.get('envelope_id', '?')}`_",
    ]
    return "\n".join(lines)


def build_inline_keyboard(envelope_id: str) -> list[list[dict[str, str]]]:
    """Telegram inline_keyboard for [Fill]/[Ignore] per envelope_id."""
    return [
        [
            {"text": "✅ Fill", "callback_data": f"{CB_PREFIX}:{CB_FILL}:{envelope_id}"},
            {"text": "❌ Ignore", "callback_data": f"{CB_PREFIX}:{CB_IGNORE}:{envelope_id}"},
        ]
    ]


@dataclass(frozen=True)
class CallbackAction:
    action: str  # "fill" | "ignore"
    envelope_id: str


def parse_callback_data(data: str) -> CallbackAction | None:
    """Parse a Telegram callback_data string. Returns None if not for us."""
    if not isinstance(data, str):
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != CB_PREFIX:
        return None
    code, env_id = parts[1], parts[2]
    if code == CB_FILL:
        return CallbackAction(action="fill", envelope_id=env_id)
    if code == CB_IGNORE:
        return CallbackAction(action="ignore", envelope_id=env_id)
    return None


def is_ttl_expired(
    record_ts_iso: str,
    *,
    ttl_minutes: int,
    now: datetime | None = None,
) -> bool:
    """True iff (now - record_ts) exceeds ttl_minutes. Tz-safe."""
    try:
        ts = datetime.fromisoformat(record_ts_iso)
    except ValueError:
        logger.warning("[approval] unparseable record_ts=%r → treating as expired", record_ts_iso)
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return (current - ts) > timedelta(minutes=ttl_minutes)


# ── JSONL helpers ───────────────────────────────────────────────────────────


def _iter_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("[approval] log read failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_envelope_by_id(path: Path, envelope_id: str) -> dict[str, Any] | None:
    """Return the most recent envelope record matching envelope_id, or None."""
    if not envelope_id:
        return None
    for rec in reversed(_iter_records(path)):
        if rec.get("envelope_id") == envelope_id:
            return rec
    return None


def is_already_approved(path: Path, origin_envelope_id: str) -> bool:
    """True if an approved re-emit already exists for this origin envelope."""
    if not origin_envelope_id:
        return False
    for rec in _iter_records(path):
        if rec.get("origin_envelope_id") == origin_envelope_id:
            return True
    return False


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Approval re-emit ────────────────────────────────────────────────────────


def build_approval_record(
    orig_record: dict[str, Any],
    *,
    approved_by: str | int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a new envelope-JSONL record derived from the original.

    Key differences vs the original:
    - ``source`` gets the ``_approved`` suffix → bridge allowlist match
    - ``payload.source`` also updated → canonical idempotency_key differs
    - new ``envelope_id`` (fresh timestamp-based id)
    - ``origin_envelope_id`` references the original (audit-trail)
    - ``approved_by`` records the operator user id

    Pure function — no disk io, no bot, no settings.
    """
    from app.messaging.message_models import (
        _canonical_idempotency_key,
        _generate_envelope_id,
    )

    ts = (now or datetime.now(UTC)).isoformat()
    orig_source = str(orig_record.get("source", "")).strip() or "unknown"
    new_source = f"{orig_source}{APPROVED_SUFFIX}"

    new_payload = dict(orig_record.get("payload", {}) or {})
    new_payload["source"] = new_source
    new_payload["timestamp_utc"] = ts

    new_env_id = _generate_envelope_id(ts)
    new_idem = _canonical_idempotency_key(new_payload)

    rec: dict[str, Any] = {
        "timestamp_utc": ts,
        "event": "telegram_channel_approval",
        "message_type": "signal",
        "stage": "accepted",
        "status": "ok",
        "source": new_source,
        "execution_enabled": True,
        "write_back_allowed": False,
        "envelope_id": new_env_id,
        "idempotency_key": new_idem,
        "origin_envelope_id": orig_record.get("envelope_id"),
        "origin_source": orig_source,
        "payload": new_payload,
    }
    if "chat_id" in orig_record and orig_record["chat_id"] is not None:
        rec["chat_id"] = orig_record["chat_id"]
    if approved_by is not None:
        rec["approved_by"] = approved_by
    return rec


@dataclass(frozen=True)
class ApprovalOutcome:
    status: str  # "filled" | "ignored" | "expired" | "duplicate" | "not_found"
    reason: str
    new_envelope_id: str | None
    origin_envelope_id: str | None


def handle_signal_approval(
    action: str,
    envelope_id: str,
    *,
    envelope_log: Path,
    ttl_minutes: int,
    approved_by: str | int | None = None,
    now: datetime | None = None,
) -> ApprovalOutcome:
    """Dispatch a Fill/Ignore click. Writes JSONL records, returns outcome.

    - action="fill": loads origin, checks TTL + dedup, re-emits approved record.
    - action="ignore": writes an ignore audit-record, no re-emit.
    - TTL-expired Fill is refused (fail-safe).
    """
    orig = load_envelope_by_id(envelope_log, envelope_id)
    if orig is None:
        return ApprovalOutcome(
            status="not_found",
            reason="envelope_not_in_log",
            new_envelope_id=None,
            origin_envelope_id=envelope_id,
        )

    if action == "ignore":
        _append_jsonl(
            envelope_log,
            {
                "timestamp_utc": (now or datetime.now(UTC)).isoformat(),
                "event": "telegram_channel_approval",
                "message_type": "signal",
                "stage": "ignored",
                "status": "ok",
                "source": orig.get("source"),
                "origin_envelope_id": envelope_id,
                "ignored_by": approved_by,
            },
        )
        return ApprovalOutcome(
            status="ignored",
            reason="operator_ignored",
            new_envelope_id=None,
            origin_envelope_id=envelope_id,
        )

    if action != "fill":
        return ApprovalOutcome(
            status="not_found",
            reason=f"unknown_action={action}",
            new_envelope_id=None,
            origin_envelope_id=envelope_id,
        )

    # TTL check based on original's timestamp_utc.
    ts_iso = str(orig.get("timestamp_utc", ""))
    if is_ttl_expired(ts_iso, ttl_minutes=ttl_minutes, now=now):
        _append_jsonl(
            envelope_log,
            {
                "timestamp_utc": (now or datetime.now(UTC)).isoformat(),
                "event": "telegram_channel_approval",
                "message_type": "signal",
                "stage": "expired",
                "status": "refused",
                "source": orig.get("source"),
                "origin_envelope_id": envelope_id,
                "ttl_minutes": ttl_minutes,
            },
        )
        return ApprovalOutcome(
            status="expired",
            reason=f"exceeded_ttl={ttl_minutes}min",
            new_envelope_id=None,
            origin_envelope_id=envelope_id,
        )

    # Double-click / re-approve dedup.
    if is_already_approved(envelope_log, envelope_id):
        return ApprovalOutcome(
            status="duplicate",
            reason="already_approved",
            new_envelope_id=None,
            origin_envelope_id=envelope_id,
        )

    rec = build_approval_record(orig, approved_by=approved_by, now=now)
    _append_jsonl(envelope_log, rec)
    new_id = rec.get("envelope_id")
    assert isinstance(new_id, str)
    return ApprovalOutcome(
        status="filled",
        reason="approved_re_emitted",
        new_envelope_id=new_id,
        origin_envelope_id=envelope_id,
    )


# ── Telegram bot transport (thin httpx wrapper, no bot-module import) ───────


async def send_approval_request(
    record: dict[str, Any],
    *,
    bot_token: str,
    chat_id: int,
    ttl_minutes: int,
) -> dict[str, Any] | None:
    """POST sendMessage with an inline keyboard. Returns Telegram API response
    JSON (``result`` payload) or None on network/API failure.

    Kept intentionally minimal: no retries, no typing-indicator. The caller
    is a Telethon handler that shouldn't block the event loop on failures.
    Any exception → logged + None, so a temporary Telegram outage never
    kills the channel listener.
    """
    import httpx

    env_id = record.get("envelope_id")
    if not bot_token or not chat_id or not env_id:
        logger.warning(
            "[approval] send refused — missing token/chat_id/envelope_id "
            "(token_set=%s chat_id=%s env_id=%s)",
            bool(bot_token),
            chat_id,
            env_id,
        )
        return None

    text = format_approval_message(record, ttl_minutes=ttl_minutes)
    keyboard = build_inline_keyboard(str(env_id))
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": keyboard},
        "disable_web_page_preview": True,
    }
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            logger.warning(
                "[approval] telegram sendMessage failed status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return None
        body = resp.json()
        if not body.get("ok"):
            logger.warning("[approval] telegram sendMessage not-ok body=%s", body)
            return None
        return body.get("result")
    except Exception as exc:  # noqa: BLE001 — log + swallow, caller shouldn't die
        logger.warning("[approval] telegram sendMessage exception: %s", exc)
        return None


__all__ = [
    "APPROVED_SUFFIX",
    "ApprovalOutcome",
    "CallbackAction",
    "build_approval_record",
    "build_inline_keyboard",
    "format_approval_message",
    "handle_signal_approval",
    "is_already_approved",
    "is_ttl_expired",
    "load_envelope_by_id",
    "parse_callback_data",
    "send_approval_request",
]
