"""Runtime-Identität: welcher Commit läuft im Prozess — und welcher liegt im Checkout?

WARUM (25.08.2026, live gemessen): `kai-server` lief seit dem 18.08. 22:30 auf
`79e6fca7`. Der Checkout stand 23 Commits weiter, die Mainline 27. Vier
Fast-Forward-Merges am 20./21.08. hatten den Checkout bewegt, ohne den Prozess
neu zu starten. Timer und CLI-Prozesse luden bereits neuen Code, der lang
laufende Server hielt alte Module im Speicher — sieben Tage lang, und kein
Signal zeigte es an: `/health` kannte nur `{"status":"ok","version":"0.1.0"}`.

Was dieses Modul hält:

* **Einmal beim Start** wird die Identität des Prozesses eingefroren
  (Commit, Lock-Hash, Startzeit, PID) — nie pro Request, denn genau dieser
  Wert darf sich zur Laufzeit nicht ändern.
* **Pro Anfrage** wird der Checkout billig gelesen (`.git/HEAD` + Ref-Datei,
  kein Subprozess) und der Abstand nur dann per `git rev-list` gezählt, wenn
  sich der Checkout seit dem letzten Mal bewegt hat.
* **Eine Bewertung** macht aus Zahlen einen Befund — dieselbe Funktion für
  `/health`-Konsumenten und den Health-Check-Timer, damit die Invariante nicht
  an zwei Stellen driftet (Lehre 21.08.: doppelt implementierte Invarianten
  driften).

Alles ist fail-soft: fehlt `git` oder ist das Verzeichnis kein Repo, gibt es
`None` — nie einen Absturz im Health-Pfad. Aber `None` heißt „nicht messbar",
nicht „aktuell": die Bewertung meldet das ausdrücklich.

Release-Modus (Cutover 2026-09-04): Die Daemons laufen aus
``releases/<SHA>/`` — ohne ``.git``. Dort ist ``release.json`` die Wahrheit
(``runtime_source="release"``), und die Referenz für Drift ist das Release, auf
das ``current`` JETZT zeigt — nicht der wandernde Checkout, der die Daemons nach
dem Cutover nicht mehr speist. Ohne ``current`` bleibt der Checkout die Referenz.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE_NAME = "requirements.lock"
ARTIFACT_RELATIVE_PATH = Path("artifacts") / "runtime" / "runtime_identity.json"
SCHEMA = "runtime_identity/v1"

# Drift direkt nach einem Pull ist normal — der Deploy ist noch unterwegs.
# Erst wenn der Checkout LÄNGER als diese Spanne auf dem neuen Commit steht,
# ist der laufende Prozess nachweislich veraltet.
DRIFT_GRACE_S = 3600.0
DRIFT_CRITICAL_AFTER_S = 86400.0

_GIT_TIMEOUT_S = 10
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RuntimeIdentity:
    """Eingefrorener Zustand eines Prozesses zum Startzeitpunkt."""

    schema: str
    runtime_commit: str | None
    started_at_utc: str
    lock_sha256_at_start: str | None
    pid: int
    #: "release" (release.json eines versiegelten Baums) | "checkout" (.git) | None.
    runtime_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DriftFinding:
    severity: str  # "warning" | "critical"
    message: str


# ── git, fail-soft ────────────────────────────────────────────────────────────


def _git_stdout(args: list[str], repo_dir: Path, run: Runner) -> str | None:
    try:
        proc = run(  # noqa: S603 - feste Argumentliste, kein shell
            ["git", *args],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    return out or None


def git_head_commit(repo_dir: Path | str, run: Runner = subprocess.run) -> str | None:
    out = _git_stdout(["rev-parse", "HEAD"], Path(repo_dir), run)
    return out if out and _SHA_RE.match(out) else None


def _git_dirs(repo_dir: Path) -> tuple[Path, Path] | None:
    """(gitdir, commondir) — auch für verknüpfte Worktrees (``.git`` ist eine Datei)."""
    dotgit = repo_dir / ".git"
    if dotgit.is_dir():
        gitdir = dotgit
    elif dotgit.is_file():
        text = dotgit.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return None
        raw = Path(text[len("gitdir:") :].strip())
        gitdir = raw if raw.is_absolute() else (repo_dir / raw)
        if not gitdir.is_dir():
            return None
    else:
        return None
    commondir_file = gitdir / "commondir"
    if commondir_file.is_file():
        rel = Path(commondir_file.read_text(encoding="utf-8").strip())
        commondir = rel if rel.is_absolute() else (gitdir / rel)
    else:
        commondir = gitdir
    return gitdir, commondir


def _resolve_ref(ref: str, gitdir: Path, commondir: Path) -> tuple[str | None, Path | None]:
    """Commit + Datei, die den Stand trägt (für die Stabilitäts-Messung)."""
    for base in (gitdir, commondir):
        candidate = base / ref
        if candidate.is_file():
            value = candidate.read_text(encoding="utf-8").strip()
            return (value if _SHA_RE.match(value) else None), candidate
    packed = commondir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if not line or line[0] in "#^":
                continue
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref and _SHA_RE.match(parts[0]):
                return parts[0], packed
    return None, None


def _read_head_without_git(repo_dir: Path) -> tuple[str | None, Path | None]:
    dirs = _git_dirs(repo_dir)
    if dirs is None:
        return None, None
    gitdir, commondir = dirs
    head_file = gitdir / "HEAD"
    if not head_file.is_file():
        return None, None
    head = head_file.read_text(encoding="utf-8").strip()
    if head.startswith("ref:"):
        return _resolve_ref(head[len("ref:") :].strip(), gitdir, commondir)
    return (head if _SHA_RE.match(head) else None), head_file


def read_checkout_commit(repo_dir: Path | str, run: Runner = subprocess.run) -> str | None:
    """Commit des Checkouts — billig über die Ref-Dateien, ``git`` nur als Fallback."""
    path = Path(repo_dir)
    try:
        commit, _ = _read_head_without_git(path)
    except OSError:
        commit = None
    return commit or git_head_commit(path, run)


# ── Release-Modus ─────────────────────────────────────────────────────────────

RUNTIME_SOURCE_RELEASE = "release"
RUNTIME_SOURCE_CHECKOUT = "checkout"


def read_release_commit(repo_dir: Path | str) -> str | None:
    """Commit aus ``release.json`` eines unveraenderlichen Release-Baums.

    Nach dem Cutover laeuft kai-server aus ``releases/<SHA>/`` — dort gibt es kein
    ``.git``; die Wahrheit steht im versiegelten Manifest. Fremdes/kaputtes
    Manifest → ``None`` (nicht messbar), nie ein geratener Wert.
    """
    from app.observability.release_identity import read_release_manifest

    manifest = read_release_manifest(Path(repo_dir))
    if manifest is None:
        return None
    sha = manifest.repo_sha.strip().lower()
    return sha if _SHA_RE.match(sha) else None


def read_runtime_commit(
    repo_dir: Path | str, run: Runner = subprocess.run
) -> tuple[str | None, str | None]:
    """(Commit, Quelle). Manifest vor Git: der versiegelte Baum ist die staerkere Aussage."""
    release = read_release_commit(repo_dir)
    if release:
        return release, RUNTIME_SOURCE_RELEASE
    checkout = read_checkout_commit(repo_dir, run)
    if checkout:
        return checkout, RUNTIME_SOURCE_CHECKOUT
    return None, None


def find_current_link(repo_dir: Path | str) -> Path | None:
    """``current`` neben dem Checkout (``…/current``) oder neben ``releases/``.

    Layout auf der Pi: ``…/ai_analyst_trading_bot`` (Checkout),
    ``…/releases/<SHA>`` (Release), ``…/current`` (Link auf ein Release).
    Nur ein Ziel mit gueltigem Manifest zaehlt — ein fremdes ``current`` nicht.
    Bewusst leichter als ``process_runtime_probe.release_governs``: hier geht es
    nur um die Referenz fuer Drift, nicht um den Beweis der ganzen Kette.
    """
    from app.observability.release_identity import read_release_manifest, resolve_current

    try:
        path = Path(repo_dir).resolve()
    except OSError:
        return None
    for candidate in (path.parent / "current", path.parent.parent / "current"):
        target = resolve_current(candidate)
        if target is not None and read_release_manifest(target) is not None:
            return candidate
    return None


def active_release_commit(repo_dir: Path | str) -> str | None:
    """Commit des Releases, auf das ``current`` JETZT zeigt — die Referenz fuer Drift."""
    from app.observability.release_identity import resolve_current

    link = find_current_link(repo_dir)
    if link is None:
        return None
    target = resolve_current(link)
    return read_release_commit(target) if target is not None else None


def reference_stable_for_s(repo_dir: Path | str, *, now: datetime | None = None) -> float | None:
    """Seit wann steht die Referenz — ``current`` (lstat-mtime des Links), sonst der Checkout."""
    link = find_current_link(repo_dir)
    if link is None:
        return checkout_stable_for_s(repo_dir, now=now)
    try:
        mtime = os.lstat(link).st_mtime
    except OSError:
        return None
    current = (now or datetime.now(UTC)).timestamp()
    return max(0.0, current - mtime)


def checkout_stable_for_s(repo_dir: Path | str, *, now: datetime | None = None) -> float | None:
    """Seit wie vielen Sekunden steht der Checkout auf seinem aktuellen Commit?

    Gemessen an der mtime der Ref-Datei (ein ff-Merge schreibt sie neu). ``None``,
    wenn nicht messbar — das ist NICHT dasselbe wie „gerade eben".
    """
    try:
        _, ref_file = _read_head_without_git(Path(repo_dir))
        if ref_file is None:
            return None
        mtime = ref_file.stat().st_mtime
    except OSError:
        return None
    current = (now or datetime.now(UTC)).timestamp()
    return max(0.0, current - mtime)


# ── Lock-Datei ────────────────────────────────────────────────────────────────

_LOCK_CACHE: dict[str, tuple[tuple[int, int], str]] = {}


def lock_sha256(repo_dir: Path | str) -> str | None:
    """sha256 der Lock-Datei, per (mtime_ns, size) gecacht — nicht pro Request neu hashen."""
    path = Path(repo_dir) / LOCK_FILE_NAME
    try:
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        cached = _LOCK_CACHE.get(str(path))
        if cached is not None and cached[0] == key:
            return cached[1]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    _LOCK_CACHE[str(path)] = (key, digest)
    return digest


# ── Drift ─────────────────────────────────────────────────────────────────────

_DRIFT_CACHE: dict[tuple[str, str], int | None] = {}


def count_commits_between(
    old: str, new: str, repo_dir: Path | str, run: Runner = subprocess.run
) -> int | None:
    """Wie viele Commits liegt ``new`` vor ``old``? ``None`` = nicht messbar."""
    if old == new:
        return 0
    key = (old, new)
    if key in _DRIFT_CACHE:
        return _DRIFT_CACHE[key]
    out = _git_stdout(["rev-list", "--count", f"{old}..{new}"], Path(repo_dir), run)
    value = int(out) if out is not None and out.isdigit() else None
    if value is not None:
        _DRIFT_CACHE[key] = value
    return value


# ── Capture (einmal pro Prozess) ──────────────────────────────────────────────


def capture_runtime_identity(
    repo_dir: Path | str = REPO_ROOT,
    *,
    now: datetime | None = None,
    run: Runner = subprocess.run,
    pid: int | None = None,
) -> RuntimeIdentity:
    path = Path(repo_dir)
    commit, source = read_runtime_commit(path, run)
    return RuntimeIdentity(
        schema=SCHEMA,
        runtime_commit=commit,
        started_at_utc=(now or datetime.now(UTC)).isoformat(),
        lock_sha256_at_start=lock_sha256(path),
        pid=os.getpid() if pid is None else pid,
        runtime_source=source,
    )


_IDENTITY: RuntimeIdentity | None = None
_IDENTITY_LOCK = threading.Lock()


def get_runtime_identity(
    repo_dir: Path | str = REPO_ROOT, *, run: Runner = subprocess.run
) -> RuntimeIdentity:
    """Prozessweit einmal eingefroren — der erste Aufruf legt den Wert fest."""
    global _IDENTITY
    if _IDENTITY is None:
        with _IDENTITY_LOCK:
            if _IDENTITY is None:
                _IDENTITY = capture_runtime_identity(repo_dir, run=run)
    return _IDENTITY


def reset_runtime_identity_for_tests() -> None:
    global _IDENTITY
    with _IDENTITY_LOCK:
        _IDENTITY = None
    _DRIFT_CACHE.clear()
    _LOCK_CACHE.clear()


# ── Report + Bewertung ────────────────────────────────────────────────────────


def drift_report(
    identity: RuntimeIdentity,
    repo_dir: Path | str = REPO_ROOT,
    *,
    now: datetime | None = None,
    run: Runner = subprocess.run,
) -> dict[str, Any]:
    """Runtime vs. Referenz, als flaches Dict (für /health und den Health-Check).

    Referenz ist das aktive Release (``current``), sonst der Checkout. Nach dem
    Cutover wandert der Checkout unabhaengig vom Daemon — als Referenz waere er
    falsch (``checkout_commit`` bleibt als Feldname, traegt aber die Referenz).
    """
    path = Path(repo_dir)
    current = now or datetime.now(UTC)
    reference = active_release_commit(path)
    reference_source: str | None = RUNTIME_SOURCE_RELEASE if reference else None
    if reference is None:
        reference = read_checkout_commit(path, run)
        reference_source = RUNTIME_SOURCE_CHECKOUT if reference else None
    checkout = reference
    drift: int | None = None
    if identity.runtime_commit and checkout:
        if identity.runtime_commit == checkout:
            drift = 0
        else:
            drift = count_commits_between(identity.runtime_commit, checkout, path, run)
    lock_now = lock_sha256(path)
    lock_changed: bool | None
    if identity.lock_sha256_at_start is None or lock_now is None:
        lock_changed = None
    else:
        lock_changed = lock_now != identity.lock_sha256_at_start
    try:
        started = datetime.fromisoformat(identity.started_at_utc)
        uptime = max(0.0, (current - started).total_seconds())
    except ValueError:
        uptime = None
    return {
        "runtime_commit": identity.runtime_commit,
        "runtime_source": identity.runtime_source,
        "checkout_commit": checkout,
        "reference_source": reference_source,
        "drift_commits": drift,
        "started_at_utc": identity.started_at_utc,
        "uptime_s": uptime,
        "lock_changed": lock_changed,
    }


def _short(commit: object) -> str:
    return str(commit)[:8] if commit else "?"


def evaluate_runtime_drift(
    report: dict[str, Any],
    *,
    checkout_stable_for_s: float | None,
    grace_s: float = DRIFT_GRACE_S,
    critical_after_s: float = DRIFT_CRITICAL_AFTER_S,
) -> list[DriftFinding]:
    """Zahlen → Befunde. Rein, ohne I/O — dieselbe Regel für alle Konsumenten.

    * Drift 0 → nichts.
    * Drift > 0, Checkout jünger als ``grace_s`` → nichts (Deploy unterwegs).
    * Drift > 0, älter → warning; älter als ``critical_after_s`` → critical.
    * Abweichung belegt, Abstand nicht zählbar (Release-Baum ohne git) → wie
      Drift > 0, nur ohne Zahl.
    * Nicht messbar (kein Commit) → warning: „aktuell" ist unbelegt.
    * Lock geändert → eigene warning (``pip install -e .`` + Restart nötig).

    Ist die Checkout-Stabilität nicht messbar, gilt die Prozess-Uptime als
    Untergrenze: ein Prozess, der seit 7 Tagen läuft, ist bei Drift > 0 auf
    keinen Fall „gerade eben" veraltet.
    """
    findings: list[DriftFinding] = []
    drift = report.get("drift_commits")
    runtime = report.get("runtime_commit")
    checkout = report.get("checkout_commit")
    uptime = report.get("uptime_s")

    if drift is None and runtime and checkout and runtime != checkout:
        stable = checkout_stable_for_s
        if stable is None and isinstance(uptime, int | float):
            stable = float(uptime)
        if stable is not None and stable >= grace_s:
            severity = "critical" if stable >= critical_after_s else "warning"
            findings.append(
                DriftFinding(
                    severity=severity,
                    message=(
                        f"kai-server laeuft auf {_short(runtime)}, aktiv ist "
                        f"{_short(checkout)} seit {stable / 3600.0:.1f} h — Abstand nicht "
                        "zaehlbar; der Prozess laedt neuen Code erst nach einem Restart."
                    ),
                )
            )
    elif drift is None:
        findings.append(
            DriftFinding(
                severity="warning",
                message=(
                    "Runtime-Drift nicht messbar "
                    f"(runtime={_short(runtime)}, checkout={_short(checkout)}) — "
                    "'aktuell' ist damit unbelegt; git/Checkout pruefen."
                ),
            )
        )
    elif drift > 0:
        stable = checkout_stable_for_s
        if stable is None and isinstance(uptime, int | float):
            stable = float(uptime)
        if stable is not None and stable >= grace_s:
            severity = "critical" if stable >= critical_after_s else "warning"
            hours = stable / 3600.0
            findings.append(
                DriftFinding(
                    severity=severity,
                    message=(
                        f"kai-server laeuft auf {_short(runtime)}, die Referenz (aktives "
                        f"Release, sonst Checkout) steht auf {_short(checkout)} — {drift} "
                        f"Commits voraus seit {hours:.1f} h. Der Prozess laedt neuen Code "
                        "erst nach einem Restart (Deploy-Fenster)."
                    ),
                )
            )

    if report.get("lock_changed") is True:
        findings.append(
            DriftFinding(
                severity="warning",
                message=(
                    f"{LOCK_FILE_NAME} hat sich seit dem Prozessstart geaendert — "
                    "Abhaengigkeiten im Prozess sind veraltet: pip install -e . + Restart."
                ),
            )
        )
    return findings


# ── Artefakt ──────────────────────────────────────────────────────────────────


def write_runtime_identity_artifact(identity: RuntimeIdentity, path: Path) -> Path:
    """Atomar schreiben (tmp + replace) — ein halbes Artefakt ist schlimmer als keins."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(identity.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_runtime_identity_artifact(path: Path) -> RuntimeIdentity | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return None
    try:
        return RuntimeIdentity(
            schema=SCHEMA,
            runtime_commit=raw.get("runtime_commit"),
            started_at_utc=str(raw["started_at_utc"]),
            lock_sha256_at_start=raw.get("lock_sha256_at_start"),
            pid=int(raw.get("pid", 0)),
            runtime_source=(
                raw["runtime_source"] if isinstance(raw.get("runtime_source"), str) else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None
