"""Unveraenderliche Release-Baeume — was ein Prozess wirklich geladen hat.

**Warum es diese Datei gibt.** Der Vorgaenger band den Marker an die
Kernel-Identitaet des Prozesses (PID, Startzeit, Boot) und las den Commit beim
Start. Das schliesst die *Prozess*-Identitaet, nicht die *Code*-Identitaet: in
einem beweglichen Checkout kann sich der Baum zwischen Attestierung und Import
weiterbewegen, und Python importiert Module erst beim Laufen.

    Checkout OLD -> attestiert OLD -> Checkout wandert auf NEW -> exec
    -> importiert NEW -> Marker behauptet OLD

Die Antwort ist nicht mehr Logik um einen beweglichen Baum, sondern ein Baum,
der sich nicht bewegt:

    /home/ubuntu/releases/<SHA>/   Code, Config, Lock, eigener .venv, release.json
    /home/ubuntu/current       ->  /home/ubuntu/releases/<SHA>

Der Prozess loest ``current`` beim Start auf und fuehrt den **aufgeloesten** Pfad.
Ein spaeter umgeschalteter Symlink kann einen laufenden Prozess damit nicht mehr
rueckwirkend umetikettieren.

**Zustand gehoert nicht ins Release.** ``.env``, ``artifacts/``, ``data/`` und
``logs/`` sind Symlinks in einen stabilen Zustandspfad. Sie sind bewusst NICHT
Teil von :func:`release_tree_sha256` — sonst aenderte sich die Identitaet des
Releases bei jedem geschriebenen Logeintrag.

Rein bis auf das Lesen des Baums; keine Uhr ausser dort, wo ein Zeitstempel
erzeugt wird.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

RELEASE_MANIFEST_SCHEMA: Final = "kai_release/v1"
RELEASE_MANIFEST_NAME: Final = "release.json"

#: Was die Identitaet eines Releases ausmacht. Ausschliesslich Unveraenderliches:
#: Anwendungscode, Konfiguration, Deploy-Dateien und das gepinnte Lockfile.
#: Alles, was der laufende Dienst aus der Release-Wurzel laedt, gehoert in die
#: Identitaet -- sonst behauptet ein Release Unveraenderlichkeit fuer Bytes,
#: die sich unbemerkt aendern duerfen. ``monitor/`` und die beiden Schemata
#: liest ``app/`` ueber ``parents[2]``; ``web/`` traegt im Release nur die
#: gebaute SPA, die ``app/api/main.py`` unter ``/dashboard`` ausliefert.
SEALED_DIRS: Final = ("app", "config", "deploy", "monitor", "scripts", "web")
SEALED_FILES: Final = (
    "requirements.lock",
    "pyproject.toml",
    "CONFIG_SCHEMA.json",
    "DECISION_SCHEMA.json",
    "alembic.ini",
)

#: Niemals in die Identitaet: Zustand, Caches und der venv. Der venv wird ueber
#: das Lockfile plus ``pip check`` beim Bau belegt, nicht byteweise gehasht —
#: 549 MB bei jedem Health-Lauf zu lesen waere teuer und beweist nichts, was das
#: Lockfile nicht schon sagt.
EXCLUDED_NAMES: Final = frozenset(
    {".venv", "__pycache__", ".env", "artifacts", "data", "logs", ".git", "node_modules"}
)


@dataclass(frozen=True)
class ReleaseManifest:
    """Der Inhalt von ``release.json``, geprueft statt geraten."""

    schema: str
    repo_sha: str
    release_path: str
    release_tree_sha256: str
    requirements_lock_sha256: str
    python_version: str
    created_at_utc: str
    venv_python_path: str = ""
    dependency_manifest_sha256: str = ""
    builder_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "repo_sha": self.repo_sha,
            "release_path": self.release_path,
            "release_tree_sha256": self.release_tree_sha256,
            "requirements_lock_sha256": self.requirements_lock_sha256,
            "python_version": self.python_version,
            "created_at_utc": self.created_at_utc,
            "venv_python_path": self.venv_python_path,
            "dependency_manifest_sha256": self.dependency_manifest_sha256,
            "builder_version": self.builder_version,
        }


def _sealed_files(root: Path) -> Iterable[Path]:
    """Jede Datei, die zur Release-Identitaet gehoert — sortiert, deterministisch."""
    found: list[Path] = []
    for name in SEALED_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if any(part in EXCLUDED_NAMES for part in path.parts):
                continue
            if path.is_file() and not path.is_symlink():
                found.append(path)
    for name in SEALED_FILES:
        path = root / name
        if path.is_file() and not path.is_symlink():
            found.append(path)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def release_tree_sha256(root: Path) -> str:
    """Ein Hash ueber Pfad UND Inhalt jeder versiegelten Datei.

    Der Pfad geht mit ein, damit ein Umbenennen auffaellt: zwei Baeume mit
    denselben Bytes unter anderen Namen sind nicht derselbe Code.
    """
    digest = hashlib.sha256()
    for path in _sealed_files(root):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def read_release_manifest(release_root: Path) -> ReleaseManifest | None:
    """``release.json`` eines Releases — ``None``, wenn fehlend oder fremd."""
    try:
        raw = json.loads((release_root / RELEASE_MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != RELEASE_MANIFEST_SCHEMA:
        return None
    try:
        return ReleaseManifest(
            schema=str(raw["schema"]),
            repo_sha=str(raw["repo_sha"]),
            release_path=str(raw["release_path"]),
            release_tree_sha256=str(raw["release_tree_sha256"]),
            requirements_lock_sha256=str(raw["requirements_lock_sha256"]),
            python_version=str(raw["python_version"]),
            created_at_utc=str(raw["created_at_utc"]),
            venv_python_path=str(raw.get("venv_python_path") or ""),
            dependency_manifest_sha256=str(raw.get("dependency_manifest_sha256") or ""),
            builder_version=str(raw.get("builder_version") or ""),
        )
    except KeyError:
        return None


def resolve_current(current_link: Path) -> Path | None:
    """Der AUFGELOESTE Release-Pfad hinter ``current``.

    Aufloesen statt den Symlink zu fuehren ist der ganze Punkt: wird ``current``
    spaeter umgeschaltet, darf das einen laufenden Prozess nicht rueckwirkend
    umetikettieren.
    """
    try:
        resolved = current_link.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_dir() else None


PROBLEM_MANIFEST_MISSING: Final = "RELEASE_MANIFEST_MISSING"
PROBLEM_TREE_TAMPERED: Final = "RELEASE_TREE_TAMPERED"
PROBLEM_PATH_MISMATCH: Final = "RELEASE_PATH_MISMATCH"


def verify_release(release_root: Path) -> list[str]:
    """Traegt dieser Release-Baum noch die Identitaet, die er behauptet?"""
    manifest = read_release_manifest(release_root)
    if manifest is None:
        return [PROBLEM_MANIFEST_MISSING]
    problems: list[str] = []
    if os.path.normcase(manifest.release_path) != os.path.normcase(str(release_root)):
        problems.append(PROBLEM_PATH_MISMATCH)
    if release_tree_sha256(release_root) != manifest.release_tree_sha256:
        problems.append(PROBLEM_TREE_TAMPERED)
    return problems


__all__ = [
    "EXCLUDED_NAMES",
    "PROBLEM_MANIFEST_MISSING",
    "PROBLEM_PATH_MISMATCH",
    "PROBLEM_TREE_TAMPERED",
    "RELEASE_MANIFEST_NAME",
    "RELEASE_MANIFEST_SCHEMA",
    "SEALED_DIRS",
    "SEALED_FILES",
    "ReleaseManifest",
    "read_release_manifest",
    "release_tree_sha256",
    "resolve_current",
    "verify_release",
]
