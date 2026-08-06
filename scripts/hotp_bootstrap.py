#!/usr/bin/env python
"""Explicitly initialize KAI's HOTP counter journal without consuming a code.

The command never overwrites an existing path. ``--next-counter`` is required
so commissioning (usually 0) and evidence-backed recovery are both deliberate.
It does not read the seed and cannot arm or execute a payment.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.settings import get_settings
from app.security.hotp_auth import HotpError, bootstrap_hotp_journal


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicitly bootstrap the HOTP counter journal")
    parser.add_argument(
        "--journal-path",
        default=None,
        help="Journal path (default: APP_LN_HOTP_JOURNAL_PATH)",
    )
    parser.add_argument(
        "--next-counter",
        required=True,
        type=int,
        help="Authenticator counter expected on the next verification (first setup: 0)",
    )
    args = parser.parse_args()

    configured = args.journal_path or get_settings().lightning.hotp_journal_path
    journal_path = Path(configured)
    try:
        timestamp = bootstrap_hotp_journal(journal_path, next_counter=args.next_counter)
    except (HotpError, ValueError) as exc:
        print(f"HOTP journal bootstrap denied: {exc}", file=sys.stderr)
        return 2

    print(
        f"HOTP journal bootstrapped: {journal_path} next_counter={args.next_counter} at {timestamp}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
