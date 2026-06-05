"""Convenience alias for the today-batch premium-fastlane backfill (Goal §17).

    python -m scripts.reprocess_premium_fastlane_today \
        --date 2026-06-05 \
        --symbols TAC/USDT,CLO/USDT,BEAT/USDT,4/USDT \
        --route paper --reason post_deploy_fastlane_backfill

Delegates to ``scripts.reprocess_premium_fastlane`` (same idempotent backfill).
"""

from __future__ import annotations

import sys

from scripts.reprocess_premium_fastlane import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
