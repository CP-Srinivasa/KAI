"""Forward-Precision Watchdog — silent-failure-mode-Mitigation.

V-DB5 P2 Vorschlag 2 (2026-05-09): Telegram-Alert wenn das forward-Signal
N aufeinanderfolgende Stunden unter der Quality-Schwelle liegt. Vorher war
Forward-Precision-Drift silent — Operator sah es erst beim nächsten
Dashboard-Check. Mit diesem Watchdog kommt ein Push innerhalb 1h.

Pure-functions sind im Modul, sodass Tests deterministisch laufen
(Streak-Tracking + Cooldown ohne Time-Mocking-Hell).

Standalone-Konfiguration via ENV statt Settings-Field, damit V-DB5-UI
und KAI-Live-Phase-2-Drift im Working-Tree nicht angefasst werden.

ENV-Variablen:
- APP_WATCHDOG_FWD_PREC_THRESHOLD_PCT       (default 60.0)
- APP_WATCHDOG_FWD_PREC_CI_LOW_THRESHOLD_PCT (default 50.0)
- APP_WATCHDOG_FWD_PREC_CONSECUTIVE_HOURS    (default 6)
- APP_WATCHDOG_FWD_PREC_COOLDOWN_HOURS       (default 12)

Persistenz: ``artifacts/watchdog/forward_precision_streak.json``
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import portalocker

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD_PCT = 60.0
_DEFAULT_CI_LOW_THRESHOLD_PCT = 50.0
_DEFAULT_CONSECUTIVE_HOURS = 6
_DEFAULT_COOLDOWN_HOURS = 12

_DEFAULT_STATE_PATH = Path("artifacts/watchdog/forward_precision_streak.json")


@dataclass(frozen=True)
class WatchdogConfig:
    """Schwellwerte und Streak-Verhalten — alle ENV-overridable."""

    threshold_pct: float = _DEFAULT_THRESHOLD_PCT
    ci_low_threshold_pct: float = _DEFAULT_CI_LOW_THRESHOLD_PCT
    consecutive_hours: int = _DEFAULT_CONSECUTIVE_HOURS
    cooldown_hours: int = _DEFAULT_COOLDOWN_HOURS


def load_config_from_env(env: dict[str, str] | None = None) -> WatchdogConfig:
    """Build WatchdogConfig from environment, falling back to defaults."""
    src = env if env is not None else os.environ
    return WatchdogConfig(
        threshold_pct=float(
            src.get("APP_WATCHDOG_FWD_PREC_THRESHOLD_PCT", _DEFAULT_THRESHOLD_PCT)
        ),
        ci_low_threshold_pct=float(
            src.get(
                "APP_WATCHDOG_FWD_PREC_CI_LOW_THRESHOLD_PCT",
                _DEFAULT_CI_LOW_THRESHOLD_PCT,
            )
        ),
        consecutive_hours=int(
            src.get("APP_WATCHDOG_FWD_PREC_CONSECUTIVE_HOURS", _DEFAULT_CONSECUTIVE_HOURS)
        ),
        cooldown_hours=int(
            src.get("APP_WATCHDOG_FWD_PREC_COOLDOWN_HOURS", _DEFAULT_COOLDOWN_HOURS)
        ),
    )


@dataclass
class StreakState:
    """Persistent state — N consecutive checks below threshold."""

    streak_hours: int = 0
    first_below_at: str | None = None
    last_check_at: str | None = None
    last_push_at: str | None = None
    last_push_kind: str | None = None  # "drift" or "recovery"

    def to_dict(self) -> dict[str, Any]:
        return {
            "streak_hours": self.streak_hours,
            "first_below_at": self.first_below_at,
            "last_check_at": self.last_check_at,
            "last_push_at": self.last_push_at,
            "last_push_kind": self.last_push_kind,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreakState:
        return cls(
            streak_hours=int(data.get("streak_hours", 0)),
            first_below_at=data.get("first_below_at"),
            last_check_at=data.get("last_check_at"),
            last_push_at=data.get("last_push_at"),
            last_push_kind=data.get("last_push_kind"),
        )


def load_state(path: Path = _DEFAULT_STATE_PATH) -> StreakState:
    """Load persisted streak state. Missing file → fresh state."""
    if not path.exists():
        return StreakState()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return StreakState.from_dict(data)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        # Corruption → start fresh, don't crash watchdog.
        logger.warning(
            "[fwd-prec-watchdog] state load failed (%s) — resetting", exc
        )
        return StreakState()


def save_state(state: StreakState, path: Path = _DEFAULT_STATE_PATH) -> None:
    """Persist streak state with portalocker LOCK_EX (V-DB5 B-K2 pattern)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        portalocker.lock(f, portalocker.LOCK_EX)
        json.dump(state.to_dict(), f, indent=2)


