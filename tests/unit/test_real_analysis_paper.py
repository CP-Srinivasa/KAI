"""Fail-closed three-arm override + source/probe discrimination for the
real-analysis paper-learning decoupling (Goal 2026-06-10).

These pin the SAFETY contract of the override that lets a ``source=real_analysis``
paper fill proceed while ``entry_mode=disabled`` — without touching the global
kill-switch, the synthetic loop, or the premium path. Behaviour, not impl.
"""

from __future__ import annotations

import pytest

from app.core.settings import (
    REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL,
    AppSettings,
    RealAnalysisPaperSettings,
)
from app.execution.real_analysis_paper import (
    REAL_ANALYSIS_SOURCE,
    is_real_analysis_source,
    is_synthetic_probe_document,
    real_analysis_paper_entry_disabled_override,
)


def _settings(
    *,
    enabled: bool = False,
    allow: bool = False,
    ack: str = "",
) -> AppSettings:
    """Build AppSettings with ONLY the real-analysis-paper arms set; everything
    else stays at its safe default."""
    return AppSettings(
        real_analysis_paper=RealAnalysisPaperSettings(
            enabled=enabled,
            allow_paper_while_entry_disabled=allow,
            entry_disabled_override_ack=ack,
        )
    )


# ── three-arm fail-closed ─────────────────────────────────────────────────────


def test_override_all_arms_off_is_refused() -> None:
    allowed, code = real_analysis_paper_entry_disabled_override(_settings())
    assert allowed is False
    assert code == "real_analysis_paper_disabled"


def test_override_master_only_is_refused() -> None:
    allowed, code = real_analysis_paper_entry_disabled_override(_settings(enabled=True))
    assert allowed is False
    assert code == "real_analysis_paper_while_entry_disabled_off"


def test_override_master_and_bypass_without_ack_is_refused() -> None:
    allowed, code = real_analysis_paper_entry_disabled_override(_settings(enabled=True, allow=True))
    assert allowed is False
    assert code == "real_analysis_paper_entry_disabled_override_not_armed"


def test_override_wrong_ack_is_refused() -> None:
    allowed, code = real_analysis_paper_entry_disabled_override(
        _settings(enabled=True, allow=True, ack="not_the_sentinel")
    )
    assert allowed is False
    assert code == "real_analysis_paper_entry_disabled_override_not_armed"


def test_override_fully_armed_is_allowed() -> None:
    allowed, code = real_analysis_paper_entry_disabled_override(
        _settings(
            enabled=True,
            allow=True,
            ack=REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL,
        )
    )
    assert allowed is True
    assert code is None


@pytest.mark.parametrize(
    "enabled,allow,ack",
    [
        (True, True, ""),  # ack missing
        (True, False, REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL),  # bypass missing
        (False, True, REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL),  # master missing
    ],
)
def test_override_any_single_arm_missing_is_refused(enabled: bool, allow: bool, ack: str) -> None:
    allowed, _ = real_analysis_paper_entry_disabled_override(
        _settings(enabled=enabled, allow=allow, ack=ack)
    )
    assert allowed is False


def test_defaults_are_off() -> None:
    """A fresh settings object never arms the override (no behavioural drift)."""
    cfg = RealAnalysisPaperSettings()
    assert cfg.enabled is False
    assert cfg.allow_paper_while_entry_disabled is False
    assert cfg.entry_disabled_override_ack == ""
    allowed, _ = real_analysis_paper_entry_disabled_override(AppSettings())
    assert allowed is False


# ── source / synthetic-probe discrimination ──────────────────────────────────


def test_is_real_analysis_source_exact_match() -> None:
    assert is_real_analysis_source(REAL_ANALYSIS_SOURCE) is True
    assert is_real_analysis_source("real_analysis") is True


@pytest.mark.parametrize("src", [None, "", "autonomous_generator", "premium", "canary_probe"])
def test_is_real_analysis_source_rejects_other(src: str | None) -> None:
    assert is_real_analysis_source(src) is False


def test_synthetic_probe_detection() -> None:
    assert is_synthetic_probe_document("loop_control_btc_bullish") is True
    assert is_synthetic_probe_document("loop_control_eth_bearish") is True


@pytest.mark.parametrize("doc_id", [None, "", "a1b2c3d4-real-uuid", "news_123"])
def test_non_synthetic_documents(doc_id: str | None) -> None:
    assert is_synthetic_probe_document(doc_id) is False
