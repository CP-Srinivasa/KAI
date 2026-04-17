"""Stop-Hook: Loggt Session-Summary in artifacts/session_log.jsonl.

Schreibt einen leichten JSONL-Eintrag pro Session-Ende mit git-status-Snapshot.
Dient als Langzeit-Historie für Architect/Watchdog und als Input für daily-review.

Exit-Codes:
  0 — immer (Hook soll nie failen)
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG = REPO_ROOT / "artifacts" / "session_log.jsonl"


def _git(args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def main() -> int:
    try:
        status = _git(["status", "--short"])
        head = _git(["rev-parse", "--short", "HEAD"])
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        modified = len([ln for ln in status.splitlines() if ln])

        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "head": head,
            "branch": branch,
            "modified_files": modified,
        }
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — Hook darf nie failen
        print(f"[session-summary] {exc.__class__.__name__}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
