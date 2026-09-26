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

**Offsite-Beleg (E3).** Die einzige Kopie ausserhalb der Pi, die ohne das
Windows-Profil des Laptops lesbar ist, liegt auf der Platte "KAI Backup"
(``kai_vault.ps1``, Operator-Entscheid 25.09.: die Platte ist die Offsite-Kopie
und haengt nur zum Sichern am Laptop). Nach jeder bestandenen Probe hinterlegt
das Skript eine Quittung in ``artifacts/backup/offpi_receipts.jsonl``. Gemessen
wird das Alter der neuesten VERIFIZIERTEN Generation, nicht das der Quittung —
eine erneute Probe einer alten Generation macht die Kopie nicht frischer. Der
Restore-Drill bleibt davon unberuehrt (``off_pi_redundancy: NOT_CLAIMED``): er
beweist das Archiv auf der Pi, diese Sonde verweist auf einen eigenen Beweis an
einem anderen Ort.

**Timer ohne Termin.** Wiederkehrende Timer, die laufen und trotzdem keinen
Termin haben (``timer_schedule_probe``, V4 2026-09-16), gehoeren ebenfalls zum
Host-Zustand und liefen bis E3 als ``_check_timer_scheduleability`` in
``health_check.py``. Hierher verlegt, um dort die 4 Zeilen des Waechters
``_check_host`` (Stream-Vertrag ``offpi_receipts.jsonl``) ohne Baseline-
Anhebung zu tragen. Komponente, Schwere und Pi-Bedingung sind unveraendert.

Alle Befunde sind ``warning`` — sie kosten Aktualitaet, nie Geld —, bis auf
``timer_scheduleability`` (``critical``, wie zuvor).
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from app.alerts.timer_schedule_probe import unscheduled_timer_finding
from app.observability.offpi_receipts import newest_verified

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
#: Quittungen der Offsite-Kopie, relativ zu ``artifacts/`` (Schreiber: kai_vault.ps1).
OFFPI_RECEIPTS_RELPATH: Final = Path("backup") / "offpi_receipts.jsonl"
OFFPI_RECEIPT_SCHEMA: Final = "offpi_receipt/v1"
#: Eine Woche Takt plus ein Tag Spielraum: die Platte wird zum Sichern angesteckt,
#: nicht dauerhaft betrieben.
MAX_OFFPI_AGE_DAYS: Final = 8.0
_SHOW: Final = 3
_DELETED: Final = " (deleted)"

Runner = Callable[[Sequence[str]], str | None]


def _run(cmd: Sequence[str]) -> str | None:
    """stdout bei Exit 0, sonst ``None`` — ein Werkzeugfehler ist kein Befund."""
    try:
        done = subprocess.run(  # noqa: S603
            list(cmd),
            capture_output=True,
            text=True,
            encoding="utf-8",  # git gibt Pfade als UTF-8 aus; nie die Locale raten
            errors="replace",
            timeout=15,
            check=False,
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


def offpi_backup_finding(
    artifacts_dir: Path, *, now: float | None = None, max_days: float = MAX_OFFPI_AGE_DAYS
) -> str | None:
    """Befund, wenn die neueste verifizierte Offsite-Generation fehlt oder zu alt ist.

    Zaehlt nur Quittungen im eigenen Schema mit ``probe == "PASS"``; kaputte
    Zeilen werden uebersprungen, nicht gemeldet — die Quittung ist Evidenz, die
    Wahrheit liegt auf der Platte (``VERIFIED.json`` der Generation).
    """
    try:
        newest = newest_verified(artifacts_dir)
    except OSError:
        return None  # nicht lesbar ist kein Beleg fuer "fehlt"
    if newest is None:
        where = f"artifacts/{OFFPI_RECEIPTS_RELPATH.as_posix()}"
        return (
            f"Keine verifizierte Offsite-Kopie belegt ({where} ohne PASS-Quittung) — Platte "
            "'KAI Backup' am Laptop anstecken; kai_vault.ps1 sichert, prueft und quittiert "
            "dann automatisch."
        )
    age_days = ((time.time() if now is None else now) - newest[0]) / 86400
    if age_days < max_days:
        return None
    return (
        f"Neueste verifizierte Offsite-Kopie ist {age_days:.0f} Tage alt "
        f"(Generation {newest[1].get('generation', '?')}) — Platte 'KAI Backup' am Laptop "
        "anstecken; kai_vault.ps1 sichert, prueft und quittiert dann automatisch."
    )


def check(repo_root: Path, report: Any) -> list[HealthIssue]:
    """Aufrufstelle in ``health_check.run_health_check_report`` — nur auf der Pi."""
    if os.environ.get(KILL_SWITCH_ENV, "").strip().lower() == "off":
        return []
    if not getattr(report, "runs_on_pi", False):
        return []  # Workstation-Checkouts sind absichtlich schmutzig
    # Schluesselwort-Form mit Literal: tests/unit/test_alert_classes.py liest die
    # Komponenten per AST und erzwingt fuer jede eine Alarmklasse (alert_classes).
    issues: list[HealthIssue] = []
    # Fail-soft wie zuvor in health_check.py: ohne befragbares systemd kein Befund.
    if msg := unscheduled_timer_finding():
        issues.append(_issue(severity="critical", component="timer_scheduleability", message=msg))
    for msg in checkout_findings(repo_root):
        issues.append(_issue(severity="warning", component="checkout_hygiene", message=msg))
    if msg := reboot_pending_finding():
        issues.append(_issue(severity="warning", component="reboot_pending", message=msg))
    if msg := stale_library_finding(repo_root):
        issues.append(_issue(severity="warning", component="stale_libraries", message=msg))
    if msg := offpi_backup_finding(repo_root / "artifacts"):
        issues.append(_issue(severity="warning", component="offpi_backup", message=msg))
    return issues


__all__ = [
    "KILL_SWITCH_ENV",
    "MAX_OFFPI_AGE_DAYS",
    "MAX_PENDING_DAYS",
    "OFFPI_RECEIPTS_RELPATH",
    "check",
    "checkout_findings",
    "offpi_backup_finding",
    "reboot_pending_finding",
    "stale_library_finding",
]
