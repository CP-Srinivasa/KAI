"""Die Workspace-Grenze muss den `artifacts`-Symlink des Immutable-Release tragen.

Kontext (2026-09-08): Seit dem Cutover laeuft kai-server aus
`/home/kai/current` -> `/home/ubuntu/releases/<SHA>/`. Dort ist `artifacts`
ein Symlink auf den beweglichen Datenbaum. `Path.resolve()` folgt diesem
Symlink aus dem Release heraus, danach schlug `relative_to(WORKSPACE_ROOT)`
fehl -- jeder Operator-Read endete in einem deterministischen 503
(4509/4509 Requests auf /operator/portfolio-snapshot in 48 h).

Die Tests halten BEIDE Seiten fest: der legitime artifacts-Symlink muss
tragen, und die Sandbox darf dadurch nicht aufgehen.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.agents.tools import _helpers

pytestmark = pytest.mark.skipif(
    not hasattr(os, "symlink"), reason="Symlinks werden auf dieser Plattform nicht unterstuetzt"
)


@pytest.fixture()
def release_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Baut die Release-Kulisse nach: Release-Baum + artifacts-Symlink nach aussen."""
    release = tmp_path / "releases" / "deadbeef"
    release.mkdir(parents=True)
    external_artifacts = tmp_path / "moving_checkout" / "artifacts"
    external_artifacts.mkdir(parents=True)
    (external_artifacts / "paper_execution_audit.jsonl").write_text("{}\n", encoding="utf-8")

    try:
        (release / "artifacts").symlink_to(external_artifacts, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows ohne Privileg
        pytest.skip("Symlink-Erstellung nicht erlaubt (Windows ohne Developer Mode)")

    monkeypatch.setattr(_helpers, "WORKSPACE_ROOT", release)
    return release


def test_artifacts_symlink_target_is_accepted(release_tree: Path) -> None:
    """Der Regressionsfall: relativer Pfad durch den artifacts-Symlink."""
    resolved = _helpers.resolve_workspace_path(
        "artifacts/paper_execution_audit.jsonl",
        label="Paper execution audit",
        must_exist=True,
    )
    assert resolved.name == "paper_execution_audit.jsonl"
    assert resolved.read_text(encoding="utf-8") == "{}\n"


def test_artifacts_symlink_dir_is_accepted(release_tree: Path) -> None:
    resolved = _helpers.resolve_workspace_dir("artifacts", label="Alert audit dir", must_exist=True)
    assert resolved.is_dir()


def test_write_guard_accepts_artifacts_symlink(release_tree: Path) -> None:
    """I-95-Schreibwaechter muss denselben Symlink tragen."""
    resolved = _helpers.resolve_workspace_path(
        "artifacts/paper_execution_audit.jsonl", label="Paper execution audit"
    )
    assert _helpers.require_artifacts_subpath(resolved, label="Paper execution audit") == resolved


# --- Gegenproben: die Kulisse aendert sich, die Grenze darf NICHT aufgehen ---


def test_traversal_outside_workspace_still_rejected(release_tree: Path) -> None:
    with pytest.raises(ValueError, match="must stay within workspace"):
        _helpers.resolve_workspace_path(
            "../../moving_checkout/secrets.jsonl", label="Paper execution audit"
        )


def test_sibling_of_artifacts_target_still_rejected(release_tree: Path, tmp_path: Path) -> None:
    """Erlaubt ist NUR das artifacts-Ziel, nicht dessen Elternbaum."""
    sibling = tmp_path / "moving_checkout" / "not_artifacts.jsonl"
    sibling.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must stay within workspace"):
        _helpers.resolve_workspace_path(str(sibling), label="Paper execution audit")


def test_rogue_symlink_out_of_workspace_still_rejected(release_tree: Path, tmp_path: Path) -> None:
    """Ein anderer Symlink im Release darf die Grenze nicht oeffnen."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "loot.jsonl").write_text("{}\n", encoding="utf-8")
    try:
        (release_tree / "rogue").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover
        pytest.skip("Symlink-Erstellung nicht erlaubt")
    with pytest.raises(ValueError, match="must stay within workspace"):
        _helpers.resolve_workspace_path("rogue/loot.jsonl", label="Paper execution audit")


def test_write_guard_rejects_non_artifacts_path(release_tree: Path) -> None:
    inside = release_tree / "config.json"
    with pytest.raises(ValueError, match="must be within workspace/artifacts/"):
        _helpers.require_artifacts_subpath(inside, label="Some write")
