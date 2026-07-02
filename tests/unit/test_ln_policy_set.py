"""Tests for the tight LN policy-envelope setter script (capital-free config)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.lightning.policy import PolicyStore

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ln_policy_set.py"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("ln_policy_set", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def test_recommended_envelope_forces_confirm_on_every_spend() -> None:
    env = mod.build_recommended_envelope(
        actions=["create_invoice", "open_channel"],
        reserve_floor_sat=1_900_000,
        per_action_cap_sat=150_000,
        daily_cap_sat=150_000,
        confirm_threshold_sat=1,
        recipient_allowlist=[],
    )
    assert env.allowed_actions == frozenset({"create_invoice", "open_channel"})
    assert env.reserve_floor_sat == 1_900_000
    assert env.confirm_threshold_sat == 1  # HOTP required on any spend >= 1 sat


def test_unknown_action_rejected() -> None:
    with pytest.raises(ValueError, match="unknown action"):
        mod.build_recommended_envelope(
            actions=["send_coins", "drain_wallet"],
            reserve_floor_sat=0,
            per_action_cap_sat=0,
            daily_cap_sat=0,
            confirm_threshold_sat=0,
            recipient_allowlist=[],
        )


def test_negative_value_rejected() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        mod.build_recommended_envelope(
            actions=["open_channel"],
            reserve_floor_sat=-1,
            per_action_cap_sat=0,
            daily_cap_sat=0,
            confirm_threshold_sat=0,
            recipient_allowlist=[],
        )


def test_main_writes_policy_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ln_policy.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ln_policy_set.py",
            "--policy-path",
            str(path),
            "--actions",
            "create_invoice,open_channel",
            "--reserve-floor",
            "1900000",
        ],
    )
    assert mod.main() == 0
    env = PolicyStore(path).load()
    assert "open_channel" in env.allowed_actions
    assert "send_coins" not in env.allowed_actions
    assert env.reserve_floor_sat == 1_900_000
    assert env.confirm_threshold_sat == 1


def test_dry_run_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ln_policy.json"
    monkeypatch.setattr(sys, "argv", ["ln_policy_set.py", "--policy-path", str(path), "--dry-run"])
    assert mod.main() == 0
    assert not path.exists()


def test_bad_action_via_main_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "ln_policy.json"
    monkeypatch.setattr(
        sys, "argv", ["ln_policy_set.py", "--policy-path", str(path), "--actions", "nuke"]
    )
    assert mod.main() == 2
    assert not path.exists()
