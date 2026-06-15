"""Premium-Signal-Pipeline End-to-End Healthcheck (P0 #4 — 2026-05-14).

Aggregates the operational liveness signals that matter for the
Telegram-Premium-Channel → Paper-Fill pipeline. Consumed by:
- ``GET /health/premium_pipeline`` FastAPI route (sync HTTP probe)
- ``kai-premium-healthcheck.timer`` cron (Telegram-alert push)

Why this exists: the 2026-05-12 outage stayed unnoticed for 48 hours because
``kai-paper-trading.timer`` was inactive while the FastAPI ``/health`` route
kept reporting ``status=ok``. Server liveness ≠ pipeline liveness.

DBus over jeepney (pure-Python, no system deps). subprocess would work too
but the spawn cost on Pi 5 (~40 ms per is-active call) adds up at 60s tick
cadence; DBus stays in-process and respects systemd's authoritative state.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Service-Liveness Checks — these three units carry the pipeline. If ANY is
# inactive the pipeline degrades silently (12.5.-Outage-Pattern).
CRITICAL_SERVICES = (
    "kai-tg-listener.service",
    "kai-paper-trading.timer",
    "kai-entry-watch.service",
)

# Default thresholds — overridable per call.
# Paper-trading-timer fires every 10 min. 2026-05-14 Edge-Case: ein manuelles
# ``systemctl start kai-paper-trading.service`` (z.B. für Bridge-Smoke nach
# Deploy) zählt NICHT in LastTriggerUSec — systemd schreibt den Wert nur bei
# timer-initiated Starts. Nach einem manuellen Restart kann der Gap bis zum
# nächsten Auto-Trigger 10 + 5 = 15 min betragen. 15 min Threshold (900 s)
# verkraftet das ohne false-positive Alert, fängt aber den echten Outage-Fall
# (Timer komplett inactive seit 48 h, siehe 12.5.) weiterhin in <16 min ab.
DEFAULT_PAPER_TIMER_TICK_MAX_AGE_SEC = 15 * 60
# Telethon heartbeat-loop period 60s → 90s = 1.5× slack for GC pauses.
DEFAULT_HEARTBEAT_MAX_AGE_SEC = 90
# Bridge-audit-log freshness check is informational only — silent ticks
# (no envelopes to process) don't append, so a stale audit-log does NOT
# imply a dead pipeline. Threshold kept for diagnostic detail.
DEFAULT_BRIDGE_AUDIT_INFO_AGE_SEC = 15 * 60
# Poll-backstop writes the semantic canary every 90s by default. 3 minutes
# allows one missed tick plus brief Telegram/network jitter.
DEFAULT_SEMANTIC_CANARY_MAX_AGE_SEC = 3 * 60

_BRIDGE_LOG = Path("artifacts/bridge_pending_orders.jsonl")
_HEARTBEAT_FILE = Path("artifacts/telegram_listener_heartbeat")
_SEMANTIC_CANARY_FILE = Path("artifacts/telegram_channel_semantic_canary.json")

_SYSTEMD_OBJECT = "/org/freedesktop/systemd1"
_SYSTEMD_BUS = "org.freedesktop.systemd1"
_SYSTEMD_MANAGER_IFACE = "org.freedesktop.systemd1.Manager"
_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

# systemd ActiveState values that count as "healthy" for our purposes.
# 'activating' is included because short-lived oneshot units (e.g.
# kai-entry-watch with --duration-seconds 55) spend most of their lifetime
# oscillating between 'active' (running) and 'activating' (restarting).
# A permanently-stuck 'activating' eventually flips to 'failed' anyway
# (StartLimitBurst-protected post-2026-05-14), so this is safe.
_HEALTHY_ACTIVE_STATES = frozenset({"active", "activating", "reloading"})

# Bounded-duration workers run under Restart=always and exit cleanly each cycle.
# kai-entry-watch runs ``--duration-seconds 55`` then deactivates, so for the
# RestartSec window (~5 s of every ~60 s cycle) its ActiveState is ``inactive``
# even though the pipeline is perfectly healthy — a restart job is pending. A
# point-in-time ActiveState probe samples that gap ~8 % of the time and would
# otherwise emit a false ``premium-pipeline FAIL``. For these units we accept a
# *recent* ``inactive`` (restart pending) but still fail a *stale* one: a genuine
# outage — operator ``systemctl stop`` or StartLimitBurst exhaustion (which flips
# ActiveState to ``failed``, never tolerated here) — is caught once the inactive
# dwell exceeds the cycle tolerance, i.e. within ~1.5 min.
_CYCLING_SERVICES = frozenset({"kai-entry-watch.service"})
_CYCLING_TOLERATED_STATES = frozenset({"inactive", "deactivating"})
# duration(55) + RestartSec(5) + slack for GC/scheduling jitter.
_CYCLING_SERVICE_MAX_INACTIVE_SEC = 90


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    age_seconds: float | None = None


@dataclass(frozen=True)
class PipelineHealthReport:
    healthy: bool
    timestamp_utc: str
    checks: list[CheckResult]
    failure_modes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "timestamp_utc": self.timestamp_utc,
            "checks": [asdict(c) for c in self.checks],
            "failure_modes": list(self.failure_modes),
        }


def _dbus_get_unit_path_and_state(unit: str) -> tuple[str, str]:
    """Resolve unit object path + read its ActiveState via DBus. Raises on error."""
    from jeepney import DBusAddress, new_method_call
    from jeepney.io.blocking import open_dbus_connection

    conn = open_dbus_connection(bus="SYSTEM")
    try:
        manager = DBusAddress(
            object_path=_SYSTEMD_OBJECT,
            bus_name=_SYSTEMD_BUS,
            interface=_SYSTEMD_MANAGER_IFACE,
        )
        unit_path_reply = conn.send_and_get_reply(new_method_call(manager, "GetUnit", "s", (unit,)))
        unit_path = unit_path_reply.body[0]
        state_reply = conn.send_and_get_reply(
            new_method_call(
                DBusAddress(
                    object_path=unit_path,
                    bus_name=_SYSTEMD_BUS,
                    interface=_PROPERTIES_IFACE,
                ),
                "Get",
                "ss",
                ("org.freedesktop.systemd1.Unit", "ActiveState"),
            )
        )
        # Reply body is a Variant tuple; body[0] = ('s', 'active')
        state = state_reply.body[0][1]
        return unit_path, state
    finally:
        conn.close()


def _dbus_get_timer_last_trigger_usec(unit_path: str) -> int:
    """Read LastTriggerUSec property from a Timer unit. Returns micro-epoch (UTC)."""
    from jeepney import DBusAddress, new_method_call
    from jeepney.io.blocking import open_dbus_connection

    conn = open_dbus_connection(bus="SYSTEM")
    try:
        reply = conn.send_and_get_reply(
            new_method_call(
                DBusAddress(
                    object_path=unit_path,
                    bus_name=_SYSTEMD_BUS,
                    interface=_PROPERTIES_IFACE,
                ),
                "Get",
                "ss",
                ("org.freedesktop.systemd1.Timer", "LastTriggerUSec"),
            )
        )
        return int(reply.body[0][1])
    finally:
        conn.close()


def _dbus_get_unit_inactive_enter_usec(unit_path: str) -> int:
    """Read InactiveEnterTimestamp (usec since epoch, CLOCK_REALTIME) for a unit.

    Returns 0 if the unit has not entered the inactive state since boot.
    """
    from jeepney import DBusAddress, new_method_call
    from jeepney.io.blocking import open_dbus_connection

    conn = open_dbus_connection(bus="SYSTEM")
    try:
        reply = conn.send_and_get_reply(
            new_method_call(
                DBusAddress(
                    object_path=unit_path,
                    bus_name=_SYSTEMD_BUS,
                    interface=_PROPERTIES_IFACE,
                ),
                "Get",
                "ss",
                ("org.freedesktop.systemd1.Unit", "InactiveEnterTimestamp"),
            )
        )
        return int(reply.body[0][1])
    finally:
        conn.close()


def _check_service_active(
    unit: str,
    now: datetime | None = None,
    _state_fn: Any = None,
    _inactive_usec_fn: Any = None,
) -> CheckResult:
    state_fn = _state_fn or _dbus_get_unit_path_and_state
    inactive_usec_fn = _inactive_usec_fn or _dbus_get_unit_inactive_enter_usec
    try:
        unit_path, state = state_fn(unit)
    except Exception as exc:  # noqa: BLE001 — DBus failures must not crash the report
        return CheckResult(
            name=f"systemd:{unit}",
            ok=False,
            detail=f"dbus_error: {type(exc).__name__}: {exc}",
        )

    if state in _HEALTHY_ACTIVE_STATES:
        return CheckResult(name=f"systemd:{unit}", ok=True, detail=f"ActiveState={state}")

    # Cycling worker: tolerate a *recent* inactive (restart pending), fail a stale one.
    if unit in _CYCLING_SERVICES and state in _CYCLING_TOLERATED_STATES:
        try:
            inactive_usec = inactive_usec_fn(unit_path)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=f"systemd:{unit}",
                ok=False,
                detail=f"ActiveState={state}; inactive_ts dbus_error: {type(exc).__name__}: {exc}",
            )
        if inactive_usec > 0:
            inactive_since = datetime.fromtimestamp(inactive_usec / 1_000_000, tz=UTC)
            age = ((now or datetime.now(UTC)) - inactive_since).total_seconds()
            ok = age <= _CYCLING_SERVICE_MAX_INACTIVE_SEC
            detail = (
                f"ActiveState={state} cycling (inactive {age:.0f}s, restart pending)"
                if ok
                else f"ActiveState={state} inactive {age:.0f}s "
                f"> {_CYCLING_SERVICE_MAX_INACTIVE_SEC}s tolerance"
            )
            return CheckResult(name=f"systemd:{unit}", ok=ok, detail=detail, age_seconds=age)

    return CheckResult(name=f"systemd:{unit}", ok=False, detail=f"ActiveState={state}")


def _check_paper_timer_last_trigger(
    max_age_seconds: int, now: datetime | None = None
) -> CheckResult:
    """Verify kai-paper-trading.timer triggered within the last N seconds.

    This is the authoritative bridge-liveness signal — independent of whether
    envelopes were processed during the last tick. The audit-log (silent on
    no-op ticks) cannot answer this question.
    """
    try:
        unit_path, _ = _dbus_get_unit_path_and_state("kai-paper-trading.timer")
        last_trigger_usec = _dbus_get_timer_last_trigger_usec(unit_path)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="paper_timer_last_trigger",
            ok=False,
            detail=f"dbus_error: {type(exc).__name__}: {exc}",
        )
    if last_trigger_usec == 0:
        return CheckResult(
            name="paper_timer_last_trigger",
            ok=False,
            detail="LastTriggerUSec=0 (timer has not fired since boot)",
        )
    last_trigger = datetime.fromtimestamp(last_trigger_usec / 1_000_000, tz=UTC)
    age = ((now or datetime.now(UTC)) - last_trigger).total_seconds()
    return CheckResult(
        name="paper_timer_last_trigger",
        ok=(age <= max_age_seconds),
        detail=f"last_trigger={last_trigger.isoformat()} threshold={max_age_seconds}s",
        age_seconds=age,
    )


def _check_heartbeat(
    max_age_seconds: int, now: datetime | None = None, path: Path | None = None
) -> CheckResult:
    target = path or _HEARTBEAT_FILE
    if not target.exists():
        return CheckResult(name="heartbeat", ok=False, detail=f"missing: {target}")
    try:
        mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=UTC)
    except OSError as exc:
        return CheckResult(name="heartbeat", ok=False, detail=f"stat_error: {exc}")
    age = ((now or datetime.now(UTC)) - mtime).total_seconds()
    return CheckResult(
        name="heartbeat",
        ok=(age <= max_age_seconds),
        detail=f"mtime={mtime.isoformat()} threshold={max_age_seconds}s",
        age_seconds=age,
    )


def _check_bridge_audit_freshness(
    info_age_seconds: int, now: datetime | None = None, path: Path | None = None
) -> CheckResult:
    """Last operator_signal_bridge event mtime — informational only.

    A stale audit log does NOT mean the bridge is dead — it might just have
    had no envelopes to process. This check is recorded for forensic detail
    but never contributes to failure_modes (ok=True regardless of age).
    """
    target = path or _BRIDGE_LOG
    if not target.exists():
        return CheckResult(
            name="bridge_audit_last_event",
            ok=True,  # not a failure — pipeline may simply have no history yet
            detail=f"missing: {target}",
        )
    try:
        with target.open("rb") as fh:
            data = fh.read()
    except OSError as exc:
        return CheckResult(
            name="bridge_audit_last_event",
            ok=True,
            detail=f"io_error: {exc}",
        )
    last_ts: datetime | None = None
    for line in reversed(data.decode("utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_raw = rec.get("timestamp_utc")
        if isinstance(ts_raw, str):
            try:
                parsed = datetime.fromisoformat(ts_raw)
                last_ts = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
                break
            except ValueError:
                continue
    if last_ts is None:
        return CheckResult(
            name="bridge_audit_last_event",
            ok=True,
            detail="no_parseable_records",
        )
    age = ((now or datetime.now(UTC)) - last_ts).total_seconds()
    note = "fresh" if age <= info_age_seconds else "stale_but_not_a_failure"
    return CheckResult(
        name="bridge_audit_last_event",
        ok=True,
        detail=f"last_event={last_ts.isoformat()} note={note}",
        age_seconds=age,
    )


def _check_semantic_canary(
    max_age_seconds: int, now: datetime | None = None, path: Path | None = None
) -> CheckResult:
    """Verify Telegram source head has converged into the checkpoint.

    Heartbeat only proves the process loop is alive. The canary is written by
    the listener's poll-backstop after comparing Telegram's latest message id
    with the persisted checkpoint. Any positive gap means the source has
    messages that the pipeline has not acknowledged.
    """
    target = path or _SEMANTIC_CANARY_FILE
    if not target.exists():
        return CheckResult(name="semantic_canary", ok=False, detail=f"missing: {target}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(name="semantic_canary", ok=False, detail=f"read_error: {exc}")
    checked_raw = data.get("checked_at")
    if not isinstance(checked_raw, str):
        return CheckResult(name="semantic_canary", ok=False, detail="missing checked_at")
    try:
        checked = datetime.fromisoformat(checked_raw)
    except ValueError:
        return CheckResult(
            name="semantic_canary", ok=False, detail=f"invalid checked_at={checked_raw}"
        )
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    age = ((now or datetime.now(UTC)) - checked).total_seconds()
    gap = data.get("gap")
    if not isinstance(gap, int):
        return CheckResult(name="semantic_canary", ok=False, detail="missing gap")
    ok = age <= max_age_seconds and gap <= 0
    return CheckResult(
        name="semantic_canary",
        ok=ok,
        detail=(
            f"checkpoint={data.get('checkpoint_message_id')} "
            f"latest={data.get('latest_message_id')} gap={gap} "
            f"threshold={max_age_seconds}s"
        ),
        age_seconds=age,
    )


def _check_approval_hmac() -> CheckResult:
    """Approval callback tokens must be signed when approval mode is enabled."""
    try:
        from app.core.settings import get_settings

        execution = get_settings().execution
        approval_enabled = bool(execution.operator_signal_approval_enabled)
        secret_set = bool((execution.operator_signal_approval_hmac_secret or "").strip())
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="approval_hmac",
            ok=False,
            detail=f"settings_error: {type(exc).__name__}: {exc}",
        )
    if not approval_enabled:
        return CheckResult(name="approval_hmac", ok=True, detail="approval_disabled")
    return CheckResult(
        name="approval_hmac",
        ok=secret_set,
        detail="signed_callbacks_enabled" if secret_set else "missing_hmac_secret",
    )


def compute_pipeline_health(
    *,
    paper_timer_max_age_sec: int = DEFAULT_PAPER_TIMER_TICK_MAX_AGE_SEC,
    heartbeat_max_age_sec: int = DEFAULT_HEARTBEAT_MAX_AGE_SEC,
    bridge_audit_info_age_sec: int = DEFAULT_BRIDGE_AUDIT_INFO_AGE_SEC,
    semantic_canary_max_age_sec: int = DEFAULT_SEMANTIC_CANARY_MAX_AGE_SEC,
    now: datetime | None = None,
    _service_check_fn: Any = None,
    _paper_timer_check_fn: Any = None,
    _heartbeat_check_fn: Any = None,
    _bridge_audit_check_fn: Any = None,
    _semantic_canary_check_fn: Any = None,
    _approval_hmac_check_fn: Any = None,
) -> PipelineHealthReport:
    """Run all liveness checks and aggregate into a single report.

    The four ``_*_check_fn`` hooks exist exclusively for unit tests — they let
    tests substitute deterministic fakes for the DBus / filesystem probes
    without ever touching a real systemd or artifacts directory.
    """
    svc_check = _service_check_fn or _check_service_active
    timer_check = _paper_timer_check_fn or _check_paper_timer_last_trigger
    hb_check = _heartbeat_check_fn or _check_heartbeat
    audit_check = _bridge_audit_check_fn or _check_bridge_audit_freshness
    canary_check = _semantic_canary_check_fn or _check_semantic_canary
    hmac_check = _approval_hmac_check_fn or _check_approval_hmac

    checks: list[CheckResult] = [svc_check(svc, now=now) for svc in CRITICAL_SERVICES]
    checks.append(timer_check(paper_timer_max_age_sec, now=now))
    checks.append(hb_check(heartbeat_max_age_sec, now=now))
    checks.append(canary_check(semantic_canary_max_age_sec, now=now))
    checks.append(hmac_check())
    checks.append(audit_check(bridge_audit_info_age_sec, now=now))

    failure_modes = [c.name for c in checks if not c.ok]
    return PipelineHealthReport(
        healthy=not failure_modes,
        timestamp_utc=(now or datetime.now(UTC)).isoformat(),
        checks=checks,
        failure_modes=failure_modes,
    )


__all__ = [
    "CRITICAL_SERVICES",
    "CheckResult",
    "PipelineHealthReport",
    "compute_pipeline_health",
]
