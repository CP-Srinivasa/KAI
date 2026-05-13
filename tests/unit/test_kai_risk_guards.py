"""Tests for app.messaging.kai_risk_guards."""

from __future__ import annotations

import math

from app.messaging.kai_risk_guards import (
    KaiSignalForGuards,
    all_violations,
    validate_signal_for_livetrade,
    validate_signal_invariants,
)


def _signal(**overrides) -> KaiSignalForGuards:
    base = {
        "asset": "BTC/USDT",
        "mode": "LIVETRADE",
        "direction": "LONG",
        "confidence": 70.0,
        "risk": "MEDIUM",
        "stop_loss": "76500",
        "data_basis": ("news", "volume", "structure"),
        "data_quality": "MEDIUM",
    }
    base.update(overrides)
    return KaiSignalForGuards(**base)


def test_clean_livetrade_passes():
    r = validate_signal_for_livetrade(_signal())
    assert r.allowed is True
    assert r.reasons == ()


def test_critical_risk_blocks_livetrade():
    r = validate_signal_for_livetrade(_signal(risk="CRITICAL"))
    assert r.allowed is False
    assert any("Critical Risk" in reason for reason in r.reasons)


def test_low_data_quality_blocks_livetrade():
    r = validate_signal_for_livetrade(_signal(data_quality="LOW"))
    assert r.allowed is False
    assert any("Datenqualitaet" in reason for reason in r.reasons)


def test_unknown_data_quality_blocks_livetrade():
    r = validate_signal_for_livetrade(_signal(data_quality="UNKNOWN"))
    assert r.allowed is False


def test_missing_stop_loss_blocks_livetrade():
    r = validate_signal_for_livetrade(_signal(stop_loss=""))
    assert r.allowed is False
    assert any("Stop-Loss" in reason for reason in r.reasons)


def test_waiting_stop_loss_text_blocks_livetrade():
    assert not validate_signal_for_livetrade(_signal(stop_loss="wartet auf Struktur")).allowed
    assert not validate_signal_for_livetrade(_signal(stop_loss="not confirmed yet")).allowed
    assert not validate_signal_for_livetrade(_signal(stop_loss="still waiting")).allowed


def test_empty_data_basis_blocks_livetrade():
    r = validate_signal_for_livetrade(_signal(data_basis=()))
    assert r.allowed is False
    assert any("Datenbasis" in reason for reason in r.reasons)


def test_confidence_out_of_range_blocks_livetrade():
    assert not validate_signal_for_livetrade(_signal(confidence=-1.0)).allowed
    assert not validate_signal_for_livetrade(_signal(confidence=101.0)).allowed


def test_nan_confidence_blocks_livetrade():
    r = validate_signal_for_livetrade(_signal(confidence=math.nan))
    assert r.allowed is False


def test_non_livetrade_modes_pass_without_validation():
    assert validate_signal_for_livetrade(_signal(mode="WATCHLIST", risk="CRITICAL")).allowed
    assert validate_signal_for_livetrade(_signal(mode="PAPERTRADE", data_quality="LOW")).allowed
    assert validate_signal_for_livetrade(_signal(mode="SIMULATION", stop_loss="wartet")).allowed


def test_collects_all_violations_not_just_first():
    r = validate_signal_for_livetrade(
        _signal(risk="CRITICAL", data_quality="LOW", stop_loss="wartet"),
    )
    assert r.allowed is False
    assert len(r.reasons) >= 3


def test_invariants_reject_malformed_asset():
    assert not validate_signal_invariants(_signal(asset="BTC")).allowed
    assert not validate_signal_invariants(_signal(asset="")).allowed


def test_invariants_reject_bad_direction():
    assert not validate_signal_invariants(_signal(direction="WHATEVER")).allowed


def test_all_violations_aggregates():
    r1 = validate_signal_for_livetrade(_signal(risk="CRITICAL"))
    r2 = validate_signal_invariants(_signal(asset="BTC"))
    out = all_violations(r1, r2)
    assert len(out) == len(r1.reasons) + len(r2.reasons)
