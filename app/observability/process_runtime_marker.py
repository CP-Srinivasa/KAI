"""Ein Prozess bezeugt beim Start, welchen Code er geladen hat.

**Warum das noetig wurde.** ``runtime_provenance.collect_runtime_services`` liest
``/proc/<pid>/cwd`` und fragt dann *den Checkout*, auf welchem Commit er heute
steht. Das erkennt zuverlaessig einen Prozess aus einem **fremden** Baum — dafuer
wurde es gebaut. Es erkennt aber **nicht** den haeufigeren Fall: ein Prozess laeuft
aus dem richtigen Baum und hat dort alten Code geladen, weil der Checkout sich
nach dem Start weiterbewegt hat.

Gemessen am 2026-09-01 um 21:09Z:

    Prozess kai-server gestartet 11:58:53Z   -> geladener Code dc276bc3
    Checkout danach ff-gemergt               -> HEAD 9293c423
    Sonde meldete                            -> RUNTIME_CODE_DRIFT = 0

Die Sonde, deren einziger Zweck es ist, „deployt ohne Deckung" zu finden, war
gegen genau diese Form blind. Python bindet Module beim Import; die Dateien auf
der Platte duerfen sich danach beliebig aendern, ohne dass der laufende Prozess
etwas davon merkt — und ohne dass ``git rev-parse HEAD`` davon etwas verraet.

**Die Loesung ist kein besserer Nachtraeglich-Test, sondern eine Aussage zur
richtigen Zeit.** Beim Prozessstart schreibt der Dienst, welchen Commit er gerade
laedt. Diese Zahl wird spaeter **nie neu berechnet**. Sie ist der Primaerbeweis;
die Zeitpruefung gegen den Deploy-Marker ist nur Konsistenzkontrolle.

**Fail-closed.** Fehlt der Marker, passt seine PID nicht, oder stimmt die
Startzeit nicht mit ``/proc/<pid>/stat`` ueberein, lautet der Zustand
``UNKNOWN`` bzw. ``INVALID`` — nie ``MATCH``. Ein Prozess, ueber den man nichts
weiss, ist nicht in Ordnung; er ist unbekannt.

Der Auswertungsteil ist rein: keine Uhr, kein I/O. Die Erhebung steht bewusst
duenn darunter.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol


class RuntimeIdentityLike(Protocol):
    """Der Vertrag von :class:`app.core.runtime_identity.RuntimeIdentity`.

    Bewusst ein Protokoll statt ``Any``: wird ein Feld dort umbenannt, faellt es
    hier als mypy-Fehler auf, statt still zu ``UNKNOWN`` zu degradieren.
    """

    # Nur-lesend deklariert: ``RuntimeIdentity`` ist ein frozen dataclass, und ein
    # Protokoll mit schreibbaren Feldern passt darauf nicht. mypy hat genau das
    # beim ersten Versuch gemeldet.
    @property
    def runtime_commit(self) -> str | None: ...

    @property
    def lock_sha256_at_start(self) -> str | None: ...

    @property
    def started_at_utc(self) -> str: ...


MARKER_SCHEMA: Final = "process_runtime_marker/v1"
DEPLOY_MARKER_SCHEMA: Final = "deployment_marker/v1"

#: Wo die Marker liegen. Im Checkout, damit sie mit ihm wandern — und je Unit
#: eine Datei, damit zwei Dienste sich nicht gegenseitig ueberschreiben.
DEFAULT_MARKER_DIR: Final = Path("artifacts") / "runtime" / "processes"
DEFAULT_DEPLOY_MARKER: Final = Path("artifacts") / "runtime" / "deployment_marker.json"

STATE_MATCH: Final = "MATCH"
STATE_CODE_DRIFT: Final = "RUNTIME_CODE_DRIFT"
STATE_STALE_NO_RESTART: Final = "RUNTIME_STALE_NO_RESTART"
STATE_DEPENDENCY_DRIFT: Final = "DEPENDENCY_DRIFT"
STATE_INVALID: Final = "INVALID"
STATE_UNKNOWN: Final = "UNKNOWN"
STATE_DEPLOY_PROVENANCE_MISMATCH: Final = "DEPLOYMENT_PROVENANCE_MISMATCH"
STATE_EXPECTED_UNKNOWN: Final = "EXPECTED_SHA_UNKNOWN"
STATE_NOT_RUNNING: Final = "EXPECTED_UNIT_NOT_RUNNING"
STATE_RELEASE_MISMATCH: Final = "RELEASE_IDENTITY_MISMATCH"

#: Kein Zustand einer einzelnen Unit, sondern des Deploys: was `current` aktiv
#: haelt, ist nicht das, was der Deploy-Marker als deployt fuehrt. Kann auch
#: dann gelten, wenn JEDER Prozess sauber sein aktives Release bezeugt — dann
#: laeuft die Flotte geschlossen auf einem Stand, den niemand deployt hat.
REASON_ACTIVE_RELEASE_NOT_DEPLOYED: Final = "ACTIVE_RELEASE_NOT_DEPLOYED"

#: Zustaende, die niemals als „in Ordnung" durchgehen duerfen.
NOT_PASSING: Final = frozenset(
    {
        STATE_CODE_DRIFT,
        STATE_STALE_NO_RESTART,
        STATE_DEPENDENCY_DRIFT,
        STATE_INVALID,
        STATE_UNKNOWN,
        STATE_DEPLOY_PROVENANCE_MISMATCH,
        STATE_EXPECTED_UNKNOWN,
        STATE_NOT_RUNNING,
        STATE_RELEASE_MISMATCH,
    }
)

VERDICT_OK: Final = "OK"
VERDICT_HOLD: Final = "DEPLOY_HOLD"


@dataclass(frozen=True)
class ProcessObservation:
    """Was das System JETZT ueber einen laufenden Dienst sagt."""

    unit: str
    main_pid: int
    proc_start_ticks: int
    boot_id: str
    started_at_utc: str = ""


@dataclass(frozen=True)
class ProcessFinding:
    unit: str
    state: str
    detail: str = ""
    marker_code_sha: str | None = None
    expected_sha: str = ""

    @property
    def passing(self) -> bool:
        return self.state not in NOT_PASSING


@dataclass(frozen=True)
class ProcessProvenance:
    """Urteil plus jede einzelne Zeile — nie eine Summe ohne ihre Zerlegung."""

    verdict: str
    findings: tuple[ProcessFinding, ...] = ()
    expected_sha: str = ""
    units_total: int = 0
    units_matching: int = 0
    reasons: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.verdict == VERDICT_OK

    @property
    def all_match(self) -> bool:
        return self.units_total > 0 and self.units_matching == self.units_total


def build_process_marker(
    *,
    unit: str,
    pid: int,
    proc_start_ticks: int,
    boot_id: str,
    repo_root: str,
    runtime_code_sha: str,
    python_executable: str,
    requirements_lock_sha256: str | None,
    started_at_utc: str,
    release_path: str = "",
    release_tree_sha256: str = "",
) -> dict[str, Any]:
    """Der Satz, den ein Dienst beim Start ueber sich selbst schreibt."""
    return {
        "schema": MARKER_SCHEMA,
        "unit": unit,
        "pid": pid,
        "proc_start_ticks": proc_start_ticks,
        "boot_id": boot_id,
        "started_at_utc": started_at_utc,
        "repo_root": repo_root,
        "runtime_code_sha": runtime_code_sha,
        "python_executable": python_executable,
        "requirements_lock_sha256": requirements_lock_sha256,
        # Der AUFGELOESTE Release-Pfad, nicht der Symlink: ein spaeter
        # umgeschaltetes ``current`` darf diesen Prozess nicht umetikettieren.
        "release_path": release_path,
        "release_tree_sha256": release_tree_sha256,
    }


def marker_from_release(
    manifest: Any,
    *,
    unit: str,
    pid: int,
    release_path: Path | str,
    started_at_utc: str,
    proc_start_ticks_value: int | None = None,
    boot_id: str | None = None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Der Marker aus dem RELEASE, nicht aus einem beweglichen Checkout.

    Kein ``git rev-parse`` im laufenden Prozess: der Release-Baum ist
    unveraenderlich, sein ``release.json`` sagt, welche Bytes hier liegen. Genau
    das ist die geladene Code-Identitaet — ein Commit aus einem Baum, der sich
    weiterbewegen kann, war es nie.
    """
    import sys as _sys

    return build_process_marker(
        unit=unit,
        pid=pid,
        proc_start_ticks=(
            proc_start_ticks(pid) if proc_start_ticks_value is None else proc_start_ticks_value
        ),
        boot_id=current_boot_id() if boot_id is None else boot_id,
        repo_root=str(release_path),
        runtime_code_sha=str(getattr(manifest, "repo_sha", "") or ""),
        python_executable=python_executable or _sys.executable,
        requirements_lock_sha256=getattr(manifest, "requirements_lock_sha256", None) or None,
        started_at_utc=started_at_utc,
        release_path=str(release_path),
        release_tree_sha256=str(getattr(manifest, "release_tree_sha256", "") or ""),
    )


