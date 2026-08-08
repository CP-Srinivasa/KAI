"""Rotation-Gate (Plan 08-08, PR-4): route-aware Open-Guard gegen ``archived``.

Gepinnte Invarianten:
* ``off`` (Default) = Null-Verhaltensänderung — kein Event, kein Block.
* ``shadow`` = nie blocken, aber ``rotation_gate_would_block``-Audit
  (Counterfactual für die Prä-Reg ``rotation_gated_universe_v1``).
* ``enforce`` blockt NUR Routen im Scope; **technical_paper passiert IMMER**
  (H1/H2-versiegelte Population — Operator-Entscheid Zweig A 08-08).
* Closes passieren immer, auch auf archived.
* Fail-open: fehlender/korrupter State, leere/unbekannte Quelle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.paper_engine import PaperExecutionEngine
from app.execution.rotation_gate import (
    evaluate_rotation_gate,
    parse_gate_routes,
    resolve_entry_route,
)


def _state_file(tmp_path: Path, statuses: dict[str, str]) -> Path:
    p = tmp_path / "rotation_state.json"
    p.write_text(
        json.dumps({s: {"status": st, "flagged_runs": 0} for s, st in statuses.items()}),
        encoding="utf-8",
    )
    return p


# ── Modul-Matrix ─────────────────────────────────────────────────────────────


def test_route_resolution_covers_all_streams() -> None:
    from app.execution.entry_policy import EntryRoute

    assert resolve_entry_route("autonomous_generator") is EntryRoute.AUTONOMOUS_LOOP
    assert resolve_entry_route("technical_paper") is EntryRoute.TECHNICAL_PAPER
    assert resolve_entry_route("telegram_premium_channel_approved") is EntryRoute.PREMIUM_PAPER
    assert resolve_entry_route("tradingview_webhook") is EntryRoute.TRADINGVIEW_PAPER
    assert resolve_entry_route("real_analysis") is EntryRoute.REAL_ANALYSIS_PAPER
    assert resolve_entry_route("") is None
    assert resolve_entry_route("voellig_unbekannt") is None


def test_enforce_blocks_archived_only_in_scope(tmp_path: Path) -> None:
    state = _state_file(tmp_path, {"OLD/USDT": "archived"})
    blocked = evaluate_rotation_gate(
        "OLD/USDT",
        "autonomous_generator",
        mode="enforce",
        routes_csv="autonomous_loop",
        state_path=state,
    )
    assert blocked.action == "block"
    # H1-Route: NIE blocken, aber Counterfactual-Event.
    tp = evaluate_rotation_gate(
        "OLD/USDT",
        "technical_paper",
        mode="enforce",
        routes_csv="autonomous_loop",
        state_path=state,
    )
    assert tp.action == "would_block"
    assert tp.route == "technical_paper"


def test_shadow_never_blocks(tmp_path: Path) -> None:
    state = _state_file(tmp_path, {"OLD/USDT": "archived"})
    d = evaluate_rotation_gate(
        "OLD/USDT",
        "autonomous_generator",
        mode="shadow",
        routes_csv="autonomous_loop",
        state_path=state,
    )
    assert d.action == "would_block"


def test_non_archived_status_passes_silently(tmp_path: Path) -> None:
    state = _state_file(tmp_path, {"P/USDT": "probation", "F/USDT": "rotation_flagged"})
    for sym in ("P/USDT", "F/USDT", "NIE_BEWERTET/USDT"):
        d = evaluate_rotation_gate(
            sym,
            "autonomous_generator",
            mode="enforce",
            routes_csv="autonomous_loop",
            state_path=state,
        )
        assert d.action == "pass"


def test_unattributed_source_on_archived_passes_but_is_counted(tmp_path: Path) -> None:
    state = _state_file(tmp_path, {"OLD/USDT": "archived"})
    d = evaluate_rotation_gate(
        "OLD/USDT", "", mode="enforce", routes_csv="autonomous_loop", state_path=state
    )
    assert d.action == "unattributed"
    assert d.audit_event == "rotation_gate_unattributed"


def test_missing_and_corrupt_state_fail_open(tmp_path: Path) -> None:
    missing = evaluate_rotation_gate(
        "OLD/USDT",
        "autonomous_generator",
        mode="enforce",
        routes_csv="autonomous_loop",
        state_path=tmp_path / "nope.json",
    )
    assert missing.action == "pass"
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{kaputt", encoding="utf-8")
    d = evaluate_rotation_gate(
        "OLD/USDT",
        "autonomous_generator",
        mode="enforce",
        routes_csv="autonomous_loop",
        state_path=corrupt,
    )
    assert d.action == "pass"


def test_parse_gate_routes_normalizes() -> None:
    assert parse_gate_routes(" autonomous_loop , Premium_Paper ") == {
        "autonomous_loop",
        "premium_paper",
    }
    assert parse_gate_routes("") == frozenset()


# ── Engine-Integration ───────────────────────────────────────────────────────


def _engine(tmp_path: Path) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        initial_equity=100_000.0,
        live_enabled=False,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


def _audit_events(tmp_path: Path) -> list[dict]:
    p = tmp_path / "audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture()
def _gate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Engine-Settings + Rotation-State auf tmp umbiegen."""
    import app.execution.rotation_gate as rg

    state = _state_file(tmp_path, {"OLD/USDT": "archived"})
    monkeypatch.setattr(rg, "DEFAULT_STATE_PATH", state)
    # get_settings ist gecacht — Env setzen + Cache leeren.
    from app.core.settings import get_settings

    monkeypatch.setenv("EXECUTION_ASSET_ROTATION_GATE_MODE", "enforce")
    monkeypatch.setenv("EXECUTION_ASSET_ROTATION_GATE_ROUTES", "autonomous_loop")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_engine_blocks_archived_open_in_scope_and_audits(tmp_path: Path, _gate_env) -> None:
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="OLD/USDT",
        side="buy",
        quantity=1.0,
        order_type="market",
        position_side="long",
        source="autonomous_generator",
    )
    assert eng.fill_order(order, 10.0) is None
    events = [e["event_type"] for e in _audit_events(tmp_path)]
    assert "rotation_gate_block" in events
    assert "order_filled" not in events


