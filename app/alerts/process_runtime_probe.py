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


def process_runtime_finding(repo_root: Path, *, checkout_sha: str) -> str | None:
    """Der Befundtext, wenn ein Prozess seinen Code nicht oder falsch bezeugt.

    ``checkout_sha`` heisst so, weil es das ist: der gemessene Stand des
    Checkouts. Der SOLL-Stand kommt ausschliesslich aus
    ``deployment_marker.repo_sha``. Der Parameter hiess frueher ``expected_sha``
    und wurde auch als solcher weitergereicht — daher der tautologische
    Vergleich des Checkouts mit sich selbst.

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
    from app.observability.runtime_provenance import collect_runtime_services

    expected = expected_attesting_units(repo_root)
    # ``code_bearing`` statt ``repo_based``: ein Release-Baum traegt bewusst kein
    # ``.git``, und genau daran hat die Vorgaengerfassung die fuenf korrekt
    # laufenden Dienste aussortiert — sie wurden zu EXPECTED_UNIT_NOT_RUNNING,
    # obwohl sie liefen. Das alte Checkout-Weltbild darf hier nicht mehr
    # entscheiden, WER ueberhaupt beurteilt wird.
    services = [s for s in collect_runtime_services() if s.code_bearing and s.pid > 0]
    if not services and not expected:
        # Kein systemd, keine erwarteten Units: eine Entwicklungsumgebung. Hier
        # gilt der Vertrag ausdruecklich NICHT — und "nicht anwendbar" ist etwas
        # anderes als "bestanden". Auf dem Pi ist ``expected`` nie leer, dort
        # fuehrt dieselbe Lage zu HOLD statt zu Schweigen.
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
    # Die dritte Achse: welche unveraenderlichen Bytes sind ueberhaupt aktiv?
    # ``current`` wird aufgeloest, nicht als Symlink gefuehrt — und das aktive
    # Release muss seinen eigenen Anspruch noch tragen, sonst ist der Baum
    # nachtraeglich angefasst worden.
    current_path, current_tree = _active_release(repo_root)
    # SOLL kommt AUSSCHLIESSLICH aus der Deploy-Provenienz — auch Baum und Pfad.
    # Sie aus dem gerade aktiven Release zu nehmen hiess, das Aktive mit sich
    # selbst zu vergleichen: deploy koennte TREE_A behaupten, waehrend current
    # und Prozess beide TREE_B tragen, und die Release-Achse waere trotzdem gruen.
    # Fehlt der Marker, ist der Soll-Stand unbelegt — das ist HOLD, nicht PASS.
    result = evaluate_process_markers(
        observations,
        read_process_markers([s.unit for s in services], root=repo_root),
        expected_sha=str(deploy.get("repo_sha") or ""),
        checkout_sha=checkout_sha,
        expected_units=expected,
        expected_release_tree_sha256=str(deploy.get("release_tree_sha256") or ""),
        expected_release_path=str(deploy.get("release_path") or ""),
        current_release_path=current_path,
        current_release_tree_sha256=current_tree,
        # Der Quell-Checkout ist unter dem Release-Modell NICHT mehr die aktive
        # Wahrheit. `pi_activate_release.sh` schaltet `current` und schreibt den
        # Deploy-Marker; ein `git reset` des Quellbaums gehoert nicht dazu. Beim
        # Rollback steht der Checkout deshalb legitim auf NEU, waehrend deployt,
        # aktiv und laufend alle drei ALT sind — ein Vergleich gegen den Checkout
        # meldete dort DEPLOYMENT_PROVENANCE_MISMATCH fuer einen korrekten Zustand.
        checkout_is_authoritative=not current_path,
        expected_lock_sha256=deploy.get("requirements_lock_sha256"),
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


def _active_release(state_root: Path) -> tuple[str, str]:
    """``(aufgeloester Release-Pfad, release_tree_sha256)`` — leer, wenn keiner.

    Leer heisst NICHT "in Ordnung": der Evaluator prueft die Release-Achse dann
    schlicht nicht, und ohne Release-Marker im Prozess bleibt sein Zustand
    ohnehin unbelegt. Auf dem Pi ist beides gesetzt.
    """
    from app.observability.release_identity import (
        read_release_manifest,
        resolve_current,
        verify_release,
    )

    current = resolve_current(state_root.parent / "current")
    if current is None:
        return "", ""
    if verify_release(current):
        # Der aktive Baum traegt seinen eigenen Anspruch nicht mehr. Dann ist
        # jede Aussage ueber ihn wertlos — die Achse bleibt ungenannt, und die
        # uebrigen Pruefungen entscheiden.
        return str(current), ""
    manifest = read_release_manifest(current)
    return str(current), (manifest.release_tree_sha256 if manifest else "")


def expected_attesting_units(repo_root: Path) -> tuple[str, ...]:
    """Die Units, die sich beim Start selbst bezeugen MUESSEN.

    Quelle sind die Unit-Dateien im Repo: wer ``runtime-exec`` in seinem
    ``ExecStart`` fuehrt, hat den Attestierungsvertrag. Die Liste pflegt sich
    damit selbst — eine handgefuehrte Konstante waere die naechste Wachliste,
    die von ihrer Quelle abweicht.
    """
    units_dir = repo_root / "deploy" / "systemd"
    out: list[str] = []
    try:
        candidates = sorted(units_dir.glob("*.service"))
    except OSError:
        return ()
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "runtime-exec" in text:
            out.append(path.name)
    return tuple(out)


__all__ = [
    "expected_attesting_units",
    "process_runtime_finding",
    "unit_active_enter_utc",
]
