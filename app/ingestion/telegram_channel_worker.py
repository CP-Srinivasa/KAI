"""Telethon-based worker for the premium Telegram channel (B-3).

Listens to the target channel via MTProto, parses new messages with
telegram_channel_parser, and emits envelope-JSONL records for the bridge
(telegram_channel_envelope.emit_parsed_signal).

Telethon dependency is imported lazily — the module itself can be imported
without the package installed, so unit tests for the pure handler logic
don't need the MTProto stack.

Lifecycle:
- First-time auth: run ``telegram-channel setup`` once in an interactive
  terminal. Telethon will prompt for phone number + SMS code (+ 2FA
  password if enabled) and create a session file on disk.
- Steady-state: ``telegram-channel run`` attaches to the session file
  and listens indefinitely. No interactive prompts.

Fail-closed:
- ``INGESTION_TELEGRAM_CHANNEL_ENABLED=false`` → ``run_worker`` raises
  before any network call.
- Unparseable messages are logged to the raw-log for later review but do
  not fill. Bridge allowlist gates actual execution independently.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.settings import TelegramChannelIngestSettings, get_settings
from app.ingestion.telegram_channel_approval import (
    load_envelope_by_id,
    send_approval_request,
)
from app.ingestion.telegram_channel_envelope import emit_parsed_signal
from app.ingestion.telegram_channel_parser import parse_premium_channel_message

if TYPE_CHECKING:
    # Only imported for type-checkers; runtime uses lazy import.
    from telethon import TelegramClient  # noqa: F401

logger = logging.getLogger(__name__)


# ── Pure handler (testable without Telethon) ────────────────────────────────


def _append_raw_log(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("[channel-worker] raw log write failed: %s", exc)


def process_message(
    text: str,
    *,
    source_tag: str,
    chat_id: int | None,
    raw_log_path: Path,
    emit_fn: Callable[..., dict[str, object] | None] = emit_parsed_signal,
    now: datetime | None = None,
) -> dict[str, object]:
    """Parse one channel message and emit an envelope if it's a signal.

    Always appends a raw-log record (parsed or not) so that unparsed
    messages can be reviewed later. Returns a small summary:

        {"parsed": bool, "emitted": bool,
         "envelope_id": str | None, "reason": str}
    """
    ts = (now or datetime.now(UTC)).isoformat()
    parsed = parse_premium_channel_message(text or "")
    base: dict[str, object] = {
        "timestamp_utc": ts,
        "chat_id": chat_id,
        "text_len": len(text or ""),
    }
    if parsed is None:
        base["outcome"] = "not_a_signal"
        _append_raw_log(raw_log_path, base)
        return {
            "parsed": False,
            "emitted": False,
            "envelope_id": None,
            "reason": "not_a_signal",
        }

    base["outcome"] = "parsed"
    base["symbol"] = parsed.symbol
    base["direction"] = parsed.direction
    _append_raw_log(raw_log_path, base)

    record = emit_fn(
        parsed,
        source=source_tag,
        chat_id=chat_id,
        now=now,
    )
    if record is None:
        return {
            "parsed": True,
            "emitted": False,
            "envelope_id": None,
            "reason": "duplicate_or_write_failed",
        }
    return {
        "parsed": True,
        "emitted": True,
        "envelope_id": record.get("envelope_id"),
        "reason": "ok",
    }


# ── Telethon bridge (runtime) ───────────────────────────────────────────────


def _import_telethon() -> tuple[Any, Any]:
    """Lazy-import Telethon. Raise a clear error when the package is missing."""
    try:
        from telethon import TelegramClient, events  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "telethon is not installed. Install with: pip install telethon"
        ) from exc
    return TelegramClient, events


async def resolve_target_entity(client: Any, cfg: TelegramChannelIngestSettings) -> Any:
    """Resolve the channel entity by explicit chat_id or title-match.

    Prefers ``target_chat_id`` when set. Falls back to an iteration over
    the user's dialogs looking for an exact title match — because the
    premium channel has no @handle, this is the only robust option.
    """
    if cfg.target_chat_id:
        logger.info(
            "[channel-worker] resolving channel by chat_id=%s", cfg.target_chat_id
        )
        return await client.get_entity(cfg.target_chat_id)

    if not cfg.target_title.strip():
        raise RuntimeError(
            "No target configured. Set INGESTION_TELEGRAM_CHANNEL_TARGET_CHAT_ID "
            "or INGESTION_TELEGRAM_CHANNEL_TARGET_TITLE."
        )

    wanted = cfg.target_title.strip()
    logger.info("[channel-worker] resolving channel by title=%r", wanted)
    async for dialog in client.iter_dialogs():
        title = getattr(dialog, "title", None) or getattr(dialog.entity, "title", None)
        if isinstance(title, str) and title.strip() == wanted:
            logger.info(
                "[channel-worker] matched dialog id=%s title=%r",
                dialog.id,
                title,
            )
            return dialog.entity
    raise RuntimeError(f"Channel with title {wanted!r} not found in dialogs.")


async def list_dialogs(cfg: TelegramChannelIngestSettings) -> list[dict[str, object]]:
    """Enumerate dialog titles + ids — helper to find the channel's chat_id."""
    TelegramClient, _ = _import_telethon()  # noqa: N806 — class import alias
    if not cfg.api_id or not cfg.api_hash:
        raise RuntimeError(
            "api_id and api_hash are required. Set "
            "INGESTION_TELEGRAM_CHANNEL_API_ID and _API_HASH in .env."
        )
    client = TelegramClient(cfg.session_path, cfg.api_id, cfg.api_hash)
    await client.start()
    try:
        out: list[dict[str, object]] = []
        async for dialog in client.iter_dialogs():
            title = getattr(dialog, "title", None) or ""
            out.append(
                {
                    "id": getattr(dialog, "id", None),
                    "title": title,
                    "is_channel": bool(getattr(dialog, "is_channel", False)),
                    "is_group": bool(getattr(dialog, "is_group", False)),
                }
            )
        return out
    finally:
        await client.disconnect()


