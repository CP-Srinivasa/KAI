"""Runtime-lever derivation for the Edge-Beweis notifier (truth-drift fix).

Pure-function tests: the digest lever block must reflect the actual ``.env``
state, never a hardcoded ``a+b live`` claim. A key that is absent renders as
UNKNOWN (never a fabricated default). Mirrors the script's own ``--self-test``
so the contract is CI-covered, not only Pi-local.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import edge_ic_notify as ein  # noqa: E402


# --- lever_a: sizing-shrink (composite over three Track-1 knobs) ------------
def test_lever_a_off_when_natural_sizing() -> None:
    env = {
        "RISK_MAX_RISK_PER_TRADE_PCT": "0.25",
        "RISK_MIN_STOP_PCT_FOR_SIZING": "0",
        "RISK_MAX_NOTIONAL_PER_TRADE_USD": "0",
    }
    assert ein.lever_a(env) == "OFF"


def test_lever_a_live_via_risk_pct() -> None:
    env = {
        "RISK_MAX_RISK_PER_TRADE_PCT": "0.10",
        "RISK_MIN_STOP_PCT_FOR_SIZING": "0",
        "RISK_MAX_NOTIONAL_PER_TRADE_USD": "0",
    }
    assert ein.lever_a(env) == "LIVE"


def test_lever_a_live_via_track1_knob_not_only_risk_pct() -> None:
    # a_live must hang on ALL three knobs, not just risk_pct (operator spec).
    env = {
        "RISK_MAX_RISK_PER_TRADE_PCT": "0.25",
        "RISK_MIN_STOP_PCT_FOR_SIZING": "4",
        "RISK_MAX_NOTIONAL_PER_TRADE_USD": "0",
    }
    assert ein.lever_a(env) == "LIVE"
    assert ein.lever_a({"RISK_MAX_NOTIONAL_PER_TRADE_USD": "250"}) == "LIVE"


def test_lever_a_unknown_when_keys_absent() -> None:
    assert ein.lever_a({}) == "UNKNOWN"


# --- lever_b: regime time-stop ---------------------------------------------
def test_lever_b_states() -> None:
    assert ein.lever_b({"EXECUTION_REGIME_EXIT_ENABLED": "true"}) == "LIVE"
    assert ein.lever_b({"EXECUTION_REGIME_EXIT_ENABLED": "false"}) == "OFF"
    assert ein.lever_b({}) == "UNKNOWN"


# --- bearish-short-gate (IC lever) -----------------------------------------
def test_bearish_short_gate_states() -> None:
    # LIVE means shorts are SUPPRESSED, i.e. allow_short_news=false.
    assert ein.bearish_short_gate({"ALERT_ALLOW_SHORT_NEWS": "false"}) == "LIVE"
    assert ein.bearish_short_gate({"ALERT_ALLOW_SHORT_NEWS": "true"}) == "OFF"
    assert ein.bearish_short_gate({}) == "UNKNOWN"


# --- .env parsing ----------------------------------------------------------
def test_env_map_skips_comments_and_blanks(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("# comment\n\nFOO=bar\nBAZ = qux \n", encoding="utf-8")
    m = ein._env_map(p)
    assert m == {"FOO": "bar", "BAZ": "qux"}


def test_env_map_missing_file_is_empty(tmp_path: Path) -> None:
    assert ein._env_map(tmp_path / "does-not-exist.env") == {}


# --- rendered block: the operator acceptance + no hardcoded claim -----------
def test_lever_lines_render_current_runtime_truth() -> None:
    env = {
        "RISK_MAX_RISK_PER_TRADE_PCT": "0.25",
        "RISK_MIN_STOP_PCT_FOR_SIZING": "0",
        "RISK_MAX_NOTIONAL_PER_TRADE_USD": "0",
        "EXECUTION_REGIME_EXIT_ENABLED": "true",
        "ALERT_ALLOW_SHORT_NEWS": "false",
    }
    text = "\n".join(ein.lever_lines(env))
    assert "a sizing-shrink    = OFF" in text
    assert "b regime-time-stop = LIVE" in text
    assert "bearish-short-gate = LIVE" in text
    # the old hardcoded drift claim must never reappear
    assert "a+b" not in text


def test_lever_lines_show_unknown_for_missing_keys() -> None:
    text = "\n".join(ein.lever_lines({}))
    assert "UNKNOWN" in text
    assert "fehlt" in text
