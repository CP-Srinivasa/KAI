"""Import-isolation lint for the exploration sandbox (DEC-SRC-EXPLORE-001).

Two guarantees, both enforced by scanning source text:

  1. No production runtime module imports ``app.exploration``. The sandbox must be
     removable (``rm -rf app/exploration``) without touching production. The single
     sanctioned exception is none — the sandbox has its own CLI + settings.

  2. ``app.exploration`` does not import from high-level runtime modules
     (signals, orchestrator, execution, trading, alerts, risk, market_data,
     pipeline, ingestion). Only low-level shared utilities (app.security.*,
     app.core.*) are allowed, so graduation stays a deliberate, reviewed step.
"""

from __future__ import annotations

import re
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_EXPLORATION = _APP_ROOT / "exploration"

# Runtime modules the sandbox must NOT couple to.
_FORBIDDEN_FOR_EXPLORATION = (
    "app.signals",
    "app.orchestrator",
    "app.execution",
    "app.trading",
    "app.alerts",
    "app.risk",
    "app.market_data",
    "app.pipeline",
    "app.ingestion",
)

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+(app\.[\w.]+)", re.MULTILINE)


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_production_does_not_import_exploration() -> None:
    offenders: list[str] = []
    for path in _python_files(_APP_ROOT):
        if _EXPLORATION in path.parents or path == _EXPLORATION:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bapp\.exploration\b", text):
            offenders.append(str(path.relative_to(_APP_ROOT.parent)))
    assert not offenders, (
        "Production modules must not import app.exploration "
        f"(isolation breach): {offenders}"
    )


def test_exploration_does_not_import_runtime_modules() -> None:
    offenders: list[tuple[str, str]] = []
    for path in _python_files(_EXPLORATION):
        text = path.read_text(encoding="utf-8")
        for match in _IMPORT_RE.finditer(text):
            module = match.group(1)
            for forbidden in _FORBIDDEN_FOR_EXPLORATION:
                if module == forbidden or module.startswith(forbidden + "."):
                    offenders.append((str(path.relative_to(_APP_ROOT.parent)), module))
    assert not offenders, (
        "app.exploration must not import high-level runtime modules: " + repr(offenders)
    )


def test_exploration_only_uses_allowed_shared_modules() -> None:
    """Positive guard: every app.* import in the sandbox is exploration/security/core."""
    allowed_prefixes = ("app.exploration", "app.security", "app.core")
    offenders: list[tuple[str, str]] = []
    for path in _python_files(_EXPLORATION):
        text = path.read_text(encoding="utf-8")
        for match in _IMPORT_RE.finditer(text):
            module = match.group(1)
            if not any(
                module == prefix or module.startswith(prefix + ".") for prefix in allowed_prefixes
            ):
                offenders.append((str(path.relative_to(_APP_ROOT.parent)), module))
    assert not offenders, (
        "app.exploration may only import app.exploration/app.security/app.core: "
        + repr(offenders)
    )
