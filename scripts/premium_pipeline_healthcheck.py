#!/usr/bin/env python3
"""Premium-Signal-Pipeline-Healthcheck — cron-driven Telegram-alert push.

Runs from ``kai-premium-healthcheck.timer`` every 60 s. On FAIL it sends a
single Telegram message to ``ALERT_TELEGRAM_CHAT_ID`` summarising the
failure_modes plus per-check details, then exits 1 so the journal records
the FAIL. On OK it exits 0 silently.

2026-05-16: auto-reprocess pre-step. Before health is computed the script
calls ``envelope_to_paper_bridge.run_tick()`` so that re-pending envelopes
(e.g. an auto-fill where the first market-data lookup returned None for an
exotic token) get re-processed without an operator clicking "Reprocess
Bridge". Root cause of the 2026-05-14 BAS/USDT 10h17m fill-delay. The tick
is idempotent and returns early when there is nothing pending; cost on a
quiet bus is one JSONL scan. Disable via ``KAI_HEALTHCHECK_AUTO_REPROCESS=0``.

No throttle by design — operator opted for "alert on every failing tick"
on 2026-05-14. If that becomes too noisy, gate by an env-toggle here.

Stdout structure on FAIL:
    KAI premium-pipeline FAIL @ <iso>
    failure_modes: a, b, c
    - check_name: detail
    - ...
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make the repo root importable when invoked as ``python scripts/...py``
# (systemd unit's WorkingDirectory already sets cwd, but be defensive).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import logging  # noqa: E402 — imports follow the sys.path bootstrap above
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

from app.execution.envelope_to_paper_bridge import run_tick  # noqa: E402
from app.observability.premium_pipeline_health import compute_pipeline_health  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("premium-healthcheck")


def _format_alert(report) -> str:
    lines = [
        f"KAI premium-pipeline FAIL @ {report.timestamp_utc}",
        f"failure_modes: {', '.join(report.failure_modes)}",
        "",
    ]
    for check in report.checks:
        marker = "OK  " if check.ok else "FAIL"
        age = f" age={check.age_seconds:.1f}s" if check.age_seconds is not None else ""
        lines.append(f"{marker} {check.name}: {check.detail}{age}")
    lines.append("")
    lines.append("Next: sudo bash scripts/pi_install_systemd.sh --reactivate")
    return "\n".join(lines)


def _send_telegram(text: str) -> bool:
    token = os.environ.get("ALERT_TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("ALERT_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("ALERT_TELEGRAM_TOKEN/CHAT_ID missing — skipping send")
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


def _auto_reprocess_pending() -> None:
    """Run one bridge tick to clear stuck pending envelopes (best-effort).

    Why: when the first auto-fill bridge tick happens at signal-receive time
    and market-data is briefly unavailable for an exotic token, the envelope
    goes to ``re_pending`` and stays there until an operator clicks the
    "Reprocess Bridge" button in the Portfolio UI. The healthcheck timer
    runs every 60s anyway — letting it nudge the bridge closes that gap.

    Failure-mode: any exception inside ``run_tick`` is logged at WARNING
    and swallowed. The healthcheck must still proceed to ``compute_pipeline_health``
    so an alert fires if the bridge itself is broken. We deliberately do not
    raise — the next tick will retry, and a persistent failure shows up in
    the health-report (which counts ``re_pending`` age separately).
    """
    if os.environ.get("KAI_HEALTHCHECK_AUTO_REPROCESS", "1") == "0":
        return
    try:
        result = asyncio.run(run_tick())
    except Exception as exc:  # noqa: BLE001 — must not crash healthcheck
        logger.warning("auto-reprocess tick failed: %s", exc)
        return
    if not result.enabled:
        logger.debug("auto-reprocess skipped: bridge disabled")
        return
    if result.envelopes_scanned == 0:
        return
    logger.info(
        "auto-reprocess tick scanned=%d filled=%d re_pending=%d expired=%d errors=%d",
        result.envelopes_scanned,
        result.filled,
        result.re_pending,
        result.expired,
        len(result.errors),
    )


def main() -> int:
    _auto_reprocess_pending()
    report = compute_pipeline_health()
    if report.healthy:
        logger.info("OK %s checks=%d", report.timestamp_utc, len(report.checks))
        return 0

    text = _format_alert(report)
    print(text)
    sent = _send_telegram(text)
    logger.warning(
        "FAIL failure_modes=%s telegram_sent=%s",
        report.failure_modes,
        sent,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
