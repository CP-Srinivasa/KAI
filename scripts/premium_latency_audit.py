#!/usr/bin/env python3
"""Daily premium-pipeline latency audit (P2 #11 trigger-watch — 2026-05-14).

Reads bridge_pending_orders.jsonl, computes p50/p95/p99/max for the last
7 days, writes:

- artifacts/premium_latency_report.json — most-recent stats (overwritten daily)
- artifacts/p2_11_trigger.json — ONLY when trigger condition holds
  (p95 > 20 min AND n >= 5). Once written, the marker stays until the
  operator removes it. Next Claude session reads kai_premium_pipeline_backlog
  + this marker as First-Action and proceeds with P2 #11 implementation.

Always sends one Telegram digest per run so the operator sees the
distribution evolution (no rate-limit; cron fires once per day).

Exit codes:
- 0 healthy (no trigger or trigger fired and successfully alerted)
- 1 hard error (audit read failed, telegram send broken — journal captures)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.observability.premium_latency import (  # noqa: E402
    compute_latency_stats,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("latency-audit")

_REPORT_PATH = _REPO_ROOT / "artifacts" / "premium_latency_report.json"
_TRIGGER_PATH = _REPO_ROOT / "artifacts" / "p2_11_trigger.json"


def _fmt_seconds(s: float | None) -> str:
    if s is None:
        return "n/a"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s/60:.1f}min"
    return f"{s/3600:.1f}h"


def _format_digest(stats) -> str:
    head = f"KAI premium-latency audit (last {stats.lookback_hours}h)"
    body = [
        head,
        f"samples={stats.sample_size}  expired={stats.expired_count} ({stats.expired_pct:.1f}%)",
        f"receive→fill: p50={_fmt_seconds(stats.p50_seconds)}  "
        f"p95={_fmt_seconds(stats.p95_seconds)}  "
        f"p99={_fmt_seconds(stats.p99_seconds)}  "
        f"max={_fmt_seconds(stats.max_seconds)}",
    ]
    if stats.trigger_fired:
        body.extend([
            "",
            "⚠️  P2 #11 TRIGGER FIRED",
            stats.trigger_reason,
            "Next: nächste Claude-Session sollte P2 #11 starten",
            "(siehe artifacts/p2_11_trigger.json + memory kai_premium_pipeline_backlog_20260514)",
        ])
    else:
        body.append("trigger #11: NO (threshold p95>20min, samples>=5)")
    return "\n".join(body)


def _send_telegram(text: str) -> bool:
    token = os.environ.get("ALERT_TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("ALERT_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("ALERT_TELEGRAM_TOKEN/CHAT_ID missing — printing only")
        return False
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram send failed: %s", exc)
        return False


def _write_report(stats) -> None:
    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        **stats.to_dict(),
    }
    _REPORT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_trigger_marker(stats) -> None:
    """Persist the trigger event — once written, stays until operator removes.

    File is read by the next Claude-session as First-Action (memory pin
    session_pin_p2_11_auto_eskalation lists this path). Idempotent: re-running
    the audit on the same day will overwrite the marker with the latest stats,
    not duplicate it.
    """
    _TRIGGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fired_at": datetime.now(UTC).isoformat(),
        "reason": stats.trigger_reason,
        "stats": stats.to_dict(),
        "next_action": (
            "Start P2 #11 (event-driven inotify bridge) implementation. "
            "See kai_premium_pipeline_backlog_20260514.md for design + "
            "architecture-decision (Worker stays separate or merge into FastAPI)."
        ),
    }
    _TRIGGER_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.warning(
        "P2 #11 TRIGGER FIRED — marker written to %s reason=%s",
        _TRIGGER_PATH,
        stats.trigger_reason,
    )


def main() -> int:
    try:
        stats = compute_latency_stats()
    except Exception as exc:  # noqa: BLE001
        logger.error("compute_latency_stats failed: %s", exc)
        return 1

    _write_report(stats)
    digest = _format_digest(stats)
    print(digest)

    if stats.trigger_fired:
        _write_trigger_marker(stats)

    _send_telegram(digest)
    logger.info(
        "audit complete samples=%d p95=%s trigger=%s",
        stats.sample_size,
        stats.p95_seconds,
        stats.trigger_fired,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
