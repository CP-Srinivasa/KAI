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

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APPROVED_SUFFIX = "_approved"

# Callback-data prefixes — kept short (Telegram limit 64 bytes total).
# Legacy format: "sig:{action}:{envelope_id}" → ~35 chars.
# Signed format (P1 #9, 2026-05-14):
#   "sig:{action}:{envelope_id}:{ttl_unix}:{hmac8}" → ~52 chars
# - ttl_unix: epoch seconds at which the token expires (replay window)
# - hmac8: first 8 hex chars of HMAC-SHA256 over
#          "{action}:{envelope_id}:{ttl_unix}" keyed by
#          OPERATOR_APPROVAL_HMAC_SECRET.
# Strict-mode (secret set in env) accepts only the 5-part form. Legacy
# 3-part tokens are rejected as ``None`` so an attacker cannot downgrade.
# When the secret is empty (default), both forms still pass through —
# that's the migration runway, NOT the security target state.
CB_PREFIX = "sig"
CB_FILL = "f"
CB_IGNORE = "i"
_CB_HMAC_PREFIX_LEN = 8

# Default location of the TV-4 quality-bar JSON report. Used to enrich approval
# cards with per-source precision badges (Wilson CI). When file is missing,
# stale, or sample insufficient → no badge (silent fallback, no false signals).
DEFAULT_SOURCE_QUALITY_REPORT = Path("artifacts/tv4_quality_bar_report.json")
SOURCE_QUALITY_MAX_AGE_DAYS = 7


# ── Pure helpers (io-free, test-friendly) ───────────────────────────────────


def _fmt_price(value: float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, int) or value == int(value):
        return f"{int(value)}"
    return f"{value:g}"


def _compute_risk_reward(
    entry: float | int | None,
    stop_loss: float | int | None,
    targets: list[Any],
    direction: str,
) -> float | None:
    """R/R against the *first* target (conservative take-profit).

    None when any input is missing/invalid or geometry is inverted (e.g. SL
    above entry on a long). The card silently drops the badge in those cases
    rather than showing a misleading number.
    """
    if not isinstance(entry, (int, float)) or not isinstance(stop_loss, (int, float)):
        return None
    if not targets:
        return None
    first_target = targets[0]
    if not isinstance(first_target, (int, float)):
        return None
    direction_lower = direction.strip().lower()
    if direction_lower in {"long", "buy"}:
        risk = entry - stop_loss
        reward = first_target - entry
    elif direction_lower in {"short", "sell"}:
        risk = stop_loss - entry
        reward = entry - first_target
    else:
        return None
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _compute_position_risk_pct(
    entry: float | int | None,
    stop_loss: float | int | None,
    leverage: float | int | None,
) -> float | None:
    """Margin-loss percentage when SL is hit. Lev-aware.

    Worst-case position drawdown at SL = sl_distance% × leverage. None if any
    input is missing or geometrically invalid.
    """
    if not isinstance(entry, (int, float)) or entry <= 0:
        return None
    if not isinstance(stop_loss, (int, float)):
        return None
    lev = leverage if isinstance(leverage, (int, float)) and leverage > 0 else 1.0
    sl_distance_pct = abs(stop_loss - entry) / entry
    return sl_distance_pct * lev * 100.0


def _format_ttl_endtime(record_ts_iso: str, ttl_minutes: int) -> str | None:
    """Absolute TTL deadline as 'HH:MM UTC'. None if record_ts is unparseable."""
    try:
        ts = datetime.fromisoformat(record_ts_iso)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    deadline = (ts + timedelta(minutes=ttl_minutes)).astimezone(UTC)
    return deadline.strftime("%H:%M UTC")


