"""Developer-Hub-Oberflaeche 0.4.0: Ollama-Auswahl mit Faehigkeits-Symbolen, KAI-Neon.

Die Logik (Katalog, Bereitschaft, Statuschips, Arbeitsbereiche, Monogramm) ist
ohne Tk getestet. Der Rauchtest baut das echte Fenster, wo Tk ein Display hat
(Windows-Laptop des Operators), und ueberspringt sonst.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("kai_dev_hub", SCRIPTS / "kai_dev_hub.py")
assert _SPEC and _SPEC.loader
hub = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hub)
import kai_dev_hub_ui as ui  # noqa: E402

READY = {
    "opencode": True,
    "hermes": True,
    "kimi": True,
    "ollama_online": True,
    "local_model": hub.LOCAL_MODEL,
    "local_model_installed": True,
    "hermes_64k_model_installed": True,
    "litellm_tunnel_online": False,
    "context_contract": True,
    "handoff_chain_valid": True,
    "handoff_state": {"handoffs": 0, "acknowledged": 0, "superseded": [], "pending": []},
    "active_writer": "keiner",
    "managed_workspaces": 1,
    "last_independent_check": "2026-10-03T06:30:28+00:00",
    "last_prerequisites_ok": True,
    "last_response_proven": False,
    "branch": "codex/dev-task-x",
    "head": "0123456789abcdef",
}


def test_engine_catalog_mirrors_the_hub_start_paths_and_pins_the_local_model() -> None:
    engines = ui.engine_catalog(hub.LOCAL_MODEL)

    assert [engine.key for engine in engines] == [
        "opencode-local",
        "opencode-cloud",
        "hermes-local",
        "kimi",
    ]
    by_key = {engine.key: engine for engine in engines}
    assert by_key["opencode-local"].model == hub.LOCAL_MODEL
    assert by_key["hermes-local"].model == hub.LOCAL_MODEL
    assert by_key["opencode-cloud"].model.startswith("kai-dev-code")
    # Kimi bekommt nur das Kontextpaket: kein Schreiber, Symbol "nur lesen".
    assert not by_key["kimi"].writes
    assert "readonly" in by_key["kimi"].capabilities
    assert all(engine.writes for key, engine in by_key.items() if key != "kimi")


def test_every_symbol_resolves_to_a_glyph_and_a_fallback() -> None:
    used = {engine.icon for engine in ui.engine_catalog("m")}
    used |= {capability.icon for capability in ui.CAPABILITIES.values()}
    used |= {chip.icon for chip in ui.status_chips(READY, (True, "ok"))}
    for engine in ui.engine_catalog("m"):
        assert set(engine.capabilities) <= set(ui.CAPABILITIES)
    assert used <= set(ui.ICONS), used - set(ui.ICONS)
    for fluent, fallback in ui.ICONS.values():
        assert len(fluent) == 1 and "\ue000" <= fluent <= "\uf8ff", "Segoe-Fluent-Codepunkt"
        assert fallback and fallback.isascii()


def test_readiness_separates_missing_from_started_on_demand() -> None:
    assert ui.engine_readiness("opencode-local", {})[0] == "unknown"
    assert ui.engine_readiness("opencode-local", READY)[0] == "ok"
    assert ui.engine_readiness("opencode-local", {**READY, "opencode": False}) == (
        "fail",
        "OpenCode ist nicht installiert.",
    )
    # Ollama aus ist kein Fehler: launch_* startet es kalt.
    off = {**READY, "ollama_online": False, "local_model_installed": None}
    assert ui.engine_readiness("opencode-local", off)[0] == "idle"
    assert ui.engine_readiness("opencode-local", {**READY, "local_model_installed": False})[0] == (
        "fail"
    )
    assert ui.engine_readiness("hermes-local", {**READY, "hermes_64k_model_installed": False})[
        0
    ] == ("fail")
    # Der Tunnel oeffnet sich beim Start: geschlossen = bereit auf Abruf, nicht rot.
    assert ui.engine_readiness("opencode-cloud", READY)[0] == "idle"
    assert ui.engine_readiness("kimi", {**READY, "kimi": False})[0] == "fail"


def test_status_chips_never_invent_a_value() -> None:
    assert [chip.key for chip in ui.status_chips({})] == ["loading"]

    chips = {chip.key: chip for chip in ui.status_chips(READY, (True, "7 Übergaben"))}
    assert chips["litellm_tunnel_online"].state == "idle"
    assert chips["handoff_chain_valid"].detail == "7 Übergaben"
    assert chips["last_response_proven"].state == "warn"
    assert chips["pending_handoffs"].state == "ok"

    never_run = {
        **READY,
        "last_prerequisites_ok": "NOT_RUN",
        "last_independent_check": "NOT_RUN",
        "last_response_proven": "NOT_RUN",
        "local_model_installed": None,
        "handoff_state": {"pending": [{"handoff_id": "a", "to_agent": "Kimi"}]},
    }
    chips = {chip.key: chip for chip in ui.status_chips(never_run, (False, "Kette gebrochen"))}
    assert chips["last_prerequisites_ok"].state == "unknown"
    assert chips["last_prerequisites_ok"].detail == "zuletzt nie"
    assert chips["last_response_proven"].detail == "nie geprüft"
    assert chips["local_model_installed"].state == "unknown"
    assert chips["handoff_chain_valid"].state == "fail"
    assert chips["pending_handoffs"].state == "warn"


def test_workspaces_keep_orphan_and_offline_markers_visible() -> None:
    rows = ui.session_rows(
        [
            {
                "task": "A",
                "worktree": "C:/tmp/a",
                "branch": "codex/a",
                "base_mode": "offline-cache",
            },
            {"task": "B", "orphaned": True, "orphan_reason": "Worktree fehlt"},
        ]
    )

    assert rows[0].path == Path("C:/tmp/a")
    assert rows[0].subtitle.endswith("[OFFLINE-BASIS]")
    assert rows[0].icon == "offline"
    assert rows[1].path is None
    assert rows[1].subtitle == "VERWAIST: Worktree fehlt"


def test_active_workspace_survives_refresh_and_falls_back_to_newest_valid() -> None:
    rows = [
        {"worktree": "C:/tmp/new"},
        {"worktree": "C:/tmp/old"},
        {"worktree": "C:/tmp/gone", "orphaned": True},
    ]
    assert ui.choose_active(Path("C:/tmp/old"), rows) == Path("C:/tmp/old")
    assert ui.choose_active(Path("C:/primary"), rows) == Path("C:/tmp/new")
    assert ui.choose_active(Path("C:/tmp/gone"), rows) == Path("C:/tmp/new")
    assert ui.choose_active(Path("C:/primary"), []) == Path("C:/primary")


def test_hero_and_handoff_hint_come_from_local_evidence() -> None:
    title, meta, _next = ui.workspace_summary(READY, {"session": None})
    assert title == "Kein Arbeitsbereich gewählt"

    resume = {
        "session": {"task": "Hub Neon"},
        "branch": "codex/dev-task-x",
        "head": "abc1234",
        "changed_files": 3,
        "next_action": "Review",
        "pending_handoffs": [{"handoff_id": "1234567890", "to_agent": "Hermes"}],
    }
    writer = {**READY, "active_writer": "opencode-local (PID 7, Host h, seit x)"}
    title, meta, next_step = ui.workspace_summary(writer, resume)
    assert title == "Hub Neon"
    assert "3 geänderte Dateien" in meta and "@abc1234" in meta
    assert meta.endswith("Schreiber: opencode-local")
    assert next_step == "Review"
    assert "12345678 an Hermes" in ui.handoff_hint(resume)
    assert ui.handoff_hint({}).startswith("Keine offene Übergabe")


def test_blend_and_kai_monogram_raster() -> None:
    assert ui.blend("#000000", "#ffffff", 0) == "#000000"
    assert ui.blend("#000000", "#ffffff", 1) == "#ffffff"
    assert ui.blend("#000000", "#ffffff", 0.5) == "#808080"
    coverage = ui.mark_coverage(32)
    assert coverage[16][7] == 1.0  # Mitte des K-Stamms
    assert coverage[1][1] == 0.0  # Ecke ist leer
    assert any(0 < value < 1 for row in coverage for value in row), "kantengeglaettet"
    pixels = ui.mark_pixels(16, "#000000", [("#ffffff", 0, 0, 1.0)])
    assert len(pixels) == 16 and all(len(row) == 16 for row in pixels)


def test_ui_only_calls_functions_the_hub_actually_has() -> None:
    """The window is a thin client: every hub.* name must exist in kai_dev_hub."""
    source = (SCRIPTS / "kai_dev_hub_ui.py").read_text(encoding="utf-8")
    names = set(re.findall(r"\bhub\.([A-Za-z_]+)\b", source)) - {"workflow"}
    assert names, "no hub calls found"
    assert {name for name in names if not hasattr(hub, name)} == set()
    workflow_names = set(re.findall(r"\bhub\.workflow\.([A-Za-z_]+)\b", source))
    assert {name for name in workflow_names if not hasattr(hub.workflow, name)} == set()
    # Kein Inferenz- oder Routingzugriff aus der Oberflaeche.
    assert "app.ai" not in source and "import app" not in source


def _tk_root() -> Any:
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:  # kein Display (CI)
        pytest.skip(f"Tk ohne Display: {exc}")
    root.withdraw()
    return root


def test_window_renders_status_and_starts_the_selected_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tk_root()
    worktree = tmp_path / "wt"
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(hub, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(
        hub.workflow,
        "list_sessions",
        lambda _state: [{"task": "Hub Neon", "worktree": str(worktree), "branch": "codex/x"}],
    )
    monkeypatch.setattr(hub.workflow, "require_session", lambda repo, _state: {"task": "t"})
    monkeypatch.setattr(hub, "status", lambda repo: {**READY, "repository": str(repo)})
    monkeypatch.setattr(hub, "verify_handoffs", lambda: (True, "Kette gültig"))
    monkeypatch.setattr(
        hub,
        "resume_report",
        lambda repo: {
            "session": {"task": "Hub Neon"},
            "branch": "codex/x",
            "head": "abc1234",
            "changed_files": 0,
            "pending_handoffs": [],
            "next_action": None,
        },
    )
    monkeypatch.setattr(
        hub,
        "launch_hermes",
        lambda repo, take_over=False: calls.append(("hermes", (repo, take_over))),
    )
    try:
        window = ui.HubWindow(hub, tmp_path / "primary", root)
        root.deiconify()
        deadline = time.monotonic() + 10
        while not window.values and time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)
        assert window.values, "Status kam nicht an"
        assert window.active == worktree, "erster gueltiger Arbeitsbereich wird aktiv"
        assert len(window.engine_rows) == 4
        assert all(row["state"][0] in {"ok", "idle"} for row in window.engine_rows)
        assert len(window.chip_widgets) == len(ui.status_chips(READY))

        window.start_engine("hermes-local")
        deadline = time.monotonic() + 10
        while not calls and time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)
        assert calls == [("hermes", (worktree, False))], "Start ohne Uebernahme, im Worktree"
        assert window.selected == "hermes-local"
    finally:
        root.destroy()
