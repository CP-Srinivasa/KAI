"""ONE_PRODUCTION_REVISION — läuft jeder produktive Dienst wirklich auf dem Stand,
den der Deploy behauptet?

Befund 2026-09-01, der dieses Modul ausgelöst hat: ``kai-tg-listener`` startete
laut ``ExecStart`` aus ``/home/kai/ai_analyst_trading_bot/.venv/bin/python``.
Daraus habe ich auf einen zweiten, nicht aktualisierten Checkout geschlossen —
**falsch**. ``/home/kai/...`` löst auf ``/home/ubuntu/...`` auf, es gibt genau
einen Baum. Der echte Defekt war ein anderer und subtilerer: der Prozess lief
seit dem 26.08. und hielt die **alten Bibliotheken im Speicher**, während auf
der Platte längst neue lagen.

Beides — der falsche Verdacht wie der echte Befund — hat dieselbe Wurzel: es
gab keine Messung, die vom laufenden PROZESS aus zurück auf Checkout, Revision
und venv schließt. `/health=200` und `active` beantworten die Frage nicht; ein
Dienst mit monatealtem Code im Speicher meldet beides fröhlich.

Zwei Verträge:

* **Code:** jeder produktive, repo-basierte Dienst läuft auf
  ``EXPECTED_MAINLINE_SHA``. Abweichung ⇒ ``RUNTIME_CODE_DRIFT`` (HOLD, kein
  Warning).
* **Abhängigkeiten:** ein Marker wird **erst nach** erfolgreichem
  ``pip install -r requirements.lock`` **und** ``pip check`` geschrieben. Er
  trägt ``repo_sha``, ``requirements_lock_sha256``, ``python_executable``,
  ``installed_at_utc``. Passt er nicht zum aktuellen Checkout ⇒
  ``DEPENDENCY_DRIFT`` (HOLD).

Die Erhebung ist unrein (systemd, ``/proc``), die **Entscheidung** ist rein und
ohne Uhr — Testbarkeit liegt in der Trennung, nicht in Mocks.

Ehrliche Grenze, gemessen am 2026-09-01: erfasst werden Dienste im Zustand
``running``. Ein Dienst, der gerade **neu startet**, ist in diesem Moment nicht
``running`` und taucht nicht auf — ein Lauf mitten im Deploy meldete deshalb
zwei statt fünf Dienste. Das ist kein stiller Fehlbefund (``services_total``
steht im Urteil und fällt sichtbar), aber der Vertrag ist eine Momentaufnahme
und keine Dauerüberwachung. Ob ein Dienst überhaupt läuft, beantworten
``failed_units`` und die Timer-Wache — nicht dieses Modul.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Wo der Abhängigkeits-Marker liegt. Im Checkout, nicht in ``/etc`` — er
#: beschreibt diesen Checkout und wandert mit ihm.
DEFAULT_MARKER_RELPATH = Path("artifacts") / "runtime" / "dependency_marker.json"

MARKER_SCHEMA = "dependency_marker/v1"

VERDICT_OK = "OK"
VERDICT_HOLD = "HOLD"

REASON_CODE_DRIFT = "RUNTIME_CODE_DRIFT"
REASON_DEPENDENCY_DRIFT = "DEPENDENCY_DRIFT"
REASON_NOT_MEASURABLE = "RUNTIME_NOT_MEASURABLE"


@dataclass(frozen=True)
class ServiceRuntime:
    """Was ein laufender Dienst TATSÄCHLICH benutzt — nicht, was die Unit sagt.

    ``repo_root`` und ``repo_sha`` sind ``None``, wenn der Dienst nicht
    repo-basiert ist (dann gilt der Code-Vertrag für ihn nicht) oder wenn die
    Messung nicht möglich war (dann gilt er als nicht messbar, was ebenfalls
    ein Befund ist — niemals stillschweigend „in Ordnung").
    """

    unit: str
    user: str
    pid: int
    executable: str
    cwd: str
    repo_root: str | None = None
    repo_sha: str | None = None
    venv: str | None = None
    lock_sha256: str | None = None
    measurable: bool = True
    note: str = ""

    @property
    def repo_based(self) -> bool:
        return self.repo_root is not None


@dataclass(frozen=True)
class ProvenanceVerdict:
    """Urteil plus vollständige Zerlegung — nie eine Summe ohne die Zeilen."""

    verdict: str
    reasons: tuple[str, ...] = ()
    expected_sha: str = ""
    drifted: tuple[dict[str, Any], ...] = ()
    unmeasurable: tuple[dict[str, Any], ...] = ()
    dependency_findings: tuple[str, ...] = ()
    services_total: int = 0
    services_repo_based: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.verdict == VERDICT_OK


def _short(sha: str | None) -> str:
    return (sha or "")[:8]


def sha256_of(path: Path) -> str | None:
    """Datei-SHA-256 oder ``None`` — eine unlesbare Datei ist kein Hash."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# ── Marker ──────────────────────────────────────────────────────────────────


def build_marker(
    *,
    repo_sha: str,
    requirements_lock_sha256: str,
    python_executable: str,
    installed_at: datetime,
    pip_check_ok: bool,
) -> dict[str, Any]:
    """Der Marker wird NUR gebaut, wenn Installation und ``pip check`` trugen.

    ``pip_check_ok=False`` ist ein Fehler, kein Feld: ein Marker, der einen
    fehlgeschlagenen Lauf dokumentiert, wäre schlimmer als keiner — er sähe
    beim nächsten Deploy wie ein Beweis aus.
    """
    if not pip_check_ok:
        raise ValueError(
            "Marker wird nur nach erfolgreichem 'pip check' geschrieben — "
            "sonst behauptet er eine Synchronisierung, die nicht stattgefunden hat."
        )
    return {
        "schema": MARKER_SCHEMA,
        "repo_sha": repo_sha,
        "requirements_lock_sha256": requirements_lock_sha256,
        "python_executable": python_executable,
        "installed_at_utc": installed_at.astimezone(UTC).isoformat(),
    }


def read_marker(path: Path) -> dict[str, Any] | None:
    """Fail-soft lesen. Eine kaputte Datei ist kein Marker (und damit ein Befund)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != MARKER_SCHEMA:
        return None
    return data


def evaluate_dependency_marker(
    marker: dict[str, Any] | None,
    *,
    checkout_sha: str,
    checkout_lock_sha256: str | None,
) -> tuple[str, ...]:
    """Passt der Marker zum aktuellen Checkout? Jede Abweichung einzeln benannt."""
    if marker is None:
        return (
            "kein gueltiger Abhaengigkeits-Marker vorhanden — es ist unbelegt, ob "
            "die Umgebung je gegen dieses requirements.lock synchronisiert wurde",
        )
    findings: list[str] = []
    if str(marker.get("repo_sha")) != checkout_sha:
        findings.append(
            f"Marker wurde fuer repo_sha {_short(str(marker.get('repo_sha')))} geschrieben, "
            f"der Checkout steht auf {_short(checkout_sha)}"
        )
    if checkout_lock_sha256 is None:
        findings.append("requirements.lock im Checkout nicht lesbar — Abgleich unmoeglich")
    elif str(marker.get("requirements_lock_sha256")) != checkout_lock_sha256:
        findings.append(
            "requirements.lock hat sich seit der letzten Synchronisierung geaendert "
            f"({_short(str(marker.get('requirements_lock_sha256')))} -> "
            f"{_short(checkout_lock_sha256)})"
        )
    return tuple(findings)


# ── Der Vertrag ─────────────────────────────────────────────────────────────


def evaluate_provenance(
    services: list[ServiceRuntime],
    *,
    expected_sha: str,
    marker: dict[str, Any] | None = None,
    checkout_sha: str | None = None,
    checkout_lock_sha256: str | None = None,
) -> ProvenanceVerdict:
    """Rein: aus gemessenen Zuständen ein Urteil. Keine Uhr, kein I/O.

    HOLD, nicht WARNING — ein Dienst auf altem Code ist kein Schönheitsfehler,
    sondern die Aussage „deployt" ohne Deckung.
    """
    repo_based = [s for s in services if s.repo_based]
    drifted = [s for s in repo_based if s.measurable and (s.repo_sha or "") != expected_sha]
    unmeasurable = [s for s in services if not s.measurable]

    dependency_findings: tuple[str, ...] = ()
    if checkout_sha is not None:
        dependency_findings = evaluate_dependency_marker(
            marker, checkout_sha=checkout_sha, checkout_lock_sha256=checkout_lock_sha256
        )

    reasons: list[str] = []
    if drifted:
        reasons.append(REASON_CODE_DRIFT)
    if unmeasurable:
        reasons.append(REASON_NOT_MEASURABLE)
    if dependency_findings:
        reasons.append(REASON_DEPENDENCY_DRIFT)

    return ProvenanceVerdict(
        verdict=VERDICT_HOLD if reasons else VERDICT_OK,
        reasons=tuple(reasons),
        expected_sha=expected_sha,
        drifted=tuple(
            {"unit": s.unit, "repo_root": s.repo_root, "repo_sha": s.repo_sha, "pid": s.pid}
            for s in drifted
        ),
        unmeasurable=tuple({"unit": s.unit, "note": s.note} for s in unmeasurable),
        dependency_findings=dependency_findings,
        services_total=len(services),
        services_repo_based=len(repo_based),
        detail={
            "checkout_sha": checkout_sha,
            "checkout_lock_sha256": checkout_lock_sha256,
            "marker": marker,
        },
    )


def render_verdict(v: ProvenanceVerdict) -> str:
    """Operator-Text: Urteil, dann die Zeilen, die es tragen."""
    head = (
        f"runtime-provenance: {v.verdict} — {v.services_repo_based} von "
        f"{v.services_total} Diensten repo-basiert, erwartet "
        f"{_short(v.expected_sha)}"
    )
    if v.ok:
        return head + "; alle auf dem erwarteten Stand, Abhaengigkeiten synchron"
    parts = [head]
    for d in v.drifted:
        parts.append(
            f"CODE-DRIFT {d['unit']} (pid {d['pid']}) laeuft auf "
            f"{_short(str(d['repo_sha']))} aus {d['repo_root']}"
        )
    for u in v.unmeasurable:
        parts.append(f"NICHT MESSBAR {u['unit']}: {u['note']}")
    for f in v.dependency_findings:
        parts.append(f"DEPENDENCY-DRIFT {f}")
    return "; ".join(parts)


# ── Erhebung (unrein, absichtlich duenn) ────────────────────────────────────


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=20, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def collect_runtime_services(
    *,
    unit_glob: str = "kai-*.service",
    runner: Callable[[list[str]], str] = _run,
) -> list[ServiceRuntime]:
    """Vom laufenden PROZESS aus messen, nicht von der Unit-Datei aus.

    Der Unterschied ist der ganze Punkt: ``ExecStart`` sagt, womit gestartet
    werden SOLLTE. ``/proc/<pid>`` sagt, was tatsaechlich laeuft — inklusive
    aufgeloester Symlinks. Genau daran ist der Verdacht vom 2026-09-01
    gescheitert und die echte Speicher-Drift sichtbar geworden.
    """
    listing = runner(["systemctl", "list-units", unit_glob, "--state=running", "--no-legend"])
    out: list[ServiceRuntime] = []
    for line in listing.splitlines():
        unit = line.split()[0] if line.split() else ""
        if not unit:
            continue
        user = runner(["systemctl", "show", unit, "-p", "User", "--value"]) or "root"
        pid_raw = runner(["systemctl", "show", unit, "-p", "MainPID", "--value"])
        try:
            pid = int(pid_raw or 0)
        except ValueError:
            pid = 0

        exe = cwd = ""
        if pid > 0:
            exe = runner(["readlink", "-f", f"/proc/{pid}/exe"])
            cwd = runner(["readlink", "-f", f"/proc/{pid}/cwd"])
        if not exe or not cwd:
            out.append(
                ServiceRuntime(
                    unit=unit,
                    user=user,
                    pid=pid,
                    executable=exe or "?",
                    cwd=cwd or "?",
                    measurable=False,
                    note=f"/proc/{pid} nicht lesbar (fremder Benutzer oder Prozess weg)",
                )
            )
            continue

        repo_root = repo_sha = venv = lock = None
        candidate = Path(cwd)
        if (candidate / ".git").exists():
            repo_root = str(candidate)
            repo_sha = runner(["git", "-C", repo_root, "rev-parse", "HEAD"]) or None
            lock = sha256_of(candidate / "requirements.lock")
        if "/.venv/bin/" in exe:
            venv = exe.split("/bin/")[0]

        out.append(
            ServiceRuntime(
                unit=unit,
                user=user,
                pid=pid,
                executable=exe,
                cwd=cwd,
                repo_root=repo_root,
                repo_sha=repo_sha,
                venv=venv,
                lock_sha256=lock,
            )
        )
    return out


def as_rows(services: list[ServiceRuntime]) -> list[dict[str, Any]]:
    return [asdict(s) for s in services]


__all__ = [
    "DEFAULT_MARKER_RELPATH",
    "MARKER_SCHEMA",
    "REASON_CODE_DRIFT",
    "REASON_DEPENDENCY_DRIFT",
    "REASON_NOT_MEASURABLE",
    "VERDICT_HOLD",
    "VERDICT_OK",
    "ProvenanceVerdict",
    "ServiceRuntime",
    "as_rows",
    "build_marker",
    "collect_runtime_services",
    "evaluate_dependency_marker",
    "evaluate_provenance",
    "read_marker",
    "render_verdict",
    "sha256_of",
]
