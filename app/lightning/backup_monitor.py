"""Sprint 6 — SCB (channel.backup) drift monitor (resilience, read-only).

The lnd static channel backup (SCB) must be re-archived whenever channels change
(open/close), or a recovery would miss funds. This module hashes the SCB and
compares it to a recorded baseline, surfacing an operator re-backup reminder on
drift. Pure file I/O, fail-soft, NO capital path.

States: ``missing`` (SCB gone), ``no_baseline`` (first run → records it),
``stable`` (matches and fresh), ``stale`` (copy too old), ``changed`` (differs →
reminder; baseline advanced). The one-shot module entry point sends reminders via
the existing operator-notification channel and is safe to run from systemd.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.lightning_settings import LightningSettings

logger = logging.getLogger(__name__)

_SCB_BASELINE_PATH = Path("artifacts/scb_baseline.json")


@dataclass(frozen=True)
class ScbStatus:
    present: bool
    size_bytes: int = 0
    sha256: str = ""
    mtime_iso: str = ""
    mtime_epoch: float = 0.0


def read_scb_status(scb_path: Path | str) -> ScbStatus:
    """Hash + stat the SCB file (``present=False`` if missing/unreadable)."""
    p = Path(scb_path)
    try:
        raw = p.read_bytes()
        st = p.stat()
    except OSError:
        return ScbStatus(present=False)
    return ScbStatus(
        present=True,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        mtime_iso=datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
        mtime_epoch=st.st_mtime,
    )


def _read_baseline(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_baseline(path: Path, status: ScbStatus, *, recorded_at: datetime) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "sha256": status.sha256,
                    "size_bytes": status.size_bytes,
                    "scb_mtime": status.mtime_iso,
                    "recorded_at": recorded_at.isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("[scb-monitor] baseline write failed: %s", exc)
        return False
    return True


def _age_seconds(status: ScbStatus, now: datetime) -> float:
    return max(0.0, now.timestamp() - status.mtime_epoch)


def check_scb_drift(
    scb_path: Path | str,
    *,
    baseline_path: Path | str | None = None,
    max_age_seconds: int = 7200,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare the SCB against the recorded baseline; advance the baseline on a
    legitimate change so the next run is ``stable`` again. Never raises."""
    base = Path(baseline_path) if baseline_path is not None else _SCB_BASELINE_PATH
    observed_at = now or datetime.now(UTC)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    status = read_scb_status(scb_path)
    if not status.present:
        return {
            "state": "missing",
            "reminder": True,
            "sha256": "",
            "age_seconds": None,
            "detail": "SCB file not found",
        }

    age_seconds = _age_seconds(status, observed_at)
    stale = age_seconds >= float(max_age_seconds)

    baseline = _read_baseline(base)
    prev = str(baseline.get("sha256", ""))
    if not prev:
        written = _write_baseline(base, status, recorded_at=observed_at)
        return {
            "state": "no_baseline",
            "reminder": stale or not written,
            "sha256": status.sha256,
            "age_seconds": age_seconds,
            "stale": stale,
            "baseline_written": written,
            "detail": (
                "SCB baseline created"
                if written and not stale
                else "SCB baseline missing; current copy is stale or could not be recorded"
            ),
        }
    if prev == status.sha256:
        if stale:
            return {
                "state": "stale",
                "reminder": True,
                "sha256": status.sha256,
                "age_seconds": age_seconds,
                "stale": True,
                "detail": f"SCB copy is at least {int(max_age_seconds)} seconds old",
            }
        return {
            "state": "stable",
            "reminder": False,
            "sha256": status.sha256,
            "age_seconds": age_seconds,
            "stale": False,
        }
    written = _write_baseline(base, status, recorded_at=observed_at)
    return {
        "state": "changed",
        "reminder": True,
        "sha256": status.sha256,
        "previous_sha256": prev,
        "age_seconds": age_seconds,
        "stale": stale,
        "baseline_written": written,
        "detail": "SCB changed (channels opened/closed?) — re-archive the backup",
    }


_Notifier = Callable[[str], Awaitable[bool]]


def _alert_text(report: dict[str, Any]) -> str:
    age = report.get("age_seconds")
    age_line = "n/a" if age is None else f"{float(age) / 3600:.1f}h"
    return (
        "KAI Lightning SCB Alert\n"
        f"State: {report.get('state', 'unknown')}\n"
        f"Path: {report.get('scb_path', 'unknown')}\n"
        f"Age: {age_line}\n"
        f"Detail: {report.get('detail', 'operator review required')}"
    )


async def monitor_scb_once(
    cfg: LightningSettings | None = None,
    *,
    notify: _Notifier | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one configured SCB check and notify only when operator action is due."""
    cfg = cfg or LightningSettings()
    if not cfg.enabled:
        return {"state": "disabled", "reminder": False, "alert_sent": False}

    scb_path = cfg.scb_path.strip()
    if not scb_path:
        report: dict[str, Any] = {
            "state": "missing",
            "reminder": True,
            "sha256": "",
            "age_seconds": None,
            "detail": "APP_LN_SCB_PATH is not configured",
        }
    else:
        report = check_scb_drift(
            scb_path,
            baseline_path=cfg.scb_baseline_path,
            max_age_seconds=cfg.scb_max_age_seconds,
            now=now,
        )
    report["scb_path"] = scb_path
    report["baseline_path"] = cfg.scb_baseline_path
    report["alert_sent"] = False
    if report.get("reminder"):
        if notify is None:
            from app.alerts.notify import send_operator_notification

            notify = send_operator_notification
        try:
            report["alert_sent"] = bool(await notify(_alert_text(report)))
        except Exception as exc:  # noqa: BLE001 — monitor must still report via journal
            report["alert_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("[scb-monitor] operator notification failed: %s", exc)
    return report


def main() -> int:
    """Systemd one-shot entry point; non-zero means an actionable SCB condition."""
    try:
        report = asyncio.run(monitor_scb_once())
    except Exception as exc:  # noqa: BLE001 — configuration failures must be visible
        report = {
            "state": "configuration_error",
            "reminder": True,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(report, sort_keys=True))
    return 1 if report.get("reminder") else 0


__all__ = ["ScbStatus", "check_scb_drift", "monitor_scb_once", "read_scb_status"]


if __name__ == "__main__":  # pragma: no cover - exercised by systemd
    raise SystemExit(main())
