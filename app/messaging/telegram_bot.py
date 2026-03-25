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

import hmac
import json
import logging
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"
_READ_ONLY_COMMANDS = frozenset(
    {
        "status",
        "positions",
        "exposure",
        "signals",
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

        self._token = bot_token
        self._admin_ids = set(admin_chat_ids)
        self._audit_path = Path(audit_log_path)
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._webhook_secret_token = (webhook_secret_token or "").strip()
        self._webhook_rejection_audit_path = Path(webhook_rejection_audit_log)
        self._webhook_rejection_audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._webhook_allowed_updates = normalized_updates
        self._webhook_max_body_bytes = webhook_max_body_bytes
        self._webhook_max_seen_update_ids = webhook_max_seen_update_ids
        self._webhook_seen_update_ids: OrderedDict[int, None] = OrderedDict()
        self._risk_engine = risk_engine
        self._dry_run = dry_run
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
        """Process a single Telegram update and ignore non-command traffic."""
        try:
            message = update.get("message") or update.get("edited_message")
            if not message:
                return

            chat_id = message.get("chat", {}).get("id")
            text = (message.get("text") or "").strip()
            if not chat_id or not text.startswith("/"):
                return

            if chat_id not in self._admin_ids:
                logger.warning(
                    "[BOT] Unauthorized command from chat_id=%s: %s",
                    chat_id,
                    text,
                )
                await self._send(chat_id, "Unauthorized. This incident is logged.")
                return

            command_parts = text.split(maxsplit=1)
            command = command_parts[0].lower().lstrip("/")
            args = command_parts[1].strip() if len(command_parts) > 1 else ""
            await self._dispatch(chat_id, command, args=args)
        except Exception as exc:  # noqa: BLE001
            logger.error("[BOT] Error processing update: %s", exc)

    async def _dispatch(self, chat_id: int, command: str, *, args: str = "") -> None:
        self._audit(chat_id, command, args=args)
        handlers = {
            "status": self._cmd_status,
            "positions": self._cmd_positions,
            "exposure": self._cmd_exposure,
            "signals": self._cmd_signals,
            "alert_status": self._cmd_alert_status,
            "approve": self._cmd_approve,
            "reject": self._cmd_reject,
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "kill": self._cmd_kill,
            "daily_summary": self._cmd_daily_summary,
            "help": self._cmd_help,
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
        msg = (
            "*Status (Operator Summary)*\n"
            f"readiness_status=`{self._inline(payload.get('readiness_status', 'unknown'))}`\n"
            f"cycle_count_today=`{self._inline(payload.get('cycle_count_today', 0))}`\n"
            f"position_count=`{self._inline(payload.get('position_count', 0))}`\n"
            f"ingestion_backlog_documents=`"
            f"{self._inline(payload.get('ingestion_backlog_documents', 'unknown'))}`\n"
            f"alert_fire_rate_docs_per_hour_24h=`"
            f"{self._inline(payload.get('alert_fire_rate_docs_per_hour_24h', 'unknown'))}`\n"
            f"llm_provider_failure_rate_24h=`"
            f"{self._inline(payload.get('llm_provider_failure_rate_24h', 'unknown'))}`\n"
            f"rss_to_alert_latency_p95_seconds_24h=`"
            f"{self._inline(payload.get('rss_to_alert_latency_p95_seconds_24h', 'unknown'))}`\n"
            f"execution_enabled=`{self._inline(payload.get('execution_enabled', False))}`\n"
            f"write_back_allowed=`{self._inline(payload.get('write_back_allowed', False))}`"
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
        top_symbol = "none"
        if positions and isinstance(positions[0], dict):
            top_symbol = self._inline(positions[0].get("symbol", "unknown"))
        msg = (
            "*Positions (Paper Portfolio Read-Only)*\n"
            f"position_count=`{self._inline(payload.get('position_count', 0))}`\n"
            f"mark_to_market_status=`"
            f"{self._inline(payload.get('mark_to_market_status', 'unknown'))}`\n"
            f"top_symbol=`{top_symbol}`\n"
            f"available=`{self._inline(payload.get('available', False))}`\n"
            f"execution_enabled=`{self._inline(payload.get('execution_enabled', False))}`\n"
            f"write_back_allowed=`{self._inline(payload.get('write_back_allowed', False))}`\n"
            "No direct trading position action is exposed via Telegram."
            f"{self._format_refs('positions')}"
        )
        await self._send(chat_id, msg)

    async def _cmd_exposure(self, chat_id: int, *, args: str = "") -> None:
        payload = await self._load_canonical_surface(
            chat_id=chat_id,
            surface_name="Exposure",
            command="exposure",
            loader=self._get_paper_exposure_summary,
        )
        if payload is None:
            return
        msg = (
            "*Exposure (Paper Portfolio Read-Only)*\n"
            f"mark_to_market_status=`"
            f"{self._inline(payload.get('mark_to_market_status', 'unknown'))}`\n"
            f"gross_exposure_usd=`{self._inline(payload.get('gross_exposure_usd', 0.0))}`\n"
            f"net_exposure_usd=`{self._inline(payload.get('net_exposure_usd', 0.0))}`\n"
            f"stale_position_count=`{self._inline(payload.get('stale_position_count', 0))}`\n"
            f"unavailable_price_count=`{self._inline(payload.get('unavailable_price_count', 0))}`\n"
            f"execution_enabled=`{self._inline(payload.get('execution_enabled', False))}`\n"
            f"write_back_allowed=`{self._inline(payload.get('write_back_allowed', False))}`"
            f"{self._format_refs('exposure')}"
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
        signal_preview = "none"
        if isinstance(signals, list) and signals and isinstance(signals[0], dict):
            first = signals[0]
            signal_preview = (
                f"{self._inline(first.get('target_asset', 'unknown'))} "
                f"({self._inline(first.get('direction_hint', 'neutral'))}, "
                f"priority={self._inline(first.get('priority', '?'))})"
            )

        msg = (
            "*Signals (Read-Only Handoff)*\n"
            f"signal_count=`{self._inline(payload.get('signal_count', 0))}`\n"
            f"top_signal=`{signal_preview}`\n"
            f"execution_enabled=`{self._inline(payload.get('execution_enabled', False))}`\n"
            f"write_back_allowed=`{self._inline(payload.get('write_back_allowed', False))}`\n"
            "Signals remain advisory only. No direct execution side effect."
            f"{self._format_refs('signals')}"
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
        msg = (
            "*Alert Status (Read-Only)*\n"
            f"total_count=`{self._inline(payload.get('total_count', 0))}`\n"
            f"digest_count=`{self._inline(payload.get('digest_count', 0))}`\n"
            f"latest_dispatched_at=`{self._inline(payload.get('latest_dispatched_at', 'none'))}`\n"
            f"execution_enabled=`{self._inline(payload.get('execution_enabled', False))}`\n"
            f"write_back_allowed=`{self._inline(payload.get('write_back_allowed', False))}`"
            f"{self._format_refs('alert_status')}"
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
        decision_pack_status = self._inline(payload.get("decision_pack_status", "unknown"))
        msg = (
            "*Daily Summary (Canonical Operator View)*\n"
            f"readiness_status=`{self._inline(payload.get('readiness_status', 'unknown'))}`\n"
            f"cycle_count_today=`{self._inline(payload.get('cycle_count_today', 0))}`\n"
            f"position_count=`{self._inline(payload.get('position_count', 0))}`\n"
            f"total_exposure_pct=`{self._inline(payload.get('total_exposure_pct', 0.0))}`\n"
            f"ingestion_backlog_documents=`"
            f"{self._inline(payload.get('ingestion_backlog_documents', 'unknown'))}`\n"
            f"directional_alert_documents_24h=`"
            f"{self._inline(payload.get('directional_alert_documents_24h', 'unknown'))}`\n"
            f"alert_fire_rate_docs_per_hour_24h=`"
            f"{self._inline(payload.get('alert_fire_rate_docs_per_hour_24h', 'unknown'))}`\n"
            f"rss_to_alert_latency_p50_seconds_24h=`"
            f"{self._inline(payload.get('rss_to_alert_latency_p50_seconds_24h', 'unknown'))}`\n"
            f"llm_provider_failure_rate_24h=`"
            f"{self._inline(payload.get('llm_provider_failure_rate_24h', 'unknown'))}`\n"
            f"llm_error_proxy_rate_7d=`"
            f"{self._inline(payload.get('llm_error_proxy_rate_7d', 'unknown'))}`\n"
            f"decision_pack_status=`{decision_pack_status}`\n"
            f"open_incidents=`{self._inline(payload.get('open_incidents', 0))}`\n"
            f"execution_enabled=`{self._inline(payload.get('execution_enabled', False))}`\n"
            f"write_back_allowed=`{self._inline(payload.get('write_back_allowed', False))}`"
            f"{self._format_refs('daily_summary')}"
        )
        await self._send(chat_id, msg)

    async def _cmd_help(self, chat_id: int, *, args: str = "") -> None:
        msg = (
            "*KAI Operator Commands*\n\n"
            "/status - Operator status summary\n"
            "/positions - Read-only paper positions\n"
            "/exposure - Read-only paper exposure\n"
            "/signals - Read-only signal handoff\n"
            "/daily\\_summary - Daily operator view\n"
            "/alert\\_status - Alert audit summary\n"
            "/approve <decision_ref> - Audit-only approval intent\n"
            "/reject <decision_ref> - Audit-only rejection intent\n"
            "/pause - Pause all operations\n"
            "/resume - Resume operations\n"
            "/kill - Emergency stop (requires confirmation)\n"
            "/help - This message"
        )
        await self._send(chat_id, msg)

    async def _send(self, chat_id: int, text: str) -> bool:
        if self._dry_run:
            logger.info("[BOT DRY RUN] To %s: %s", chat_id, text[:100])
            return True
        if not self._token:
            return False

        url = f"{_TELEGRAM_API_BASE}/bot{self._token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=payload)
            return response.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.error("[BOT] Send failed to %s: %s", chat_id, exc)
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
