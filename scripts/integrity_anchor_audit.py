#!/usr/bin/env python3
"""Daily L3 audit-integrity anchor run (KAI L3, default-off).

Executes ``app.integrity.anchor_audit_digest`` once: computes the deterministic
digest over the configured audit SSOT (``APP_INTEGRITY_AUDIT_PATHS``) and, with
``stamper=opentimestamps``, submits it to an OpenTimestamps calendar — proof that
KAI's records are unaltered. Writes an ``audit-<digest>.json`` record (+ ``.ots``
proof) under ``APP_INTEGRITY_PROOFS_DIR``; the read-only ``/dashboard/api/integrity``
surface then reports it.

This is the missing EXECUTION piece: the action + the read surface already
existed, but nothing ran the action on a schedule, so the surface stayed
``no_anchor``. Meant for a daily systemd timer.

Default-off: with ``APP_INTEGRITY_ENABLED`` unset/false it is a no-op (exit 0).
No capital path. Exit codes: 0 = disabled/recorded/anchored, 1 = hard error
(e.g. ``stamper=opentimestamps`` but the optional library is missing).
"""

from __future__ import annotations

import sys

from app.core.integrity_settings import IntegritySettings
from app.integrity import anchor_audit_digest


def main(cfg: IntegritySettings | None = None) -> int:
    if cfg is None:
        from app.core.settings import get_settings

        cfg = get_settings().integrity

    res = anchor_audit_digest(cfg)
    if res.state == "disabled":
        print("integrity-anchor: disabled (no-op) — set APP_INTEGRITY_ENABLED=true to anchor")
        return 0
    if res.state == "error":
        print(f"integrity-anchor: ERROR — {res.reason}")
        return 1
    if res.state == "anchored":
        print(f"integrity-anchor: anchored digest={res.digest[:16]}… proof={res.proof_path}")
        return 0
    print(f"integrity-anchor: recorded digest={res.digest[:16]}… (no OTS proof — stamper=null)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
