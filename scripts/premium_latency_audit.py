#!/usr/bin/env python3
"""Daily premium-pipeline latency digest (INFORMATIONAL — auto-trigger retired).

Reads bridge_pending_orders.jsonl, computes the receive→fill distribution for
the last 7 days, writes artifacts/premium_latency_report.json, and sends one
Telegram digest per run.

The auto-escalation trigger was RETIRED 2026-06-24 (see premium_latency.py):
receive→fill latency is limit-order price-wait, not a pipeline fault, so the old
``p95 > 20min`` trigger was a daily false alarm. This run no longer writes
artifacts/p2_11_trigger.json; instead it CLEARS any leftover marker. Real
pipeline outages are caught by kai-premium-healthcheck liveness, not by latency.

Exit codes:
- 0 healthy
- 1 hard error (audit read failed — journal captures)
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
        return f"{s / 60:.1f}min"
    return f"{s / 3600:.1f}h"


def _format_digest(stats) -> str:
    head = f"KAI premium-latency audit (last {stats.lookback_hours}h)"
    stale_note = (
        f"  (+{stats.stale_expired_count} stale-on-arrival excluded)"
        if getattr(stats, "stale_expired_count", 0)
        else ""
    )
    body = [
        head,
        f"samples={stats.sample_size}  expired={stats.expired_count} "
        f"({stats.expired_pct:.1f}%){stale_note}",
        f"receive→fill (informational, incl. limit-order price-wait): "
        f"p50={_fmt_seconds(stats.p50_seconds)}  "
        f"p95={_fmt_seconds(stats.p95_seconds)}  "
        f"p99={_fmt_seconds(stats.p99_seconds)}  "
        f"max={_fmt_seconds(stats.max_seconds)}",
        "ℹ️ informational only — receive→fill is limit-order price-wait, not a "
        "pipeline fault; pipeline outages are caught by kai-premium-healthcheck "
        "liveness (auto-escalation trigger retired 2026-06-24).",
    ]
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


def _clear_stale_trigger_marker() -> None:
    """Remove a leftover p2_11_trigger.json from the retired auto-trigger.

    The latency audit no longer auto-escalates (the receive→fill trigger was a
    false alarm on limit-order price-wait — see premium_latency.py). Any marker
    on disk is a stale relic of the old trigger; clear it on each run so nothing
    treats it as a live escalation.
    """
    try:
        if _TRIGGER_PATH.exists():
            _TRIGGER_PATH.unlink()
            logger.info("cleared stale retired-trigger marker %s", _TRIGGER_PATH)
    except OSError as exc:
        logger.warning("could not clear stale trigger marker: %s", exc)


def main() -> int:
    try:
        stats = compute_latency_stats()
    except Exception as exc:  # noqa: BLE001
        logger.error("compute_latency_stats failed: %s", exc)
        return 1

    _write_report(stats)
    _clear_stale_trigger_marker()
    digest = _format_digest(stats)
    print(digest)

    _send_telegram(digest)
    logger.info(
        "audit complete (informational) samples=%d fill_p95=%s expired=%d",
        stats.sample_size,
        stats.p95_seconds,
        stats.expired_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
