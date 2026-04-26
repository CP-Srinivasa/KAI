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

import asyncio
import json
import logging
import os
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


# ── Liveness heartbeat (D-191 / S-003) ──────────────────────────────────────
#
# The 2026-04-21..24 outage proved that "session.mtime" alone is not a
# reliable liveness signal: when the channel is silent, Telethon doesn't
# touch the session file even though the worker is alive. Heartbeat is a
# dedicated file that the worker touches at startup, on every observed
# message, and on a 60-second timer. canonical_read picks it as a third
# liveness candidate (see _summarize_telegram_channel_ingest).
#
# Fail-soft: heartbeat write errors log a warning but never break the
# run-loop. Liveness is observability, not a control plane.


def _touch_heartbeat(path: Path) -> None:
    """Update the heartbeat file's mtime to *now*. Best-effort, non-fatal."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Touch semantics: create if missing, refresh mtime regardless.
        if path.exists():
            now_ts = datetime.now(tz=UTC).timestamp()
            os.utime(path, (now_ts, now_ts))
        else:
            path.write_bytes(b"")
    except OSError as exc:
        logger.warning("[channel-worker] heartbeat touch failed: %s", exc)


async def _heartbeat_loop(path: Path, interval_seconds: float = 60.0) -> None:
    """Periodically touch the heartbeat file until cancelled.

    Cancellation is the normal exit path (parent's ``finally`` block).
    All other OSError surface as warnings inside ``_touch_heartbeat``.
    """
    try:
        while True:
            _touch_heartbeat(path)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        # Re-raise so awaiters see the cancellation.
        raise


# ── Checkpoint + Gap-Detection (testable without Telethon) ──────────────────
#
# Telethon's catch_up=True only replays updates Telegram still has in its
# update-state window when the client reconnects (typically a few hours).
# After a longer outage — or a session-file rebuild — that window is gone
# and the listener silently misses every message that arrived while offline.
# That's exactly the 6-message gap that hit on 2026-04-23/-24.
#
# The checkpoint records the highest message_id we have processed per chat.
# On startup we ask Telegram for everything strictly newer than that id and
# re-run process_message on each, before attaching the live event handler.
# This is the bridge to the Pi-migration on 2026-05-01: a Laptop-Stack that
# has to survive sleep/reboots without dropping signals.


def load_checkpoint(path: Path) -> dict[str, dict[str, Any]]:
    """Load the per-chat checkpoint map. Missing/corrupt → empty dict.

    Schema: ``{"<chat_id>": {"last_message_id": int, "last_seen_at": iso}}``
    chat_id is stored as ``str`` because JSON object keys must be strings;
    callers convert to ``int`` themselves.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "[channel-worker] checkpoint unreadable at %s (%s) — starting fresh",
            path,
            exc,
        )
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, dict) and "last_message_id" in v:
            out[k] = v
    return out


def save_checkpoint(
    path: Path,
    chat_id: int,
    message_id: int,
    *,
    now: datetime | None = None,
) -> None:
    """Persist last_message_id for chat_id. Atomic via temp-file + replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    state = load_checkpoint(path)
    state[str(chat_id)] = {
        "last_message_id": int(message_id),
        "last_seen_at": (now or datetime.now(UTC)).isoformat(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("[channel-worker] checkpoint write failed: %s", exc)


def get_last_seen_id(checkpoint: dict[str, dict[str, Any]], chat_id: int) -> int:
    """Return the last_message_id we've seen for chat_id, or 0 if unknown."""
    entry = checkpoint.get(str(chat_id))
    if not entry:
        return 0
    raw = entry.get("last_message_id", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


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


_REPLAY_MARKER_PATH = Path("artifacts/.telegram_channel_replay.json")


def _write_replay_marker(result: dict[str, int]) -> None:
    # Persist the replay outcome so the operator-summary watchdog can show
    # whether gap-replay actually ran on this listener boot, and how many
    # messages it recovered. Marker is overwritten each boot (we only care
    # about the most recent attempt). Failure to write is non-fatal — the
    # listener keeps running, watchdog just stays blind to this attempt.
    try:
        _REPLAY_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "attempted_at": datetime.now(UTC).isoformat(),
            "scanned": int(result.get("scanned", 0)),
            "processed": int(result.get("processed", 0)),
            "skipped_no_checkpoint": int(result.get("skipped_no_checkpoint", 0)),
        }
        _REPLAY_MARKER_PATH.write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning(
            "[channel-worker] could not write replay marker: %s", exc
        )


