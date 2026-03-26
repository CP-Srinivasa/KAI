"""Telegram operator bot for safe runtime control of KAI.

Handles:
- /status
- /positions
- /exposure
- /signals
- /daily_summary
- /alert_status
- /approve
- /reject
- /pause
- /resume
- /kill

This bot is separate from outbound alert delivery. It is the inbound operator
channel and remains fail-closed, admin-gated, and dry-run-safe by default.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
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
    }
)
_GUARDED_AUDIT_COMMANDS = frozenset({"approve", "reject"})
_DECISION_REF_PATTERN = re.compile(r"^dec_[0-9a-f]{12}$")
_WEBHOOK_ALLOWED_UPDATES_DEFAULT = ("message", "edited_message")
_WEBHOOK_MAX_BODY_BYTES_DEFAULT = 64_000
_WEBHOOK_MAX_SEEN_UPDATE_IDS_DEFAULT = 2_048
_WEBHOOK_REJECTION_AUDIT_LOG_DEFAULT = "artifacts/telegram_webhook_rejections.jsonl"
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
        signal_append_decision_enabled: bool = False,
        signal_auto_run_enabled: bool = False,
        signal_auto_run_mode: str = "paper",
        signal_auto_run_provider: str = "coingecko",
        signal_forward_to_exchange_enabled: bool = False,
        signal_exchange_sent_log_path: str = "artifacts/telegram_exchange_sent.jsonl",
        signal_exchange_dead_letter_log_path: str = "artifacts/telegram_exchange_dead_letter.jsonl",
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
        self._audit_path = Path(audit_log_path)
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._signal_handoff_log_path = Path(signal_handoff_log_path)
        self._signal_handoff_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._signal_exchange_outbox_log_path = Path(signal_exchange_outbox_log_path)
        self._signal_exchange_outbox_log_path.parent.mkdir(parents=True, exist_ok=True)
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
        self._system_status = "operational"
        self._invalid_command_refs = self._collect_invalid_command_refs()

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
        """Process a single Telegram update (commands, free text, voice)."""
        try:
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
            else:
                await self._handle_text(chat_id, text, source="text")
        except Exception as exc:  # noqa: BLE001
            logger.error("[BOT] Error processing update: %s", exc)

    async def _handle_text(self, chat_id: int, text: str, *, source: str = "text") -> None:
        """Process free-text messages via LLM intent classification."""
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
            await self._handle_signal_input(
                chat_id=chat_id,
                signal=result.signal,
                source=source,
                response=result.response,
            )
            return

        # Query or chat Ã¢â€ â€™ direct response
        await self._send(chat_id, result.response)

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
            await self._send(
                chat_id,
                "Signal konnte nicht normalisiert werden. "
                "Bitte Asset und Richtung klar angeben (z. B. BTC bullish).",
            )
            return

        asset, symbol, direction, reasoning = normalized
        self._audit(chat_id, "_signal_input", args=json.dumps(signal))

        signal_id = f"sig_{uuid4().hex[:12]}"
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

        msg_lines = [
            "*Signal empfangen*",
            f"Asset: `{asset}`",
            f"Symbol: `{symbol}`",
            f"Richtung: `{direction}`",
        ]
        if reasoning:
            msg_lines.append(f"Begruendung: {reasoning}")

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
                msg_lines.append(f"Decision-Journal: `ok ({decision_id})`")
            except Exception as exc:  # noqa: BLE001
                logger.error("[BOT] Signal decision append failed: %s", exc)
                handoff_record["decision_append_status"] = "failed"
                handoff_record["decision_error"] = str(exc)
                msg_lines.append("Decision-Journal: `failed`")
        else:
            handoff_record["decision_append_status"] = "disabled"
            msg_lines.append("Decision-Journal: `disabled`")

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
                msg_lines.append(f"KAI-Run: `ok ({cycle_status})`")
            except Exception as exc:  # noqa: BLE001
                logger.error("[BOT] Signal auto-run failed: %s", exc)
                handoff_record["signal_auto_run_status"] = "failed"
                handoff_record["signal_auto_run_error"] = str(exc)
                msg_lines.append("KAI-Run: `failed`")
        else:
            handoff_record["signal_auto_run_status"] = "disabled"
            msg_lines.append("KAI-Run: `disabled`")

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
                )
                handoff_record["exchange_forward_status"] = "queued"
                msg_lines.append("Exchange-Forward: `queued`")
            except OSError as exc:
                logger.error("[BOT] Exchange outbox queue failed: %s", exc)
                handoff_record["exchange_forward_status"] = "failed"
                handoff_record["exchange_forward_error"] = str(exc)
                msg_lines.append("Exchange-Forward: `failed`")
        else:
            handoff_record["exchange_forward_status"] = "disabled"
            msg_lines.append("Exchange-Forward: `disabled`")

        try:
            self._append_jsonl(self._signal_handoff_log_path, handoff_record)
        except OSError as exc:
            logger.error("[BOT] Signal handoff audit write failed: %s", exc)
            msg_lines.append("Signal-Handoff-Log: `failed`")

        if response:
            msg_lines.append("")
            msg_lines.append(response)
        await self._send(chat_id, "\n".join(msg_lines))

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
    ) -> None:
        record = {
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
        await handler(chat_id, args=args)

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
            f"Zyklen heute: {cycles} | Positionen: {pos_count}\n"
            f"Backlog: {backlog} Docs\n"
            f"Alert-Rate (24h): {alert_rate}/h\n"
            f"LLM-Fehler (24h): {llm_fail}\n"
            f"Latenz p95 (24h): {latency}s"
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

        lines = ["*Positions* (Paper, read-only)", f"Anzahl: {count} | MtM: {mtm}"]
        if positions:
            for pos in positions[:5]:
                if not isinstance(pos, dict):
                    continue
                sym = self._inline(pos.get("symbol", "?"))
                qty = pos.get("quantity", 0)
                entry = pos.get("avg_entry_price", 0)
                pnl = pos.get("unrealized_pnl_usd")
                pnl_str = f"{pnl:+.2f} USD" if pnl is not None else "n/a"
                lines.append(f"  {sym}: {qty} @ {entry} | PnL: {pnl_str}")
        else:
            lines.append("Keine offenen Positionen.")
        lines.append("Nur Lesezugriff via Telegram.")
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
            f"*Exposure* (Paper, read-only)\n"
            f"Brutto: {gross:.2f} USD | Netto: {net:.2f} USD\n"
            f"MtM: {mtm} | Stale: {stale} | Ohne Preis: {unpriced}\n"
            f"Nur Lesezugriff via Telegram."
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

        lines = ["*Signale* (read-only)", f"Anzahl: {count}"]
        if isinstance(signals, list) and signals:
            for sig in signals[:5]:
                if not isinstance(sig, dict):
                    continue
                asset = self._inline(sig.get("target_asset", "?"))
                direction = self._inline(sig.get("direction_hint", "neutral"))
                prio = sig.get("priority", "?")
                lines.append(f"  {asset} | {direction} | Prio: {prio}")
        else:
            lines.append("Keine aktiven Signale.")
        lines.append("Signale sind nur beratend. Kein automatischer Trade.")
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
            f"*Signal Status* (read-only)\n"
            f"Handoff: {handoff} gesamt, {handoff_24h} letzte 24h\n"
            f"Outbox: {outbox} wartend\n"
            f"Gesendet: {sent} gesamt, {sent_24h} letzte 24h\n"
            f"Fehlgeschlagen: {dead} gesamt, {dead_24h} letzte 24h"
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
            f"*Alert Status* (read-only)\n"
            f"Gesamt: {total} | Digest: {digest}\n"
            f"Letzter Versand: {latest}"
        )
        await self._send(chat_id, msg)

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
            f"*Tagesbericht*\n"
            f"Readiness: {readiness}\n"
            f"Zyklen: {cycles} | Positionen: {pos_count}\n"
            f"Exposure: {exposure}%\n"
            f"Backlog: {backlog} | Alerts (24h): {dir_alerts}\n"
            f"Alert-Rate: {alert_rate}/h | Latenz p50: {latency}s\n"
            f"LLM-Fehler: {llm_fail}\n"
            f"Entscheidungen: {decision_status}\n"
            f"Offene Vorfaelle: {incidents}"
        )
        await self._send(chat_id, msg)

    async def _cmd_signal(self, chat_id: int, *, args: str = "") -> None:
        """Process a structured trading signal from Telegram."""
        from app.messaging.signal_parser import SignalParseError, parse_signal_message

        if not args.strip():
            await self._send(
                chat_id,
                "*Signal-Format:*\n"
                "`/signal BUY BTC 65000 SL=62000 TP=70000`\n"
                "`/signal SELL ETH 3400`\n"
                "`/signal LONG SOL SL=120 TP=200 SIZE=0.5`\n\n"
                "Richtung: BUY/SELL/LONG/SHORT/KAUFEN/VERKAUFEN",
            )
            return

        try:
            signal = parse_signal_message(args)
        except SignalParseError as exc:
            await self._send(chat_id, f"*Signal-Fehler:* {exc}")
            return

        self._audit(chat_id, "_signal_parsed", args=json.dumps({
            "direction": signal.direction,
            "asset": signal.asset,
            "price": signal.price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "size": signal.size,
        }))

        price_line = f"Preis: `{signal.price}`" if signal.price else "Preis: `Market`"
        sl_line = f"Stop-Loss: `{signal.stop_loss}`" if signal.stop_loss else ""
        tp_line = f"Take-Profit: `{signal.take_profit}`" if signal.take_profit else ""
        size_line = f"Size: `{signal.size}`" if signal.size else ""

        lines = [
            "*Signal empfangen (structured)*\n",
            f"Richtung: `{signal.direction.upper()}`",
            f"Asset: `{signal.asset}`",
            price_line,
        ]
        for extra in [sl_line, tp_line, size_line]:
            if extra:
                lines.append(extra)
        lines.append("\nAudit-only. Kein Trade ausgefuehrt.")

        await self._send(chat_id, "\n".join(lines))

    async def _cmd_help(self, chat_id: int, *, args: str = "") -> None:
        msg = (
            "*KAI Operator Commands*\n\n"
            "*Uebersicht:*\n"
            "/status - KAI Status\n"
            "/positions - Paper-Positionen\n"
            "/exposure - Paper-Exposure/Risiko\n"
            "/signals - Aktive Signale\n"
            "/signalstatus - Signal-Pipeline\n"
            "/tagesbericht - Tagesbericht\n"
            "/alertstatus - Alert-Status\n\n"
            "*Aktionen:*\n"
            "/signal BUY BTC 65000 - Trading-Signal\n"
            "/approve dec\\_xxx - Freigabe\n"
            "/reject dec\\_xxx - Ablehnung\n"
            "/pause - Alles pausieren\n"
            "/resume - Fortsetzen\n"
            "/kill - Notfall-Stopp\n"
            "/hilfe - Diese Nachricht"
        )
        await self._send(chat_id, msg)

    async def _send(self, chat_id: int, text: str) -> bool:
        if self._dry_run:
            logger.info("[BOT DRY RUN] To %s: %s", chat_id, text[:100])
            return True
        if not self._token:
            return False

        url = f"{_TELEGRAM_API_BASE}/bot{self._token}/sendMessage"
        for idx, chunk in enumerate(_split_telegram_text(text), start=1):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            success = await self._send_payload_with_retry(url, payload)
            if not success:
                # Markdown may have been rejected by Telegram (e.g. unescaped
                # underscores).  Fall back to plain text so the operator always
                # receives the response even if formatting is lost.
                plain_payload = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                }
                success = await self._send_payload_with_retry(url, plain_payload)
                if not success:
                    logger.error("[BOT] Send failed to %s at chunk %s", chat_id, idx)
                    return False
                logger.info("[BOT] Markdown fallback to plain text for chunk %s", idx)
        return True

    async def _send_payload_with_retry(
        self,
        url: str,
        payload: dict[str, Any],
    ) -> bool:
        for attempt in range(1, _TELEGRAM_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(url, json=payload)
                if response.status_code == 200:
                    return True
                if response.status_code == 429 and attempt < _TELEGRAM_MAX_RETRIES:
                    retry_after = _extract_retry_after_seconds(response)
                    await asyncio.sleep(min(retry_after, _TELEGRAM_MAX_RETRY_SLEEP_SECONDS))
                    continue
                logger.error(
                    "[BOT] Telegram HTTP %s: %s",
                    response.status_code,
                    response.text[:200],
                )
                return False
            except Exception as exc:  # noqa: BLE001
                if attempt < _TELEGRAM_MAX_RETRIES:
                    await asyncio.sleep(1)
                    continue
                logger.error("[BOT] Send failed: %s", exc)
                return False
        return False

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
        async with httpx.AsyncClient(timeout=self._long_poll_timeout + 10) as client:
            while self._running:
                try:
                    resp = await client.get(
                        url,
                        params={
                            "offset": self._offset,
                            "timeout": self._long_poll_timeout,
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
