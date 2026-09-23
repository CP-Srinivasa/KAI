"""``TradingLoop._write_audit`` haengt unter ``append_lock`` an (Audit 16.09., Nebenbefund 22.09.).

Der Loop-Audit war der letzte Multi-Prozess-nahe Audit-Stream ohne Lock
(``alert_audit``, ``bridge_pending_orders`` haben ihn). Geprueft wird, dass der
Append durch den Lock geht, die Zeile beim Verlassen des Locks schon auf der
Platte liegt und ein Lock-Ersatz das Verhalten nicht veraendert.

Der Schreibpfad wohnt seit der Entlastung des God-Files in
``app/orchestrator/loop_audit_log.py``; der Einstieg bleibt
``TradingLoop._write_audit``. Gepatcht wird daher dort, wo der Lock jetzt
gebunden ist — geprueft wird weiterhin der Weg durch den Loop.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

from app.execution.paper_engine import PaperExecutionEngine
from app.market_data.mock_adapter import MockMarketDataAdapter
from app.orchestrator import loop_audit_log as audit_mod
from app.orchestrator import trading_loop as loop_mod
from app.orchestrator.models import CycleStatus, LoopCycle
from app.risk.engine import RiskEngine
from app.risk.models import RiskLimits
from app.signals.generator import SignalGenerator


class _LockRecorder:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.paths: list[Path] = []
        self.content_at_exit: str | None = None

    @contextlib.contextmanager
    def __call__(self, target: Path, *, strict: bool = False) -> Iterator[None]:
        target = Path(target)
        self.paths.append(target)
        self.events.append("enter")
        try:
            yield
        finally:
            self.events.append("exit")
            if target.is_file():
                self.content_at_exit = target.read_text(encoding="utf-8")


def _loop(tmp_path: Path) -> loop_mod.TradingLoop:
    limits = RiskLimits(
        initial_equity=10000.0,
        max_risk_per_trade_pct=0.25,
        max_daily_loss_pct=1.0,
        max_total_drawdown_pct=5.0,
        max_open_positions=3,
        max_leverage=1.0,
        require_stop_loss=True,
        allow_averaging_down=False,
        allow_martingale=False,
        kill_switch_enabled=True,
        min_signal_confidence=0.75,
        min_signal_confluence_count=2,
    )
    engine = PaperExecutionEngine(
        initial_equity=10000.0,
        fee_pct=0.1,
        slippage_pct=0.05,
        live_enabled=False,
        audit_log_path=str(tmp_path / "exec_audit.jsonl"),
    )
    generator = SignalGenerator(
        min_confidence=0.75, min_confluence=2, stop_loss_pct=2.5, take_profit_pct=5.0
    )
    return loop_mod.TradingLoop(
        risk_engine=RiskEngine(limits),
        execution_engine=engine,
        market_data_adapter=MockMarketDataAdapter(),
        signal_generator=generator,
        audit_log_path=str(tmp_path / "loop_audit.jsonl"),
    )


def _cycle() -> LoopCycle:
    return LoopCycle(
        cycle_id="cycle-lock-1",
        started_at="2026-09-22T10:00:00+00:00",
        completed_at="2026-09-22T10:00:01+00:00",
        symbol="BTC/USDT",
        status=CycleStatus.NO_SIGNAL,
    )


def test_loop_audit_append_geht_durch_den_lock(monkeypatch, tmp_path: Path) -> None:
    rec = _LockRecorder()
    monkeypatch.setattr(audit_mod, "append_lock", rec)
    loop = _loop(tmp_path)
    audit_path = tmp_path / "loop_audit.jsonl"

    loop._write_audit(_cycle())

    assert rec.paths == [audit_path]
    assert rec.events == ["enter", "exit"]
    assert rec.content_at_exit is not None
    assert json.loads(rec.content_at_exit)["cycle_id"] == "cycle-lock-1"
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == "no_signal"


def test_loop_audit_zwei_zyklen_zwei_zeilen(tmp_path: Path) -> None:
    loop = _loop(tmp_path)
    loop._write_audit(_cycle())
    loop._write_audit(_cycle())
    lines = (tmp_path / "loop_audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["cycle_id"] for line in lines] == ["cycle-lock-1", "cycle-lock-1"]
