"""Tests für den Paper-Writer-Freeze-Guard im Reactivate-/Enable-Pfad.

Vorfall 2026-07-12: `pi_install_systemd.sh --reactivate` (über kai_deploy) hat
den versiegelten Weg-B+-Writer-Freeze blind aufgehoben (reset-failed + restart
der inaktiven kritischen Units) → echter Paper-Trade in die kontaminierte
Alt-Epoche geleakt. Dieser Guard schließt die Ursache:

- Zentraler Shell-Helper `scripts/lib/paper_writer_freeze.sh` mit fail-CLOSED
  Semantik (Deploy darf im Zweifel KEINEN Writer anfassen — anders als der
  fail-OPEN Monitor in premium_pipeline_health).
- Guard in beiden Mutationsschleifen von `pi_install_systemd.sh`
  (reactivate_critical + ENABLE_ON_INSTALL).

Return-Codes von `paper_writer_freeze_state`:
    0  = nicht eingefroren (Marker fehlt / valides Objekt frozen=false)
    10 = eingefroren (valides Objekt frozen===true)
    20 = Marker invalid (unlesbar/kein Objekt/frozen fehlt|falscher Typ) → HOLD
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "lib" / "paper_writer_freeze.sh"
INSTALLER = REPO / "scripts" / "pi_install_systemd.sh"
_BASH = shutil.which("bash")

PROTECTED = (
    "kai-paper-trading.timer",
    "kai-real-analysis-paper-feed.timer",
    "kai-tv-auto-promote.timer",
    "kai-entry-watch.service",
)

pytestmark = pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")


def _bash(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    return subprocess.run(  # noqa: S603 — fixed interpreter, test-controlled script
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def _marker(tmp_path: Path, content: str | None) -> dict[str, str]:
    """Return env dict pointing the helper at a marker with `content` (or absent)."""
    path = tmp_path / "paper_writer_freeze.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    return {"PAPER_WRITER_FREEZE_MARKER": str(path)}


def _state(tmp_path: Path, content: str | None) -> int:
    res = _bash(
        f'source "{HELPER}"; paper_writer_freeze_state; echo "RC=$?"',
        env=_marker(tmp_path, content),
    )
    assert "RC=" in res.stdout, f"stdout={res.stdout!r} stderr={res.stderr!r}"
    return int(res.stdout.strip().rsplit("RC=", 1)[-1])


# --------------------------------------------------------------------------- #
# Helper-Ebene: Marker-Klassifikation
# --------------------------------------------------------------------------- #


def test_state_no_marker_is_not_frozen(tmp_path: Path) -> None:
    assert _state(tmp_path, None) == 0


def test_state_frozen_true(tmp_path: Path) -> None:
    assert _state(tmp_path, '{"frozen": true, "reason": "weg_b_plus_epoch_reset"}') == 10


def test_state_frozen_false_is_not_frozen(tmp_path: Path) -> None:
    assert _state(tmp_path, '{"frozen": false}') == 0


def test_state_corrupt_json_is_invalid(tmp_path: Path) -> None:
    assert _state(tmp_path, "{not valid json,,,") == 20


def test_state_list_instead_of_object_is_invalid(tmp_path: Path) -> None:
    assert _state(tmp_path, '["frozen", true]') == 20


def test_state_frozen_missing_is_invalid(tmp_path: Path) -> None:
    # fail-closed: Objekt ohne frozen-Feld ist mehrdeutig → HOLD (nicht "not frozen")
    assert _state(tmp_path, '{"reason": "x"}') == 20


def test_state_frozen_wrong_type_is_invalid(tmp_path: Path) -> None:
    assert _state(tmp_path, '{"frozen": "true"}') == 20


def test_protected_unit_list_is_exactly_the_four_writers(tmp_path: Path) -> None:
    res = _bash(f'source "{HELPER}"; printf "%s\\n" "${{PAPER_WRITER_PROTECTED_UNITS[@]}}"')
    got = tuple(line for line in res.stdout.splitlines() if line.strip())
    assert got == PROTECTED


def test_is_protected_predicate(tmp_path: Path) -> None:
    ok = _bash(f'source "{HELPER}"; paper_writer_is_protected kai-paper-trading.timer')
    assert ok.returncode == 0
    nok = _bash(f'source "{HELPER}"; paper_writer_is_protected kai-server.service')
    assert nok.returncode == 1


# --------------------------------------------------------------------------- #
# Integration: reactivate_critical mit gestubbtem systemctl
# --------------------------------------------------------------------------- #


def _stub_systemctl(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """PATH with a stateful systemctl stub that logs calls.

    A unit is 'inactive' until it is (re)started, then 'active' — so the
    reactivate hook can succeed for non-frozen/non-writer units and return 0.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "systemctl_calls.log"
    stub = bindir / "systemctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        f'STATE="{state}"\n'
        'case "$1" in\n'
        "  is-active)\n"
        '    if [ -e "$STATE/$2.active" ]; then echo active; exit 0; '
        "else echo inactive; exit 3; fi ;;\n"
        '  start|restart) : > "$STATE/$2.active"; exit 0 ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
    return env, log


