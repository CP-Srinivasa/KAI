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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

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

#: Zustaende, die niemals als „in Ordnung" durchgehen duerfen.
NOT_PASSING: Final = frozenset(
    {
        STATE_CODE_DRIFT,
        STATE_STALE_NO_RESTART,
        STATE_DEPENDENCY_DRIFT,
        STATE_INVALID,
        STATE_UNKNOWN,
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
    }


def _one(
    obs: ProcessObservation,
    marker: Mapping[str, Any] | None,
    *,
    expected_sha: str,
    expected_lock_sha256: str | None,
    checkout_sha: str,
    deployed_at_utc: str | None,
) -> ProcessFinding:
    if marker is None:
        return ProcessFinding(
            obs.unit,
            STATE_UNKNOWN,
            "kein Startmarker — der Prozess hat nie bezeugt, welchen Code er laedt",
            expected_sha=expected_sha,
        )

    code_sha = str(marker.get("runtime_code_sha") or "")

    # Identitaet zuerst: ein Marker, der zu einem anderen Prozess gehoert, sagt
    # ueber diesen hier nichts — auch dann nicht, wenn seine SHA zufaellig passt.
    if int(marker.get("pid") or 0) != obs.main_pid:
        return ProcessFinding(
            obs.unit,
            STATE_INVALID,
            f"Marker-PID {marker.get('pid')} != MainPID {obs.main_pid}",
            code_sha,
            expected_sha,
        )
    if int(marker.get("proc_start_ticks") or -1) != obs.proc_start_ticks:
        return ProcessFinding(
            obs.unit,
            STATE_INVALID,
            "Startzeit weicht ab — PID wiederverwendet, Marker gehoert zum Vorgaenger",
            code_sha,
            expected_sha,
        )
    if str(marker.get("boot_id") or "") != obs.boot_id:
        return ProcessFinding(
            obs.unit,
            STATE_INVALID,
            "Marker stammt aus einem frueheren Boot",
            code_sha,
            expected_sha,
        )

    # Primaerbeweis: die beim Start geladene Revision.
    if code_sha != expected_sha:
        return ProcessFinding(
            obs.unit,
            STATE_CODE_DRIFT,
            (
                f"laeuft auf {code_sha[:8] or '?'}, erwartet {expected_sha[:8]}"
                + (
                    f"; der Checkout steht bereits auf {checkout_sha[:8]}"
                    if checkout_sha and checkout_sha != code_sha
                    else ""
                )
            ),
            code_sha,
            expected_sha,
        )

    lock = marker.get("requirements_lock_sha256")
    if expected_lock_sha256 and lock and str(lock) != expected_lock_sha256:
        return ProcessFinding(
            obs.unit,
            STATE_DEPENDENCY_DRIFT,
            "Abhaengigkeiten des Prozesses stammen aus einer anderen Lock-Datei",
            code_sha,
            expected_sha,
        )

    # Konsistenzkontrolle, nicht Primaerbeweis: wer nach dem Deploy nicht neu
    # gestartet wurde, kann den neuen Code nicht geladen haben.
    if deployed_at_utc and obs.started_at_utc and obs.started_at_utc < deployed_at_utc:
        return ProcessFinding(
            obs.unit,
            STATE_STALE_NO_RESTART,
            f"gestartet {obs.started_at_utc} vor dem Deploy {deployed_at_utc}",
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
) -> ProcessProvenance:
    """Rein: aus Marker und Beobachtung ein Urteil. Keine Uhr, kein I/O."""
    findings = tuple(
        _one(
            obs,
            markers.get(obs.unit),
            expected_sha=expected_sha,
            expected_lock_sha256=expected_lock_sha256,
            checkout_sha=checkout_sha,
            deployed_at_utc=deployed_at_utc,
        )
        for obs in observations
    )
    matching = sum(1 for f in findings if f.state == STATE_MATCH)
    reasons = tuple(sorted({f.state for f in findings if not f.passing}))
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
        },
    )


def render_process_provenance(p: ProcessProvenance) -> str:
    head = (
        f"process-runtime: {p.verdict} — {p.units_matching} von {p.units_total} "
        f"Diensten bezeugen {p.expected_sha[:8]}"
    )
    if p.ok:
        return head + "; jeder laufende Prozess hat seinen Code beim Start bezeugt"
    return "; ".join(
        [head] + [f"{f.state} {f.unit}: {f.detail}" for f in p.findings if not f.passing]
    )


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
    "STATE_STALE_NO_RESTART",
    "STATE_UNKNOWN",
    "VERDICT_HOLD",
    "VERDICT_OK",
    "ProcessFinding",
    "ProcessObservation",
    "ProcessProvenance",
    "build_deployment_marker",
    "build_process_marker",
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
