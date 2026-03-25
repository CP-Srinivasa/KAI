"""Architecture boundary guards for the core production path."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "app"


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
    return modules


def _assert_no_import_prefix(root: Path, forbidden_prefixes: tuple[str, ...]) -> None:
    violations: list[str] = []
    for py_file in _python_files(root):
        imports = _module_imports(py_file)
        for module in imports:
            if module.startswith(forbidden_prefixes):
                violations.append(f"{py_file}: {module}")
    assert not violations, "Forbidden imports detected:\n" + "\n".join(sorted(violations))


def test_signals_layer_does_not_depend_on_alerts_or_storage() -> None:
    _assert_no_import_prefix(
        APP_ROOT / "signals",
        ("app.alerts", "app.storage"),
    )


def test_alerts_layer_does_not_depend_on_ingestion_or_provider_integrations() -> None:
    _assert_no_import_prefix(
        APP_ROOT / "alerts",
        ("app.ingestion", "app.integrations", "app.analysis.providers", "app.analysis.factory"),
    )


def test_analysis_layer_does_not_depend_on_alerts_or_execution() -> None:
    _assert_no_import_prefix(
        APP_ROOT / "analysis",
        ("app.alerts", "app.execution"),
    )


def test_collect_rss_feed_calls_are_confined_to_pipeline_service() -> None:
    allowed = {APP_ROOT / "pipeline" / "service.py"}
    callers: set[Path] = set()

    for py_file in _python_files(APP_ROOT):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "collect_rss_feed":
                callers.add(py_file)
            elif isinstance(func, ast.Attribute) and func.attr == "collect_rss_feed":
                callers.add(py_file)

    assert callers <= allowed, (
        "collect_rss_feed must only be called inside app/pipeline/service.py; "
        f"found callers: {sorted(str(p) for p in callers)}"
    )