@dataclass(frozen=True)
class WatchdogResult:
    """Pure-function result — Worker decides what to do with it."""

    severity: str  # "info", "warn", "ok"
    title: str
    detail: str
    new_state: StreakState
    should_push: bool
    push_kind: str | None  # "drift" or "recovery" or None


def evaluate_forward_precision_drift(
    *,
    forward_precision_pct: float | None,
    forward_precision_ci_low_pct: float | None,
    state: StreakState,
    now: datetime,
    config: WatchdogConfig,
) -> WatchdogResult:
    """Pure-function evaluation — no I/O, fully testable.

    Logic:
    - If precision/CI-low below threshold: increment streak. When streak
      crosses ``consecutive_hours`` and cooldown elapsed: push "drift".
    - If precision/CI-low recovers: if last push was "drift" and cooldown
      elapsed, push "recovery". Always reset streak.
    - If both metrics ``None``: no judgment possible — info finding,
      no streak change.
    """
    now_iso = now.isoformat()

    if forward_precision_pct is None and forward_precision_ci_low_pct is None:
        new_state = StreakState(
            streak_hours=state.streak_hours,
            first_below_at=state.first_below_at,
            last_check_at=now_iso,
            last_push_at=state.last_push_at,
            last_push_kind=state.last_push_kind,
        )
        return WatchdogResult(
            severity="info",
            title="forward_precision_no_data",
            detail="forward_precision_pct/ci_low fehlen im Hold-Report",
            new_state=new_state,
            should_push=False,
            push_kind=None,
        )

    below_threshold = (
        forward_precision_pct is not None
        and forward_precision_pct < config.threshold_pct
    )
    below_ci_low = (
        forward_precision_ci_low_pct is not None
        and forward_precision_ci_low_pct < config.ci_low_threshold_pct
    )
    is_drift = below_threshold or below_ci_low

    cooldown_ok = _cooldown_elapsed(state.last_push_at, now, config.cooldown_hours)

    if is_drift:
        # Increment streak. First-below-at gets set on transition 0→1.
        new_streak = state.streak_hours + 1
        first_below_at = (
            state.first_below_at if state.streak_hours > 0 else now_iso
        )
        crossed_threshold = (
            state.streak_hours < config.consecutive_hours
            and new_streak >= config.consecutive_hours
        )

        should_push = crossed_threshold and cooldown_ok and state.last_push_kind != "drift"
        push_kind = "drift" if should_push else None

        new_state = StreakState(
            streak_hours=new_streak,
            first_below_at=first_below_at,
            last_check_at=now_iso,
            last_push_at=now_iso if should_push else state.last_push_at,
            last_push_kind="drift" if should_push else state.last_push_kind,
        )

        detail = _format_drift_detail(
            forward_precision_pct=forward_precision_pct,
            forward_precision_ci_low_pct=forward_precision_ci_low_pct,
            streak_hours=new_streak,
            config=config,
        )
        # Severity: warn only after threshold crossed, info while building up.
        severity = "warn" if new_streak >= config.consecutive_hours else "info"
        title = (
            "forward_precision_drift"
            if severity == "warn"
            else "forward_precision_below_warming"
        )
        return WatchdogResult(
            severity=severity,
            title=title,
            detail=detail,
            new_state=new_state,
            should_push=should_push,
            push_kind=push_kind,
        )

    # Recovery path
    was_in_drift = state.last_push_kind == "drift" and state.streak_hours > 0
    should_push = was_in_drift and cooldown_ok
    push_kind = "recovery" if should_push else None

    new_state = StreakState(
        streak_hours=0,
        first_below_at=None,
        last_check_at=now_iso,
        last_push_at=now_iso if should_push else state.last_push_at,
        last_push_kind="recovery" if should_push else state.last_push_kind,
    )
    detail = _format_recovery_detail(
        forward_precision_pct=forward_precision_pct,
        forward_precision_ci_low_pct=forward_precision_ci_low_pct,
        prev_streak=state.streak_hours,
        config=config,
    )
    return WatchdogResult(
        severity="info",
        title="forward_precision_recovered" if was_in_drift else "forward_precision_ok",
        detail=detail,
        new_state=new_state,
        should_push=should_push,
        push_kind=push_kind,
    )


