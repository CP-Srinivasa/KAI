"""Phase-0 Telegram-Command-Handler Tests (Task N+3 §6).

Deckt:
- Parser: /live unlock, /live status, /live lock, /trade
- Pretty-Print-Replies (✅/❌ + Side-Channel-Schutz für HOTP-Fails)
- Happy-Path through Engine (dry-run-Adapter)
- Args-Validation (bad inputs werden mit ❌-Message gerejected, kein Raise)
"""

from __future__ import annotations

from pathlib import Path

import pyotp
import pytest

from app.execution.exchanges.binance import BinanceAdapter
from app.execution.live_engine import LiveExecutionEngine
from app.messaging.live_telegram_commands import (
    _parse_live_unlock,
    _parse_trade,
    handle_live_lock,
    handle_live_status,
    handle_live_unlock,
    handle_trade,
)
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.security.exchange_perms import (
    ExchangePermissionVerifier,
    PermissionStatus,
    Phase0Expectations,
)
from app.security.hotp_auth import HotpVerifier

_TEST_SEED = "JBSWY3DPEHPK3PXP"


def _phase0_risk_limits() -> RiskLimits:
    return RiskLimits(
        initial_equity=10000.0,
        max_risk_per_trade_pct=10.0,
        max_daily_loss_pct=20.0,
        max_total_drawdown_pct=50.0,
        max_open_positions=5,
        max_leverage=10.0,
        require_stop_loss=False,
        allow_averaging_down=True,
        allow_martingale=True,
        kill_switch_enabled=False,
        min_signal_confidence=0.0,
        min_signal_confluence_count=0,
    )


class _StubPermsVerifier(ExchangePermissionVerifier):
    exchange_name = "binance"

    def fetch_status(self) -> PermissionStatus:
        return PermissionStatus(
            exchange="binance",
            api_key_label=None,
            spot_trading_enabled=True,
            withdrawals_enabled=False,
            margin_enabled=False,
            futures_enabled=False,
            derivatives_enabled=False,
            ip_allowlist=("203.0.113.1",),
            ip_restrict_enforced=True,
            last_check_utc="2026-05-11T11:00:00+00:00",
        )


def _expectations() -> Phase0Expectations:
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


@pytest.fixture
def engine(tmp_path: Path) -> LiveExecutionEngine:
    seed = tmp_path / "seed.b32"
    seed.write_text(_TEST_SEED, encoding="ascii")
    return LiveExecutionEngine(
        hotp_verifier=HotpVerifier(seed_path=seed, journal_path=tmp_path / "j.jsonl"),
        risk_engine=RiskEngine(_phase0_risk_limits()),
        adapters={"binance": BinanceAdapter(dry_run=True)},
        perms_verifiers={"binance": _StubPermsVerifier()},
        perms_expectations=_expectations(),
        audit_log_path=tmp_path / "audit.jsonl",
    )


# --------- Parser ---------


class TestParseLiveUnlock:
    def test_happy_path(self) -> None:
        args = _parse_live_unlock("/live unlock 123456")
        assert args.hotp_code == "123456"

    def test_wrong_command(self) -> None:
        from app.messaging.live_telegram_commands import LiveCommandError

        with pytest.raises(LiveCommandError):
            _parse_live_unlock("/live status")

    def test_short_code(self) -> None:
        from app.messaging.live_telegram_commands import LiveCommandError

        with pytest.raises(LiveCommandError):
            _parse_live_unlock("/live unlock 1234")

    def test_non_digit_code(self) -> None:
        from app.messaging.live_telegram_commands import LiveCommandError

        with pytest.raises(LiveCommandError):
            _parse_live_unlock("/live unlock ABCDEF")