def test_engine_never_blocks_technical_paper_route(tmp_path: Path, _gate_env) -> None:
    eng = _engine(tmp_path)
    order = eng.create_order(
        symbol="OLD/USDT",
        side="buy",
        quantity=1.0,
        order_type="market",
        position_side="long",
        source="technical_paper",
    )
    assert eng.fill_order(order, 10.0) is not None
    events = [e["event_type"] for e in _audit_events(tmp_path)]
    assert "rotation_gate_would_block" in events  # Counterfactual gezählt
    assert "order_filled" in events


def test_engine_close_on_archived_always_passes(tmp_path: Path, _gate_env) -> None:
    """Bestehende Position auf einem archivierten Symbol MUSS schließbar bleiben."""
    import app.execution.rotation_gate as rg

    # Erst öffnen, solange das Symbol noch nicht archiviert ist …
    rg.DEFAULT_STATE_PATH.write_text(json.dumps({}), encoding="utf-8")
    eng = _engine(tmp_path)
    opened = eng.create_order(
        symbol="OLD/USDT",
        side="buy",
        quantity=1.0,
        order_type="market",
        position_side="long",
        source="autonomous_generator",
    )
    assert eng.fill_order(opened, 10.0) is not None
    # … dann archivieren und den Exit füllen: muss durchgehen.
    rg.DEFAULT_STATE_PATH.write_text(
        json.dumps({"OLD/USDT": {"status": "archived", "flagged_runs": 3}}),
        encoding="utf-8",
    )
    closed = eng.create_order(
        symbol="OLD/USDT",
        side="sell",
        quantity=1.0,
        order_type="market",
        position_side="long",
        source="autonomous_generator",
    )
    assert eng.fill_order(closed, 11.0) is not None


def test_engine_mode_off_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.execution.rotation_gate as rg
    from app.core.settings import get_settings

    state = _state_file(tmp_path, {"OLD/USDT": "archived"})
    monkeypatch.setattr(rg, "DEFAULT_STATE_PATH", state)
    monkeypatch.setenv("EXECUTION_ASSET_ROTATION_GATE_MODE", "off")
    get_settings.cache_clear()
    try:
        eng = _engine(tmp_path)
        order = eng.create_order(
            symbol="OLD/USDT",
            side="buy",
            quantity=1.0,
            order_type="market",
            position_side="long",
            source="autonomous_generator",
        )
        assert eng.fill_order(order, 10.0) is not None
        events = [e["event_type"] for e in _audit_events(tmp_path)]
        assert not any(ev.startswith("rotation_gate") for ev in events)
    finally:
        get_settings.cache_clear()
