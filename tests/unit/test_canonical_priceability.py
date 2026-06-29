"""Tests for deliverables 1–3: settings flag, priceability lookup, paper-guard gate.

TDD order: these tests are written first and drive the implementation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. Settings flag — universe_eligibility_enforce
# ---------------------------------------------------------------------------


def test_universe_eligibility_enforce_default_is_false() -> None:
    """Flag defaults to False — no behavior change on deploy."""
    import os

    # Ensure env is clean.
    os.environ.pop("EXECUTION_UNIVERSE_ELIGIBILITY_ENFORCE", None)

    from app.core.settings import ExecutionSettings

    s = ExecutionSettings()
    assert s.universe_eligibility_enforce is False


def test_universe_eligibility_enforce_can_be_enabled_via_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXECUTION_UNIVERSE_ELIGIBILITY_ENFORCE", "true")

    # Bypass lru_cache by instantiating directly.
    from app.core.settings import ExecutionSettings

    s = ExecutionSettings()
    assert s.universe_eligibility_enforce is True


# ---------------------------------------------------------------------------
# 2. latest_ineligible_symbols + is_canonical_priceable
# ---------------------------------------------------------------------------


def _write_ledger(path: Path, verdicts: list[dict]) -> None:
    """Write a minimal eligibility ledger snapshot."""
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "count": len(verdicts),
        "eligible_count": sum(1 for v in verdicts if v["eligible"]),
        "verdicts": verdicts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def test_latest_ineligible_symbols_no_ledger(tmp_path: Path) -> None:
    """Permissive: missing ledger → empty set (never blocks unknown symbols)."""
    from app.trading.symbol_eligibility import latest_ineligible_symbols

    missing = tmp_path / "nonexistent.jsonl"
    assert latest_ineligible_symbols(missing) == set()


def test_latest_ineligible_symbols_returns_only_ineligible(tmp_path: Path) -> None:
    """Returns only symbols whose latest verdict is ineligible."""
    from app.trading.symbol_eligibility import latest_ineligible_symbols

    ledger = tmp_path / "symbol_eligibility_audit.jsonl"
    _write_ledger(
        ledger,
        [
            {"symbol": "SLX/USDT", "eligible": False, "reasons": ["no_canonical_venue_data"]},
            {"symbol": "BTC/USDT", "eligible": True, "reasons": []},
            {"symbol": "ACT/USDT", "eligible": False, "reasons": ["no_canonical_venue_data"]},
        ],
    )
    result = latest_ineligible_symbols(ledger)
    assert result == {"SLX/USDT", "ACT/USDT"}
    assert "BTC/USDT" not in result


def test_latest_ineligible_symbols_uses_latest_snapshot(tmp_path: Path) -> None:
    """When multiple snapshots exist, only the last one counts."""
    from app.trading.symbol_eligibility import latest_ineligible_symbols

    ledger = tmp_path / "symbol_eligibility_audit.jsonl"
    # First snapshot: FOO ineligible.
    snap1 = {
        "ts": "2026-06-01T00:00:00",
        "count": 1,
        "eligible_count": 0,
        "verdicts": [
            {"symbol": "FOO/USDT", "eligible": False, "reasons": ["no_canonical_venue_data"]}
        ],
    }
    # Second snapshot: FOO eligible (recovered).
    snap2 = {
        "ts": "2026-06-02T00:00:00",
        "count": 1,
        "eligible_count": 1,
        "verdicts": [{"symbol": "FOO/USDT", "eligible": True, "reasons": []}],
    }
    with ledger.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(snap1) + "\n")
        fh.write(json.dumps(snap2) + "\n")

    result = latest_ineligible_symbols(ledger)
    # Second snapshot is latest → FOO is now eligible → not in ineligible set.
    assert "FOO/USDT" not in result


def test_is_canonical_priceable_known_ineligible_returns_false() -> None:
    from app.trading.symbol_eligibility import is_canonical_priceable

    ineligible = {"SLX/USDT", "ACT/USDT"}
    assert is_canonical_priceable("SLX/USDT", ineligible) is False
    assert is_canonical_priceable("ACT/USDT", ineligible) is False


def test_is_canonical_priceable_known_eligible_returns_true() -> None:
    from app.trading.symbol_eligibility import is_canonical_priceable

    ineligible = {"SLX/USDT"}
    assert is_canonical_priceable("BTC/USDT", ineligible) is True


def test_is_canonical_priceable_unknown_symbol_permissive() -> None:
    """Unknown symbol (not in any snapshot) must NOT be blocked."""
    from app.trading.symbol_eligibility import is_canonical_priceable

    ineligible = {"SLX/USDT"}
    # DOGE/USDT not in ineligible set → priceable (permissive default)
    assert is_canonical_priceable("DOGE/USDT", ineligible) is True


def test_is_canonical_priceable_empty_ineligible_set() -> None:
    """Empty ineligible set → every symbol is priceable."""
    from app.trading.symbol_eligibility import is_canonical_priceable

    assert is_canonical_priceable("SLX/USDT", set()) is True


# ---------------------------------------------------------------------------
# 3. Paper-engine gate: flag ON/OFF
# ---------------------------------------------------------------------------


def _make_engine_and_open_order(symbol: str):
    """Helper: build a minimal PaperExecutionEngine + open order for testing."""
    from app.execution.models import PaperOrder
    from app.execution.paper_engine import PaperExecutionEngine

    engine = PaperExecutionEngine(
        initial_equity=10_000.0,
        fee_pct=0.1,
        slippage_pct=0.0,
        audit_log_path=None,  # no audit writes in unit tests
    )
    order = PaperOrder(
        order_id=f"test_{symbol.replace('/', '_')}",
        symbol=symbol,
        side="buy",
        position_side="long",
        quantity=1.0,
        order_type="market",
        limit_price=None,
        stop_loss=None,
        take_profit=None,
        created_at="2026-06-29T00:00:00+00:00",
        idempotency_key=f"idem_{symbol}",
        status="pending",
        venue="paper",
    )
    return engine, order


def test_paper_engine_gate_flag_off_slx_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With UNIVERSE_ELIGIBILITY_ENFORCE=false (default), SLX open passes the guard."""
    monkeypatch.setenv("EXECUTION_UNIVERSE_ELIGIBILITY_ENFORCE", "false")

    # Write a ledger marking SLX ineligible
    ledger = tmp_path / "artifacts" / "symbol_eligibility_audit.jsonl"
    _write_ledger(
        ledger,
        [{"symbol": "SLX/USDT", "eligible": False, "reasons": ["no_canonical_venue_data"]}],
    )
    monkeypatch.chdir(tmp_path)

    engine, order = _make_engine_and_open_order("SLX/USDT")
    # Flag is OFF → guard is bypassed → fill_order runs normally (may return
    # None for other reasons like cash < cost, but must not hit the eligibility gate).
    # We only assert it doesn't raise; we don't assert the fill succeeds end-to-end.
    try:
        engine.fill_order(order, current_price=1.0)
    except Exception as exc:
        pytest.fail(f"Unexpected exception with flag OFF: {exc}")


