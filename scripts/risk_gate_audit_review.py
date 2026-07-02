#!/usr/bin/env python3
"""Pi-local one-shot risk-gate audit-window review (2026-06-04 window).

Runs WHERE the data lives (the Pi): reads the live audit/bridge artifacts,
builds a read-only enforce-readiness verdict, writes
``artifacts/risk_gate_audit_review_<date>.{json,md}`` and sends ONE short
Telegram digest. It NEVER changes any config (no enforce, no entry_mode flip,
no orders/fills).

Empty data is a STATUS (NO_DATA / INSUFFICIENT_DATA), not a failure — exit 0.
Exit 1 only on a hard error (import/IO), captured by journalctl.

Wired by deploy/systemd/kai-risk-gate-audit-review.{service,timer}.
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

from app.observability.risk_gate_audit_review import (  # noqa: E402
    build_review,
    render_markdown,
    render_telegram,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("risk-gate-audit-review")


def _send_telegram(text: str) -> bool:
    token = os.environ.get("ALERT_TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("ALERT_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("ALERT_TELEGRAM_TOKEN/CHAT_ID missing — printing only")
        return False
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return bool(resp.status == 200)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram send failed: %s", exc)
        return False


def main() -> int:
    # Read CURRENT config to record the preconditions — read-only, never set.
    entry_mode = gates_mode = None
    max_lev = min_rr = None
    try:
        from app.core.settings import get_settings

        s = get_settings()
        entry_mode = str(s.execution.entry_mode)
        gates_mode = str(s.risk.gates_mode)
        max_lev = float(s.risk.max_leveraged_risk_pct)
        min_rr = float(s.risk.min_rr)
    except Exception as exc:  # noqa: BLE001
        logger.warning("settings read failed (preconditions left null): %s", exc)

    try:
        verdict = build_review(
            entry_mode=entry_mode,
            gates_mode=gates_mode,
            max_leveraged_risk_pct=max_lev,
            min_rr=min_rr,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("build_review failed: %s", exc)
        return 1

    date_tag = datetime.now(UTC).strftime("%Y%m%d")
    art = _REPO_ROOT / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / f"risk_gate_audit_review_{date_tag}.json").write_text(
        json.dumps(verdict.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md = render_markdown(verdict)
    (art / f"risk_gate_audit_review_{date_tag}.md").write_text(md, encoding="utf-8")
    print(md)

    _send_telegram(render_telegram(verdict))
    logger.info(
        "review complete status=%s n=%d would_reject=%d enforce_ready=%s",
        verdict.status,
        verdict.n_evaluated,
        verdict.would_reject_count,
        verdict.enforce_ready,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
