#!/usr/bin/env python
"""Run the daily recalc steps best-effort, but fail the unit if any step crashed.

Replaces the five ``ExecStart=-`` lines in ``kai-recalc-cycle.service``. The ``-``
prefix told systemd to ignore each step's exit code so one failing step would not
abort the rest — but it also made the *whole unit* report ``success`` even when a
step crashed. On 2026-06-24 a circular-import crash in ``bayes_posterior_recalc.py``
went completely silent this way: the unit stayed green while
``artifacts/bayes_posterior_state.json`` went stale, and only ``kai-health-check``
caught it ~31h later (exit 2, stale data).

This wrapper keeps the independence the ``-`` gave (every step runs even if an
earlier one fails — a stale source-reliability recalc must not block the
bayes/ph5 refresh) but aggregates the exit codes: if any step crashed the wrapper
exits non-zero, so the unit is marked ``failed`` and shows up in
``systemctl --failed`` immediately — no 25h stale-artifact delay.

Each step's own stdout/stderr is inherited so it still flows to the journal.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Ordered exactly as the previous unit ran them. source_reliability /
# source_lifecycle feed source_confluence; bayes + ph5 are independent tails.
RECALC_STEPS: tuple[str, ...] = (
    "source_reliability_recalc.py",
    "source_lifecycle_recalc.py",
    # Phase 1: transponiert das frische Ranking in den DB-Status (Dry-Run, solange
    # SOURCE_LIFECYCLE_APPLY_ENABLED nicht gesetzt). Direkt nach dem Lifecycle-
    # Recalc, damit es auf der eben geschriebenen source_ranking.json arbeitet.
    "source_lifecycle_apply.py",
    "source_confluence_recalc.py",
    "bayes_posterior_recalc.py",
    "ph5_feature_analysis_recalc.py",
)


def run_recalc_steps(
    steps: tuple[str, ...],
    *,
    python_exe: str,
    scripts_dir: Path,
    cwd: Path,
) -> list[tuple[str, int]]:
    """Run each step best-effort; return ``[(step, returncode), …]`` in order.

    A non-zero returncode does NOT abort the remaining steps — that independence
    is the whole reason the unit used ``ExecStart=-`` per line. Each child
    inherits stdout/stderr so its output still reaches the journal.
    """
    results: list[tuple[str, int]] = []
    for step in steps:
        proc = subprocess.run([python_exe, str(scripts_dir / step)], cwd=str(cwd))  # noqa: S603
        results.append((step, proc.returncode))
    return results


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    results = run_recalc_steps(
        RECALC_STEPS,
        python_exe=sys.executable,
        scripts_dir=root / "scripts",
        cwd=root,
    )
    summary = " ".join(f"{step}={rc}" for step, rc in results)
    print(f"recalc-cycle: {summary}", flush=True)

    failed = [step for step, rc in results if rc != 0]
    if failed:
        print(
            f"recalc-cycle: {len(failed)} step(s) FAILED: {', '.join(failed)} — "
            "unit marked failed (all steps still ran).",
            file=sys.stderr,
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
