"""Architecture boundary guards for the ADR-0013 frontier invariants.

Turns three plan doctrines into enforced contracts (mirrors the AST approach of
``test_core_path_boundaries.py``):

1. ``edge_validation_gate`` is a PROMOTION gate, never an execution dependency —
   no module under ``app/execution/`` may import it.
2. The third-party gate cannot be consumed half-way: any module that imports
   ``ThirdPartyServiceSettings`` outside ``app/governance/`` must also import
   ``require_third_party_authorization`` (settings without guard = unlicensed
   service path waiting to happen).
3. Shadow purity: ``app/capital/`` and ``app/truth/`` never import execution.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "app"


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def _import_map(path: Path) -> dict[str, set[str]]:
    """Map imported module -> imported names (empty set for plain ``import x``)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.setdefault(alias.name, set())
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = out.setdefault(node.module, set())
            names.update(alias.name for alias in node.names)
    return out


def _violations_import_prefix(root: Path, forbidden_prefix: str) -> list[str]:
    hits: list[str] = []
    for py_file in _python_files(root):
        for module in _import_map(py_file):
            if module == forbidden_prefix or module.startswith(forbidden_prefix + "."):
                hits.append(f"{py_file.relative_to(PROJECT_ROOT)} -> {module}")
    return hits


def test_execution_never_imports_edge_validation_gate() -> None:
    hits = _violations_import_prefix(
        APP_ROOT / "execution", "app.observability.edge_validation_gate"
    )
    assert hits == [], (
        "edge_validation_gate ist ein Promotion-Tor, keine Execution-Abhängigkeit "
        f"(ADR 0013 Invariante). Verstöße: {hits}"
    )


def test_third_party_settings_never_consumed_without_guard() -> None:
    violations: list[str] = []
    for py_file in _python_files(APP_ROOT):
        rel = py_file.relative_to(APP_ROOT)
        if rel.parts[0] == "governance":
            continue
        imports = _import_map(py_file)
        names = imports.get("app.governance.third_party_gate", set())
        if "ThirdPartyServiceSettings" in names and (
            "require_third_party_authorization" not in names
        ):
            violations.append(str(py_file.relative_to(PROJECT_ROOT)))
    assert violations == [], (
        "ThirdPartyServiceSettings ohne require_third_party_authorization importiert "
        "— das Lizenz-Gate MUSS am selben Entrypoint stehen (ADR 0013). "
        f"Verstöße: {violations}"
    )


def test_capital_and_truth_stay_out_of_execution() -> None:
    hits: list[str] = []
    for shadow_pkg in ("capital", "truth"):
        pkg_root = APP_ROOT / shadow_pkg
        if not pkg_root.exists():
            continue
        hits += _violations_import_prefix(pkg_root, "app.execution")
    assert hits == [], (
        "app/capital und app/truth sind shadow-only Rechen-/Beweis-Schichten und "
        f"dürfen Execution nie berühren. Verstöße: {hits}"
    )
