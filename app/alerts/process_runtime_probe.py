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

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Final


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
    # Die Beweiskette EINMAL erheben und beide Achsen daraus steuern.
    chain_problems = release_provenance_problems(repo_root)
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
        # DIESELBE strenge Quelle wie die alte Sonde. Vorher stand hier
        # ``not current_path`` — eine zweite, lose Definition desselben
        # Praedikats. Bei manipuliertem Baum gab ``_active_release`` den Pfad
        # OHNE Hash zurueck: die Release-Achse entfiel mangels Hash, und derselbe
        # Pfad setzte ueber ``not current_path`` gleichzeitig die Checkout-Achse
        # aus. Ein Signal schaltete zwei Sicherungen ab, und beide Sonden
        # schwiegen genau in dem Fall, fuer den ``verify_release`` gebaut ist.
        checkout_is_authoritative=chain_problems != [],
        expected_lock_sha256=deploy.get("requirements_lock_sha256"),
        deployed_at_utc=deploy.get("deployed_at_utc"),
    )
    # Eine gebrochene Kette wird BEIM NAMEN genannt, nicht nur indirekt ueber
    # eine wiederbelebte Checkout-Achse. Sie kann auch dann gelten, wenn jeder
    # Prozess sauber sein Release bezeugt — der Baum wurde eben NACH dem Start
    # angefasst.
    kette = (
        "Release-Provenienz unvollstaendig: " + ", ".join(sorted(chain_problems))
        if chain_problems and current_path
        else ""
    )
    befund = "" if result.ok else render_process_provenance(result)
    if kette and befund:
        return kette + "; " + befund
    return kette or befund or None


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


def checkout_axis_active(state_root: Path) -> bool:
    """Gilt der alte Checkout-Vertrag hier noch?

    Die Abhaengigkeits-Achse von ``runtime_provenance`` vergleicht einen
    ``dependency_marker`` gegen den HEAD des Quellbaums — reines Checkout-Modell.
    Regiert ein unveraenderliches Release, ist der Quell-Checkout nicht mehr die
    aktive Wahrheit, und der Lock-Stand ist staerker belegt: ``release.json``
    pinnt ihn, der Deploy-Marker fuehrt ihn, jeder Prozessmarker traegt ihn.

    Ohne diese Unterscheidung entstuende ein Dauerbefund: das Skript, das den
    ``dependency_marker`` schrieb (``pi_sync_dependencies.sh``), ist auf der
    Mainline geloescht und im Release-Fluss durch ``pi_make_release.sh`` und
    ``pi_activate_release.sh`` ersetzt. Ein fehlender Marker meldete "unbelegt,
    ob je synchronisiert wurde" — alle 60 Minuten, fuer immer. Genau solche
    selbstverschuldeten Dauerbefunde haben zwei G8-Akte zerstoert.

    Fail-closed: bei jeder Luecke in der Release-Beweiskette bleibt der alte
    Vertrag in Kraft und der Befund bestehen.
    """
    return not release_governs(state_root)


def release_provenance_problems(state_root: Path) -> list[str]:
    """Alle Luecken in der Release-Beweiskette — leer heisst: vollstaendig belegt.

    Ein vorhandenes ``release.json`` ist KEIN Freifahrtschein. Der alte
    Abhaengigkeits-Marker darf nur dort entfallen, wo das Release-Modell ihn
    tatsaechlich ersetzt — und das tut es erst, wenn die Kette geschlossen ist:

        release.json.requirements_lock_sha256
        == SHA256(<aufgeloestes Release>/requirements.lock)
        == deployment_marker.requirements_lock_sha256

        release.json.repo_sha == deployment_marker.repo_sha

    Die dritte Stufe, ``process_marker.repo_sha``, prueft
    :func:`process_runtime_finding` als eigene Achse: dort ist der Soll-Stand der
    Deploy-Marker, und eine Abweichung ergibt RUNTIME_CODE_DRIFT. Sie hier
    nochmals zu behaupten, waere eine Zweitmeinung ueber dieselbe Tatsache.

    Fail-closed: jede fehlende oder unlesbare Stufe ist ein Eintrag in dieser
    Liste, kein stilles Bestehen.
    """
    from app.observability.process_runtime_marker import read_deployment_marker
    from app.observability.release_identity import (
        read_release_manifest,
        resolve_current,
        verify_release,
    )

    current = resolve_current(_current_link(state_root))
    if current is None:
        return ["RELEASE_NOT_ACTIVE"]
    problems = list(verify_release(current))
    manifest = read_release_manifest(current)
    if manifest is None:
        return problems + ["RELEASE_MANIFEST_UNREADABLE"]

    lock_path = current / "requirements.lock"
    try:
        actual_lock = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    except OSError:
        problems.append("RELEASE_LOCK_MISSING")
        actual_lock = ""
    if actual_lock and actual_lock != manifest.requirements_lock_sha256:
        problems.append("RELEASE_LOCK_HASH_MISMATCH")

    deploy = read_deployment_marker(state_root)
    if not deploy:
        return problems + ["DEPLOY_MARKER_MISSING"]
    if str(deploy.get("requirements_lock_sha256") or "") != manifest.requirements_lock_sha256:
        problems.append("DEPLOY_LOCK_MISMATCH")
    if str(deploy.get("repo_sha") or "") != manifest.repo_sha:
        problems.append("DEPLOY_REPO_SHA_MISMATCH")
    return problems