def bind_argv_to_release(argv: Sequence[str], *, given: str, resolved: str) -> list[str]:
    """Jeden Pfad im Kommando vom Symlink auf das aufgeloeste Release umschreiben.

    ``execv`` loest ``argv[0]`` ERST beim Exec auf, und Python loest Importpfade
    erst beim Import auf — beides also NACH dem Lesen des Manifests. Bliebe
    ``/home/kai/current/.venv/bin/python`` stehen, koennte ein Switch dazwischen
    den Prozess aus dem NEUEN Release starten, waehrend der Marker das ALTE
    nennt. Das Fenster ist klein; geschlossen war die Bedingung.
    """
    g = given.rstrip("/\\")
    r = resolved.rstrip("/\\")
    if not g or g == r:
        return list(argv)
    return [r + a[len(g) :] if a.startswith(g) else a for a in argv]


def self_attest_and_exec(
    identity: RuntimeIdentityLike,
    *,
    unit: str,
    repo_root: Path | str,
    argv: Sequence[str],
    execv: Callable[[str, Sequence[str]], None] | None = None,
    chdir: Callable[[str], None] | None = None,
    resolve: Callable[[Path], Path | None] | None = None,
) -> None:
    """Sich selbst bezeugen und dann zum Dienst werden — an EIN Release gebunden.

    ``os.execv`` ersetzt das Prozessabbild, behaelt aber PID und Kernel-Startzeit;
    Marker und laufender Dienst sind damit dieselbe Kernel-Identitaet.

    ``ExecStartPost`` koennte das nicht: ein zweiter Prozess wuerde einen selbst
    gelesenen Commit einer per ``systemctl show MainPID`` abgefragten FREMDEN PID
    zuschreiben.

    Und die Bindung endet nicht beim Marker. Nach dem Aufloesen darf **kein**
    Pfad mehr ueber den beweglichen Symlink laufen — weder das Executable noch
    das Arbeitsverzeichnis, aus dem Python seine Module aufloest. Sonst zeigt der
    Marker auf das alte Release, waehrend der Prozess aus dem neuen laedt.
    """
    from app.observability.release_identity import (
        read_release_manifest,
        resolve_current,
        verify_release,
    )

    given = Path(repo_root)
    # Der Aufloesungspunkt ist injizierbar, damit die Symlink-Indirektion auf
    # JEDER Plattform pruefbar bleibt. Ohne das waere der entscheidende Test auf
    # Windows gruen, ohne den Defekt sehen zu koennen — die Blindstelle, gegen
    # die diese Datei gebaut ist.
    resolved = (resolve or resolve_current)(given)
    if resolved is None:
        # Kein aufloesbarer Pfad: hier wird nichts mit angeblicher
        # Release-Provenienz gestartet.
        raise FileNotFoundError(f"Release-Pfad nicht aufloesbar: {given}")

    manifest = read_release_manifest(resolved)
    if manifest is not None:
        problems = verify_release(resolved)
        if problems:
            # Der Baum traegt seinen eigenen Anspruch nicht mehr. Ein Marker
            # darueber waere eine Behauptung ohne Deckung.
            raise RuntimeError(f"Release nicht verifizierbar: {', '.join(problems)}")
        marker = marker_from_release(
            manifest,
            unit=unit,
            pid=os.getpid(),
            release_path=resolved,
            started_at_utc=identity.started_at_utc,
        )
    else:
        # Kein Release-Baum (Entwicklungsumgebung): der Marker traegt dann KEINE
        # Release-Identitaet, und der Evaluator behandelt das als unbelegt statt
        # als bestanden.
        marker = marker_from_identity(identity, unit=unit, pid=os.getpid(), repo_root=resolved)

    write_process_marker(marker, root=resolved)

    bound = bind_argv_to_release(argv, given=str(given), resolved=str(resolved))
    (chdir or os.chdir)(str(resolved))
    (execv or os.execv)(bound[0], bound)


