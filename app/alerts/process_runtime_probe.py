"""Health-Sonde: bezeugt jeder laufende Prozess selbst, welchen Code er geladen hat?

``health_check._check_runtime_provenance`` fragt den CHECKOUT nach seinem heutigen
HEAD. Das erkennt einen Prozess aus einem fremden Baum, aber nicht den haeufigeren
Fall: richtiger Baum, alter Code im Speicher, weil der Checkout sich nach dem
Prozessstart weiterbewegt hat. Am 2026-09-01 um 21:09Z lief ``kai-server`` real auf
``dc276bc3``, waehrend der Checkout auf ``9293c423`` stand — und die Sonde meldete
``RUNTIME_CODE_DRIFT = 0``.

Eigenes Modul, nicht ein weiterer Block in ``health_check.py``: die Datei stand bei
1869 Zeilen gegen eine God-File-Schwelle von 1800. Eine angehobene Baseline haette
den Ratchet zum Formular gemacht.

Gibt den Alarmtext zurueck, nicht die ``HealthIssue`` — ``HealthIssue`` wohnt in
``health_check`` und ein Import von dort waere zirkulaer.
"""

from __future__ import annotations

from pathlib import Path


def process_runtime_finding(repo_root: Path, *, expected_sha: str) -> str | None:
    """Der Befundtext, wenn ein Prozess seinen Code nicht oder falsch bezeugt.

    ``None`` heisst: jeder repo-basierte, laufende Dienst hat beim Start die
    erwartete Revision bezeugt. Fehlt ein Marker, lautet der Zustand ``UNKNOWN``
    — nie „in Ordnung".
    """
    from app.observability.process_runtime_marker import (
        ProcessObservation,
        current_boot_id,
        evaluate_process_markers,
        proc_start_ticks,
        read_deployment_marker,
        read_process_markers,
        render_process_provenance,
    )
    from app.observability.runtime_provenance import collect_runtime_services, sha256_of

    services = [s for s in collect_runtime_services() if s.repo_based and s.pid > 0]
    if not services:
        return None
    boot = current_boot_id()
    observations = [
        ProcessObservation(
            unit=s.unit,
            main_pid=s.pid,
            proc_start_ticks=proc_start_ticks(s.pid),
            boot_id=boot,
            started_at_utc=unit_active_enter_utc(s.unit),
        )
        for s in services
    ]
    deploy = read_deployment_marker(repo_root) or {}
    result = evaluate_process_markers(
        observations,
        read_process_markers([s.unit for s in services], root=repo_root),
        expected_sha=expected_sha,
        checkout_sha=expected_sha,
        expected_lock_sha256=sha256_of(repo_root / "requirements.lock"),
        deployed_at_utc=deploy.get("deployed_at_utc"),
    )
    return None if result.ok else render_process_provenance(result)


def unit_active_enter_utc(unit: str) -> str:
    """``ActiveEnterTimestamp`` als ISO-UTC — leer, wenn nicht ermittelbar.

    Ueber die MONOTONE Variante plus ``/proc/uptime``: der Wandzeit-Stempel von
    systemd ist lokalisiert und in der Zeitzone des Hosts formatiert, was beim
    Vergleich mit einem UTC-Deploy-Marker still danebenliegt.
    """
    import subprocess
    from datetime import UTC, datetime

    try:
        raw = subprocess.run(  # noqa: S603
            ["systemctl", "show", unit, "-p", "ActiveEnterTimestampMonotonic", "--value"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
        usec = int(raw or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    if usec <= 0:
        return ""
    try:
        boot = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return ""
    now = datetime.now(UTC).timestamp()
    return datetime.fromtimestamp(now - boot + usec / 1_000_000, tz=UTC).isoformat(
        timespec="seconds"
    )


__all__ = ["process_runtime_finding", "unit_active_enter_utc"]