def _cooldown_elapsed(last_push_at: str | None, now: datetime, hours: int) -> bool:
    if last_push_at is None:
        return True
    try:
        last = datetime.fromisoformat(last_push_at)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now - last >= timedelta(hours=hours)


def _format_drift_detail(
    *,
    forward_precision_pct: float | None,
    forward_precision_ci_low_pct: float | None,
    streak_hours: int,
    config: WatchdogConfig,
) -> str:
    parts: list[str] = [f"streak={streak_hours}h"]
    if forward_precision_pct is not None:
        parts.append(
            f"forward_precision={forward_precision_pct:.1f}% (Schwelle {config.threshold_pct:.0f}%)"
        )
    if forward_precision_ci_low_pct is not None:
        parts.append(
            f"ci_low={forward_precision_ci_low_pct:.1f}% "
            f"(Schwelle {config.ci_low_threshold_pct:.0f}%)"
        )
    return ", ".join(parts)


def _format_recovery_detail(
    *,
    forward_precision_pct: float | None,
    forward_precision_ci_low_pct: float | None,
    prev_streak: int,
    config: WatchdogConfig,
) -> str:
    parts: list[str] = [f"prev_streak={prev_streak}h"]
    if forward_precision_pct is not None:
        parts.append(f"forward_precision={forward_precision_pct:.1f}%")
    if forward_precision_ci_low_pct is not None:
        parts.append(f"ci_low={forward_precision_ci_low_pct:.1f}%")
    return ", ".join(parts)


def format_telegram_message(result: WatchdogResult) -> str:
    """Telegram-Markdown-formatted message for drift/recovery push."""
    if result.push_kind == "drift":
        return (
            f"⚠️ *KAI Watchdog — Forward-Precision-Drift*\n\n"
            f"`{result.title}`\n"
            f"{result.detail}\n\n"
            f"_KAI meldet stille Quality-Drift. Operator-Action empfohlen: "
            f"Source-Quality im Dashboard prüfen, blocked-alert-Reasons checken._"
        )
    if result.push_kind == "recovery":
        return (
            f"✅ *KAI Watchdog — Forward-Precision erholt*\n\n"
            f"`{result.title}`\n"
            f"{result.detail}"
        )
    return ""


def push_to_telegram(
    text: str,
    *,
    bot_token: str,
    chat_id: str,
    timeout_s: float = 10.0,
) -> bool:
    """Synchronous Telegram push — returns True on HTTP 200 + ok=true.

    Watchdog läuft im sync-Worker-Thread, deshalb httpx.Client (nicht async).
    Failures werden geloggt, aber nicht raised — Watchdog bleibt unkillable.
    """
    if not bot_token or not chat_id:
        logger.warning("[fwd-prec-watchdog] telegram push skipped: missing token/chat_id")
        return False
    try:
        import httpx  # local import: keeps watchdog importable in test envs without httpx

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=payload)
        if resp.status_code != 200:
            logger.warning(
                "[fwd-prec-watchdog] telegram push HTTP %s: %s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        body = resp.json()
        ok = bool(body.get("ok"))
        if not ok:
            logger.warning("[fwd-prec-watchdog] telegram push not-ok: %s", body)
        return ok
    except Exception as exc:  # noqa: BLE001 — watchdog must never crash on push
        logger.warning("[fwd-prec-watchdog] telegram push exception: %s", exc)
        return False


def run_check(
    *,
    forward_precision_pct: float | None,
    forward_precision_ci_low_pct: float | None,
    now: datetime | None = None,
    state_path: Path | None = None,
    config: WatchdogConfig | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> tuple[str, str, str]:
    """Top-level adapter for the worker — load state, evaluate, save, push.

    Returns ``(severity, title, detail)`` — same shape as
    ``_listener_reactivity_check`` so the worker can append directly.
    Telegram push is fire-and-forget; failure does not change the
    finding.
    """
    cfg = config if config is not None else load_config_from_env()
    path = state_path if state_path is not None else _DEFAULT_STATE_PATH
    moment = now if now is not None else datetime.now(UTC)
    state = load_state(path)
    result = evaluate_forward_precision_drift(
        forward_precision_pct=forward_precision_pct,
        forward_precision_ci_low_pct=forward_precision_ci_low_pct,
        state=state,
        now=moment,
        config=cfg,
    )
    save_state(result.new_state, path)
    if result.should_push and bot_token and chat_id:
        text = format_telegram_message(result)
        if text:
            push_to_telegram(text, bot_token=bot_token, chat_id=chat_id)
    return (result.severity, result.title, result.detail)