def marker_from_identity(
    identity: RuntimeIdentityLike,
    *,
    unit: str,
    pid: int,
    repo_root: Path | str,
    proc_start_ticks_value: int | None = None,
    boot_id: str | None = None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Der Marker aus der eingefrorenen ``RuntimeIdentity`` — keine zweite Quelle.

    ``runtime_commit`` und ``lock_sha256_at_start`` friert
    :mod:`app.core.runtime_identity` beim Prozessstart ein; dieselbe Quelle
    bedient ``/health`` und den Drift-Report. Sie hier per eigenem
    ``git rev-parse`` nachzurechnen waere eine zweite Wahrheit ueber denselben
    Wert — und zwei Wahrheiten driften (#723/#748/#755).

    Ergaenzt wird nur, was der Identitaet zur KERNEL-Prozessidentitaet fehlt:
    ``unit``, ``pid``, ``proc_start_ticks``, ``boot_id``, ``python_executable``.
    """
    import sys as _sys

    return build_process_marker(
        unit=unit,
        pid=pid,
        proc_start_ticks=(
            proc_start_ticks(pid) if proc_start_ticks_value is None else proc_start_ticks_value
        ),
        boot_id=current_boot_id() if boot_id is None else boot_id,
        repo_root=str(repo_root),
        runtime_code_sha=str(identity.runtime_commit or ""),
        python_executable=python_executable or _sys.executable,
        requirements_lock_sha256=identity.lock_sha256_at_start,
        started_at_utc=str(identity.started_at_utc or ""),
    )


def _parse_utc(value: str) -> datetime | None:
    """ISO-8601 nach aware-UTC. ``None``, wenn nicht beweisbar.

    ``...Z`` und ``...+00:00`` sind derselbe Moment, lexikografisch aber nicht
    vergleichbar: ``"Z" > "+"``. Ein Stringvergleich haette einen Prozess, der
    NACH dem Deploy startete, als "davor" gelesen — und umgekehrt.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    # Ein reines Datum ist gueltiges ISO-8601 und wird als Mitternacht gelesen.
    # Als Deploy-Zeitpunkt ist es wertlos: "am 02.09. deployt" beweist nicht,
    # dass ein um 07:00 gestarteter Prozess danach kam. Fail-closed.
    if "T" not in raw or ":" not in raw:
        return None
    if raw.endswith(("z", "Z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    # Ein naiver Zeitstempel bekommt hier KEINE Zeitzone verpasst. "06:00:00"
    # ohne Offset ist kein Moment, sondern eine Ablesung ohne Ort; ihn still als
    # UTC zu lesen waere eine erfundene Provenienz — und genau die Sorte
    # stillschweigender Annahme, gegen die dieser Marker gebaut ist.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _one(
    obs: ProcessObservation,
    marker: Mapping[str, Any] | None,
    *,
    expected_sha: str,
    expected_lock_sha256: str | None,
    checkout_sha: str,
    deployed_at_utc: str | None,
    expected_release_tree_sha256: str = "",
    expected_release_path: str = "",
    current_release_path: str = "",
    checkout_is_authoritative: bool = True,
) -> ProcessFinding:
    if marker is None:
        return ProcessFinding(
            obs.unit,
            STATE_UNKNOWN,
            "kein Startmarker — der Prozess hat nie bezeugt, welchen Code er laedt",
            expected_sha=expected_sha,
        )

    code_sha = str(marker.get("runtime_code_sha") or "")

    def invalid(detail: str) -> ProcessFinding:
        return ProcessFinding(obs.unit, STATE_INVALID, detail, code_sha, expected_sha)

    def unknown(detail: str) -> ProcessFinding:
        return ProcessFinding(obs.unit, STATE_UNKNOWN, detail, code_sha, expected_sha)

    if not expected_sha:
        return ProcessFinding(
            obs.unit,
            STATE_EXPECTED_UNKNOWN,
            "kein Deploy-Marker — der erwartete Stand ist nicht belegt",
            code_sha,
            expected_sha,
        )
    if checkout_is_authoritative and checkout_sha and checkout_sha != expected_sha:
        # Nur ohne aktives Release ist der Quell-Checkout der Massstab. Regiert
        # ein Release, ist er es NICHT: `pi_activate_release.sh` schaltet
        # `current` und schreibt den Deploy-Marker, fasst den Quellbaum aber
        # nicht an. Beim Rollback steht der Checkout dann legitim auf NEU,
        # waehrend deployt/aktiv/laufend alle drei ALT sind — dieser Vergleich
        # meldete dort DEPLOYMENT_PROVENANCE_MISMATCH fuer einen korrekten Zustand.
        return ProcessFinding(
            obs.unit,
            STATE_DEPLOY_PROVENANCE_MISMATCH,
            f"Checkout steht auf {checkout_sha[:8]}, deployt wurde {expected_sha[:8]}",
            code_sha,
            expected_sha,
        )

    # Identitaet zuerst, und FEHLENDE Identitaet ist kein Treffer: zwei leere
    # boot_ids sind gleich, beweisen aber nichts. Der Vorgaenger dieses Blocks
    # liess genau das als MATCH durchgehen.
    raw_pid = marker.get("pid")
    if not isinstance(raw_pid, int) or isinstance(raw_pid, bool):
        return invalid(f"Marker-PID ist keine Zahl: {raw_pid!r}")
    if raw_pid != obs.main_pid:
        return invalid(f"Marker-PID {raw_pid} != MainPID {obs.main_pid}")

    raw_ticks = marker.get("proc_start_ticks")
    if not isinstance(raw_ticks, int) or isinstance(raw_ticks, bool) or raw_ticks < 0:
        return unknown("Marker nennt keine lesbare Startzeit (proc_start_ticks)")
    if obs.proc_start_ticks < 0:
        return unknown(f"/proc/{obs.main_pid}/stat nicht lesbar — Startzeit nicht vergleichbar")
    if raw_ticks != obs.proc_start_ticks:
        return invalid("Startzeit weicht ab — PID wiederverwendet, Marker gehoert zum Vorgaenger")

    marker_boot = str(marker.get("boot_id") or "")
    if not marker_boot or not obs.boot_id:
        return unknown("boot_id fehlt auf einer Seite — Boot-Zugehoerigkeit nicht beweisbar")
    if marker_boot != obs.boot_id:
        return invalid("Marker stammt aus einem frueheren Boot")

    # Primaerbeweis: die beim Start geladene Revision.
    if not code_sha:
        return unknown("Marker bezeugt keine Revision")
    if code_sha != expected_sha:
        return ProcessFinding(
            obs.unit,
            STATE_CODE_DRIFT,
            (
                f"laeuft auf {code_sha[:8]}, erwartet {expected_sha[:8]}"
                + (
                    f"; der Checkout steht bereits auf {checkout_sha[:8]}"
                    if checkout_sha and checkout_sha != code_sha
                    else ""
                )
            ),
            code_sha,
            expected_sha,
        )

    # Der unveraenderliche Baum: Prozess, aktives Release und Deploy muessen
    # DIESELBE Release-Identitaet nennen. Ohne diese Achse beweist ein gleicher
    # Commit nur, dass irgendwo dieselbe Zahl steht — nicht, welche Bytes der
    # Prozess geladen hat.
    if expected_release_tree_sha256:
        marker_tree = str(marker.get("release_tree_sha256") or "")
        if not marker_tree:
            return unknown("Marker nennt keinen Release-Baum — geladene Bytes unbelegt")
        if marker_tree != expected_release_tree_sha256:
            return ProcessFinding(
                obs.unit,
                STATE_RELEASE_MISMATCH,
                (
                    f"Prozess laedt Release-Baum {marker_tree[:8]}, "
                    f"aktiv/deployt ist {expected_release_tree_sha256[:8]}"
                ),
                code_sha,
                expected_sha,
            )
    if current_release_path:
        marker_path_value = str(marker.get("release_path") or "")
        if not marker_path_value:
            return unknown("Marker nennt keinen Release-Pfad")
        if os.path.normcase(marker_path_value) != os.path.normcase(current_release_path):
            return ProcessFinding(
                obs.unit,
                STATE_RELEASE_MISMATCH,
                f"Prozess laeuft aus {marker_path_value}, current zeigt auf {current_release_path}",
                code_sha,
                expected_sha,
            )

    lock = marker.get("requirements_lock_sha256")
    # Eine fehlende Soll-Lock ist keine bestandene Pruefung, sondern eine
    # unvollstaendige Provenienz: ohne Soll laesst sich ueber die
    # Abhaengigkeiten des Prozesses nichts beweisen.
    if not expected_lock_sha256:
        return unknown("Deploy-Provenienz nennt keine Lock-SHA — Abhaengigkeiten unbelegt")
    if not lock:
        return unknown("Marker nennt keine Lock-SHA — Abhaengigkeiten nicht pruefbar")
    if str(lock) != expected_lock_sha256:
        return ProcessFinding(
            obs.unit,
            STATE_DEPENDENCY_DRIFT,
            "Abhaengigkeiten des Prozesses stammen aus einer anderen Lock-Datei",
            code_sha,
            expected_sha,
        )

    # Konsistenzkontrolle, nicht Primaerbeweis — aber eine UNTERLASSENE Kontrolle
    # ist kein bestandener Test. Fehlt der Deploy-Zeitstempel, laesst sich
    # ``process_started_at >= deployed_at`` nicht beweisen, und Unbeweisbares
    # darf nicht als MATCH enden.
    if not deployed_at_utc:
        return unknown("Deploy-Zeitstempel fehlt — Startrelation nicht beweisbar")
    deployed = _parse_utc(deployed_at_utc)
    started = _parse_utc(obs.started_at_utc)
    if deployed is None:
        return unknown(f"Deploy-Zeitstempel nicht beweisbar: {deployed_at_utc!r}")
    if started is None:
        return unknown(f"Startzeit nicht beweisbar: {obs.started_at_utc!r}")
    if started < deployed:
        return ProcessFinding(
            obs.unit,
            STATE_STALE_NO_RESTART,
            f"gestartet {started.isoformat()} vor dem Deploy {deployed.isoformat()}",
            code_sha,
            expected_sha,
        )

    return ProcessFinding(obs.unit, STATE_MATCH, "", code_sha, expected_sha)


def evaluate_process_markers(
    observations: Iterable[ProcessObservation],
    markers: Mapping[str, Mapping[str, Any] | None],
    *,
    expected_sha: str,
    checkout_sha: str = "",
    expected_lock_sha256: str | None = None,
    deployed_at_utc: str | None = None,
    expected_units: Iterable[str] = (),
    expected_release_tree_sha256: str = "",
    expected_release_path: str = "",
    current_release_path: str = "",
    current_release_tree_sha256: str = "",
    checkout_is_authoritative: bool = True,
) -> ProcessProvenance:
    """Rein: aus Marker und Beobachtung ein Urteil. Keine Uhr, kein I/O.

    ``expected_units`` ist die Menge der Units, die laufen MUESSEN. Ohne sie
    waere "0 von 0 Diensten in Ordnung" ein Urteil ueber nichts — und genau das
    hat frueher als ``OK`` gegolten. Eine erwartete Unit ohne Beobachtung ist
    ``EXPECTED_UNIT_NOT_RUNNING`` und damit HOLD.
    """
    seen = {obs.unit for obs in observations}
    findings = tuple(
        _one(
            obs,
            markers.get(obs.unit),
            expected_sha=expected_sha,
            expected_lock_sha256=expected_lock_sha256,
            checkout_sha=checkout_sha,
            deployed_at_utc=deployed_at_utc,
            expected_release_tree_sha256=expected_release_tree_sha256,
            expected_release_path=expected_release_path,
            current_release_path=current_release_path,
            checkout_is_authoritative=checkout_is_authoritative,
        )
        for obs in observations
    ) + tuple(
        ProcessFinding(
            unit,
            STATE_NOT_RUNNING,
            "erwartete Unit laeuft nicht — ueber ihren Code ist nichts bezeugt",
            expected_sha=expected_sha,
        )
        for unit in sorted(set(expected_units) - seen)
    )
    matching = sum(1 for f in findings if f.state == STATE_MATCH)
    open_reasons = {f.state for f in findings if not f.passing}

    # B3 — der Deploy-Marker ist SSOT, auch fuer Baum und Pfad. Ohne diese Achse
    # koennte deploy TREE_A fuehren, waehrend `current` und jeder Prozess TREE_B
    # tragen: alle Unit-Pruefungen gruen, und trotzdem laeuft nichts von dem,
    # was deployt wurde. Vorher kam das SOLL aus dem aktiven Release selbst —
    # ein Vergleich des Aktiven mit sich selbst.
    active_vs_deployed: list[str] = []
    if expected_release_tree_sha256 and current_release_tree_sha256:
        if expected_release_tree_sha256 != current_release_tree_sha256:
            active_vs_deployed.append(
                f"aktiver Baum {current_release_tree_sha256[:8]} != deployt "
                f"{expected_release_tree_sha256[:8]}"
            )
    if expected_release_path and current_release_path:
        if os.path.normcase(expected_release_path) != os.path.normcase(current_release_path):
            active_vs_deployed.append(
                f"current zeigt auf {current_release_path}, deployt ist {expected_release_path}"
            )
    if active_vs_deployed:
        open_reasons.add(REASON_ACTIVE_RELEASE_NOT_DEPLOYED)

    reasons = tuple(sorted(open_reasons))
    return ProcessProvenance(
        verdict=VERDICT_HOLD if reasons else VERDICT_OK,
        findings=findings,
        expected_sha=expected_sha,
        units_total=len(findings),
        units_matching=matching,
        reasons=reasons,
        detail={
            "checkout_sha": checkout_sha,
            "deployed_at_utc": deployed_at_utc,
            "expected_lock_sha256": expected_lock_sha256,
            "active_vs_deployed": tuple(active_vs_deployed),
        },
    )


def render_process_provenance(p: ProcessProvenance) -> str:
    head = (
        f"process-runtime: {p.verdict} — {p.units_matching} von {p.units_total} "
        f"Diensten bezeugen {p.expected_sha[:8]}"
    )
    if p.ok:
        return head + "; jeder laufende Prozess hat seinen Code beim Start bezeugt"
    lines = [f"{f.state} {f.unit}: {f.detail}" for f in p.findings if not f.passing]
    for detail in p.detail.get("active_vs_deployed") or ():
        lines.append(f"{REASON_ACTIVE_RELEASE_NOT_DEPLOYED}: {detail}")
    return "; ".join([head] + lines)


# ── Erhebung (unrein, absichtlich duenn) ────────────────────────────────────


def current_boot_id(proc: Path = Path("/proc/sys/kernel/random/boot_id")) -> str:
    try:
        return proc.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def proc_start_ticks(pid: int, proc_root: Path = Path("/proc")) -> int:
    """Feld 22 aus ``/proc/<pid>/stat`` — monoton, boot-relativ, faelschungssicher.

    Der Kommandoname steht in Klammern und darf Leerzeichen enthalten; deshalb
    wird ab der schliessenden Klammer geteilt und nicht naiv am Leerzeichen.
    """
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return -1
    close = raw.rfind(")")
    if close < 0:
        return -1
    parts = raw[close + 2 :].split()
    try:
        return int(parts[19])
    except (IndexError, ValueError):
        return -1


def marker_path(unit: str, *, root: Path, marker_dir: Path = DEFAULT_MARKER_DIR) -> Path:
    return root / marker_dir / f"{unit}.json"


def write_process_marker(marker: Mapping[str, Any], *, root: Path) -> Path:
    path = marker_path(str(marker.get("unit") or "unknown"), root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_process_markers(units: Iterable[str], *, root: Path) -> dict[str, dict[str, Any] | None]:
    out: dict[str, dict[str, Any] | None] = {}
    for unit in units:
        try:
            doc = json.loads(marker_path(unit, root=root).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out[unit] = None
            continue
        out[unit] = doc if isinstance(doc, dict) and doc.get("schema") == MARKER_SCHEMA else None
    return out


def build_deployment_marker(
    *, repo_sha: str, requirements_lock_sha256: str | None, deployed_at_utc: str | None = None
) -> dict[str, Any]:
    return {
        "schema": DEPLOY_MARKER_SCHEMA,
        "repo_sha": repo_sha,
        "deployed_at_utc": deployed_at_utc or datetime.now(UTC).isoformat(timespec="seconds"),
        "requirements_lock_sha256": requirements_lock_sha256,
    }


def read_deployment_marker(root: Path, rel: Path = DEFAULT_DEPLOY_MARKER) -> dict[str, Any] | None:
    try:
        doc = json.loads((root / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == DEPLOY_MARKER_SCHEMA else None


def self_marker(
    *, unit: str, repo_root: Path, runtime_code_sha: str, lock_sha256: str | None
) -> dict[str, Any]:
    """Der Marker dieses Prozesses — aufzurufen unmittelbar nach dem Start."""
    pid = os.getpid()
    import sys

    return build_process_marker(
        unit=unit,
        pid=pid,
        proc_start_ticks=proc_start_ticks(pid),
        boot_id=current_boot_id(),
        repo_root=str(repo_root),
        runtime_code_sha=runtime_code_sha,
        python_executable=sys.executable,
        requirements_lock_sha256=lock_sha256,
        started_at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
    )


__all__ = [
    "DEFAULT_DEPLOY_MARKER",
    "DEFAULT_MARKER_DIR",
    "DEPLOY_MARKER_SCHEMA",
    "MARKER_SCHEMA",
    "NOT_PASSING",
    "STATE_CODE_DRIFT",
    "STATE_DEPENDENCY_DRIFT",
    "STATE_INVALID",
    "STATE_MATCH",
    "STATE_NOT_RUNNING",
    "REASON_ACTIVE_RELEASE_NOT_DEPLOYED",
    "STATE_RELEASE_MISMATCH",
    "STATE_STALE_NO_RESTART",
    "STATE_DEPLOY_PROVENANCE_MISMATCH",
    "STATE_EXPECTED_UNKNOWN",
    "STATE_UNKNOWN",
    "VERDICT_HOLD",
    "VERDICT_OK",
    "ProcessFinding",
    "ProcessObservation",
    "ProcessProvenance",
    "build_deployment_marker",
    "RuntimeIdentityLike",
    "build_process_marker",
    "bind_argv_to_release",
    "self_attest_and_exec",
    "marker_from_identity",
    "marker_from_release",
    "current_boot_id",
    "evaluate_process_markers",
    "marker_path",
    "proc_start_ticks",
    "read_deployment_marker",
    "read_process_markers",
    "render_process_provenance",
    "self_marker",
    "write_process_marker",
]
