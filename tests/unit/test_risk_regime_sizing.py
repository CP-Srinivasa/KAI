"""WP-B (regime-edge-capture 2026-06-15): regime-konditionierter Sizing-Multiplier.

Befund (Edge-Attribution): Edge trägt in breakout_up, ist in chop_quiet thin/
revertierend → dort kleiner sizen. Default-off ⇒ keine Größenänderung, kein
Regime-Lookup. Fokus, KEINE Gate-Lockerung.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.core.settings as settings_mod
import app.regime.lookup as regime_mod
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits

_LIMITS = {
    "initial_equity": 10_000.0,
    "max_risk_per_trade_pct": 0.25,
    "max_daily_loss_pct": 1.0,
    "max_total_drawdown_pct": 5.0,
    "max_open_positions": 3,
    "max_leverage": 1.0,
    "require_stop_loss": True,
    "allow_averaging_down": False,
    "allow_martingale": False,
    "kill_switch_enabled": True,
    "min_signal_confidence": 0.75,
    "min_signal_confluence_count": 2,
    "min_notional_usd": 10.0,
}


def _units(engine: RiskEngine) -> float:
    r = engine.calculate_position_size(
        symbol="BTC/USDT", entry_price=100.0, stop_loss_price=95.0, equity=10_000.0
    )
    assert r.approved
    return r.position_size_units


def _patch_regime(monkeypatch, *, enabled: bool, multipliers: dict, regime: str) -> None:
    fake = SimpleNamespace(
        risk=SimpleNamespace(
            regime_size_enabled=enabled,
            regime_size_multipliers=multipliers,
        )
    )
    monkeypatch.setattr(settings_mod, "get_settings", lambda: fake)
    monkeypatch.setattr(regime_mod, "regime_label_at", lambda *a, **k: regime)


def test_sizing_unchanged_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_regime(monkeypatch, enabled=False, multipliers={"chop_quiet": 0.5}, regime="chop_quiet")
    eng = RiskEngine(RiskLimits(**_LIMITS))
    base = _units(eng)
    assert base > 0  # default-off ⇒ voller Size


def test_sizing_halved_for_chop_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = RiskEngine(RiskLimits(**_LIMITS))
    # Baseline (disabled) zuerst messen.
    _patch_regime(monkeypatch, enabled=False, multipliers={}, regime="chop_quiet")
    base = _units(eng)
    # Dann aktiviert mit 0.5x für chop_quiet.
    _patch_regime(monkeypatch, enabled=True, multipliers={"chop_quiet": 0.5}, regime="chop_quiet")
    scaled = _units(eng)
    assert scaled == pytest.approx(base * 0.5, rel=1e-6)


def test_sizing_unchanged_for_unconfigured_regime(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = RiskEngine(RiskLimits(**_LIMITS))
    _patch_regime(monkeypatch, enabled=False, multipliers={}, regime="breakout_up")
    base = _units(eng)
    # breakout_up NICHT in der Map ⇒ Multiplier 1.0.
    _patch_regime(monkeypatch, enabled=True, multipliers={"chop_quiet": 0.5}, regime="breakout_up")
    assert _units(eng) == pytest.approx(base, rel=1e-6)