class TestParseTrade:
    def test_happy_path(self) -> None:
        args = _parse_trade("/trade BTCUSDT buy 0.001 80100 78500 384733 binance")
        assert args.symbol == "BTCUSDT"
        assert args.quantity == 0.001
        assert args.entry_price == 80100.0
        assert args.stop_loss == 78500.0
        assert args.exchange == "binance"

    def test_default_exchange_binance(self) -> None:
        args = _parse_trade("/trade ETHUSDT sell 0.5 3000 3100 555555")
        assert args.exchange == "binance"

    def test_bad_side(self) -> None:
        from app.messaging.live_telegram_commands import LiveCommandError

        with pytest.raises(LiveCommandError, match="side"):
            _parse_trade("/trade BTCUSDT hold 1 100 90 111111")

    def test_negative_qty(self) -> None:
        from app.messaging.live_telegram_commands import LiveCommandError

        with pytest.raises(LiveCommandError, match="> 0"):
            _parse_trade("/trade BTCUSDT buy -0.1 100 90 111111")

    def test_bad_hotp_format(self) -> None:
        from app.messaging.live_telegram_commands import LiveCommandError

        with pytest.raises(LiveCommandError, match="HOTP"):
            _parse_trade("/trade BTCUSDT buy 0.1 100 90 ABC")

    def test_unknown_exchange(self) -> None:
        from app.messaging.live_telegram_commands import LiveCommandError

        with pytest.raises(LiveCommandError, match="exchange"):
            _parse_trade("/trade BTCUSDT buy 0.1 100 90 111111 kraken")


# --------- Handler-Replies ---------


class TestHandleLiveUnlock:
    def test_valid_code_returns_active(self, engine: LiveExecutionEngine) -> None:
        reply = handle_live_unlock(f"/live unlock {_code_for(0)}", engine)
        assert "✅" in reply
        assert "Live-Mode aktiv" in reply

    def test_bad_code_returns_rejected(self, engine: LiveExecutionEngine) -> None:
        reply = handle_live_unlock("/live unlock 000000", engine)
        assert "❌" in reply
        assert "HOTP" in reply
        # Side-Channel-Schutz: kein Counter-Hint
        assert "counter" not in reply.lower()

    def test_bad_format(self, engine: LiveExecutionEngine) -> None:
        reply = handle_live_unlock("/live unlock", engine)
        assert "Format" in reply


class TestHandleLiveStatus:
    def test_locked_default(self, engine: LiveExecutionEngine) -> None:
        reply = handle_live_status(engine)
        assert "🔒" in reply
        assert "locked" in reply

    def test_unlocked_after_unlock(self, engine: LiveExecutionEngine) -> None:
        engine.unlock(_code_for(0))
        reply = handle_live_status(engine)
        assert "🔓" in reply
        assert "unlocked" in reply


class TestHandleLiveLock:
    def test_lock_after_unlock(self, engine: LiveExecutionEngine) -> None:
        engine.unlock(_code_for(0))
        reply = handle_live_lock(engine)
        assert "🔒" in reply
        assert "locked" in reply


# --------- Handle-Trade ---------


class TestHandleTrade:
    @pytest.mark.asyncio
    async def test_happy_path_returns_placed(self, engine: LiveExecutionEngine) -> None:
        engine.unlock(_code_for(0))
        reply = await handle_trade(
            f"/trade BTCUSDT buy 0.001 80000 78000 {_code_for(1)} binance",
            engine,
        )
        assert "✅" in reply
        assert "placed" in reply.lower()
        assert "Audit:" in reply
        assert "Counter: 1" in reply or "HOTP-Counter: 1" in reply

    @pytest.mark.asyncio
    async def test_locked_engine_rejects(self, engine: LiveExecutionEngine) -> None:
        # No unlock called → state = LOCKED
        reply = await handle_trade(
            f"/trade BTCUSDT buy 0.001 80000 78000 {_code_for(0)} binance",
            engine,
        )
        assert "❌" in reply
        assert "live_mode_locked" in reply

    @pytest.mark.asyncio
    async def test_bad_hotp_side_channel_protected(self, engine: LiveExecutionEngine) -> None:
        engine.unlock(_code_for(0))
        reply = await handle_trade(
            "/trade BTCUSDT buy 0.001 80000 78000 999999 binance",
            engine,
        )
        assert "❌" in reply
        # Side-Channel: user sieht nur hotp_failed, kein Counter-Hint
        assert "hotp_failed" in reply.lower()

    @pytest.mark.asyncio
    async def test_args_validation_error(self, engine: LiveExecutionEngine) -> None:
        # Bad command → kein Engine-Call
        reply = await handle_trade("/trade", engine)
        assert "❌" in reply
        assert "Format" in reply