async def replay_missed_messages(
    client: Any,
    entity: Any,
    *,
    chat_id: int,
    last_seen_id: int,
    process_fn: Callable[[int, str], None],
    max_replay: int = 200,
) -> dict[str, int]:
    """Fetch messages with id > last_seen_id and re-run process_fn on each.

    Returns ``{"scanned": int, "processed": int, "skipped_no_checkpoint": int}``.

    Behaviour:
    - ``last_seen_id <= 0`` → no prior checkpoint exists; we do NOT replay
      historical messages on first run (would replay the channel's history).
      The first live message will establish the baseline.
    - Telethon's ``iter_messages`` returns newest-first; we collect, sort
      ascending by id, then process so the checkpoint advances monotonically
      and the operator sees the same order Telegram delivered them in.
    - Per-message handler errors are logged and skipped so one bad message
      cannot abort the entire replay.
    """
    if last_seen_id <= 0:
        return {"scanned": 0, "processed": 0, "skipped_no_checkpoint": 1}
    collected: list[tuple[int, str]] = []
    async for msg in client.iter_messages(
        entity, min_id=last_seen_id, limit=max_replay,
    ):
        msg_id = getattr(msg, "id", None)
        if not isinstance(msg_id, int) or msg_id <= last_seen_id:
            continue
        text = (
            getattr(msg, "raw_text", "")
            or getattr(msg, "message", "")
            or ""
        )
        collected.append((msg_id, text))
    collected.sort(key=lambda item: item[0])
    processed = 0
    for msg_id, text in collected:
        try:
            process_fn(msg_id, text)
        except Exception as exc:  # noqa: BLE001 — one bad msg must not stall replay
            logger.warning(
                "[channel-worker] gap-replay handler error chat=%s msg=%s: %s",
                chat_id,
                msg_id,
                exc,
            )
            continue
        processed += 1
    if collected:
        logger.info(
            "[channel-worker] gap-replay chat=%s scanned=%d processed=%d "
            "from_msg_id=%d to_msg_id=%d",
            chat_id,
            len(collected),
            processed,
            collected[0][0],
            collected[-1][0],
        )
    else:
        logger.info(
            "[channel-worker] gap-replay chat=%s no missed messages since id=%d",
            chat_id,
            last_seen_id,
        )
    return {
        "scanned": len(collected),
        "processed": processed,
        "skipped_no_checkpoint": 0,
    }


async def list_dialogs(cfg: TelegramChannelIngestSettings) -> list[dict[str, object]]:
    """Enumerate dialog titles + ids — helper to find the channel's chat_id."""
    TelegramClient, _ = _import_telethon()  # noqa: N806 — class import alias
    if not cfg.api_id or not cfg.api_hash:
        raise RuntimeError(
            "api_id and api_hash are required. Set "
            "INGESTION_TELEGRAM_CHANNEL_API_ID and _API_HASH in .env."
        )
    client = TelegramClient(
        cfg.session_path, cfg.api_id, cfg.api_hash, catch_up=True,
    )
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
    checkpoint_path = Path(cfg.checkpoint_path)
    heartbeat_path = Path(cfg.heartbeat_path)

    client = TelegramClient(
        cfg.session_path, cfg.api_id, cfg.api_hash, catch_up=True,
    )
    await client.start()
    # First heartbeat right after a successful start — the watchdog must
    # see "alive" within seconds of process spawn, not only after the
    # first periodic tick (60 s away).
    _touch_heartbeat(heartbeat_path)
    heartbeat_task: asyncio.Task[None] | None = None
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

        # Gap-Replay: before attaching the live handler, ask Telegram for
        # any messages that arrived after our last checkpoint. catch_up=True
        # only handles the short Telegram-side update-state window; this
        # closes the longer-outage gap (e.g. laptop sleep, restart, session
        # rebuild). Skipped silently when no checkpoint exists yet so we
        # don't accidentally replay channel history on first run.
        entity_chat_id = getattr(entity, "id", None)
        if isinstance(entity_chat_id, int):
            initial_checkpoint = load_checkpoint(checkpoint_path)
            last_seen = get_last_seen_id(initial_checkpoint, entity_chat_id)

            def _replay_handler(msg_id: int, text: str) -> None:
                process_message(
                    text,
                    source_tag=cfg.source_tag,
                    chat_id=entity_chat_id,
                    raw_log_path=raw_log,
                )
                save_checkpoint(checkpoint_path, entity_chat_id, msg_id)

            replay_result = await replay_missed_messages(
                client,
                entity,
                chat_id=entity_chat_id,
                last_seen_id=last_seen,
                process_fn=_replay_handler,
            )
            _write_replay_marker(replay_result)

        @client.on(events.NewMessage(chats=entity))  # type: ignore[misc]
        async def _handler(event: Any) -> None:
            # First action: refresh the liveness heartbeat. Even if the
            # message is unparseable / not a signal, observing it counts
            # as proof the listener is alive.
            _touch_heartbeat(heartbeat_path)
            text = getattr(event, "raw_text", "") or getattr(event.message, "message", "")
            chat_id = getattr(event, "chat_id", None)
            msg_id = getattr(getattr(event, "message", None), "id", None)
            summary = process_message(
                text,
                source_tag=cfg.source_tag,
                chat_id=chat_id,
                raw_log_path=raw_log,
            )
            # Advance checkpoint after every observed message — including
            # non-signals. Otherwise a long run of chatter without signals
            # would leave the replay window open and re-process them on
            # the next restart.
            if isinstance(chat_id, int) and isinstance(msg_id, int):
                save_checkpoint(checkpoint_path, chat_id, msg_id)
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
        # Periodic heartbeat — independent of channel chatter. Without
        # this a silent channel would let the watchdog flip to "stale"
        # 30 minutes into a perfectly healthy run.
        heartbeat_task = asyncio.create_task(_heartbeat_loop(heartbeat_path))
        await client.run_until_disconnected()
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                # Cancellation is expected; any other tail-end error from
                # the heartbeat loop must not mask the original disconnect.
                pass
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
    client = TelegramClient(
        cfg.session_path, cfg.api_id, cfg.api_hash, catch_up=True,
    )
    await client.start()  # triggers interactive auth on first run
    me = await client.get_me()
    logger.info(
        "[channel-worker] auth ok: user_id=%s username=%s",
        getattr(me, "id", "?"),
        getattr(me, "username", "?"),
    )
    await client.disconnect()


__all__ = [
    "get_last_seen_id",
    "list_dialogs",
    "load_checkpoint",
    "process_message",
    "replay_missed_messages",
    "resolve_target_entity",
    "run_worker",
    "save_checkpoint",
    "setup_auth",
]