def release_governs(state_root: Path) -> bool:
    """Regiert ein unveraenderliches Release — mit LUECKENLOSEM Beweis?

    Der Unterschied entscheidet, welche Achsen ueberhaupt etwas beweisen. Unter
    dem Release-Modell ist der Quell-Checkout nicht mehr die aktive Wahrheit; er
    darf beim Rollback legitim auf NEU stehen, waehrend deployt, aktiv und
    laufend alle drei ALT sind.

    Regiert wird aber nur bei geschlossener Kette. Jede Luecke laesst den alten
    Checkout-Vertrag in Kraft — und damit den Befund, statt ihn wegzudefinieren.
    """
    return not release_provenance_problems(state_root)


def _current_link(state_root: Path) -> Path:
    """Der ``current``-Symlink neben dem Zustandsbaum -- AUFGELOEST abgeleitet.

    ``state_root.parent`` ohne ``resolve()`` ist die Falle: bei einem relativen
    Pfad -- und genau so ruft ``app/alerts/health_check.py`` auf, naemlich mit
    dem CWD-relativen Repo-Wurzelpfad -- ist ``Path(".").parent`` wieder ``.``.
    Gesucht wurde dann ``./current`` IM Checkout statt ``../current`` daneben,
    der Symlink blieb unauffindbar, und ``release_governs`` meldete ``False``,
    waehrend das Release nachweislich regierte.

    Die Folge war kein stiller Fehler, sondern das Gegenteil: die stillgelegte
    Checkout-Achse sprang wieder an und meldete alle 15 Minuten
    ``runtime-provenance: HOLD`` samt DEPENDENCY-DRIFT auf einen
    ``dependency_marker``, den im Release-Modell niemand mehr schreibt -- ein
    CRITICAL, das kein Operator je haette schliessen koennen. Gemessen am
    2026-09-07 auf kai-pi5, waehrend alle fuenf Prozessmarker korrekt auf das
    aktive Release zeigten.

    ``app/core/runtime_identity.py`` macht es seit jeher richtig; deshalb war
    ``/health`` gruen, waehrend die Sonde HOLD rief. Zwei Ableitungen desselben
    Pfades, eine davon falsch -- hier steht sie jetzt einmal.
    """
    try:
        aufgeloest = state_root.resolve()
    except OSError:
        aufgeloest = state_root.absolute()
    return aufgeloest.parent / "current"


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

    current = resolve_current(_current_link(state_root))
    if current is None:
        return "", ""
    if verify_release(current):
        # Der aktive Baum traegt seinen eigenen Anspruch nicht mehr. Dann ist
        # jede Aussage ueber ihn wertlos — die Achse bleibt ungenannt, und die
        # uebrigen Pruefungen entscheiden.
        return str(current), ""
    manifest = read_release_manifest(current)
    return str(current), (manifest.release_tree_sha256 if manifest else "")


#: Units, die im Repo LIEGEN, aber bewusst NICHT laufen sollen.
#:
#: Die Ableitung unten ist gut gedacht und an einer Stelle blind: sie liest die
#: Unit-Dateien und schliesst daraus auf die Erwartung. Eine Datei im Repo ist
#: aber nicht dasselbe wie eine Unit, die laufen SOLL. Fuer eine bewusst
#: zurueckgestellte Komponente erzeugt sie damit einen Befund, den niemand
#: aufloesen kann — und ein CRITICAL, den kein Operator schliessen kann,
#: entwertet jeden CRITICAL neben sich.
#:
#: Jeder Eintrag traegt Datum, Grund und das EREIGNIS, das ihn beendet — kein
#: Ablaufdatum. Ein Datum laeuft ab, ohne dass sich etwas geaendert haette;
#: dann steht die Erwartung wieder da, waehrend der Grund fortbesteht.
DEFERRED_UNITS: Final[dict[str, dict[str, str]]] = {
    "kai-litellm.service": {
        "decision_date": "2026-09-08",
        "reason": (
            "DEFERRED_UPSTREAM_DEPENDENCY_CONFLICT — litellm 1.99.0 verlangt "
            "openai<3.0.0,>=2.20.0, das Lockfile pinnt openai==3.6.0. Auch "
            "1.100.0 scheitert identisch; ohne Pin loest pip auf litellm-0.1.236 "
            "auf. Ein Core-Downgrade von openai ist ausgeschlossen."
        ),
        "reopen_when": (
            "die LiteLLM-Runtime-Adoption ausdruecklich wiedereroeffnet ist UND "
            "ein vertraeglicher Abhaengigkeitsvertrag existiert UND Bau, "
            "Installation und Start erneut operator-freigegeben sind"
        ),
    },
}

