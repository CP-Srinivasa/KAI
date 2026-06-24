"""Unit tests for scripts/recalc_cycle.py — the fail-visible recalc wrapper.

Regression intent (2026-06-24): the old ``ExecStart=-`` lines let a crashed
recalc step pass as unit success. The wrapper must (a) run EVERY step even when
an earlier one fails (independence), and (b) report failure when any step
returned non-zero.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "recalc_cycle.py"
_spec = importlib.util.spec_from_file_location("recalc_cycle", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
recalc_cycle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recalc_cycle)


def _write_step(scripts_dir: Path, name: str, exit_code: int) -> None:
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / name).write_text(f"import sys\nsys.exit({exit_code})\n", encoding="utf-8")


def test_runs_all_steps_and_collects_returncodes_in_order(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    _write_step(scripts_dir, "a.py", 0)
    _write_step(scripts_dir, "b.py", 0)

    results = recalc_cycle.run_recalc_steps(
        ("a.py", "b.py"),
        python_exe=sys.executable,
        scripts_dir=scripts_dir,
        cwd=tmp_path,
    )

    assert results == [("a.py", 0), ("b.py", 0)]


def test_failing_step_does_not_abort_later_steps(tmp_path: Path) -> None:
    """Independence: a crashed step (rc=3) must NOT stop the step after it."""
    scripts_dir = tmp_path / "scripts"
    _write_step(scripts_dir, "ok_first.py", 0)
    _write_step(scripts_dir, "boom.py", 3)
    _write_step(scripts_dir, "ok_last.py", 0)

    results = recalc_cycle.run_recalc_steps(
        ("ok_first.py", "boom.py", "ok_last.py"),
        python_exe=sys.executable,
        scripts_dir=scripts_dir,
        cwd=tmp_path,
    )

    # All three ran (the boom in the middle did not abort ok_last), and the
    # failing returncode is surfaced, not swallowed.
    assert results == [("ok_first.py", 0), ("boom.py", 3), ("ok_last.py", 0)]
    assert any(rc != 0 for _, rc in results)
