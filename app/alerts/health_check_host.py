"""Host-Hygiene der Pi: Checkout, ausstehender Neustart, ersetzte Bibliotheken.

Ausgelagert aus ``app/alerts/health_check.py`` (God-File-Ratchet ohne Spielraum) —
dieselbe Bauart wie ``health_check_pay.py``: dort steht die Aufrufstelle, hier die
Sonde. MindBlow 2.0, Pakete E10 und U-2 (25.09.2026).

**Checkout-Hygiene (E10).** 54 Timer-Units laufen aus dem Checkout, nicht aus dem
Release. Am 25.09.2026 stand dort eine gestagte ``scripts/operator_digest.py``
(der Digest lief damit auf unversioniertem Code) und eine untracked
``scripts/kai_operator_arm_backup.sh``, die die Mainline inzwischen mit anderem
Inhalt versionierte. Der ``git merge --ff-only`` in ``pi_release_deploy.sh`` waere
daran abgebrochen (Befund N1) — gefunden hat es ein Mensch, keine Sonde.

**Ausstehender Neustart (U-2).** Seit ``deploy/needrestart/50-kai.conf`` (#1072)
startet ``needrestart`` die KAI-Dienste nach einem Sicherheitsupdate nicht mehr
selbst neu, sondern meldet sie nur. Das ist gewollt (Neustarts gehoeren in den
Release-Weg), hat aber einen Preis: ein Update kann installiert und trotzdem
unwirksam sein. Diese Sonde macht den Preis sichtbar, sobald er aelter als
:data:`MAX_PENDING_DAYS` ist — der Kernel-Neustart ueber ``/run/reboot-required``,
die Bibliotheken ueber ``(deleted)``-Eintraege in den Speicherkarten der Daemons.

Alle drei Befunde sind ``warning``: sie kosten Aktualitaet, nie Geld.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from app.alerts.health_check import HealthIssue

#: ``off`` schaltet die Sonde ab. ``tests/conftest.py`` setzt es fuer jeden Test —
#: ``runs_on_pi`` ist dort immer wahr, und ein CI-Runner hat eigene Reboot-Marker.
KILL_SWITCH_ENV: Final = "KAI_HOST_HYGIENE_PROBE"
REBOOT_REQUIRED_FILE: Final = Path("/run/reboot-required")
REBOOT_PKGS_FILE: Final = Path("/run/reboot-required.pkgs")
#: Abstand zum woechentlichen Wartungsfenster (U-1): wer ein Fenster verpasst,
#: wird gemeldet, wer es nur abwartet, nicht.
MAX_PENDING_DAYS: Final = 7.0
_SHOW: Final = 3
_DELETED: Final = " (deleted)"

Runner = Callable[[Sequence[str]], str | None]


def _run(cmd: Sequence[str]) -> str | None:
    """stdout bei Exit 0, sonst ``None`` — ein Werkzeugfehler ist kein Befund."""
    try:
        done = subprocess.run(  # noqa: S603
            list(cmd), capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _issue(severity: str, component: str, message: str) -> HealthIssue:
    from app.alerts.health_check import HealthIssue as _HealthIssue  # Zyklus vermeiden

    return _HealthIssue(severity=severity, component=component, message=message)


def _preview(items: Sequence[str]) -> str:
    shown = ", ".join(items[:_SHOW])
    return shown + (f" und {len(items) - _SHOW} weitere" if len(items) > _SHOW else "")


def checkout_findings(repo_root: Path, *, run: Runner = _run) -> list[str]:
    """Getrackte Aenderungen und untracked Dateien, die der Upstream versioniert."""
    if not (repo_root / ".git").exists():
        return []
    git = ["git", "-C", str(repo_root)]
    status = run([*git, "status", "--porcelain=v1", "--untracked-files=normal"])
    if status is None:
        return []
    lines = [line for line in status.splitlines() if len(line) > 3]
    tracked = [line[3:] for line in lines if not line.startswith("??")]
    untracked = [line[3:] for line in lines if line.startswith("??")]
    findings: list[str] = []
    if tracked:
        findings.append(
            f"Pi-Checkout hat {len(tracked)} getrackte Aenderung(en) ({_preview(tracked)}) — "
            "die Timer-Jobs laufen damit auf Code, den keine Mainline-Version belegt."
        )
    upstream = run([*git, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    ref = upstream.strip() if upstream else ""
    listing = run([*git, "ls-tree", "-r", "--name-only", ref]) if ref else None
    if untracked and listing is not None:
        files = set(listing.splitlines())
        # git status zeigt ein ganz untracked Verzeichnis nur als "dir/" -- deshalb
        # jede Vorfahren-Ebene der versionierten Pfade kennen.
        dirs = {
            "/".join(parts[:i]) + "/"
            for parts in (p.split("/") for p in files)
            for i in range(1, len(parts))
        }
        collide = [u for u in untracked if u in files or u in dirs]
        if collide:
            findings.append(
                f"Pi-Checkout hat {len(collide)} untracked Datei(en), die {ref} "
                f"inzwischen versioniert ({_preview(collide)}) — der naechste "
                "`git merge --ff-only` in pi_release_deploy.sh bricht daran ab. "
                "Mit der versionierten Fassung vergleichen und beiseitelegen."
            )
    return findings


def reboot_pending_finding(
    *,
    marker: Path = REBOOT_REQUIRED_FILE,
    pkgs: Path = REBOOT_PKGS_FILE,
    now: float | None = None,
    max_days: float = MAX_PENDING_DAYS,
) -> str | None:
    """Befund, wenn ``/run/reboot-required`` aelter als ``max_days`` ist."""
    try:
        since = marker.stat().st_mtime
    except OSError:
        return None
    age_days = ((time.time() if now is None else now) - since) / 86400
    if age_days < max_days:
        return None
    try:
        raw = pkgs.read_text(encoding="utf-8").splitlines()
    except OSError:
        raw = []
    names = sorted({n.strip() for n in raw if n.strip()})
    what = f" ({_preview(names)})" if names else ""
    return (
        f"Neustart der Pi seit {age_days:.0f} Tagen faellig{what} — installierte "
        "Kernel-/Sicherheitsupdates sind nicht aktiv. Im Wartungsfenster neu starten."
    )


def _main_pid(unit: str, run: Runner) -> int:
    raw = run(["systemctl", "show", unit, "-p", "MainPID", "--value"])
    try:
        return int((raw or "0").strip() or 0)
    except ValueError:
        return 0


def stale_library_finding(
    repo_root: Path,
    *,
    units: Sequence[str] | None = None,
    run: Runner = _run,
    proc_root: Path = Path("/proc"),
    now: float | None = None,
    max_days: float = MAX_PENDING_DAYS,
) -> str | None:
    """Befund, wenn ein Daemon eine seit ``max_days`` ersetzte ``.so`` im Speicher haelt.

    Alter = ``st_ctime`` der Ersatzdatei am selben Pfad: dpkg uebernimmt die
    ``mtime`` aus dem Paket, die ``ctime`` dagegen entsteht beim Einspielen.
    Fehlt die Ersatzdatei, ist das Alter unbelegt und es gibt keinen Befund.
    """
    if units is None:
        from app.alerts.process_runtime_probe import expected_attesting_units

        units = expected_attesting_units(repo_root)
    cutoff = (time.time() if now is None else now) - max_days * 86400
    hits: list[str] = []
    for unit in units:
        pid = _main_pid(unit, run)
        if pid <= 0:
            continue
        try:
            maps = (proc_root / str(pid) / "maps").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # anderer Benutzer oder Prozess gerade beendet
        stale: set[str] = set()
        for line in maps.splitlines():
            parts = line.split(maxsplit=5)
            if len(parts) < 6 or not parts[5].endswith(_DELETED):
                continue
            path = parts[5][: -len(_DELETED)]
            if not os.path.isabs(path) or ".so" not in Path(path).name:
                continue
            try:
                replaced_at = os.stat(path).st_ctime
            except OSError:
                continue
            if replaced_at <= cutoff:
                stale.add(Path(path).name)
        if stale:
            hits.append(f"{unit.removesuffix('.service')}: {_preview(sorted(stale))}")
    if not hits:
        return None
    return (
        f"{len(hits)} Dienst(e) halten seit mehr als {max_days:.0f} Tagen ersetzte "
        f"Bibliotheken im Speicher ({'; '.join(hits)}) — Sicherheitsupdates sind "
        "installiert, in diesen Prozessen aber nicht wirksam (needrestart meldet kai-* "
        "nur, 50-kai.conf). Neustart ueber den naechsten Release oder das Wartungsfenster."
    )


def check(repo_root: Path, report: Any) -> list[HealthIssue]:
    """Aufrufstelle in ``health_check.run_health_check_report`` — nur auf der Pi."""
    if os.environ.get(KILL_SWITCH_ENV, "").strip().lower() == "off":
        return []
    if not getattr(report, "runs_on_pi", False):
        return []  # Workstation-Checkouts sind absichtlich schmutzig
    issues = [_issue("warning", "checkout_hygiene", m) for m in checkout_findings(repo_root)]
    if msg := reboot_pending_finding():
        issues.append(_issue("warning", "reboot_pending", msg))
    if msg := stale_library_finding(repo_root):
        issues.append(_issue("warning", "stale_libraries", msg))
    return issues


__all__ = [
    "KILL_SWITCH_ENV",
    "MAX_PENDING_DAYS",
    "check",
    "checkout_findings",
    "reboot_pending_finding",
    "stale_library_finding",
]