def _run_reactivate(
    tmp_path: Path, marker_content: str | None, *, dry_run: bool = False
) -> tuple[subprocess.CompletedProcess[str], str]:
    env, log = _stub_systemctl(tmp_path)
    env.update(_marker(tmp_path, marker_content))
    pre = "DRY_RUN=1;" if dry_run else "DRY_RUN=0;"
    # if-Bedingung fängt den Return-Code ohne set -e auszulösen.
    res = _bash(
        f'source "{INSTALLER}"; {pre} '
        'if reactivate_critical; then rc=0; else rc=$?; fi; echo "RC=$rc"',
        env=env,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return res, calls


def _rc(res: subprocess.CompletedProcess[str]) -> int:
    return int(res.stdout.strip().rsplit("RC=", 1)[-1])


def test_reactivate_not_frozen_restarts_writers_normally(tmp_path: Path) -> None:
    res, calls = _run_reactivate(tmp_path, None)
    # Ohne Freeze läuft der Hook unverändert: inaktive Writer werden restartet.
    assert "restart kai-paper-trading.timer" in calls
    assert "restart kai-entry-watch.service" in calls


def test_reactivate_frozen_skips_all_four_writers(tmp_path: Path) -> None:
    res, calls = _run_reactivate(tmp_path, '{"frozen": true}')
    assert "PAPER_WRITER_FREEZE_ACTIVE" in res.stdout
    for unit in PROTECTED:
        assert unit not in calls, f"frozen writer touched: {unit}\ncalls={calls!r}"


def test_reactivate_frozen_still_processes_non_writers(tmp_path: Path) -> None:
    res, calls = _run_reactivate(tmp_path, '{"frozen": true}')
    # kai-server ist kein geschützter Writer → wird weiter geprüft/restartet.
    assert "restart kai-server.service" in calls


def test_reactivate_invalid_marker_aborts_before_any_mutation(tmp_path: Path) -> None:
    res, calls = _run_reactivate(tmp_path, "{corrupt")
    assert _rc(res) != 0
    assert "PAPER_WRITER_FREEZE_MARKER_INVALID" in (res.stdout + res.stderr)
    assert calls.strip() == "", f"mutation happened despite invalid marker: {calls!r}"


def test_reactivate_dry_run_frozen_shows_skip_and_mutates_nothing(tmp_path: Path) -> None:
    res, calls = _run_reactivate(tmp_path, '{"frozen": true}', dry_run=True)
    assert "PAPER_WRITER_FREEZE_ACTIVE" in res.stdout
    for unit in PROTECTED:
        assert f"restart {unit}" not in calls


def test_incident_regression_frozen_deploy_touches_no_writer(tmp_path: Path) -> None:
    """Regression zum 2026-07-12-Leak: aktiver Freeze + Reactivate darf KEINEN
    der vier Writer starten/restarten/reset-failen — sonst schreibt ein Writer
    erneut in die kontaminierte Alt-Epoche."""
    res, calls = _run_reactivate(tmp_path, '{"frozen": true, "reason": "epoch_reset"}')
    assert _rc(res) == 0
    for verb in ("restart", "reset-failed", "enable", "start", "unmask"):
        for unit in PROTECTED:
            assert f"{verb} {unit}" not in calls
