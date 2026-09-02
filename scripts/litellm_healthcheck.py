"""Dependency-free local health probe for kai-litellm.service."""

from __future__ import annotations

import argparse
import os
import time
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:4000")
    parser.add_argument("--retries", type=int, default=1)
    args = parser.parse_args()
    headers = {}
    master_key = os.environ.get("LITELLM_MASTER_KEY", "")
    if master_key:
        headers["Authorization"] = f"Bearer {master_key}"
    paths = ("/health/liveliness", "/health")
    for attempt in range(max(1, args.retries)):
        for path in paths:
            request = urllib.request.Request(f"{args.url.rstrip('/')}{path}", headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310
                    if 200 <= response.status < 400:
                        return 0
            except (urllib.error.URLError, TimeoutError):
                continue
        if attempt + 1 < max(1, args.retries):
            time.sleep(1)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
