"""Telegram operator bot for safe runtime control of KAI.

Handles:
- /status, /positions, /exposure, /signals, /daily_summary, /alert_status
- /quality — quality-bar metrics from hold report
- /annotate — pending alert annotation with inline buttons
- /approve, /reject — decision journal
- /pause, /resume, /kill — runtime control

This bot is separate from outbound alert delivery. It is the inbound operator
channel and remains fail-closed, admin-gated, and dry-run-safe by default.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx

if TYPE_CHECKING:
    from app.messaging.text_intent import TextIntentProcessor
    from app.messaging.voice_transcriber import VoiceTranscriber

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"
_TELEGRAM_MAX_TEXT_LEN = 4096
_TELEGRAM_MAX_RETRIES = 3
_TELEGRAM_MAX_RETRY_SLEEP_SECONDS = 5
_READ_ONLY_COMMANDS = frozenset(
    {
        "status",
        "positions",
        "exposure",
        "signals",
        "signal_status",
        "daily_summary",
        "alert_status",
        "quality",
        "annotate",
    }
)
_GUARDED_AUDIT_COMMANDS = frozenset({"approve", "reject"})
_VALID_OUTCOMES = frozenset({"hit", "miss", "inconclusive"})
_DECISION_REF_PATTERN = re.compile(r"^dec_[0-9a-f]{12}$")
_WEBHOOK_ALLOWED_UPDATES_DEFAULT = ("message", "edited_message", "callback_query")
_WEBHOOK_MAX_BODY_BYTES_DEFAULT = 64_000
_WEBHOOK_MAX_SEEN_UPDATE_IDS_DEFAULT = 2_048
_WEBHOOK_REJECTION_AUDIT_LOG_DEFAULT = "artifacts/telegram_webhook_rejections.jsonl"

# Ephemeral menu commands: their output is tracked per chat in a ring buffer
# of size 3. The 4th such output deletes the oldest, keeping the chat focused
# on the most recent operator view. Signal / voice / exchange-response
# messages stay permanent — they carry audit value.
_EPHEMERAL_MENU_COMMANDS = frozenset(
    {
        "status",
        "positions",
        "exposure",
        "signals",
        "signalstatus",
        "signal_status",
        "alertstatus",
        "alert_status",
        "quality",
        "qualitaet",
        "daily_summary",
        "tagesbericht",
        "help",
        "hilfe",
        "menu_reload",
        "menue_reload",
        "menu_validate",
        "menue_validate",
    }
)
_EPHEMERAL_MENU_HISTORY_DEPTH = 3
_VOICE_DRAFT_TTL_SECONDS = 600  # voice-transkribierte Signale verfallen nach 10 Minuten ohne /ok
_SIGNAL_AUTO_RUN_ALLOWED_MODES = frozenset({"paper", "shadow"})
_SIGNAL_DIRECTION_MAP = {
    "bullish": "bullish",
    "long": "bullish",
    "buy": "bullish",
    "up": "bullish",
    "bearish": "bearish",
    "short": "bearish",
    "sell": "bearish",
    "down": "bearish",
    "neutral": "neutral",
    "flat": "neutral",
    "sideways": "neutral",
}
TELEGRAM_CANONICAL_COMMAND_REFS: dict[str, tuple[str, ...]] = {
    "positions": ("trading paper-positions-summary",),
    "exposure": ("trading paper-exposure-summary",),
    "signals": ("trading signals",),
    "approve": ("trading decision-journal-append",),
    "reject": ("trading decision-journal-append",),
}


def get_telegram_command_inventory() -> dict[str, object]:
    """Return the canonical Telegram command inventory used by tests/contracts."""
    return {
        "read_only_commands": sorted(_READ_ONLY_COMMANDS),
        "guarded_audit_commands": sorted(_GUARDED_AUDIT_COMMANDS),
        "webhook_transport_defaults": {
            "allowed_updates": list(_WEBHOOK_ALLOWED_UPDATES_DEFAULT),
            "max_body_bytes": _WEBHOOK_MAX_BODY_BYTES_DEFAULT,
        },
        "canonical_command_refs": {
            command: list(refs) for command, refs in TELEGRAM_CANONICAL_COMMAND_REFS.items()
        },
        # Backward-compatible key for existing consumers.
        "canonical_research_refs": {
            command: list(refs) for command, refs in TELEGRAM_CANONICAL_COMMAND_REFS.items()
        },
    }


@dataclass(frozen=True)
class TelegramWebhookProcessResult:
    """Outcome of transport-level webhook validation and dispatch."""

    accepted: bool
    processed: bool
    rejection_reason: str | None = None
    update_id: int | None = None
    update_type: str | None = None


class TelegramOperatorBot:
    """Operator command handler for Telegram."""

    def __init__(
        self,
        *,
        bot_token: str,
        admin_chat_ids: list[int],
        audit_log_path: str = "artifacts/operator_commands.jsonl",
        risk_engine: Any | None = None,
        dry_run: bool = True,
        webhook_secret_token: str | None = None,
        webhook_rejection_audit_log: str = _WEBHOOK_REJECTION_AUDIT_LOG_DEFAULT,
        webhook_allowed_updates: tuple[str, ...] = _WEBHOOK_ALLOWED_UPDATES_DEFAULT,
        webhook_max_body_bytes: int = _WEBHOOK_MAX_BODY_BYTES_DEFAULT,
        webhook_max_seen_update_ids: int = _WEBHOOK_MAX_SEEN_UPDATE_IDS_DEFAULT,
        text_processor: TextIntentProcessor | None = None,
        voice_transcriber: VoiceTranscriber | None = None,
        context_provider: Callable[[], Awaitable[str]] | None = None,
        signal_handoff_log_path: str = "artifacts/telegram_signal_handoff.jsonl",
        signal_exchange_outbox_log_path: str = "artifacts/telegram_exchange_outbox.jsonl",
        message_envelope_log_path: str = "artifacts/telegram_message_envelope.jsonl",
        signal_append_decision_enabled: bool = False,
        signal_auto_run_enabled: bool = False,
        signal_auto_run_mode: str = "paper",
        signal_auto_run_provider: str = "coingecko",
        signal_forward_to_exchange_enabled: bool = False,
        signal_exchange_sent_log_path: str = "artifacts/telegram_exchange_sent.jsonl",
        signal_exchange_dead_letter_log_path: str = "artifacts/telegram_exchange_dead_letter.jsonl",
        dashboard_url: str = "",
    ) -> None:
        normalized_updates = tuple(
            dict.fromkeys(
                update_type.strip().lower()
                for update_type in webhook_allowed_updates
                if update_type.strip()
            )
        )
        if not normalized_updates:
            raise ValueError("webhook_allowed_updates must contain at least one update type")
        if webhook_max_body_bytes <= 0:
            raise ValueError("webhook_max_body_bytes must be positive")
        if webhook_max_seen_update_ids <= 0:
            raise ValueError("webhook_max_seen_update_ids must be positive")
        normalized_signal_mode = signal_auto_run_mode.strip().lower()
        if normalized_signal_mode not in _SIGNAL_AUTO_RUN_ALLOWED_MODES:
            raise ValueError("signal_auto_run_mode must be one of: paper, shadow")

        self._token = bot_token
        self._admin_ids = set(admin_chat_ids)
        self._dashboard_url = (dashboard_url or "").strip()
        self._audit_path = Path(audit_log_path)
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._signal_handoff_log_path = Path(signal_handoff_log_path)
        self._signal_handoff_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._signal_exchange_outbox_log_path = Path(signal_exchange_outbox_log_path)
        self._signal_exchange_outbox_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._message_envelope_log_path = Path(message_envelope_log_path)
        self._message_envelope_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._signal_exchange_sent_log_path = Path(signal_exchange_sent_log_path)
        self._signal_exchange_sent_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._signal_exchange_dead_letter_log_path = Path(signal_exchange_dead_letter_log_path)
        self._signal_exchange_dead_letter_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._webhook_secret_token = (webhook_secret_token or "").strip()
        self._webhook_rejection_audit_path = Path(webhook_rejection_audit_log)
        self._webhook_rejection_audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._webhook_allowed_updates = normalized_updates
        self._webhook_max_body_bytes = webhook_max_body_bytes
        self._webhook_max_seen_update_ids = webhook_max_seen_update_ids
        self._webhook_seen_update_ids: OrderedDict[int, None] = OrderedDict()
        self._risk_engine = risk_engine
        self._dry_run = dry_run
        self._text_processor = text_processor
        self._voice_transcriber = voice_transcriber
        self._context_provider = context_provider
        self._signal_append_decision_enabled = signal_append_decision_enabled
        self._signal_auto_run_enabled = signal_auto_run_enabled
        self._signal_auto_run_mode = normalized_signal_mode
        self._signal_auto_run_provider = signal_auto_run_provider.strip()
        self._signal_forward_to_exchange_enabled = signal_forward_to_exchange_enabled
        self._pending_confirm: dict[int, str] = {}
        # Voice-Confirm-Gate: voice-transkribierte Signale parken hier, bis /ok oder /cancel
        self._pending_signal_draft: dict[int, dict[str, Any]] = {}
        self._system_status = "operational"
        self._invalid_command_refs = self._collect_invalid_command_refs()
        # Ring buffer of ephemeral menu message IDs per chat. On overflow the
        # oldest ID is deleted via Telegram's deleteMessage API (fail-soft).
        self._menu_history: dict[int, deque[int]] = {}
        # Set during _dispatch for ephemeral commands so _send can track IDs.
        self._track_ephemeral_reply = False

    @property
    def is_configured(self) -> bool:
        return bool(self._token) and bool(self._admin_ids)

    @property
    def webhook_configured(self) -> bool:
        return bool(self._webhook_secret_token)

    def get_webhook_status_summary(self) -> dict[str, object]:
        """Return read-only webhook hardening configuration/status."""
        return {
            "report_type": "telegram_webhook_status_summary",
            "webhook_configured": self.webhook_configured,
            "allowed_updates": list(self._webhook_allowed_updates),
            "max_body_bytes": self._webhook_max_body_bytes,
            "seen_update_ids": len(self._webhook_seen_update_ids),
            "max_seen_update_ids": self._webhook_max_seen_update_ids,
            "execution_enabled": False,
            "write_back_allowed": False,
        }

    def _collect_invalid_command_refs(self) -> tuple[str, ...]:
        refs = [
            ref
            for command_refs in TELEGRAM_CANONICAL_COMMAND_REFS.values()
            for ref in command_refs
        ]
        try:
            from app.cli.commands.trading import get_invalid_trading_command_refs

            invalid_refs = get_invalid_trading_command_refs(refs)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[BOT] Failed to validate canonical command refs; failing closed: %s",
                exc,
            )
            return tuple(sorted(set(refs)))
        return tuple(sorted(set(invalid_refs)))

    def _constant_time_secret_match(self, candidate: str) -> bool:
        if not self._webhook_secret_token:
            return False
        return hmac.compare_digest(candidate, self._webhook_secret_token)

    def _extract_allowed_update_type(self, update: dict[str, Any]) -> str | None:
        for update_type in self._webhook_allowed_updates:
            if update.get(update_type) is not None:
                return update_type
        return None

    def _track_webhook_update_id(self, update_id: int) -> None:
        self._webhook_seen_update_ids[update_id] = None
        if len(self._webhook_seen_update_ids) > self._webhook_max_seen_update_ids:
            self._webhook_seen_update_ids.popitem(last=False)

    def _audit_webhook_rejection(
        self,
        *,
        reason: str,
        method: str,
        content_type: str | None,
        content_length: int | None,
        update_id: int | None = None,
        update_type: str | None = None,
    ) -> None:
        record = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "event": "telegram_webhook_rejected",
            "reason": reason,
            "method": method,
            "content_type": content_type or "",
            "content_length": content_length,
            "update_id": update_id,
            "update_type": update_type,
            "allowed_updates": list(self._webhook_allowed_updates),
            "execution_enabled": False,
            "write_back_allowed": False,
        }
        try:
            with self._webhook_rejection_audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.error("[BOT] Webhook rejection audit write failed: %s", exc)

    def _reject_webhook(
        self,
        *,
        reason: str,
        method: str,
        content_type: str | None,
        content_length: int | None,
        update_id: int | None = None,
        update_type: str | None = None,
    ) -> TelegramWebhookProcessResult:
        logger.warning(
            (
                "[BOT] Webhook rejected: reason=%s method=%s content_type=%s "
                "content_length=%s update_id=%s update_type=%s"
            ),
            reason,
            method,
            content_type,
            content_length,
            update_id,
            update_type,
        )
        self._audit_webhook_rejection(
            reason=reason,
            method=method,
            content_type=content_type,
            content_length=content_length,
            update_id=update_id,
            update_type=update_type,
        )
        return TelegramWebhookProcessResult(
            accepted=False,
            processed=False,
            rejection_reason=reason,
            update_id=update_id,
            update_type=update_type,
        )

    async def process_webhook_update(
        self,
        *,
        method: str,
        content_type: str | None,
        content_length: int | None,
        header_secret_token: str | None,
        update: dict[str, Any] | None,
    ) -> TelegramWebhookProcessResult:
        """Validate webhook transport and dispatch the update fail-closed."""
        normalized_method = method.strip().upper()
        if not self.webhook_configured:
            return self._reject_webhook(
                reason="webhook_secret_not_configured",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
            )
        if normalized_method != "POST":
            return self._reject_webhook(
                reason="invalid_http_method",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
            )

        normalized_content_type = (content_type or "").strip().lower()
        if not normalized_content_type.startswith("application/json"):
            return self._reject_webhook(
                reason="invalid_content_type",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
            )

        if content_length is None:
            return self._reject_webhook(
                reason="missing_content_length",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
            )
        if content_length <= 0:
            return self._reject_webhook(
                reason="invalid_content_length",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
            )
        if content_length > self._webhook_max_body_bytes:
            return self._reject_webhook(
                reason="payload_too_large",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
            )

        candidate_token = (header_secret_token or "").strip()
        if not candidate_token:
            return self._reject_webhook(
                reason="missing_secret_token_header",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
            )
        if not self._constant_time_secret_match(candidate_token):
            return self._reject_webhook(
                reason="invalid_secret_token",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
            )

        if not isinstance(update, dict):
            return self._reject_webhook(
                reason="missing_or_invalid_update_body",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
            )

        raw_update_id = update.get("update_id")
        if not isinstance(raw_update_id, int) or raw_update_id < 0:
            return self._reject_webhook(
                reason="invalid_update_id",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
            )
        update_id = raw_update_id
        update_type = self._extract_allowed_update_type(update)
        if update_type is None:
            return self._reject_webhook(
                reason="disallowed_update_type",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
                update_id=update_id,
            )
        if update_id in self._webhook_seen_update_ids:
            return self._reject_webhook(
                reason="duplicate_update_id",
                method=normalized_method,
                content_type=content_type,
                content_length=content_length,
                update_id=update_id,
                update_type=update_type,
            )

        self._track_webhook_update_id(update_id)
        await self.process_update(update)
        return TelegramWebhookProcessResult(
            accepted=True,
            processed=True,
            update_id=update_id,
            update_type=update_type,
        )

    async def process_update(self, update: dict[str, Any]) -> None:
        """Process a single Telegram update (commands, free text, voice, callbacks)."""
        try:
            # Inline keyboard callback queries
            callback_query = update.get("callback_query")
            if callback_query:
                await self._handle_callback_query(callback_query)
                return

            message = update.get("message") or update.get("edited_message")
            if not message:
                return

            chat_id = message.get("chat", {}).get("id")
            if not chat_id:
                return

            # Auth gate Ã¢â‚¬â€ applies to all message types
            if chat_id not in self._admin_ids:
                text_preview = (message.get("text") or "voice")[:50]
                logger.warning(
                    "[BOT] Unauthorized message from chat_id=%s: %s",
                    chat_id,
                    text_preview,
                )
                await self._send(chat_id, "Unauthorized. This incident is logged.")
                return

            # Voice message Ã¢â€ â€™ transcribe Ã¢â€ â€™ intent pipeline
            voice = message.get("voice")
            if voice:
                await self._handle_voice(chat_id, voice)
                return

            text = (message.get("text") or "").strip()
            if not text:
                return

            if text.startswith("/"):
                command_parts = text.split(maxsplit=1)
                command = command_parts[0].lower().lstrip("/")
                args = command_parts[1].strip() if len(command_parts) > 1 else ""
                await self._dispatch(chat_id, command, args=args)
                return

            # Persistent reply-keyboard taps arrive as plain text labels.
            # Route them to the matching command before the LLM text path.
            from app.messaging.telegram_persistent_keyboard import (
                match_label_to_command,
            )
            mapped = match_label_to_command(text)
            if mapped is not None:
                await self._dispatch(chat_id, mapped)
                return

            await self._handle_text(chat_id, text, source="text")
        except Exception as exc:  # noqa: BLE001
            logger.error("[BOT] Error processing update: %s", exc)

    async def _handle_text(self, chat_id: int, text: str, *, source: str = "text") -> None:
        """Process free-text messages via LLM intent classification.

        Structured messages with [SIGNAL], [NEWS], or [EXCHANGE_RESPONSE]
        headers are parsed directly without LLM round-trip.
        """
        # Fast path: detect structured message format before LLM
        from app.messaging.signal_parser import detect_message_type

        msg_type = detect_message_type(text)
        if msg_type is not None:
            await self._handle_structured_message(chat_id, text, source=source)
            return

        if self._text_processor is None:
            await self._send(
                chat_id,
                "Freitext-Verarbeitung ist noch nicht aktiviert.\n"
                "Nutze /help fuer verfuegbare Befehle.",
            )
            return

        self._audit(chat_id, "_text", args=text)

        # Inject current analysis context if available
        context = ""
        if self._context_provider is not None:
            try:
                context = await self._context_provider()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[BOT] Context provider failed: %s", exc)

        result = await self._text_processor.process(text, context=context)
        logger.info(
            "[BOT] Text intent=%s mapped_command=%s signal=%s",
            result.intent,
            result.mapped_command,
            bool(result.signal),
        )

        # Natural-language command Ã¢â€ â€™ dispatch to existing handler
        if result.intent == "command" and result.mapped_command:
            await self._dispatch(chat_id, result.mapped_command)
            return

        # Signal input -> structured KAI handoff + optional guarded follow-up.
        if result.intent == "signal" and result.signal:
            # Voice-Confirm-Gate: voice-transkribierte Signale werden NICHT direkt
            # ausgeführt, sondern als Draft geparkt. Operator bestätigt mit /ok
            # oder verwirft mit /cancel. Text-Eingaben laufen weiter direkt durch.
            if source == "voice":
                await self._stash_voice_signal_draft(
                    chat_id=chat_id,
                    signal=result.signal,
                    source=source,
                    response=result.response,
                )
                return
            await self._handle_signal_input(
                chat_id=chat_id,
                signal=result.signal,
                source=source,
                response=result.response,
            )
            return

        # Query or chat Ã¢â€ â€™ direct response
        await self._send(chat_id, result.response)

    async def _handle_structured_message(
        self,
        chat_id: int,
        text: str,
        *,
        source: str = "text",
    ) -> None:
        """Parse and handle a structured [SIGNAL] / [NEWS] / [EXCHANGE_RESPONSE] message."""
        from app.messaging.message_formatter import (
            format_exchange_response_telegram,
            format_news_telegram,
            format_signal_telegram,
        )
        from app.messaging.message_models import (
            ExchangeResponse as MsgExchangeResponse,
        )
        from app.messaging.message_models import (
            NewsMessage as MsgNewsMessage,
        )
        from app.messaging.message_models import (
            TradingSignal as MsgTradingSignal,
        )
        from app.messaging.message_schema import (
            MessageSchemaValidationError,
            validate_message_model,
        )
        from app.messaging.signal_parser import (
            SignalParseError,
            detect_message_type,
            parse_structured_message,
        )

        self._audit(chat_id, "_structured_input", args=text[:500])
        detected_type = detect_message_type(text) or "unknown"

        try:
            parsed = parse_structured_message(text)
        except SignalParseError as exc:
            self._audit_message_envelope(
                chat_id=chat_id,
                message_type=detected_type,
                stage="parse",
                status="rejected",
                source=source,
                payload={"text_preview": text[:300]},
                errors=[self._inline(exc)],
            )
            await self._send(
                chat_id,
                "*Structured-Format Fehler*\n"
                f"{self._inline(exc)}\n"
                "Erwartet wird [NEWS], [SIGNAL] oder [EXCHANGE_RESPONSE] mit Feldzeilen.",
            )
            return

        try:
            schema_payload = validate_message_model(parsed)
        except MessageSchemaValidationError as exc:
            error_lines = exc.errors or [self._inline(exc)]
            self._audit_message_envelope(
                chat_id=chat_id,
                message_type=detected_type,
                stage="schema_validation",
                status="rejected",
                source=source,
                payload={"text_preview": text[:300]},
                errors=error_lines,
            )
            details = "\n".join(f"- `{self._inline(item)}`" for item in error_lines[:8])
            await self._send(
                chat_id,
                "*Structured-Schema Fehler (fail-closed)*\n"
                "Die Nachricht verletzt den 3-Typen-Standard:\n"
                f"{details}",
            )
            return

        if isinstance(parsed, MsgNewsMessage):
            formatted = format_news_telegram(parsed)
            self._audit_message_envelope(
                chat_id=chat_id,
                message_type="news",
                stage="accepted",
                status="ok",
                source=source,
                payload=schema_payload,
                parsed_payload=parsed,
            )
            await self._send(
                chat_id,
                f"{formatted}\n\n"
                "Analyse-only. NEWS fuehrt nie zu einer Order-Ausfuehrung.",
            )
            logger.info("[BOT] Structured NEWS received from %s", chat_id)
            return

        if isinstance(parsed, MsgTradingSignal):
            formatted = format_signal_telegram(parsed)
            validation_errors = parsed.validation_errors
            if validation_errors:
                self._audit_message_envelope(
                    chat_id=chat_id,
                    message_type="signal",
                    stage="execution_gate",
                    status="blocked",
                    source=source,
                    payload=schema_payload,
                    errors=validation_errors,
                )
                error_lines = "\n".join(
                    f"- `{self._inline(error)}`" for error in validation_errors[:8]
                )
                await self._send(
                    chat_id,
                    f"{formatted}\n\n"
                    "*Signal blockiert (fail-closed)*\n"
                    "Pflichtfelder fehlen oder sind ungueltig:\n"
                    f"{error_lines}",
                )
                return

            display_symbol = parsed.display_symbol or parsed.symbol
            asset = display_symbol.split("/", 1)[0] if "/" in display_symbol else display_symbol
            direction_hint_map = {"long": "bullish", "short": "bearish", "neutral": "neutral"}
            direction_hint = direction_hint_map.get(parsed.direction.value, "neutral")
            signal_dict = parsed.to_dict()
            signal_dict.update(
                {
                    "asset": asset,
                    "direction": direction_hint,
                    "reasoning": parsed.notes or f"structured_signal:{parsed.signal_id}",
                }
            )
            idempotency_key = self._compute_idempotency_key(parsed)
            if idempotency_key and self._is_duplicate_envelope(idempotency_key):
                self._audit_message_envelope(
                    chat_id=chat_id,
                    message_type="signal",
                    stage="idempotency_gate",
                    status="duplicate",
                    source=source,
                    payload=schema_payload,
                    parsed_payload=parsed,
                    metadata={"idempotency_key": idempotency_key},
                )
                await self._send(
                    chat_id,
                    f"{formatted}\n\n"
                    "*Signal als Duplikat erkannt - keine erneute Weiterleitung* "
                    f"(`idem={idempotency_key[:12]}`).",
                )
                return
            self._audit_message_envelope(
                chat_id=chat_id,
                message_type="signal",
                stage="accepted",
                status="ok",
                source=source,
                payload=schema_payload,
                parsed_payload=parsed,
            )
            await self._handle_signal_input(
                chat_id=chat_id,
                signal=signal_dict,
                source=f"structured_{source}",
                response="",
            )
            return

        if isinstance(parsed, MsgExchangeResponse):
            formatted = format_exchange_response_telegram(parsed)
            self._audit_message_envelope(
                chat_id=chat_id,
                message_type="exchange_response",
                stage="accepted",
                status="ok",
                source=source,
                payload=schema_payload,
                parsed_payload=parsed,
            )
            await self._send(chat_id, formatted)
            logger.info("[BOT] Structured EXCHANGE_RESPONSE received from %s", chat_id)
            return

    async def _handle_voice(self, chat_id: int, voice: dict[str, Any]) -> None:
        """Transcribe a voice message via Whisper, then process as text."""
        if self._voice_transcriber is None or self._text_processor is None:
            await self._send(
                chat_id,
                "Sprachnachrichten-Verarbeitung ist noch nicht aktiviert.",
            )
            return

        file_id = voice.get("file_id")
        if not file_id:
            return

        duration = voice.get("duration", 0)
        self._audit(chat_id, "_voice", args=f"file_id={file_id} duration={duration}s")

        transcript = await self._voice_transcriber.transcribe(file_id)
        if not transcript:
            await self._send(chat_id, "Sprachnachricht konnte nicht transkribiert werden.")
            return

        # Show transcription, then process through intent pipeline
        await self._send(chat_id, f"Transkript: _{transcript}_")
        await self._handle_text(chat_id, transcript, source="voice")

    def _prune_expired_signal_draft(self, chat_id: int) -> dict[str, Any] | None:
        """Return the non-expired draft for chat_id, else drop and return None."""
        draft = self._pending_signal_draft.get(chat_id)
        if draft is None:
            return None
        created = draft.get("created_ts_epoch", 0.0)
        if not isinstance(created, (int, float)):
            created = 0.0
        age = datetime.now(UTC).timestamp() - float(created)
        if age > _VOICE_DRAFT_TTL_SECONDS:
            self._pending_signal_draft.pop(chat_id, None)
            self._audit_message_envelope(
                chat_id=chat_id,
                message_type="signal",
                stage="voice_confirm_gate",
                status="expired",
                source="voice",
                payload={"raw_signal": dict(draft.get("signal", {}))},
            )
            return None
        return draft

    async def _stash_voice_signal_draft(
        self,
        *,
        chat_id: int,
        signal: dict[str, Any],
        source: str,
        response: str,
    ) -> None:
        """Park a voice-transcribed signal and ask the operator to /ok or /cancel."""
        normalized = self._normalize_signal_payload(signal)
        if normalized is None:
            self._audit_message_envelope(
                chat_id=chat_id,
                message_type="signal",
                stage="voice_confirm_gate",
                status="rejected",
                source=source,
                payload={"raw_signal": dict(signal)},
                errors=["signal_payload_normalization_failed"],
            )
            await self._send(
                chat_id,
                "*Voice signal could not be interpreted*\n"
                "Please state the asset and direction clearly — for example "
                "\"BTC bullish\".",
            )
            return

        asset, symbol, direction, reasoning = normalized
        self._pending_signal_draft[chat_id] = {
            "signal": dict(signal),
            "source": source,
            "response": response,
            "created_ts_epoch": datetime.now(UTC).timestamp(),
            "asset": asset,
            "symbol": symbol,
            "direction": direction,
        }
        self._audit_message_envelope(
            chat_id=chat_id,
            message_type="signal",
            stage="voice_confirm_gate",
            status="draft_pending",
            source=source,
            payload={
                "asset": asset,
                "symbol": symbol,
                "direction": direction,
                "reasoning": reasoning,
            },
        )
        preview_lines = [
            "*Voice Signal — Draft*",
            "Review before confirming.",
            "",
            f"Asset: `{symbol}`",
            f"Direction: `{direction}`",
        ]
        if reasoning:
            preview_lines.append(f"Reasoning: _{reasoning}_")
        preview_lines.append("")
        preview_lines.append("Confirm with /ok · Discard with /cancel")
        preview_lines.append(
            f"Expires automatically after {_VOICE_DRAFT_TTL_SECONDS // 60} minutes."
        )
        await self._send(chat_id, "\n".join(preview_lines))

    async def _cmd_ok(self, chat_id: int, *, args: str = "") -> None:
        """Confirm a pending voice-signal draft → route through signal handoff."""
        draft = self._prune_expired_signal_draft(chat_id)
        if draft is None:
            await self._send(
                chat_id,
                "No pending voice signal. /ok is only valid after a voice message.",
            )
            return
        self._pending_signal_draft.pop(chat_id, None)
        self._audit_message_envelope(
            chat_id=chat_id,
            message_type="signal",
            stage="voice_confirm_gate",
            status="confirmed",
            source=str(draft.get("source", "voice")),
            payload={
                "asset": draft.get("asset"),
                "symbol": draft.get("symbol"),
                "direction": draft.get("direction"),
            },
        )
        await self._handle_signal_input(
            chat_id=chat_id,
            signal=dict(draft.get("signal", {})),
            source=str(draft.get("source", "voice")),
            response=str(draft.get("response", "")),
        )

    async def _cmd_cancel(self, chat_id: int, *, args: str = "") -> None:
        """Drop a pending voice-signal draft without executing."""
        draft = self._prune_expired_signal_draft(chat_id)
        if draft is None:
            await self._send(
                chat_id,
                "No pending voice signal — nothing to discard.",
            )
            return
        self._pending_signal_draft.pop(chat_id, None)
        self._audit_message_envelope(
            chat_id=chat_id,
            message_type="signal",
            stage="voice_confirm_gate",
            status="cancelled",
            source=str(draft.get("source", "voice")),
            payload={
                "asset": draft.get("asset"),
                "symbol": draft.get("symbol"),
                "direction": draft.get("direction"),
            },
        )
        await self._send(chat_id, "Voice signal draft discarded.")

    async def _handle_signal_input(
        self,
        *,
        chat_id: int,
        signal: dict[str, Any],
        source: str,
        response: str,
    ) -> None:
        normalized = self._normalize_signal_payload(signal)
        if normalized is None:
            self._audit_message_envelope(
                chat_id=chat_id,
                message_type="signal",
                stage="normalize",
                status="rejected",
                source=source,
                payload={"raw_signal": dict(signal)},
                errors=["signal_payload_normalization_failed"],
            )
            await self._send(
                chat_id,
                "*Signal could not be normalized*\n"
                "Please state the asset and direction clearly — for example "
                "\"BTC bullish\".",
            )
            return

        asset, symbol, direction, reasoning = normalized
        self._audit(chat_id, "_signal_input", args=json.dumps(signal))

        # Use structured signal_id if present, otherwise generate
        signal_id = str(signal.get("signal_id", "")) or f"sig_{uuid4().hex[:12]}"
        self._audit_message_envelope(
            chat_id=chat_id,
            message_type="signal",
            stage="handoff_received",
            status="ok",
            source=source,
            payload={
                "signal_id": signal_id,
                "asset": asset,
                "symbol": symbol,
                "direction": direction,
            },
        )

        # Preserve structured signal data if available (from parse_structured_message)
        structured_data: dict[str, object] = {}
        if "message_type" in signal:
            # Full TradingSignal dict — preserve all fields
            structured_data = {
                k: v for k, v in signal.items()
                if k not in {"asset", "direction", "reasoning"}
            }

        handoff_record: dict[str, object] = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "event": "telegram_signal_handoff",
            "signal_id": signal_id,
            "chat_id": chat_id,
            "source": source,
            "asset": asset,
            "symbol": symbol,
            "direction": direction,
            "reasoning": reasoning,
            "raw_signal": dict(signal),
            "execution_enabled": False,
            "write_back_allowed": False,
        }
        if structured_data:
            handoff_record["structured_signal"] = structured_data

        # Build formatted signal confirmation via TradingSignal model
        from app.messaging.message_formatter import format_signal_telegram
        from app.messaging.message_models import Direction as MsgDirection
        from app.messaging.message_models import Side as MsgSide
        from app.messaging.message_models import TradingSignal as MsgTradingSignal

        direction_map = {"bullish": MsgDirection.LONG, "bearish": MsgDirection.SHORT}
        side_map = {"bullish": MsgSide.BUY, "bearish": MsgSide.SELL}
        sig_obj = MsgTradingSignal(
            signal_id=signal_id,
            source=source,
            symbol=symbol.replace("/", ""),
            display_symbol=symbol,
            side=side_map.get(direction, MsgSide.BUY),
            direction=direction_map.get(direction, MsgDirection.NEUTRAL),
            notes=reasoning,
            # Carry over structured fields if present
            stop_loss=(
                signal.get("stop_loss")
                if isinstance(signal.get("stop_loss"), (int, float))
                else None
            ),
            targets=(
                signal.get("targets", [])
                if isinstance(signal.get("targets"), list)
                else []
            ),
            leverage=int(signal.get("leverage", 1)) if signal.get("leverage") else 1,
            entry_value=(
                signal.get("entry_value")
                if isinstance(signal.get("entry_value"), (int, float))
                else None
            ),
        )
        # Internal log lines (not sent to Telegram)
        log_lines = [format_signal_telegram(sig_obj), ""]
        log_lines.append("*Pipeline*")

        if self._signal_append_decision_enabled:
            try:
                decision_payload = await self._append_decision_from_signal(
                    symbol=symbol,
                    direction=direction,
                    reasoning=reasoning,
                    source=source,
                )
                decision_id = str(decision_payload.get("decision_id", "unknown"))
                handoff_record["decision_append_status"] = "ok"
                handoff_record["decision_id"] = decision_id
                log_lines.append(f"Decision-Journal: `ok ({decision_id})`")
            except Exception as exc:  # noqa: BLE001
                logger.error("[BOT] Signal decision append failed: %s", exc)
                handoff_record["decision_append_status"] = "failed"
                handoff_record["decision_error"] = str(exc)
                log_lines.append("Decision-Journal: `failed`")
        else:
            handoff_record["decision_append_status"] = "disabled"
            log_lines.append("Decision-Journal: `disabled`")

        exchange_response_msg: str | None = None
        execution_success: bool = False
        if self._signal_auto_run_enabled:
            try:
                cycle_payload = await self._run_signal_cycle(
                    symbol=symbol,
                    direction=direction,
                )
                cycle = cycle_payload.get("cycle", {})
                cycle_status = (
                    cycle.get("status", "unknown")
                    if isinstance(cycle, dict)
                    else "unknown"
                )
                cycle_id = (
                    cycle.get("cycle_id", "unknown")
                    if isinstance(cycle, dict)
                    else "unknown"
                )
                handoff_record["signal_auto_run_status"] = "ok"
                handoff_record["signal_auto_run_cycle_status"] = cycle_status
                handoff_record["signal_auto_run_cycle_id"] = cycle_id
                log_lines.append(f"KAI-Run: `ok ({cycle_status})`")

                # Build formatted exchange response from cycle result
                exchange_response_msg = self._build_exchange_response_from_cycle(
                    signal_id=signal_id,
                    symbol=symbol,
                    cycle=cycle if isinstance(cycle, dict) else {},
                    signal=signal,
                )
                execution_success = exchange_response_msg is not None
            except Exception as exc:  # noqa: BLE001
                logger.error("[BOT] Signal auto-run failed: %s", exc)
                handoff_record["signal_auto_run_status"] = "failed"
                handoff_record["signal_auto_run_error"] = str(exc)
                log_lines.append("KAI-Run: `failed`")
        else:
            handoff_record["signal_auto_run_status"] = "disabled"
            log_lines.append("KAI-Run: `disabled`")

        if self._signal_forward_to_exchange_enabled:
            try:
                self._queue_signal_for_exchange(
                    signal_id=signal_id,
                    chat_id=chat_id,
                    source=source,
                    asset=asset,
                    symbol=symbol,
                    direction=direction,
                    reasoning=reasoning,
                    structured_signal=structured_data or None,
                )
                handoff_record["exchange_forward_status"] = "queued"
                log_lines.append("Exchange-Forward: `queued`")
            except OSError as exc:
                logger.error("[BOT] Exchange outbox queue failed: %s", exc)
                handoff_record["exchange_forward_status"] = "failed"
                handoff_record["exchange_forward_error"] = str(exc)
                log_lines.append("Exchange-Forward: `failed`")
        else:
            handoff_record["exchange_forward_status"] = "disabled"
            log_lines.append("Exchange-Forward: `disabled`")

        try:
            self._append_jsonl(self._signal_handoff_log_path, handoff_record)
        except OSError as exc:
            logger.error("[BOT] Signal handoff audit write failed: %s", exc)
            log_lines.append("Signal-Handoff-Log: `failed`")

        if response:
            log_lines.append("")
            log_lines.append(response)

        # Log full pipeline report internally (not sent to Telegram)
        logger.info("[BOT] Signal pipeline report:\n%s", "\n".join(log_lines))

        self._audit_message_envelope(
            chat_id=chat_id,
            message_type="signal",
            stage="handoff_completed",
            status="ok",
            source=source,
            payload={
                "signal_id": signal_id,
                "asset": asset,
                "symbol": symbol,
                "direction": direction,
                "decision_append_status": str(handoff_record.get("decision_append_status", "")),
                "signal_auto_run_status": str(handoff_record.get("signal_auto_run_status", "")),
                "exchange_forward_status": str(handoff_record.get("exchange_forward_status", "")),
            },
        )

        # Telegram: only compact confirmation (no signal echo, no pipeline report)
        if exchange_response_msg:
            await self._send(chat_id, exchange_response_msg)
        elif execution_success is False and self._signal_auto_run_enabled:
            display = sig_obj.display_symbol or sig_obj.symbol
            await self._send(chat_id, f"*Not Executed* \u2014 `{display}`")

    def _build_exchange_response_from_cycle(
        self,
        *,
        signal_id: str,
        symbol: str,
        cycle: dict[str, object],
        signal: dict[str, object] | None = None,
    ) -> str | None:
        """Convert a trading loop cycle result into a formatted exchange response."""
        from app.messaging.message_formatter import format_exchange_response_telegram
        from app.messaging.message_models import (
            ExchangeAction,
            ExchangeResponse,
            ResponseStatus,
        )

        cycle_status = str(cycle.get("status", ""))
        order_created = cycle.get("order_created", False)
        fill_simulated = cycle.get("fill_simulated", False)
        order_id = str(cycle.get("order_id", "")) or ""
        cycle_id = str(cycle.get("cycle_id", "")) or ""

        # Map cycle status to exchange action + response status
        if cycle_status == "completed" and fill_simulated:
            action = ExchangeAction.FILLED
            status = ResponseStatus.SUCCESS
        elif cycle_status == "completed" and order_created:
            action = ExchangeAction.ORDER_CREATED
            status = ResponseStatus.SUCCESS
        elif cycle_status == "order_failed":
            action = ExchangeAction.REJECTED
            status = ResponseStatus.ERROR
        elif cycle_status in {"risk_rejected", "size_rejected"}:
            action = ExchangeAction.REJECTED
            status = ResponseStatus.ERROR
        elif cycle_status in {"no_signal", "no_market_data", "stale_data"}:
            return None
        elif cycle_status == "error":
            action = ExchangeAction.ERROR
            status = ResponseStatus.ERROR
        else:
            return None

        notes: list[str] = []
        if isinstance(cycle.get("notes"), (list, tuple)):
            notes = [str(n) for n in cycle["notes"]]

        # Extract trade details from signal
        sig = signal or {}
        entry_price = None
        stop_loss = None
        leverage = None
        for key in ("entry_value", "entry_price"):
            val = sig.get(key)
            if val is not None:
                try:
                    entry_price = float(val)
                except (ValueError, TypeError):
                    pass
                break
        val = sig.get("stop_loss")
        if val is not None:
            try:
                stop_loss = float(val)
            except (ValueError, TypeError):
                pass
        val = sig.get("leverage")
        if val is not None:
            try:
                leverage = int(val)
            except (ValueError, TypeError):
                pass

        resp = ExchangeResponse(
            response_id=cycle_id,
            related_signal_id=signal_id,
            exchange="kai_paper",
            symbol=symbol,
            action=action,
            status=status,
            exchange_order_id=order_id,
            entry_price=entry_price,
            stop_loss=stop_loss,
            leverage=leverage,
            message="; ".join(notes[:3]) if notes else cycle_status,
        )
        return format_exchange_response_telegram(resp)

    async def _append_decision_from_signal(
        self,
        *,
        symbol: str,
        direction: str,
        reasoning: str,
        source: str,
    ) -> dict[str, object]:
        from app.agents.mcp_server import append_decision_instance

        thesis = (
            f"Telegram {source} signal: {direction} bias on {symbol}. "
            f"Reasoning: {reasoning or 'operator signal input'}"
        )
        return await append_decision_instance(
            symbol=symbol,
            thesis=thesis,
            mode=self._signal_auto_run_mode,
            confidence_score=0.65 if direction in {"bullish", "bearish"} else 0.5,
            supporting_factors=[
                "telegram_signal",
                f"source:{source}",
                f"direction:{direction}",
            ],
            data_sources_used=["telegram_operator_input"],
            model_version="telegram_signal_handoff_v1",
            prompt_version="telegram_signal_handoff_v1",
        )

    async def _run_signal_cycle(
        self,
        *,
        symbol: str,
        direction: str,
    ) -> dict[str, object]:
        from app.agents.mcp_server import run_trading_loop_once

        analysis_profile = "conservative"
        if direction == "bullish":
            analysis_profile = "bullish"
        elif direction == "bearish":
            analysis_profile = "bearish"

        return await run_trading_loop_once(
            symbol=symbol,
            mode=self._signal_auto_run_mode,
            provider=self._signal_auto_run_provider or "coingecko",
            analysis_profile=analysis_profile,
        )

    def _queue_signal_for_exchange(
        self,
        *,
        signal_id: str,
        chat_id: int,
        source: str,
        asset: str,
        symbol: str,
        direction: str,
        reasoning: str,
        structured_signal: dict[str, object] | None = None,
    ) -> None:
        record: dict[str, object] = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "event": "telegram_signal_exchange_forward_queued",
            "signal_id": signal_id,
            "chat_id": chat_id,
            "source": source,
            "asset": asset,
            "symbol": symbol,
            "direction": direction,
            "reasoning": reasoning,
            "status": "queued",
            "attempt_count": 0,
            "execution_enabled": False,
            "write_back_allowed": False,
        }
        if structured_signal:
            record["structured_signal"] = structured_signal
        self._append_jsonl(self._signal_exchange_outbox_log_path, record)

    @staticmethod
    def _normalize_signal_payload(signal: dict[str, Any]) -> tuple[str, str, str, str] | None:
        asset_raw = signal.get("asset")
        if not isinstance(asset_raw, str):
            return None
        asset = asset_raw.strip().upper().lstrip("$")
        if not asset:
            return None

        symbol = TelegramOperatorBot._asset_to_symbol(asset)
        if symbol is None:
            return None

        direction_raw = signal.get("direction", "")
        direction_key = (
            direction_raw.strip().lower()
            if isinstance(direction_raw, str)
            else "neutral"
        )
        direction = _SIGNAL_DIRECTION_MAP.get(direction_key, "neutral")

        reasoning_raw = signal.get("reasoning", "")
        reasoning = reasoning_raw.strip() if isinstance(reasoning_raw, str) else ""
        return asset, symbol, direction, reasoning

    @staticmethod
    def _asset_to_symbol(asset: str) -> str | None:
        candidate = asset.strip().upper().replace(" ", "").lstrip("$")
        if not candidate:
            return None
        if "/" in candidate:
            return candidate
        if "-" in candidate:
            parts = [part for part in candidate.split("-", 1) if part]
            if len(parts) == 2:
                return f"{parts[0]}/{parts[1]}"
        for quote in ("USDT", "USD", "EUR", "BTC", "ETH"):
            if candidate.endswith(quote) and len(candidate) > len(quote):
                base = candidate[: -len(quote)]
                if base:
                    return f"{base}/{quote}"
        return f"{candidate}/USDT"

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, object]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def _compute_idempotency_key(self, parsed_payload: Any) -> str | None:
        """Return the canonical idempotency key for a typed payload, or None."""
        from app.messaging.message_models import MessageEnvelope, SourceChannel
        try:
            env = MessageEnvelope.wrap(
                parsed_payload, source_channel=SourceChannel.TELEGRAM,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[BOT] Idempotency compute failed: %s", exc)
            return None
        return env.idempotency_key

    def _is_duplicate_envelope(
        self,
        idempotency_key: str,
        *,
        lookback: int = 500,
    ) -> bool:
        """Return True when a previous accepted envelope shares this key.

        Walks the tail of the envelope log (bounded by `lookback`) and
        checks for any prior record with stage=accepted/idempotency_gate
        and the same idempotency_key. Deliberately file-based: survives
        process restarts without an in-memory table.
        """
        path = self._message_envelope_log_path
        if not path.exists():
            return False
        try:
            with path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as exc:
            logger.warning("[BOT] Envelope log read failed: %s", exc)
            return False
        for line in reversed(lines[-lookback:]):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("idempotency_key") != idempotency_key:
                continue
            stage = rec.get("stage")
            if stage in {"accepted", "idempotency_gate"}:
                return True
        return False

    def _audit_message_envelope(
        self,
        *,
        chat_id: int,
        message_type: str,
        stage: str,
        status: str,
        source: str,
        payload: Mapping[str, object] | None = None,
        errors: list[str] | None = None,
        metadata: Mapping[str, object] | None = None,
        parsed_payload: Any | None = None,
    ) -> str | None:
        """Append one envelope audit record and return the idempotency_key, if any.

        When `parsed_payload` is a typed NEWS/SIGNAL/EXCHANGE_RESPONSE object,
        a canonical MessageEnvelope is built so the JSONL carries
        envelope_id + idempotency_key for v2 routing/de-duplication.
        Legacy raw `payload` dicts are still accepted for parse/reject stages
        where no typed payload is available.
        """
        from app.messaging.message_models import (
            MessageEnvelope,
            SourceChannel,
        )

        record: dict[str, object] = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "event": "telegram_message_envelope",
            "chat_id": chat_id,
            "message_type": message_type,
            "stage": stage,
            "status": status,
            "source": source,
            "execution_enabled": False,
            "write_back_allowed": False,
        }
        idempotency_key: str | None = None
        if parsed_payload is not None:
            try:
                channel = SourceChannel.VOICE if source == "voice" else SourceChannel.TELEGRAM
                envelope = MessageEnvelope.wrap(
                    parsed_payload,
                    source_channel=channel,
                    chat_id=chat_id,
                )
                record["envelope_id"] = envelope.envelope_id
                record["idempotency_key"] = envelope.idempotency_key
                record["payload"] = dict(envelope.payload)
                idempotency_key = envelope.idempotency_key
            except Exception as exc:  # noqa: BLE001
                logger.warning("[BOT] Envelope wrap failed (fallback to raw): %s", exc)
                if payload:
                    record["payload"] = dict(payload)
        elif payload:
            record["payload"] = dict(payload)
        if errors:
            record["errors"] = list(errors)
        if metadata:
            record["metadata"] = dict(metadata)
        try:
            self._append_jsonl(self._message_envelope_log_path, record)
        except OSError as exc:
            logger.error("[BOT] Message envelope audit write failed: %s", exc)
        return idempotency_key

    async def _dispatch(self, chat_id: int, command: str, *, args: str = "") -> None:
        self._audit(chat_id, command, args=args)
        handlers = {
            "status": self._cmd_status,
            "positions": self._cmd_positions,
            "positionen": self._cmd_positions,
            "positionspapier": self._cmd_positions,
            "exposure": self._cmd_exposure,
            "risiko": self._cmd_exposure,
            "signals": self._cmd_signals,
            "signale": self._cmd_signals,
            "signal_status": self._cmd_signal_status,
            "signalstatus": self._cmd_signal_status,
            "alert_status": self._cmd_alert_status,
            "alertstatus": self._cmd_alert_status,
            "quality": self._cmd_quality,
            "qualitaet": self._cmd_quality,
            "annotate": self._cmd_annotate,
            "approve": self._cmd_approve,
            "reject": self._cmd_reject,
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "kill": self._cmd_kill,
            "daily_summary": self._cmd_daily_summary,
            "tagesbericht": self._cmd_daily_summary,
            "signal": self._cmd_signal,
            "help": self._cmd_help,
            "hilfe": self._cmd_help,
            "start": self._cmd_menu,
            "menu": self._cmd_menu,
            "menue": self._cmd_menu,
            "menu_reload": self._cmd_menu_reload,
            "menue_reload": self._cmd_menu_reload,
            "menu_validate": self._cmd_menu_validate,
            "menue_validate": self._cmd_menu_validate,
            "sentr": self._cmd_agent_sentr,
            "watchdog": self._cmd_agent_watchdog,
            "architect": self._cmd_agent_architect,
            "ok": self._cmd_ok,
            "bestaetigen": self._cmd_ok,
            "confirm": self._cmd_ok,
            "cancel": self._cmd_cancel,
            "abbrechen": self._cmd_cancel,
            "verwerfen": self._cmd_cancel,
        }
        handler = handlers.get(command)
        if handler is None:
            await self._send(chat_id, f"Unknown command: /{command}\nUse /help for list.")
            return
        if command in _READ_ONLY_COMMANDS and self._invalid_command_refs:
            await self._send(
                chat_id,
                "*Operator Surface Misconfigured*\n"
                "Canonical command references failed validation (fail-closed).\n"
                "Please verify CLI command inventory before using Telegram read surfaces.",
            )
            return
        track = command in _EPHEMERAL_MENU_COMMANDS
        previous_track = self._track_ephemeral_reply
        self._track_ephemeral_reply = track
        try:
            await handler(chat_id, args=args)
        finally:
            self._track_ephemeral_reply = previous_track

    @staticmethod
    def _inline(value: object) -> str:
        return str(value).replace("`", "'").replace("\n", " ").strip() or "unknown"

    def _format_refs(self, command: str) -> str:
        refs = TELEGRAM_CANONICAL_COMMAND_REFS.get(command, ())
        if not refs:
            return ""
        if len(refs) == 1:
            return f"\nRef: `{refs[0]}`"
        refs_text = ", ".join(f"`{ref}`" for ref in refs)
        return f"\nRefs: {refs_text}"

    @staticmethod
    def _validate_decision_ref(value: str) -> str | None:
        candidate = value.strip()
        if not candidate:
            return None
        if not _DECISION_REF_PATTERN.fullmatch(candidate):
            return None
        return candidate

    async def _load_canonical_surface(
        self,
        *,
        chat_id: int,
        surface_name: str,
        command: str,
        loader: Callable[[], Awaitable[dict[str, object]]],
    ) -> dict[str, object] | None:
        try:
            payload = await loader()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[BOT] Canonical surface load failed for /%s (%s): %s",
                command,
                surface_name,
                exc,
            )
            await self._send(
                chat_id,
                f"*{surface_name}*\n"
                "Canonical read surface unavailable (fail-closed).\n"
                "No execution side effect was performed.",
            )
            return None
        if not isinstance(payload, dict):
            await self._send(
                chat_id,
                f"*{surface_name}*\n"
                "Invalid canonical payload (fail-closed).\n"
                "No execution side effect was performed.",
            )
            return None
        return payload

    async def _get_paper_portfolio_snapshot(self) -> dict[str, Any]:
        from app.agents.mcp_server import get_paper_portfolio_snapshot

        return await get_paper_portfolio_snapshot()

    async def _get_paper_positions_summary(self) -> dict[str, Any]:
        from app.agents.mcp_server import get_paper_positions_summary

        return await get_paper_positions_summary()

    async def _get_paper_exposure_summary(self) -> dict[str, Any]:
        from app.agents.mcp_server import get_paper_exposure_summary

        return await get_paper_exposure_summary()

    async def _get_signals_for_execution(self) -> dict[str, Any]:
        from app.agents.mcp_server import get_signals_for_execution

        return await get_signals_for_execution(limit=5)

    async def _get_alert_audit_summary(self) -> dict[str, Any]:
        from app.agents.mcp_server import get_alert_audit_summary

        return await get_alert_audit_summary()

    async def _get_daily_operator_summary(self) -> dict[str, Any]:
        from app.agents.mcp_server import get_daily_operator_summary

        return await get_daily_operator_summary()


    async def _cmd_agent(
        self,
        chat_id: int,
        slug: str,
        *,
        args: str = "",
    ) -> None:
        """Bridge Telegram -> Agent conversation (SENTR / Watchdog / Architect).

        Usage:
          /{slug}              -> last 5 events from conversation.jsonl
          /{slug} <text>       -> operator message appended to conversation
          /{slug} !<mode> [note] -> command enqueued (same as dashboard button)

        Conversation is single source of truth — dashboard and telegram see
        identical history because both write to artifacts/agents/{slug}/.
        """
        from app.api.routers.agents import (
            _AGENTS,
            _agent_dir,
            _load_conversation,
            append_conversation_event,
        )

        defn = _AGENTS.get(slug)
        if defn is None:
            await self._send(chat_id, f"Unknown agent: {slug}")
            return

        text = args.strip()

        if not text:
            events = _load_conversation(slug, tail=5, since=None)
            if not events:
                await self._send(
                    chat_id,
                    f"*{defn.name}* — noch kein Conversation-Log.\n"
                    f"Schreibe `/{slug} <text>` um zu starten.",
                )
                return
            lines = [f"*{defn.name}* — letzte {len(events)} Events"]
            for ev in events:
                ts = str(ev.get("ts", ""))[11:19]  # HH:MM:SS
                src = ev.get("source", "?")
                role = ev.get("role", "?")
                kind = ev.get("kind", "message")
                content = str(ev.get("content", "")).replace("`", "'")
                if len(content) > 180:
                    content = content[:177] + "..."
                tag = f"{src}/{role}" + (f"/{kind}" if kind != "message" else "")
                lines.append(f"`{ts}` [{tag}] {content}")
            await self._send(chat_id, "\n".join(lines))
            return

        if text.startswith("!"):
            parts = text[1:].split(maxsplit=1)
            mode = parts[0].lower()
            note = parts[1].strip() if len(parts) > 1 else None
            if mode not in defn.modes:
                await self._send(
                    chat_id,
                    f"*{defn.name}* unterstützt keinen Modus `{mode}`.\n"
                    f"Verfügbar: {', '.join(defn.modes)}",
                )
                return
            d = _agent_dir(slug)
            d.mkdir(parents=True, exist_ok=True)
            cmd_id = uuid4().hex
            entry = {
                "id": cmd_id,
                "ts": datetime.now(UTC).isoformat(),
                "agent": slug,
                "mode": mode,
                "note": note,
                "status": "queued",
            }
            with (d / "commands.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            append_conversation_event(
                slug,
                source="telegram",
                role="operator",
                content=f"[mode:{mode}] {note or ''}".strip(),
                kind="command",
                meta={"command_id": cmd_id, "mode": mode},
            )
            await self._send(
                chat_id,
                f"*{defn.name}* — Kommando `{mode}` eingereiht (`{cmd_id[:8]}`).",
            )
            return

        event = append_conversation_event(
            slug,
            source="telegram",
            role="operator",
            content=text,
            kind="message",
        )
        await self._send(
            chat_id,
            f"*{defn.name}* — Nachricht gespeichert (`{str(event['id'])[:8]}`).",
        )

    async def _cmd_agent_sentr(self, chat_id: int, *, args: str = "") -> None:
        await self._cmd_agent(chat_id, "sentr", args=args)

    async def _cmd_agent_watchdog(self, chat_id: int, *, args: str = "") -> None:
        await self._cmd_agent(chat_id, "watchdog", args=args)

    async def _cmd_agent_architect(self, chat_id: int, *, args: str = "") -> None:
        await self._cmd_agent(chat_id, "architect", args=args)

    async def _cmd_status(self, chat_id: int, *, args: str = "") -> None:
        payload = await self._load_canonical_surface(
            chat_id=chat_id,
            surface_name="Status",
            command="status",
            loader=self._get_daily_operator_summary,
        )
        if payload is None:
            return
        readiness = self._inline(payload.get("readiness_status", "unknown"))
        cycles = payload.get("cycle_count_today", 0)
        pos_count = payload.get("position_count", 0)
        backlog = self._inline(payload.get("ingestion_backlog_documents", "?"))
        alert_rate = self._inline(
            payload.get("alert_fire_rate_docs_per_hour_24h", "?")
        )
        llm_fail = self._inline(
            payload.get("llm_provider_failure_rate_24h", "?")
        )
        latency = self._inline(
            payload.get("rss_to_alert_latency_p95_seconds_24h", "?")
        )
        msg = (
            f"*KAI Status*\n"
            f"Readiness: {readiness}\n"
            f"Cycles today: {cycles} · Positions: {pos_count}\n"
            f"Ingestion backlog: {backlog} docs\n"
            f"Alert rate (24h): {alert_rate}/h\n"
            f"LLM failures (24h): {llm_fail}\n"
            f"Latency p95 (24h): {latency}s"
        )
        await self._send(chat_id, msg)

    async def _cmd_positions(self, chat_id: int, *, args: str = "") -> None:
        payload = await self._load_canonical_surface(
            chat_id=chat_id,
            surface_name="Positions",
            command="positions",
            loader=self._get_paper_positions_summary,
        )
        if payload is None:
            return
        raw_positions = payload.get("positions", [])
        positions = raw_positions if isinstance(raw_positions, list) else []
        count = payload.get("position_count", 0)
        mtm = self._inline(payload.get("mark_to_market_status", "unknown"))

        lines = [
            "*Positions*",
            "Paper portfolio · read-only",
            "",
            f"Total: {count} · Mark-to-market: {mtm}",
        ]
        if positions:
            for pos in positions[:5]:
                if not isinstance(pos, dict):
                    continue
                sym = self._inline(pos.get("symbol", "?"))
                qty = pos.get("quantity", 0)
                entry = pos.get("avg_entry_price", 0)
                pnl = pos.get("unrealized_pnl_usd")
                pnl_str = f"{pnl:+.2f} USD" if pnl is not None else "n/a"
                lines.append(f"  {sym}: {qty} @ {entry} · PnL {pnl_str}")
        else:
            lines.append("No open positions.")
        lines.append("")
        lines.append("Telegram is view-only; execution runs server-side.")
        await self._send(chat_id, "\n".join(lines))

    async def _cmd_exposure(self, chat_id: int, *, args: str = "") -> None:
        payload = await self._load_canonical_surface(
            chat_id=chat_id,
            surface_name="Exposure",
            command="exposure",
            loader=self._get_paper_exposure_summary,
        )
        if payload is None:
            return
        mtm = self._inline(payload.get("mark_to_market_status", "unknown"))
        gross = payload.get("gross_exposure_usd", 0.0)
        net = payload.get("net_exposure_usd", 0.0)
        stale = payload.get("stale_position_count", 0)
        unpriced = payload.get("unavailable_price_count", 0)
        msg = (
            f"*Exposure*\n"
            f"Paper portfolio · read-only\n"
            f"\n"
            f"Gross: {gross:.2f} USD · Net: {net:.2f} USD\n"
            f"Mark-to-market: {mtm} · Stale: {stale} · Missing price: {unpriced}"
        )
        await self._send(chat_id, msg)

    async def _cmd_signals(self, chat_id: int, *, args: str = "") -> None:
        payload = await self._load_canonical_surface(
            chat_id=chat_id,
            surface_name="Signals",
            command="signals",
            loader=self._get_signals_for_execution,
        )
        if payload is None:
            return
        signals = payload.get("signals", [])
        count = payload.get("signal_count", 0)

        lines = [
            "*Signals*",
            "Active · read-only",
            "",
            f"Count: {count}",
        ]
        if isinstance(signals, list) and signals:
            for sig in signals[:5]:
                if not isinstance(sig, dict):
                    continue
                asset = self._inline(sig.get("target_asset", "?"))
                direction = self._inline(sig.get("direction_hint", "neutral"))
                prio = sig.get("priority", "?")
                lines.append(f"  {asset} · {direction} · priority {prio}")
        else:
            lines.append("No active signals.")
        lines.append("")
        lines.append("Signals are advisory; no automatic trade.")
        msg = "\n".join(lines)
        await self._send(chat_id, msg)

    async def _cmd_signal_status(self, chat_id: int, *, args: str = "") -> None:
        try:
            from app.messaging.exchange_relay import build_signal_pipeline_status

            payload = build_signal_pipeline_status(
                handoff_log_path=self._signal_handoff_log_path,
                outbox_log_path=self._signal_exchange_outbox_log_path,
                sent_log_path=self._signal_exchange_sent_log_path,
                dead_letter_log_path=self._signal_exchange_dead_letter_log_path,
                lookback_hours=24,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[BOT] Signal status load failed: %s", exc)
            await self._send(
                chat_id,
                "*Signal Status*\n"
                "Signal pipeline status unavailable (fail-closed).\n"
                "No execution side effect was performed.",
            )
            return

        handoff = payload.get("handoff_total", 0)
        handoff_24h = payload.get("handoff_lookback", 0)
        outbox = payload.get("outbox_queued_total", 0)
        sent = payload.get("exchange_sent_total", 0)
        sent_24h = payload.get("exchange_sent_lookback", 0)
        dead = payload.get("exchange_dead_letter_total", 0)
        dead_24h = payload.get("exchange_dead_letter_lookback", 0)
        msg = (
            f"*Signal Pipeline*\n"
            f"Read-only · last 24h window\n"
            f"\n"
            f"Handoff: {handoff} total · {handoff_24h} last 24h\n"
            f"Outbox: {outbox} queued\n"
            f"Sent: {sent} total · {sent_24h} last 24h\n"
            f"Dead-letter: {dead} total · {dead_24h} last 24h"
        )
        await self._send(chat_id, msg)

    async def _cmd_alert_status(self, chat_id: int, *, args: str = "") -> None:
        payload = await self._load_canonical_surface(
            chat_id=chat_id,
            surface_name="Alert Status",
            command="alert_status",
            loader=self._get_alert_audit_summary,
        )
        if payload is None:
            return
        total = payload.get("total_count", 0)
        digest = payload.get("digest_count", 0)
        latest = self._inline(payload.get("latest_dispatched_at", "keine"))
        msg = (
            f"*Alert Status*\n"
            f"Read-only\n"
            f"\n"
            f"Total: {total} · Digest: {digest}\n"
            f"Last dispatch: {latest}"
        )
        await self._send(chat_id, msg)

    async def _cmd_quality(
        self, chat_id: int, *, args: str = "",
    ) -> None:
        """Show quality-bar metrics from hold report."""
        report_path = Path("artifacts/ph5_hold/ph5_hold_metrics_report.json")
        if not report_path.exists():
            await self._send(chat_id, "*Quality Bar*\nNo hold report available yet.")
            return
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            await self._send(chat_id, f"*Quality Bar*\nReport error: {exc}")
            return

        sq = data.get("signal_quality_validation", {})
        hr = data.get("alert_hit_rate_evidence", {})
        fwd = data.get("forward_simulation", {})
        paper = data.get("paper_trading_evidence", {})
        gate = data.get("hold_gate_evaluation", {})

        prec = sq.get("resolved_precision_pct")
        fwd_prec = fwd.get("precision_pct")
        fwd_res = fwd.get("resolved", 0)
        fwd_h = fwd.get("hits", 0)
        fwd_m = fwd.get("miss", 0)
        resolved = hr.get("resolved_directional_documents", 0)
        hits = hr.get("alert_hits", 0)
        misses = hr.get("alert_misses", 0)
        corr = sq.get("priority_hit_correlation")
        cycles = paper.get("loop_metrics", {}).get("total_cycles", 0)
        fills_count = sq.get("paper_real_price_cycle_count", 0)
        status = gate.get("overall_status", "unknown")

        prec_s = f"{prec:.1f}%" if prec is not None else "--"
        fwd_s = f"{fwd_prec:.1f}%" if fwd_prec is not None else "--"
        corr_s = f"{corr:.4f}" if corr is not None else "--"
        icon = "+" if status == "hold_releasable" else "!"

        msg = (
            f"*Quality Bar* [{icon}]\n"
            f"Gate: `{status}`\n"
            f"\n"
            f"Forward precision: {fwd_s} ({fwd_h}h / {fwd_m}m · {fwd_res} resolved)\n"
            f"Raw precision: {prec_s} ({hits}h / {misses}m · {resolved} resolved)\n"
            f"Priority/hit correlation: {corr_s} (target ≥ 0.40)\n"
            f"Paper cycles: {cycles}\n"
            f"Real-price cycles: {fills_count}\n"
            f"\n"
            f"Report: {data.get('generated_at', '?')[:16]}"
        )
        await self._send(chat_id, msg)

    async def _cmd_annotate(
        self, chat_id: int, *, args: str = "",
    ) -> None:
        """Show pending alerts for annotation or annotate directly.

        Usage:
          /annotate           -- list pending alerts with buttons
          /annotate <id> hit  -- annotate directly via text
        """
        parts = args.strip().split()
        if len(parts) >= 2:
            await self._annotate_direct(chat_id, parts[0], parts[1])
            return

        from app.alerts.audit import (
            load_alert_audits,
            load_outcome_annotations,
        )
        from app.alerts.eligibility import evaluate_directional_eligibility

        artifacts = Path("artifacts")
        records = load_alert_audits(artifacts)
        annotations = load_outcome_annotations(artifacts)
        annotated = {a.document_id for a in annotations}

        latest_by_doc: dict[str, Any] = {}
        for rec in records:
            sent = (rec.sentiment_label or "").lower()
            if rec.is_digest or sent not in {"bullish", "bearish"}:
                continue
            check = evaluate_directional_eligibility(
                sentiment_label=rec.sentiment_label,
                affected_assets=list(rec.affected_assets or []),
            )
            if check.directional_eligible is not True:
                continue
            if rec.directional_eligible is False:
                continue
            prev = latest_by_doc.get(rec.document_id)
            if prev is None or rec.dispatched_at > prev.dispatched_at:
                latest_by_doc[rec.document_id] = rec

        pending = [
            r for r in latest_by_doc.values()
            if r.document_id not in annotated
        ]
        pending.sort(key=lambda r: r.dispatched_at, reverse=True)

        if not pending:
            await self._send(
                chat_id,
                "*Annotation*\nKeine offenen Alerts. Alles annotiert!",
            )
            return

        batch = pending[:5]
        now = datetime.now(UTC)
        lines = [f"*Annotation* ({len(pending)} offen)\n"]
        buttons: list[list[dict[str, str]]] = []
        for rec in batch:
            doc_short = rec.document_id[:12]
            age_h = "--"
            try:
                ts = datetime.fromisoformat(
                    rec.dispatched_at.replace("Z", "+00:00"),
                )
                age_h = f"{(now - ts).total_seconds() / 3600:.0f}h"
            except ValueError:
                pass
            sent = rec.sentiment_label or "?"
            prio = rec.priority if rec.priority else "?"
            assets_s = ", ".join(
                rec.affected_assets[:2],
            ) if rec.affected_assets else "--"
            lines.append(
                f"`{doc_short}` {sent} P{prio} {assets_s} ({age_h})",
            )
            row = [
                {
                    "text": f"Hit {doc_short}",
                    "callback_data": f"ann:{rec.document_id}:hit",
                },
                {
                    "text": f"Miss {doc_short}",
                    "callback_data": f"ann:{rec.document_id}:miss",
                },
                {
                    "text": "?",
                    "callback_data": (
                        f"ann:{rec.document_id}:inconclusive"
                    ),
                },
            ]
            buttons.append(row)

        text = "\n".join(lines)
        keyboard = json.dumps({"inline_keyboard": buttons})
        await self._send_with_keyboard(chat_id, text, keyboard)

    async def _send_with_keyboard(
        self,
        chat_id: int,
        text: str,
        reply_markup: str,
    ) -> bool:
        """Send a message with an inline keyboard."""
        if self._dry_run:
            logger.info(
                "[BOT DRY RUN] Keyboard to %s: %s", chat_id, text[:80],
            )
            return True
        if not self._token:
            return False
        url = (
            f"{_TELEGRAM_API_BASE}/bot{self._token}/sendMessage"
        )
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
            "reply_markup": reply_markup,
        }
        return await self._send_payload_with_retry(url, payload)

    async def _annotate_direct(
        self, chat_id: int, doc_id: str, outcome: str,
    ) -> None:
        """Annotate a document directly via text command."""
        normalized = outcome.strip().lower()
        if normalized not in _VALID_OUTCOMES:
            await self._send(
                chat_id,
                "*Annotation*\n"
                f"Ungueltiges Outcome: `{outcome}`\n"
                "Erlaubt: hit, miss, inconclusive",
            )
            return
        from app.alerts.audit import (
            AlertOutcomeAnnotation,
            append_outcome_annotation,
        )

        annotation = AlertOutcomeAnnotation(
            document_id=doc_id,
            outcome=normalized,  # type: ignore[arg-type]
            note="via Telegram",
        )
        try:
            append_outcome_annotation(annotation, Path("artifacts"))
        except OSError as exc:
            await self._send(
                chat_id,
                f"*Annotation*\nSchreibfehler: {exc}",
            )
            return
        await self._send(
            chat_id,
            f"*Annotation gespeichert*\n"
            f"`{doc_id[:12]}` -> {normalized}",
        )

    async def _cmd_approve(self, chat_id: int, *, args: str = "") -> None:
        decision_ref = self._validate_decision_ref(args)
        if decision_ref is None:
            await self._send(
                chat_id,
                "*Approval Journal*\n"
                "Invalid or missing `decision_ref`.\n"
                "Expected format: `dec_<12 lowercase hex>`.\n"
                "Fail-closed: approval intent rejected.\n"
                "Audit-only. No execution side effect occurs."
                f"{self._format_refs('approve')}",
            )
            return
        await self._send(
            chat_id,
            "*Approval Journal*\n"
            f"decision_ref=`{decision_ref}`\n"
            "Approval intent is recorded in append-only command audit log only.\n"
            "Audit-only. No execution side effect occurs."
            f"{self._format_refs('approve')}",
        )

    async def _cmd_reject(self, chat_id: int, *, args: str = "") -> None:
        decision_ref = self._validate_decision_ref(args)
        if decision_ref is None:
            await self._send(
                chat_id,
                "*Rejection Journal*\n"
                "Invalid or missing `decision_ref`.\n"
                "Expected format: `dec_<12 lowercase hex>`.\n"
                "Fail-closed: rejection intent rejected.\n"
                "Audit-only. No execution side effect occurs."
                f"{self._format_refs('reject')}",
            )
            return
        await self._send(
            chat_id,
            "*Rejection Journal*\n"
            f"decision_ref=`{decision_ref}`\n"
            "Rejection intent is recorded in append-only command audit log only.\n"
            "Audit-only. No execution side effect occurs."
            f"{self._format_refs('reject')}",
        )

    async def _cmd_pause(self, chat_id: int, *, args: str = "") -> None:
        if self._dry_run:
            await self._send(chat_id, "[DRY RUN] Would pause system. No action taken.")
            return
        if self._risk_engine:
            self._risk_engine.pause()
        self._system_status = "paused"
        await self._send(chat_id, "*System PAUSED*. Use /resume to restart.")

    async def _cmd_resume(self, chat_id: int, *, args: str = "") -> None:
        if self._dry_run:
            await self._send(chat_id, "[DRY RUN] Would resume system. No action taken.")
            return
        if self._risk_engine:
            self._risk_engine.resume()
        self._system_status = "operational"
        await self._send(chat_id, "*System RESUMED*.")

    async def _cmd_kill(self, chat_id: int, *, args: str = "") -> None:
        if self._pending_confirm.get(chat_id) == "kill":
            self._pending_confirm.pop(chat_id)
            if self._dry_run:
                await self._send(
                    chat_id,
                    "[DRY RUN] Would activate kill switch. No action taken.",
                )
                return
            if self._risk_engine:
                self._risk_engine.trigger_kill_switch()
            self._system_status = "killed"
            await self._send(
                chat_id,
                "*KILL SWITCH ACTIVATED*. All operations halted. Manual reset required.",
            )
            return

        self._pending_confirm[chat_id] = "kill"
        await self._send(
            chat_id,
            "*Confirm KILL*\nSend /kill again to confirm emergency stop.\n"
            "This halts ALL operations immediately.",
        )

    async def _cmd_daily_summary(self, chat_id: int, *, args: str = "") -> None:
        payload = await self._load_canonical_surface(
            chat_id=chat_id,
            surface_name="Daily Summary",
            command="daily_summary",
            loader=self._get_daily_operator_summary,
        )
        if payload is None:
            return
        readiness = self._inline(payload.get("readiness_status", "?"))
        cycles = payload.get("cycle_count_today", 0)
        pos_count = payload.get("position_count", 0)
        exposure = payload.get("total_exposure_pct", 0.0)
        backlog = self._inline(payload.get("ingestion_backlog_documents", "?"))
        dir_alerts = self._inline(
            payload.get("directional_alert_documents_24h", "?")
        )
        alert_rate = self._inline(
            payload.get("alert_fire_rate_docs_per_hour_24h", "?")
        )
        latency = self._inline(
            payload.get("rss_to_alert_latency_p50_seconds_24h", "?")
        )
        llm_fail = self._inline(
            payload.get("llm_provider_failure_rate_24h", "?")
        )
        decision_status = self._inline(
            payload.get("decision_pack_status", "?")
        )
        incidents = payload.get("open_incidents", 0)
        msg = (
            f"*Daily Report*\n"
            f"Readiness: {readiness}\n"
            f"Cycles: {cycles} · Positions: {pos_count}\n"
            f"Exposure: {exposure}%\n"
            f"Ingestion backlog: {backlog} · Directional alerts (24h): {dir_alerts}\n"
            f"Alert rate: {alert_rate}/h · Latency p50: {latency}s\n"
            f"LLM failures: {llm_fail}\n"
            f"Decision pack: {decision_status}\n"
            f"Open incidents: {incidents}"
        )
        await self._send(chat_id, msg)

    async def _cmd_signal(self, chat_id: int, *, args: str = "") -> None:
        """Process a structured trading signal from Telegram."""
        from app.messaging.signal_parser import SignalParseError, parse_signal_message

        if not args.strip():
            await self._send(
                chat_id,
                "*Signal Format*\n"
                "Preferred — structured block:\n"
                "`[SIGNAL]`\n"
                "`Signal ID: SIG-20260415-BTCUSDT-001`\n"
                "`Source: Premium Signals`\n"
                "`Exchange Scope: binance_futures, bybit`\n"
                "`Market Type: Futures`\n"
                "`Symbol: BTC/USDT`\n"
                "`Side: SELL`\n"
                "`Direction: SHORT`\n"
                "`Entry Rule: BELOW 74700`\n"
                "`Targets: 72800`\n"
                "`Stop Loss: 76600`\n"
                "`Leverage: 10x`\n"
                "`Status: NEW`\n"
                "`Timestamp: 2026-04-15T10:00:00Z`\n"
                "\n"
                "Legacy short form (still supported):\n"
                "`/signal BUY BTC 65000 SL=62000 TP=70000`\n"
                "`/signal SELL ETH 3400`\n"
                "`/signal LONG SOL SL=120 TP=200 SIZE=0.5`",
            )
            return

        try:
            signal = parse_signal_message(args)
        except SignalParseError as exc:
            await self._send(chat_id, f"*Signal error:* {exc}")
            return

        self._audit(chat_id, "_signal_parsed", args=json.dumps({
            "direction": signal.direction,
            "asset": signal.asset,
            "price": signal.price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "size": signal.size,
        }))

        price_line = f"Price: `{signal.price}`" if signal.price else "Price: `Market`"
        sl_line = f"Stop-Loss: `{signal.stop_loss}`" if signal.stop_loss else ""
        tp_line = f"Take-Profit: `{signal.take_profit}`" if signal.take_profit else ""
        size_line = f"Size: `{signal.size}`" if signal.size else ""

        lines = [
            "*Signal Received*",
            "Structured · audit-only",
            "",
            f"Direction: `{signal.direction.upper()}`",
            f"Asset: `{signal.asset}`",
            price_line,
        ]
        for extra in [sl_line, tp_line, size_line]:
            if extra:
                lines.append(extra)
        lines.append("")
        lines.append("No order dispatched.")

        await self._send(chat_id, "\n".join(lines))

    async def _cmd_help(self, chat_id: int, *, args: str = "") -> None:
        msg = (
            "*KAI Help & Support*\n"
            "\n"
            "*Read-only views*\n"
            "/status — system status\n"
            "/positions — paper positions\n"
            "/exposure — paper exposure and risk\n"
            "/signals — active signals\n"
            "/signalstatus — signal pipeline\n"
            "/tagesbericht — daily report\n"
            "/alertstatus — alert delivery status\n"
            "/quality — quality-bar metrics\n"
            "/annotate — annotate alerts\n"
            "\n"
            "*Actions*\n"
            "/signal BUY BTC 65000 — submit a trading signal\n"
            "/approve dec\\_xxx — approve a decision\n"
            "/reject dec\\_xxx — reject a decision\n"
            "/pause — pause the system\n"
            "/resume — resume the system\n"
            "/kill — emergency stop\n"
            "\n"
            "*Message types*\n"
            "[NEWS] — information only, never triggers execution\n"
            "[SIGNAL] — structured trade instruction, schema-validated\n"
            "[EXCHANGE_RESPONSE] — execution status update\n"
            "\n"
            "Telegram is view-only; the JSON envelope is the source of truth. "
            "SIGNAL entries without required fields fail closed.\n"
            "\n"
            "*Navigation*\n"
            "/menu — open the main menu\n"
            "/menu\\_reload — reload menu config\n"
            "/menu\\_validate — validate menu config\n"
            "/hilfe — show this help"
        )
        await self._send(chat_id, msg)

    async def _cmd_menu(self, chat_id: int, *, args: str = "") -> None:
        """Show the main inline menu. Also re-docks the persistent keyboard.

        The preceding _send attaches the persistent reply-keyboard under the
        text input. The inline menu card follows so the operator can drill
        into sub-menus. Callback handler posts sub-menus as **new** messages
        at the bottom of the chat (never editing in place) so the active
        selection is always at the end of the scroll.
        """
        await self._send(chat_id, "_Navigation ready._")
        await self._send_menu(chat_id, "main")

    async def _cmd_menu_reload(self, chat_id: int, *, args: str = "") -> None:
        """Clear menu cache and reload menu config from disk."""
        from app.messaging.telegram_menu import clear_menu_cache

        clear_menu_cache()
        await self._send(
            chat_id,
            "*Menu reloaded*\n"
            "Configuration re-read from disk.",
        )

    async def _cmd_menu_validate(self, chat_id: int, *, args: str = "") -> None:
        """Validate menu config file and report diagnostics."""
        from app.messaging.telegram_menu import validate_menu_config

        validation = validate_menu_config()
        status = "OK" if validation.get("is_valid") else "ERROR"
        source = self._inline(validation.get("source", "unknown"))
        menu_count = self._inline(validation.get("menu_count", 0))
        error_count = self._inline(validation.get("error_count", 0))
        warning_count = self._inline(validation.get("warning_count", 0))
        path = self._inline(validation.get("path", "unknown"))

        lines = [
            "*Menu Validation*",
            f"Status: `{status}`",
            f"Source: `{source}`",
            f"Menus: `{menu_count}`",
            f"Warnings: `{warning_count}` · Errors: `{error_count}`",
            f"Path: `{path}`",
        ]

        warnings_raw = validation.get("warnings", [])
        warnings = warnings_raw if isinstance(warnings_raw, list) else []
        if warnings:
            lines.append("")
            lines.append("*Warnings*")
            for warning in warnings[:5]:
                lines.append(f"- {self._inline(warning)}")
            if len(warnings) > 5:
                lines.append(f"- … +{len(warnings) - 5} more")

        errors_raw = validation.get("errors", [])
        errors = errors_raw if isinstance(errors_raw, list) else []
        if errors:
            lines.append("")
            lines.append("*Errors*")
            for error in errors[:5]:
                lines.append(f"- {self._inline(error)}")
            if len(errors) > 5:
                lines.append(f"- … +{len(errors) - 5} more")

        await self._send(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # Inline keyboard menu system
    # ------------------------------------------------------------------

    async def bootstrap_bot_menu(self) -> bool:
        """Register the curated slash-command list with Telegram.

        Idempotent; safe to call on every startup. Registers setMyCommands
        so the Telegram UI shows a typeable list. setChatMenuButton flips
        the left-of-input button to "commands" (default), ensuring the list
        is always one tap away.

        Skipped in dry-run and when no token is configured.
        """
        if self._dry_run or not self._token:
            return False

        commands = [
            {"command": "start", "description": "Main Menu öffnen"},
        ]

        # Telegram resolves commands by most-specific scope. A leftover
        # registration in `all_private_chats` or a per-chat scope overrides
        # `default`, so we must clear those scopes explicitly and re-set the
        # reduced list at every scope the operator may actually see.
        scopes: list[dict[str, Any]] = [
            {"type": "default"},
            {"type": "all_private_chats"},
            {"type": "all_group_chats"},
            {"type": "all_chat_administrators"},
        ]
        for chat_id in self._admin_ids:
            scopes.append({"type": "chat", "chat_id": chat_id})

        ok_cmds = True
        for scope in scopes:
            ok_delete = await self._send_payload_with_retry(
                f"{_TELEGRAM_API_BASE}/bot{self._token}/deleteMyCommands",
                {"scope": scope},
            )
            ok_set = await self._send_payload_with_retry(
                f"{_TELEGRAM_API_BASE}/bot{self._token}/setMyCommands",
                {"commands": commands, "scope": scope},
            )
            if not (ok_delete and ok_set):
                logger.warning("[BOT] Command scope sync incomplete: %s", scope)
                ok_cmds = False
        if self._dashboard_url:
            menu_button: dict[str, Any] = {
                "type": "web_app",
                "text": "KAI",
                "web_app": {"url": self._dashboard_url},
            }
        else:
            menu_button = {"type": "commands"}
        ok_btn = await self._send_payload_with_retry(
            f"{_TELEGRAM_API_BASE}/bot{self._token}/setChatMenuButton",
            {"menu_button": menu_button},
        )
        if ok_cmds and ok_btn:
            logger.info("[BOT] Telegram bot menu bootstrapped (%d commands)", len(commands))
        else:
            logger.warning(
                "[BOT] Bot menu bootstrap incomplete (commands=%s, button=%s)",
                ok_cmds, ok_btn,
            )
        return ok_cmds and ok_btn

    async def _send_menu(
        self,
        chat_id: int,
        menu_id: str,
        *,
        message_id: int | None = None,
    ) -> bool:
        """Send or edit a menu message with inline keyboard buttons."""
        from app.messaging.telegram_menu import build_inline_keyboard, get_menu

        menu = get_menu(menu_id)
        if menu is None:
            logger.warning("[BOT] Unknown menu: %s", menu_id)
            return False

        keyboard = build_inline_keyboard(menu_id)
        if self._dry_run:
            logger.info("[BOT DRY RUN] Menu %s to %s", menu_id, chat_id)
            return True
        if not self._token:
            return False

        if message_id:
            # Edit existing message to show new menu (no flicker)
            url = f"{_TELEGRAM_API_BASE}/bot{self._token}/editMessageText"
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": menu["text"],
                "parse_mode": "Markdown",
                "reply_markup": keyboard,
            }
        else:
            # Send new message with menu
            url = f"{_TELEGRAM_API_BASE}/bot{self._token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": menu["text"],
                "parse_mode": "Markdown",
                "reply_markup": keyboard,
            }

        return await self._send_payload_with_retry(url, payload)

    async def _handle_callback_query(self, callback_query: dict[str, Any]) -> None:
        """Handle inline keyboard button presses."""
        query_id = callback_query.get("id", "")
        data = callback_query.get("data", "")
        user = callback_query.get("from", {})
        chat_id = user.get("id")

        if not chat_id or not data:
            await self._answer_callback_query(query_id)
            return

        # Auth gate
        if chat_id not in self._admin_ids:
            await self._answer_callback_query(query_id, text="Nicht autorisiert.")
            return

        self._audit(chat_id, "_callback", args=data)

        if data.startswith("menu:"):
            menu_id = data[5:]
            # Always post the sub-menu as a new message at the bottom of the
            # chat. Editing the source message in place would hide the user's
            # selection path — keeping each step as a new card preserves the
            # trail and puts the active choice at the end of the scroll.
            await self._send_menu(chat_id, menu_id, message_id=None)
            await self._answer_callback_query(query_id)
        elif data.startswith("cmd:"):
            payload = data[4:].strip()
            await self._answer_callback_query(query_id)
            if not payload:
                return
            parts = payload.split(maxsplit=1)
            command = parts[0]
            cmd_args = parts[1] if len(parts) > 1 else ""
            await self._dispatch(chat_id, command, args=cmd_args)
        elif data.startswith("ann:"):
            await self._handle_annotation_callback(
                chat_id, query_id, data,
            )
        else:
            await self._answer_callback_query(query_id, text="Unbekannte Aktion.")

    async def _answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
    ) -> bool:
        """Acknowledge a callback query (removes loading indicator)."""
        if self._dry_run or not self._token:
            return True
        url = f"{_TELEGRAM_API_BASE}/bot{self._token}/answerCallbackQuery"
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return await self._send_payload_with_retry(url, payload)

    async def _handle_annotation_callback(
        self, chat_id: int, query_id: str, data: str,
    ) -> None:
        """Process ann:<doc_id>:<outcome> callback from inline button."""
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[2] not in _VALID_OUTCOMES:
            await self._answer_callback_query(
                query_id, text="Ungueltiges Format.",
            )
            return
        doc_id = parts[1]
        outcome = parts[2]
        await self._answer_callback_query(query_id, text=f"{outcome}!")
        await self._annotate_direct(chat_id, doc_id, outcome)

    async def _send(self, chat_id: int, text: str) -> bool:
        from app.messaging.telegram_persistent_keyboard import PERSISTENT_KEYBOARD

        if self._dry_run:
            logger.info("[BOT DRY RUN] To %s: %s", chat_id, text[:100])
            return True
        if not self._token:
            return False

        track = self._track_ephemeral_reply
        url = f"{_TELEGRAM_API_BASE}/bot{self._token}/sendMessage"
        chunks = list(_split_telegram_text(text))
        last_message_id: int | None = None
        for idx, chunk in enumerate(chunks, start=1):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
                "reply_markup": PERSISTENT_KEYBOARD,
            }
            response_json = await self._send_payload_capture_response(url, payload)
            if response_json is None:
                # Markdown may have been rejected by Telegram (e.g. unescaped
                # underscores).  Fall back to plain text so the operator always
                # receives the response even if formatting is lost.
                plain_payload = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                    "reply_markup": PERSISTENT_KEYBOARD,
                }
                response_json = await self._send_payload_capture_response(url, plain_payload)
                if response_json is None:
                    logger.error("[BOT] Send failed to %s at chunk %s", chat_id, idx)
                    return False
                logger.info("[BOT] Markdown fallback to plain text for chunk %s", idx)
            message_id = (
                response_json.get("result", {}).get("message_id")
                if isinstance(response_json, dict)
                else None
            )
            if isinstance(message_id, int):
                last_message_id = message_id
        if track and last_message_id is not None:
            await self._track_and_prune_ephemeral(chat_id, last_message_id)
        return True

    async def _track_and_prune_ephemeral(self, chat_id: int, message_id: int) -> None:
        """Append message_id to the per-chat ring buffer; delete oldest on overflow."""
        history = self._menu_history.setdefault(
            chat_id, deque(maxlen=_EPHEMERAL_MENU_HISTORY_DEPTH)
        )
        evicted: int | None = None
        if len(history) == history.maxlen:
            evicted = history[0]
        history.append(message_id)
        if evicted is not None:
            await self._delete_message(chat_id, evicted)

    async def _delete_message(self, chat_id: int, message_id: int) -> bool:
        """Best-effort delete of an old menu output. Never raises."""
        if self._dry_run or not self._token:
            return False
        url = f"{_TELEGRAM_API_BASE}/bot{self._token}/deleteMessage"
        payload = {"chat_id": chat_id, "message_id": message_id}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=payload)
            if response.status_code == 200:
                return True
            logger.info(
                "[BOT] deleteMessage skipped chat=%s mid=%s status=%s",
                chat_id, message_id, response.status_code,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("[BOT] deleteMessage error chat=%s mid=%s: %s", chat_id, message_id, exc)
        return False

    async def _send_payload_with_retry(
        self,
        url: str,
        payload: dict[str, Any],
    ) -> bool:
        response_json = await self._send_payload_capture_response(url, payload)
        return response_json is not None

    async def _send_payload_capture_response(
        self,
        url: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        for attempt in range(1, _TELEGRAM_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(url, json=payload)
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError:
                        return {}
                if response.status_code == 429 and attempt < _TELEGRAM_MAX_RETRIES:
                    retry_after = _extract_retry_after_seconds(response)
                    await asyncio.sleep(min(retry_after, _TELEGRAM_MAX_RETRY_SLEEP_SECONDS))
                    continue
                logger.error(
                    "[BOT] Telegram HTTP %s: %s",
                    response.status_code,
                    response.text[:200],
                )
                return None
            except Exception as exc:  # noqa: BLE001
                if attempt < _TELEGRAM_MAX_RETRIES:
                    await asyncio.sleep(1)
                    continue
                logger.error("[BOT] Send failed: %s", exc)
                return None
        return None

    def _audit(self, chat_id: int, command: str, *, args: str = "") -> None:
        record = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "chat_id": chat_id,
            "command": command,
            "args": args,
            "dry_run": self._dry_run,
        }
        try:
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.error("[BOT] Audit log write failed: %s", exc)


class TelegramPoller:
    """Long-polling runner for TelegramOperatorBot.

    Calls Telegram getUpdates in a loop and dispatches each update
    to the bot's process_update method. Runs as an asyncio background task.

    Usage (in FastAPI lifespan):
        poller = TelegramPoller(bot)
        poller.start()
        ...
        poller.stop()
    """

    def __init__(
        self,
        bot: TelegramOperatorBot,
        poll_interval: float = 1.0,
        long_poll_timeout: int = 20,
    ) -> None:
        self._bot = bot
        self._poll_interval = poll_interval
        self._long_poll_timeout = long_poll_timeout
        self._offset: int = 0
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if not self._bot.is_configured:
            logger.warning("[POLLER] Bot not configured (missing token or admin IDs). Skipping.")
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[POLLER] Telegram polling started")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[POLLER] Telegram polling stopped")

    async def _poll_loop(self) -> None:
        url = f"{_TELEGRAM_API_BASE}/bot{self._bot._token}/getUpdates"
        try:
            await self._bot.bootstrap_bot_menu()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[POLLER] Bot menu bootstrap failed: %s", exc)
        async with httpx.AsyncClient(timeout=self._long_poll_timeout + 10) as client:
            while self._running:
                try:
                    resp = await client.post(
                        url,
                        json={
                            "offset": self._offset,
                            "timeout": self._long_poll_timeout,
                            "allowed_updates": [
                                "message",
                                "edited_message",
                                "callback_query",
                            ],
                        },
                    )
                    if resp.status_code != 200:
                        if resp.status_code == 429:
                            retry_after = _extract_retry_after_seconds(resp)
                            await asyncio.sleep(
                                min(retry_after, _TELEGRAM_MAX_RETRY_SLEEP_SECONDS)
                            )
                            continue
                        logger.error(
                            "[POLLER] Telegram HTTP %s: %s",
                            resp.status_code,
                            resp.text[:200],
                        )
                        await asyncio.sleep(self._poll_interval * 3)
                        continue

                    payload = resp.json()
                    updates = payload.get("result", []) if isinstance(payload, dict) else []
                    if not isinstance(updates, list):
                        logger.error("[POLLER] Invalid getUpdates payload shape")
                        await asyncio.sleep(self._poll_interval * 3)
                        continue
                    for update in updates:
                        update_id = update.get("update_id", 0)
                        try:
                            await self._bot.process_update(update)
                        except Exception as exc:  # noqa: BLE001
                            logger.error("[POLLER] Update %s failed: %s", update_id, exc)
                        self._offset = update_id + 1
                except asyncio.CancelledError:
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.error("[POLLER] Poll error: %s", exc)
                    await asyncio.sleep(self._poll_interval * 3)


def _split_telegram_text(text: str) -> list[str]:
    if len(text) <= _TELEGRAM_MAX_TEXT_LEN:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > _TELEGRAM_MAX_TEXT_LEN:
        cut = rest.rfind("\n", 0, _TELEGRAM_MAX_TEXT_LEN)
        if cut < int(_TELEGRAM_MAX_TEXT_LEN * 0.6):
            cut = _TELEGRAM_MAX_TEXT_LEN
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if rest:
        chunks.append(rest)
    return chunks


def _extract_retry_after_seconds(response: httpx.Response) -> int:
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        return 1
    if isinstance(payload, dict):
        params = payload.get("parameters")
        if isinstance(params, dict):
            retry_after = params.get("retry_after")
            if isinstance(retry_after, int) and retry_after > 0:
                return retry_after
    return 1
