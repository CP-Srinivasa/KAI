#!/usr/bin/env python3
"""Periodic L3 OTS proof upgrade (KAI L3, default-off).

The daily anchor (``scripts/integrity_anchor_audit.py``) writes PENDING ``.ots``
proofs: a calendar commitment that is not yet Bitcoin-mined. This runner does the
asynchronous second half — it re-queries each pending calendar and upgrades any
proof whose aggregation has since been mined into a real Bitcoin attestation,
rewriting the ``.ots`` in place. Meant for a periodic systemd timer (mining lands
HOURS after submission, so it must run repeatedly, not once).

Default-off: a no-op (exit 0) unless ``APP_INTEGRITY_ENABLED=true`` AND
``APP_INTEGRITY_STAMPER=opentimestamps``. Read-only w.r.t. KAI's audit SSOT, no
capital path. Exit codes: 0 = disabled/ran, 1 = hard error (opentimestamps
library missing — run ``pip install -e .``).
"""

from __future__ import annotations

import sys

from app.core.integrity_settings import IntegritySettings
from app.integrity.anchor import AnchorUnavailableError
from app.integrity.upgrade import upgrade_pending_proofs


def main(cfg: IntegritySettings | None = None) -> int:
    if cfg is None:
        from app.core.settings import get_settings

        cfg = get_settings().integrity

    if not cfg.enabled:
        print("integrity-ots-upgrade: disabled (no-op) — set APP_INTEGRITY_ENABLED=true")
        return 0
    if cfg.stamper != "opentimestamps":
        print(
            f"integrity-ots-upgrade: stamper={cfg.stamper} (no-op) — "
            "needs APP_INTEGRITY_STAMPER=opentimestamps"
        )
        return 0

    try:
        report = upgrade_pending_proofs(cfg.proofs_dir)
    except AnchorUnavailableError as exc:
        print(f"integrity-ots-upgrade: ERROR — {exc}")
        return 1

    print(
        "integrity-ots-upgrade: "
        f"scanned={report.scanned} upgraded={report.upgraded} "
        f"confirmed_already={report.already_confirmed} "
        f"still_pending={report.still_pending} failed={report.failed}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
