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
from app.execution.target_completion_reconciler import (
    reconcile_target_completion,
)
from app.ingestion.telegram_channel_approval import (
    handle_signal_approval,
    load_envelope_by_id,
    send_approval_request,
)
from app.ingestion.telegram_channel_envelope import emit_parsed_signal
from app.ingestion.telegram_channel_parser import (
    TargetCompletionEvent,
    parse_premium_channel_message,
    parse_target_completion,
)

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


# F3 (2026-05-05): Heartbeat-Reactivity-Counter. Two distinct liveness
# signals — periodic heartbeat (process-alive) and message-driven
# reactivity (Telegram-updates-flowing). Persisted as JSON inside the
# heartbeat file; mtime is still updated as a side effect of the write
# so legacy mtime-only readers (pre-F3 canonical_read) keep working.
#
# State lives at module scope because run_worker is a single asyncio
# loop in a single process — counter mutations happen between awaits,
# so no lock is needed. Tests must clear() the dict in an autouse
# fixture to stop state leaking across cases.
_HEARTBEAT_STATE: dict[str, Any] = {}


def _write_heartbeat(path: Path) -> None:
    """Persist _HEARTBEAT_STATE as JSON. Fail-soft: warns, never raises.

    The write also updates mtime, so canonical_read's legacy mtime-only
    liveness check keeps working transparently.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_HEARTBEAT_STATE), encoding="utf-8")
    except OSError as exc:
        logger.warning("[channel-worker] heartbeat write failed: %s", exc)


def _init_heartbeat(path: Path) -> None:
    """Reset _HEARTBEAT_STATE on worker boot.

    Called once after ``client.start()``. Sets ``messages_since_boot=0``
    and stamps ``boot_iso``. Subsequent ``_record_message_observed`` calls
    increment the counter; ``_touch_heartbeat`` only refreshes the
    periodic-liveness timestamp without touching the counter.
    """
    now_iso = datetime.now(tz=UTC).isoformat()
    _HEARTBEAT_STATE.clear()
    _HEARTBEAT_STATE.update(
        {
            "boot_iso": now_iso,
            "last_heartbeat_iso": now_iso,
            "last_message_iso": None,
            "messages_since_boot": 0,
        }
    )
    _write_heartbeat(path)


def _record_message_observed(path: Path) -> None:
    """Increment counter + stamp last_message_iso on every observed message.

    Called from both the live ``_handler`` and the ``_replay_handler``,
    so reactivity reflects total Telegram-updates-flowing through the
    listener regardless of replay vs. live origin.

    Defensive: if state was never initialised (e.g. a unit test calls this
    helper directly without going through run_worker), fall back to a
    fresh init so we never silently drop the counter.
    """
    if not _HEARTBEAT_STATE:
        _init_heartbeat(path)
    now_iso = datetime.now(tz=UTC).isoformat()
    _HEARTBEAT_STATE["last_message_iso"] = now_iso
    _HEARTBEAT_STATE["last_heartbeat_iso"] = now_iso
    _HEARTBEAT_STATE["messages_since_boot"] = (
        int(_HEARTBEAT_STATE.get("messages_since_boot", 0)) + 1
    )
    _write_heartbeat(path)


def make_verbose_observer_handler(target_logger: logging.Logger) -> Callable[[Any], Any]:
    """Factory for the F4 diagnostic observer. Returns an async handler.

    The handler logs ``chat_id`` and ``msg_id`` of every NewMessage event
    Telethon delivers, **regardless of chat filter**. It exists to verify
    whether updates reach the worker process at all (as opposed to being
    silently dropped by ``events.NewMessage(chats=entity)``) — Hypotheses
    B and D from V19's 4-day-silence diagnosis.

    Strict diagnostic constraints (verified by tests):
    - Logs at ``DEBUG`` level only — invisible at the default ``INFO``
      logger config, so an accidental enable in production is still
      effectively silent.
    - Never logs ``raw_text`` / ``message`` content — irrelevant channels
      the user follows must not bleed into KAI logs (PII).
    - Never calls ``_record_message_observed`` — would inflate the F3
      reactivity counter with non-target-channel updates and corrupt the
      ``stale_silent`` classification.

    Returned handler must still be registered with
    ``@client.on(events.NewMessage())`` (without ``chats=`` filter) for
    the diagnostic to do anything.
    """

    async def _observer(event: Any) -> None:
        chat_id = getattr(event, "chat_id", None)
        msg = getattr(event, "message", None)
        msg_id = getattr(msg, "id", None) if msg is not None else None
        target_logger.debug("[channel-worker] verbose-observer: chat=%s msg_id=%s", chat_id, msg_id)

    return _observer


def _touch_heartbeat(path: Path) -> None:
    """Update heartbeat timestamp. Best-effort, non-fatal.

    Two modes:
    - Post-F3 (state initialised via ``_init_heartbeat``): JSON write,
      refreshes ``last_heartbeat_iso`` while preserving the counter.
    - Legacy / pre-F3 (state empty, e.g. in unit tests that don't go
      through run_worker): mtime-only touch on an empty file. Same
      semantics as before this patch.
    """
    if _HEARTBEAT_STATE:
        _HEARTBEAT_STATE["last_heartbeat_iso"] = datetime.now(tz=UTC).isoformat()
        _write_heartbeat(path)
        return
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


# F6 (2026-05-04): Telethon has two chat_id conventions:
# - entity.id        — unmarked positive int for Channels (e.g. 1275462917)
# - event.chat_id    — marked negative int with -100 prefix (e.g. -1001275462917)
#
# Pre-F6, the read path used entity.id (unmarked) while the live write path
# used event.chat_id (marked). Result: live-written checkpoints could never
# be found by the read path on the next boot — manifested as 4 days of
# Approval-Loop silence on 2026-05-02..04 (see operator_loop_silence_20260504.md).
#
# Canonical form going forward: marked. Public Telegram URLs, Bot-API, and
# deep-links all use the marked form, so persisting it matches the public
# identity. _checkpoint_chat_id_marked() normalises both incoming variants.
# get_last_seen_id() additionally falls back to legacy unmarked keys with a
# deprecation warning so existing checkpoints migrate transparently on the
# first save after upgrade.
_MARKED_CHANNEL_PREFIX = -1_000_000_000_000


def _checkpoint_chat_id_marked(chat_id: int) -> int:
    """Return marked chat_id form (negative, with -100 prefix for Channels).

    Heuristic: positive int → assumed unmarked Channel/Supergroup, gets prefix.
    Already-negative int → pass through unchanged. The listener only handles
    Channels (resolve_target_entity), so the heuristic is safe in this scope.
    """
    if chat_id > 0:
        return _MARKED_CHANNEL_PREFIX - chat_id
    return chat_id


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
    """Persist last_message_id for chat_id. Atomic via temp-file + replace.

    chat_id is normalised to the marked form (-100 prefix for Channels) before
    persistence so read-path and write-path agree on the JSON key. Legacy
    unmarked entries for the same chat are removed on save to migrate the
    checkpoint cleanly.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    state = load_checkpoint(path)
    canonical = _checkpoint_chat_id_marked(int(chat_id))
    # Drop the legacy (unmarked) entry for the same chat if it exists,
    # regardless of whether chat_id was passed in marked or unmarked form.
    if canonical < 0:
        unmarked_legacy_key = str(_MARKED_CHANNEL_PREFIX - canonical)
        state.pop(unmarked_legacy_key, None)
    state[str(canonical)] = {
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
    """Return the last_message_id we've seen for chat_id, or 0 if unknown.

    Tries marked form first (canonical post-F6), falls back to unmarked form
    (legacy pre-F6 from entity.id-based reads/writes). Logs a deprecation
    warning when only the legacy key matches; the next save_checkpoint() will
    migrate the entry to the canonical form.
    """
    canonical = _checkpoint_chat_id_marked(int(chat_id))
    entry = checkpoint.get(str(canonical))
    if entry and "last_message_id" in entry:
        try:
            return int(entry["last_message_id"])
        except (TypeError, ValueError):
            return 0
    # Legacy fallback: try the unmarked counterpart of the canonical key so
    # pre-F6 checkpoints (entity.id-based, unmarked) still resolve.
    if canonical < 0:
        unmarked_key = str(_MARKED_CHANNEL_PREFIX - canonical)
        legacy = checkpoint.get(unmarked_key)
        if legacy and "last_message_id" in legacy:
            logger.warning(
                "[channel-worker] checkpoint legacy unmarked key matched for "
                "chat_id=%s (canonical=%s) — will migrate on next save",
                chat_id,
                canonical,
            )
            try:
                return int(legacy["last_message_id"])
            except (TypeError, ValueError):
                return 0
    return 0


def process_message(
    text: str,
    *,
    source_tag: str,
    chat_id: int | None,
    message_id: int | None = None,
    raw_log_path: Path,
    emit_fn: Callable[..., dict[str, object] | None] = emit_parsed_signal,
    now: datetime | None = None,
    scale_factor: float | None = None,
) -> dict[str, object]:
    """Parse one channel message and emit an envelope if it's a signal.

    Always appends a raw-log record (parsed or not) so that unparsed
    messages can be reviewed later. Returns a small summary:

        {"parsed": bool, "emitted": bool,
         "envelope_id": str | None, "reason": str}

    ``scale_factor`` (2026-05-14 P1 #8): forwarded to ``emit_fn`` so the
    persisted envelope carries the resolved channel scale. ``None`` means
    market_data was unreachable at receive time and the envelope is
    annotated ``scale_unknown=True``.
    """
    ts = (now or datetime.now(UTC)).isoformat()
    parsed = parse_premium_channel_message(text or "")
    base: dict[str, object] = {
        "timestamp_utc": ts,
        "chat_id": chat_id,
        "message_id": message_id,
        "text_len": len(text or ""),
    }
    if parsed is None:
        # 2026-05-12 Sprint D: Bevor wir "not_a_signal" loggen, prüfen ob es
        # eine 🎯 all-TP-Completion-Meldung ist. Diese wird separat als
        # target_completion-Outcome im raw-log markiert und der Reconciler
        # läuft im Anschluss async. Hier nur das raw-log-Marker — der eigentliche
        # Reconcile-Call passiert in run_worker._handler / _replay_handler.
        completion = parse_target_completion(text or "")
        if completion is not None:
            base["outcome"] = "target_completion"
            base["symbol"] = completion.symbol
            base["touch_price"] = completion.touch_price
            _append_raw_log(raw_log_path, base)
            return {
                "parsed": False,
                "emitted": False,
                "envelope_id": None,
                "reason": "target_completion",
                "completion_symbol": completion.display_symbol,
                "completion_touch_price": completion.touch_price,
                "completion_raw_text": completion.raw_text,
            }
        base["outcome"] = "not_a_signal"
        # 2026-05-14 P1 #10: Parser-Feedback-Loop braucht ein Preview vom Roh-
        # Text damit der Operator beim stündlichen Aggregat sieht, welches
        # Channel-Format er nicht parsen kann. Nur für Long-Messages (>50 Zeichen)
        # — kurzer Chat-Noise (👍, "ok", "thanks") braucht keine Aufmerksamkeit.
        # Truncated auf 200 Zeichen damit das raw-log nicht explodiert.
        if len(text or "") > 50:
            base["text_preview"] = (text or "")[:200]
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
        message_id=message_id,
        source_platform="telegram" if message_id is not None else None,
        now=now,
        scale_factor=scale_factor,
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
        raise RuntimeError("telethon is not installed. Install with: pip install telethon") from exc
    return TelegramClient, events


async def resolve_target_entity(client: Any, cfg: TelegramChannelIngestSettings) -> Any:
    """Resolve the channel entity by explicit chat_id or title-match.

    Prefers ``target_chat_id`` when set. Falls back to an iteration over
    the user's dialogs looking for an exact title match — because the
    premium channel has no @handle, this is the only robust option.
    """
    if cfg.target_chat_id:
        logger.info("[channel-worker] resolving channel by chat_id=%s", cfg.target_chat_id)
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
_SEMANTIC_CANARY_PATH = Path("artifacts/telegram_channel_semantic_canary.json")


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
        _REPLAY_MARKER_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        logger.warning("[channel-worker] could not write replay marker: %s", exc)


def _write_semantic_canary(
    *,
    path: Path = _SEMANTIC_CANARY_PATH,
    chat_id: int,
    checkpoint_message_id: int,
    latest_message_id: int | None,
    replay_processed: int,
) -> None:
    """Persist source-vs-checkpoint semantics for health probes.

    Heartbeat answers "process alive"; this answers "Telegram source head is
    at, or has been reconciled into, our checkpoint." The healthcheck reads it
    without touching the Telethon session, avoiding SQLite session locks.
    """
    payload = {
        "checked_at": datetime.now(UTC).isoformat(),
        "source_platform": "telegram",
        "chat_id": int(chat_id),
        "checkpoint_message_id": int(checkpoint_message_id),
        "latest_message_id": latest_message_id,
        "gap": (
            max(0, int(latest_message_id) - int(checkpoint_message_id))
            if latest_message_id is not None
            else None
        ),
        "replay_processed": int(replay_processed),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("[channel-worker] semantic canary write failed: %s", exc)


async def _latest_message_id(client: Any, entity: Any) -> int | None:
    async for msg in client.iter_messages(entity, limit=1):
        msg_id = getattr(msg, "id", None)
        return int(msg_id) if isinstance(msg_id, int) else None
    return None


async def replay_missed_messages(
    client: Any,
    entity: Any,
    *,
    chat_id: int,
    last_seen_id: int,
    process_fn: Callable[[int, str], Any],
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
    - ``process_fn`` may be sync or async. If it returns a coroutine, the
      coroutine is awaited before continuing — required so the V25 replay
      handler can call the async ``send_approval_request`` for every
      replayed signal (symmetry with the live handler).
    """
    if last_seen_id <= 0:
        return {"scanned": 0, "processed": 0, "skipped_no_checkpoint": 1}
    collected: list[tuple[int, str]] = []
    async for msg in client.iter_messages(
        entity,
        min_id=last_seen_id,
        limit=max_replay,
    ):
        msg_id = getattr(msg, "id", None)
        if not isinstance(msg_id, int) or msg_id <= last_seen_id:
            continue
        text = getattr(msg, "raw_text", "") or getattr(msg, "message", "") or ""
        collected.append((msg_id, text))
    collected.sort(key=lambda item: item[0])
    processed = 0
    for msg_id, text in collected:
        try:
            maybe_coro = process_fn(msg_id, text)
            if asyncio.iscoroutine(maybe_coro):
                await maybe_coro
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
        cfg.session_path,
        cfg.api_id,
        cfg.api_hash,
        catch_up=True,
        # F1 (2026-05-04): FloodWait-Härtung. Default flood_sleep_threshold=60s
        # crasht bei FloodWait > 60s mit FloodWaitError; auf 300s gesetzt damit
        # Telethon intern wartet statt aufzugeben. connection_retries=10 (default 5)
        # gegen TCP-Flapping bei chronischer Telegram-Drosselung (V19-Befund:
        # 1039× InvalidBufferError 429 in jüngsten ~50000 stderr-Zeilen).
        flood_sleep_threshold=300,
        connection_retries=10,
        retry_delay=2,
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


_POLL_MAX_CONSECUTIVE_FAILURES = 5


async def _poll_backstop_loop(
    client: Any,
    entity: Any,
    checkpoint_path: Path,
    process_fn: Callable[[int, str], Any],
    chat_id_marked: int,
    interval_s: int,
) -> None:
    """Active poll backstop against silent MTProto update-stream death.

    run_until_disconnected only reacts to *push* updates; Telethon can
    silently stop delivering them without raising (the heartbeat loop
    keeps ticking so the process looks alive while the channel is dark).
    This loop polls the channel every ``interval_s`` seconds via
    ``replay_missed_messages`` against the *current* on-disk checkpoint,
    so any message the push stream missed is pulled within one interval.
    Idempotent (emit_parsed_signal dedups by idempotency_key) and
    fail-soft. After ``_POLL_MAX_CONSECUTIVE_FAILURES`` consecutive
    failures (total connection death) it disconnects the client so
    systemd restarts the worker (Restart=always) and the next boot's
    replay recovers the gap.
    """
    consecutive_failures = 0
    while True:
        try:
            await asyncio.sleep(interval_s)
            checkpoint = load_checkpoint(checkpoint_path)
            last_seen = get_last_seen_id(checkpoint, chat_id_marked)
            result = await replay_missed_messages(
                client,
                entity,
                chat_id=chat_id_marked,
                last_seen_id=last_seen,
                process_fn=process_fn,
            )
            updated_checkpoint = load_checkpoint(checkpoint_path)
            updated_last_seen = get_last_seen_id(updated_checkpoint, chat_id_marked)
            latest_id = await _latest_message_id(client, entity)
            _write_semantic_canary(
                chat_id=chat_id_marked,
                checkpoint_message_id=updated_last_seen,
                latest_message_id=latest_id,
                replay_processed=int(result.get("processed", 0)),
            )
            consecutive_failures = 0
            processed = int(result.get("processed", 0))
            if processed > 0:
                logger.warning(
                    "[channel-worker] poll-backstop recovered %s message(s) "
                    "the push stream missed (last_seen=%s)",
                    processed,
                    last_seen,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — backstop must never crash listener
            consecutive_failures += 1
            logger.warning(
                "[channel-worker] poll-backstop iteration failed (%s/%s consecutive): %s",
                consecutive_failures,
                _POLL_MAX_CONSECUTIVE_FAILURES,
                exc,
            )
            if consecutive_failures >= _POLL_MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "[channel-worker] poll-backstop hit %s consecutive "
                    "failures — disconnecting for systemd restart + "
                    "boot-replay recovery",
                    consecutive_failures,
                )
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                return


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
        cfg.session_path,
        cfg.api_id,
        cfg.api_hash,
        catch_up=True,
        # F1 (2026-05-04): FloodWait-Härtung. Default flood_sleep_threshold=60s
        # crasht bei FloodWait > 60s mit FloodWaitError; auf 300s gesetzt damit
        # Telethon intern wartet statt aufzugeben. connection_retries=10 (default 5)
        # gegen TCP-Flapping bei chronischer Telegram-Drosselung (V19-Befund:
        # 1039× InvalidBufferError 429 in jüngsten ~50000 stderr-Zeilen).
        flood_sleep_threshold=300,
        connection_retries=10,
        retry_delay=2,
    )
    await client.start()
    # F3 (2026-05-05): initialise heartbeat-reactivity-state at boot so
    # the counter resets to 0 and boot_iso is stamped. Pre-F3 this was a
    # plain _touch_heartbeat — kept that name but split into init (boot),
    # touch (periodic), and record_message_observed (per Telegram update).
    _init_heartbeat(heartbeat_path)
    heartbeat_task: asyncio.Task[None] | None = None
    poll_task: asyncio.Task[None] | None = None
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
        # 2026-05-14 P1 #9: HMAC secret for callback_data. Empty string = legacy
        # unsigned mode; non-empty = strict-mode (signed tokens, TTL-enforced).
        approval_hmac_secret = full_settings.execution.operator_signal_approval_hmac_secret or ""
        # 2026-05-12 Sprint B: Premium-Auto-Fill (paper-mode-only). Wenn aktiv,
        # triggert der Worker nach jedem accepted Envelope sofort den fill-Pfad
        # ohne Operator-Klick. ADR 0004.
        auto_fill_enabled = full_settings.execution.operator_signal_premium_auto_fill_enabled
        bot_token = full_settings.operator.telegram_bot_token
        admin_chat_ids = full_settings.operator.admin_chat_id_list
        approval_chat_id = admin_chat_ids[0] if admin_chat_ids else 0
        envelope_log_path = Path("artifacts/telegram_message_envelope.jsonl")

        def _reconcile_completion_if_present(
            summary: dict[str, object], *, msg_id: int | None
        ) -> None:
            """Run target_completion-reconciler for 🎯 all-TP-completion messages.

            Sprint D (2026-05-12). Fail-soft: any exception is logged and
            swallowed — reconcile must never crash the listener-loop. The
            "envelope_id" used for reconciliation idempotency is synthesised
            from chat_id + msg_id when present, else falls back to a timestamp,
            so a checkpoint-replay of the same 🎯-message is a no-op.
            """
            if summary.get("reason") != "target_completion":
                return
            display_symbol_raw = summary.get("completion_symbol")
            if not isinstance(display_symbol_raw, str) or not display_symbol_raw:
                return
            touch_price_raw = summary.get("completion_touch_price")
            touch_price = (
                float(touch_price_raw)
                if isinstance(touch_price_raw, (int, float))
                and not isinstance(touch_price_raw, bool)
                else None
            )
            raw_text_raw = summary.get("completion_raw_text")
            raw_text = raw_text_raw if isinstance(raw_text_raw, str) else ""
            # Synthetic envelope_id für Reconcile-Idempotency (separat vom
            # NewSignal-Envelope-Stream weil Completion-Meldungen keinen
            # eigenen Envelope erzeugen).
            synthetic_env_id = (
                f"TGCOMPL-{chat_id_marked}-{msg_id}"
                if msg_id is not None
                else f"TGCOMPL-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
            )
            # Internal symbol = display ohne /
            internal_symbol = display_symbol_raw.replace("/", "")
            event = TargetCompletionEvent(
                symbol=internal_symbol,
                display_symbol=display_symbol_raw,
                touch_price=touch_price,
                raw_text=raw_text,
            )
            try:
                outcome = reconcile_target_completion(
                    event=event,
                    source_envelope_id=synthetic_env_id,
                )
                logger.info(
                    "[channel-worker] target-completion reconcile "
                    "env=%s sym=%s status=%s reason=%s",
                    synthetic_env_id,
                    display_symbol_raw,
                    outcome.status,
                    outcome.reason,
                )
            except Exception as exc:  # noqa: BLE001 — reconcile must never crash listener
                logger.warning(
                    "[channel-worker] reconcile failed sym=%s env=%s err=%s",
                    display_symbol_raw,
                    synthetic_env_id,
                    exc,
                )

        async def _auto_fill_envelope(env_id: str, *, replay: bool = False) -> None:
            """Auto-fill the envelope without waiting for an operator click.

            Writes the same `_approved` re-emit record that a manual Fill-click
            would produce, so the bridge picks it up via the existing source-
            allowlist path. Idempotent: handle_signal_approval refuses double-
            approval for the same origin_envelope_id.
            """
            if not auto_fill_enabled:
                return
            try:
                outcome = handle_signal_approval(
                    action="fill",
                    envelope_id=env_id,
                    envelope_log=envelope_log_path,
                    ttl_minutes=approval_ttl_min,
                    approved_by="auto-fill",
                )
                logger.info(
                    "[channel-worker] auto-fill env=%s replay=%s outcome=%s reason=%s",
                    env_id,
                    replay,
                    outcome.status,
                    outcome.reason,
                )
            except Exception as exc:  # noqa: BLE001 — auto-fill must never crash listener
                logger.warning(
                    "[channel-worker] auto-fill failed env=%s replay=%s err=%s",
                    env_id,
                    replay,
                    exc,
                )

        # Gap-Replay: before attaching the live handler, ask Telegram for
        # any messages that arrived after our last checkpoint. catch_up=True
        # only handles the short Telegram-side update-state window; this
        # closes the longer-outage gap (e.g. laptop sleep, restart, session
        # rebuild). Skipped silently when no checkpoint exists yet so we
        # don't accidentally replay channel history on first run.

        # 2026-05-14 P1 #8: pre-receive channel-scale resolver. Worker fetches
        # the current provider price for the parsed symbol and detects the
        # power-of-ten factor BEFORE persisting the envelope. Two outcomes:
        # - factor resolved (1.0 .. 1e8) → envelope-payload carries scaled
        #   values + ``scale_resolved_at_emit=True`` + ``scale_factor=X``.
        #   Bridge skips its own re-detection (cheaper per tick).
        # - market_data unreachable → factor=None → envelope marked
        #   ``scale_unknown=True``; bridge falls back to per-tick detection
        #   until the provider answers (legacy behaviour).
        # Fail-soft: any exception in the resolver path is logged and we fall
        # through with scale_factor=None — never block the emit on price-fetch.
        async def _resolve_channel_scale(text: str) -> float | None:
            try:
                from app.execution.scale_resolver import resolve_scale_for_symbol

                parsed = parse_premium_channel_message(text or "")
                if parsed is None or parsed.entry_value is None or parsed.entry_value <= 0:
                    return None
                return await resolve_scale_for_symbol(
                    parsed.display_symbol, float(parsed.entry_value)
                )
            except Exception as exc:  # noqa: BLE001 — scale-resolve must not stall the listener
                logger.warning(
                    "[channel-worker] scale-resolve failed: %s — emitting as scale_unknown",
                    exc,
                )
                return None

        # V25 (2026-05-04): single approval-send helper used by BOTH the live
        # NewMessage handler and the gap-replay loop. Pre-V25 only the live
        # handler sent approval pushes — replayed signals slipped through to
        # the envelope log silently, the operator never got a Fill/Ignore
        # button, and the bridge could not pick them up because no _approved
        # re-emit ever happened. Any restart / reconnect / cutover therefore
        # produced "ghost signals" that the dashboard surfaced but never
        # filled. Symmetric send eliminates that whole failure mode. See
        # listener_reactivity_followup_20260504.md.
        async def _send_approval_for_envelope(env_id: str, *, replay: bool = False) -> None:
            if not approval_enabled:
                return
            if not bot_token or not approval_chat_id:
                logger.warning(
                    "[channel-worker] approval enabled but "
                    "OPERATOR_TELEGRAM_BOT_TOKEN or OPERATOR_ADMIN_CHAT_IDS "
                    "missing — skipping approval request for env=%s",
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
                hmac_secret=approval_hmac_secret or None,
            )
            logger.info(
                "[channel-worker] approval request env=%s replay=%s sent=%s",
                env_id,
                replay,
                result is not None,
            )

        # 2026-05-31 (poll-backstop): wiring set inside the checkpoint
        # block below; pre-declared so the names exist even when the
        # entity has no integer id (poll then stays inactive).
        poll_replay_handler: Callable[[int, str], Any] | None = None
        poll_chat_id_marked: int | None = None
        entity_chat_id_raw = getattr(entity, "id", None)
        if isinstance(entity_chat_id_raw, int):
            # F6 (2026-05-04): Normalise to marked form so this matches the
            # canonical chat_id format used by event.chat_id in the live
            # handler. Pre-F6, the read path saw the unmarked Telethon
            # entity.id while the live writer used the marked event.chat_id —
            # the two never aligned and replay was perpetually skipped on
            # boot. See operator_loop_silence_20260504.md and
            # listener_reactivity_followup_20260504.md.
            chat_id_marked = _checkpoint_chat_id_marked(entity_chat_id_raw)
            initial_checkpoint = load_checkpoint(checkpoint_path)
            last_seen = get_last_seen_id(initial_checkpoint, chat_id_marked)

            async def _replay_handler(msg_id: int, text: str) -> None:
                # F3 (2026-05-05): replay-pulled messages also count as
                # reactivity. After a Pi restart the first 0..N messages
                # arrive via replay (not live events); without this the
                # cold-boot window would look "0 messages since boot"
                # even when 50 signals were just recovered.
                _record_message_observed(heartbeat_path)
                # 2026-05-14 P1 #8: pre-resolve channel-scale-factor before
                # emit so the bridge does not have to re-detect on every tick.
                scale_factor = await _resolve_channel_scale(text)
                summary = process_message(
                    text,
                    source_tag=cfg.source_tag,
                    chat_id=chat_id_marked,
                    message_id=msg_id,
                    raw_log_path=raw_log,
                    scale_factor=scale_factor,
                )
                save_checkpoint(checkpoint_path, chat_id_marked, msg_id)
                # V25: replayed signals get the same approval-send treatment
                # as live signals so a restart/cutover never silently drops a
                # signal between accepted-stage and the operator's Fill click.
                env_id = summary.get("envelope_id")
                if summary.get("emitted") and isinstance(env_id, str):
                    await _send_approval_for_envelope(env_id, replay=True)
                    # 2026-05-12 Sprint B: Auto-Fill für Replay-Signale identisch
                    # zum Live-Pfad. Reihenfolge: erst approval-send (Operator
                    # sieht Button für manual override), dann auto-fill — der
                    # double-click-dedup von handle_signal_approval verhindert
                    # eine race wenn Operator zwischen den beiden Aktionen klickt.
                    await _auto_fill_envelope(env_id, replay=True)
                # Sprint D: Replay kann auch 🎯 all-TP-completion-messages enthalten.
                _reconcile_completion_if_present(summary, msg_id=msg_id)

            replay_result = await replay_missed_messages(
                client,
                entity,
                chat_id=chat_id_marked,
                last_seen_id=last_seen,
                process_fn=_replay_handler,
            )
            _write_replay_marker(replay_result)
            latest_id = await _latest_message_id(client, entity)
            checkpoint_after_replay = load_checkpoint(checkpoint_path)
            _write_semantic_canary(
                chat_id=chat_id_marked,
                checkpoint_message_id=get_last_seen_id(checkpoint_after_replay, chat_id_marked),
                latest_message_id=latest_id,
                replay_processed=int(replay_result.get("processed", 0)),
            )
            # 2026-05-31: hand the replay handler + marked chat-id to the
            # poll-backstop so it pulls via the same checkpoint path.
            poll_replay_handler = _replay_handler
            poll_chat_id_marked = chat_id_marked

        @client.on(events.NewMessage(chats=entity))  # type: ignore[misc]
        async def _handler(event: Any) -> None:
            # First action: record the message-observed reactivity event.
            # Counts toward messages_since_boot + stamps last_message_iso
            # so canonical_read can distinguish "process alive but channel
            # silent" from "process alive AND processing updates" — the
            # core blind spot from the V19 4-day silence.
            _record_message_observed(heartbeat_path)
            text = getattr(event, "raw_text", "") or getattr(event.message, "message", "")
            chat_id = getattr(event, "chat_id", None)
            msg_id = getattr(getattr(event, "message", None), "id", None)
            # 2026-05-14 P1 #8: pre-resolve channel-scale-factor before emit.
            scale_factor = await _resolve_channel_scale(text)
            summary = process_message(
                text,
                source_tag=cfg.source_tag,
                chat_id=chat_id,
                message_id=msg_id if isinstance(msg_id, int) else None,
                raw_log_path=raw_log,
                scale_factor=scale_factor,
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
            # not break the listener. Uses the same _send_approval_for_envelope
            # helper as the replay handler (V25 symmetry).
            if summary["emitted"]:
                env_id_live = summary.get("envelope_id")
                if isinstance(env_id_live, str):
                    await _send_approval_for_envelope(env_id_live, replay=False)
                    # 2026-05-12 Sprint B: Auto-Fill. Operator-Auftrag Sektion 4:
                    # "Auch wenn keine manuelle Bestätigung erfolgt, muss das
                    # Signal mindestens im Paper Trading verarbeitet werden."
                    # Wenn auto_fill_enabled=False, ist das ein No-op.
                    await _auto_fill_envelope(env_id_live, replay=False)
            # Sprint D: 🎯 all-TP-completion-messages reconcile-Pfad. Läuft
            # unabhängig vom emitted-Flag — Completion-Meldungen erzeugen
            # keinen Envelope, sie schließen nur bereits offene Positionen.
            _reconcile_completion_if_present(
                summary, msg_id=msg_id if isinstance(msg_id, int) else None
            )

        # F4 (2026-05-05): opt-in diagnostic observer (no chats= filter).
        # Verifies whether updates reach the worker process at all when
        # the entity-filtered _handler stays silent. Strictly diagnostic
        # — see make_verbose_observer_handler docstring for constraints.
        if getattr(cfg, "verbose_observer", False):
            verbose_observer = make_verbose_observer_handler(logger)
            client.on(events.NewMessage())(verbose_observer)  # type: ignore[misc]
            logger.info(
                "[channel-worker] F4 verbose-observer ENABLED — "
                "set INGESTION_TELEGRAM_CHANNEL_VERBOSE_OBSERVER=false to disable"
            )

        logger.info("[channel-worker] entering run-loop")
        # Periodic heartbeat — independent of channel chatter. Without
        # this a silent channel would let the watchdog flip to "stale"
        # 30 minutes into a perfectly healthy run.
        heartbeat_task = asyncio.create_task(_heartbeat_loop(heartbeat_path))

        # 2026-05-31 (poll-backstop): run_until_disconnected is push-only.
        # Telethon can silently stop delivering updates without raising
        # (connection stays "connected") while the heartbeat loop keeps
        # ticking — the process looks alive but the channel is dark. On
        # 2026-05-31 this lost a NIGHT/USDT premium signal: messages_since_
        # boot stuck at 1 for ~46h, the signal never reached _handler,
        # nothing surfaced in external-signals or the portfolio. This loop
        # actively PULLS via the same checkpoint+replay path so a dead push
        # stream can no longer cause silent signal loss. Idempotent
        # (emit_parsed_signal dedups by idempotency_key) + fail-soft.
        poll_interval = int(getattr(cfg, "poll_backstop_seconds", 90) or 0)
        if (
            poll_interval > 0
            and poll_replay_handler is not None
            and poll_chat_id_marked is not None
        ):
            poll_task = asyncio.create_task(
                _poll_backstop_loop(
                    client,
                    entity,
                    checkpoint_path,
                    poll_replay_handler,
                    poll_chat_id_marked,
                    poll_interval,
                )
            )
            logger.info(
                "[channel-worker] poll-backstop active interval=%ss",
                poll_interval,
            )
        else:
            logger.warning(
                "[channel-worker] poll-backstop INACTIVE "
                "(interval=%s handler=%s chat_id=%s) — push-only mode",
                poll_interval,
                poll_replay_handler is not None,
                poll_chat_id_marked,
            )

        # F2 (2026-05-04): structured exception handling around the long-
        # running run-loop. Pre-F2 a Telethon InvalidBufferError (HTTP 429
        # FloodWait, IOError on a TCP hiccup, ServerError on an MTProto
        # transport glitch) propagated unhandled and the process crashed
        # silently — visible only in stderr. systemd Restart=always brings
        # us back inside ~30s, and V25's replay-push then ensures any
        # signals that landed during the gap still reach the operator.
        # We catch the known transient classes here purely to LOG with the
        # right context (so the watchdog's stderr trail is searchable) and
        # then let systemd own the restart instead of pretending we can
        # repair Telethon's internal state in-process.
        try:
            await client.run_until_disconnected()
        except (asyncio.CancelledError, KeyboardInterrupt):
            # Operator-driven shutdown — respect it, no log noise.
            raise
        except Exception as exc:  # noqa: BLE001
            exc_name = type(exc).__name__
            transient_markers = (
                "InvalidBufferError",
                "ServerError",
                "RpcCallFailError",
                "TimeoutError",
                "IOError",
                "IncompleteReadError",
                "FloodWaitError",
                "ConnectionError",
            )
            is_transient = any(marker in exc_name for marker in transient_markers)
            if is_transient:
                logger.warning(
                    "[channel-worker] run-loop transient error %s: %s — "
                    "exiting for systemd Restart=always; V25 replay-push "
                    "will recover any missed signals on the next boot",
                    exc_name,
                    exc,
                )
            else:
                logger.exception(
                    "[channel-worker] run-loop unexpected error %s — "
                    "exiting for systemd Restart=always",
                    exc_name,
                )
            raise
    finally:
        for _bg_task in (heartbeat_task, poll_task):
            if _bg_task is not None:
                _bg_task.cancel()
                try:
                    await _bg_task
                except (asyncio.CancelledError, Exception):
                    # Cancellation is expected; any other tail-end error
                    # must not mask the original disconnect.
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
        raise RuntimeError("api_id and api_hash are required. Set them in .env first.")
    TelegramClient, _ = _import_telethon()  # noqa: N806 — class import alias
    client = TelegramClient(
        cfg.session_path,
        cfg.api_id,
        cfg.api_hash,
        catch_up=True,
        # F1 (2026-05-04): FloodWait-Härtung. Default flood_sleep_threshold=60s
        # crasht bei FloodWait > 60s mit FloodWaitError; auf 300s gesetzt damit
        # Telethon intern wartet statt aufzugeben. connection_retries=10 (default 5)
        # gegen TCP-Flapping bei chronischer Telegram-Drosselung (V19-Befund:
        # 1039× InvalidBufferError 429 in jüngsten ~50000 stderr-Zeilen).
        flood_sleep_threshold=300,
        connection_retries=10,
        retry_delay=2,
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