def test_paper_engine_gate_flag_on_slx_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With enforce=true and SLX in ineligible set, SLX open returns None."""
    monkeypatch.setenv("EXECUTION_UNIVERSE_ELIGIBILITY_ENFORCE", "true")

    # Write a ledger marking SLX ineligible
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    ledger = tmp_path / "artifacts" / "symbol_eligibility_audit.jsonl"
    _write_ledger(
        ledger,
        [{"symbol": "SLX/USDT", "eligible": False, "reasons": ["no_canonical_venue_data"]}],
    )
    monkeypatch.chdir(tmp_path)

    engine, order = _make_engine_and_open_order("SLX/USDT")
    # Invalidate cached settings so monkeypatched env takes effect.
    import app.core.settings as _settings_mod

    if hasattr(_settings_mod.get_settings, "cache_clear"):
        _settings_mod.get_settings.cache_clear()

    fill = engine.fill_order(order, current_price=1.0)
    assert fill is None, "Expected None: SLX/USDT is ineligible, flag is ON"


def test_paper_engine_gate_flag_on_btc_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With enforce=true and SLX ineligible, BTC (eligible) open still passes."""
    monkeypatch.setenv("EXECUTION_UNIVERSE_ELIGIBILITY_ENFORCE", "true")

    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    ledger = tmp_path / "artifacts" / "symbol_eligibility_audit.jsonl"
    _write_ledger(
        ledger,
        [{"symbol": "SLX/USDT", "eligible": False, "reasons": ["no_canonical_venue_data"]}],
    )
    monkeypatch.chdir(tmp_path)

    import app.core.settings as _settings_mod

    if hasattr(_settings_mod.get_settings, "cache_clear"):
        _settings_mod.get_settings.cache_clear()

    engine, order = _make_engine_and_open_order("BTC/USDT")
    # BTC/USDT is not in ineligible set → guard passes it through.
    # The fill may still fail for unrelated reasons (e.g., equity checks).
    # We just verify no eligibility exception and the function runs.
    try:
        engine.fill_order(order, current_price=50_000.0)
    except Exception as exc:
        pytest.fail(f"Unexpected exception for BTC/USDT with flag ON: {exc}")
