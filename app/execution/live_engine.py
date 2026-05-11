"""Phase-0 Live-Order Execution Engine.

Spec: docs/security/kai_light_live_phase0_spec.md (Implementation-Reihenfolge Step 7).

Five-Gate-Chain für jede Live-Order — alle Gates müssen GRÜN sein:

    [1] HOTP-Verify           (jeder /trade braucht NEUEN Code, Spec §2)
    [2] Live-Caps             (Position-Size + Open-Count Hard-Cap, Spec §1)
    [3] Risk-Engine           (kill-switch, daily-loss, dd-cap, Spec §1 Schicht-1)
    [4] Exchange-Perms        (Withdraw=OFF, IP-Allowlist, Spec §3)
    [5] Server-Side-SL-Order  (OCO/V5-tpslMode, Spec §4)

Audit-Pflicht: vor JEDEM Exchange-Call ein "live_order_attempted"-Eintrag,
nach jedem Erfolg/Fehlschlag ein "live_order_placed"/"live_order_rejected".
Schema-Versionierung "live-v1" (Spec §5). N+4-Sprint erweitert das Schema.

Status 2026-05-11: Skeleton + Five-Gate-Chain + Idle-Lock + minimal Audit.
Pending für N+4: vollständiges `live_execution_audit.jsonl`-Schema,
Integration-Tests gegen Binance/Bybit-Testnet, Tabletop, Migration-Drill.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.execution.exchanges.base import BaseExchangeAdapter, OrderRequest, OrderResult
from app.risk.engine import RiskEngine
from app.security.exchange_perms import (
    ExchangePermissionVerifier,
    PermissionDriftError,
    Phase0Expectations,
    verify_against_phase0_requirements,
)
from app.security.hotp_auth import HotpError, HotpVerifier, HotpVerifyResult
from app.security.live_caps import (
    LIVE_MODE_IDLE_LOCK_SECONDS,
    LiveCapBreach,
    LiveOrderCapsView,
    verify_live_order,
)

logger = logging.getLogger(__name__)

_DEFAULT_AUDIT_LOG = Path("artifacts/security/live_execution_audit.jsonl")


class LiveModeState(StrEnum):
    """Boot-Default: LOCKED. /live unlock <hotp> setzt UNLOCKED."""

    LOCKED = "locked"
    UNLOCKED = "unlocked"


@dataclass(frozen=True)
class GateResult:
    """Outcome einer einzelnen Gate-Prüfung in der 5-Gate-Chain."""

    name: str  # "hotp" | "live_caps" | "risk" | "exchange_perms" | "server_sl"
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class LiveOrderOutcome:
    """Aggregiertes Ergebnis von ``submit_live_order``.

    Bei ``success=False`` enthält ``gates`` die genaue Gate-Sequence bis zum
    Fail. Bei ``success=True`` sind alle 5 Gates ``passed=True``.

    ``exchange_result`` ist nur bei Gate 5 (Server-SL-Order placed) gefüllt.
    """

    success: bool
    gates: tuple[GateResult, ...] = field(default_factory=tuple)
    hotp_counter: int | None = None
    audit_id: str = ""
    exchange_result: OrderResult | None = None
    reject_reason: str = ""


class LiveEngineError(Exception):
    """Basis für nicht-aufgehende Live-Engine-Errors (z.B. Setup-Fail)."""


class LiveExecutionEngine:
    """Phase-0 Live-Order-Engine mit 5-Gate-Chain + Idle-Lock.

    Boot-Invariante: state=LOCKED. Operator muss explizit ``/live unlock <hotp>``
    senden. Auto-Lock nach 60 min Idle. Jeder einzelne ``submit_live_order``
    verlangt frischen HOTP (per-Order-HOTP-Pflicht aus Spec §2).

    Args:
        hotp_verifier: ``HotpVerifier``-Instanz mit konfiguriertem Seed/Journal.
        risk_engine: gestartete ``RiskEngine`` für Layer-1-Approval.
        adapters: Map ``exchange_name → BaseExchangeAdapter`` (z.B.
            ``{"binance": BinanceAdapter(...), "bybit": BybitAdapter(...)}``).
        perms_verifiers: Map ``exchange_name → ExchangePermissionVerifier``.
            Phase 0 nutzt einen Cache (Spec §3 — 30 min Refresh). In dieser
            Engine prüfen wir bei jeder Order; periodic-Refresh ist
            Scheduler-Aufgabe außerhalb.
        perms_expectations: Phase-0-Erwartungen für Permissions-Check.
        idle_lock_seconds: Override für Tests; Default aus ``live_caps``.
        audit_log_path: Override für Tests; Default
            ``artifacts/security/live_execution_audit.jsonl``.
    """

    def __init__(
        self,
        *,
        hotp_verifier: HotpVerifier,
        risk_engine: RiskEngine,
        adapters: dict[str, BaseExchangeAdapter],
        perms_verifiers: dict[str, ExchangePermissionVerifier],
        perms_expectations: Phase0Expectations,
        idle_lock_seconds: int = LIVE_MODE_IDLE_LOCK_SECONDS,
        audit_log_path: Path | None = None,
    ) -> None:
        self._hotp = hotp_verifier
        self._risk = risk_engine
        self._adapters = adapters
        self._perms_verifiers = perms_verifiers
        self._perms_expectations = perms_expectations
        self._idle_lock_seconds = idle_lock_seconds
        self._audit_log_path = audit_log_path or _DEFAULT_AUDIT_LOG
        # State
        self._state = LiveModeState.LOCKED
        self._last_unlock_ts: float | None = None
        self._open_position_count = 0  # extern via update_open_count
        # Audit-Counter für Operator-Sichtbarkeit
        self._orders_attempted = 0
        self._orders_placed = 0

    # --------- State / Lock-Management ---------

    @property
    def state(self) -> LiveModeState:
        """Aktueller Live-Mode-State. Auto-Lock-aware."""
        if self._state == LiveModeState.UNLOCKED and self._is_idle_expired():
            logger.info("live_engine_auto_lock idle_timeout=%ds", self._idle_lock_seconds)
            self._state = LiveModeState.LOCKED
            self._last_unlock_ts = None
        return self._state

    def _is_idle_expired(self) -> bool:
        if self._last_unlock_ts is None:
            return False
        return (time.time() - self._last_unlock_ts) > self._idle_lock_seconds

    def lock(self) -> None:
        """Sofort-Lock — kein HOTP nötig (Spec §2 ``/live lock``)."""
        self._state = LiveModeState.LOCKED
        self._last_unlock_ts = None
        logger.info("live_engine_locked manual=true")

    def unlock(self, hotp_code: str) -> HotpVerifyResult:
        """Aktiviert Live-Mode (60 min Idle-TTL).

        Args:
            hotp_code: 6-stelliger HOTP-Code vom Operator-Authenticator.

        Returns:
            ``HotpVerifyResult`` der erfolgreich verbrauchten Counter.

        Raises:
            HotpError (subclasses): bei jedem HOTP-Fehler. Live-Mode bleibt
                im LOCKED-State, kein State-Wechsel.
        """
        result = self._hotp.verify(hotp_code)  # raises HotpError on failure
        self._state = LiveModeState.UNLOCKED
        self._last_unlock_ts = time.time()
        logger.info(
            "live_engine_unlocked counter=%d advance=%d",
            result.counter_used, result.counter_advance,
        )
        return result

    def status(self) -> dict[str, Any]:
        """Read-only Snapshot für ``/live status``-Command."""
        return {
            "state": self.state.value,
            "idle_lock_remaining_s": self._idle_lock_remaining(),
            "last_unlock_iso": (
                datetime.fromtimestamp(self._last_unlock_ts, UTC).isoformat()
                if self._last_unlock_ts is not None
                else None
            ),
            "hotp_last_counter": self._hotp.last_used_counter(),
            "hotp_next_expected": self._hotp.next_expected_counter(),
            "open_positions": self._open_position_count,
            "orders_attempted": self._orders_attempted,
            "orders_placed": self._orders_placed,
        }

    def _idle_lock_remaining(self) -> int:
        if self._last_unlock_ts is None or self._state == LiveModeState.LOCKED:
            return 0
        remaining = self._idle_lock_seconds - int(time.time() - self._last_unlock_ts)
        return max(0, remaining)

    def update_open_count(self, count: int) -> None:
        """Extern updated open-position-count — eg. nach jeder Position-Open/Close.

        Phase 0 hat keinen integrierten Position-Monitor (Spec §1 sagt nur
        "Cap-Check vor Order"). Diese Methode erlaubt dem Operator-Caller
        (z.B. Telegram-Bot oder Position-Monitor-Cron), den Wert zu pflegen.
        """
        if count < 0:
            raise ValueError(f"open_count must be ≥ 0, got {count}")
        self._open_position_count = count

    # --------- Order-Pipeline ---------

    async def submit_live_order(
        self,
        order: OrderRequest,
        *,
        hotp_code: str,
        signal_confidence: float,
        signal_confluence_count: int,
        exchange: str,
        notional_usd: float,
    ) -> LiveOrderOutcome:
        """Submit eine Live-Order durch alle 5 Gates.

        Args:
            order: ``OrderRequest`` mit symbol, side, price, stop_loss, quantity,
                client_order_id. Phase 0: nur LIMIT-Orders (siehe
                ``BaseExchangeAdapter._validate_server_sl``).
            hotp_code: frischer HOTP-Code für DIESE Order (per-Order-Pflicht).
            signal_confidence: 0..1 — wird von RiskEngine konsumiert.
            signal_confluence_count: integer count — wird von RiskEngine
                konsumiert.
            exchange: "binance" oder "bybit" — Adapter-Lookup-Key.
            notional_usd: USD-Wert der Order (price × quantity in USDT).
                Wird für ``live_caps`` benutzt. Caller-Verantwortung dass
                der Wert korrekt aus dem Markt-Preis ableitet.

        Returns:
            ``LiveOrderOutcome`` mit Gate-Spur + exchange_result.
        """
        self._orders_attempted += 1
        audit_id = f"live_attempt_{int(time.time() * 1000)}_{self._orders_attempted}"
        gates: list[GateResult] = []

        self._audit("live_order_attempted", {
            "audit_id": audit_id,
            "exchange": exchange,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "stop_loss": order.stop_loss,
            "notional_usd": notional_usd,
            "live_state": self.state.value,
            "client_order_id": order.client_order_id,
        })

        # Pre-Check: Live-Mode muss UNLOCKED sein (per Idle-Check)
        if self.state != LiveModeState.UNLOCKED:
            return self._reject(
                audit_id, gates, "live_mode_locked",
                "Live-Mode is LOCKED — /live unlock <hotp> first",
            )

        # Gate 1: HOTP — jeder Trade braucht NEUEN Code (counter++).
        try:
            hotp_result = self._hotp.verify(hotp_code)
            gates.append(GateResult("hotp", True, f"counter={hotp_result.counter_used}"))
            self._last_unlock_ts = time.time()  # Refresh Idle-TTL after every HOTP
        except HotpError as exc:
            gates.append(GateResult("hotp", False, type(exc).__name__))
            return self._reject(audit_id, gates, "hotp_failed", str(exc))

        # Gate 2: Live-Caps (notional + open-count). Hardcoded, second
        # unabhängig Layer parallel zu RiskEngine (Spec S-002-Mitigation).
        try:
            view = LiveOrderCapsView(
                notional_usd=notional_usd,
                current_open_positions=self._open_position_count,
                symbol=order.symbol,
                side=order.side,
            )
            verify_live_order(view)
            gates.append(GateResult("live_caps", True, f"notional={notional_usd}"))
        except LiveCapBreach as exc:
            gates.append(GateResult("live_caps", False, str(exc)))
            return self._reject(
                audit_id, gates, "live_caps_breach", str(exc),
                hotp_counter=hotp_result.counter_used,
            )

        # Gate 3: Risk-Engine (kill-switch, daily-loss, dd, etc.)
        risk_result = self._risk.check_order(
            symbol=order.symbol,
            side=order.side,
            signal_confidence=signal_confidence,
            signal_confluence_count=signal_confluence_count,
            stop_loss_price=order.stop_loss,
            current_open_positions=self._open_position_count,
            entry_price=order.price,
            take_profit_price=order.take_profit,
        )
        if not risk_result.approved:
            gates.append(GateResult("risk", False, risk_result.reason or "rejected"))
            return self._reject(
                audit_id, gates, "risk_engine_rejected",
                risk_result.reason or "no_reason",
                hotp_counter=hotp_result.counter_used,
            )
        gates.append(GateResult("risk", True, "approved"))

        # Gate 4: Exchange-Perms (Withdraw=OFF, IP-Allowlist, etc.)
        verifier = self._perms_verifiers.get(exchange)
        if verifier is None:
            gates.append(GateResult("exchange_perms", False, f"no_verifier_for_{exchange}"))
            return self._reject(
                audit_id, gates, "exchange_perms_not_configured",
                f"no verifier for exchange={exchange}",
                hotp_counter=hotp_result.counter_used,
            )
        try:
            status = verifier.fetch_status()
            verify_against_phase0_requirements(
                status=status, expectations=self._perms_expectations,
            )
            gates.append(GateResult("exchange_perms", True, "phase0_compliant"))
        except PermissionDriftError as exc:
            gates.append(GateResult("exchange_perms", False, str(exc)))
            return self._reject(
                audit_id, gates, "exchange_perms_drift", str(exc),
                hotp_counter=hotp_result.counter_used,
            )
        except Exception as exc:  # ExchangeApiError oder anderes
            gates.append(GateResult("exchange_perms", False, f"api_error: {exc}"))
            return self._reject(
                audit_id, gates, "exchange_perms_api_error", str(exc),
                hotp_counter=hotp_result.counter_used,
            )

        # Gate 5: Server-Side-SL Order — atomarer Place via Adapter.
        adapter = self._adapters.get(exchange)
        if adapter is None:
            gates.append(GateResult("server_sl", False, f"no_adapter_for_{exchange}"))
            return self._reject(
                audit_id, gates, "adapter_not_configured",
                f"no adapter for exchange={exchange}",
                hotp_counter=hotp_result.counter_used,
            )

        exchange_result = await adapter.place_order_with_server_sl(order)
        if not exchange_result.success:
            gates.append(GateResult("server_sl", False, exchange_result.error))
            return self._reject(
                audit_id, gates, "server_sl_failed", exchange_result.error,
                hotp_counter=hotp_result.counter_used,
                exchange_result=exchange_result,
            )
        gates.append(GateResult(
            "server_sl", True,
            f"order_id={exchange_result.order_id} sl={exchange_result.sl_order_id}",
        ))

        # All gates passed — record placement.
        self._orders_placed += 1
        self._audit("live_order_placed", {
            "audit_id": audit_id,
            "schema_version": "live-v1",
            "exchange": exchange,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "notional_usd": notional_usd,
            "order_id": exchange_result.order_id,
            "sl_order_id": exchange_result.sl_order_id,
            "sl_price": exchange_result.sl_price,
            "hotp_counter_used": hotp_result.counter_used,
            "current_open_positions_at_send": self._open_position_count,
            "live_caps_check": "passed",
            "risk_engine_check": "passed",
            "exchange_perms_check": "passed",
        })

        return LiveOrderOutcome(
            success=True,
            gates=tuple(gates),
            hotp_counter=hotp_result.counter_used,
            audit_id=audit_id,
            exchange_result=exchange_result,
        )

    def _reject(
        self,
        audit_id: str,
        gates: list[GateResult],
        reject_reason: str,
        detail: str,
        *,
        hotp_counter: int | None = None,
        exchange_result: OrderResult | None = None,
    ) -> LiveOrderOutcome:
        """Helper: persist reject-audit + return outcome."""
        self._audit("live_order_rejected", {
            "audit_id": audit_id,
            "schema_version": "live-v1",
            "reject_reason": reject_reason,
            "detail": detail,
            "gates_passed": [g.name for g in gates if g.passed],
            "gate_failed": next((g.name for g in gates if not g.passed), None),
            "hotp_counter_used": hotp_counter,
        })
        return LiveOrderOutcome(
            success=False,
            gates=tuple(gates),
            hotp_counter=hotp_counter,
            audit_id=audit_id,
            exchange_result=exchange_result,
            reject_reason=f"{reject_reason}: {detail}",
        )

    # --------- Audit ---------

    def _audit(self, event_type: str, record: dict[str, Any]) -> None:
        """Append-only audit-jsonl. Fails silent (logger.error) — never raise."""
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            full_record = {
                "event_type": event_type,
                "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                **record,
            }
            with self._audit_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(full_record) + "\n")
                fh.flush()
        except OSError as exc:
            logger.error("live_audit_write_failed event=%s exc=%s", event_type, exc)
