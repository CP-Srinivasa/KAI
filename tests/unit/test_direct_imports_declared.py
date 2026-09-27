"""Jedes Fremdpaket, das ``app/`` importiert, steht DIREKT in pyproject.toml.

Befundklasse vom 27.09.2026: Pakete, die KAI nutzt, kamen nur beilaeufig ueber
andere mit und verschwanden still, sobald ein Upstream seine Abhaengigkeiten aenderte:

* ``greenlet`` fiel mit SQLAlchemy 2.1 aus dem Lock -> Async-DB-ImportError (#1121)
* ``jeepney`` stand nie im Lock -> jede Release-venv ohne DBus ->
  ``/health/premium_pipeline`` dauerhaft falsch 503
* ``cryptography`` (Payment-Intent-Vault) und ``websockets`` (Liquidations-Stream)
  hingen nur an google-auth/pyjwt bzw. uvicorn/google-genai

Der Test liest die Imports per AST (auch die lazy in Funktionen) und bildet sie ueber
``importlib.metadata`` auf Distributionen ab. In der CI ist der Lock installiert.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_OWN = {"app", "scripts", "tests", "tools", "__future__"}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(spec: str) -> str:
    return _norm(re.split(r"[\[<>=!~; ]", spec, maxsplit=1)[0])


def _declared() -> set[str]:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = {_requirement_name(d) for d in project["dependencies"]}
    for deps in project.get("optional-dependencies", {}).values():
        names |= {_requirement_name(d) for d in deps}
    return names


def _imported_top_level_modules() -> dict[str, str]:
    """Top-Level-Modul -> eine Beispieldatei (fuer eine brauchbare Fehlermeldung)."""
    found: dict[str, str] = {}
    for path in sorted((REPO / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            else:
                continue
            for mod in mods:
                top = mod.split(".")[0]
                if top not in sys.stdlib_module_names and top not in _OWN:
                    found.setdefault(top, str(path.relative_to(REPO)))
    return found


def test_every_third_party_import_is_a_direct_dependency() -> None:
    declared = _declared()
    distributions = packages_distributions()
    undeclared = []
    for module, example in sorted(_imported_top_level_modules().items()):
        # Nicht installiert (z. B. plattformgebunden wie jeepney unter Windows):
        # Distribution == Modulname annehmen — der Name muss trotzdem deklariert sein.
        dists = {_norm(d) for d in distributions.get(module, [module])}
        if not dists & declared:
            undeclared.append(f"{module} ({', '.join(sorted(dists))}) z. B. in {example}")
    assert not undeclared, (
        "Direkt importiert, aber nicht in pyproject.toml deklariert — kommt nur beilaeufig "
        "ueber andere Pakete und kann still verschwinden:\n  " + "\n  ".join(undeclared)
    )
