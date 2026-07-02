"""Entry-policy SSOT tests (Sprint S3, Issue #181).

Pins the consolidated per-route semantics of ``EXECUTION_ENTRY_MODE``:

  - the explicit limited paper modes open ONLY their named routes,
  - ``disabled`` keeps the legacy three-arm acks as byte-identical migration
    aliases (Pi-neutrality),
  - contradictory configurations refuse ALL routes fail-closed (#181 §7),
  - the invariant "disabled without armed acks ⇒ ZERO risk-increasing routes,
    for every source, across the flag sweep" (#181 §3/§4),
  - route-volume limits measure + enforce correctly (#181 §5).

Behaviour, not implementation.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.enums import EntryMode
from app.core.settings import (
    PREMIUM_PAPER_WHILE_DISABLED_ACK_SENTINEL,
    REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL,
    AlertSettings,
    AppSettings,
    ExecutionSettings,
    PremiumFastlaneSettings,
    PremiumSettings,
    RealAnalysisPaperSettings,
)
from app.execution.entry_policy import (
    DEFAULT_LEARNING_ROUTE_LIMITS,
    DEFAULT_PREMIUM_ROUTE_LIMITS,
    EntryRoute,
    RouteLimits,
    check_route_limits,
    detect_contradictions,
    measure_route_usage,
    resolve_entry_policy,
)
from app.execution.premium_fastlane import premium_paper_entry_disabled_override
from app.execution.real_analysis_paper import real_analysis_paper_entry_disabled_override

# ── settings builders ────────────────────────────────────────────────────────


def _settings(
    *,
    mode: EntryMode = EntryMode.DISABLED,
    premium_paper: bool = False,
    premium_allow: bool = False,
    premium_ack: str = "",
    rap_enabled: bool = False,
    rap_allow: bool = False,
    rap_ack: str = "",
    fastlane_enabled: bool = False,
    premium_live: bool = False,
    premium_limits: tuple[int, float, int] = (0, 0.0, 0),
    rap_limits: tuple[int, float, int] = (0, 0.0, 0),
    tv_paper: bool = False,
) -> AppSettings:
    return AppSettings(
        alerts=AlertSettings(tradingview_paper_feed_enabled=tv_paper),
        execution=ExecutionSettings(entry_mode=mode),
        premium=PremiumSettings(
            paper_execution_enabled=premium_paper,
            live_execution_enabled=premium_live,
            allow_paper_while_entry_disabled=premium_allow,
            entry_disabled_override_ack=premium_ack,
            paper_route_max_trades_per_hour=premium_limits[0],
            paper_route_max_notional_per_day_usd=premium_limits[1],
            paper_route_max_open_positions=premium_limits[2],
        ),
        real_analysis_paper=RealAnalysisPaperSettings(
            enabled=rap_enabled,
            allow_paper_while_entry_disabled=rap_allow,
            entry_disabled_override_ack=rap_ack,
            paper_route_max_trades_per_hour=rap_limits[0],
            paper_route_max_notional_per_day_usd=rap_limits[1],
            paper_route_max_open_positions=rap_limits[2],
        ),
        premium_fastlane=PremiumFastlaneSettings(enabled=fastlane_enabled),
    )


def _armed_premium(**kw) -> AppSettings:
    return _settings(
        premium_paper=True,
        premium_allow=True,
        premium_ack=PREMIUM_PAPER_WHILE_DISABLED_ACK_SENTINEL,
        **kw,
    )


def _armed_rap(**kw) -> AppSettings:
    return _settings(
        rap_enabled=True,
        rap_allow=True,
        rap_ack=REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL,
        **kw,
    )


# ── invariant: disabled without armed acks opens NOTHING (#181 §3) ───────────


def test_disabled_without_acks_refuses_every_route_across_flag_sweep() -> None:
    """The kill-switch invariant: under ``disabled``, no combination of master
    enables / partial arms / fastlane-enable opens ANY route — only the full,
    correctly-spelled three-arm ack opens exactly its own route."""
    sentinel_p = PREMIUM_PAPER_WHILE_DISABLED_ACK_SENTINEL
    sentinel_r = REAL_ANALYSIS_PAPER_WHILE_DISABLED_ACK_SENTINEL
    for premium_paper, premium_allow, p_ack, rap_enabled, rap_allow, r_ack in itertools.product(
        (False, True),
        (False, True),
        ("", "wrong", sentinel_p),
        (False, True),
        (False, True),
        ("", "wrong", sentinel_r),
    ):
        settings = _settings(
            mode=EntryMode.DISABLED,
            premium_paper=premium_paper,
            premium_allow=premium_allow,
            premium_ack=p_ack,
            rap_enabled=rap_enabled,
            rap_allow=rap_allow,
            rap_ack=r_ack,
        )
        policy = resolve_entry_policy(settings)
        premium_armed = premium_paper and premium_allow and p_ack == sentinel_p
        rap_armed = rap_enabled and rap_allow and r_ack == sentinel_r
        assert policy.allows(EntryRoute.AUTONOMOUS_LOOP) is False
        assert policy.allows(EntryRoute.PREMIUM_FASTLANE) is False
        assert policy.allows(EntryRoute.PREMIUM_PAPER) is premium_armed
        assert policy.allows(EntryRoute.REAL_ANALYSIS_PAPER) is rap_armed


def test_disabled_alias_parity_with_legacy_override_functions() -> None:
    """Migration-alias contract: under ``disabled`` the policy verdict equals
    the legacy override functions bit-for-bit (allowed AND refusal code)."""
    for settings in (
        _settings(),
        _settings(premium_paper=True),
        _settings(premium_paper=True, premium_allow=True),
        _armed_premium(),
        _settings(rap_enabled=True, rap_allow=True),
        _armed_rap(),
    ):
        policy = resolve_entry_policy(settings)
        legacy_p = premium_paper_entry_disabled_override(settings)
        v_p = policy.verdict(EntryRoute.PREMIUM_PAPER)
        if settings.premium.paper_execution_enabled:
            assert (v_p.allowed, v_p.reason_code if not v_p.allowed else None) == (
                legacy_p[0],
                legacy_p[1],
            )
        legacy_r = real_analysis_paper_entry_disabled_override(settings)
        v_r = policy.verdict(EntryRoute.REAL_ANALYSIS_PAPER)
        if settings.real_analysis_paper.enabled:
            assert (v_r.allowed, v_r.reason_code if not v_r.allowed else None) == (
                legacy_r[0],
                legacy_r[1],
            )


def test_disabled_armed_aliases_are_marked_as_aliases() -> None:
    p = resolve_entry_policy(_armed_premium())
    assert p.verdict(EntryRoute.PREMIUM_PAPER).alias_used == "premium_three_arm_ack"
    r = resolve_entry_policy(_armed_rap())
    assert r.verdict(EntryRoute.REAL_ANALYSIS_PAPER).alias_used == "real_analysis_three_arm_ack"


# ── new mode: paper_premium_limited ──────────────────────────────────────────


def test_paper_premium_limited_opens_only_premium_route_without_acks() -> None:
    settings = _settings(mode=EntryMode.PAPER_PREMIUM_LIMITED, premium_paper=True)
    policy = resolve_entry_policy(settings)
    assert policy.allows(EntryRoute.PREMIUM_PAPER) is True
    assert policy.verdict(EntryRoute.PREMIUM_PAPER).alias_used is None
    assert policy.allows(EntryRoute.AUTONOMOUS_LOOP) is False
    assert policy.allows(EntryRoute.REAL_ANALYSIS_PAPER) is False
    assert policy.allows(EntryRoute.PREMIUM_FASTLANE) is False


def test_paper_premium_limited_still_requires_master_enable() -> None:
    settings = _settings(mode=EntryMode.PAPER_PREMIUM_LIMITED, premium_paper=False)
    policy = resolve_entry_policy(settings)
    assert policy.allows(EntryRoute.PREMIUM_PAPER) is False
    assert (
        policy.verdict(EntryRoute.PREMIUM_PAPER).reason_code == "premium_paper_execution_disabled"
    )


def test_paper_premium_limited_injects_default_limits() -> None:
    settings = _settings(mode=EntryMode.PAPER_PREMIUM_LIMITED, premium_paper=True)
    verdict = resolve_entry_policy(settings).verdict(EntryRoute.PREMIUM_PAPER)
    assert verdict.limits == DEFAULT_PREMIUM_ROUTE_LIMITS


def test_explicit_limits_override_defaults() -> None:
    settings = _settings(
        mode=EntryMode.PAPER_PREMIUM_LIMITED,
        premium_paper=True,
        premium_limits=(2, 500.0, 3),
    )
    verdict = resolve_entry_policy(settings).verdict(EntryRoute.PREMIUM_PAPER)
    assert verdict.limits == RouteLimits(2, 500.0, 3)


# ── new mode: paper_learning ─────────────────────────────────────────────────


def test_paper_learning_opens_premium_and_learning_routes_without_acks() -> None:
    settings = _settings(mode=EntryMode.PAPER_LEARNING, premium_paper=True, rap_enabled=True)
    policy = resolve_entry_policy(settings)
    assert policy.allows(EntryRoute.PREMIUM_PAPER) is True
    assert policy.allows(EntryRoute.REAL_ANALYSIS_PAPER) is True
    assert policy.verdict(EntryRoute.REAL_ANALYSIS_PAPER).alias_used is None
    assert policy.allows(EntryRoute.AUTONOMOUS_LOOP) is False
    assert policy.allows(EntryRoute.PREMIUM_FASTLANE) is False


def test_tradingview_route_armed_under_paper_learning_with_flag() -> None:
    policy = resolve_entry_policy(_settings(mode=EntryMode.PAPER_LEARNING, tv_paper=True))
    v = policy.verdict(EntryRoute.TRADINGVIEW_PAPER)
    assert v.allowed is True
    assert v.limits is not None  # learning route limits attached


def test_tradingview_route_closed_without_flag_failclosed() -> None:
    v = resolve_entry_policy(_settings(mode=EntryMode.PAPER_LEARNING, tv_paper=False)).verdict(
        EntryRoute.TRADINGVIEW_PAPER
    )
    assert v.allowed is False and v.reason_code == "tradingview_paper_feed_disabled"
    # Flag on but a non-limited mode also stays closed (route opens only in the
    # explicit limited paper modes).
    v2 = resolve_entry_policy(_settings(mode=EntryMode.DISABLED, tv_paper=True)).verdict(
        EntryRoute.TRADINGVIEW_PAPER
    )
    assert v2.allowed is False


def test_paper_learning_requires_route_master_enables() -> None:
    policy = resolve_entry_policy(_settings(mode=EntryMode.PAPER_LEARNING))
    assert policy.allows(EntryRoute.PREMIUM_PAPER) is False
    assert policy.allows(EntryRoute.REAL_ANALYSIS_PAPER) is False
    assert (
        policy.verdict(EntryRoute.REAL_ANALYSIS_PAPER).reason_code == "real_analysis_paper_disabled"
    )


def test_paper_learning_injects_default_learning_limits() -> None:
    settings = _settings(mode=EntryMode.PAPER_LEARNING, rap_enabled=True)
    verdict = resolve_entry_policy(settings).verdict(EntryRoute.REAL_ANALYSIS_PAPER)
    assert verdict.limits == DEFAULT_LEARNING_ROUTE_LIMITS


# ── legacy modes stay behaviour-neutral ──────────────────────────────────────


def test_legacy_paper_mode_premium_route_needs_no_ack_and_gets_no_limits() -> None:
    settings = _settings(mode=EntryMode.PAPER, premium_paper=True)
    verdict = resolve_entry_policy(settings).verdict(EntryRoute.PREMIUM_PAPER)
    assert verdict.allowed is True
    assert verdict.limits is None  # no implicit limits in legacy modes


def test_legacy_paper_mode_feeder_still_requires_three_arm_ack() -> None:
    """Pre-S3 the feeder was gated by the three-arm ack regardless of mode —
    that stays (behaviour-neutral)."""
    settings = _settings(mode=EntryMode.PAPER, rap_enabled=True)
    policy = resolve_entry_policy(settings)
    assert policy.allows(EntryRoute.REAL_ANALYSIS_PAPER) is False
    armed = _armed_rap(mode=EntryMode.PAPER)
    assert resolve_entry_policy(armed).allows(EntryRoute.REAL_ANALYSIS_PAPER) is True


def test_legacy_modes_open_autonomous_loop() -> None:
    for mode in (EntryMode.PAPER, EntryMode.PROBE):
        policy = resolve_entry_policy(_settings(mode=mode))
        assert policy.allows(EntryRoute.AUTONOMOUS_LOOP) is True


# ── enum-level coarse kill-switch semantics ──────────────────────────────────


def test_limited_modes_close_autonomous_loop_but_allow_risk_entries() -> None:
    for mode in (EntryMode.PAPER_PREMIUM_LIMITED, EntryMode.PAPER_LEARNING):
        assert mode.allows_autonomous_loop_entry is False
        assert mode.allows_risk_increasing_entry is True
    assert EntryMode.DISABLED.allows_autonomous_loop_entry is False
    assert EntryMode.DISABLED.allows_risk_increasing_entry is False


# ── contradictions (#181 §7) ─────────────────────────────────────────────────


def test_fastlane_enabled_in_limited_mode_refuses_all_routes() -> None:
    settings = _settings(
        mode=EntryMode.PAPER_PREMIUM_LIMITED, premium_paper=True, fastlane_enabled=True
    )
    policy = resolve_entry_policy(settings)
    assert policy.contradictions == ("fastlane_enabled_in_limited_paper_mode",)
    for route in EntryRoute:
        assert policy.allows(route) is False
        assert "entry_policy_contradiction" in (policy.verdict(route).reason_code or "")


def test_premium_live_flag_in_limited_mode_is_contradiction() -> None:
    settings = _settings(mode=EntryMode.PAPER_LEARNING, premium_paper=True, premium_live=True)
    policy = resolve_entry_policy(settings)
    assert "premium_live_execution_enabled_in_limited_paper_mode" in policy.contradictions
    assert policy.allows(EntryRoute.PREMIUM_PAPER) is False


def test_no_contradictions_in_legacy_modes() -> None:
    # fastlane enabled under disabled is governed by the legacy two-flag
    # override (#181 §7 already implemented pre-S3) — not re-judged as a
    # policy contradiction (Pi-neutrality).
    settings = _settings(mode=EntryMode.DISABLED, fastlane_enabled=True)
    assert detect_contradictions(settings) == ()


# ── route-usage limiter (#181 §5) ────────────────────────────────────────────


def _write_audit(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _fill_row(ts: datetime, order_id: str, qty: float, price: float) -> dict:
    return {
        "event_type": "order_filled",
        "timestamp_utc": ts.isoformat(),
        "order_id": order_id,
        "side": "buy",
        "position_side": "long",
        "quantity": qty,
        "fill_price": price,
    }


def _label_row(order_id: str, source: str) -> dict:
    return {"event_type": "paper_trade_label", "order_id": order_id, "source_name": source}


def test_measure_route_usage_counts_only_matching_route(tmp_path: Path) -> None:
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    audit = tmp_path / "audit.jsonl"
    _write_audit(
        audit,
        [
            _fill_row(now - timedelta(minutes=10), "o1", 1.0, 100.0),
            _label_row("o1", "telegram_premium_channel_approved"),
            _fill_row(now - timedelta(minutes=20), "o2", 2.0, 50.0),
            _label_row("o2", "real_analysis"),
            # older than 1h but today → notional counts, hourly does not
            _fill_row(now - timedelta(hours=3), "o3", 1.0, 300.0),
            _label_row("o3", "telegram_premium_channel_approved"),
            # exit fill (sell/long) must never count
            {
                "event_type": "order_filled",
                "timestamp_utc": (now - timedelta(minutes=5)).isoformat(),
                "order_id": "o4",
                "side": "sell",
                "position_side": "long",
                "quantity": 9.0,
                "fill_price": 999.0,
            },
        ],
    )
    usage = measure_route_usage(audit, source_prefixes=("telegram_premium",), now=now)
    assert usage.trades_last_hour == 1
    assert usage.notional_today_usd == pytest.approx(400.0)
    usage_rap = measure_route_usage(audit, source_prefixes=("real_analysis",), now=now)
    assert usage_rap.trades_last_hour == 1
    assert usage_rap.notional_today_usd == pytest.approx(100.0)


def test_measure_route_usage_missing_file_is_zero(tmp_path: Path) -> None:
    usage = measure_route_usage(tmp_path / "nope.jsonl", source_prefixes=("x",))
    assert usage.trades_last_hour == 0
    assert usage.notional_today_usd == 0.0


def test_check_route_limits_unlimited_is_ok(tmp_path: Path) -> None:
    ok, detail, _ = check_route_limits(
        route=EntryRoute.PREMIUM_PAPER,
        limits=None,
        audit_path=tmp_path / "audit.jsonl",
    )
    assert ok is True and detail is None


def test_check_route_limits_trades_per_hour(tmp_path: Path) -> None:
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    audit = tmp_path / "audit.jsonl"
    rows = []
    for i in range(3):
        rows.append(_fill_row(now - timedelta(minutes=5 + i), f"o{i}", 1.0, 10.0))
        rows.append(_label_row(f"o{i}", "telegram_premium_channel_approved"))
    _write_audit(audit, rows)
    ok, detail, snapshot = check_route_limits(
        route=EntryRoute.PREMIUM_PAPER,
        limits=RouteLimits(max_trades_per_hour=3),
        audit_path=audit,
        now=now,
    )
    assert ok is False
    assert detail == "max_trades_per_hour"
    assert snapshot["usage"]["trades_last_hour"] == 3


def test_check_route_limits_notional_per_day(tmp_path: Path) -> None:
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    audit = tmp_path / "audit.jsonl"
    _write_audit(
        audit,
        [
            _fill_row(now - timedelta(hours=2), "o1", 10.0, 100.0),  # 1000 USD today
            _label_row("o1", "real_analysis"),
        ],
    )
    ok, detail, _ = check_route_limits(
        route=EntryRoute.REAL_ANALYSIS_PAPER,
        limits=RouteLimits(max_notional_per_day_usd=1000.0),
        audit_path=audit,
        now=now,
    )
    assert ok is False
    assert detail == "max_notional_per_day_usd"


def test_check_route_limits_open_positions(tmp_path: Path) -> None:
    ok, detail, _ = check_route_limits(
        route=EntryRoute.PREMIUM_PAPER,
        limits=RouteLimits(max_open_positions=5),
        audit_path=tmp_path / "audit.jsonl",
        current_open_positions=5,
    )
    assert ok is False
    assert detail == "max_open_positions"


def test_check_route_limits_under_all_caps_is_ok(tmp_path: Path) -> None:
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    audit = tmp_path / "audit.jsonl"
    _write_audit(
        audit,
        [
            _fill_row(now - timedelta(minutes=10), "o1", 1.0, 100.0),
            _label_row("o1", "telegram_premium_channel_approved"),
        ],
    )
    ok, detail, _ = check_route_limits(
        route=EntryRoute.PREMIUM_PAPER,
        limits=RouteLimits(6, 10_000.0, 10),
        audit_path=audit,
        current_open_positions=2,
        now=now,
    )
    assert ok is True and detail is None


# ── evaluate_route_gate (D-234 extraction: verdict + limits → one decision) ──


def _technical_settings(enabled: bool) -> AppSettings:
    from app.core.settings import TechnicalPaperSettings

    settings = AppSettings()
    settings.execution = ExecutionSettings(entry_mode=EntryMode.PAPER)
    settings.technical_paper = TechnicalPaperSettings(enabled=enabled)
    return settings


def test_evaluate_route_gate_blocks_when_route_disabled(tmp_path: Path) -> None:
    from app.execution.entry_policy import RouteGateRejection, evaluate_route_gate

    reject = evaluate_route_gate(
        settings=_technical_settings(enabled=False),
        route=EntryRoute.TECHNICAL_PAPER,
        audit_path=tmp_path / "audit.jsonl",
        current_open_positions=0,
    )
    assert isinstance(reject, RouteGateRejection)
    assert reject.blocked is True
    assert reject.notes == ("route_blocked:technical_paper_disabled",)


def test_evaluate_route_gate_rejects_on_volume_cap(tmp_path: Path) -> None:
    from app.execution.entry_policy import evaluate_route_gate

    # DEFAULT_TECHNICAL_ROUTE_LIMITS caps open positions at 10.
    reject = evaluate_route_gate(
        settings=_technical_settings(enabled=True),
        route=EntryRoute.TECHNICAL_PAPER,
        audit_path=tmp_path / "audit.jsonl",
        current_open_positions=10,
    )
    assert reject is not None
    assert reject.blocked is False
    assert reject.notes[0].startswith("route_limit_reject:max_open_positions")
    assert reject.notes[1].startswith("reason_code:")


def test_evaluate_route_gate_allows_under_caps(tmp_path: Path) -> None:
    from app.execution.entry_policy import evaluate_route_gate

    assert (
        evaluate_route_gate(
            settings=_technical_settings(enabled=True),
            route=EntryRoute.TECHNICAL_PAPER,
            audit_path=tmp_path / "audit.jsonl",
            current_open_positions=0,
        )
        is None
    )
