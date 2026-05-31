"""Backfill premium JSONL audit trails into the SQLite event store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.observability.premium_event_store_backfill import (
    DEFAULT_BRIDGE_LOG,
    DEFAULT_ENVELOPE_LOG,
    backfill_event_store,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelope-log", type=Path, default=DEFAULT_ENVELOPE_LOG)
    parser.add_argument("--bridge-log", type=Path, default=DEFAULT_BRIDGE_LOG)
    parser.add_argument("--store-path", type=Path, default=None)
    args = parser.parse_args()

    summary = backfill_event_store(
        envelope_log=args.envelope_log,
        bridge_log=args.bridge_log,
        store_path=args.store_path,
    )
    print(json.dumps(summary.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
