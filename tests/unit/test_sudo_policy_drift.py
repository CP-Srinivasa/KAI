"""Der Wächter über die passwortfreie sudo-Policy (P0 2026-08-19).

Bis hierher meldete niemand, wenn `/etc/sudoers.d` und `deploy/sudoers.d`
auseinanderlaufen — dieselbe Klasse wie die Unit-Drift aus #717, nur mit
höherem Einsatz. Der scharfe Teil ist die Wildcard-Prüfung: ein Argument-Glob
sieht eng aus, ist aber umgehbar, weil sudoers Argumente als EINEN String
matcht und `*` auch Leerzeichen frisst.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from app.alerts.health_check import _check_sudo_policy

BROKER_LINE = "    (root) NOPASSWD: /usr/local/sbin/kai-service-control"
GLOB_LINE = "    (root) NOPASSWD: /usr/bin/systemctl restart kai-*"
HEADER = "User ubuntu may run the following commands on kai-pi5:\n    (ALL : ALL) ALL\n"


@pytest.fixture(autouse=True)
def _probe_enabled(monkeypatch):
    """Die conftest-Fixture schaltet die Probe global ab — hier wieder an."""
    monkeypatch.delenv("KAI_SUDO_POLICY_PROBE", raising=False)


@pytest.fixture
def sudo_output(monkeypatch):
    """Ersetzt den `sudo -n -l`-Aufruf durch eine feste Ausgabe."""

    def _set(stdout: str, returncode: int = 0) -> None:
        def fake_run(*args, **kwargs):
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

    return _set


def test_erwartete_policy_erzeugt_keinen_befund(sudo_output) -> None:
    sudo_output(HEADER + BROKER_LINE + "\n")
    assert _check_sudo_policy(runs_on_pi=True) == []


def test_argument_wildcard_ist_ein_kritischer_befund(sudo_output) -> None:
    """Genau die Regel, die live umgehbar war."""
    sudo_output(HEADER + GLOB_LINE + "\n")
    issues = _check_sudo_policy(runs_on_pi=True)
    assert len(issues) == 1
    assert issues[0].severity == "critical"
    assert issues[0].component == "sudo_policy"
    assert "umgehbar" in issues[0].message


def test_fremde_nopasswd_regel_ist_ein_befund(sudo_output) -> None:
    sudo_output(HEADER + "    (ALL) NOPASSWD: ALL\n")
    issues = _check_sudo_policy(runs_on_pi=True)
    assert len(issues) == 1
    assert "unerwartete" in issues[0].message


def test_mehrere_regeln_werden_einzeln_gemeldet(sudo_output) -> None:
    sudo_output(HEADER + BROKER_LINE + "\n" + GLOB_LINE + "\n")
    issues = _check_sudo_policy(runs_on_pi=True)
    assert len(issues) == 1
    assert issues[0].severity == "critical"


def test_ausserhalb_des_pi_wird_nicht_geprueft(sudo_output) -> None:
    """Die Workstation hat eine andere Policy — das ist kein Befund."""
    sudo_output(HEADER + GLOB_LINE + "\n")
    assert _check_sudo_policy(runs_on_pi=False) == []


def test_unlesbare_policy_erzeugt_keinen_befund_und_keinen_abbruch(sudo_output) -> None:
    """Lehre #718: die Probe ist ein Befund-Kanal, kein Abbruchgrund."""
    sudo_output("", returncode=1)
    assert _check_sudo_policy(runs_on_pi=True) == []


def test_probe_kann_abgeschaltet_werden(monkeypatch, sudo_output) -> None:
    """Der Schalter, den tests/conftest.py setzt — sonst wuerde die Probe in
    jedem fremden Test einen echten `sudo -n -l` ausloesen."""
    sudo_output(HEADER + GLOB_LINE + "\n")
    monkeypatch.setenv("KAI_SUDO_POLICY_PROBE", "off")
    assert _check_sudo_policy(runs_on_pi=True) == []


def test_sudo_nicht_vorhanden_bricht_nicht(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise FileNotFoundError("sudo")

    monkeypatch.setattr(subprocess, "run", boom)
    assert _check_sudo_policy(runs_on_pi=True) == []


def test_timeout_bricht_nicht(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="sudo", timeout=15)

    monkeypatch.setattr(subprocess, "run", boom)
    assert _check_sudo_policy(runs_on_pi=True) == []
