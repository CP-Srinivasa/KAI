"""Phase-0 LiveExecutionEngine — Gate-Chain + Idle-Lock Tests (Task N+3).

Spec: docs/security/kai_light_live_phase0_spec.md §7.

Test-Strategie:
- Engine wird mit Stub-Verifier + Stub-Adapter + echter HotpVerifier auf
  tmp-Files initialisiert.
- pyotp generiert für jede HOTP-Verification den passenden Code.
- Gate-Failures werden einzeln per Stub-Override getestet (1 Gate failed,
  alle anderen wären grün) → chain bricht beim ersten failure.
- Idle-Lock via patched ``time.time`` (Monkeypatch).
- Audit-Log wird auf tmp-Pfad geschrieben und nach jedem Test verifiziert.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pyotp
import pytest

from app.execution.exchanges.base import (
    OrderRequest,
    OrderSide,
    OrderType,
)
from app.execution.exchanges.binance import BinanceAdapter
from app.execution.execution_protocol import ExecutionEngineProtocol, LiveExecutionExtensions
from app.execution.live_engine import (
    LiveExecutionEngine,
    LiveModeState,
)
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.security.exchange_perms import (
    ExchangePermissionVerifier,
    PermissionStatus,
    Phase0Expectations,
)
from app.security.hotp_auth import HotpVerifier


def _phase0_risk_limits() -> RiskLimits:
    """Default-Limits für Phase-0-Tests — sehr permissiv, weil Caps-Gate
    schon vor Risk-Gate steht. RiskEngine prüft hier nur kill-switch + dd."""
    return RiskLimits(
        initial_equity=10000.0,
        max_risk_per_trade_pct=10.0,
        max_daily_loss_pct=20.0,
        max_total_drawdown_pct=50.0,
        max_open_positions=5,
        max_leverage=10.0,
        require_stop_loss=False,  # SL-Pflicht ist schon in _validate_server_sl
        allow_averaging_down=True,
        allow_martingale=True,
        kill_switch_enabled=False,
        min_signal_confidence=0.0,
        min_signal_confluence_count=0,
    )


_TEST_SEED = "JBSWY3DPEHPK3PXP"


# --------- Stubs ---------


@dataclass
class _StubVerifier(ExchangePermissionVerifier):
    """Stub für ExchangePermissionVerifier — returns precanned PermissionStatus."""

    exchange_name: str = "binance"
    next_status: PermissionStatus | None = None
    raise_on_fetch: Exception | None = None

    def fetch_status(self) -> PermissionStatus:
        if self.raise_on_fetch:
            raise self.raise_on_fetch
        assert self.next_status is not None
        return self.next_status


def _phase0_compliant_status(exchange: str = "binance") -> PermissionStatus:
    return PermissionStatus(
        exchange=exchange,
        api_key_label=None,
        spot_trading_enabled=True,
        withdrawals_enabled=False,
        margin_enabled=False,
        futures_enabled=False,
        derivatives_enabled=False,
        ip_allowlist=("203.0.113.1",),  # Pi-WAN-IP-Stub
        ip_restrict_enforced=True,
        last_check_utc="2026-05-11T11:00:00+00:00",
    )


def _phase0_expectations() -> Phase0Expectations:
    return Phase0Expectations(
        expected_pi_wan_ip="203.0.113.1",
        require_spot=True,
        forbid_withdrawals=True,
        forbid_margin=True,
        forbid_futures=True,
        forbid_derivatives=True,
        require_ip_allowlist=True,
    )


def _code_for(counter: int) -> str:
    return pyotp.HOTP(_TEST_SEED).at(counter)


# --------- Fixtures ---------


@pytest.fixture
def seed_path(tmp_path: Path) -> Path:
    p = tmp_path / "hotp_seed.b32"
    p.write_text(_TEST_SEED, encoding="ascii")
    return p


@pytest.fixture
def journal_path(tmp_path: Path) -> Path:
    return tmp_path / "hotp_counter.jsonl"


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "live_audit.jsonl"


@pytest.fixture
def engine(seed_path: Path, journal_path: Path, audit_path: Path) -> LiveExecutionEngine:
    verifier = HotpVerifier(seed_path=seed_path, journal_path=journal_path)
    risk = RiskEngine(_phase0_risk_limits())  # defaults: no kill, no pause
    adapter = BinanceAdapter(dry_run=True)
    perms_verifier = _StubVerifier(next_status=_phase0_compliant_status())
    return LiveExecutionEngine(
        hotp_verifier=verifier,
        risk_engine=risk,
        adapters={"binance": adapter},
        perms_verifiers={"binance": perms_verifier},
        perms_expectations=_phase0_expectations(),
        audit_log_path=audit_path,
    )


def _make_order(
    *, side: OrderSide = OrderSide.BUY, qty: float = 0.001, price: float = 80000.0
) -> OrderRequest:
    return OrderRequest(
        symbol="BTCUSDT",
        side=side,
        order_type=OrderType.LIMIT,
        quantity=qty,
        price=price,
        stop_loss=78000.0 if side == OrderSide.BUY else 82000.0,
        client_order_id=f"t-{int(qty * price * 100)}",
    )


def _audit_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --------- State / Lock-Management ---------


class TestLockState:
    def test_boot_default_locked(self, engine: LiveExecutionEngine) -> None:
        assert engine.state == LiveModeState.LOCKED

    def test_unlock_with_valid_hotp(self, engine: LiveExecutionEngine) -> None:
        result = engine.unlock(_code_for(0))
        assert result.counter_used == 0
        assert engine.state == LiveModeState.UNLOCKED

    def test_unlock_with_bad_hotp_keeps_locked(self, engine: LiveExecutionEngine) -> None:
        from app.security.hotp_auth import HotpVerificationFailed

        with pytest.raises(HotpVerificationFailed):
            engine.unlock("000000")
        assert engine.state == LiveModeState.LOCKED

    def test_manual_lock_no_hotp_needed(self, engine: LiveExecutionEngine) -> None:
        engine.unlock(_code_for(0))
        assert engine.state == LiveModeState.UNLOCKED
        engine.lock()
        assert engine.state == LiveModeState.LOCKED

    def test_auto_lock_after_idle(self, engine: LiveExecutionEngine, monkeypatch) -> None:
        # Mock time.time to control idle-elapsed.
        import app.execution.live_engine as le_mod

        engine.unlock(_code_for(0))
        assert engine.state == LiveModeState.UNLOCKED

        # Advance time past idle-lock window.
        original_ts = engine._last_unlock_ts
        monkeypatch.setattr(
            le_mod.time,
            "time",
            lambda: (original_ts or 0) + le_mod.LIVE_MODE_IDLE_LOCK_SECONDS + 1,
        )
        assert engine.state == LiveModeState.LOCKED

    def test_status_shape(self, engine: LiveExecutionEngine) -> None:
        s = engine.status()
        assert s["state"] == "locked"
        assert "hotp_last_counter" in s
        assert "open_positions" in s
        assert "orders_attempted" in s

    def test_protocol_surfaces(self, engine: LiveExecutionEngine) -> None:
        assert isinstance(engine, ExecutionEngineProtocol)
        assert isinstance(engine, LiveExecutionExtensions)
        assert engine.state == LiveModeState.LOCKED
        assert engine.status()["state"] == "locked"

    def test_update_open_count(self, engine: LiveExecutionEngine) -> None:
        engine.update_open_count(2)
        assert engine.status()["open_positions"] == 2
        with pytest.raises(ValueError):
            engine.update_open_count(-1)


# --------- Gate-Chain Happy-Path ---------


class TestGateChainSuccess:
    @pytest.mark.asyncio
    async def test_all_gates_pass_with_dry_run(
        self, engine: LiveExecutionEngine, audit_path: Path
    ) -> None:
        engine.unlock(_code_for(0))
        outcome = await engine.submit_live_order(
            _make_order(),
            hotp_code=_code_for(1),
            signal_confidence=0.9,
            signal_confluence_count=2,
            exchange="binance",
            notional_usd=80.0,
        )
        assert outcome.success
        assert len(outcome.gates) == 5
        assert all(g.passed for g in outcome.gates)
        # Audit: 1× attempted + 1× placed
        records = _audit_records(audit_path)
        assert any(r["event_type"] == "live_order_attempted" for r in records)
        assert any(r["event_type"] == "live_order_placed" for r in records)


# --------- Gate-Chain Failures (jeder Gate einzeln) ---------


class TestGateFailures:
    @pytest.mark.asyncio
    async def test_blocked_when_locked(self, engine: LiveExecutionEngine) -> None:
        # state == LOCKED beim Boot
        outcome = await engine.submit_live_order(
            _make_order(),
            hotp_code=_code_for(0),
            signal_confidence=0.9,
            signal_confluence_count=2,
            exchange="binance",
            notional_usd=80.0,
        )
        assert not outcome.success
        assert "live_mode_locked" in outcome.reject_reason

    @pytest.mark.asyncio
    async def test_hotp_gate_fails_with_bad_code(self, engine: LiveExecutionEngine) -> None:
        engine.unlock(_code_for(0))
        outcome = await engine.submit_live_order(
            _make_order(),
            hotp_code="000000",  # falscher Code
            signal_confidence=0.9,
            signal_confluence_count=2,
            exchange="binance",
            notional_usd=80.0,
        )
        assert not outcome.success
        assert "hotp_failed" in outcome.reject_reason
        # Stop after HOTP — folgende Gates wurden nicht evaluiert
        gate_names = [g.name for g in outcome.gates]
        assert gate_names == ["hotp"]

    @pytest.mark.asyncio
    async def test_live_caps_gate_fails_on_oversize(self, engine: LiveExecutionEngine) -> None:
        engine.unlock(_code_for(0))
        outcome = await engine.submit_live_order(
            _make_order(qty=1.0, price=300.0),  # 300 USD > 200 cap
            hotp_code=_code_for(1),
            signal_confidence=0.9,
            signal_confluence_count=2,
            exchange="binance",
            notional_usd=300.0,
        )
        assert not outcome.success
        assert "live_caps_breach" in outcome.reject_reason
        gate_names = [g.name for g in outcome.gates]
        assert gate_names == ["hotp", "live_caps"]

    @pytest.mark.asyncio
    async def test_perms_drift_fails_gate_4(
        self,
        seed_path: Path,
        journal_path: Path,
        audit_path: Path,
    ) -> None:
        verifier = HotpVerifier(seed_path=seed_path, journal_path=journal_path)
        risk = RiskEngine(_phase0_risk_limits())
        adapter = BinanceAdapter(dry_run=True)
        # Stub gibt einen Status mit ENABLED withdrawals zurück → drift.
        bad_status_with_withdraw = PermissionStatus(
            exchange="binance",
            api_key_label=None,
            spot_trading_enabled=True,
            withdrawals_enabled=True,  # !! drift
            margin_enabled=False,
            futures_enabled=False,
            derivatives_enabled=False,
            ip_allowlist=("203.0.113.1",),
            ip_restrict_enforced=True,
            last_check_utc="2026-05-11T11:00:00+00:00",
        )
        perms = _StubVerifier(next_status=bad_status_with_withdraw)
        engine = LiveExecutionEngine(
            hotp_verifier=verifier,
            risk_engine=risk,
            adapters={"binance": adapter},
            perms_verifiers={"binance": perms},
            perms_expectations=_phase0_expectations(),
            audit_log_path=audit_path,
        )
        engine.unlock(_code_for(0))
        outcome = await engine.submit_live_order(
            _make_order(),
            hotp_code=_code_for(1),
            signal_confidence=0.9,
            signal_confluence_count=2,
            exchange="binance",
            notional_usd=80.0,
        )
        assert not outcome.success
        assert "exchange_perms_drift" in outcome.reject_reason
        # Gates 1-3 passed, gate 4 failed
        gate_names = [g.name for g in outcome.gates]
        assert gate_names == ["hotp", "live_caps", "risk", "exchange_perms"]

    @pytest.mark.asyncio
    async def test_unknown_exchange_fails(self, engine: LiveExecutionEngine) -> None:
        engine.unlock(_code_for(0))
        outcome = await engine.submit_live_order(
            _make_order(),
            hotp_code=_code_for(1),
            signal_confidence=0.9,
            signal_confluence_count=2,
            exchange="kraken",  # nicht configured
            notional_usd=80.0,
        )
        assert not outcome.success
        assert "exchange_perms_not_configured" in outcome.reject_reason


# --------- Idle-TTL refreshed per HOTP-verify ---------


class TestIdleRefresh:
    @pytest.mark.asyncio
    async def test_successful_trade_refreshes_idle(
        self, engine: LiveExecutionEngine, monkeypatch
    ) -> None:
        import app.execution.live_engine as le_mod

        engine.unlock(_code_for(0))
        first_ts = engine._last_unlock_ts
        assert first_ts is not None

        # Advance time by ~30 min (within idle window).
        monkeypatch.setattr(le_mod.time, "time", lambda: first_ts + 1800)

        outcome = await engine.submit_live_order(
            _make_order(),
            hotp_code=_code_for(1),
            signal_confidence=0.9,
            signal_confluence_count=2,
            exchange="binance",
            notional_usd=80.0,
        )
        assert outcome.success
        # last_unlock_ts must have been refreshed to ~first_ts + 1800
        assert engine._last_unlock_ts is not None
        assert engine._last_unlock_ts >= first_ts + 1800