#: Zustaende, in denen eine zurueckgestellte Unit NICHT sein darf. Zurueckgestellt
#: heisst "wird nicht erwartet" — ausdruecklich nicht "wird nicht beobachtet".
#: Taucht sie doch auf, ist das die interessantere Abweichung: jemand hat sie
#: installiert oder gestartet, ohne dass die Zurueckstellung aufgehoben wurde.
STATE_DEFERRED_UNEXPECTED: Final = "DEFERRED_UNIT_UNEXPECTEDLY_PRESENT"


def _systemctl(*args: str) -> str:
    """``systemctl``-Ausgabe als getrimmter Text — leer, wenn nicht ermittelbar."""
    import subprocess

    try:
        return subprocess.run(  # noqa: S603
            ["systemctl", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def deferred_unit_violations(
    deferred: dict[str, dict[str, str]] | None = None,
    *,
    probe: Callable[[str, str], str] | None = None,
) -> tuple[str, ...]:
    """Ist eine zurueckgestellte Unit entgegen der Entscheidung doch da?

    Die Gegenprobe zur Ausnahme. Ohne sie waere aus "nicht erwartet" ein
    "nicht ueberwacht" geworden, und genau dann faellt niemandem auf, wenn die
    Komponente still doch anlaeuft — mit einem Abhaengigkeitskonflikt, dessen
    wegen sie zurueckgestellt wurde.

    Geprueft werden die Zustaende, die systemd selbst kennt: ``is-active`` und
    ``is-enabled``. ``not-found`` und ``inactive``/``disabled`` sind der
    erwartete Fall und ergeben KEINEN Befund.
    """
    eintraege = DEFERRED_UNITS if deferred is None else deferred
    frage = probe or (lambda verb, unit: _systemctl(verb, unit))
    befunde: list[str] = []
    for unit in sorted(eintraege):
        aktiv = frage("is-active", unit)
        if aktiv in ("active", "activating", "reloading"):
            befunde.append(f"{unit}: laeuft ({aktiv}), obwohl zurueckgestellt")
        freigeschaltet = frage("is-enabled", unit)
        if freigeschaltet in ("enabled", "enabled-runtime", "static", "alias"):
            befunde.append(f"{unit}: ist {freigeschaltet}, obwohl zurueckgestellt")
    return tuple(befunde)


def deferred_unit_finding(
    deferred: dict[str, dict[str, str]] | None = None,
    *,
    probe: Callable[[str, str], str] | None = None,
) -> str:
    """Die fertige Operator-Meldung — leer, wenn alles wie entschieden steht.

    Die Gegenprobe zur Ausnahme, und der Grund, warum die Ausnahme ueberhaupt
    vertretbar ist: eine Unit aus :data:`DEFERRED_UNITS` wird nicht ERWARTET,
    beobachtet wird sie trotzdem. Taucht sie doch auf, ist das die
    interessantere Abweichung als ihr Fehlen — jemand hat sie installiert oder
    gestartet, ohne dass die Entscheidung aufgehoben wurde, und im Fall von
    ``kai-litellm`` mit genau dem Abhaengigkeitskonflikt, dessen wegen sie
    zurueckgestellt ist.

    Der Text nennt beide Auswege, damit der Befund schliessbar ist: die
    Zurueckstellung foermlich aufheben oder die Unit stoppen. Ein CRITICAL ohne
    Ausweg ist das, was hier gerade abgeschafft wurde.
    """
    verstoesse = deferred_unit_violations(deferred, probe=probe)
    if not verstoesse:
        return ""
    return (
        "Zurueckgestellte Unit ist aktiv: "
        + "; ".join(verstoesse)
        + " — entweder die Zurueckstellung foermlich aufheben (DEFERRED_UNITS in "
        "app/alerts/process_runtime_probe.py) oder die Unit stoppen und deaktivieren."
    )


def expected_attesting_units(repo_root: Path) -> tuple[str, ...]:
    """Die Units, die sich beim Start selbst bezeugen MUESSEN.

    Quelle sind die Unit-Dateien im Repo: wer ``runtime-exec`` in seinem
    ``ExecStart`` fuehrt, hat den Attestierungsvertrag. Die Liste pflegt sich
    damit selbst — eine handgefuehrte Konstante waere die naechste Wachliste,
    die von ihrer Quelle abweicht.

    Ausgenommen sind ausschliesslich die Eintraege aus :data:`DEFERRED_UNITS`.
    Sie werden nicht erwartet — und durch :func:`deferred_unit_violations`
    trotzdem beobachtet.
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
        if "runtime-exec" in text and path.name not in DEFERRED_UNITS:
            out.append(path.name)
    return tuple(out)


__all__ = [
    "DEFERRED_UNITS",
    "STATE_DEFERRED_UNEXPECTED",
    "deferred_unit_finding",
    "deferred_unit_violations",
    "expected_attesting_units",
    "checkout_axis_active",
    "release_governs",
    "release_provenance_problems",
    "process_runtime_finding",
    "unit_active_enter_utc",
]
