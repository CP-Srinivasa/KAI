"""Host-Hygiene-Sonde (MindBlow 2.0, E10/U-2): Checkout, Neustart, ersetzte Bibliotheken.

Der Anlass steht im Modul: am 25.09.2026 haette eine untracked Datei im
Pi-Checkout den Release-Lauf abgebrochen, und seit ``50-kai.conf`` koennen
Sicherheitsupdates installiert, aber unwirksam sein. Die Tests bauen die Lagen
echt nach (git-Repo, Reboot-Marker, /proc-Karten), statt die Sonde zu mocken.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.alerts import health_check_host as hch

_DAY = 86400.0
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git nicht installiert")


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout


@pytest.fixture
def pi_checkout(tmp_path: Path) -> Path:
    """Upstream versioniert ``scripts/arm.sh``; der Pi-Checkout steht einen Commit dahinter."""
    up = tmp_path / "up"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    (up / "README.md").write_text("a\n", encoding="utf-8")
    _git(up, "add", ".")
    _git(up, "commit", "-q", "-m", "c1")
    (up / "scripts").mkdir()
    (up / "scripts" / "arm.sh").write_text("#!/bin/bash\necho versioniert\n", encoding="utf-8")
    _git(up, "add", ".")
    _git(up, "commit", "-q", "-m", "c2")
    pi = tmp_path / "pi"
    _git(tmp_path, "clone", "-q", str(up), str(pi))
    _git(pi, "reset", "-q", "--hard", "HEAD~1")
    return pi


# ── Checkout-Hygiene (E10) ─────────────────────────────────────────────────


@needs_git
def test_clean_checkout_is_silent(pi_checkout: Path) -> None:
    assert hch.checkout_findings(pi_checkout) == []


@needs_git
def test_tracked_change_is_reported(pi_checkout: Path) -> None:
    (pi_checkout / "README.md").write_text("lokal geaendert\n", encoding="utf-8")
    findings = hch.checkout_findings(pi_checkout)
    assert len(findings) == 1
    assert "getrackte Aenderung" in findings[0] and "README.md" in findings[0]


@needs_git
def test_untracked_file_versioned_upstream_blocks_fast_forward(pi_checkout: Path) -> None:
    # Befund N1 vom 25.09.: dieselbe Datei liegt lokal untracked und upstream versioniert.
    (pi_checkout / "scripts").mkdir()
    (pi_checkout / "scripts" / "arm.sh").write_text("#!/bin/bash\necho lokal\n", encoding="utf-8")
    findings = hch.checkout_findings(pi_checkout)
    assert len(findings) == 1
    assert "origin/main" in findings[0] and "scripts/" in findings[0]
    assert "--ff-only" in findings[0]
    # Gegenprobe: genau diese Lage laesst den Fast-Forward wirklich scheitern.
    with pytest.raises(subprocess.CalledProcessError):
        _git(pi_checkout, "merge", "--ff-only", "origin/main")


@needs_git
def test_untracked_file_unknown_upstream_is_silent(pi_checkout: Path) -> None:
    (pi_checkout / "notizen.txt").write_text("harmlos\n", encoding="utf-8")
    assert hch.checkout_findings(pi_checkout) == []


def test_no_git_checkout_is_silent(tmp_path: Path) -> None:
    assert hch.checkout_findings(tmp_path) == []


# ── Ausstehender Neustart (U-2) ────────────────────────────────────────────


def _marker(tmp_path: Path, age_days: float, pkgs: str = "") -> tuple[Path, Path, float]:
    marker = tmp_path / "reboot-required"
    marker.write_text("*** System restart required ***\n", encoding="utf-8")
    now = time.time()
    os.utime(marker, (now - age_days * _DAY, now - age_days * _DAY))
    pkg_file = tmp_path / "reboot-required.pkgs"
    if pkgs:
        pkg_file.write_text(pkgs, encoding="utf-8")
    return marker, pkg_file, now


def test_no_reboot_marker_is_silent(tmp_path: Path) -> None:
    assert hch.reboot_pending_finding(marker=tmp_path / "fehlt", pkgs=tmp_path / "fehlt2") is None


def test_fresh_reboot_marker_waits_for_the_window(tmp_path: Path) -> None:
    marker, pkgs, now = _marker(tmp_path, age_days=2)
    assert hch.reboot_pending_finding(marker=marker, pkgs=pkgs, now=now) is None


def test_old_reboot_marker_is_reported_with_packages(tmp_path: Path) -> None:
    pkg_lines = "linux-image-6.8.0-1065-raspi\nlinux-base\nlinux-base\n"
    marker, pkgs, now = _marker(tmp_path, 8, pkg_lines)
    msg = hch.reboot_pending_finding(marker=marker, pkgs=pkgs, now=now)
    assert msg is not None
    assert "seit 8 Tagen" in msg
    assert "linux-image-6.8.0-1065-raspi" in msg and msg.count("linux-base") == 1


# ── Ersetzte Bibliotheken im Speicher (U-2) ────────────────────────────────


def _proc_with_maps(tmp_path: Path, pid: int, lines: list[str]) -> Path:
    proc = tmp_path / "proc"
    (proc / str(pid)).mkdir(parents=True)
    (proc / str(pid) / "maps").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return proc


def _maps_line(path: Path | str) -> str:
    return f"7f3a00000000-7f3a00020000 r-xp 00000000 b3:02 131245 {path} (deleted)"


def _systemctl_pid(pid: int) -> hch.Runner:
    return lambda cmd: f"{pid}\n" if list(cmd)[:2] == ["systemctl", "show"] else None


def test_replaced_library_older_than_window_is_reported(tmp_path: Path) -> None:
    lib = tmp_path / "libexpat.so.1.8.10"
    lib.write_bytes(b"\x7fELF-ersatz")  # die Ersatzdatei am selben Pfad
    proc = _proc_with_maps(
        tmp_path,
        4242,
        [
            _maps_line(lib),
            _maps_line("/memfd:python"),  # kein .so -> ignoriert
            _maps_line(tmp_path / "libweg.so.3"),  # Ersatz fehlt -> Alter unbelegt
            "7f3a00020000-7f3a00030000 r-xp 00000000 b3:02 1 /usr/lib/libc.so.6",
        ],
    )
    msg = hch.stale_library_finding(
        tmp_path,
        units=["kai-server.service"],
        run=_systemctl_pid(4242),
        proc_root=proc,
        now=time.time() + 8 * _DAY,
    )
    assert msg is not None
    assert "kai-server: libexpat.so.1.8.10" in msg
    assert "libweg" not in msg and "memfd" not in msg and "libc.so.6" not in msg


def test_recently_replaced_library_waits_for_the_window(tmp_path: Path) -> None:
    lib = tmp_path / "libcurl.so.4"
    lib.write_bytes(b"\x7fELF")
    proc = _proc_with_maps(tmp_path, 7, [_maps_line(lib)])
    assert (
        hch.stale_library_finding(
            tmp_path, units=["kai-litellm.service"], run=_systemctl_pid(7), proc_root=proc
        )
        is None
    )


def test_unreadable_or_stopped_process_is_silent(tmp_path: Path) -> None:
    # MainPID 0 (Dienst gestoppt) und fehlende maps-Datei sind kein Befund.
    assert (
        hch.stale_library_finding(
            tmp_path, units=["kai-server.service"], run=_systemctl_pid(0), proc_root=tmp_path
        )
        is None
    )
    assert (
        hch.stale_library_finding(
            tmp_path, units=["kai-server.service"], run=_systemctl_pid(99), proc_root=tmp_path
        )
        is None
    )


# ── Offsite-Beleg (E3) ─────────────────────────────────────────────────────


def _receipt(gen_age_days: float, *, probe: str = "PASS", now: float) -> str:
    gen_ts = datetime.fromtimestamp(now - gen_age_days * _DAY, tz=UTC)
    return json.dumps(
        {
            "schema": "offpi_receipt/v1",
            "ts_utc": datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generation": gen_ts.strftime("%Y-%m-%dT%H-%M-%SZ"),
            "generation_ts_utc": gen_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "probe": probe,
        }
    )


def _write_receipts(adir: Path, lines: list[str]) -> None:
    target = adir / hch.OFFPI_RECEIPTS_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_missing_receipts_mean_no_verified_offsite_copy(tmp_path: Path) -> None:
    msg = hch.offpi_backup_finding(tmp_path)
    assert msg is not None and "Keine verifizierte Offsite-Kopie" in msg


def test_fresh_verified_generation_is_silent(tmp_path: Path) -> None:
    now = time.time()
    _write_receipts(tmp_path, [_receipt(1, now=now)])
    assert hch.offpi_backup_finding(tmp_path, now=now) is None


def test_old_verified_generation_is_reported(tmp_path: Path) -> None:
    now = time.time()
    _write_receipts(tmp_path, [_receipt(9, now=now)])
    msg = hch.offpi_backup_finding(tmp_path, now=now)
    assert msg is not None and "9 Tage alt" in msg and "KAI Backup" in msg


def test_reprobing_an_old_generation_does_not_make_it_fresh(tmp_path: Path) -> None:
    # Die Quittung ist von heute (ts_utc), die Generation aber 10 Tage alt.
    now = time.time()
    _write_receipts(tmp_path, [_receipt(10, now=now)])
    assert "10 Tage alt" in (hch.offpi_backup_finding(tmp_path, now=now) or "")


def test_failed_probes_foreign_schema_and_garbage_are_not_evidence(tmp_path: Path) -> None:
    now = time.time()
    foreign = json.dumps({"schema": "anderes/v1", "probe": "PASS", "generation_ts_utc": "x"})
    _write_receipts(tmp_path, [_receipt(1, probe="FAIL", now=now), foreign, "{kaputt"])
    msg = hch.offpi_backup_finding(tmp_path, now=now)
    assert msg is not None and "Keine verifizierte Offsite-Kopie" in msg


def test_newest_generation_wins(tmp_path: Path) -> None:
    now = time.time()
    lines = [_receipt(20, now=now), _receipt(2, now=now), _receipt(15, now=now)]
    _write_receipts(tmp_path, lines)
    assert hch.offpi_backup_finding(tmp_path, now=now) is None


# ── Aufrufstelle ───────────────────────────────────────────────────────────


def test_check_is_off_by_kill_switch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(hch.KILL_SWITCH_ENV, "off")
    monkeypatch.setattr(hch, "reboot_pending_finding", lambda: "waere ein Befund")
    assert hch.check(tmp_path, SimpleNamespace(runs_on_pi=True)) == []


def test_check_is_silent_off_the_pi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(hch.KILL_SWITCH_ENV, raising=False)
    monkeypatch.setattr(hch, "reboot_pending_finding", lambda: "waere ein Befund")
    assert hch.check(tmp_path, SimpleNamespace(runs_on_pi=False)) == []


def test_check_maps_findings_to_warning_issues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(hch.KILL_SWITCH_ENV, raising=False)
    monkeypatch.setattr(hch, "unscheduled_timer_finding", lambda: "timer")
    monkeypatch.setattr(hch, "checkout_findings", lambda _root: ["checkout"])
    monkeypatch.setattr(hch, "reboot_pending_finding", lambda: "reboot")
    monkeypatch.setattr(hch, "stale_library_finding", lambda _root: "libs")
    seen: list[Path] = []
    monkeypatch.setattr(hch, "offpi_backup_finding", lambda adir: seen.append(adir) or "offpi")
    monkeypatch.setattr(hch, "enabled_set_finding", lambda _root: "drift")
    issues = hch.check(tmp_path, SimpleNamespace(runs_on_pi=True))
    assert [(i.severity, i.component, i.message) for i in issues] == [
        # aus health_check.py verlegt: Komponente und Schwere unveraendert
        ("critical", "timer_scheduleability", "timer"),
        ("warning", "checkout_hygiene", "checkout"),
        ("warning", "reboot_pending", "reboot"),
        ("warning", "stale_libraries", "libs"),
        ("warning", "offpi_backup", "offpi"),
        ("warning", "enabled_set_drift", "drift"),
    ]
    assert seen == [tmp_path / "artifacts"]


# ── Soll-Set der aktivierten Units (E4 Teil 2) ─────────────────────────────


def _unit_repo(tmp_path: Path, soll: str, units: list[str]) -> Path:
    unit_dir = tmp_path / hch.ENABLED_SET_RELPATH.parent
    unit_dir.mkdir(parents=True)
    for name in units:
        (unit_dir / name).write_text("[Unit]\n", encoding="utf-8")
    (tmp_path / hch.ENABLED_SET_RELPATH).write_text(soll, encoding="utf-8")
    return tmp_path


def _listing(*enabled: str) -> hch.Runner:
    rows = "".join(f"{name} enabled enabled\n" for name in enabled)
    return lambda _cmd: rows


_UNITS = ["kai-a.timer", "kai-a.service", "kai-b.timer", "kai-b.service", "cloudflared.service"]


def test_matching_enabled_set_is_silent(tmp_path: Path) -> None:
    repo = _unit_repo(tmp_path, "# Kommentar\nkai-a.timer\ncloudflared.service  # Tunnel\n", _UNITS)
    run = _listing("kai-a.timer", "cloudflared.service", "ssh.service", "needrestart.service")
    assert hch.enabled_set_finding(repo, run=run) is None, "fremde Systemdienste zaehlen nicht"


def test_silently_disabled_and_unwanted_units_are_reported(tmp_path: Path) -> None:
    repo = _unit_repo(tmp_path, "kai-a.timer\nkai-b.timer\n", _UNITS)
    msg = hch.enabled_set_finding(repo, run=_listing("kai-a.timer", "kai-a.service"))
    assert msg is not None
    assert "1 nicht aktiviert (kai-b.timer)" in msg
    assert "1 zusaetzlich aktiviert (kai-a.service)" in msg


def test_no_enabled_set_or_no_systemctl_is_silent(tmp_path: Path) -> None:
    assert hch.enabled_set_finding(tmp_path, run=_listing("kai-a.timer")) is None
    repo = _unit_repo(tmp_path, "kai-a.timer\n", _UNITS)
    assert hch.enabled_set_finding(repo, run=lambda _cmd: None) is None


_REPO = Path(__file__).resolve().parents[2]


def test_repo_enabled_set_names_only_existing_units_once() -> None:
    lines = [
        line.split("#", 1)[0].strip()
        for line in (_REPO / hch.ENABLED_SET_RELPATH).read_text(encoding="utf-8").splitlines()
    ]
    entries = [entry for entry in lines if entry]
    assert len(entries) == len(set(entries)), "Doppelte Eintraege im Soll-Set"
    unit_dir = _REPO / hch.ENABLED_SET_RELPATH.parent
    missing = [e for e in entries if not (unit_dir / e).is_file()]
    assert not missing, f"Soll-Set nennt Units ohne Datei in deploy/systemd/: {missing}"


def test_everything_the_installer_enables_is_in_the_enabled_set() -> None:
    """Was ein frischer Host scharfschaltet, muss auch gewollt aktiv sein."""
    import re

    script = (_REPO / "scripts" / "pi_install_systemd.sh").read_text(encoding="utf-8")
    block = re.search(r"^ENABLE_ON_INSTALL=\((.*?)^\)", script, re.S | re.M)
    assert block is not None
    on_install = set(re.findall(r'"([^"]+)"', block.group(1)))
    soll = hch.read_enabled_set(_REPO)
    assert soll is not None
    assert on_install <= soll, f"Installer aktiviert, Soll-Set nicht: {sorted(on_install - soll)}"


def test_health_report_includes_host_hygiene(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Die Aufrufstelle in ``run_health_check_report`` ist verdrahtet."""
    from app.alerts.health_check import run_health_check_report

    monkeypatch.delenv(hch.KILL_SWITCH_ENV, raising=False)
    monkeypatch.setattr(hch, "unscheduled_timer_finding", lambda: "timer ohne Termin")
    monkeypatch.setattr(hch, "checkout_findings", lambda _root: [])
    monkeypatch.setattr(hch, "reboot_pending_finding", lambda: "reboot faellig")
    monkeypatch.setattr(hch, "stale_library_finding", lambda _root: None)
    adir = tmp_path / "artifacts"
    adir.mkdir()
    report = run_health_check_report(artifacts_dir=adir)
    components = {i.component: i.severity for i in report.issues}
    assert components.get("reboot_pending") == "warning"
    # Keine Kopie belegt -> Befund; die Timer-Sonde laeuft jetzt ueber dieselbe Aufrufstelle.
    assert components.get("offpi_backup") == "warning"
    assert components.get("timer_scheduleability") == "critical"


def test_stream_contract_watcher_is_wired() -> None:
    """Der Vertrag fuer offpi_receipts.jsonl verweist auf einen verdrahteten Waechter."""
    from app.alerts import health_check as hc

    repo_root = Path(__file__).resolve().parents[2]
    contracts = json.loads((repo_root / "config" / "stream_contracts.json").read_text("utf-8"))
    entry = contracts["streams"]["offpi_receipts.jsonl"]
    assert entry["monitoring"] == "alternative_watcher"
    assert entry["watcher"] == "_check_host" and callable(hc._check_host)
