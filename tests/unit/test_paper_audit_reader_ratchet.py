"""RATCHET: keine NEUE Eigenlesung des Paper-Audit-Streams.

Befund 2026-08-17: ``artifacts/paper_execution_audit.jsonl`` ist die Replay-SSOT
der Paper-Ausführung — und war ein Bus ohne Port. 90 Dateien referenzieren ihn,
**34** kombinieren den Bezug mit eigenem ``open()``/``json.loads``. Zwei davon
trugen dieselbe Lesefunktion unter demselben Namen, Zeile für Zeile identisch
bis auf das Log-Präfix.

Die Folge ist nicht Redundanz, sondern Uneinheitlichkeit: jede Stelle darf
eigene Annahmen über Kodierung, Leerzeilen und defekte Datensätze treffen, und
keine davon lässt sich zentral korrigieren. Genau deshalb konnten zwei
Fassungen nebeneinander existieren, von denen die eine kaputte Zeilen
kommentarlos verschluckt und die andere pro Zeile warnt.

Wie bei den anderen Struktur-Ratchets (#682/#684/#687, God-File, Zerlegung):
Der Ist-Zustand wird eingefroren und darf nur SCHRUMPFEN. Alle 34 auf einmal zu
migrieren wäre ein Umbau quer durch CLI, API, Execution und Orchestrator — mit
echtem Risiko am Handelspfad und ohne Testnutzen. Neue Lesestellen nehmen
dagegen ab sofort ``app/execution/paper_audit_stream.py``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = _ROOT / "tests" / "fixtures" / "paper_audit_reader_baseline.json"
_PORT = "app/execution/paper_audit_stream.py"
_AUDIT_MARKER = "paper_execution_audit"
_PARSE_CALLS = frozenset({"loads", "open"})


def _parses_directly(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in _PARSE_CALLS:
            return True
    return False


def scan_own_readers(root: Path) -> set[str]:
    """``app/``-Dateien, die den Audit-Stream nennen UND selbst parsen."""
    found: set[str] = set()
    for path in sorted((root / "app").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == _PORT:
            continue  # Das Modul IST der Port.
        source = path.read_text(encoding="utf-8", errors="replace")
        if _AUDIT_MARKER not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - defensiv
            continue
        if _parses_directly(tree):
            found.add(rel)
    return found


def _baseline() -> set[str]:
    return set(json.loads(_BASELINE.read_text(encoding="utf-8"))["entries"])


def test_scanner_detects_a_synthetic_own_reader(tmp_path: Path) -> None:
    """Positivkontrolle: ein gruener Ratchet muss von einem kaputten Scanner
    unterscheidbar sein (Lehre aus feedback_prereg_evaluator_must_be_committed)."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "sneaky.py").write_text(
        "import json\n"
        "def read():\n"
        '    with open("artifacts/paper_execution_audit.jsonl") as fh:\n'
        "        return [json.loads(x) for x in fh]\n",
        encoding="utf-8",
    )

    assert scan_own_readers(tmp_path) == {"app/sneaky.py"}


def test_scanner_ignores_a_file_that_only_delegates(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "clean.py").write_text(
        "from app.execution.paper_audit_stream import load_audit_events\n"
        "def read():\n"
        '    return load_audit_events("artifacts/paper_execution_audit.jsonl")\n',
        encoding="utf-8",
    )

    assert scan_own_readers(tmp_path) == set()


def test_no_new_own_reader_of_the_audit_stream() -> None:
    new = sorted(scan_own_readers(_ROOT) - _baseline())

    assert not new, (
        "Diese Dateien lesen den Paper-Audit-Stream selbst, statt "
        f"{_PORT} zu benutzen:\n  " + "\n  ".join(new) + "\n\n"
        "Neue Lesestellen gehen ueber den Port — eine Leseregel, an einer Stelle "
        "korrigierbar."
    )


def test_baseline_has_no_stale_entries() -> None:
    """Migrierte Dateien muessen aus der Schuldenliste verschwinden."""
    stale = sorted(_baseline() - scan_own_readers(_ROOT))

    assert not stale, (
        "Diese Eintraege lesen nicht mehr selbst und gehoeren aus "
        f"{_BASELINE.name} entfernt: {stale}"
    )
