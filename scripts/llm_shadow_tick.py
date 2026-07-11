#!/usr/bin/env python
"""Shadow tick of the Local Intelligence Layer (ADR 0015 Phase 2).

Thin systemd/cron entrypoint: when the layer is enabled, summarize today's
daily-strategy review into the audit trail + a marked shadow note under
artifacts/. Flag-gated no-op when disabled (exit 0) — safe to install anywhere.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from app.intelligence.settings import get_llm_settings
    from app.intelligence.use_cases import daily_review_summary, render_untrusted_block

    settings = get_llm_settings()
    if not settings.enabled or settings.mode != "shadow":
        print("llm_shadow_tick: disabled (KAI_LLM_ENABLED/MODE) — no-op")
        return 0

    today = datetime.now(UTC).date().isoformat()
    review = REPO_ROOT / "artifacts" / "daily_strategy" / f"{today}.md"
    if not review.exists():
        print(f"llm_shadow_tick: no review for {today} — no-op")
        return 0

    result = daily_review_summary(f"artifacts/daily_strategy/{today}.md", workspace_root=REPO_ROOT)
    out_dir = REPO_ROOT / "artifacts" / "llm_shadow_notes"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{today}_daily_summary.md").write_text(
        render_untrusted_block(result) + "\n", encoding="utf-8"
    )
    print(f"llm_shadow_tick: ok={result.ok} provider={result.provider}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
