"""SessionStart-Hook: Zeigt Daily-Review-Status.

Druckt eine Zeile zum aktuellen Daily-Review-Status. Erinnert den Agent/Operator,
wenn seit > 20h kein Review mehr lief. Non-blocking, non-disruptive.

Exit-Codes:
  0 — immer (Hook soll nie failen)
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAST_RUN = REPO_ROOT / "artifacts" / "agents" / "daily_review" / "last_run.json"


def main() -> int:
    if not LAST_RUN.exists():
        print(
            "[daily-review] Noch kein Review heute vorhanden. "
            "Skill `daily-strategy-review` ausführen für Lagebild."
        )
        return 0

    try:
        data = json.loads(LAST_RUN.read_text(encoding="utf-8"))
        ts_str = data.get("ts")
        if not ts_str:
            print("[daily-review] last_run.json ohne ts — Skill erneut ausführen.")
            return 0

        last = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        age = datetime.now(UTC) - last
        if age > timedelta(hours=20):
            hours = int(age.total_seconds() // 3600)
            p0 = data.get("p0_count", "?")
            print(
                f"[daily-review] Letzter Review vor {hours}h. "
                f"P0-Tasks offen: {p0}. "
                f"Skill `daily-strategy-review` erneut ausführen."
            )
        else:
            hours = int(age.total_seconds() // 3600)
            print(
                f"[daily-review] Letzter Review vor {hours}h — aktuell. "
                f"P0: {data.get('p0_count', '?')}, "
                f"P1: {data.get('p1_count', '?')}."
            )
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f"[daily-review] status check failed: {exc.__class__.__name__}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