def _load_source_quality(
    source: str,
    quality_report_path: Path,
    *,
    max_age_days: int = SOURCE_QUALITY_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """TV-4 quality-bar lookup for a single source. Stale-safe, fail-silent.

    Returns the by_source[i] dict (resolved/hits/hit_rate_pct/ci_low_pct/...)
    only when: file exists, JSON parses, report not older than max_age_days,
    source is present, and Wilson sample is sufficient. Otherwise None — the
    card omits the badge rather than showing a low-confidence number.
    """
    if not quality_report_path.exists():
        return None
    try:
        data = json.loads(quality_report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    generated_at_iso = data.get("generated_at")
    if isinstance(generated_at_iso, str):
        try:
            generated_at = datetime.fromisoformat(generated_at_iso)
        except ValueError:
            return None
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if (current - generated_at) > timedelta(days=max_age_days):
            return None
    by_source = data.get("by_source")
    if not isinstance(by_source, list):
        return None
    src_lower = source.strip().lower()
    for entry in by_source:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("source", "")).strip().lower() == src_lower:
            if not entry.get("sample_sufficient"):
                return None
            return entry
    return None


def _format_source_quality_badge(metrics: dict[str, Any] | None) -> str:
    """One-line precision badge: '50% · n=30 · CI [33–67]'. Empty when missing."""
    if not metrics:
        return ""
    rate = metrics.get("hit_rate_pct")
    n = metrics.get("resolved")
    low = metrics.get("ci_low_pct")
    high = metrics.get("ci_high_pct")
    if not isinstance(rate, (int, float)) or not isinstance(n, int):
        return ""
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return ""
    return f"{rate:.0f}% · n={n} · CI [{low:.0f}–{high:.0f}]"


def format_approval_message(
    record: dict[str, Any],
    *,
    ttl_minutes: int,
    source_quality_report_path: Path | None = None,
    now: datetime | None = None,
) -> str:
    """Human-readable approval prompt. Callable independent of bot transport.

    ``record`` is the envelope JSONL entry (output of build_envelope_record).
    Returned string is Markdown-safe for Telegram — no escape helpers required
    since we only emit digits / ticker symbols / short labels.

    Decision-Glance header (R/R, position-risk, absolute TTL deadline) and
    per-source quality badge are best-effort: any missing or geometrically
    invalid input drops just that field, the rest of the card still renders.
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

    rr = _compute_risk_reward(entry, sl, targets, direction)
    pos_risk_pct = _compute_position_risk_pct(entry, sl, leverage)
    ttl_endtime = _format_ttl_endtime(str(record.get("timestamp_utc", "")), ttl_minutes)

    quality_path = source_quality_report_path or DEFAULT_SOURCE_QUALITY_REPORT
    source_metrics = _load_source_quality(source, quality_path, now=now)
    source_badge = _format_source_quality_badge(source_metrics)

    entry_line = _fmt_price(entry)
    if entry_min is not None and entry_max is not None:
        entry_line = f"{_fmt_price(entry_min)}–{_fmt_price(entry_max)}"

    sl_pct = ""
    if isinstance(entry, (int, float)) and isinstance(sl, (int, float)) and entry:
        pct = (sl - entry) / entry * 100.0
        sl_pct = f" ({pct:+.2f}%)"

    lev_str = f"{int(leverage)}x" if isinstance(leverage, (int, float)) and leverage else "—"
    t_str = " / ".join(_fmt_price(t) for t in targets) if targets else "—"

    glance_parts: list[str] = [f"{symbol} · {direction} · {lev_str}"]
    if rr is not None:
        glance_parts.append(f"R/R 1:{rr:.1f}")
    if pos_risk_pct is not None:
        glance_parts.append(f"Risk {pos_risk_pct:.1f}%")
    if ttl_endtime is not None:
        glance_parts.append(f"TTL bis {ttl_endtime}")
    decision_glance = "🎯 *" + " · ".join(glance_parts) + "*"

    source_line = f"📡 *Signal* — `{source}`"
    if source_badge:
        source_line = f"📡 *Signal* — `{source}` · {source_badge}"

    lines = [
        decision_glance,
        "",
        source_line,
        f"*{symbol}* · *{direction}* · {lev_str}",
        f"Entry: `{entry_line}`",
        f"SL: `{_fmt_price(sl)}`{sl_pct}",
        f"Targets: `{t_str}`",
        "",
        f"_TTL: {ttl_minutes} Min_ · _env: `{record.get('envelope_id', '?')}`_",
    ]
    return "\n".join(lines)


def _compute_callback_hmac(
    secret: str, action: str, envelope_id: str, ttl_deadline_unix: int
) -> str:
    """Return first 8 hex chars of HMAC-SHA256 over the canonical token input.

    The HMAC binds ``action`` so a captured Fill-token cannot be re-used as
    Ignore (the action is signed, not just trusted from URL). It binds
    ``ttl_deadline_unix`` so a leaked token cannot be replayed past its
    TTL — even before ``handle_signal_approval``'s envelope-id dedup runs.
    """
    msg = f"{action}:{envelope_id}:{ttl_deadline_unix}".encode()
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return digest[:_CB_HMAC_PREFIX_LEN]


def build_inline_keyboard(
    envelope_id: str,
    *,
    secret: str | None = None,
    ttl_deadline_unix: int | None = None,
) -> list[list[dict[str, str]]]:
    """Telegram inline_keyboard for [Fill]/[Ignore] per envelope_id.

    When ``secret`` is non-empty AND ``ttl_deadline_unix`` is provided, the
    callback_data carries an HMAC tag + TTL window. The bot's parse-side
    rejects tokens with bad HMAC or expired TTL even before the envelope
    dedup gate fires.

    Legacy (secret missing): emits the 3-part form for backward-compat
    while the operator rolls out OPERATOR_APPROVAL_HMAC_SECRET.
    """
    use_signed = bool(secret) and ttl_deadline_unix is not None
    if use_signed:
        # mypy: secret is non-empty + ttl_deadline_unix is int at this point
        assert secret is not None and ttl_deadline_unix is not None
        fill_hmac = _compute_callback_hmac(secret, CB_FILL, envelope_id, ttl_deadline_unix)
        ignore_hmac = _compute_callback_hmac(secret, CB_IGNORE, envelope_id, ttl_deadline_unix)
        fill_cb = f"{CB_PREFIX}:{CB_FILL}:{envelope_id}:{ttl_deadline_unix}:{fill_hmac}"
        ignore_cb = f"{CB_PREFIX}:{CB_IGNORE}:{envelope_id}:{ttl_deadline_unix}:{ignore_hmac}"
    else:
        fill_cb = f"{CB_PREFIX}:{CB_FILL}:{envelope_id}"
        ignore_cb = f"{CB_PREFIX}:{CB_IGNORE}:{envelope_id}"
    return [
        [
            {"text": "✅ Fill", "callback_data": fill_cb},
            {"text": "❌ Ignore", "callback_data": ignore_cb},
        ]
    ]


@dataclass(frozen=True)
class CallbackAction:
    action: str  # "fill" | "ignore"
    envelope_id: str


def parse_callback_data(
    data: str,
    *,
    secret: str | None = None,
    now: datetime | None = None,
) -> CallbackAction | None:
    """Parse a Telegram callback_data string. Returns None if invalid.

    Validation rules:
    - Legacy 3-part tokens: accepted IFF ``secret`` is empty/None. When a
      secret is configured, legacy tokens are rejected — that's the whole
      point of enabling HMAC, an attacker cannot downgrade.
    - Signed 5-part tokens: accepted IFF the HMAC matches AND ``now`` is
      before ttl_deadline_unix. Either failure returns None silently —
      the bot answers the callback with a benign "ignored" rather than
      leaking which check fired.

    ``now`` is injectable for tests so TTL boundary cases are deterministic.
    """
    if not isinstance(data, str):
        return None
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != CB_PREFIX:
        return None

    code = parts[1]
    if code not in (CB_FILL, CB_IGNORE):
        return None
    action_name = "fill" if code == CB_FILL else "ignore"

    has_secret = bool(secret)

    if len(parts) == 3:
        # Legacy form — only accepted while secret is unset (migration mode)
        if has_secret:
            logger.info("[approval] legacy unsigned callback rejected under HMAC strict-mode")
            return None
        return CallbackAction(action=action_name, envelope_id=parts[2])

    if len(parts) == 5:
        env_id = parts[2]
        try:
            ttl_deadline_unix = int(parts[3])
        except ValueError:
            logger.warning("[approval] callback ttl_unix not int: %r", parts[3])
            return None
        provided_hmac = parts[4]

        if not has_secret:
            # Strict-mode not active yet — accept signed tokens too so a
            # mid-rollout flip-flop (secret set then unset) doesn't strand
            # in-flight buttons. Cheap to keep symmetric.
            return CallbackAction(action=action_name, envelope_id=env_id)

        assert secret is not None
        expected = _compute_callback_hmac(secret, code, env_id, ttl_deadline_unix)
        # Constant-time compare prevents timing oracles on the 8-hex tag.
        if not hmac.compare_digest(provided_hmac, expected):
            logger.warning("[approval] callback hmac mismatch env=%s — rejected", env_id)
            return None

        current = (now or datetime.now(UTC)).timestamp()
        if current > ttl_deadline_unix:
            logger.info(
                "[approval] callback ttl expired env=%s (deadline=%s now=%s)",
                env_id,
                ttl_deadline_unix,
                int(current),
            )
            return None

        return CallbackAction(action=action_name, envelope_id=env_id)

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


# Forward-only audit log for approval-bot send attempts (NEO-P-approval-send-audit-v1).
# Separate file from the envelope log to avoid breaking downstream envelope consumers.
_DEFAULT_SEND_AUDIT_LOG = Path("artifacts/telegram_approval_send.jsonl")

# Stable V1 field order for human-readable inspection. Schema is additive-only.
_SEND_AUDIT_FIELDS: tuple[str, ...] = (
    "timestamp_utc",
    "event",
    "stage",
    "envelope_id",
    "chat_id",
    "bot_message_id",
    "status",
    "failure_reason",
    "http_status",
    "tg_error_code",
    "error",
    "ttl_minutes",
)


def _append_send_audit(
    record: dict[str, Any],
    path: Path = _DEFAULT_SEND_AUDIT_LOG,
) -> None:
    """Append a single approval-send audit record (forward-only).

    Sync write, no lock, no aiofiles. Caller passes a fully-populated dict;
    we normalise field order for stable JSONL inspection but never mutate
    contents. Failure to write is logged + swallowed - audit must never
    kill the listener.
    """
    try:
        ordered: dict[str, Any] = {}
        for key in _SEND_AUDIT_FIELDS:
            ordered[key] = record.get(key)
        # Preserve any caller-supplied extras (forward-compatible) at the end.
        for key, value in record.items():
            if key not in ordered:
                ordered[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ordered, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("[approval] send-audit write failed: %s", exc)


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
    send_audit_log: Path | None = None,
    hmac_secret: str | None = None,
) -> dict[str, Any] | None:
    """POST sendMessage with an inline keyboard. Returns Telegram API response
    JSON (``result`` payload) or None on network/API failure.

    Kept intentionally minimal: no retries, no typing-indicator. The caller
    is a Telethon handler that shouldn't block the event loop on failures.
    Any exception is logged + swallowed, so a temporary Telegram outage never
    kills the channel listener.

    Forward-only audit (NEO-P-approval-send-audit-v1): every send attempt is
    persisted to ``send_audit_log`` (default _DEFAULT_SEND_AUDIT_LOG) with one
    of status=ok|failed and a structured failure_reason. No backfill.
    """
    import httpx

    env_id = record.get("envelope_id")
    audit_path = send_audit_log if send_audit_log is not None else _DEFAULT_SEND_AUDIT_LOG

    def _audit(extra: dict[str, Any]) -> None:
        # Only emit when env_id is present; without an envelope id the record
        # is unusable for downstream correlation.
        if not env_id:
            return
        base: dict[str, Any] = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "event": "telegram_approval_send",
            "stage": "approval_sent",
            "envelope_id": env_id,
            "chat_id": chat_id,
            "bot_message_id": None,
            "status": None,
            "failure_reason": None,
            "http_status": None,
            "tg_error_code": None,
            "error": None,
            "ttl_minutes": ttl_minutes,
        }
        base.update(extra)
        _append_send_audit(base, path=audit_path)

    if not bot_token or not chat_id or not env_id:
        logger.warning(
            "[approval] send refused - missing token/chat_id/envelope_id "
            "(token_set=%s chat_id=%s env_id=%s)",
            bool(bot_token),
            chat_id,
            env_id,
        )
        _audit({"status": "failed", "failure_reason": "missing_config"})
        return None

    text = format_approval_message(record, ttl_minutes=ttl_minutes)
    # 2026-05-14 P1 #9: signed callback_data when HMAC secret is configured.
    # ttl_deadline_unix is computed here (not at parse time) so the bot can
    # verify the token without re-fetching the envelope's send timestamp.
    ttl_deadline_unix = (
        int(datetime.now(UTC).timestamp()) + ttl_minutes * 60 if hmac_secret else None
    )
    keyboard = build_inline_keyboard(
        str(env_id), secret=hmac_secret, ttl_deadline_unix=ttl_deadline_unix
    )
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
            _audit(
                {
                    "status": "failed",
                    "failure_reason": "http_error",
                    "http_status": resp.status_code,
                    "error": resp.text[:200],
                }
            )
            return None
        body = resp.json()
        if not body.get("ok"):
            logger.warning("[approval] telegram sendMessage not-ok body=%s", body)
            _audit(
                {
                    "status": "failed",
                    "failure_reason": "api_not_ok",
                    "http_status": 200,
                    "tg_error_code": body.get("error_code"),
                    "error": str(body)[:200],
                }
            )
            return None
        result = body.get("result") or {}
        _audit(
            {
                "status": "ok",
                "http_status": 200,
                "bot_message_id": result.get("message_id") if isinstance(result, dict) else None,
            }
        )
        return result if isinstance(result, dict) else None
    except Exception as exc:  # noqa: BLE001 - log + swallow, caller shouldn't die
        logger.warning("[approval] telegram sendMessage exception: %s", exc)
        _audit({"status": "failed", "failure_reason": "exception", "error": repr(exc)[:200]})
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