async def run_worker(cfg: TelegramChannelIngestSettings | None = None) -> None:
    """Connect and listen for new messages on the target channel. Blocks."""
    if cfg is None:
        cfg = get_settings().telegram_channel_ingest
    if not cfg.enabled:
        raise RuntimeError(
            "Telegram channel ingest is disabled "
            "(INGESTION_TELEGRAM_CHANNEL_ENABLED=false). Refusing to start."
        )
    if not cfg.api_id or not cfg.api_hash:
        raise RuntimeError(
            "api_id and api_hash are required. Set "
            "INGESTION_TELEGRAM_CHANNEL_API_ID and _API_HASH in .env."
        )

    TelegramClient, events = _import_telethon()  # noqa: N806 — class import alias
    raw_log = Path(cfg.raw_log_path)

    client = TelegramClient(cfg.session_path, cfg.api_id, cfg.api_hash)
    await client.start()
    try:
        entity = await resolve_target_entity(client, cfg)
        logger.info(
            "[channel-worker] listening on entity id=%s",
            getattr(entity, "id", "?"),
        )

        # Approval-Mode settings — resolved once, read-only per session.
        full_settings = get_settings()
        approval_enabled = full_settings.execution.operator_signal_approval_enabled
        approval_ttl_min = full_settings.execution.operator_signal_approval_ttl_minutes
        bot_token = full_settings.operator.telegram_bot_token
        admin_chat_ids = full_settings.operator.admin_chat_id_list
        approval_chat_id = admin_chat_ids[0] if admin_chat_ids else 0
        envelope_log_path = Path("artifacts/telegram_message_envelope.jsonl")

        @client.on(events.NewMessage(chats=entity))  # type: ignore[misc]
        async def _handler(event: Any) -> None:
            text = getattr(event, "raw_text", "") or getattr(event.message, "message", "")
            chat_id = getattr(event, "chat_id", None)
            summary = process_message(
                text,
                source_tag=cfg.source_tag,
                chat_id=chat_id,
                raw_log_path=raw_log,
            )
            logger.info(
                "[channel-worker] msg chat=%s parsed=%s emitted=%s reason=%s env=%s",
                chat_id,
                summary["parsed"],
                summary["emitted"],
                summary["reason"],
                summary.get("envelope_id"),
            )

            # Approval-Mode: if a signal was just emitted, ping the operator.
            # Fail-soft: missing bot token / chat id logs a warning but does
            # not break the listener. Approval skip => shadow envelope stays
            # in log but bridge drops it (no _approved source re-emit yet).
            if not (approval_enabled and summary["emitted"]):
                return
            env_id = summary.get("envelope_id")
            if not env_id:
                return
            if not bot_token or not approval_chat_id:
                logger.warning(
                    "[channel-worker] approval enabled but "
                    "OPERATOR_TELEGRAM_BOT_TOKEN or OPERATOR_ADMIN_CHAT_IDS missing — "
                    "skipping approval request for env=%s",
                    env_id,
                )
                return
            record = load_envelope_by_id(envelope_log_path, str(env_id))
            if record is None:
                logger.warning(
                    "[channel-worker] emitted envelope not readable from log "
                    "env=%s path=%s — skipping approval request",
                    env_id,
                    envelope_log_path,
                )
                return
            result = await send_approval_request(
                record,
                bot_token=bot_token,
                chat_id=approval_chat_id,
                ttl_minutes=approval_ttl_min,
            )
            logger.info(
                "[channel-worker] approval request env=%s sent=%s",
                env_id,
                result is not None,
            )

        logger.info("[channel-worker] entering run-loop")
        await client.run_until_disconnected()
    finally:
        await client.disconnect()


async def setup_auth(cfg: TelegramChannelIngestSettings | None = None) -> None:
    """Interactive first-time auth: creates/updates the session file.

    Telethon will prompt for phone number, SMS code, and (if set) 2FA
    password. After this succeeds, subsequent run_worker calls attach to
    the session file without prompting.
    """
    if cfg is None:
        cfg = get_settings().telegram_channel_ingest
    if not cfg.api_id or not cfg.api_hash:
        raise RuntimeError(
            "api_id and api_hash are required. Set them in .env first."
        )
    TelegramClient, _ = _import_telethon()  # noqa: N806 — class import alias
    client = TelegramClient(cfg.session_path, cfg.api_id, cfg.api_hash)
    await client.start()  # triggers interactive auth on first run
    me = await client.get_me()
    logger.info(
        "[channel-worker] auth ok: user_id=%s username=%s",
        getattr(me, "id", "?"),
        getattr(me, "username", "?"),
    )
    await client.disconnect()


__all__ = [
    "list_dialogs",
    "process_message",
    "resolve_target_entity",
    "run_worker",
    "setup_auth",
]
